from __future__ import annotations

import os

import httpx

from .config import settings
from .schemas import ExtractedRecipe


class TandoorError(RuntimeError):
    pass


def get_client() -> httpx.Client:
    if not settings.tandoor_url or not settings.tandoor_token:
        raise TandoorError(
            "TANDOOR_URL / TANDOOR_TOKEN sind nicht konfiguriert (siehe .env)."
        )
    base_url = settings.tandoor_url.rstrip("/")
    return httpx.Client(
        base_url=f"{base_url}/api",
        headers={
            "Authorization": f"Bearer {settings.tandoor_token}",
            "Accept": "application/json",
        },
        timeout=30.0,
    )


def _get_or_create(client: httpx.Client, endpoint: str, name: str) -> int:
    """
    Sucht ein Objekt (Food/Unit/Keyword) per Namen, legt es bei Bedarf an.
    Tandoor's Such-Endpunkte unterstuetzen ueblicherweise ?query=<name>.
    """
    name = name.strip()
    if not name:
        raise TandoorError("Leerer Name kann nicht angelegt werden.")

    try:
        resp = client.get(f"/{endpoint}/", params={"query": name, "page_size": 10})
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", data) if isinstance(data, dict) else data

        for item in results:
            if item.get("name", "").strip().lower() == name.lower():
                return item["id"]

        resp = client.post(f"/{endpoint}/", json={"name": name})
        if resp.status_code not in (200, 201):
            raise TandoorError(
                f"Konnte '{name}' nicht unter /{endpoint}/ anlegen: {resp.status_code} {resp.text[:300]}"
            )
        return resp.json()["id"]
    except httpx.HTTPError as exc:
        raise TandoorError(f"Netzwerkfehler bei /{endpoint}/ ({name}): {exc}") from exc


# ---------- Kochbuch (Cookbook) ----------
# Tandoor nennt das Feature in der UI "Cookbook", intern/historisch lautet der
# API-Endpunkt meist "recipe-book" (mit "recipe-book-entry" fuer die Zuordnung
# Rezept<->Kochbuch). Manche Versionen nutzen stattdessen "cookbook". Wir
# probieren beide, damit es unabhaengig von der Tandoor-Version funktioniert.
_COOKBOOK_ENDPOINT_CANDIDATES = ["recipe-book", "cookbook"]
_COOKBOOK_ENTRY_ENDPOINT_CANDIDATES = ["recipe-book-entry", "cookbook-recipe", "cookbookrecipe"]


def get_or_create_cookbook(client: httpx.Client, name: str) -> tuple[int, str]:
    """Gibt (cookbook_id, funktionierender_endpoint_name) zurueck."""
    name = name.strip()
    if not name:
        raise TandoorError("Kein Kochbuch-Name angegeben.")

    last_error = None
    for endpoint in _COOKBOOK_ENDPOINT_CANDIDATES:
        try:
            resp = client.get(f"/{endpoint}/", params={"query": name, "page_size": 20})
            if resp.status_code == 404:
                last_error = f"/{endpoint}/ existiert nicht (404)"
                continue
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", data) if isinstance(data, dict) else data
            for item in results:
                if item.get("name", "").strip().lower() == name.lower():
                    return item["id"], endpoint

            resp = client.post(f"/{endpoint}/", json={"name": name, "description": ""})
            if resp.status_code in (200, 201):
                return resp.json()["id"], endpoint
            last_error = f"POST /{endpoint}/ -> {resp.status_code} {resp.text[:300]}"
        except httpx.HTTPStatusError as exc:
            last_error = str(exc)
            continue
        except httpx.HTTPError as exc:
            last_error = f"Netzwerkfehler bei /{endpoint}/: {exc}"
            continue

    raise TandoorError(
        f"Konnte Kochbuch '{name}' nicht anlegen/finden. Letzter Fehler: {last_error}"
    )


def add_recipe_to_cookbook(client: httpx.Client, cookbook_id: int, cookbook_endpoint: str, recipe_id: int) -> None:
    last_error = None
    for endpoint in _COOKBOOK_ENTRY_ENDPOINT_CANDIDATES:
        try:
            resp = client.post(f"/{endpoint}/", json={"book": cookbook_id, "recipe": recipe_id})
        except httpx.HTTPError as exc:
            last_error = f"Netzwerkfehler bei /{endpoint}/: {exc}"
            continue
        if resp.status_code in (200, 201):
            return
        if resp.status_code == 404:
            last_error = f"/{endpoint}/ existiert nicht (404)"
            continue
        last_error = f"POST /{endpoint}/ -> {resp.status_code} {resp.text[:300]}"

    raise TandoorError(
        f"Rezept wurde angelegt, konnte aber keinem Kochbuch zugeordnet werden. Letzter Fehler: {last_error}"
    )


# ---------- Rezept anlegen ----------

