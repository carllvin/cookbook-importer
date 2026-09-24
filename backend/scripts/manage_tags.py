#!/usr/bin/env python3
"""
Everything tag-related in one script, as five subcommands:

  simplify        The AI finds tags that mean the same thing even if worded
                  differently (e.g. "schnell" / "Schnelles Gericht"), and
                  merges them. Conservative on purpose: tags that are merely
                  related but not the same (e.g. "Vegan" vs "Vegetarisch")
                  are left alone.
                  💰 Uses AI tokens (one call per ~80 tags, batched).

  translate      Translate tags not already in OUTPUT_LANGUAGE, merging into
                  an existing translated tag where one already exists.
                  💰 Uses AI tokens (one call per ~80 tags, batched).

  season          Backfill a season tag (Spring/Summer/Autumn/Winter) onto
                  recipes that don't have one yet - the same check the live
                  importer does when first extracting a recipe.
                  💰 Uses AI tokens (one small call per recipe checked).

  suggest-more    For recipes with very few tags, suggest additional relevant
                  ones - reusing your existing tags where they fit, inventing
                  a new one only when nothing existing applies.
                  💰 Uses AI tokens (one call per under-tagged recipe).

  all             Runs all four of the above in that order (simplify,
                  translate, season, suggest-more) - the sensible order,
                  since simplifying and translating first means season/
                  suggest-more work from a cleaner, less redundant tag
                  vocabulary. 💰 Uses AI tokens (all of the above, combined).

Every subcommand defaults to a dry run / report and takes --preview N to
limit how many items are looked at (instead of always scanning everything) -
useful both to keep AI cost down while checking quality, and just to get a
quick partial report on a big collection. Token usage prints as it
accumulates and a total at the end for every subcommand.

Usage (run inside the container, from the backend/ directory):

    python scripts/manage_tags.py all --preview 10          # dry run everything, in order
    python scripts/manage_tags.py all --apply --yes         # do everything, no per-suggestion confirmation

    python scripts/manage_tags.py simplify --preview 20
    python scripts/manage_tags.py simplify --apply --yes
    python scripts/manage_tags.py translate --preview 20
    python scripts/manage_tags.py translate --apply --yes
    python scripts/manage_tags.py season --preview 5
    python scripts/manage_tags.py season --apply
    python scripts/manage_tags.py suggest-more --min-tags 5 --preview 10
    python scripts/manage_tags.py suggest-more --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm_provider, tandoor_client  # noqa: E402
from app.config import get_language_code, settings  # noqa: E402
from app.tandoor_client import TandoorError  # noqa: E402
from scripts._shared import (  # noqa: E402
    TokenTracker,
    chunked,
    fetch_all_recipes_full,
    find_recipes_by_filter,
    format_cost_estimate,
    print_header,
    resolve_name_collisions,
    validate_actions,
)

# ================================================================
# Shared merge/rename primitives (used by translate + simplify)
# ================================================================

def find_recipes_using_keyword(client, keyword_id: int) -> list[dict]:
    return find_recipes_by_filter(client, "keywords", keyword_id)


def apply_rename(client, keyword_id: int, new_name: str) -> None:
    resp = client.patch(f"/keyword/{keyword_id}/", json={"name": new_name})
    if resp.status_code not in (200, 201):
        raise TandoorError(f"Could not rename keyword #{keyword_id}: {resp.status_code} {resp.text[:300]}")


def apply_merge(client, keep_id: int, keep_name: str, remove_ids: list[int]) -> None:
    apply_rename(client, keep_id, keep_name)
    for remove_id in remove_ids:
        for recipe in find_recipes_using_keyword(client, remove_id):
            current_ids = {kw["id"] for kw in recipe.get("keywords", [])}
            current_ids.discard(remove_id)
            current_ids.add(keep_id)
            resp = client.patch(f"/recipe/{recipe['id']}/", json={"keywords": [{"id": kid} for kid in current_ids]})
            if resp.status_code not in (200, 201):
                raise TandoorError(f"Could not update recipe {recipe['id']}: {resp.status_code} {resp.text[:300]}")

        still_used = find_recipes_using_keyword(client, remove_id)
        if still_used:
            raise TandoorError(f"Still used by {len(still_used)} recipe(s) after repointing - not deleting #{remove_id}.")

        resp = client.delete(f"/keyword/{remove_id}/")
        if resp.status_code not in (200, 202, 204):
            raise TandoorError(f"Could not delete keyword #{remove_id}: {resp.status_code} {resp.text[:200]}")


# ================================================================
# translate
# ================================================================

TRANSLATE_SYSTEM_PROMPT = """You review recipe tags for a database whose
target language is {language}. You will receive a JSON array of existing
tags, each {{"id": integer, "name": string}}.

