#!/usr/bin/env python3
"""
Finds units that are really the same thing written differently - "TL" /
"Teelöffel" / "tsp" all meaning teaspoon - and merges them into one. Unlike
duplicate-merging (which only catches exact case/whitespace
duplicates), this uses the AI to recognize genuinely different spellings of
the same unit, and also translates a unit name into your OUTPUT_LANGUAGE if
it isn't already, and fills in a missing plural form ("cup" -> "cups") for
units that meaningfully pluralize.

Uses AI tokens (one call per ~80 units, batched). Token usage prints as it
accumulates and a total at the end. Use --preview N to only review the first
N units instead of your whole collection (useful to sanity-check quality/
cost before running against everything).

Same repoint-then-delete approach as manage_ingredients.py's food merging,
adapted for the "unit" field on a recipe ingredient instead of "food".

Usage (run inside the container, from the backend/ directory):

    python scripts/cleanup_units.py --preview 20        # review only the first 20 units
    python scripts/cleanup_units.py                      # report only (whole collection)
    python scripts/cleanup_units.py --apply               # apply, confirm each
    python scripts/cleanup_units.py --apply --yes          # apply, no per-item confirm
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
    entity_still_referenced,
    fetch_all_recipes_full,
    find_recipes_using_unit,
    format_cost_estimate,
    minimal_ref,
    print_header,
    resolve_name_collisions,
    validate_actions,
)

REVIEW_SYSTEM_PROMPT = """You are cleaning up a home cook's unit-of-measure
database. Its target language is {language}. You will receive a JSON array
of existing unit entries, each {{"id": integer, "name": string,
"plural_name": string|null}}.

Find three kinds of problems:
1. Units that are really the same measure written differently - "TL",
   "Teelöffel", "tsp" all mean teaspoon; "EL", "Esslöffel", "tbsp" all mean
   tablespoon; "g" and "Gramm" mean the same thing but are still worth
   keeping SEPARATE in Tandoor's convention of short abbreviations for
   common units (g, ml, kg, l) - only flag those if BOTH forms exist for the
   exact same thing (a long-form duplicate of a unit that also exists as its
   normal abbreviation).
2. Any unit name that is not already in {language} - propose a {language}
   equivalent, merging with an existing matching entry if one exists.
3. A unit whose "plural_name" is null AND whose plural genuinely differs
   from the singular in {language} (e.g. "cup" -> "cups", "Dose" -> "Dosen")
   - propose that plural. Skip units that don't meaningfully pluralize (g,
   ml, kg, l, Stück/pcs, TL, EL and similar abbreviations keep the same form
   or have no natural plural - leave plural_name alone for those). If the
   plural would be spelled identically to the singular, don't propose it.

Prefer keeping the entry that is already the clean, conventional
abbreviated/short form used in {language} recipes (e.g. "TL" over
"Teelöffel" over "tsp"); if none of a group is already in {language}, invent
the keep_name yourself.

Only include entries that actually need a change - do not list units that
are already fine as-is (most short unit abbreviations like "g", "ml", "kg",
"l", "Stück"/"pcs" need NO change of any kind). Respond with ONLY a JSON
array (no explanation, no markdown fence), each element one of:

{{"type": "rename", "id": <id>, "new_name": <corrected name>}}
{{"type": "merge", "keep_id": <id to keep>, "keep_name": <possibly corrected name for it>, "remove_ids": [<other ids that mean the same unit>]}}
{{"type": "set_plural", "id": <id>, "plural_name": <plural form>}}

