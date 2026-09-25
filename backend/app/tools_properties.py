"""Clean-up tool for nutrition property types: finds duplicates of the same
nutrient in different languages (e.g. "Energy" next to "Kalorien",
"Protein" next to "Proteine") and merges them into the one in
OUTPUT_LANGUAGE - moving every ingredient's value over, then deleting the
duplicate type. A nutrient that only exists in another language is renamed
instead. No AI involved - names are recognized from a fixed synonym list."""
from __future__ import annotations

import logging
import uuid

from . import nutrition_properties as np_, tandoor_client, tool_jobs, tools_ingredients
from .schemas import ToolSuggestion

log = logging.getLogger("tandoor-helper")


def _foods_with_properties(client) -> list[dict]:
    """All foods incl. their properties. Uses the list view when it carries
    them, otherwise falls back to one GET per food."""
    foods = tools_ingredients.fetch_all_foods_full(client)
    if all("properties" in f for f in foods):
        return foods
    detailed = []
    for food in foods:
        resp = client.get(f"/food/{food['id']}/")
        if resp.status_code == 200:
            detailed.append(resp.json())
    return detailed


def _type_id(prop):
    return (prop.get("property_type") or {}).get("id")


def run_scan(job_id: str) -> None:
    job = tool_jobs.get_tool_job(job_id)
    if job is None:
        return
    try:
        with tandoor_client.get_client() as client:
            job.progress_label = "Loading property types and ingredients..."
            tool_jobs.save_tool_job(job)
            _endpoint, types = np_.fetch_property_types(client)
            foods = _foods_with_properties(client)
            usage = {}
            for food in foods:
                for prop in food.get("properties") or []:
                    usage[_type_id(prop)] = usage.get(_type_id(prop), 0) + 1

            groups: dict[str, list[dict]] = {}
            for pt in types:
                key = np_.nutrient_of(pt.get("name"))
                if key:
                    groups.setdefault(key, []).append(pt)

            suggestions = []
            for key, group in groups.items():
                group.sort(key=lambda pt: (np_.rank(key, pt["name"]), pt["id"]))
                keep = group[0]
                if len(group) == 1:
                    wanted = np_.target_name(key)
                    if np_.rank(key, keep["name"])[0] != 0 and not any(
                        np_._norm(t["name"]) == np_._norm(wanted) for t in types
                    ):
                        suggestions.append(ToolSuggestion(
                            id=uuid.uuid4().hex[:10], kind="rename_property",
                            summary=f"property: rename {keep['name']!r} -> {wanted!r}",
                            detail={"type": "rename", "id": keep["id"], "new_name": wanted},
                        ))
                    continue
                for dup in group[1:]:
                    suggestions.append(ToolSuggestion(
                        id=uuid.uuid4().hex[:10], kind="merge_property",
                        summary=(f"property: merge {dup['name']!r} ({dup.get('unit') or '-'}) into "
                                 f"{keep['name']!r} ({keep.get('unit') or '-'}) - used by {usage.get(dup['id'], 0)} "
                                 f"ingredient(s), then delete {dup['name']!r}"),
                        detail={"type": "merge", "key": key,
                                "keep": {"id": keep["id"], "name": keep["name"], "unit": keep.get("unit")},
                                "remove": {"id": dup["id"], "name": dup["name"], "unit": dup.get("unit")}},
                    ))
            job.progress_total = len(types)
            job.suggestions = suggestions
            job.status = "ready"
            job.progress_label = None
            tool_jobs.save_tool_job(job)
    except Exception as exc:  # noqa: BLE001
        log.exception("Property clean-up scan failed for job %s", job_id)
        job.status = "error"
        job.error = str(exc)
        tool_jobs.save_tool_job(job)


def _merge_food_properties(food, detail):
    """New `properties` list for one food: the duplicate's value moves to
    the kept type - unless the food already has a value there, which wins."""
    keep, remove = detail["keep"], detail["remove"]
    props = food.get("properties") or []
    dup = next((p for p in props if _type_id(p) == remove["id"]), None)
    target = next((p for p in props if _type_id(p) == keep["id"]), None)
    moved = np_.convert_between(dup.get("property_amount") if dup else None, detail["key"],
                                remove.get("unit"), keep.get("unit"))

    new_props = []
    for p in props:
        if _type_id(p) == remove["id"]:
            continue
        entry = {"id": p["id"], "property_type": {"id": _type_id(p), "name": p["property_type"].get("name")},
                 "property_amount": p.get("property_amount")}
        if p is target and entry["property_amount"] is None and moved is not None:
            entry["property_amount"] = moved
        new_props.append(entry)
    if target is None and moved is not None:
        new_props.append({"property_type": {"id": keep["id"], "name": keep["name"]}, "property_amount": moved})
    return new_props


def apply_suggestion(job_id: str, suggestion_id: str) -> ToolSuggestion:
    job = tool_jobs.get_tool_job(job_id)
    if job is None:
        raise tandoor_client.TandoorError("Job not found.")
    suggestion = next((s for s in job.suggestions if s.id == suggestion_id), None)
    if suggestion is None:
        raise tandoor_client.TandoorError("Suggestion not found.")
    if suggestion.status != "pending":
        return suggestion

    detail = suggestion.detail
    try:
        with tandoor_client.get_client() as client:
            endpoint, _types = np_.fetch_property_types(client)
            if detail["type"] == "rename":
                resp = client.patch(f"/{endpoint}/{detail['id']}/", json={"name": detail["new_name"]})
                if resp.status_code not in (200, 201):
                    raise tandoor_client.TandoorError(f"{resp.status_code} {resp.text[:300]}")
            else:
                remove_id = detail["remove"]["id"]
                users = [f for f in _foods_with_properties(client)
                         if any(_type_id(p) == remove_id for p in f.get("properties") or [])]
                for food in users:
                    resp = client.get(f"/food/{food['id']}/")
                    resp.raise_for_status()
                    fresh = resp.json()
                    resp = client.patch(f"/food/{food['id']}/",
                                        json={"name": fresh["name"], "properties": _merge_food_properties(fresh, detail)})
                    if resp.status_code not in (200, 201):
                        raise tandoor_client.TandoorError(
                            f"Could not update ingredient {fresh['name']!r}: {resp.status_code} {resp.text[:300]}"
                        )
                    # Verify before anything gets deleted: deleting the type
                    # would take any value still attached to it along.
                    check = client.get(f"/food/{food['id']}/").json()
                    if any(_type_id(p) == remove_id for p in check.get("properties") or []):
                        raise tandoor_client.TandoorError(
                            f"{fresh['name']!r} still has {detail['remove']['name']!r} after the update - "
                            f"not deleting the property type."
                        )
                resp = client.delete(f"/{endpoint}/{remove_id}/")
                if resp.status_code not in (200, 202, 204, 404):
                    raise tandoor_client.TandoorError(
                        f"Values moved, but could not delete {detail['remove']['name']!r}: {resp.status_code} {resp.text[:200]}"
                    )
        suggestion.status = "applied"
    except Exception as exc:  # noqa: BLE001
        suggestion.status = "error"
        suggestion.error = str(exc)
    finally:
        tool_jobs.save_tool_job(job)
    return suggestion
