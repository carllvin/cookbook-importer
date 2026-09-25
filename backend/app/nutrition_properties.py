"""Maps the four nutrients the tools estimate (energy, protein, fat, carbs) to
the property types that ALREADY exist in Tandoor - "Kalorien", "Proteine",
"Fett", "Kohlenhydrate" or whatever they're called there - instead of
creating new ones. Also used by the property clean-up tool to find
duplicates (e.g. "Energy" next to "Kalorien") and merge them."""
from __future__ import annotations

import re

from . import tandoor_client
from .config import get_language_code, settings

PROPERTY_TYPE_ENDPOINT_CANDIDATES = ["property-type", "food-property-type", "propertytype"]

# nutrient key -> {language code: known names, most common first}
NUTRIENT_NAMES = {
    "energy_kcal": {
        "de": ["Kalorien", "Energie", "Brennwert", "Kilokalorien", "kcal"],
        "en": ["Energy", "Calories", "Kcal"],
        "fr": ["Calories", "Énergie", "Energie"],
        "it": ["Calorie", "Energia"],
        "es": ["Calorías", "Energía"],
    },
    "protein_g": {
        "de": ["Proteine", "Protein", "Eiweiß", "Eiweiss"],
        "en": ["Protein", "Proteins"],
        "fr": ["Protéines"],
        "it": ["Proteine"],
        "es": ["Proteínas"],
    },
    "fat_g": {
        "de": ["Fett", "Fette", "Fettgehalt"],
        "en": ["Fat", "Fats", "Total Fat"],
        "fr": ["Lipides", "Matières grasses"],
        "it": ["Grassi"],
        "es": ["Grasas"],
    },
    "carbs_g": {
        "de": ["Kohlenhydrate", "KH"],
        "en": ["Carbohydrates", "Carbs", "Carbohydrate"],
        "fr": ["Glucides"],
        "it": ["Carboidrati"],
        "es": ["Carbohidratos", "Hidratos de carbono"],
    },
}
NUTRIENT_UNITS = {"energy_kcal": "kcal", "protein_g": "g", "fat_g": "g", "carbs_g": "g"}


def _norm(name) -> str:
    """Lowercase, without a trailing "(kcal)"-style unit note."""
    return " ".join(re.sub(r"\(.*?\)", " ", name or "").lower().split())


def nutrient_of(name) -> str | None:
    """Which of the four nutrients a property type name refers to, if any."""
    norm = _norm(name)
    for key, by_lang in NUTRIENT_NAMES.items():
        if any(_norm(n) == norm for names in by_lang.values() for n in names):
            return key
    return None


def _lang() -> str:
    return get_language_code(settings.output_language) or "en"


def target_name(key) -> str:
    """The standard name for a nutrient in OUTPUT_LANGUAGE."""
    by_lang = NUTRIENT_NAMES[key]
    return (by_lang.get(_lang()) or by_lang["en"])[0]


def rank(key, name) -> tuple:
    """Sort key: names in OUTPUT_LANGUAGE first, in list order ("Proteine"
    before "Protein" for German), then everything else."""
    own = [_norm(n) for n in NUTRIENT_NAMES[key].get(_lang(), [])]
    norm = _norm(name)
    return (0, own.index(norm)) if norm in own else (1, 0)


def fetch_property_types(client) -> tuple[str, list[dict]]:
    """(endpoint, all property types) - tries the endpoint names used by
    different Tandoor versions."""
    last_error = None
    for endpoint in PROPERTY_TYPE_ENDPOINT_CANDIDATES:
        types, url, params = [], f"/{endpoint}/", {"page_size": 200}
        resp = client.get(url, params=params)
        if resp.status_code == 404:
            continue
        if resp.status_code != 200:
            last_error = f"{resp.status_code} {resp.text[:200]}"
            continue
        for _ in range(50):
            data = resp.json()
            types.extend(data.get("results", data) if isinstance(data, dict) else data)
            url = data.get("next") if isinstance(data, dict) else None
            if not url:
                break
            resp = client.get(url)
            resp.raise_for_status()
        return endpoint, types
    raise tandoor_client.TandoorError(
        f"No property-type endpoint found (tried {PROPERTY_TYPE_ENDPOINT_CANDIDATES})"
        + (f": {last_error}" if last_error else "")
    )


def existing_nutrient_types(client) -> dict[str, dict]:
    """nutrient key -> the existing property type to use for it. Nutrients
    without a matching property type are left out - they're never created."""
    _endpoint, types = fetch_property_types(client)
    chosen = {}
    for pt in types:
        key = nutrient_of(pt.get("name"))
        if key is None:
            continue
        current = chosen.get(key)
        if current is None or (rank(key, pt["name"]), pt["id"]) < (rank(key, current["name"]), current["id"]):
            chosen[key] = pt
    return chosen


def convert(amount, key, to_unit) -> float:
    """Converts a value given in NUTRIENT_UNITS[key] into the property type's
    own unit where that's a known conversion (kcal->kJ, g->mg); otherwise
    the value is used as-is."""
    unit = (to_unit or "").strip().lower()
    if key == "energy_kcal" and unit == "kj":
        return round(amount * 4.184, 1)
    if NUTRIENT_UNITS[key] == "g" and unit == "mg":
        return round(amount * 1000, 1)
    return amount


def convert_between(amount, key, from_unit, to_unit):
    """For merging two property types of the same nutrient with different
    units (e.g. kJ -> kcal)."""
    if amount is None:
        return None
    f, t = (from_unit or "").strip().lower(), (to_unit or "").strip().lower()
    if f == t:
        return amount
    factors = {("kj", "kcal"): 1 / 4.184, ("kcal", "kj"): 4.184, ("mg", "g"): 0.001, ("g", "mg"): 1000}
    factor = factors.get((f, t))
    return round(amount * factor, 2) if factor else amount
