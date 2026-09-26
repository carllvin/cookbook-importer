"""Finds likely duplicate ingredients and units by their names - locally, no
AI. Used by the collection health overview (counting) and by the review
tools' "duplicates only" mode (which then lets the AI decide on just these).

Deliberately strict, so the count means something: same name apart from
spelling details, one is the other's plural, a small typo in a longer name
("Zuchini" / "Zucchini"), or - for units - known abbreviations of the same
unit ("EL" / "Esslöffel" / "tbsp"). Pairs that are rightly separate can be
ignored in the overview (key "<smaller id>:<larger id>")."""
from __future__ import annotations

import difflib
import re

from . import ignored

FOOD_MIN_LEN = 5
FOOD_MIN_RATIO = 0.9

UNIT_ALIASES = [
    {"el", "essl", "esslöffel", "eßlöffel", "tbsp", "tablespoon", "tablespoons"},
    {"tl", "teel", "teelöffel", "tsp", "teaspoon", "teaspoons"},
    {"g", "gr", "gramm", "gram", "grams"},
    {"kg", "kilo", "kilogramm", "kilogram"},
    {"ml", "milliliter", "millilitre"},
    {"l", "liter", "litre", "ltr"},
    {"stück", "stk", "st", "stck", "piece", "pieces", "pc", "pcs"},
    {"prise", "prisen", "pr", "pinch"},
    {"dose", "dosen", "can", "cans"},
    {"bund", "bd", "bunch"},
    {"zehe", "zehen", "clove", "cloves"},
    {"päckchen", "pck", "pk", "pkg", "packung", "packungen"},
    {"tasse", "tassen", "cup", "cups"},
    {"scheibe", "scheiben", "slice", "slices"},
]
_UNIT_CANON = {alias: i for i, group in enumerate(UNIT_ALIASES) for alias in group}


def _key(name) -> str:
    return re.sub(r"[\s.\-_/]+", "", (name or "").casefold())


def pair_key(a_id, b_id) -> str:
    a, b = sorted((a_id, b_id))
    return f"{a}:{b}"


def _pair(a, b) -> dict:
    first, second = sorted((a, b), key=lambda item: item["id"])
    return {"key": pair_key(a["id"], b["id"]), "name": f"{first['name']} ↔ {second['name']}",
            "ids": [first["id"], second["id"]]}


def _plural_match(a, b) -> bool:
    return (bool(_key(a.get("plural_name"))) and _key(a.get("plural_name")) == _key(b["name"])) or \
           (bool(_key(b.get("plural_name"))) and _key(b.get("plural_name")) == _key(a["name"]))


def food_duplicates(foods) -> list[dict]:
    """[{"key", "name", "ids"}] for foods (dicts with id, name, plural_name)."""
    foods = [f for f in foods if _key(f.get("name"))]
    pairs, seen = [], set()

    def add(a, b):
        key = pair_key(a["id"], b["id"])
        if a["id"] != b["id"] and key not in seen:
            seen.add(key)
            pairs.append(_pair(a, b))

    by_key, by_plural = {}, {}
    for food in foods:
        by_key.setdefault(_key(food["name"]), []).append(food)
        if _key(food.get("plural_name")):
            by_plural.setdefault(_key(food["plural_name"]), []).append(food)
    for key, group in by_key.items():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                add(a, b)  # same name apart from case/spacing/punctuation
        for a in group:
            for b in by_plural.get(key, []):
                add(a, b)  # a is b's plural

    # Small typos in longer names - compared only within the same first
    # letter and a similar length, which keeps this fast for big lists.
    buckets = {}
    for food in foods:
        key = _key(food["name"])
        if len(key) >= FOOD_MIN_LEN:
            buckets.setdefault(key[0], []).append((len(key), key, food))
    for bucket in buckets.values():
        bucket.sort(key=lambda entry: entry[0])
        for i, (len_a, key_a, a) in enumerate(bucket):
            matcher = difflib.SequenceMatcher(None, key_a)
            for len_b, key_b, b in bucket[i + 1:]:
                if 2 * len_a / (len_a + len_b) < FOOD_MIN_RATIO:
                    break  # all further ones are even longer
                matcher.set_seq2(key_b)
                if matcher.quick_ratio() >= FOOD_MIN_RATIO and matcher.ratio() >= FOOD_MIN_RATIO:
                    add(a, b)
    return sorted(pairs, key=lambda p: p["name"].casefold())


def unit_duplicates(units) -> list[dict]:
    """[{"key", "name", "ids"}] for units (dicts with id, name, plural_name)."""
    units = [u for u in units if _key(u.get("name"))]
    pairs = []
    for i, a in enumerate(units):
        for b in units[i + 1:]:
            ka, kb = _key(a["name"]), _key(b["name"])
            same_alias = ka in _UNIT_CANON and _UNIT_CANON.get(ka) == _UNIT_CANON.get(kb)
            if ka == kb or same_alias or _plural_match(a, b):
                pairs.append(_pair(a, b))
    return sorted(pairs, key=lambda p: p["name"].casefold())


def open_duplicate_ids(pairs, metric) -> list[list[int]]:
    """Groups of ids connected by not-ignored pairs, e.g. [[3, 7, 12], ...] -
    for the review tools' duplicates-only mode."""
    skip = ignored.keys(metric)
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for pair in pairs:
        if pair["key"] not in skip:
            a, b = pair["ids"]
            parent[find(a)] = find(b)
    groups = {}
    for x in parent:
        groups.setdefault(find(x), []).append(x)
    return [sorted(g) for g in groups.values()]


def pack_groups(groups, size) -> list[list[int]]:
    """Chunks of ids that keep each group together (the AI only sees one
    chunk at a time)."""
    chunks, current = [], []
    for group in groups:
        if current and len(current) + len(group) > size:
            chunks.append(current)
            current = []
        current = current + group
    if current:
        chunks.append(current)
    return chunks


def drop_ignored_merges(actions, metric) -> list[dict]:
    """Removes merges of pairs the user marked as rightly separate."""
    skip = ignored.keys(metric)
    if not skip:
        return actions
    result = []
    for action in actions:
        if action.get("type") == "merge":
            remove_ids = [r for r in action["remove_ids"] if pair_key(action["keep_id"], r) not in skip]
            if not remove_ids:
                continue
            action = {**action, "remove_ids": remove_ids}
        result.append(action)
    return result
