#!/usr/bin/env python3
"""
Everything ingredient-related in one script, as three subcommands:

  review      The AI reviews all existing food/ingredient names and fixes:
              (a) a prepared/adjective form ("geriebener Parmesan") -> a
              plain base noun ("Parmesan"); (b) a name not already in your
              OUTPUT_LANGUAGE -> translated; (c) two or more entries that
              turn out to mean the same ingredient once (a)/(b) are
              accounted for -> merged into one (repoint-then-delete, same
              approach used throughout this project's merge scripts).
              💰 Uses AI tokens (one call per ~80 foods, batched).

  metadata    Fills in a missing plural name and/or category for foods that
              don't have one, using ONLY categories that already exist -
              unless none of them fit, in which case the AI's suggested new
              category name is shown and YOU decide whether to create it
              (never created silently).
              💰 Uses AI tokens (one call per food checked).

  nutrition   Estimates rough nutritional values (energy, protein, fat,
              carbs per 100g) for foods with none set yet, via Tandoor's
              Property feature.
              💰 Uses AI tokens (one call per food checked).

Every subcommand defaults to a dry run / report and takes --preview N to
limit how many items are looked at (instead of always scanning everything).
Token usage prints as it accumulates and a total at the end.

WARNING: `review` reuses food-merge logic that was tested (via a mocked
Tandoor API) - real confidence, but not
verified against a live instance. `metadata` and `nutrition` are LESS
certain still: the category/property-type endpoint names and the exact
field names Tandoor expects are guessed from its general REST conventions,
not confirmed live. Both self-check on the first food before processing any
more, stopping with the exact error rather than burning AI calls against a
write shape that doesn't work - if that happens, share the printed error.

Usage (run inside the container, from the backend/ directory):

    python scripts/manage_ingredients.py review --preview 20
    python scripts/manage_ingredients.py review --apply --yes
    python scripts/manage_ingredients.py metadata --preview 5
    python scripts/manage_ingredients.py metadata --apply
    python scripts/manage_ingredients.py nutrition --preview 3
    python scripts/manage_ingredients.py nutrition --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app import llm_provider, tandoor_client  # noqa: E402
from app.config import settings  # noqa: E402
from app.tandoor_client import TandoorError  # noqa: E402
from scripts._shared import (  # noqa: E402
    TokenTracker,
    chunked,
    format_cost_estimate,
    find_recipes_by_filter,
    minimal_ref,
    print_header,
    resolve_name_collisions,
    validate_actions,
)

CHUNK_SIZE = 80
CATEGORY_ENDPOINT_CANDIDATES = ["supermarket-category", "supermarketcategory", "food-category"]
PROPERTY_TYPE_ENDPOINT_CANDIDATES = ["property-type", "propertytype", "property_type"]

REVIEW_SYSTEM_PROMPT = """You are cleaning up a home cook's ingredient
database. Its target language is {language}. You will receive a JSON array
of existing ingredient entries, each {{"id": integer, "name": string}}.

Find three kinds of problems:
1. A name that is a prepared/adjective form ("geriebener Parmesan", "gehackte
   Zwiebeln") rather than a plain base ingredient noun ("Parmesan",
   "Zwiebel") - propose a corrected, singular, plain name for it.
2. A name that is NOT already in {language} - propose a natural {language}
   translation of it (still following rule 1: plain singular noun, not a
   prepared form).
3. Two or more entries that clearly refer to the exact same base ingredient
   once you account for both of the above (singular/plural spelling
   variants, near-duplicate spelling, a prepared-form or other-language
   duplicate of an already-clean {language} entry) - these should be merged
   into one. Prefer keeping the entry that is already a clean,
   correctly-spelled, singular, plain {language} noun; if more than one
   qualifies, prefer the shorter one. If none of the group is already clean
   {language}, invent the keep_name yourself (translated + cleaned) even
   though no existing entry currently has it.

