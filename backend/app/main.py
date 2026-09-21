from __future__ import annotations

import asyncio
import difflib
import logging
import os
import shutil
import threading

from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import jobs, tandoor_client
from .ai_extractor import extract_recipes_from_pages, guess_cookbook_title
from .config import settings, get_ui_language_code
from .pdf_processor import process_pdf
from .schemas import ExtractedRecipe

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cookbook-importer")

app = FastAPI(title="Cookbook -> Tandoor Importer")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

DUPLICATE_SIMILARITY_THRESHOLD = 0.82  # titles scoring at or above this (0-1) count as "similar"
CLEANUP_INTERVAL_SECONDS = 3600        # how often the background cleanup task runs


def _job_dir(job_id: str) -> str:
    return os.path.join(settings.data_dir, job_id)


def _mark_duplicates(job) -> None:
    """Fetches all existing Tandoor recipe names once and flags recipes with an
    exact/very similar title match. Non-fatal: if the request fails (e.g. Tandoor
    not configured/reachable) it's simply skipped - the import itself doesn't
    depend on this."""
    if not settings.check_duplicates:
        return
    try:
        with tandoor_client.get_client() as client:
            existing_names = tandoor_client.fetch_all_recipe_names(client)
    except Exception as exc:  # noqa: BLE001
        log.info("Duplicate check skipped (Tandoor unreachable/not configured): %s", exc)
        return

    if not existing_names:
        return

    existing_lower = {n.strip().lower(): n for n in existing_names if n.strip()}

    for recipe in job.recipes:
        title_norm = recipe.title.strip().lower()
        if not title_norm:
            continue

        if title_norm in existing_lower:
            recipe.duplicate_match = existing_lower[title_norm]
            recipe.duplicate_exact = True
            recipe.selected = False  # deselect exact matches as a precaution
            continue

        best_ratio = 0.0
        best_name = None
        for norm, original in existing_lower.items():
            ratio = difflib.SequenceMatcher(None, title_norm, norm).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_name = original
        if best_ratio >= DUPLICATE_SIMILARITY_THRESHOLD:
            recipe.duplicate_match = best_name
            recipe.duplicate_exact = False
            # only a similar match: leave it selected, just warn


def _fetch_existing_tags() -> list[str] | None:
    """Fetches existing Tandoor keywords once, so the extraction prompt can ask the
    AI to prefer reusing them. Non-fatal: returns None if unavailable/disabled."""
    if not settings.reuse_existing_tags:
        return None
    try:
        with tandoor_client.get_client() as client:
            return tandoor_client.fetch_all_keyword_names(client)
    except Exception as exc:  # noqa: BLE001
        log.info("Tag reuse lookup skipped (Tandoor unreachable/not configured): %s", exc)
        return None


