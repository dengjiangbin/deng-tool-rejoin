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


def ensure_session_for_feature(
    feature: str,
    *,
    allow_validate_refresh: bool = True,
    force_refresh: bool = False,
) -> tuple[bool, str]:
    if force_refresh:
        clear_session()
    sid = session_id_for_feature(feature)
    if sid:
        return True, sid
    if not allow_validate_refresh:
        return False, "valid license session required"

    try:
        from .config import load_config
        from .constants import DEFAULT_LICENSE_SERVER_URL, VERSION
        from .license import check_remote_license_status, sync_install_id_with_config
        from .license import get_public_device_model
    except Exception:  # noqa: BLE001
        return False, "could not load license helpers"

    try:
        cfg = load_config()
    except Exception:  # noqa: BLE001
        return False, "could not load saved config"
    lic = cfg.setdefault("license", {})
    key = str(lic.get("key") or cfg.get("license_key") or "").strip()
    if not key:
        return False, (
            "[!] Probe upload requires a valid license session.\n"
            "[?] Open deng-rejoin once and pass license check, or enter license key in the tool."
        )
    try:
        install_id = sync_install_id_with_config(lic)
    except Exception:  # noqa: BLE001
        install_id = str(lic.get("install_id") or "").strip()
    if not install_id:
        return False, "could not determine install ID for license validation"

    server_url = str(lic.get("server_url") or "").strip()
    if not server_url:
        from . import api_config as _api_cfg
        server_url = _api_cfg.license_server_url()
    try:
        result, message = check_remote_license_status(
            server_url,
            license_key=key,
            install_id=install_id,
            device_model=get_public_device_model() or "unknown",
            app_version=VERSION,
            device_label=str(lic.get("device_label") or ""),
        )
    except Exception:  # noqa: BLE001
        return False, "license validation failed before upload"
    if result != "active":
        if result == "requires_manual_rebind":
            return False, "license must be entered manually again after HWID reset; open deng-rejoin"
        return False, message or f"license validation failed: {result}"
    sid = session_id_for_feature(feature)
    if not sid:
        return False, "license validated but server did not issue required capability"
    return True, sid


def clear_session() -> None:
    try:
        _session_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