Flag any tag whose name is NOT already in {language} - propose a natural
{language} translation (a single word or short phrase, matching the style of
a recipe tag, not a literal word-for-word translation). If another entry in
the list is already that same {language} translation (or close enough to
clearly mean the same thing), propose merging into it instead of just
renaming.

Only include tags that actually need a change - do not list tags already in
{language}. Respond with ONLY a JSON array (no explanation, no markdown
fence), each element one of:

{{"type": "rename", "id": <id>, "new_name": <translated name>}}
{{"type": "merge", "keep_id": <id to keep>, "keep_name": <name for it, translated if needed>, "remove_ids": [<other ids that mean the same tag>]}}

If nothing needs a change, respond with [].
"""


def translate_review_chunk(tags: list[dict], language: str, tracker: TokenTracker) -> list[dict]:
    system_prompt = TRANSLATE_SYSTEM_PROMPT.replace("{language}", language)
    text_out, usage = llm_provider.complete_text(
        system_prompt,
        json.dumps([{"id": t["id"], "name": t["name"]} for t in tags], ensure_ascii=False),
        max_tokens=8000,
    )
    tracker.add(usage)
    text_out = text_out.strip().strip("`")
    if text_out.startswith("json"):
        text_out = text_out[4:]
    return json.loads(text_out)


def run_translate(preview_count: int, apply: bool, assume_yes: bool) -> TokenTracker:
    if not llm_provider.is_configured():
        print(f"Error: {llm_provider.missing_key_hint()}", file=sys.stderr)
        sys.exit(1)

    with tandoor_client.get_client() as client:
        tags = tandoor_client.fetch_all_items(client, "keyword")
        if preview_count:
            tags = tags[:preview_count]
        print(f"{len(tags)} tag(s) selected. Target language: {settings.output_language}.")
        print(format_cost_estimate(len(tags), "chunked_review"))

        tracker = TokenTracker()
        all_actions = []
        for i, chunk in enumerate(chunked(tags, 80), 1):
            print(f"Reviewing chunk {i} ({len(chunk)} tag(s))...")
            try:
                all_actions.extend(validate_actions(translate_review_chunk(chunk, settings.output_language, tracker), "tags_translate"))
            except Exception as exc:  # noqa: BLE001
                print(f"  ! chunk failed: {exc}")
            print(tracker.progress_line())

        if not all_actions:
            print(f"\nNothing to translate. {tracker.summary_line()}")
            return tracker

        all_actions = resolve_name_collisions(all_actions, tags)

        print_header(f"{len(all_actions)} suggested change(s)")
        by_id = {t["id"]: t for t in tags}
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
        return tracker


# ================================================================
# simplify (AI - finds semantic, not just spelling, duplicates)
# ================================================================

SIMPLIFY_SYSTEM_PROMPT = """You review recipe tags for duplicates in MEANING
(not just spelling) in a database whose target language is {language}. You
will receive a JSON array of tags, each {{"id": integer, "name": string}}.

Find groups of tags that clearly mean the same thing to a home cook, even
worded differently (e.g. "Schnelles Gericht" and "schnell", "Nachtisch" and
"Dessert", "Ofengericht" and "aus dem Ofen"). Be conservative: only merge
when they're genuinely the same concept, not just related or overlapping
(e.g. "Vegan" and "Vegetarisch" are NOT the same and must NOT be merged;
"Sommer" and "Grillen" are NOT the same either, even though many grilled
dishes are eaten in summer). Prefer keeping the shorter, more common-sounding
tag as the survivor.

Only include tags that need a change - do not list tags that are already
fine on their own. Respond with ONLY a JSON array (no explanation, no
markdown fence), each element:

{{"type": "merge", "keep_id": <id to keep>, "keep_name": <its name, unchanged unless it also needs a small fix>, "remove_ids": [<other ids meaning the same tag>]}}

