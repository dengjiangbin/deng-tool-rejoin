"""Persisted per-package Launching timestamps for Status Monitor runtime."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from .constants import DATA_DIR

_STATE_PATH = DATA_DIR / "status-monitor-runtime-state.json"


def _load_state() -> dict[str, Any]:
    try:
        if _STATE_PATH.is_file():
            parsed = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                packages = parsed.get("packages")
                if isinstance(packages, dict):
                    return {"packages": packages}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {"packages": {}}


def _save_state(state: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps({"packages": state.get("packages") or {}}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def load_package_launch_started_at() -> dict[str, float]:
    packages = _load_state().get("packages") or {}
    out: dict[str, float] = {}
    for pkg, row in packages.items():
        if not isinstance(row, dict):
            continue
        raw = row.get("package_launch_started_at")
        try:
            out[str(pkg)] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def persist_package_launch_started(package: str, started_at: float | None = None) -> float:
    pkg = str(package or "").strip()
    if not pkg:
        return 0.0
    ts = float(started_at if started_at is not None else time.time())
    state = _load_state()
    packages = state.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    row["package_launch_started_at"] = ts
    row["updated_at"] = time.time()
    packages[pkg] = row
    _save_state(state)
    return ts


def clear_package_launch_started(package: str) -> None:
    pkg = str(package or "").strip()
    if not pkg:
        return
    state = _load_state()
    packages = state.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    row.pop("package_launch_started_at", None)
    row["updated_at"] = time.time()
    if row:
        packages[pkg] = row
    else:
        packages.pop(pkg, None)
    _save_state(state)


def monitor_started_at_from_config(config_data: dict[str, Any] | None) -> float | None:
    cfg = config_data if isinstance(config_data, dict) else {}
    raw = cfg.get("monitor_started_at")
    try:
        if raw is not None:
            return float(raw)
    except (TypeError, ValueError):
        pass
    start_times = cfg.get("package_start_times") or {}
    if not isinstance(start_times, dict):
        return None
    earliest: float | None = None
    for value in start_times.values():
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
            earliest = ts if earliest is None else min(earliest, ts)
        except (ValueError, TypeError):
            continue
    return earliest


def fallback_monitor_started_at(
    config_data: dict[str, Any] | None,
    package: str,
) -> tuple[float | None, str]:
    cfg = config_data if isinstance(config_data, dict) else {}
    pkg = str(package or "").strip()
    start_times = cfg.get("package_start_times") or {}
    if isinstance(start_times, dict) and pkg in start_times:
        try:
            dt = datetime.fromisoformat(str(start_times[pkg]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp(), "fallback_monitor_started_at"
        except (ValueError, TypeError):
            pass
    global_at = monitor_started_at_from_config(cfg)
    if global_at is not None:
        return global_at, "fallback_monitor_started_at"
    return None, "missing"
