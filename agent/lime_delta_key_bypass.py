"""Lime-style live Delta key bypass (test/latest2 only).

Real Lime flow (see ``lime auto bypass delta.mp4``):
  1. Launch clone 1 (stagger).
  2. OCR detects Delta dialog (``Enter key`` / ``Receive Key`` / ``Welcome Back``).
  3. Tap **Receive Key** → Delta opens ad/key link containing ``token=…``.
  4. Capture token from clipboard / logcat / captured URL text.
  5. Force-close clone 1.
  6. ``GET {origin}/bypass?token=…`` → Delta license key.
  7. ``POST {origin}/license/activate`` + inject ``…/Internals/Cache/license``.
  8. Relaunch clone 1, then stagger continues.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from .constants import DATA_DIR, DEFAULT_LICENSE_SERVER_URL
from .lime_channel import lime_detection_enabled

STATE_PATH = DATA_DIR / "lime-delta-key-bypass-state.json"
_TOKEN_IN_URL_RE = re.compile(
    r"(https?://[^\s\"'<>]+?/bypass\?token=[^\s\"'&<>]+|[?&]token=([^\s\"'&<>#]+))",
    re.I,
)
_BYPASS_LINK_RE = re.compile(r"https?://[^\s\"'<>]+?/bypass\?token=[^\s\"'&<>]+", re.I)

DELTA_DIALOG_MARKERS = (
    "enter key",
    "welcome back",
    "receive key",
    "get key",
    "activate delta",
    "key system",
    "paste key",
    "delta key",
    "license key",
)

RECEIVE_KEY_TAP_MARKERS = (
    "receive key",
    "get key",
    "key bypass",
)

_flow_lock = threading.RLock()
_session_first_pkg: str = ""
_bypass_done_for: set[str] = set()


@dataclass
class DeltaBypassState:
    enabled: bool = False
    mode: str = "lime_live_flow"
    package: str = ""
    phase: str = ""
    last_attempt_at: float | None = None
    last_success_at: float | None = None
    last_error: str = ""
    last_key_masked: str = ""
    last_token_hint: str = ""
    bypass_base_url: str = ""
    ocr_scans: int = 0
    token_source: str = ""
    relaunch_requested: bool = False
    packages_written: list[str] = field(default_factory=list)


def parse_bypass_token(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    m = _BYPASS_LINK_RE.search(s)
    if m:
        q = parse_qs(urlparse(m.group(0)).query)
        return str((q.get("token") or [""])[0]).strip()
    m2 = _TOKEN_IN_URL_RE.search(s)
    if m2:
        if m2.lastindex and m2.lastindex >= 2 and m2.group(2):
            return m2.group(2).strip()
        q = parse_qs(urlparse(m2.group(0)).query if "://" in m2.group(0) else f"?{m2.group(0)}")
        return str((q.get("token") or [""])[0]).strip()
    if "token=" in s.lower():
        return s.split("token=", 1)[1].split("&", 1)[0].split("#", 1)[0].strip()
    return s


def parse_bypass_origin(text: str) -> str:
    m = _BYPASS_LINK_RE.search(text or "")
    if m:
        p = urlparse(m.group(0))
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}".rstrip("/")
    return ""


def _default_base_url() -> str:
    env = (os.environ.get("DENG_REJOIN_DELTA_BYPASS_BASE_URL") or "").strip().rstrip("/")
    if env:
        return env
    try:
        from .config import load_config

        cfg = load_config()
        block = cfg.get("delta_bypass") if isinstance(cfg.get("delta_bypass"), dict) else {}
        cfg_base = str(block.get("base_url") or "").strip().rstrip("/")
        if cfg_base:
            return cfg_base
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_LICENSE_SERVER_URL.rstrip("/")


def _ocr_screen_text() -> str:
    try:
        from . import snapshot as _snap

        cap = _snap.capture_snapshot_detailed()
        if not cap.ok or not cap.data:
            return ""
    except Exception:  # noqa: BLE001
        return ""
    tmp = DATA_DIR / f"lime-delta-flow-{int(time.time())}.png"
    timeout = float(os.environ.get("DENG_REJOIN_DELTA_KEY_OCR_TIMEOUT_SEC", "5") or "5")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(cap.data)
        import shutil

        if shutil.which("termux-ocr"):
            res = subprocess.run(
                ["termux-ocr", "-i", str(tmp)],
                capture_output=True,
                text=True,
                timeout=timeout,
                errors="replace",
            )
            return (res.stdout or "").strip()
        if shutil.which("tesseract"):
            res = subprocess.run(
                ["tesseract", str(tmp), "stdout", "-l", "eng"],
                capture_output=True,
                text=True,
                timeout=timeout,
                errors="replace",
            )
            return (res.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return ""


def _display_size() -> tuple[int, int]:
    try:
        from . import android

        wm = android.get_wm_size()
        w = int(wm.get("width") or wm.get("override_width") or 720)
        h = int(wm.get("height") or wm.get("override_height") or 1280)
        return max(1, w), max(1, h)
    except Exception:  # noqa: BLE001
        return 720, 1280


def _root_input(cmd: list[str]) -> bool:
    try:
        from . import android

        root = android.detect_root()
        if root.available and root.tool:
            res = android.run_root_command(cmd, root_tool=root.tool, timeout=4)
            return bool(res.ok)
        res = subprocess.run(cmd, capture_output=True, timeout=4)
        return res.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _tap_receive_key() -> bool:
    w, h = _display_size()
    # Delta dialog: Receive Key sits below Continue (~68% screen height, centered).
    cx, cy = w // 2, int(h * 0.68)
    return _root_input(["input", "tap", str(cx), str(cy)])


def _read_clipboard() -> str:
    for cmd in (["termux-clipboard-get"], ["termux-clipboard", "get"]):
        try:
            if not __import__("shutil").which(cmd[0].split("-")[0] if cmd[0] == "termux-clipboard-get" else cmd[0]):
                continue
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3, errors="replace")
            text = (res.stdout or "").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001
            continue
    return ""


def _read_logcat_tail(max_lines: int = 400) -> str:
    try:
        res = subprocess.run(
            ["logcat", "-d", "-t", str(max(50, max_lines))],
            capture_output=True,
            text=True,
            timeout=8,
            errors="replace",
        )
        return res.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def _capture_token_from_device(*, wait_s: float = 25.0) -> tuple[str, str, str]:
    """Poll clipboard + logcat for ``/bypass?token=``. Returns (token, origin, source)."""
    deadline = time.time() + max(5.0, wait_s)
    seen: set[str] = set()
    while time.time() < deadline:
        for source, blob in (
            ("clipboard", _read_clipboard()),
            ("logcat", _read_logcat_tail()),
        ):
            if not blob or blob in seen:
                continue
            seen.add(blob)
            origin = parse_bypass_origin(blob)
            tok = parse_bypass_token(blob)
            if tok:
                return tok, origin or _default_base_url(), source
            for m in _BYPASS_LINK_RE.finditer(blob):
                link = m.group(0)
                tok = parse_bypass_token(link)
                if tok:
                    return tok, parse_bypass_origin(link) or _default_base_url(), source
        time.sleep(0.75)
    return "", "", ""


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


def fetch_bypass_license(token: str, *, base_url: str) -> tuple[bool, dict[str, Any]]:
    base = base_url.rstrip("/")
    resp = _http_get_json(f"{base}/bypass?token={token}")
    if resp.get("ok") is True and (resp.get("key") or resp.get("license_key")):
        return True, resp
    if resp.get("key") or resp.get("license_key"):
        resp["ok"] = True
        return True, resp
    return False, resp


def activate_bypass_license(key: str, *, base_url: str) -> tuple[bool, dict[str, Any]]:
    from .license import get_or_create_install_id, hash_install_id

    hwid = hash_install_id(get_or_create_install_id())
    resp = _http_post_json(
        f"{base_url.rstrip('/')}/license/activate",
        {"key": key, "hwid": hwid, "install_id_hash": hwid},
    )
    if resp.get("ok") is True or resp.get("activated") is True:
        return True, resp
    return False, resp


def _write_license(package: str, key: str) -> bool:
    from . import package_key as pk

    path = pk.package_key_license_path(package)
    ok, _err = pk._write_via_python(path, key)
    if ok:
        return True
    try:
        from . import android

        root = android.detect_root()
        if root.available and root.tool:
            ok2, _err2 = pk._write_via_root(path, key, root.tool)
            return bool(ok2)
    except Exception:  # noqa: BLE001
        pass
    return False


def _persist_key(key: str, *, token_source: str, package: str) -> None:
    try:
        from .config import load_config, save_config

        cfg = load_config()
        pkg_keys = dict(cfg.get("package_keys") or {})
        per = dict(pkg_keys.get("per_package") or {})
        per[package] = key
        pkg_keys["per_package"] = per
        cfg["package_keys"] = pkg_keys
        block = dict(cfg.get("delta_bypass") or {})
        block["last_activated_at"] = time.time()
        block["last_source"] = token_source
        block["last_package"] = package
        cfg["delta_bypass"] = block
        save_config(cfg)
    except Exception:  # noqa: BLE001
        pass


def _wait_for_delta_dialog(*, timeout_s: float = 90.0) -> tuple[bool, str]:
    deadline = time.time() + max(10.0, timeout_s)
    scans = 0
    while time.time() < deadline:
        scans += 1
        text = _ocr_screen_text()
        lower = text.lower()
        if text and any(m in lower for m in DELTA_DIALOG_MARKERS):
            return True, text[:240]
        time.sleep(2.0)
    return False, ""


def _force_stop_package(package: str) -> bool:
    try:
        from . import android

        res = android.force_stop_package(package)
        return bool(getattr(res, "ok", False))
    except Exception:  # noqa: BLE001
        return False


def is_first_stagger_package(package: str, config: dict[str, Any]) -> bool:
    global _session_first_pkg
    pkg = str(package or "").strip()
    if not pkg:
        return False
    try:
        from .config import enabled_package_entries

        entries = enabled_package_entries(config)
        names = [str(e.get("package") or "").strip() for e in entries if e.get("package")]
    except Exception:  # noqa: BLE001
        names = []
    if not names:
        return False
    if not _session_first_pkg:
        _session_first_pkg = names[0]
    return pkg == _session_first_pkg


def run_lime_delta_bypass_flow(
    package: str,
    config: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Execute full Lime bypass on first clone after its initial Start launch."""
    state = DeltaBypassState(
        enabled=lime_detection_enabled(),
        package=str(package or "").strip(),
    )

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

    pkg = state.package
    if not pkg or not is_first_stagger_package(pkg, config):
        state.last_error = "not_first_stagger_package"
        return _finish_state(state)

    with _flow_lock:
        if pkg in _bypass_done_for and not force:
            state.last_error = "already_completed"
            state.phase = "skipped"
            return _finish_state(state)

        state.last_attempt_at = time.time()
        dialog_timeout = float(os.environ.get("DENG_REJOIN_DELTA_DIALOG_WAIT_SEC", "90") or "90")
        token_wait = float(os.environ.get("DENG_REJOIN_DELTA_TOKEN_WAIT_SEC", "25") or "25")

        state.phase = "wait_delta_dialog"
        found, ocr_sample = _wait_for_delta_dialog(timeout_s=dialog_timeout)
        state.ocr_scans += 1
        if not found:
            state.last_error = "delta_dialog_not_detected"
            state.phase = "no_dialog"
            return _finish_state(state)

        state.phase = "tap_receive_key"
        _tap_receive_key()
        time.sleep(1.5)

        state.phase = "capture_token"
        token, origin, token_source = _capture_token_from_device(wait_s=token_wait)
        if not token:
            state.last_error = "token_not_captured"
            state.phase = "token_missing"
            return _finish_state(state)

        state.last_token_hint = token[:6] + "…" if len(token) > 6 else token
        state.token_source = token_source
        state.bypass_base_url = origin or _default_base_url()

        state.phase = "force_close"
        _force_stop_package(pkg)
        time.sleep(0.8)

        state.phase = "fetch_key"
        ok, bypass_resp = fetch_bypass_license(token, base_url=state.bypass_base_url)
        if not ok:
            state.last_error = str(
                bypass_resp.get("message") or bypass_resp.get("error") or "bypass_fetch_failed"
            )[:120]
            return _finish_state(state)

        key = str(bypass_resp.get("key") or bypass_resp.get("license_key") or "").strip()
        if not key:
            state.last_error = "bypass_missing_key"
            return _finish_state(state)

        state.phase = "activate"
        act_ok, _act = activate_bypass_license(key, base_url=state.bypass_base_url)
        if not act_ok:
            state.last_error = "activate_failed"
            return _finish_state(state)

        state.phase = "inject"
        if not _write_license(pkg, key):
            state.last_error = "inject_failed"
            return _finish_state(state)

        _persist_key(key, token_source=token_source, package=pkg)
        from .package_key import mask_package_key

        state.last_key_masked = mask_package_key(key)
        state.packages_written = [pkg]
        state.last_success_at = time.time()
        state.relaunch_requested = True
        state.phase = "done"
        state.last_error = ""
        _bypass_done_for.add(pkg)
        return _finish_state(state)


