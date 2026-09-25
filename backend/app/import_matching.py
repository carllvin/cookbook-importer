"""Matches the ingredients (and units) of freshly extracted recipes against
what already exists in Tandoor, BEFORE import - so importing a cookbook
reuses "Zwiebel" instead of creating a new "Zwiebeln" next to it.

Same approach as the new-recipes workflow, minus the normalize step (the
extraction already wrote names in OUTPUT_LANGUAGE):
1. exact match (case-insensitive) against existing names and plurals -
   decided in code, the ingredient gets the existing entry's exact name;
2. only names with a merely similar existing name go to the AI, with at
   most a few candidates each (one small call for the whole cookbook);
3. everything else is marked as new.
Each ingredient records the outcome in `tandoor_match` ("exists" |
"matched" | "new") and, when its name was changed, `original_name`, so the
review screen can show it and undo it."""
from __future__ import annotations

import logging

from . import tandoor_client, tools_ingredients, tools_tags
from .tools_new_recipes import PICK_SYSTEM_PROMPT, _key, _prompt, _similar_candidates

log = logging.getLogger("tandoor-helper")

PICK_BATCH_SIZE = 60  # names per AI call when a big cookbook has many "similar" ones


def _index(items):
    index = {}
    for item in items:
        for name in (item.get("name"), item.get("plural_name")):
            if name:
                index.setdefault(_key(name), item)
    return index


def match_job_ingredients(job) -> None:
    """Updates job.recipes' ingredients in place. Non-fatal: if Tandoor
    isn't reachable, the review screen simply shows no match badges."""
    try:
        with tandoor_client.get_client() as client:
            foods = tools_ingredients.fetch_all_foods_full(client)
            units = tandoor_client.fetch_all_items(client, "unit")
    except Exception as exc:  # noqa: BLE001
        log.info("Ingredient matching skipped (Tandoor unreachable/not configured): %s", exc)
        return

    food_index, unit_index = _index(foods), _index(units)
    all_ingredients = [ing for recipe in job.recipes for ing in recipe.ingredients]

    # Units: exact (case-insensitive) matches only - adopt Tandoor's spelling.
    for ing in all_ingredients:
        if ing.unit and _key(ing.unit) in unit_index:
            ing.unit = unit_index[_key(ing.unit)]["name"]

    # Foods: decide once per distinct name.
    decisions: dict[str, tuple[str, str | None]] = {}  # key -> (match, existing name)
    undecided: dict[str, tuple[str, list[dict]]] = {}  # key -> (name, similar existing items)
    for ing in all_ingredients:
        key = _key(ing.name)
        if not key or key in decisions or key in undecided:
            continue
        if key in food_index:
            decisions[key] = ("exists", food_index[key]["name"])
            continue
        similar = _similar_candidates([ing.name], food_index, own_id=None)
        if similar:
            undecided[key] = (ing.name, similar)
        else:
            decisions[key] = ("new", None)

    entries = list(undecided.items())
    picked = {}
    for start in range(0, len(entries), PICK_BATCH_SIZE):
        batch = list(enumerate(entries))[start:start + PICK_BATCH_SIZE]
        try:
            picks = tools_tags._complete_json(
                job, _prompt(PICK_SYSTEM_PROMPT, "food"),
                [{"id": i, "name": name, "candidates": [s["name"] for s in similar]}
                 for i, (_key_, (name, similar)) in batch],
                max_tokens=40 * len(batch) + 100,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Ingredient matching AI call failed, treating those as new: %s", exc)
            picks = []
        picked.update({p.get("id"): p.get("match_name") for p in picks if isinstance(p, dict)})
    if entries:
        for i, (key, (name, similar)) in enumerate(entries):
            choice = picked.get(i)
            match = next((s for s in similar if isinstance(choice, str) and _key(s["name"]) == _key(choice)), None)
            decisions[key] = ("matched", match["name"]) if match else ("new", None)

    counts = {"exists": 0, "matched": 0, "new": 0}
    for ing in all_ingredients:
        decision = decisions.get(_key(ing.name))
        if decision is None:
            continue
        match, existing_name = decision
        if existing_name and existing_name != ing.name:
            ing.original_name = ing.name
            ing.name = existing_name
            # Same entry, only spelled/pluralized differently -> still a match
            # the user may want to see (and undo).
            match = "matched"
        ing.tandoor_match = match
        counts[match] += 1
    log.info("Ingredient matching for job %s: %s", job.id, counts)