Only include entries that actually need a change - do not list names that
are already a clean, plain, singular {language} noun. Respond with ONLY a
JSON array (no explanation, no markdown fence), each element one of:

{{"type": "rename", "id": <id>, "new_name": <corrected name>}}
{{"type": "merge", "keep_id": <id to keep>, "keep_name": <possibly corrected/translated name for it>, "remove_ids": [<other ids that mean the same thing>]}}

If nothing needs a change, respond with [].
"""


def review_chunk(foods, language, tracker):
    system_prompt = REVIEW_SYSTEM_PROMPT.replace("{language}", language)
    text_out, usage = llm_provider.complete_text(
        system_prompt,
        json.dumps([{"id": f["id"], "name": f["name"]} for f in foods], ensure_ascii=False),
        max_tokens=8000,
    )
    tracker.add(usage)
    text_out = text_out.strip().strip("`")
    if text_out.startswith("json"):
        text_out = text_out[4:]
    return json.loads(text_out)


def find_recipes_using_food(client, food_id):
    return find_recipes_by_filter(client, "foods", food_id)


def repoint_recipe_food(recipe_detail, remove_id, keep_id, keep_name):
    new_steps = []
    for step in recipe_detail.get("steps", []):
        new_ingredients = []
        for ing in step.get("ingredients", []):
            new_ing = dict(ing)
            food_ref = ing.get("food")
            if food_ref and food_ref.get("id") == remove_id:
                new_ing["food"] = {"id": keep_id, "name": keep_name}
            elif food_ref is not None:
                new_ing["food"] = minimal_ref(food_ref)
            if ing.get("unit") is not None:
                new_ing["unit"] = minimal_ref(ing["unit"])
            new_ingredients.append(new_ing)
        new_step = dict(step)
        new_step["ingredients"] = new_ingredients
        new_steps.append(new_step)
    return {"steps": new_steps}


def apply_rename(client, food_id, new_name):
    resp = client.patch(f"/food/{food_id}/", json={"name": new_name})
    if resp.status_code not in (200, 201):
        raise TandoorError(f"Could not rename food #{food_id}: {resp.status_code} {resp.text[:300]}")


def apply_merge(client, keep_id, keep_name, remove_ids):
    apply_rename(client, keep_id, keep_name)
    for remove_id in remove_ids:
        for recipe in find_recipes_using_food(client, remove_id):
            resp = client.get(f"/recipe/{recipe['id']}/")
            resp.raise_for_status()
            payload = repoint_recipe_food(resp.json(), remove_id, keep_id, keep_name)
            resp = client.patch(f"/recipe/{recipe['id']}/", json=payload)
            if resp.status_code not in (200, 201):
                raise TandoorError(f"Could not update recipe {recipe['id']}: {resp.status_code} {resp.text[:300]}")

        still_used = find_recipes_using_food(client, remove_id)
        if still_used:
            raise TandoorError(f"Still used by {len(still_used)} recipe(s) after repointing - not deleting #{remove_id}.")

        resp = client.delete(f"/food/{remove_id}/")
        if resp.status_code not in (200, 202, 204):
            raise TandoorError(f"Could not delete food #{remove_id}: {resp.status_code} {resp.text[:200]}")


def run_review(preview_count, apply, assume_yes):
    if not llm_provider.is_configured():
        print(f"Error: {llm_provider.missing_key_hint()}", file=sys.stderr)
        sys.exit(1)

    with tandoor_client.get_client() as client:
        foods = tandoor_client.fetch_all_items(client, "food")
        if preview_count:
            foods = foods[:preview_count]
        print(f"{len(foods)} food(s) selected. Target language: {settings.output_language}.")
        print(format_cost_estimate(len(foods), "chunked_review"))

        tracker = TokenTracker()
        all_actions = []
        chunks = list(chunked(foods, CHUNK_SIZE))
        for i, chunk in enumerate(chunks, 1):
            print(f"Reviewing chunk {i}/{len(chunks)} ({len(chunk)} food(s))...")
            try:
                all_actions.extend(validate_actions(review_chunk(chunk, settings.output_language, tracker), "ingredients_review"))
            except Exception as exc:  # noqa: BLE001
                print(f"  ! chunk failed: {exc}")
            print(tracker.progress_line())

        if not all_actions:
            print(f"\nNothing to fix - all names look clean. {tracker.summary_line()}")
            return

        all_actions = resolve_name_collisions(all_actions, foods)

        print_header(f"{len(all_actions)} suggested change(s)")
        by_id = {f["id"]: f for f in foods}
        renamed, merged, errors, skipped = 0, 0, 0, 0

        for action in all_actions:
            if action["type"] == "rename":
                old = by_id.get(action["id"], {}).get("name", "?")
                print(f"  rename #{action['id']}: {old!r} -> {action['new_name']!r}")
            elif action["type"] == "merge":
                keep_old = by_id.get(action["keep_id"], {}).get("name", "?")
                remove_names = ", ".join(f"{by_id.get(rid, {}).get('name', '?')!r} (#{rid})" for rid in action["remove_ids"])
                print(f"  merge into #{action['keep_id']} ({keep_old!r} -> {action['keep_name']!r}): remove {remove_names}")
            else:
                continue

            if not apply:
                continue
            if not assume_yes:
                answer = input("    Apply? [y/N] ").strip().lower()
                if answer != "y":
                    print("    skipped.")
                    skipped += 1
                    continue

            try:
                if action["type"] == "rename":
                    apply_rename(client, action["id"], action["new_name"])
                    renamed += 1
                else:
                    apply_merge(client, action["keep_id"], action["keep_name"], action["remove_ids"])
                    merged += 1
                print("    done.")
            except Exception as exc:  # noqa: BLE001
                print(f"    ! error: {exc}")
                errors += 1

        print_header("Summary")
        print(tracker.summary_line())
        if not apply:
            print(f"Dry run: {len(all_actions)} change(s) suggested. Re-run with --apply to make them.")
        else:
            print(f"{renamed} renamed, {merged} merged, {skipped} skipped, {errors} error(s).")


METADATA_SYSTEM_PROMPT = """You fill in missing metadata for ingredient
entries in {language}. You will receive a JSON object: {{"food": {{"id":
integer, "name": string}}, "categories": [{{"id": integer, "name": string}},
...], "needs_plural": bool, "needs_category": bool}}.

