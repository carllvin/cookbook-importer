from __future__ import annotations

import threading
import time
import uuid

from .schemas import ToolJob

_tool_jobs: dict[str, ToolJob] = {}
_lock = threading.Lock()


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


def cleanup_old_tool_jobs(retention_hours: int) -> int:
    """Same idea as jobs.cleanup_old_jobs, but tool jobs have no files on disk
    to remove - just the in-memory entry."""
    if retention_hours <= 0:
        return 0
    cutoff = time.time() - retention_hours * 3600
    with _lock:
        stale_ids = [jid for jid, job in _tool_jobs.items() if job.created_at < cutoff]
        for jid in stale_ids:
            del _tool_jobs[jid]
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
