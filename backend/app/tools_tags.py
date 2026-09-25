from __future__ import annotations

import json
import logging
import uuid

from . import llm_provider, tandoor_client, tool_jobs
from .config import settings, get_language_code
from .schemas import ToolSuggestion
from .tandoor_helpers import chunked, delete_entity, entity_exists, fetch_all_recipes_full, find_recipes_by_filter, format_cost_estimate, resolve_name_collisions, validate_actions

log = logging.getLogger("tandoor-helper")


def _find_recipes_using_keyword(client, keyword_id):
    return find_recipes_by_filter(client, "keywords", keyword_id)


def _apply_rename(client, keyword_id, new_name):
    resp = client.patch(f"/keyword/{keyword_id}/", json={"name": new_name})
    if resp.status_code not in (200, 201):
        raise tandoor_client.TandoorError(f"Could not rename keyword #{keyword_id}: {resp.status_code} {resp.text[:300]}")


def _apply_merge(client, keep_id, keep_name, remove_ids):
    if not entity_exists(client, "keyword", keep_id):
        raise tandoor_client.TandoorError(
            f"Keyword #{keep_id} no longer exists (already merged by another suggestion?) - rescan to continue."
        )
    _apply_rename(client, keep_id, keep_name)
    for remove_id in remove_ids:
        if not entity_exists(client, "keyword", remove_id):
            continue  # already merged away by an earlier suggestion
        for recipe in _find_recipes_using_keyword(client, remove_id):
            current_ids = {kw["id"] for kw in recipe.get("keywords", [])}
            current_ids.discard(remove_id)
            current_ids.add(keep_id)
            resp = client.patch(f"/recipe/{recipe['id']}/", json={"keywords": [{"id": kid} for kid in current_ids]})
            if resp.status_code not in (200, 201):
                raise tandoor_client.TandoorError(f"Could not update recipe {recipe['id']}: {resp.status_code} {resp.text[:300]}")

        still_used = _find_recipes_using_keyword(client, remove_id)
        if still_used:
            raise tandoor_client.TandoorError(f"Still used by {len(still_used)} recipe(s) after repointing - not deleting #{remove_id}.")

        delete_entity(client, "keyword", remove_id)


def _describe_action(action, by_id):
    if action["type"] == "rename":
        old = by_id.get(action["id"], {}).get("name", "?")
        return f"rename {old!r} -> {action['new_name']!r}"
    keep_old = by_id.get(action["keep_id"], {}).get("name", "?")
    remove_names = ", ".join(f"{by_id.get(rid, {}).get('name', '?')!r}" for rid in action["remove_ids"])
    return f"merge {remove_names} into {keep_old!r} -> {action['keep_name']!r}"


def _run_tag_review_scan(job_id, system_prompt_template):
    job = tool_jobs.get_tool_job(job_id)
    if job is None:
        return
    try:
        if not llm_provider.is_configured():
            job.status = "error"
            job.error = llm_provider.missing_key_hint()
            tool_jobs.save_tool_job(job)
            return

        with tandoor_client.get_client() as client:
            tags = tandoor_client.fetch_all_items(client, "keyword")
            job.progress_total = len(tags)
            job.cost_estimate = format_cost_estimate(len(tags), "chunked_review")
            tool_jobs.save_tool_job(job)

            all_actions = []
            chunks = list(chunked(tags, 80))
            for i, chunk in enumerate(chunks, 1):
                if job.cancel_requested:
                    break
                job.progress_label = f"Reviewing chunk {i}/{len(chunks)}..."
                tool_jobs.save_tool_job(job)
                try:
                    system_prompt = system_prompt_template.replace("{language}", settings.output_language)
                    text_out, usage = llm_provider.complete_tool_text(
                        system_prompt,
                        json.dumps([{"id": t["id"], "name": t["name"]} for t in chunk], ensure_ascii=False),
                        max_tokens=8000,
                    )
                    text_out = text_out.strip().strip("`")
                    if text_out.startswith("json"):
                        text_out = text_out[4:]
                    all_actions.extend(validate_actions(json.loads(text_out), "tags_review"))
                    job.token_usage.input_tokens += getattr(usage, "input_tokens", 0) or 0
                    job.token_usage.output_tokens += getattr(usage, "output_tokens", 0) or 0
                except Exception as exc:  # noqa: BLE001
                    log.warning("Tag review chunk %d failed: %s", i, exc)
                job.progress_current = i * 80
                tool_jobs.save_tool_job(job)

            all_actions = resolve_name_collisions(all_actions, tags)
            by_id = {t["id"]: t for t in tags}
            job.suggestions = [
                ToolSuggestion(id=uuid.uuid4().hex[:10], kind=action["type"], summary=_describe_action(action, by_id), detail=action)
                for action in all_actions
            ]
            job.status = "cancelled" if job.cancel_requested else "ready"
            job.progress_label = None
            tool_jobs.save_tool_job(job)

    except Exception as exc:  # noqa: BLE001
        log.exception("Tag review scan failed for job %s", job_id)
        job.status = "error"
        job.error = str(exc)
        tool_jobs.save_tool_job(job)


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


