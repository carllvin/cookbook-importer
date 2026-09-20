from __future__ import annotations

import threading
import uuid

from .schemas import Job

_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def create_job(filename: str) -> Job:
    job = Job(id=uuid.uuid4().hex[:12], filename=filename)
    with _lock:
        _jobs[job.id] = job
    return job


def get_job(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def save_job(job: Job) -> None:
    with _lock:
        _jobs[job.id] = job


def match_images_to_recipes(job: Job, page_margin: int = 1) -> None:
    """Weist jedem Rezept Bild-Kandidaten zu, deren Seite im (erweiterten) Seitenbereich liegt."""
    for recipe in job.recipes:
        start = recipe.source_page_start - page_margin
        end = recipe.source_page_end + page_margin
        candidates = [
            image_id
            for image_id, info in job.images.items()
            if start <= info["page"] <= end
        ]
        # Bevorzugt Bilder direkt im Kernbereich, dann Randbereich
        candidates.sort(
            key=lambda iid: abs(job.images[iid]["page"] - (recipe.source_page_start + recipe.source_page_end) / 2)
        )
        recipe.candidate_image_ids = candidates
        if candidates and not recipe.selected_image_id:
            recipe.selected_image_id = candidates[0]