def _build_recipe_payload(recipe: ExtractedRecipe, client: httpx.Client) -> dict:
    keyword_refs = []
    for tag in recipe.tags:
        try:
            kid = _get_or_create(client, "keyword", tag)
            keyword_refs.append({"id": kid, "name": tag})
        except TandoorError:
            continue  # ein fehlerhafter Tag soll den Import nicht blockieren

    def build_ingredient(ing) -> dict:
        food_id = _get_or_create(client, "food", ing.name)
        unit_ref = None
        if ing.unit:
            unit_id = _get_or_create(client, "unit", ing.unit)
            unit_ref = {"id": unit_id, "name": ing.unit}
        return {
            "food": {"id": food_id, "name": ing.name},
            "unit": unit_ref,
            "amount": ing.amount if ing.amount is not None else 0,
            "note": ing.note or "",
            "order": 0,
            "no_amount": ing.amount is None,
        }

    steps_payload = []
    for order, step in enumerate(recipe.steps):
        ingredients_payload = []
        if order == 0:
            for ing in recipe.ingredients:
                ing_payload = build_ingredient(ing)
                ing_payload["order"] = len(ingredients_payload)
                ingredients_payload.append(ing_payload)

        steps_payload.append({
            "name": step.title or "",
            "instruction": step.instruction,
            "ingredients": ingredients_payload,
            "time": step.time_minutes or 0,
            "order": order,
            "show_as_header": False,
        })

    if not steps_payload and recipe.ingredients:
        ingredients_payload = []
        for ing in recipe.ingredients:
            ing_payload = build_ingredient(ing)
            ing_payload["order"] = len(ingredients_payload)
            ingredients_payload.append(ing_payload)
        steps_payload.append({
            "name": "",
            "instruction": recipe.description or "",
            "ingredients": ingredients_payload,
            "time": 0,
            "order": 0,
            "show_as_header": False,
        })

    return {
        "name": recipe.title[:128],
        "description": recipe.description or "",
        "servings": recipe.servings or 1,
        "working_time": recipe.prep_time_minutes or 0,
        "waiting_time": recipe.cook_time_minutes or 0,
        "keywords": keyword_refs,
        "steps": steps_payload,
        "internal": True,
    }


def create_recipe(client: httpx.Client, recipe: ExtractedRecipe) -> int:
    payload = _build_recipe_payload(recipe, client)
    try:
        resp = client.post("/recipe/", json=payload)
    except httpx.HTTPError as exc:
        raise TandoorError(f"Netzwerkfehler beim Anlegen des Rezepts: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise TandoorError(
            f"Tandoor lehnte das Rezept ab ({resp.status_code}): {resp.text[:500]}"
        )
    return resp.json()["id"]


def upload_image(client: httpx.Client, recipe_id: int, image_path: str) -> None:
    if not os.path.exists(image_path):
        return
    try:
        with open(image_path, "rb") as f:
            files = {"image": (os.path.basename(image_path), f, "application/octet-stream")}
            resp = client.put(f"/recipe/{recipe_id}/image/", files=files)
    except httpx.HTTPError as exc:
        raise TandoorError(f"Netzwerkfehler beim Bild-Upload: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise TandoorError(
            f"Bild-Upload fehlgeschlagen ({resp.status_code}): {resp.text[:300]}"
        )


def test_connection() -> dict:
    with get_client() as client:
        try:
            resp = client.get("/recipe/", params={"page_size": 1})
        except httpx.HTTPError as exc:
            raise TandoorError(f"Tandoor nicht erreichbar: {exc}") from exc
        if resp.status_code == 401:
            raise TandoorError("Tandoor hat den API-Token abgelehnt (401 Unauthorized).")
        resp.raise_for_status()
        return {"ok": True}


# ---------- Duplikat-Check ----------

def fetch_all_recipe_names(client: httpx.Client, max_pages: int = 30) -> list[str]:
    """Laedt (paginiert) die Namen aller bereits in Tandoor vorhandenen Rezepte,
    um vor dem Import lokal auf moegliche Duplikate abzugleichen. Eine begrenzte
    Anzahl Seiten schuetzt vor sehr grossen Instanzen (mehrere Tausend Rezepte)."""
    names: list[str] = []
    url = "/recipe/"
    params = {"page_size": 200}

    for _ in range(max_pages):
        try:
            resp = client.get(url, params=params if url == "/recipe/" else None)
        except httpx.HTTPError as exc:
            raise TandoorError(f"Netzwerkfehler beim Laden bestehender Rezepte: {exc}") from exc
        if resp.status_code != 200:
            raise TandoorError(
                f"Konnte bestehende Rezepte nicht laden ({resp.status_code}): {resp.text[:200]}"
            )
        data = resp.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        names.extend(item.get("name", "") for item in results if item.get("name"))

        next_url = data.get("next") if isinstance(data, dict) else None
        if not next_url:
            break
        # 'next' ist eine vollstaendige URL - relativ zur Client-base_url weiterverwenden
        url = next_url
        params = None

    return names
