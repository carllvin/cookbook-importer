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

Only the recipe's OWN text is touched: title, description, and step
instructions. Ingredient/unit/tag NAMES are deliberately left alone - those
are shared entities used by many other recipes too, and translating a shared
food's name would silently change how it displays everywhere else, which is
a much bigger and riskier operation than this script is meant for.

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
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import llm_provider, tandoor_client  # noqa: E402
from app.config import get_language_code, settings  # noqa: E402
from app.tandoor_client import TandoorError  # noqa: E402
from scripts._shared import TokenTracker, fetch_all_recipes_full, format_cost_estimate, print_header  # noqa: E402

SYSTEM_PROMPT = """You translate cookbook recipe text into {language}.

You will receive a JSON object with "title", "description" (may be null),
and "steps" (a list of instruction strings). Translate every text value
naturally into {language}, preserving meaning and a natural recipe-writing
tone - not a literal word-for-word translation.

CRITICAL: some step instructions contain Jinja template placeholders like
"{{ ingredients[0] }}" or "{{ scale(200) }}" or Jinja comments like
"{# note #}". Leave every such {{ ... }} or {# ... #} block EXACTLY as
written, character for character, including its exact index number - only
translate the surrounding prose text around it.

Respond with ONLY a JSON object of the same shape - "title", "description",
"steps" (same number of steps, same order) - no explanation, no markdown
code fence.
"""


def recipe_text_blob(recipe: dict) -> str:
    parts = [recipe.get("name") or "", recipe.get("description") or ""]
    parts += [s.get("instruction") or "" for s in recipe.get("steps", [])]
    return " ".join(p for p in parts if p).strip()


def already_in_target_language(recipe: dict, expected_code: str | None) -> bool:
    """Best-effort language check via langdetect, so a recipe already in the
    target language costs no AI call at all. If the code can't tell (too
    little text, or OUTPUT_LANGUAGE isn't one langdetect recognizes), this
    returns False - safer to translate unnecessarily than to silently skip a
    recipe that actually needed it."""
    if not expected_code:
        return False
    text = recipe_text_blob(recipe)
    if len(text) < 8:
        return False
    try:
        from langdetect import detect
        return detect(text) == expected_code
    except Exception:  # noqa: BLE001 - langdetect raises its own exception type for "can't tell"
        return False


def translate_recipe_text(recipe: dict, language: str):
    payload = {
        "title": recipe.get("name", ""),
        "description": recipe.get("description"),
        "steps": [s.get("instruction", "") for s in recipe.get("steps", [])],
    }
    # Plain replace, not str.format(): the prompt's own {{ ... }} / {# ... #}
    # Jinja examples would otherwise be misread as format fields.
    system_prompt = SYSTEM_PROMPT.replace("{language}", language)
    text_out, usage = llm_provider.complete_text(
        system_prompt, json.dumps(payload, ensure_ascii=False), max_tokens=4000
    )
    text_out = text_out.strip()
    if text_out.startswith("```"):
        text_out = text_out.strip("`")
        if text_out.startswith("json"):
            text_out = text_out[4:]
    result = json.loads(text_out)
    if len(result.get("steps", [])) != len(payload["steps"]):
        raise ValueError(
            f"Translation returned {len(result.get('steps', []))} step(s), expected {len(payload['steps'])} - "
            f"refusing to apply this (would misalign steps and their ingredients)."
        )
    return result, usage


def build_update_payload(recipe: dict, translated: dict) -> dict:
    new_steps = []
    for step, new_instruction in zip(recipe.get("steps", []), translated["steps"]):
        new_step = dict(step)
        new_step["instruction"] = new_instruction
        # Ingredients are preserved completely untouched, including food/unit -
        # this script never touches them.
        new_step["ingredients"] = [dict(ing) for ing in step.get("ingredients", [])]
        for ing in new_step["ingredients"]:
            for field in ("food", "unit"):
                ref = ing.get(field)
                if ref is not None:
                    ing[field] = {"id": ref["id"], "name": ref.get("name", "")}
        new_steps.append(new_step)

    return {
        "name": translated["title"][:128],
        "description": (translated.get("description") or "")[:512],
        "steps": new_steps,
    }


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

            print(f"  title:  {recipe.get('name', '')!r}\n      -> {translated['title']!r}")
            if recipe.get("description") or translated.get("description"):
                print(f"  descr:  {recipe.get('description')!r}\n      -> {translated.get('description')!r}")
            for old_step, new_instruction in zip(recipe.get("steps", []), translated["steps"]):
                print(f"  step:   {old_step.get('instruction', '')!r}\n      -> {new_instruction!r}")
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