def _run_extraction(job_id: str, pdf_path: str) -> None:
    job = jobs.get_job(job_id)
    if job is None:
        return
    try:
        job.progress_label = "Reading PDF …"
        jobs.save_job(job)

        images_dir = os.path.join(_job_dir(job_id), "images")
        result = process_pdf(pdf_path, images_dir)
        job.page_count = result["page_count"]
        job.images = {
            iid: {"page": info["page"], "filename": info["filename"]}
            for iid, info in result["images"].items()
        }
        jobs.save_job(job)

        def progress_cb(idx: int, total: int, page_start: int, page_end: int) -> None:
            job.progress_current = idx
            job.progress_total = total
            page_range = f"page {page_start}" if page_start == page_end else f"pages {page_start}-{page_end}"
            job.progress_label = f"Analyzed {page_range} ({idx}/{total})"
            jobs.save_job(job)

        existing_tags = _fetch_existing_tags()
        toc_pages = result.get("toc_pages") if settings.toc_aware_chunking else None

        recipes: list[ExtractedRecipe]
        recipes, usage = extract_recipes_from_pages(
            result["pages"],
            on_progress=progress_cb,
            toc_pages=toc_pages,
            existing_tags=existing_tags,
        )
        job.recipes = recipes
        job.token_usage.add(usage)
        jobs.match_images_to_recipes(job)

        job.progress_label = "Determining cookbook title …"
        jobs.save_job(job)
        guess, title_usage = guess_cookbook_title(
            result["pages"], job.filename, metadata_title=result.get("metadata_title", "")
        )
        job.token_usage.add(title_usage)
        job.suggested_cookbook_name = guess
        job.cookbook_name = guess

        job.progress_label = "Checking for duplicates already in Tandoor …"
        jobs.save_job(job)
        _mark_duplicates(job)

        job.status = "ready"
    except Exception as exc:  # noqa: BLE001
        log.exception("Extraction failed for job %s", job_id)
        job.status = "error"
        job.error = str(exc)
    finally:
        jobs.save_job(job)


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    job = jobs.create_job(file.filename)
    job_dir = _job_dir(job.id)
    os.makedirs(job_dir, exist_ok=True)
    pdf_path = os.path.join(job_dir, "source.pdf")

    size = 0
    max_bytes = settings.max_upload_mb * 1024 * 1024
    with open(pdf_path, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                out.close()
                shutil.rmtree(job_dir, ignore_errors=True)
                raise HTTPException(413, f"File larger than {settings.max_upload_mb} MB.")
            out.write(chunk)

    jobs.save_job(job)
    threading.Thread(target=_run_extraction, args=(job.id, pdf_path), daemon=True).start()

    return {"job_id": job.id}


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    return job.model_dump()


@app.get("/api/jobs/{job_id}/images/{image_id}")
async def get_job_image(job_id: str, image_id: str):
    job = jobs.get_job(job_id)
    if job is None or image_id not in job.images:
        raise HTTPException(404, "Image not found.")
    filename = job.images[image_id]["filename"]
    path = os.path.join(_job_dir(job_id), "images", filename)
    if not os.path.exists(path):
        raise HTTPException(404, "Image file is missing.")
    return FileResponse(path)


@app.put("/api/jobs/{job_id}/recipes/{recipe_id}")
async def update_recipe(job_id: str, recipe_id: str, payload: dict = Body(...)):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    for i, r in enumerate(job.recipes):
        if r.id == recipe_id:
            updated = r.model_copy(update=payload)
            job.recipes[i] = updated
            jobs.save_job(job)
            return updated.model_dump()
    raise HTTPException(404, "Recipe not found.")


@app.put("/api/jobs/{job_id}")
async def update_job(job_id: str, payload: dict = Body(...)):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    allowed_fields = {"cookbook_name"}
    for key, value in payload.items():
        if key in allowed_fields:
            setattr(job, key, value)
    jobs.save_job(job)
    return job.model_dump()


@app.post("/api/jobs/{job_id}/import")
async def import_selected(job_id: str, body: dict = Body(default={})):
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")

    recipe_ids = body.get("recipe_ids")  # None = import all selected recipes
    to_import = [
        r for r in job.recipes
        if (recipe_ids is None and r.selected) or (recipe_ids is not None and r.id in recipe_ids)
    ]

    cookbook_name = (body.get("cookbook_name") or job.cookbook_name or "").strip()
    if cookbook_name:
        job.cookbook_name = cookbook_name
        jobs.save_job(job)

    results = []
    cookbook_warning = None

    try:
        with tandoor_client.get_client() as client:
            cookbook_id = None
            cookbook_endpoint = None
            if cookbook_name:
                try:
                    cookbook_id, cookbook_endpoint = tandoor_client.get_or_create_cookbook(client, cookbook_name)
                except tandoor_client.TandoorError as exc:
                    cookbook_warning = str(exc)
                    log.warning("Could not create cookbook: %s", exc)

            for recipe in to_import:
                recipe.import_status = "importing"
                jobs.save_job(job)

                image_path = None
                if recipe.selected_image_id and recipe.selected_image_id in job.images:
                    image_path = os.path.join(
                        _job_dir(job_id), "images", job.images[recipe.selected_image_id]["filename"]
                    )

                warnings: list[str] = []
                try:
                    tandoor_id = tandoor_client.create_recipe(client, recipe)
                    recipe.tandoor_recipe_id = tandoor_id

                    if image_path and os.path.exists(image_path):
                        try:
                            tandoor_client.upload_image(client, tandoor_id, image_path)
                        except tandoor_client.TandoorError as exc:
                            warnings.append(f"Image upload failed: {exc}")

                    if cookbook_id is not None:
                        try:
                            tandoor_client.add_recipe_to_cookbook(client, cookbook_id, cookbook_endpoint, tandoor_id)
                        except tandoor_client.TandoorError as exc:
                            warnings.append(str(exc))

                    recipe.import_status = "imported"
                    recipe.import_error = "; ".join(warnings) if warnings else None
                except Exception as exc:  # noqa: BLE001
                    recipe.import_status = "error"
                    recipe.import_error = str(exc)
                    log.warning("Import failed for recipe %s: %s", recipe.title, exc)

                results.append({
                    "id": recipe.id,
                    "status": recipe.import_status,
                    "tandoor_recipe_id": recipe.tandoor_recipe_id,
                    "error": recipe.import_error,
                })
                jobs.save_job(job)

    except tandoor_client.TandoorError as exc:
        # e.g. TANDOOR_URL/TANDOOR_TOKEN missing entirely -> mark all affected recipes as failed
        for recipe in to_import:
            recipe.import_status = "error"
            recipe.import_error = str(exc)
            results.append({
                "id": recipe.id, "status": "error",
                "tandoor_recipe_id": None, "error": str(exc),
            })
        jobs.save_job(job)

    return {"results": results, "cookbook_name": cookbook_name or None, "cookbook_warning": cookbook_warning}


@app.get("/api/config")
async def get_config():
    return {
        "language_code": get_ui_language_code(settings.output_language),
        "output_language": settings.output_language,
        "convert_to_metric": settings.convert_to_metric,
        "check_duplicates": settings.check_duplicates,
    }


@app.get("/api/tandoor/status")
async def tandoor_status():
    try:
        tandoor_client.test_connection()
        return {"connected": True}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"connected": False, "error": str(exc)}, status_code=200)


# ---------- Housekeeping: periodically delete old jobs and their uploaded files ----------

async def _cleanup_loop() -> None:
    while True:
        try:
            jobs.cleanup_old_jobs(settings.data_dir, settings.job_retention_hours)
        except Exception:  # noqa: BLE001
            log.exception("Background cleanup failed")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@app.on_event("startup")
async def on_startup() -> None:
    # Run once immediately (covers jobs left over from a previous container run),
    # then keep running in the background for as long as the app is up.
    jobs.cleanup_old_jobs(settings.data_dir, settings.job_retention_hours)
    asyncio.create_task(_cleanup_loop())


# Mount the static frontend last, so /api/* routes take precedence
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