CLEANUP_SYSTEM_PROMPT = """You clean up recipe tags in a database whose
target language is {language}. You will receive a JSON array of existing
tags, each {{"id": integer, "name": string}}.

Do BOTH of these in one pass:
1. Translate: any tag whose name is NOT already in {language} gets a natural
   {language} name (a single word or short phrase in the style of a recipe
   tag, not a literal word-for-word translation).
2. Simplify: tags that clearly mean the same thing to a home cook - after
   translation, and even when worded differently (e.g. "cakes", "cake" and
   "Kuchen"; "Schnelles Gericht" and "schnell"; "Nachtisch" and "Dessert")
   - get merged into ONE tag. Be conservative: only merge genuinely identical
   concepts, not merely related ones ("Vegan" and "Vegetarisch" are NOT the
   same; "Sommer" and "Grillen" are NOT the same). Prefer keeping the entry
   that is already a clean {language} name; among those, the shorter, more
   common-sounding one.

Every tag id may appear in AT MOST ONE element of your answer - put all ids
of one concept into a single merge instead of listing a rename and a merge
for the same tag. Only include tags that actually need a change. Respond
with ONLY a JSON array (no explanation, no markdown fence), each element one
of:

{{"type": "rename", "id": <id>, "new_name": <translated name>}}
{{"type": "merge", "keep_id": <id to keep>, "keep_name": <name for it, translated if needed>, "remove_ids": [<other ids that mean the same tag>]}}

If nothing needs a change, respond with [].
"""


def run_cleanup_scan(job_id: str) -> None:
    """Translate + simplify in one AI pass - running them as two separate
    tools meant a tag could be renamed by one and merged by the other, and
    each pass only saw half the picture ("cakes" -> "Kuchen" is both)."""
    _run_tag_review_scan(job_id, CLEANUP_SYSTEM_PROMPT)


def run_simplify_scan(job_id: str) -> None:
    _run_tag_review_scan(job_id, SIMPLIFY_SYSTEM_PROMPT)


def run_translate_scan(job_id: str) -> None:
    _run_tag_review_scan(job_id, TRANSLATE_SYSTEM_PROMPT)


def apply_rename_or_merge_suggestion(job_id: str, suggestion_id: str) -> ToolSuggestion:
    job = tool_jobs.get_tool_job(job_id)
    if job is None:
        raise tandoor_client.TandoorError("Job not found.")
    suggestion = next((s for s in job.suggestions if s.id == suggestion_id), None)
    if suggestion is None:
        raise tandoor_client.TandoorError("Suggestion not found.")
    if suggestion.status != "pending":
        return suggestion

    action = suggestion.detail
    try:
        with tandoor_client.get_client() as client:
            if action["type"] == "rename":
                _apply_rename(client, action["id"], action["new_name"])
            else:
                _apply_merge(client, action["keep_id"], action["keep_name"], action["remove_ids"])
        suggestion.status = "applied"
    except Exception as exc:  # noqa: BLE001
        suggestion.status = "error"
        suggestion.error = str(exc)
    finally:
        tool_jobs.save_tool_job(job)
    return suggestion


