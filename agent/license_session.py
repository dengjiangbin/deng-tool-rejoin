from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def _session_path() -> Path:
    home = Path(os.environ.get("DENG_REJOIN_HOME") or Path.home() / ".deng-tool" / "rejoin")
    return home / ".license-session.json"


def save_session(session: Any) -> None:
    if not isinstance(session, dict):
        return
    sid = str(session.get("session_id") or "").strip()
    if not sid:
        return
    try:
        exp = int(session.get("expires_in") or 0)
    except (TypeError, ValueError):
        exp = 0
    session = dict(session)
    if exp > 0:
        session["saved_at"] = time.time()
    path = _session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session, sort_keys=True), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_session() -> dict[str, Any] | None:
    path = _session_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    sid = str(data.get("session_id") or "").strip()
    if not sid:
        return None
    saved_at = data.get("saved_at")
    expires_in = data.get("expires_in")
    try:
        if float(saved_at) + float(expires_in) <= time.time():
            clear_session()
            return None
    except (TypeError, ValueError):
        return None
    return data


def session_id_for_feature(feature: str) -> str:
    data = load_session()
    if not data:
        return ""
    caps = data.get("capabilities") or {}
    if not isinstance(caps, dict) or not caps.get(feature):
        return ""
    return str(data.get("session_id") or "").strip()


def clear_session() -> None:
    try:
        _session_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
