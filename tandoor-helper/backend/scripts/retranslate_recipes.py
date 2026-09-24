#!/usr/bin/env python3
"""
Re-translates recipes ALREADY in Tandoor into the language currently set as
OUTPUT_LANGUAGE - useful if you change that setting after having imported
cookbooks in a different language, without re-importing everything from the
source PDFs.

Recipes already in the target language are detected (via langdetect) and
skipped automatically - no AI call is made for them, so re-running this
after a partial run, or on a collection that's already mostly in the target
language, doesn't cost anything for the recipes that don't need it.

Only the recipe's OWN text is touched: title, description, step titles and
instructions, and the recipe-specific ingredient notes (e.g. "finely
chopped"). Ingredient/unit/tag NAMES are deliberately left alone - those
are shared entities used by many other recipes too, and translating a shared
food's name would silently change how it displays everywhere else, which is
a much bigger and riskier operation than this script is meant for.

The same tool is available in the web UI (🔧 Tools -> "Recipes: translate");
both share the logic in app/tools_recipes.py.

💰 Uses AI tokens: one real API call per recipe that isn't already in the
target language (same provider/model as AI_PROVIDER). Token usage prints as
it accumulates and a total at the end. Use --preview first to sample a few
recipes and check quality/cost before committing to the whole collection.

Usage (run inside the container, from the backend/ directory):

    python scripts/retranslate_recipes.py --preview 3          # translate 3 sample recipes, print before/after, write nothing
    python scripts/retranslate_recipes.py --keyword "Italienisch"   # only recipes carrying this tag
    python scripts/retranslate_recipes.py --apply               # translate + write everything (after you've sanity-checked with --preview!)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm_provider, tandoor_client  # noqa: E402
from app.config import get_language_code, settings  # noqa: E402
from app.tandoor_client import TandoorError  # noqa: E402
from app.tools_recipes import (  # noqa: E402
    already_in_target_language,
    build_update_payload,
    describe_changes,
    translate_recipe_text,
)
from scripts._shared import TokenTracker, fetch_all_recipes_full, format_cost_estimate, print_header  # noqa: E402


def run(keyword_filter: str | None, preview_count: int, apply: bool) -> None:
    if not llm_provider.is_configured():
        print(f"Error: {llm_provider.missing_key_hint()}", file=sys.stderr)
        sys.exit(1)

    expected_code = get_language_code(settings.output_language)
    if not expected_code:
        print(f"Note: OUTPUT_LANGUAGE={settings.output_language!r} isn't one this script recognizes for "
              f"skip-detection - every recipe will be sent for translation (nothing will be skipped).")

    with tandoor_client.get_client() as client:
        if keyword_filter:
            keywords = tandoor_client.fetch_all_items(client, "keyword")
            match = next((k for k in keywords if k["name"].strip().lower() == keyword_filter.strip().lower()), None)
            if not match:
                print(f"No keyword named {keyword_filter!r} found.")
                return
            resp = client.get("/recipe/", params={"keywords": match["id"], "page_size": 200})
            resp.raise_for_status()
            data = resp.json()
            compact = data.get("results", data) if isinstance(data, dict) else data
            recipes = [client.get(f"/recipe/{r['id']}/").json() for r in compact]
        else:
            recipes = fetch_all_recipes_full(client, max_recipes=preview_count or None)

        print(f"{len(recipes)} recipe(s) selected. Target language: {settings.output_language}")

        if preview_count:
            recipes = recipes[:preview_count]
            print(format_cost_estimate(len(recipes), "per_recipe_translate"))
            print(f"--preview: checking {len(recipes)} sample recipe(s), writing nothing.\n")
        elif not apply:
            # Skip-detection is free (langdetect, no AI call), so estimate against
            # only the recipes that would actually need a translation call, not
            # the full selected count - much more accurate than assuming all of
            # them need it.
            needing_translation = sum(1 for r in recipes if not already_in_target_language(r, expected_code))
            print(format_cost_estimate(needing_translation, "per_recipe_translate"))
            print("Dry run: nothing will be translated or written. Use --preview N to see a sample, "
                  "or --apply to translate and write everything listed above.")
            return

        needing_translation = sum(1 for r in recipes if not already_in_target_language(r, expected_code))
        print(format_cost_estimate(needing_translation, "per_recipe_translate"))

        tracker = TokenTracker()
        translated_count, skipped_count, errors = 0, 0, 0

        for i, recipe in enumerate(recipes, 1):
            print_header(f"[{i}/{len(recipes)}] #{recipe['id']}  {recipe.get('name', '')}")

            if already_in_target_language(recipe, expected_code):
                print(f"  already {settings.output_language} - skipping (no AI call).")
                skipped_count += 1
                continue

            try:
                translated, usage = translate_recipe_text(recipe, settings.output_language)
                tracker.add(usage)
            except Exception as exc:  # noqa: BLE001
                print(f"  ! translation failed: {exc}")
                errors += 1
                continue

            for line in (describe_changes(recipe, translated) or "(no changes)").splitlines():
                print(f"  {line}")
            print(tracker.progress_line())

            if preview_count:
                continue  # preview mode: never writes

            payload = build_update_payload(recipe, translated)
            resp = client.patch(f"/recipe/{recipe['id']}/", json=payload)
            if resp.status_code in (200, 201):
                print("  written.")
                translated_count += 1
            else:
                print(f"  ! could not write: {resp.status_code} {resp.text[:200]}")
                errors += 1

        print_header("Summary")
        print(tracker.summary_line())
        if preview_count:
            print(f"Preview only - nothing was written. {skipped_count} already-{settings.output_language}, {errors} error(s).")
        else:
            print(f"{translated_count} translated and written, {skipped_count} already-{settings.output_language} "
                  f"(skipped, no AI call), {errors} error(s).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keyword", help="Only recipes carrying this exact tag (default: all recipes).")
    parser.add_argument("--preview", type=int, default=0, metavar="N",
                         help="Translate and print N sample recipes without writing anything.")
    parser.add_argument("--apply", action="store_true", help="Translate and write every selected recipe.")
    args = parser.parse_args()

    try:
        run(args.keyword, args.preview, args.apply)
    except TandoorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