SEASON_WORDS = {
    "frühling", "fruhling", "spring", "printemps", "primavera",
    "sommer", "summer", "été", "ete", "estate", "verano",
    "herbst", "autumn", "fall", "automne", "autunno", "otoño", "otono",
    "winter", "hiver", "inverno", "invierno",
}

SEASON_SYSTEM_PROMPT = """You decide whether recipes clearly belong to one
season, the same way a cookbook-import tool would when first extracting
them. You will receive a JSON array of recipes, each {"id": integer,
"title": string, "description": string|null, "ingredients": [string],
"existing_tags": [string]}.

A recipe clearly fits one season if its main ingredients say so (e.g.
asparagus/strawberries -> Spring, pumpkin/mushrooms -> Autumn, mulled
wine/cookies -> Winter) or it's mentioned explicitly. Then answer that
season, translated into {language}: one of "{spring}", "{summer}",
"{autumn}", "{winter}". An everyday dish available year-round with no clear
seasonal tie (e.g. pasta with tomato sauce) gets null.

Respond with ONLY a JSON array (no explanation, no markdown fence), one
element per recipe: {"id": <id>, "season": <one of the four words, or null>}
"""

# Recipes per AI call for the season check - the answer per recipe is tiny,
# so the prompt is the main input cost and is now sent once per batch.
SEASON_BATCH_SIZE = 25
SEASON_MAX_INGREDIENTS = 10

SEASON_LABELS = {
    "de": ("Frühling", "Sommer", "Herbst", "Winter"),
    "en": ("Spring", "Summer", "Autumn", "Winter"),
    "fr": ("Printemps", "Été", "Automne", "Hiver"),
    "it": ("Primavera", "Estate", "Autunno", "Inverno"),
    "es": ("Primavera", "Verano", "Otoño", "Invierno"),
}


def has_season_tag(recipe):
    return any(kw.get("name", "").strip().lower() in SEASON_WORDS for kw in recipe.get("keywords", []))


def season_suggestions(job, recipes) -> list[ToolSuggestion]:
    """Batched season check for the given recipes (callers pass only ones
    without a season tag). Adds token usage to `job`, stops early on cancel.
    Shared by the "Tags: add season" tool and the new-recipes workflow."""
    labels = SEASON_LABELS.get(get_language_code(settings.output_language) or "en", SEASON_LABELS["en"])
    # Plain replace, not str.format(): the prompt's JSON braces would
    # otherwise be read as format fields.
    system_prompt = SEASON_SYSTEM_PROMPT
    for key, value in (("language", settings.output_language), ("spring", labels[0]),
                       ("summer", labels[1]), ("autumn", labels[2]), ("winter", labels[3])):
        system_prompt = system_prompt.replace("{" + key + "}", value)
    labels_by_lower = {label.lower(): label for label in labels}

    by_id = {r["id"]: r for r in recipes}
    suggestions = []
    batches = list(chunked(recipes, SEASON_BATCH_SIZE))
    for i, batch in enumerate(batches, 1):
        if job.cancel_requested:
            break
        job.progress_label = f"Checking seasons, batch {i}/{len(batches)}..."
        tool_jobs.save_tool_job(job)
        try:
            compact = []
            for recipe in batch:
                entry = _compact_recipe(recipe)
                entry["ingredients"] = entry["ingredients"][:SEASON_MAX_INGREDIENTS]
                compact.append(entry)
            answers = _complete_json(job, system_prompt, compact, max_tokens=25 * len(batch) + 100)
        except Exception as exc:  # noqa: BLE001
            log.warning("Season batch %d failed: %s", i, exc)
            answers = []

        for answer in answers:
            recipe = by_id.get(answer.get("id")) if isinstance(answer, dict) else None
            season = answer.get("season") if recipe else None
            season = labels_by_lower.get(season.strip().strip(".").lower()) if isinstance(season, str) else None
            if season is None:
                continue
            suggestions.append(ToolSuggestion(
                id=uuid.uuid4().hex[:10], kind="season",
                summary=f"tag {recipe.get('name', '')!r} as {season!r}",
                detail={"recipe_id": recipe["id"], "season": season},
            ))
        job.progress_current = min(i * SEASON_BATCH_SIZE, len(recipes))
        tool_jobs.save_tool_job(job)
    return suggestions