If needs_plural is true, give the plural form of the food's name in
{language} (e.g. "Tomate" -> "Tomaten"). If needs_category is true, pick the
SINGLE best-fitting category id from the given "categories" list; if
genuinely none of them fit, instead suggest a short, natural new category
name in {language} (e.g. "Gewürze" for a spice with no spice category yet) -
do not force a bad fit just to avoid suggesting a new one.

Respond with ONLY a JSON object (no explanation, no markdown fence):
{{"plural_name": <string or null>, "category_id": <integer or null>,
"suggested_new_category": <string or null>}}
Only include a non-null plural_name/category_id if the corresponding
"needs_..." flag was true. Set at most one of category_id and
suggested_new_category (never both).
"""


def find_category_endpoint(client):
    for endpoint in CATEGORY_ENDPOINT_CANDIDATES:
        try:
            resp = client.get(f"/{endpoint}/", params={"page_size": 1})
        except httpx.HTTPError:
            continue
        if resp.status_code == 200:
            return endpoint
    raise TandoorError(
        f"Could not find a working category endpoint (tried {CATEGORY_ENDPOINT_CANDIDATES}). "
        f"Check your Tandoor version's API schema at <TANDOOR_URL>/api/schema/swagger-ui/ for the correct name."
    )


def estimate_metadata(food_name, needs_plural, needs_category, categories, language, tracker):
    system_prompt = METADATA_SYSTEM_PROMPT.replace("{language}", language)
    user_content = json.dumps({
        "food": {"name": food_name},
        "categories": [{"id": c["id"], "name": c["name"]} for c in categories],
        "needs_plural": needs_plural,
        "needs_category": needs_category,
    }, ensure_ascii=False)
    text_out, usage = llm_provider.complete_text(system_prompt, user_content, max_tokens=200)
    tracker.add(usage)
    text_out = text_out.strip().strip("`")
    if text_out.startswith("json"):
        text_out = text_out[4:]
    return json.loads(text_out)


def create_category(client, endpoint, name):
    resp = client.post(f"/{endpoint}/", json={"name": name})
    if resp.status_code not in (200, 201):
        raise TandoorError(f"Could not create category {name!r}: {resp.status_code} {resp.text[:300]}")
    return resp.json()["id"]


def run_metadata(preview_count, apply, assume_yes):
    if (preview_count or apply) and not llm_provider.is_configured():
        print(f"Error: {llm_provider.missing_key_hint()}", file=sys.stderr)
        sys.exit(1)

    with tandoor_client.get_client() as client:
        endpoint = find_category_endpoint(client)
        categories = tandoor_client.fetch_all_items(client, endpoint)
        print(f"{len(categories)} existing categor(y/ies) found via /{endpoint}/.")

        all_foods = tandoor_client.fetch_all_items(client, "food")
        targets = []
        for food in all_foods:
            resp = client.get(f"/food/{food['id']}/")
            if resp.status_code != 200:
                continue
            detail = resp.json()
            needs_plural = not detail.get("plural_name")
            needs_category = not detail.get("category")
            if needs_plural or needs_category:
                targets.append((food, needs_plural, needs_category))

        print(f"{len(targets)} of {len(all_foods)} food(s) missing plural and/or category.")

        if not preview_count and not apply:
            print(format_cost_estimate(len(targets), "per_recipe_small"))
            print("Dry run: no AI calls made. Use --preview N to estimate a sample, or --apply for everything.")
            for food, np, nc in targets[:20]:
                missing = ", ".join(x for x, flag in (("plural", np), ("category", nc)) if flag)
                print(f"  - {food['name']!r} (#{food['id']}) - missing: {missing}")
            return

        sample = targets[:preview_count] if preview_count else targets
        print(format_cost_estimate(len(sample), "per_recipe_small"))
        write_mode = apply and not preview_count
        checked_first_write = not write_mode
        tracker = TokenTracker()
        written, errors, new_categories_created = 0, 0, 0

        for i, (food, needs_plural, needs_category) in enumerate(sample, 1):
            try:
                result = estimate_metadata(food["name"], needs_plural, needs_category, categories, settings.output_language, tracker)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(sample)}] ! {food['name']!r}: estimation failed: {exc}")
                errors += 1
                continue

            print(f"  [{i}/{len(sample)}] {food['name']!r}: {result}")
            if i % 10 == 0:
                print(tracker.progress_line())

            if not write_mode:
                continue

            payload = {}
            if needs_plural and result.get("plural_name"):
                payload["plural_name"] = result["plural_name"]

            if needs_category and result.get("category_id"):
                payload["category"] = {"id": result["category_id"]}
            elif needs_category and result.get("suggested_new_category"):
                new_name = result["suggested_new_category"]
                create_it = True
                if not assume_yes:
                    answer = input(f"    No existing category fits {food['name']!r}. "
                                    f"Create new category {new_name!r}? [y/N] ").strip().lower()
                    create_it = answer == "y"
                if create_it:
                    try:
                        new_id = create_category(client, endpoint, new_name)
                        categories.append({"id": new_id, "name": new_name})
                        payload["category"] = {"id": new_id}
                        new_categories_created += 1
                        print(f"    created category {new_name!r} (#{new_id}).")
                    except TandoorError as exc:
                        print(f"    ! could not create category: {exc}")
                else:
                    print("    skipped creating a new category - leaving uncategorized.")

            if not payload:
                continue

            resp = client.patch(f"/food/{food['id']}/", json=payload)

            if not checked_first_write:
                if resp.status_code not in (200, 201):
                    print_header("Self-check failed on the first food - stopping before any more AI calls")
                    print(f"PATCH /food/{food['id']}/ -> {resp.status_code}\n{resp.text[:500]}")
                    print("\nThe category field name this script guessed is likely wrong for your "
                          "Tandoor version. Share the error above to get this fixed.")
                    return
                checked_first_write = True

            if resp.status_code in (200, 201):
                written += 1
            else:
                print(f"    ! could not write: {resp.status_code} {resp.text[:200]}")
                errors += 1

        print_header("Summary")
        print(tracker.summary_line())
        if not write_mode:
            print(f"Preview only - nothing was written. {errors} error(s).")
        else:
            print(f"{written} of {len(sample)} food(s) updated, {new_categories_created} new categor(y/ies) "
                  f"created, {errors} error(s).")


NUTRIENTS = {
    "Energy": ("kcal", "energy in kcal"),
    "Protein": ("g", "protein in grams"),
    "Fat": ("g", "fat in grams"),
    "Carbohydrates": ("g", "carbohydrates in grams"),
}

NUTRITION_SYSTEM_PROMPT = """You estimate rough, typical nutritional values
for raw or commonly-used food ingredients, per 100g (or per 100ml for
liquids). Respond with ONLY a JSON object: {"energy_kcal": number,
"protein_g": number, "fat_g": number, "carbs_g": number} - no explanation,
no markdown. Use well-known typical values (e.g. USDA-style reference data)
rounded to a sensible precision. If the food name is a prepared/composite
dish rather than a single ingredient, still give your best rough estimate
for it as eaten.
"""


def find_property_type_endpoint(client):
    for endpoint in PROPERTY_TYPE_ENDPOINT_CANDIDATES:
        try:
            resp = client.get(f"/{endpoint}/", params={"page_size": 1})
        except httpx.HTTPError:
            continue
        if resp.status_code == 200:
            return endpoint
    raise TandoorError(
        f"Could not find a working property-type endpoint (tried {PROPERTY_TYPE_ENDPOINT_CANDIDATES}). "
        f"Check your Tandoor version's API schema at <TANDOOR_URL>/api/schema/swagger-ui/ for the correct name."
    )


def get_or_create_property_type(client, endpoint, name, unit):
    resp = client.get(f"/{endpoint}/", params={"query": name, "page_size": 10})
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", data) if isinstance(data, dict) else data
    for item in results:
        if item.get("name", "").strip().lower() == name.lower():
            return item["id"]
    resp = client.post(f"/{endpoint}/", json={"name": name, "unit": unit})
    if resp.status_code not in (200, 201):
        raise TandoorError(f"Could not create property type {name!r}: {resp.status_code} {resp.text[:300]}")
    return resp.json()["id"]


def estimate_nutrition(food_name, tracker):
    text_out, usage = llm_provider.complete_text(NUTRITION_SYSTEM_PROMPT, f"Food: {food_name}", max_tokens=200)
    tracker.add(usage)
    text_out = text_out.strip().strip("`")
    if text_out.startswith("json"):
        text_out = text_out[4:]
    return json.loads(text_out)


def build_properties_payload(estimate_result, property_type_ids):
    mapping = {
        "Energy": estimate_result.get("energy_kcal"),
        "Protein": estimate_result.get("protein_g"),
        "Fat": estimate_result.get("fat_g"),
        "Carbohydrates": estimate_result.get("carbs_g"),
    }
    return [
        {"property_type": {"id": property_type_ids[name]}, "property_amount": value}
        for name, value in mapping.items()
        if value is not None
    ]


def run_nutrition(preview_count, apply):
    if (preview_count or apply) and not llm_provider.is_configured():
        print(f"Error: {llm_provider.missing_key_hint()}", file=sys.stderr)
        sys.exit(1)

    with tandoor_client.get_client() as client:
        all_foods = tandoor_client.fetch_all_items(client, "food")
        without_properties = []
        for food in all_foods:
            resp = client.get(f"/food/{food['id']}/")
            if resp.status_code != 200:
                continue
            if not resp.json().get("properties"):
                without_properties.append(food)

        print(f"{len(all_foods)} food(s) total, {len(without_properties)} with no properties set.")

        if not preview_count and not apply:
            print(format_cost_estimate(len(without_properties), "per_recipe_small"))
            print("Dry run: no AI calls made. Use --preview N to estimate a sample, or --apply for everything.")
            for food in without_properties[:20]:
                print(f"  - {food['name']!r} (#{food['id']})")
            if len(without_properties) > 20:
                print(f"  ... and {len(without_properties) - 20} more")
            return

        endpoint = find_property_type_endpoint(client)
        property_type_ids = {
            name: get_or_create_property_type(client, endpoint, name, unit)
            for name, (unit, _label) in NUTRIENTS.items()
        }

        targets = without_properties[:preview_count] if preview_count else without_properties
        print(format_cost_estimate(len(targets), "per_recipe_small"))
        write_mode = apply and not preview_count
        checked_first_write = not write_mode
        tracker = TokenTracker()
        written, errors = 0, 0

        for i, food in enumerate(targets, 1):
            try:
                result = estimate_nutrition(food["name"], tracker)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(targets)}] ! {food['name']!r}: estimation failed: {exc}")
                errors += 1
                continue

            print(f"  [{i}/{len(targets)}] {food['name']!r}: {result}")
            if i % 10 == 0:
                print(tracker.progress_line())

            if not write_mode:
                continue

            payload = {"properties": build_properties_payload(result, property_type_ids)}
            resp = client.patch(f"/food/{food['id']}/", json=payload)

            if not checked_first_write:
                if resp.status_code not in (200, 201):
                    print_header("Self-check failed on the first food - stopping before any more AI calls")
                    print(f"PATCH /food/{food['id']}/ -> {resp.status_code}\n{resp.text[:500]}")
                    print("\nThe property payload shape this script guessed is likely wrong for your "
                          "Tandoor version. Share the error above to get this fixed.")
                    return
                checked_first_write = True

            if resp.status_code in (200, 201):
                written += 1
            else:
                print(f"    ! could not write: {resp.status_code} {resp.text[:200]}")
                errors += 1

        print_header("Summary")
        print(tracker.summary_line())
        if not write_mode:
            print(f"Preview only - nothing was written. {errors} error(s).")
        else:
            print(f"{written} of {len(targets)} food(s) updated, {errors} error(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    p_review = sub.add_parser("review", help="Fix/translate/merge ingredient names. Uses AI tokens.")
    p_review.add_argument("--preview", type=int, default=0, metavar="N", help="Only review N foods.")
    p_review.add_argument("--apply", action="store_true")
    p_review.add_argument("--yes", action="store_true", help="Don't ask per suggestion.")

    p_metadata = sub.add_parser("metadata", help="Fill in missing plural/category. Uses AI tokens.")
    p_metadata.add_argument("--preview", type=int, default=0, metavar="N")
    p_metadata.add_argument("--apply", action="store_true")
    p_metadata.add_argument("--yes", action="store_true", help="Auto-create a suggested new category without asking.")

    p_nutrition = sub.add_parser("nutrition", help="Estimate nutritional properties. Uses AI tokens.")
    p_nutrition.add_argument("--preview", type=int, default=0, metavar="N")
    p_nutrition.add_argument("--apply", action="store_true")

    args = parser.parse_args()

    try:
        if args.mode == "review":
            run_review(args.preview, args.apply, args.yes)
        elif args.mode == "metadata":
            run_metadata(args.preview, args.apply, args.yes)
        elif args.mode == "nutrition":
            run_nutrition(args.preview, args.apply)
    except TandoorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
