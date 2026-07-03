"""Lime-style Delta bypass via ``/bypass?token=`` license activation (test/latest2).

Flow (matches Lime Rejoiner APK paths):
  1. User pastes bypass link or token from ad-gate / Discord.
  2. GET ``{base}/bypass?token=…`` → executor license key.
  3. POST ``{base}/license/activate`` with key + device HWID.
  4. Write key to each clone's Delta license file and config ``package_keys``.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from .constants import DATA_DIR, DEFAULT_LICENSE_SERVER_URL
from .lime_channel import lime_detection_enabled

STATE_PATH = DATA_DIR / "lime-delta-key-bypass-state.json"
_TOKEN_IN_URL_RE = re.compile(r"[?&]token=([^&\s#]+)", re.I)

_active_lock = threading.Lock()
_activation_lock = threading.RLock()
_activated_once = False


@dataclass
class DeltaBypassState:
    enabled: bool = False
    last_attempt_at: float | None = None
    last_success_at: float | None = None
    last_token_hint: str = ""
    last_error: str = ""
    last_key_masked: str = ""
    activation_count: int = 0
    packages_written: list[str] = field(default_factory=list)
    bypass_base_url: str = ""
    mode: str = "token_activation"


def _default_base_url() -> str:
    env = (os.environ.get("DENG_REJOIN_DELTA_BYPASS_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env
    try:
        from .config import load_config

        cfg = load_config()
        lic = cfg.get("license") if isinstance(cfg.get("license"), dict) else {}
        server = str(lic.get("server_url") or "").strip().rstrip("/")
        if server:
            return server
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_LICENSE_SERVER_URL.rstrip("/")


def parse_bypass_token(raw: str) -> str:
    """Extract token from plain token or full ``…/bypass?token=…`` link."""
    s = (raw or "").strip()
    if not s:
        return ""
    if "token=" in s.lower():
        m = _TOKEN_IN_URL_RE.search(s)
        if m:
            return m.group(1).strip()
        if "://" in s:
            q = parse_qs(urlparse(s).query)
            tok = (q.get("token") or [""])[0]
            return str(tok).strip()
        return s.split("token=", 1)[1].split("&", 1)[0].split("#", 1)[0].strip()
    return s


def resolve_bypass_token(config: dict[str, Any] | None = None) -> tuple[str, str]:
    """Return ``(token, source)`` from env, config link, or config token."""
    env_tok = parse_bypass_token(os.environ.get("DENG_REJOIN_DELTA_BYPASS_TOKEN", ""))
    if env_tok:
        return env_tok, "env"

    cfg = config
    if cfg is None:
        try:
            from .config import load_config

            cfg = load_config()
        except Exception:  # noqa: BLE001
            cfg = {}

    block = cfg.get("delta_bypass") if isinstance(cfg.get("delta_bypass"), dict) else {}
    for key_name, source in (("link", "config_link"), ("token", "config_token"), ("url", "config_url")):
        tok = parse_bypass_token(str(block.get(key_name) or ""))
        if tok:
            return tok, source

    pkg_keys = cfg.get("package_keys") if isinstance(cfg.get("package_keys"), dict) else {}
    tok = parse_bypass_token(str(pkg_keys.get("bypass_link") or ""))
    if tok:
        return tok, "package_keys.bypass_link"
    return "", ""


def _http_get_json(url: str, *, timeout: int = 30) -> dict[str, Any]:
    from . import safe_http

    try:
        return safe_http.get_json(url, timeout=timeout)
    except safe_http.SafeHttpStatusError as exc:
        try:
            parsed = json.loads(exc.body)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
        return {"ok": False, "error": "http_error", "message": f"HTTP {exc.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "network_error", "message": str(exc)[:120]}


def _http_post_json(url: str, payload: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
    from . import safe_http

    try:
        return safe_http.post_json(url, payload, timeout=timeout)
    except safe_http.SafeHttpStatusError as exc:
        try:
            parsed = json.loads(exc.body)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError, AttributeError):
            pass
        return {"ok": False, "error": "http_error", "message": f"HTTP {exc.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "network_error", "message": str(exc)[:120]}


def fetch_bypass_license(token: str, *, base_url: str | None = None) -> tuple[bool, dict[str, Any]]:
    base = (base_url or _default_base_url()).rstrip("/")
    url = f"{base}/bypass?token={token}"
    resp = _http_get_json(url)
    if resp.get("ok") is True and (resp.get("key") or resp.get("license_key")):
        return True, resp
    if resp.get("key") or resp.get("license_key"):
        resp["ok"] = True
        return True, resp
    return False, resp


def activate_bypass_license(
    key: str,
    *,
    base_url: str | None = None,
    install_id: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    from .license import get_or_create_install_id, hash_install_id

    base = (base_url or _default_base_url()).rstrip("/")
    iid = (install_id or get_or_create_install_id()).strip()
    hwid = hash_install_id(iid)
    payload = {
        "key": key,
        "hwid": hwid,
        "install_id_hash": hwid,
    }
    resp = _http_post_json(f"{base}/license/activate", payload)
    if resp.get("ok") is True or resp.get("activated") is True:
        return True, resp
    return False, resp


def _write_license_to_packages(key: str, config: dict[str, Any]) -> list[str]:
    from . import package_key as pk
    from .auto_execute import configured_package_names

    written: list[str] = []
    for pkg in configured_package_names(config):
        path = pk.package_key_license_path(pkg)
        ok, _err = pk._write_via_python(path, key)
        if not ok:
            try:
                from . import android

                root = android.detect_root()
                if root.available and root.tool:
                    ok, _err = pk._write_via_root(path, key, root.tool)
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            written.append(pkg)
    return written


def _persist_config_key(config: dict[str, Any], key: str, token_source: str) -> None:
    try:
        from .config import load_config, save_config

        cfg = load_config()
        pkg_keys = dict(cfg.get("package_keys") or {})
        pkg_keys["global"] = key
        cfg["package_keys"] = pkg_keys
        block = dict(cfg.get("delta_bypass") or {})
        block["last_activated_at"] = time.time()
        block["last_source"] = token_source
        cfg["delta_bypass"] = block
        save_config(cfg)
    except Exception:  # noqa: BLE001
        pass


def activate_delta_bypass_if_configured(
    config: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Redeem bypass token and write Delta license — idempotent per session."""
    global _activated_once

    state = DeltaBypassState(enabled=lime_detection_enabled(), mode="token_activation")
    if not lime_detection_enabled():
        state.last_error = "not_test_latest2"
        return _finish_state(state)

    if os.environ.get("DENG_REJOIN_DISABLE_DELTA_KEY_BYPASS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        state.last_error = "disabled_by_env"
        return _finish_state(state)

    token, source = resolve_bypass_token(config)
    if not token:
        state.last_error = "no_token_configured"
        return _finish_state(state)

    with _activation_lock:
        if _activated_once and not force:
            state.last_error = "already_activated_this_session"
            return _finish_state(state)

        state.last_attempt_at = time.time()
        state.last_token_hint = token[:4] + "…" if len(token) > 4 else "…"
        state.bypass_base_url = _default_base_url()

        ok, bypass_resp = fetch_bypass_license(token, base_url=state.bypass_base_url)
        if not ok:
            state.last_error = str(
                bypass_resp.get("message") or bypass_resp.get("error") or "bypass_failed"
            )[:120]
            return _finish_state(state)

        key = str(bypass_resp.get("key") or bypass_resp.get("license_key") or "").strip()
        if not key:
            state.last_error = "bypass_missing_key"
            return _finish_state(state)

        act_ok, act_resp = activate_bypass_license(key, base_url=state.bypass_base_url)
        if not act_ok:
            state.last_error = str(
                act_resp.get("message") or act_resp.get("error") or "activate_failed"
            )[:120]
            return _finish_state(state)

        cfg = config
        if cfg is None:
            try:
                from .config import load_config

                cfg = load_config()
            except Exception:  # noqa: BLE001
                cfg = {}

        written = _write_license_to_packages(key, cfg)
        _persist_config_key(cfg, key, source)

        from .package_key import mask_package_key

        state.last_success_at = time.time()
        state.activation_count += 1
        state.packages_written = written
        state.last_key_masked = mask_package_key(key)
        state.last_error = ""
        _activated_once = True
        return _finish_state(state)


def _finish_state(state: DeltaBypassState) -> dict[str, Any]:
    row = {
        "enabled": state.enabled,
        "mode": state.mode,
        "last_attempt_at": state.last_attempt_at,
        "last_success_at": state.last_success_at,
        "last_token_hint": state.last_token_hint or None,
        "last_key_masked": state.last_key_masked or None,
        "activation_count": state.activation_count,
        "packages_written": state.packages_written,
        "bypass_base_url": state.bypass_base_url or None,
        "last_error": state.last_error or None,
    }
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return row


def start_delta_key_bypass() -> dict[str, Any] | None:
    """Run token activation once (Start prep + monitor fallback)."""
    if not lime_detection_enabled():
        return None

    holder: dict[str, Any] = {}

    def _work() -> None:
        holder["result"] = activate_delta_bypass_if_configured()

    t = threading.Thread(target=_work, name="lime-delta-bypass-activate", daemon=True)
    t.start()
    t.join(timeout=45.0)
    return holder.get("result")


def probe_snapshot() -> dict[str, Any]:
    try:
        if STATE_PATH.is_file():
            parsed = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                parsed.setdefault("mode", "token_activation")
                parsed["enabled"] = lime_detection_enabled()
                token, _ = resolve_bypass_token()
                parsed["token_configured"] = bool(token)
                return parsed
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    token, _ = resolve_bypass_token()
    return {
        "enabled": lime_detection_enabled(),
        "mode": "token_activation",
        "token_configured": bool(token),
        "activation_count": 0,
    }
