"""Maintenance-tool runs ("jobs") and their suggestions. Kept in memory for
fast polling AND written to the data volume (tool_jobs/<id>.json), so open
suggestions survive a container restart or update."""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid

from .config import settings
from .schemas import ToolJob

log = logging.getLogger("tandoor-helper")

_tool_jobs: dict[str, ToolJob] = {}
_lock = threading.Lock()
_last_written: dict[str, float] = {}

WRITE_INTERVAL_WHILE_SCANNING = 5.0  # seconds - progress updates are frequent, disk writes needn't be
PENDING_RETENTION_DAYS = 30          # runs with unreviewed suggestions are kept this long


def _dir() -> str:
    return os.path.join(settings.data_dir, "tool_jobs")


def _write(job: ToolJob) -> None:
    try:
        os.makedirs(_dir(), exist_ok=True)
        path = os.path.join(_dir(), f"{job.id}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(job.model_dump_json())
        os.replace(tmp, path)  # atomic - never a half-written file
        _last_written[job.id] = time.time()
    except OSError as exc:
        log.warning("Could not persist tool job %s: %s", job.id, exc)


def load_tool_jobs() -> int:
    """Called once at startup. A run that was still scanning when the app
    stopped can't continue (its thread is gone) - it's marked cancelled, so
    the suggestions found so far stay usable."""
    if not os.path.isdir(_dir()):
        return 0
    loaded = 0
    for name in os.listdir(_dir()):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(_dir(), name), encoding="utf-8") as f:
                job = ToolJob.model_validate_json(f.read())
        except Exception as exc:  # noqa: BLE001
            log.warning("Skipping unreadable tool job file %s: %s", name, exc)
            continue
        if job.status == "scanning":
            job.status = "cancelled"
            job.progress_label = None
        with _lock:
            _tool_jobs[job.id] = job
        loaded += 1
    return loaded


def create_tool_job(tool: str) -> ToolJob:
    job = ToolJob(id=uuid.uuid4().hex[:12], tool=tool)
    with _lock:
        _tool_jobs[job.id] = job
    return job


def get_tool_job(job_id: str) -> ToolJob | None:
    with _lock:
        return _tool_jobs.get(job_id)


def save_tool_job(job: ToolJob) -> None:
    with _lock:
        _tool_jobs[job.id] = job
    # While scanning, progress is saved every few items - only write to
    # disk every few seconds then. Everything else is written right away.
    if job.status == "scanning" and time.time() - _last_written.get(job.id, 0) < WRITE_INTERVAL_WHILE_SCANNING:
        return
    _write(job)


def list_all_tool_jobs() -> list[ToolJob]:
    with _lock:
        return list(_tool_jobs.values())


def cleanup_old_tool_jobs(retention_hours: int) -> int:
    """Removes finished runs older than retention_hours - but keeps runs
    with suggestions nobody has reviewed yet for PENDING_RETENTION_DAYS."""
    if retention_hours <= 0:
        return 0
    now = time.time()
    with _lock:
        stale_ids = [
            jid for jid, job in _tool_jobs.items()
            if job.status != "scanning" and (
                job.created_at < now - PENDING_RETENTION_DAYS * 86400
                or (job.created_at < now - retention_hours * 3600
                    and not any(s.status == "pending" for s in job.suggestions))
            )
        ]
        for jid in stale_ids:
            del _tool_jobs[jid]
    for jid in stale_ids:
        _last_written.pop(jid, None)
        try:
            os.remove(os.path.join(_dir(), f"{jid}.json"))
        except OSError:
            pass
    return len(stale_ids)


def check_cancelled(job: ToolJob) -> bool:
    """Call this after each chunk/item in a scan loop. If a POST .../cancel
    request has set job.cancel_requested (the same in-memory ToolJob object,
    so the flag is visible immediately - no extra signalling needed for this
    single-process, in-memory job store), marks the job cancelled, saves it,
    and returns True so the caller can break out of its loop and return."""
    if job.cancel_requested:
        job.status = "cancelled"
        job.progress_label = None
        save_tool_job(job)
        return True
    return False


def list_tool_jobs(tool: str) -> list[ToolJob]:
    with _lock:
        return [job for job in _tool_jobs.values() if job.tool == tool]