def _complete_json(job, system_prompt, payload, max_tokens):
    """One AI call with a JSON payload; adds token usage to the job and
    returns the parsed JSON answer."""
    text_out, usage = llm_provider.complete_tool_text(system_prompt, json.dumps(payload, ensure_ascii=False), max_tokens=max_tokens)
    job.token_usage.input_tokens += getattr(usage, "input_tokens", 0) or 0
    job.token_usage.output_tokens += getattr(usage, "output_tokens", 0) or 0
    text_out = text_out.strip().strip("`")
    if text_out.startswith("json"):
        text_out = text_out[4:]
    return json.loads(text_out)


def run_season_scan(job_id: str) -> None:
    job = tool_jobs.get_tool_job(job_id)
    if job is None:
        return
    try:
        if not llm_provider.is_configured():
            job.status = "error"
            job.error = llm_provider.missing_key_hint()
            tool_jobs.save_tool_job(job)
            return

        with tandoor_client.get_client() as client:
            job.progress_label = "Scanning every recipe's full detail..."
            tool_jobs.save_tool_job(job)
            recipes = fetch_all_recipes_full(client)
            missing = [r for r in recipes if not has_season_tag(r)]
            job.progress_total = len(missing)
            job.cost_estimate = format_cost_estimate(len(missing), "batched_season")
            tool_jobs.save_tool_job(job)

            job.suggestions = season_suggestions(job, missing)
            job.status = "cancelled" if job.cancel_requested else "ready"
            job.progress_label = None
            tool_jobs.save_tool_job(job)

    except Exception as exc:  # noqa: BLE001
        log.exception("Season scan failed for job %s", job_id)
        job.status = "error"
        job.error = str(exc)
        tool_jobs.save_tool_job(job)


def apply_season_suggestion(job_id: str, suggestion_id: str) -> ToolSuggestion:
    job = tool_jobs.get_tool_job(job_id)
    if job is None:
        raise tandoor_client.TandoorError("Job not found.")
    suggestion = next((s for s in job.suggestions if s.id == suggestion_id), None)
    if suggestion is None:
        raise tandoor_client.TandoorError("Suggestion not found.")
    if suggestion.status != "pending":
        return suggestion

    try:
        with tandoor_client.get_client() as client:
            recipe_id = suggestion.detail["recipe_id"]
            season = suggestion.detail["season"]
            resp = client.get(f"/recipe/{recipe_id}/")
            resp.raise_for_status()
            recipe = resp.json()
            tag_id, _name = tandoor_client._get_or_create(client, "keyword", season)
            current_ids = {kw["id"] for kw in recipe.get("keywords", [])}
            current_ids.add(tag_id)
            resp = client.patch(f"/recipe/{recipe_id}/", json={"keywords": [{"id": kid} for kid in current_ids]})
            if resp.status_code not in (200, 201):
                raise tandoor_client.TandoorError(f"{resp.status_code} {resp.text[:300]}")
        suggestion.status = "applied"
    except Exception as exc:  # noqa: BLE001
        suggestion.status = "error"
        suggestion.error = str(exc)
    finally:
        tool_jobs.save_tool_job(job)
    return suggestion