If nothing needs merging, respond with [].
"""


def simplify_review_chunk(tags: list[dict], language: str, tracker: TokenTracker) -> list[dict]:
    system_prompt = SIMPLIFY_SYSTEM_PROMPT.replace("{language}", language)
    text_out, usage = llm_provider.complete_text(
        system_prompt,
        json.dumps([{"id": t["id"], "name": t["name"]} for t in tags], ensure_ascii=False),
        max_tokens=8000,
    )
    tracker.add(usage)
    text_out = text_out.strip().strip("`")
    if text_out.startswith("json"):
        text_out = text_out[4:]
    return json.loads(text_out)


def run_simplify(preview_count: int, apply: bool, assume_yes: bool) -> TokenTracker:
    if not llm_provider.is_configured():
        print(f"Error: {llm_provider.missing_key_hint()}", file=sys.stderr)
        sys.exit(1)

    with tandoor_client.get_client() as client:
        tags = tandoor_client.fetch_all_items(client, "keyword")
        if preview_count:
            tags = tags[:preview_count]
        print(f"{len(tags)} tag(s) selected.")
        print(format_cost_estimate(len(tags), "chunked_review"))

        tracker = TokenTracker()
        all_actions = []
        chunks = list(chunked(tags, 80))
        for i, chunk in enumerate(chunks, 1):
            print(f"Reviewing chunk {i}/{len(chunks)} ({len(chunk)} tag(s))...")
            try:
                all_actions.extend(validate_actions(simplify_review_chunk(chunk, settings.output_language, tracker), "tags_simplify"))
            except Exception as exc:  # noqa: BLE001
                print(f"  ! chunk failed: {exc}")
            print(tracker.progress_line())

        if not all_actions:
            print(f"\nNothing to merge - no duplicate meanings found. {tracker.summary_line()}")
            return tracker

        all_actions = resolve_name_collisions(all_actions, tags)

        print_header(f"{len(all_actions)} suggested merge(s)")
        by_id = {t["id"]: t for t in tags}
        merged, skipped, errors = 0, 0, 0

        for action in all_actions:
            keep_old = by_id.get(action["keep_id"], {}).get("name", "?")
            remove_names = ", ".join(f"{by_id.get(rid, {}).get('name', '?')!r} (#{rid})" for rid in action["remove_ids"])
            print(f"  merge into #{action['keep_id']} ({keep_old!r} -> {action['keep_name']!r}): remove {remove_names}")

            if not apply:
                continue
            if not assume_yes:
                answer = input("    Apply? [y/N] ").strip().lower()
                if answer != "y":
                    print("    skipped.")
                    skipped += 1
                    continue
            try:
                apply_merge(client, action["keep_id"], action["keep_name"], action["remove_ids"])
                print("    done.")
                merged += 1
            except Exception as exc:  # noqa: BLE001
                print(f"    ! error: {exc}")
                errors += 1

        print_header("Summary")
        print(tracker.summary_line())
        if not apply:
            print(f"Dry run: {len(all_actions)} merge(s) suggested. Re-run with --apply to make them.")
        else:
            print(f"{merged} merged, {skipped} skipped, {errors} error(s).")
        return tracker


# ================================================================
# season
# ================================================================

SEASON_WORDS = {
    "frühling", "fruhling", "spring", "printemps", "primavera",
    "sommer", "summer", "été", "ete", "estate", "verano",
    "herbst", "autumn", "fall", "automne", "autunno", "otoño", "otono",
    "winter", "hiver", "inverno", "invierno",
}

SEASON_SYSTEM_PROMPT = """You decide whether a recipe clearly belongs to one
season, the same way a cookbook-import tool would when first extracting it.
You will receive a JSON object: {{"title": string, "description": string|null,
"tags": [string]}}.

If the recipe clearly fits one season - based on its main ingredients (e.g.
asparagus/strawberries -> Spring, pumpkin/mushrooms -> Autumn, mulled
wine/cookies -> Winter) or an explicit mention - respond with that season,
translated into {language}, as ONLY that one word (no explanation, no
punctuation): one of "{spring}", "{summer}", "{autumn}", "{winter}".

