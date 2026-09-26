"""Collection health overview for the maintenance page: what each tool would
find - without any AI call. Computing it reads every recipe in full (one
request per recipe), so it runs in the background on request and the last
result (the affected entries per metric) is cached in the data volume.
Entries the user ignored (see ignored.py) are left out of the counts when
reading the cache, so ignoring takes effect without recomputing."""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import ignored, recipe_restructure, tandoor_client, tools_conversions, tools_ingredients, tools_recipes, tools_tags
from .config import get_language_code, settings
from .tandoor_helpers import fetch_all_recipes_full

log = logging.getLogger("tandoor-helper")

_state = {"running": False, "error": None}
_lock = threading.Lock()


def _path() -> str:
    return os.path.join(settings.data_dir, "health.json")


def _read() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"computed_at": None, "metrics": {}}


def cached() -> dict:
    data = _read()
    metrics = dict(data.get("metrics", {}))
    ignored_all = ignored.load()
    ignored_counts = {m: len(ignored_all.get(m, {})) for m in ignored.METRICS}
    for metric, items in (data.get("items") or {}).items():
        skip = set(ignored_all.get(metric, {}))
        metrics[metric] = sum(1 for item in items if item["key"] not in skip)
    return {"computed_at": data.get("computed_at"), "metrics": metrics, "ignored": ignored_counts,
            "running": _state["running"], "error": _state["error"]}


def items(metric: str) -> dict:
    """The affected entries of one metric (without the ignored ones) and the
    ignored ones, as [{"key", "name", "recipe_id"?}]."""
    if metric not in ignored.METRICS:
        raise ValueError(f"Unknown metric {metric!r}")
    skip = ignored.load().get(metric, {})
    listed = [item for item in (_read().get("items") or {}).get(metric, []) if item["key"] not in skip]
    ignored_items = [{"key": key, "name": name} for key, name in skip.items()]
    if metric.startswith("recipes_"):
        for item in ignored_items:
            item["recipe_id"] = int(item["key"]) if item["key"].isdigit() else None
    return {"items": listed, "ignored": sorted(ignored_items, key=lambda i: i["name"].casefold())}


def start_refresh() -> bool:
    with _lock:
        if _state["running"]:
            return False
        _state["running"], _state["error"] = True, None
    threading.Thread(target=_compute, daemon=True).start()
    return True


def _compute() -> None:
    try:
        with tandoor_client.get_client() as client:
            recipes = fetch_all_recipes_full(client)
            foods = {f["id"]: f for f in tools_ingredients.fetch_all_foods_full(client)}
            categories = tools_ingredients.fetch_supermarket_categories(client)
            food_names = tools_tags.food_name_set(client)
            used_foods = {
                (ing.get("food") or {}).get("id")
                for r in recipes for step in r.get("steps", []) for ing in step.get("ingredients", [])
            } - {None}
            general, to_estimate = tools_conversions.find_missing(
                client, tools_conversions.recipe_pairs(recipes), respect_ignored=False)

        expected = get_language_code(settings.output_language)

        def food_items(missing):
            return sorted(({"key": str(fid), "name": foods[fid]["name"]} for fid in used_foods
                           if fid in foods and missing(foods[fid])), key=lambda i: i["name"].casefold())

        def recipe_items(matches):
            return [{"key": str(r["id"]), "name": r.get("name", ""), "recipe_id": r["id"]}
                    for r in sorted(recipes, key=lambda r: (r.get("name") or "").casefold()) if matches(r)]

        conversion_items = [
            {"key": f"*:{sug.detail['base_unit']['id']}",
             "name": f"{sug.detail['base_unit']['name']} → {sug.detail['converted_unit']['name']}"}
            for sug in general
        ] + [
            {"key": f"{p['food']['id']}:{p['unit']['id']}",
             "name": f"{p['food']['name']}: {p['unit']['name']} → {p['target']['name']}"}
            for p in to_estimate
        ]
        item_lists = {
            "foods_without_nutrition": food_items(lambda f: not f.get("properties")),
            "foods_without_category": food_items(lambda f: not f.get("supermarket_category")) if categories else [],
            "missing_conversions": conversion_items,
            "recipes_not_translated": recipe_items(lambda r: not tools_recipes.already_in_target_language(r, expected)),
            "recipes_need_restructure": recipe_items(lambda r: bool(recipe_restructure.needs_restructure(r))),
            "recipes_without_season": recipe_items(lambda r: not tools_tags.has_season_tag(r)),
            "recipes_few_tags": recipe_items(
                lambda r: sum(1 for kw in r.get("keywords", []) if kw["name"].strip().casefold() not in food_names)
                < tools_tags.MIN_TAGS_DEFAULT
            ),
        }
        metrics = {"recipes_total": len(recipes), "foods_used": len(used_foods)}
        os.makedirs(settings.data_dir, exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"computed_at": time.time(), "metrics": metrics, "items": item_lists}, f, ensure_ascii=False)
        os.replace(tmp, _path())
    except Exception as exc:  # noqa: BLE001
        log.exception("Health overview failed")
        _state["error"] = str(exc)
    finally:
        _state["running"] = False