SUGGEST_MORE_SYSTEM_PROMPT = """You suggest additional tags for under-tagged
recipes in a database whose target language is {language}. You will receive
a JSON object: {"vocabulary": [string], "recipes": [{"id": integer,
"title": string, "description": string|null, "ingredients": [string],
"existing_tags": [string]}, ...]}.

"vocabulary" is the list of tags already used in the collection - STRONGLY
prefer reusing one of these over inventing a new tag, if it genuinely fits;
only propose a new one when nothing in the vocabulary applies.
"existing_tags" are tags a recipe already has (don't repeat these).

For each recipe, suggest at most 3 tags, each a short {language} word or
phrase in the same style as the vocabulary (cuisine, meal type, diet, main
ingredient, occasion, season - whatever is genuinely obvious from the
recipe, not a stretch).

Respond with ONLY a JSON array (no explanation, no markdown fence), one
element per recipe: {"id": <id>, "tags": [string, ...]}. Use an empty
"tags" list if nothing fits.
"""

MIN_TAGS_DEFAULT = 5
# Recipes per AI call. The prompt and the tag vocabulary (the bulk of the
# input) are sent once per batch instead of once per recipe.
SUGGEST_MORE_BATCH_SIZE = 20
MAX_VOCABULARY = 200
MAX_DESCRIPTION_CHARS = 200
MAX_INGREDIENTS = 20


def _compact_recipe(recipe):
    """Just enough of a recipe to judge its tags - no steps, a shortened
    description, and each ingredient name only once."""
    ingredients = []
    for step in recipe.get("steps", []):
        for ing in step.get("ingredients", []):
            name = (ing.get("food") or {}).get("name")
            if name and name not in ingredients:
                ingredients.append(name)
    description = (recipe.get("description") or "").strip()
    return {
        "id": recipe["id"],
        "title": recipe.get("name", ""),
        "description": description[:MAX_DESCRIPTION_CHARS] or None,
        "ingredients": ingredients[:MAX_INGREDIENTS],
        "existing_tags": [kw["name"] for kw in recipe.get("keywords", [])],
    }


def tag_vocabulary(all_tags, recipes) -> list[str]:
    """Existing tag names, most-used first, so the MAX_VOCABULARY cap keeps
    the ones that matter instead of whatever sorts first alphabetically."""
    usage = {}
    for recipe in recipes:
        for kw in recipe.get("keywords", []):
            usage[kw.get("name")] = usage.get(kw.get("name"), 0) + 1
    return sorted((t["name"] for t in all_tags), key=lambda n: -usage.get(n, 0))[:MAX_VOCABULARY]


