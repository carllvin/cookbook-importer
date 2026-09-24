from __future__ import annotations

import json
import logging
import uuid

from . import llm_provider, tandoor_client, tool_jobs
from .config import settings, get_language_code
from .schemas import ToolSuggestion
from .tandoor_helpers import chunked, fetch_all_recipes_full, find_recipes_by_filter, format_cost_estimate, resolve_name_collisions, validate_actions

log = logging.getLogger("tandoor-helper")


def _find_recipes_using_keyword(client, keyword_id):
    return find_recipes_by_filter(client, "keywords", keyword_id)


def _apply_rename(client, keyword_id, new_name):
    resp = client.patch(f"/keyword/{keyword_id}/", json={"name": new_name})
    if resp.status_code not in (200, 201):
        raise tandoor_client.TandoorError(f"Could not rename keyword #{keyword_id}: {resp.status_code} {resp.text[:300]}")


def _apply_merge(client, keep_id, keep_name, remove_ids):
    _apply_rename(client, keep_id, keep_name)
    for remove_id in remove_ids:
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

        resp = client.delete(f"/keyword/{remove_id}/")
        if resp.status_code not in (200, 202, 204):
            raise tandoor_client.TandoorError(f"Could not delete keyword #{remove_id}: {resp.status_code} {resp.text[:200]}")


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
                    text_out, usage = llm_provider.complete_text(
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


def _has_season_tag(recipe):
    return any(kw.get("name", "").strip().lower() in SEASON_WORDS for kw in recipe.get("keywords", []))


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
            missing = [r for r in recipes if not _has_season_tag(r)]
            job.progress_total = len(missing)
            job.cost_estimate = format_cost_estimate(len(missing), "per_recipe_tiny")
            tool_jobs.save_tool_job(job)

            labels = SEASON_LABELS.get(get_language_code(settings.output_language) or "en", SEASON_LABELS["en"])
            system_prompt = SEASON_SYSTEM_PROMPT.format(
                language=settings.output_language, spring=labels[0], summer=labels[1], autumn=labels[2], winter=labels[3]
            )

            suggestions = []
            for i, recipe in enumerate(missing, 1):
                if job.cancel_requested:
                    break
                job.progress_current = i
                job.progress_label = f"Checking recipe {i}/{len(missing)}..."
                tool_jobs.save_tool_job(job)
                try:
                    user_content = json.dumps({
                        "title": recipe.get("name", ""),
                        "description": recipe.get("description"),
                        "tags": [kw["name"] for kw in recipe.get("keywords", [])],
                    }, ensure_ascii=False)
                    text_out, usage = llm_provider.complete_text(system_prompt, user_content, max_tokens=20)
                    job.token_usage.input_tokens += getattr(usage, "input_tokens", 0) or 0
                    job.token_usage.output_tokens += getattr(usage, "output_tokens", 0) or 0
                    season = text_out.strip().strip(".").strip()
                    if season.lower() == "none" or season not in labels:
                        continue
                    suggestions.append(ToolSuggestion(
                        id=uuid.uuid4().hex[:10], kind="season",
                        summary=f"tag {recipe.get('name', '')!r} as {season!r}",
                        detail={"recipe_id": recipe["id"], "season": season},
                    ))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Season check failed for recipe %s: %s", recipe.get("id"), exc)

            job.suggestions = suggestions
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

MIN_TAGS_DEFAULT = 5


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
            vocabulary = [t["name"] for t in all_tags][:200]

            job.progress_label = "Scanning every recipe's full detail..."
            tool_jobs.save_tool_job(job)
            recipes = fetch_all_recipes_full(client)
            under_tagged = [r for r in recipes if len(r.get("keywords", [])) < MIN_TAGS_DEFAULT]
            job.progress_total = len(under_tagged)
            job.cost_estimate = format_cost_estimate(len(under_tagged), "per_recipe_small")
            tool_jobs.save_tool_job(job)

            system_prompt = SUGGEST_MORE_SYSTEM_PROMPT.replace("{language}", settings.output_language)
            suggestions = []
            for i, recipe in enumerate(under_tagged, 1):
                if job.cancel_requested:
                    break
                job.progress_current = i
                job.progress_label = f"Checking recipe {i}/{len(under_tagged)}..."
                tool_jobs.save_tool_job(job)
                try:
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
                    job.token_usage.input_tokens += getattr(usage, "input_tokens", 0) or 0
                    job.token_usage.output_tokens += getattr(usage, "output_tokens", 0) or 0
                    text_out = text_out.strip().strip("`")
                    if text_out.startswith("json"):
                        text_out = text_out[4:]
                    tags = json.loads(text_out)
                    if not tags:
                        continue
                    suggestions.append(ToolSuggestion(
                        id=uuid.uuid4().hex[:10], kind="suggest_tags",
                        summary=f"add {tags} to {recipe.get('name', '')!r}",
                        detail={"recipe_id": recipe["id"], "tags": tags},
                    ))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Suggest-more failed for recipe %s: %s", recipe.get("id"), exc)

            job.suggestions = suggestions
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
    the single entry point main.py calls, regardless of which of the four tag
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
