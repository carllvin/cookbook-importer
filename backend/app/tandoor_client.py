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
            "TANDOOR_URL / TANDOOR_TOKEN are not configured (see .env)."
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
    Looks up an object (Food/Unit/Keyword) by name, creating it if needed.
    Tandoor's search endpoints usually support ?query=<name>.
    """
    name = name.strip()
    if not name:
        raise TandoorError("Cannot create an object with an empty name.")

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
                f"Could not create '{name}' under /{endpoint}/: {resp.status_code} {resp.text[:300]}"
            )
        return resp.json()["id"]
    except httpx.HTTPError as exc:
        raise TandoorError(f"Network error at /{endpoint}/ ({name}): {exc}") from exc


# ---------- Cookbook ----------
# Tandoor calls this feature "Cookbook" in the UI; internally/historically the API
# endpoint is usually "recipe-book" (with "recipe-book-entry" for the recipe<->book
# association). Some versions instead use "cookbook". We try both so this works
# regardless of the Tandoor version.
_COOKBOOK_ENDPOINT_CANDIDATES = ["recipe-book", "cookbook"]
_COOKBOOK_ENTRY_ENDPOINT_CANDIDATES = ["recipe-book-entry", "cookbook-recipe", "cookbookrecipe"]


def get_or_create_cookbook(client: httpx.Client, name: str) -> tuple[int, str]:
    """Returns (cookbook_id, working_endpoint_name)."""
    name = name.strip()
    if not name:
        raise TandoorError("No cookbook name given.")

    last_error = None
    for endpoint in _COOKBOOK_ENDPOINT_CANDIDATES:
        try:
            resp = client.get(f"/{endpoint}/", params={"query": name, "page_size": 20})
        except httpx.HTTPError as exc:
            last_error = f"Network error at /{endpoint}/: {exc}"
            continue

        if resp.status_code == 404:
            # This endpoint name doesn't exist in this Tandoor version -> try the next candidate
            last_error = f"/{endpoint}/ does not exist (404)"
            continue

        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # The endpoint exists (not a 404) but responded with a different error
            # (e.g. auth/redirect) - that's the actual, relevant error for this
            # Tandoor instance. Do NOT silently try the next candidate, or this
            # message would get overwritten by a misleading follow-up error.
            raise TandoorError(f"Error at /{endpoint}/: {exc}") from exc

        # Endpoint found and responding validly -> this is the right one; all
        # further steps (search/create) apply only to this endpoint.
        data = resp.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        for item in results:
            if item.get("name", "").strip().lower() == name.lower():
                return item["id"], endpoint

        try:
            # 'shared' is a required field in some Tandoor versions (list of users
            # the cookbook is shared with) - an empty list is correct here.
            resp = client.post(f"/{endpoint}/", json={"name": name, "description": "", "shared": []})
        except httpx.HTTPError as exc:
            raise TandoorError(f"Network error creating /{endpoint}/: {exc}") from exc

        if resp.status_code in (200, 201):
            return resp.json()["id"], endpoint

        raise TandoorError(
            f"Could not create cookbook '{name}' (POST /{endpoint}/ -> {resp.status_code}): {resp.text[:300]}"
        )

    raise TandoorError(
        f"Could not create/find cookbook '{name}'. Last error: {last_error}"
    )


def add_recipe_to_cookbook(client: httpx.Client, cookbook_id: int, cookbook_endpoint: str, recipe_id: int) -> None:
    last_error = None
    for endpoint in _COOKBOOK_ENTRY_ENDPOINT_CANDIDATES:
        try:
            resp = client.post(f"/{endpoint}/", json={"book": cookbook_id, "recipe": recipe_id})
        except httpx.HTTPError as exc:
            last_error = f"Network error at /{endpoint}/: {exc}"
            continue

        if resp.status_code in (200, 201):
            return
        if resp.status_code == 404:
            # This endpoint name doesn't exist -> try the next candidate
            last_error = f"/{endpoint}/ does not exist (404)"
            continue

        # The endpoint exists (not a 404), but the request was rejected - that's
        # the actual error for this instance. Don't keep trying other candidates,
        # or this message would get overwritten by a misleading follow-up error.
        raise TandoorError(
            f"Recipe was created but could not be added to a cookbook "
            f"(POST /{endpoint}/ -> {resp.status_code}): {resp.text[:300]}"
        )

    raise TandoorError(
        f"Recipe was created but could not be added to a cookbook. Last error: {last_error}"
    )


# ---------- Creating a recipe ----------

def _build_recipe_payload(recipe: ExtractedRecipe, client: httpx.Client) -> dict:
    keyword_refs = []
    for tag in recipe.tags:
        try:
            kid = _get_or_create(client, "keyword", tag)
            keyword_refs.append({"id": kid, "name": tag})
        except TandoorError:
            continue  # a single bad tag shouldn't block the whole import

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

    n_steps = len(recipe.steps)
    # Assign each ingredient to its step (step_index from the AI extraction, possibly
    # corrected by the user in the UI). Out of range or missing entirely -> step 0,
    # so no ingredient ever gets lost.
    ingredients_by_step: dict[int, list] = {i: [] for i in range(max(n_steps, 1))}
    for ing in recipe.ingredients:
        idx = ing.step_index if ing.step_index is not None else 0
        if idx < 0 or idx >= len(ingredients_by_step):
            idx = 0
        ingredients_by_step[idx].append(ing)

    steps_payload = []
    for order, step in enumerate(recipe.steps):
        ingredients_payload = []
        for ing in ingredients_by_step.get(order, []):
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
        raise TandoorError(f"Network error creating the recipe: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise TandoorError(
            f"Tandoor rejected the recipe ({resp.status_code}): {resp.text[:500]}"
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
        raise TandoorError(f"Network error uploading the image: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise TandoorError(
            f"Image upload failed ({resp.status_code}): {resp.text[:300]}"
        )


def test_connection() -> dict:
    with get_client() as client:
        try:
            resp = client.get("/recipe/", params={"page_size": 1})
        except httpx.HTTPError as exc:
            raise TandoorError(f"Tandoor unreachable: {exc}") from exc
        if resp.status_code == 401:
            raise TandoorError("Tandoor rejected the API token (401 Unauthorized).")
        resp.raise_for_status()
        return {"ok": True}


# ---------- Duplicate check ----------

def fetch_all_recipe_names(client: httpx.Client, max_pages: int = 30) -> list[str]:
    """Loads (paginated) the names of all recipes already in Tandoor, so they can
    be checked locally for possible duplicates before import. A page limit guards
    against very large instances (several thousand recipes)."""
    names: list[str] = []
    url = "/recipe/"
    params = {"page_size": 200}

    for _ in range(max_pages):
        try:
            resp = client.get(url, params=params if url == "/recipe/" else None)
        except httpx.HTTPError as exc:
            raise TandoorError(f"Network error loading existing recipes: {exc}") from exc
        if resp.status_code != 200:
            raise TandoorError(
                f"Could not load existing recipes ({resp.status_code}): {resp.text[:200]}"
            )
        data = resp.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        names.extend(item.get("name", "") for item in results if item.get("name"))

        next_url = data.get("next") if isinstance(data, dict) else None
        if not next_url:
            break
        # 'next' is a full URL - use it as-is for the next request
        url = next_url
        params = None

    return names


# ---------- Tag reuse ----------

def fetch_all_keyword_names(client: httpx.Client, max_pages: int = 10) -> list[str]:
    """Loads (paginated) the names of all keywords/tags already in Tandoor, so the
    AI extraction prompt can be told to prefer reusing them instead of creating
    near-duplicate tags. Non-fatal by design: callers should catch TandoorError
    and simply skip tag-reuse hints rather than failing the whole job."""
    names: list[str] = []
    url = "/keyword/"
    params = {"page_size": 200}

    for _ in range(max_pages):
        try:
            resp = client.get(url, params=params if url == "/keyword/" else None)
        except httpx.HTTPError as exc:
            raise TandoorError(f"Network error loading existing keywords: {exc}") from exc
        if resp.status_code != 200:
            raise TandoorError(
                f"Could not load existing keywords ({resp.status_code}): {resp.text[:200]}"
            )
        data = resp.json()
        results = data.get("results", data) if isinstance(data, dict) else data
        names.extend(item.get("name", "") for item in results if item.get("name"))

        next_url = data.get("next") if isinstance(data, dict) else None
        if not next_url:
            break
        url = next_url
        params = None

    return names
