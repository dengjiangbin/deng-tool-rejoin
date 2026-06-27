"""Structured resize trace for probe/debug."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import DATA_DIR

TRACE_PATH = DATA_DIR / "resize-debug.jsonl"


def append_resize_event(event: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(event)
        payload.setdefault(
            "recorded_at",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        with TRACE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


def read_latest_resize_event() -> dict[str, Any]:
    try:
        if not TRACE_PATH.exists():
            return {}
        last = ""
        with TRACE_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    last = line
        if not last:
            return {}
        data = json.loads(last)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def build_resize_debug_from_event(event: dict[str, Any]) -> dict[str, Any]:
    if not event:
        return {"status": "no_resize_runs_recorded"}
    out = {
        "last_resize_at": event.get("last_resize_at") or event.get("recorded_at"),
        "trigger": event.get("trigger", "unknown"),
        "package_source": event.get("package_source", "own_system"),
        "package_count": event.get("package_count", 0),
        "mode": event.get("mode"),
        "confidence": event.get("confidence"),
        "basis": event.get("basis"),
        "signals": event.get("signals") or {},
        "layout": event.get("layout") or {},
        "packages": event.get("packages") or [],
        "summary": event.get("summary") or {},
    }
    if event.get("skipped_reason"):
        out["skipped_reason"] = event.get("skipped_reason")
    return out