If nothing needs a change, respond with [].
"""


def review_chunk(units: list[dict], language: str, tracker: TokenTracker) -> list[dict]:
    system_prompt = REVIEW_SYSTEM_PROMPT.replace("{language}", language)
    text_out, usage = llm_provider.complete_text(
        system_prompt,
        json.dumps(
            [{"id": u["id"], "name": u["name"], "plural_name": u.get("plural_name")} for u in units],
            ensure_ascii=False,
        ),
        max_tokens=4000,
    )
    tracker.add(usage)
    text_out = text_out.strip().strip("`")
    if text_out.startswith("json"):
        text_out = text_out[4:]
    return json.loads(text_out)


def fetch_units_with_plural(client: httpx.Client, units: list[dict]) -> list[dict]:
    """fetch_all_items only returns {id, name} - this adds each unit's current
    plural_name (from its full detail) so the AI review can tell which units
    are actually missing one."""
    enriched = []
    for unit in units:
        resp = client.get(f"/unit/{unit['id']}/")
        plural_name = None
        if resp.status_code == 200:
            plural_name = resp.json().get("plural_name")
        enriched.append({**unit, "plural_name": plural_name})
    return enriched


def apply_set_plural(client: httpx.Client, unit_id: int, plural_name: str) -> None:
    resp = client.patch(f"/unit/{unit_id}/", json={"plural_name": plural_name})
    if resp.status_code not in (200, 201):
        raise TandoorError(f"Could not set plural for unit #{unit_id}: {resp.status_code} {resp.text[:300]}")


def repoint_recipe_unit(recipe_detail: dict, remove_id: int, keep_id: int, keep_name: str) -> dict:
    new_steps = []
    for step in recipe_detail.get("steps", []):
        new_ingredients = []
        for ing in step.get("ingredients", []):
            new_ing = dict(ing)
            unit_ref = ing.get("unit")
            if unit_ref and unit_ref.get("id") == remove_id:
                new_ing["unit"] = {"id": keep_id, "name": keep_name}
            elif unit_ref is not None:
                new_ing["unit"] = minimal_ref(unit_ref)
            if ing.get("food") is not None:
                new_ing["food"] = minimal_ref(ing["food"])
            new_ingredients.append(new_ing)
        new_step = dict(step)
        new_step["ingredients"] = new_ingredients
        new_steps.append(new_step)
    return {"steps": new_steps}


def apply_rename(client: httpx.Client, unit_id: int, new_name: str) -> None:
    resp = client.patch(f"/unit/{unit_id}/", json={"name": new_name})
    if resp.status_code not in (200, 201):
        raise TandoorError(f"Could not rename unit #{unit_id}: {resp.status_code} {resp.text[:300]}")


def apply_merge(client: httpx.Client, all_recipes_full: list[dict], keep_id: int, keep_name: str, remove_ids: list[int]) -> None:
    apply_rename(client, keep_id, keep_name)
    for remove_id in remove_ids:
        affected = find_recipes_using_unit(all_recipes_full, remove_id)
        for recipe in affected:
            resp = client.get(f"/recipe/{recipe['id']}/")
            resp.raise_for_status()
            payload = repoint_recipe_unit(resp.json(), remove_id, keep_id, keep_name)
            resp = client.patch(f"/recipe/{recipe['id']}/", json=payload)
            if resp.status_code not in (200, 201):
                raise TandoorError(f"Could not update recipe {recipe['id']}: {resp.status_code} {resp.text[:300]}")

        # Verify against FRESH data, not the (now stale) all_recipes_full
        # snapshot - checking the cache here would always report "still used"
        # even after a successful repoint, since the cache was never updated.
        still_used_ids = entity_still_referenced(client, "unit", remove_id, [r["id"] for r in affected])
        if still_used_ids:
            raise TandoorError(f"Still used by {len(still_used_ids)} recipe(s) after repointing - not deleting #{remove_id}.")

        resp = client.delete(f"/unit/{remove_id}/")
        if resp.status_code not in (200, 202, 204):
            raise TandoorError(f"Could not delete unit #{remove_id}: {resp.status_code} {resp.text[:200]}")


def run(preview_count: int, apply: bool, assume_yes: bool) -> None:
    if not llm_provider.is_configured():
        print(f"Error: {llm_provider.missing_key_hint()}", file=sys.stderr)
        sys.exit(1)

    with tandoor_client.get_client() as client:
        units = tandoor_client.fetch_all_items(client, "unit")
        if preview_count:
            units = units[:preview_count]
        print(f"{len(units)} unit(s) selected. Fetching current plural forms...")
        units = fetch_units_with_plural(client, units)
        print(f"Target language: {settings.output_language}.")
        print(format_cost_estimate(len(units), "chunked_review"))

        tracker = TokenTracker()
        all_actions = []
        chunks = list(chunked(units, 80))
        for i, chunk in enumerate(chunks, 1):
            print(f"Reviewing chunk {i}/{len(chunks)} ({len(chunk)} unit(s))...")
            try:
                all_actions.extend(validate_actions(review_chunk(chunk, settings.output_language, tracker), "units_review"))
            except Exception as exc:  # noqa: BLE001
                print(f"  ! chunk failed: {exc}")
            print(tracker.progress_line())

        all_actions = resolve_name_collisions(all_actions, units)
        by_id = {u["id"]: u for u in units}

        # Drop no-op plurals: if the AI proposed a plural identical to the
        # singular (e.g. "Zucker" -> "Zucker"), there's nothing to set.
        all_actions = [
            a for a in all_actions
            if not (a["type"] == "set_plural"
                    and a["plural_name"].strip().lower() == by_id.get(a["id"], {}).get("name", "").strip().lower())
        ]

        if not all_actions:
            print(f"\nNothing to fix - all unit names and plurals look clean. {tracker.summary_line()}")
            return

        print_header(f"{len(all_actions)} suggested change(s)")

        needs_recipe_scan = apply and any(a["type"] == "merge" for a in all_actions)
        all_recipes_full = []
        if needs_recipe_scan:
            print("Scanning every recipe's full detail once (needed to repoint merges)...")
            all_recipes_full = fetch_all_recipes_full(client)
            print(f"  {len(all_recipes_full)} recipe(s) scanned.\n")

        renamed, merged, pluralized, errors, skipped = 0, 0, 0, 0, 0
        for action in all_actions:
            if action["type"] == "rename":
                old = by_id.get(action["id"], {}).get("name", "?")
                print(f"  rename #{action['id']}: {old!r} -> {action['new_name']!r}")
            elif action["type"] == "merge":
                keep_old = by_id.get(action["keep_id"], {}).get("name", "?")
                remove_names = ", ".join(f"{by_id.get(rid, {}).get('name', '?')!r} (#{rid})" for rid in action["remove_ids"])
                print(f"  merge into #{action['keep_id']} ({keep_old!r} -> {action['keep_name']!r}): remove {remove_names}")
            elif action["type"] == "set_plural":
                name = by_id.get(action["id"], {}).get("name", "?")
                print(f"  set plural #{action['id']} ({name!r}): -> {action['plural_name']!r}")
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
                elif action["type"] == "merge":
                    apply_merge(client, all_recipes_full, action["keep_id"], action["keep_name"], action["remove_ids"])
                    merged += 1
                else:
                    apply_set_plural(client, action["id"], action["plural_name"])
                    pluralized += 1
                print("    done.")
            except Exception as exc:  # noqa: BLE001
                print(f"    ! error: {exc}")
                errors += 1

        print_header("Summary")
        print(tracker.summary_line())
        if not apply:
            print(f"Dry run: {len(all_actions)} change(s) suggested. Re-run with --apply to make them.")
        else:
            print(f"{renamed} renamed, {merged} merged, {pluralized} plural(s) set, {skipped} skipped, {errors} error(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preview", type=int, default=0, metavar="N", help="Only review N units.")
    parser.add_argument("--apply", action="store_true", help="Actually make changes (default: dry run).")
    parser.add_argument("--yes", action="store_true", help="Don't ask per suggestion.")
    args = parser.parse_args()

    try:
        run(args.preview, args.apply, args.yes)
    except TandoorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