If it's an everyday dish available year-round with no clear seasonal tie
(e.g. pasta with tomato sauce), respond with exactly: none
"""

SEASON_LABELS = {
    "de": ("Frühling", "Sommer", "Herbst", "Winter"),
    "en": ("Spring", "Summer", "Autumn", "Winter"),
    "fr": ("Printemps", "Été", "Automne", "Hiver"),
    "it": ("Primavera", "Estate", "Autunno", "Inverno"),
    "es": ("Primavera", "Verano", "Otoño", "Invierno"),
}


def has_season_tag(recipe: dict) -> bool:
    return any(kw.get("name", "").strip().lower() in SEASON_WORDS for kw in recipe.get("keywords", []))


def guess_season(recipe: dict, language: str, tracker: TokenTracker) -> str | None:
    labels = SEASON_LABELS.get(get_language_code(language) or "en", SEASON_LABELS["en"])
    system_prompt = SEASON_SYSTEM_PROMPT.format(
        language=language, spring=labels[0], summer=labels[1], autumn=labels[2], winter=labels[3]
    )
    user_content = json.dumps({
        "title": recipe.get("name", ""),
        "description": recipe.get("description"),
        "tags": [kw["name"] for kw in recipe.get("keywords", [])],
    }, ensure_ascii=False)
    text_out, usage = llm_provider.complete_text(system_prompt, user_content, max_tokens=20)
    tracker.add(usage)
    text_out = text_out.strip().strip(".").strip()
    if text_out.lower() == "none" or not text_out:
        return None
    if text_out not in labels:
        return None
    return text_out


def run_season(preview_count: int, apply: bool) -> TokenTracker:
    if not llm_provider.is_configured():
        print(f"Error: {llm_provider.missing_key_hint()}", file=sys.stderr)
        sys.exit(1)

    with tandoor_client.get_client() as client:
        print("Scanning every recipe's full detail once...")
        recipes = fetch_all_recipes_full(client)
        missing = [r for r in recipes if not has_season_tag(r)]
        print(f"{len(recipes)} recipe(s) total, {len(missing)} with no season tag yet.")

        if not preview_count and not apply:
            print(format_cost_estimate(len(missing), "per_recipe_tiny"))
            print("Dry run: no AI calls made. Use --preview N to check a sample, or --apply for everything.")
            for r in missing[:20]:
                print(f"  - {r.get('name', '')!r} (#{r['id']})")
            if len(missing) > 20:
                print(f"  ... and {len(missing) - 20} more")
            return TokenTracker()

        sample = missing[:preview_count] if preview_count else missing
        print(format_cost_estimate(len(sample), "per_recipe_tiny"))
        write_mode = apply and not preview_count
        tracker = TokenTracker()
        tagged, none_found, errors = 0, 0, 0

        for i, recipe in enumerate(sample, 1):
            try:
                season = guess_season(recipe, settings.output_language, tracker)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(sample)}] ! {recipe.get('name', '')!r}: {exc}")
                errors += 1
                continue

            if season is None:
                print(f"  [{i}/{len(sample)}] {recipe.get('name', '')!r}: no clear season.")
                none_found += 1
                continue

            print(f"  [{i}/{len(sample)}] {recipe.get('name', '')!r}: {season}")
            if i % 10 == 0:
                print(tracker.progress_line())

            if not write_mode:
                continue
            try:
                tag_id, _tag_name = tandoor_client._get_or_create(client, "keyword", season)
                current_ids = {kw["id"] for kw in recipe.get("keywords", [])}
                current_ids.add(tag_id)
                resp = client.patch(f"/recipe/{recipe['id']}/", json={"keywords": [{"id": kid} for kid in current_ids]})
                if resp.status_code in (200, 201):
                    tagged += 1
                else:
                    print(f"    ! could not tag: {resp.status_code} {resp.text[:200]}")
                    errors += 1
            except Exception as exc:  # noqa: BLE001
                print(f"    ! error: {exc}")
                errors += 1

        print_header("Summary")
        print(tracker.summary_line())
        if not write_mode:
            print(f"Preview only - nothing was written. {none_found} with no clear season, {errors} error(s).")
        else:
            print(f"{tagged} recipe(s) tagged, {none_found} with no clear season, {errors} error(s).")
        return tracker


# ================================================================
# suggest-more
# ================================================================

SUGGEST_MORE_SYSTEM_PROMPT = """You suggest additional tags for an under-
tagged recipe in a database whose target language is {language}. You will
receive a JSON object: {{"title": string, "description": string|null,
"ingredients": [string], "existing_tags": [string], "vocabulary": [string]}}.