def _finish_state(state: DeltaBypassState) -> dict[str, Any]:
    row = {
        "enabled": state.enabled,
        "mode": state.mode,
        "package": state.package or None,
        "phase": state.phase or None,
        "last_attempt_at": state.last_attempt_at,
        "last_success_at": state.last_success_at,
        "last_error": state.last_error or None,
        "last_key_masked": state.last_key_masked or None,
        "last_token_hint": state.last_token_hint or None,
        "token_source": state.token_source or None,
        "bypass_base_url": state.bypass_base_url or None,
        "ocr_scans": state.ocr_scans,
        "relaunch_requested": state.relaunch_requested,
        "packages_written": state.packages_written,
    }
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
    return row


def reset_session_state() -> None:
    global _session_first_pkg
    _session_first_pkg = ""
    _bypass_done_for.clear()


def start_delta_key_bypass() -> dict[str, Any] | None:
    """No-op on monitor start — live flow runs from first Start launch hook."""
    return probe_snapshot()


def probe_snapshot() -> dict[str, Any]:
    try:
        if STATE_PATH.is_file():
            parsed = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                parsed["enabled"] = lime_detection_enabled()
                parsed.setdefault("mode", "lime_live_flow")
                return parsed
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {"enabled": lime_detection_enabled(), "mode": "lime_live_flow", "phase": "idle"}
