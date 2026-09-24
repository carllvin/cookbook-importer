from __future__ import annotations

import json
import logging
import uuid

from . import llm_provider, tandoor_client, tool_jobs
from .config import settings
from .schemas import ToolSuggestion
from .tandoor_helpers import chunked, delete_entity, entity_exists, find_recipes_by_filter, format_cost_estimate, minimal_ref, resolve_name_collisions, validate_actions

log = logging.getLogger("tandoor-helper")

CHUNK_SIZE = 80

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

Every id may appear in AT MOST ONE element of your answer - put all ids
of one ingredient into a single merge instead of listing a rename and a merge
for the same entry.

Only include entries that actually need a change - do not list names that
are already a clean, plain, singular {language} noun. Respond with ONLY a
JSON array (no explanation, no markdown fence), each element one of:

{{"type": "rename", "id": <id>, "new_name": <corrected name>}}
{{"type": "merge", "keep_id": <id to keep>, "keep_name": <possibly corrected/translated name for it>, "remove_ids": [<other ids that mean the same thing>]}}

If nothing needs a change, respond with [].
"""


def _review_chunk(foods, language):
    system_prompt = REVIEW_SYSTEM_PROMPT.replace("{language}", language)
    text_out, usage = llm_provider.complete_text(
        system_prompt,
        json.dumps([{"id": f["id"], "name": f["name"]} for f in foods], ensure_ascii=False),
        max_tokens=8000,
    )
    text_out = text_out.strip().strip("`")
    if text_out.startswith("json"):
        text_out = text_out[4:]
    return json.loads(text_out), usage


def _describe(action, by_id):
    if action["type"] == "rename":
        old = by_id.get(action["id"], {}).get("name", "?")
        return f"rename {old!r} -> {action['new_name']!r}"
    keep_old = by_id.get(action["keep_id"], {}).get("name", "?")
    remove_names = ", ".join(f"{by_id.get(rid, {}).get('name', '?')!r}" for rid in action["remove_ids"])
    return f"merge {remove_names} into {keep_old!r} -> {action['keep_name']!r}"


def run_scan(job_id: str) -> None:
    """Runs in a background thread (see main.py). Scans all foods, reviews
    them in chunks via the AI, and populates the job with one ToolSuggestion
    per proposed change - same logic as manage_ingredients.py's `review`
    subcommand, adapted to populate a job for the UI to poll instead of
    printing to a terminal and asking for confirmation there."""
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
            foods = tandoor_client.fetch_all_items(client, "food")
            job.progress_total = len(foods)
            job.cost_estimate = format_cost_estimate(len(foods), "chunked_review")
            tool_jobs.save_tool_job(job)

            all_actions = []
            chunks = list(chunked(foods, CHUNK_SIZE))
            for i, chunk in enumerate(chunks, 1):
                if job.cancel_requested:
                    break
                job.progress_label = f"Reviewing chunk {i}/{len(chunks)}..."
                tool_jobs.save_tool_job(job)
                try:
                    actions, usage = _review_chunk(chunk, settings.output_language)
                    all_actions.extend(validate_actions(actions, "ingredients_review"))
                    job.token_usage.input_tokens += getattr(usage, "input_tokens", 0) or 0
                    job.token_usage.output_tokens += getattr(usage, "output_tokens", 0) or 0
                except Exception as exc:  # noqa: BLE001
                    log.warning("Ingredients review chunk %d failed: %s", i, exc)
                job.progress_current = i * CHUNK_SIZE
                tool_jobs.save_tool_job(job)

            # Whether the loop finished naturally or was cancelled partway
            # through, build suggestions from whatever was gathered - a
            # cancelled scan shouldn't throw away chunks that already cost
            # real AI calls and produced valid suggestions.
            all_actions = resolve_name_collisions(all_actions, foods)
            by_id = {f["id"]: f for f in foods}

            job.suggestions = [
                ToolSuggestion(id=uuid.uuid4().hex[:10], kind=action["type"], summary=_describe(action, by_id), detail=action)
                for action in all_actions
            ]
            job.status = "cancelled" if job.cancel_requested else "ready"
            job.progress_label = None
            tool_jobs.save_tool_job(job)

    except Exception as exc:  # noqa: BLE001
        log.exception("Ingredients review scan failed for job %s", job_id)
        job.status = "error"
        job.error = str(exc)
        tool_jobs.save_tool_job(job)


def _find_recipes_using_food(client, food_id):
    return find_recipes_by_filter(client, "foods", food_id)


def _repoint_recipe_food(recipe_detail, remove_id, keep_id, keep_name):
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


def apply_suggestion(job_id: str, suggestion_id: str) -> ToolSuggestion:
    """Applies exactly one suggestion (called by the web UI's per-item Apply
    button) and updates its status in place. The suggestion is left with
    status="error" and the message on failure; the job's other suggestions
    are unaffected."""
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
                resp = client.patch(f"/food/{action['id']}/", json={"name": action["new_name"]})
                if resp.status_code not in (200, 201):
                    raise tandoor_client.TandoorError(f"{resp.status_code} {resp.text[:300]}")
            else:  # merge
                keep_id, keep_name = action["keep_id"], action["keep_name"]
                if not entity_exists(client, "food", keep_id):
                    raise tandoor_client.TandoorError(
                        f"Food #{keep_id} no longer exists (already merged by another suggestion?) - rescan to continue."
                    )
                resp = client.patch(f"/food/{keep_id}/", json={"name": keep_name})
                if resp.status_code not in (200, 201):
                    raise tandoor_client.TandoorError(f"{resp.status_code} {resp.text[:300]}")

                for remove_id in action["remove_ids"]:
                    if not entity_exists(client, "food", remove_id):
                        continue  # already merged away by an earlier suggestion
                    for recipe in _find_recipes_using_food(client, remove_id):
                        resp = client.get(f"/recipe/{recipe['id']}/")
                        resp.raise_for_status()
                        payload = _repoint_recipe_food(resp.json(), remove_id, keep_id, keep_name)
                        resp = client.patch(f"/recipe/{recipe['id']}/", json=payload)
                        if resp.status_code not in (200, 201):
                            raise tandoor_client.TandoorError(
                                f"Could not update recipe {recipe['id']}: {resp.status_code} {resp.text[:300]}"
                            )

                    still_used = _find_recipes_using_food(client, remove_id)
                    if still_used:
                        raise tandoor_client.TandoorError(
                            f"Still used by {len(still_used)} recipe(s) after repointing - not deleting #{remove_id}."
                        )
                    delete_entity(client, "food", remove_id)

        suggestion.status = "applied"
    except Exception as exc:  # noqa: BLE001
        suggestion.status = "error"
        suggestion.error = str(exc)
    finally:
        tool_jobs.save_tool_job(job)

    return suggestion
