"""Persisted online_since for Status Monitor runtime (confirmed online only)."""

from __future__ import annotations

import json
import time
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
    """Debug-only launch duration anchor — never used for Status Monitor Runtime."""
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


def load_online_since(package: str) -> tuple[float | None, dict[str, Any]]:
    pkg = str(package or "").strip()
    if not pkg:
        return None, {}
    row = (_load_state().get("packages") or {}).get(pkg)
    if not isinstance(row, dict):
        return None, {}
    try:
        return float(row.get("online_since")), dict(row)
    except (TypeError, ValueError):
        return None, dict(row)


def load_all_online_since() -> dict[str, float]:
    packages = _load_state().get("packages") or {}
    out: dict[str, float] = {}
    for pkg, row in packages.items():
        if not isinstance(row, dict):
            continue
        try:
            out[str(pkg)] = float(row.get("online_since"))
        except (TypeError, ValueError):
            continue
    return out


def mark_online_confirmed_evidence(
    package: str,
    at: float,
    *,
    source: str,
    previous_state: str = "",
) -> float:
    """Set online_since from accepted fallback evidence (presence, logcat join hints)."""
    pkg = str(package or "").strip()
    if not pkg:
        return 0.0
    ts = float(at)
    src = str(source or "online_evidence").strip() or "online_evidence"
    state = _load_state()
    packages = state.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    prev = str(previous_state or row.get("state") or "").strip()
    if prev != "ONLINE_CONFIRMED":
        row["online_since"] = ts
        row["last_transition_at"] = ts
        row["last_transition_reason"] = src
        row["last_online_evidence_at"] = ts
    row["state"] = "ONLINE_CONFIRMED"
    row["runtime_source"] = src
    row["updated_at"] = time.time()
    packages[pkg] = row
    _save_state(state)
    return float(row.get("online_since") or ts)


def mark_online_confirmed_gamejoin(
    package: str,
    at: float,
    *,
    previous_state: str = "",
) -> float:
    """Set online_since from UID-matched gamejoinloadtime (rjn runtime source)."""
    pkg = str(package or "").strip()
    if not pkg:
        return 0.0
    ts = float(at)
    state = _load_state()
    packages = state.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    prev = str(previous_state or row.get("state") or "").strip()
    if prev != "ONLINE_CONFIRMED":
        row["online_since"] = ts
        row["last_transition_at"] = ts
        row["last_transition_reason"] = "gamejoinloadtime"
        row["last_online_evidence_at"] = ts
    row["state"] = "ONLINE_CONFIRMED"
    row["runtime_source"] = "gamejoinloadtime"
    row["last_gamejoinloadtime_at"] = ts
    row["updated_at"] = time.time()
    packages[pkg] = row
    _save_state(state)
    return float(row.get("online_since") or ts)


def mark_online_confirmed(
    package: str,
    now: float,
    evidence: dict[str, Any] | None = None,
    *,
    previous_state: str = "",
) -> float:
    """Set online_since when transitioning into ONLINE_CONFIRMED."""
    pkg = str(package or "").strip()
    if not pkg:
        return 0.0
    ts = float(now)
    state = _load_state()
    packages = state.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    prev = str(previous_state or row.get("state") or "").strip()
    if prev != "ONLINE_CONFIRMED":
        row["online_since"] = ts
        row["last_transition_at"] = ts
        row["last_transition_reason"] = "online_confirmed"
        row["last_online_evidence_at"] = ts
    row["state"] = "ONLINE_CONFIRMED"
    if evidence:
        row["last_online_evidence"] = {
            k: evidence.get(k)
            for k in (
                "resumed_activity",
                "top_activity",
                "experience_level",
                "pid",
            )
            if evidence.get(k)
        }
    row["updated_at"] = time.time()
    packages[pkg] = row
    _save_state(state)
    return float(row.get("online_since") or ts)


def record_lifecycle_transition(
    package: str,
    state: str,
    reason: str,
    *,
    now: float | None = None,
    offline: bool = False,
) -> None:
    pkg = str(package or "").strip()
    if not pkg:
        return
    ts = float(now if now is not None else time.time())
    data = _load_state()
    packages = data.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    row["state"] = str(state or "").strip()
    row["last_transition_at"] = ts
    row["last_transition_reason"] = str(reason or "")[:180]
    if offline:
        row["last_offline_evidence_at"] = ts
        row.pop("online_since", None)
    row["updated_at"] = time.time()
    packages[pkg] = row
    _save_state(data)


def clear_online_since(package: str) -> None:
    pkg = str(package or "").strip()
    if not pkg:
        return
    state = _load_state()
    packages = state.setdefault("packages", {})
    row = dict(packages.get(pkg) or {})
    row.pop("online_since", None)
    row["state"] = "DEAD"
    row["last_transition_reason"] = "online_cleared"
    row["updated_at"] = time.time()
    packages[pkg] = row
    _save_state(state)


def lifecycle_row_for_package(package: str) -> dict[str, Any]:
    pkg = str(package or "").strip()
    row = (_load_state().get("packages") or {}).get(pkg)
    return dict(row) if isinstance(row, dict) else {}


def monitor_started_at_from_config(config_data: dict[str, Any] | None) -> float | None:
    from datetime import datetime, timezone

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
    """Legacy fallback — must not be used for Status Monitor Runtime display."""
    return None, "missing"
