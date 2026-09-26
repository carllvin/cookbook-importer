"""Token usage over time: every finished tool run and cookbook/URL import
appends one line to usage.jsonl in the data volume, so the UI can show what
the AI actually used in the last 30 days (per tool)."""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from .config import settings

log = logging.getLogger("tandoor-helper")
_lock = threading.Lock()


def _path() -> str:
    return os.path.join(settings.data_dir, "usage.jsonl")


def record(source: str, input_tokens: int, output_tokens: int) -> None:
    if input_tokens <= 0 and output_tokens <= 0:
        return
    line = json.dumps({"ts": time.time(), "source": source, "in": int(input_tokens), "out": int(output_tokens)})
    try:
        with _lock:
            os.makedirs(settings.data_dir, exist_ok=True)
            with open(_path(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError as exc:
        log.warning("Could not record token usage: %s", exc)


def summary(days: int = 30) -> dict:
    cutoff = time.time() - days * 86400
    by_source: dict[str, dict] = {}
    total = {"input_tokens": 0, "output_tokens": 0}
    try:
        with open(_path(), encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("ts", 0) < cutoff:
                    continue
                src = by_source.setdefault(entry.get("source", "?"), {"input_tokens": 0, "output_tokens": 0})
                for key, short in (("input_tokens", "in"), ("output_tokens", "out")):
                    src[key] += entry.get(short, 0)
                    total[key] += entry.get(short, 0)
    except FileNotFoundError:
        pass
    return {"days": days, "total": total, "by_source": by_source}
