"""Collection health overview for the maintenance page: how many things each
tool would find - without any AI call. Computing it reads every recipe in
full (one request per recipe), so it runs in the background on request and
the last result is cached in the data volume."""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from . import recipe_restructure, tandoor_client, tools_conversions, tools_ingredients, tools_recipes, tools_tags
from .config import get_language_code, settings
from .tandoor_helpers import fetch_all_recipes_full

log = logging.getLogger("tandoor-helper")

_state = {"running": False, "error": None}
_lock = threading.Lock()


def _path() -> str:
    return os.path.join(settings.data_dir, "health.json")


def cached() -> dict:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"computed_at": None, "metrics": {}}
    return {**data, "running": _state["running"], "error": _state["error"]}


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
            general, to_estimate = tools_conversions.find_missing(client, tools_conversions.recipe_pairs(recipes))

        expected = get_language_code(settings.output_language)
        metrics = {
            "foods_without_nutrition": sum(1 for fid in used_foods if fid in foods and not foods[fid].get("properties")),
            "foods_without_category": (sum(1 for fid in used_foods if fid in foods and not foods[fid].get("supermarket_category"))
                                       if categories else 0),
            "missing_conversions": len(general) + len(to_estimate),
            "recipes_not_translated": sum(1 for r in recipes if not tools_recipes.already_in_target_language(r, expected)),
            "recipes_need_restructure": sum(1 for r in recipes if recipe_restructure.needs_restructure(r)),
            "recipes_without_season": sum(1 for r in recipes if not tools_tags.has_season_tag(r)),
            "recipes_few_tags": sum(
                1 for r in recipes
                if sum(1 for kw in r.get("keywords", []) if kw["name"].strip().casefold() not in food_names)
                < tools_tags.MIN_TAGS_DEFAULT
            ),
            "recipes_total": len(recipes),
            "foods_used": len(used_foods),
        }
        os.makedirs(settings.data_dir, exist_ok=True)
        tmp = _path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"computed_at": time.time(), "metrics": metrics}, f)
        os.replace(tmp, _path())
    except Exception as exc:  # noqa: BLE001
        log.exception("Health overview failed")
        _state["error"] = str(exc)
    finally:
        _state["running"] = False