"existing_tags" are tags this recipe already has (don't repeat these).
"vocabulary" is a sample of tags already used elsewhere in the collection -
STRONGLY prefer reusing one of these over inventing a new tag, if it
genuinely fits; only propose a new one when nothing in the vocabulary applies.

Suggest at most 3 tags, each a short {language} word or phrase in the same
style as the vocabulary (cuisine, meal type, diet, main ingredient, occasion,
season - whatever is genuinely obvious from the recipe, not a stretch).

Respond with ONLY a JSON array of strings (no explanation, no markdown
fence), e.g. ["Vegetarisch", "Herbst"]. If nothing fits, respond with [].
"""


def suggest_tags_for_recipe(recipe: dict, vocabulary: list[str], language: str, tracker: TokenTracker) -> list[str]:
    system_prompt = SUGGEST_MORE_SYSTEM_PROMPT.replace("{language}", language)
    ingredients = []
    for step in recipe.get("steps", []):
        for ing in step.get("ingredients", []):
            food = ing.get("food")
            if food and food.get("name"):
                ingredients.append(food["name"])
    user_content = json.dumps({
        "title": recipe.get("name", ""),
        "description": recipe.get("description"),
        "ingredients": ingredients,
        "existing_tags": [kw["name"] for kw in recipe.get("keywords", [])],
        "vocabulary": vocabulary,
    }, ensure_ascii=False)
    text_out, usage = llm_provider.complete_text(system_prompt, user_content, max_tokens=200)
    tracker.add(usage)
    text_out = text_out.strip().strip("`")
    if text_out.startswith("json"):
        text_out = text_out[4:]
    return json.loads(text_out)


def run_suggest_more(min_tags: int, preview_count: int, apply: bool) -> TokenTracker:
    if not llm_provider.is_configured():
        print(f"Error: {llm_provider.missing_key_hint()}", file=sys.stderr)
        sys.exit(1)

    with tandoor_client.get_client() as client:
        all_tags = tandoor_client.fetch_all_items(client, "keyword")
        vocabulary = [t["name"] for t in all_tags][:200]  # cap prompt size on huge tag lists

        print("Scanning every recipe's full detail once...")
        recipes = fetch_all_recipes_full(client)
        under_tagged = [r for r in recipes if len(r.get("keywords", [])) < min_tags]
        print(f"{len(recipes)} recipe(s) total, {len(under_tagged)} with fewer than {min_tags} tag(s).")

        if not preview_count and not apply:
            print(format_cost_estimate(len(under_tagged), "per_recipe_small"))
            print("Dry run: no AI calls made. Use --preview N to check a sample, or --apply for everything.")
            for r in under_tagged[:20]:
                print(f"  - {r.get('name', '')!r} (#{r['id']}) - {len(r.get('keywords', []))} tag(s)")
            if len(under_tagged) > 20:
                print(f"  ... and {len(under_tagged) - 20} more")
            return TokenTracker()

        sample = under_tagged[:preview_count] if preview_count else under_tagged
        print(format_cost_estimate(len(sample), "per_recipe_small"))
        write_mode = apply and not preview_count
        tracker = TokenTracker()
        tagged_count, none_count, errors = 0, 0, 0

        for i, recipe in enumerate(sample, 1):
            try:
                suggestions = suggest_tags_for_recipe(recipe, vocabulary, settings.output_language, tracker)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{i}/{len(sample)}] ! {recipe.get('name', '')!r}: {exc}")
                errors += 1
                continue

            if not suggestions:
                print(f"  [{i}/{len(sample)}] {recipe.get('name', '')!r}: nothing to add.")
                none_count += 1
                continue

            print(f"  [{i}/{len(sample)}] {recipe.get('name', '')!r}: +{suggestions}")
            if i % 10 == 0:
                print(tracker.progress_line())

            if not write_mode:
                continue
            try:
                current_ids = {kw["id"] for kw in recipe.get("keywords", [])}
                for tag_name in suggestions:
                    tag_id, _name = tandoor_client._get_or_create(client, "keyword", tag_name)
                    current_ids.add(tag_id)
                resp = client.patch(f"/recipe/{recipe['id']}/", json={"keywords": [{"id": kid} for kid in current_ids]})
                if resp.status_code in (200, 201):
                    tagged_count += 1
                else:
                    print(f"    ! could not tag: {resp.status_code} {resp.text[:200]}")
                    errors += 1
            except Exception as exc:  # noqa: BLE001
                print(f"    ! error: {exc}")
                errors += 1

        print_header("Summary")
        print(tracker.summary_line())
        if not write_mode:
            print(f"Preview only - nothing was written. {none_count} with nothing to add, {errors} error(s).")
        else:
            print(f"{tagged_count} recipe(s) tagged, {none_count} with nothing to add, {errors} error(s).")
        return tracker


# ================================================================
# all: runs simplify -> translate -> season -> suggest-more in sequence
# ================================================================

def run_all(preview_count: int, apply: bool, assume_yes: bool, min_tags: int) -> None:
    print_header("Step 1/4: simplify")
    t1 = run_simplify(preview_count, apply, assume_yes)
    print_header("Step 2/4: translate")
    t2 = run_translate(preview_count, apply, assume_yes)
    print_header("Step 3/4: season")
    t3 = run_season(preview_count, apply)
    print_header("Step 4/4: suggest-more")
    t4 = run_suggest_more(min_tags, preview_count, apply)

    total_in = t1.input_tokens + t2.input_tokens + t3.input_tokens + t4.input_tokens
    total_out = t1.output_tokens + t2.output_tokens + t3.output_tokens + t4.output_tokens
    total_calls = t1.calls + t2.calls + t3.calls + t4.calls
    print_header("All done")
    print(f"Total across all 4 steps: {total_in} input token(s), {total_out} output token(s), {total_calls} AI call(s).")


# ================================================================
# CLI
# ================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)

    p_all = sub.add_parser("all", help="Run simplify -> translate -> season -> suggest-more in order. Uses AI tokens.")
    p_all.add_argument("--preview", type=int, default=0, metavar="N", help="Limit every step to N items.")
    p_all.add_argument("--apply", action="store_true")
    p_all.add_argument("--yes", action="store_true", help="Don't ask per suggestion (simplify/translate).")
    p_all.add_argument("--min-tags", type=int, default=5, help="Recipes with fewer tags than this get suggest-more treatment.")

    p_simplify = sub.add_parser("simplify", help="AI-merge tags that mean the same thing. Uses AI tokens.")
    p_simplify.add_argument("--preview", type=int, default=0, metavar="N", help="Only review N tags.")
    p_simplify.add_argument("--apply", action="store_true")
    p_simplify.add_argument("--yes", action="store_true", help="Don't ask per suggestion.")

    p_translate = sub.add_parser("translate", help="Translate tags not already in OUTPUT_LANGUAGE. Uses AI tokens.")
    p_translate.add_argument("--preview", type=int, default=0, metavar="N", help="Only review N tags.")
    p_translate.add_argument("--apply", action="store_true")
    p_translate.add_argument("--yes", action="store_true", help="Don't ask per suggestion.")

    p_season = sub.add_parser("season", help="Backfill season tags on recipes missing one. Uses AI tokens.")
    p_season.add_argument("--preview", type=int, default=0, metavar="N")
    p_season.add_argument("--apply", action="store_true")

    p_more = sub.add_parser("suggest-more", help="Suggest extra tags for under-tagged recipes. Uses AI tokens.")
    p_more.add_argument("--min-tags", type=int, default=5, help="Recipes with fewer tags than this are considered under-tagged.")
    p_more.add_argument("--preview", type=int, default=0, metavar="N")
    p_more.add_argument("--apply", action="store_true")

    args = parser.parse_args()

    try:
        if args.mode == "all":
            run_all(args.preview, args.apply, args.yes, args.min_tags)
        elif args.mode == "translate":
            run_translate(args.preview, args.apply, args.yes)
        elif args.mode == "simplify":
            run_simplify(args.preview, args.apply, args.yes)
        elif args.mode == "season":
            run_season(args.preview, args.apply)
        elif args.mode == "suggest-more":
            run_suggest_more(args.min_tags, args.preview, args.apply)
    except TandoorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