def suggest_tags_suggestions(job, recipes, vocabulary) -> list[ToolSuggestion]:
    """Batched "suggest more tags" for the given recipes. Adds token usage to
    `job`, stops early on cancel. Shared by the "Tags: suggest more" tool and
    the new-recipes workflow."""
    system_prompt = SUGGEST_MORE_SYSTEM_PROMPT.replace("{language}", settings.output_language)
    by_id = {r["id"]: r for r in recipes}
    suggestions = []
    batches = list(chunked(recipes, SUGGEST_MORE_BATCH_SIZE))
    for i, batch in enumerate(batches, 1):
        if job.cancel_requested:
            break
        job.progress_label = f"Suggesting tags, batch {i}/{len(batches)}..."
        tool_jobs.save_tool_job(job)
        try:
            answers = _complete_json(
                job, system_prompt,
                {"vocabulary": vocabulary, "recipes": [_compact_recipe(r) for r in batch]},
                max_tokens=60 * len(batch) + 200,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Suggest-more batch %d failed: %s", i, exc)
            answers = []

        for answer in answers:
            recipe = by_id.get(answer.get("id")) if isinstance(answer, dict) else None
            if recipe is None:
                continue
            existing = {kw["name"].strip().lower() for kw in recipe.get("keywords", [])}
            tags = [t.strip() for t in answer.get("tags") or [] if isinstance(t, str) and t.strip()]
            tags = [t for t in dict.fromkeys(tags) if t.lower() not in existing][:3]
            if not tags:
                continue
            suggestions.append(ToolSuggestion(
                id=uuid.uuid4().hex[:10], kind="suggest_tags",
                summary=f"add {tags} to {recipe.get('name', '')!r}",
                detail={"recipe_id": recipe["id"], "tags": tags},
            ))
        job.progress_current = min(i * SUGGEST_MORE_BATCH_SIZE, len(recipes))
        tool_jobs.save_tool_job(job)
    return suggestions


def run_suggest_more_scan(job_id: str) -> None:
    job = tool_jobs.get_tool_job(job_id)
    if job is None:
        return
    try:
        if not llm_provider.is_configured():
            job.status = "error"
            job.error = llm_provider.missing_key_hint()
            tool_jobs.save_tool_job(job)
            return

        with tandoor_client.get_client() as client:
            all_tags = tandoor_client.fetch_all_items(client, "keyword")

            job.progress_label = "Scanning every recipe's full detail..."
            tool_jobs.save_tool_job(job)
            recipes = fetch_all_recipes_full(client)
            under_tagged = [r for r in recipes if len(r.get("keywords", [])) < MIN_TAGS_DEFAULT]
            job.progress_total = len(under_tagged)
            job.cost_estimate = format_cost_estimate(len(under_tagged), "batched_suggest_tags")
            tool_jobs.save_tool_job(job)

            job.suggestions = suggest_tags_suggestions(job, under_tagged, tag_vocabulary(all_tags, recipes))
            job.status = "cancelled" if job.cancel_requested else "ready"
            job.progress_label = None
            tool_jobs.save_tool_job(job)

    except Exception as exc:  # noqa: BLE001
        log.exception("Suggest-more scan failed for job %s", job_id)
        job.status = "error"
        job.error = str(exc)
        tool_jobs.save_tool_job(job)


def apply_suggest_tags_suggestion(job_id: str, suggestion_id: str) -> ToolSuggestion:
    job = tool_jobs.get_tool_job(job_id)
    if job is None:
        raise tandoor_client.TandoorError("Job not found.")
    suggestion = next((s for s in job.suggestions if s.id == suggestion_id), None)
    if suggestion is None:
        raise tandoor_client.TandoorError("Suggestion not found.")
    if suggestion.status != "pending":
        return suggestion

    try:
        with tandoor_client.get_client() as client:
            recipe_id = suggestion.detail["recipe_id"]
            resp = client.get(f"/recipe/{recipe_id}/")
            resp.raise_for_status()
            recipe = resp.json()
            current_ids = {kw["id"] for kw in recipe.get("keywords", [])}
            for tag_name in suggestion.detail["tags"]:
                tag_id, _name = tandoor_client._get_or_create(client, "keyword", tag_name)
                current_ids.add(tag_id)
            resp = client.patch(f"/recipe/{recipe_id}/", json={"keywords": [{"id": kid} for kid in current_ids]})
            if resp.status_code not in (200, 201):
                raise tandoor_client.TandoorError(f"{resp.status_code} {resp.text[:300]}")
        suggestion.status = "applied"
    except Exception as exc:  # noqa: BLE001
        suggestion.status = "error"
        suggestion.error = str(exc)
    finally:
        tool_jobs.save_tool_job(job)
    return suggestion


def apply_suggestion(job_id: str, suggestion_id: str) -> ToolSuggestion:
    """Dispatches to the right apply function based on the suggestion's kind -
    the single entry point main.py calls, regardless of which of the tag
    tools produced the suggestion."""
    job = tool_jobs.get_tool_job(job_id)
    if job is None:
        raise tandoor_client.TandoorError("Job not found.")
    suggestion = next((s for s in job.suggestions if s.id == suggestion_id), None)
    if suggestion is None:
        raise tandoor_client.TandoorError("Suggestion not found.")

    if suggestion.kind in ("rename", "merge"):
        return apply_rename_or_merge_suggestion(job_id, suggestion_id)
    if suggestion.kind == "season":
        return apply_season_suggestion(job_id, suggestion_id)
    if suggestion.kind == "suggest_tags":
        return apply_suggest_tags_suggestion(job_id, suggestion_id)
    raise tandoor_client.TandoorError(f"Unknown suggestion kind: {suggestion.kind}")
