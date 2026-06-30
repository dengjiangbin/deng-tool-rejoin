"""CLI command handlers for DENG Tool: Rejoin."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import account_detect, android, auto_execute, db, package_username, root_access, safe_io, termux_ui
from .banner import banner_text, print_banner
from .config import (
    ConfigError,
    DEFAULT_SCREEN_MODE,
    default_config,
    effective_private_server_url,
    enabled_package_entries,
    enabled_package_names,
    ensure_app_dirs,
    get_package_display_username,
    load_config,
    mask_license_key,
    normalize_package_detection_hint,
    package_display_name,
    package_entry,
    MAPPING_STATUSES,
    safe_config_view,
    save_config,
    validate_account_username,
    validate_config,
    validate_license_key,
    validate_package_detection_hints,
    validate_package_entries,
    validate_package_name,
    validate_private_url_mode,
    validate_roblosecurity_cookie,
    validate_screen_mode,
    validate_username_source,
)
from .constants import (
    APP_HOME,
    DATA_DIR,
    CONFIG_PATH,
    CRASH_LOG_PATH,
    DB_PATH,
    DEFAULT_LICENSE_SERVER_URL,
    DEFAULT_ROBLOX_PACKAGE,
    DEFAULT_ROBLOX_PACKAGE_HINTS,
    GITHUB_REMOTE,
    LOCK_PATH,
    LOG_PATH,
    MONITOR_LOCK_PATH,
    MONITOR_LOG_PATH,
    MONITOR_PID_PATH,
    MONITOR_STATUS_PATH,
    PID_PATH,
    PRODUCT_NAME,
    RAW_INSTALL_URL,
    RUN_DIR,
    START_CRASH_STATE_PATH,
    LOG_DIR,
    TERMUX_BOOT_SCRIPT,
    VERSION,
)
from .doctor import print_doctor, run_doctor
from .launcher import RejoinResult, perform_rejoin
from .launcher_file import create_market_launchers
from .lockfile import LockError, LockManager, is_process_alive, stop_running_agent
from .menu import run_menu
from .onboarding import (
    NEW_USER_HELP_TEXT,
    print_beginner_license_gate_help,
    print_beginner_menu_license_prompt,
)
from .platform_detect import detect_public_download_dir, get_android_release, get_android_sdk, get_platform_info
from .supervisor import MultiPackageSupervisor, WatchdogSupervisor, Supervisor
from . import keystore
from .license import (
    HWID_RESET_REENTRY_MESSAGE,
    LICENSE_CHECK_TIMEOUT_USER_MESSAGE,
    WRONG_DEVICE_USER_MESSAGE,
    bind_remote_license_key,
    check_remote_license_status,
    disable_test_license_bypass,
    enable_test_license_bypass,
    get_device_summary,
    get_or_create_install_id,
    get_public_device_model,
    hash_install_id,
    is_dev_channel,
    is_test_license_bypass_active,
    normalize_license_key,
    sync_install_id_with_config,
)

# Set True only after a successful validate-only check or manual bind this process.
_license_session_validated = False
# Set True when the user manually entered a license key and verification succeeded.
_license_manual_verification_success = False
from . import snapshot, webhook, window_layout
from .runtime_format import format_runtime_compact
from .url_utils import UrlValidationError, detect_launch_mode_from_url, mask_urls_in_text, validate_launch_url

COMMANDS = {
    "setup",
    "first-setup",
    "doctor",
    "status",
    "once",
    "start",
    "stop",
    "reset",
    "config",
    "logs",
    "version",
    "menu",
    "license",
    "monitor",
    "new-user-help",
    "enable-boot",
    "update",
    "scan",
    "map",
    "list",
    "unmap",
    "launch",
    "selftest",
    "state",
}

# ─── ANSI color constants (used only when a tty is available) ─────────────────
# Termux's default monospace font is very thin and the previous status
# colors were unreadable on a cloud-phone screen (user feedback: "i cant
# read shit so thin").  Every status color now starts with the BOLD
# attribute (\033[1;<color>m) so the foreground text uses the BRIGHT
# weight glyphs that all Termux monospace fonts have.
_ANSI_GREEN   = "\033[1;92m"   # bold bright green
_ANSI_YELLOW  = "\033[1;93m"   # bold bright yellow
_ANSI_ORANGE  = "\033[1;38;5;208m"  # bold orange (No Heartbeat — matches APK Warning)
_ANSI_RED     = "\033[1;91m"   # bold bright red
_ANSI_CYAN    = "\033[1;96m"   # bold bright cyan
_ANSI_WHITE   = "\033[1;97m"   # bold bright white
_ANSI_BOLD    = "\033[1m"      # plain bold (no color)
_ANSI_DIM     = "\033[2;37m"   # dim grey (Unknown only — intentionally low-contrast)
_ANSI_RESET   = "\033[0m"
_ANSI_RE      = re.compile(r"\x1b\[[0-9;]*m")
_CONFIG_RECOVERED_DEFAULTS = False
# Legacy account/cookie mapping scanners are intentionally disabled in this
# release. Public setup/start paths must save package names only.
_ACCOUNT_MAPPING_DISABLED = True


def _print_dev_license_skipped(use_color: bool) -> None:
    msg = "Dev Mode: License Check Skipped"
    if use_color:
        print(f"{_ANSI_YELLOW}{msg}{_ANSI_RESET}")
    else:
        print(msg)


def _print_test_license_bypass_active(use_color: bool) -> None:
    msg = "TEST LICENSE BYPASS ACTIVE"
    sub = "Test build only — run: deng-rejoin --disable-test-license-bypass to require a key again."
    if use_color:
        print(f"{_ANSI_YELLOW}{msg}{_ANSI_RESET}")
        print(sub)
    else:
        print(msg)
        print(sub)


def _print_license_ok(use_color: bool) -> None:
    if use_color:
        print(termux_ui.success_line("License OK"))
    else:
        print("OK: License Verified")


def _print_license_err(message: str, use_color: bool) -> None:
    short = message.strip()
    if use_color:
        print(termux_ui.error_line(short))
    else:
        print(short if short.upper().startswith("ERROR:") else f"ERROR: {short}")


def _load_config_for_menu() -> dict[str, Any]:
    """Load config for public menus, recreating defaults on missing/corrupt data."""
    global _CONFIG_RECOVERED_DEFAULTS
    try:
        return load_config()
    except ConfigError:
        _CONFIG_RECOVERED_DEFAULTS = True
        cfg = default_config()
        try:
            return save_config(cfg)
        except Exception:  # noqa: BLE001
            return cfg


def _print_missing_license_prompt(use_color: bool) -> None:
    if use_color:
        print(termux_ui.prompt_prefix("Verifying License"))
        print(termux_ui.warning_line("No License Key Found"))
    else:
        print("[?] Verifying License:")
        print("[!] No License Key Found.")


def _persist_license_status(cfg: dict[str, Any], status: str) -> dict[str, Any]:
    from .config import utc_now

    lic = cfg.setdefault("license", {})
    lic["last_status"] = status
    lic["last_check_at"] = utc_now()
    return save_config(cfg)


_MONITOR_WORKER_ENV = "DENG_MONITOR_BRIDGE_WORKER"
_MONITOR_WORKER_BOOTSTRAP_SECONDS = 8.0


def _monitor_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _monitor_worker_pid() -> int | None:
    try:
        raw = MONITOR_PID_PATH.read_text(encoding="utf-8").strip()
        pid = int(raw)
    except Exception:  # noqa: BLE001
        return None
    return pid if pid > 0 else None


def _monitor_worker_running() -> bool:
    pid = _monitor_worker_pid()
    return bool(pid and is_process_alive(pid))


def _cleanup_monitor_worker_files(*, keep_status: bool = False) -> None:
    for path in (MONITOR_PID_PATH, MONITOR_LOCK_PATH):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            pass
    if not keep_status:
        try:
            MONITOR_STATUS_PATH.unlink()
        except FileNotFoundError:
            pass
        except Exception:  # noqa: BLE001
            pass


def _monitor_bridge_launch_material(cfg: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(cfg, dict):
        return None
    lic = cfg.get("license") if isinstance(cfg.get("license"), dict) else {}
    raw_key = (lic.get("key") or "").strip() or str(cfg.get("license_key") or "").strip()
    if not raw_key:
        return None
    try:
        key = normalize_license_key(raw_key)
    except Exception:  # noqa: BLE001
        key = str(raw_key).strip().upper()
    if not key:
        return None
    try:
        install_id = get_or_create_install_id()
    except Exception:  # noqa: BLE001
        return None
    if not install_id:
        return None
    label = str(lic.get("device_label") or "").strip()
    if not label or label.lower() in {"termux on android", "localhost", "unknown"}:
        label = (get_public_device_model() or "").strip()
    if not label or label.lower() == "unknown":
        label = "Android device"
    channel = "stable"
    try:
        ch_raw = str(cfg.get("channel") or "").strip().lower()
        if ch_raw in {"stable", "beta", "dev", "latest", "test", "main-dev"}:
            channel = ch_raw
    except Exception:  # noqa: BLE001
        pass
    return {
        "license_key": key,
        "install_id_hash": hash_install_id(install_id),
        "channel": channel,
        "device_label": label[:64],
    }


def _ensure_monitor_bridge_for_config(cfg: dict[str, Any]) -> bool:
    try:
        from . import monitor_autostart
    except Exception:  # noqa: BLE001
        return False
    material = _monitor_bridge_launch_material(cfg)
    if not material:
        return False
    try:
        monitor_autostart.set_config(cfg)
    except Exception:  # noqa: BLE001
        pass
    try:
        return monitor_autostart.ensure_monitor_bridge_started(
            license_key=material["license_key"],
            install_id_hash=material["install_id_hash"],
            tool_version=VERSION,
            channel=material["channel"],
            device_label=material["device_label"],
            config=cfg,
            announce=False,
        )
    except Exception:  # noqa: BLE001
        return False


def _monitor_status_from_disk() -> dict[str, Any]:
    status = _read_json_file(MONITOR_STATUS_PATH)
    pid = _monitor_worker_pid()
    alive = bool(pid and is_process_alive(pid))
    status["status_file_present"] = MONITOR_STATUS_PATH.is_file()
    if pid:
        status["worker_pid"] = pid
    status["worker_running"] = alive
    if not alive:
        status["bridge_running"] = False
        status["connected"] = False
    return status


def _format_monitor_ram(device_ram: dict[str, Any] | None) -> str:
    if not isinstance(device_ram, dict):
        return "—"
    try:
        available = int(device_ram.get("available_mb") or 0)
        total = int(device_ram.get("total_mb") or 0)
        percent = int(device_ram.get("percent") or 0)
    except Exception:  # noqa: BLE001
        return "—"
    if total <= 0:
        return "—"
    return f"{available:,} MB / {total:,} MB {percent}%"


def _wait_for_monitor_status(*, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    latest = _monitor_status_from_disk()
    while time.time() < deadline:
        latest = _monitor_status_from_disk()
        if latest.get("last_push_result") or latest.get("last_error"):
            return latest
        time.sleep(0.25)
    return latest


def _resolve_monitor_bridge_auth(cfg: dict[str, Any], *, refresh: bool = False) -> dict[str, str] | None:
    from . import monitor_autostart

    material = _monitor_bridge_launch_material(cfg)
    if not material:
        return None
    bridge_url = monitor_autostart._resolve_bridge_url(None).rstrip("/")
    token = "" if refresh else monitor_autostart._load_cached_token_for_url(bridge_url)
    device_id = ""
    bridge_cache_path = getattr(monitor_autostart, "BRIDGE_CACHE_PATH", None)
    if bridge_cache_path is not None and bridge_cache_path.exists() and not refresh:
        cached = _read_json_file(bridge_cache_path)
        device_id = str(cached.get("device_id") or "")
    if not token:
        issued = monitor_autostart._issue_token_from_license(
            bridge_url=bridge_url,
            license_key=material["license_key"],
            install_id_hash=material["install_id_hash"],
            device_label=material["device_label"],
            tool_version=VERSION,
            channel=material["channel"],
        )
        if not issued or not issued.get("bridge_token"):
            return None
        token = str(issued.get("bridge_token") or "")
        device_id = str(issued.get("device_id") or "")
        monitor_autostart._save_cached_token({
            "bridge_url": bridge_url,
            "bridge_token": token,
            "device_id": device_id,
            "expires_at": issued.get("expires_at"),
            "expires_at_epoch": monitor_autostart._iso_to_epoch(issued.get("expires_at")),
            "issued_at_epoch": time.time(),
        })
    return {
        "bridge_url": bridge_url,
        "bridge_token": token,
        "device_id": device_id,
    }


def _write_cli_crash_log(exc: BaseException, *, context: str = "cli") -> None:
    try:
        CRASH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CRASH_LOG_PATH.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"\n[{_monitor_now_iso()}] {context}: {exc.__class__.__name__}: {exc}\n")
            traceback.print_exc(file=fh)
    except Exception:  # noqa: BLE001
        pass


def _record_last_command(command: str) -> None:
    """Persist the command before dispatch so a later probe names the failure."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "last-command.json").write_text(
            json.dumps({"command": command, "at": _monitor_now_iso()}), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        pass


def _report_start_dashboard_crash(
    exc: BaseException,
    *,
    session: StartSessionLogger | None = None,
) -> int:
    """Persist and print a dashboard crash instead of silently exiting Termux."""
    _write_cli_crash_log(exc, context="start_dashboard_monitor_loop")
    try:
        if session is not None:
            session.mark("dashboard_fatal_crash", error=str(exc)[:200])
    except Exception:  # noqa: BLE001
        pass
    for line in (
        "",
        f"[FATAL ERROR] Dashboard crashed: {exc}",
        f"Traceback saved to {CRASH_LOG_PATH}.",
        "Tool paused to prevent silent exit.",
    ):
        try:
            print(termux_ui.fit_line(line))
        except Exception:  # noqa: BLE001
            print(line)
    if _is_interactive():
        try:
            input("Press Enter to exit...")
        except (EOFError, KeyboardInterrupt):
            pass
    return 1


def _spawn_monitor_worker(cfg: dict[str, Any]) -> bool:
    if _monitor_worker_running():
        return True
    if not _monitor_bridge_launch_material(cfg):
        return False
    _cleanup_monitor_worker_files(keep_status=True)
    MONITOR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    script_path = Path(__file__).with_name("deng_tool_rejoin.py")
    env = os.environ.copy()
    env[_MONITOR_WORKER_ENV] = "1"
    env.setdefault("PYTHONUNBUFFERED", "1")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    # Serialize the interpreter spawn through the global subprocess lock so it
    # can never fork() concurrently with the watchdog daemon / logcat reader
    # threads (probe p-3daeae4cbd).  Combined with the entrypoint's vfork
    # disable, this keeps the heavy ``exec`` of a new Python on the safe path.
    with android.subprocess_lock():
        with MONITOR_LOG_PATH.open("ab") as log_file:
            proc = subprocess.Popen(
                [sys.executable, str(script_path), "monitor", "run-worker"],
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                cwd=str(APP_HOME),
                env=env,
                start_new_session=(os.name != "nt"),
                creationflags=creationflags,
                shell=False,
            )
    deadline = time.time() + _MONITOR_WORKER_BOOTSTRAP_SECONDS
    while time.time() < deadline:
        if _monitor_worker_running():
            return True
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    return _monitor_worker_running()


def _stop_monitor_worker(*, timeout: float = 10.0) -> tuple[bool, str]:
    pid = _monitor_worker_pid()
    if not pid:
        _cleanup_monitor_worker_files(keep_status=True)
        return False, "monitor bridge is not running"
    if not is_process_alive(pid):
        _cleanup_monitor_worker_files(keep_status=True)
        return False, f"stale monitor bridge PID {pid} cleaned"
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=max(3, int(timeout)),
                check=False,
                shell=False,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as exc:  # noqa: BLE001
        return False, f"failed to signal monitor bridge PID {pid}: {exc.__class__.__name__}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_process_alive(pid):
            _cleanup_monitor_worker_files(keep_status=True)
            return True, f"stopped monitor bridge PID {pid}"
        time.sleep(0.2)
    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    if not is_process_alive(pid):
        _cleanup_monitor_worker_files(keep_status=True)
        return True, f"stopped monitor bridge PID {pid}"
    return False, f"monitor bridge PID {pid} did not exit in time"


def _try_autostart_monitor_bridge(cfg: dict[str, Any]) -> bool:
    """Best-effort: ensure the persistent APK monitor bridge worker exists."""
    try:
        if os.environ.get(_MONITOR_WORKER_ENV) == "1":
            return _ensure_monitor_bridge_for_config(cfg)
        return _spawn_monitor_worker(cfg)
    except Exception:  # noqa: BLE001
        return False


# ── License cache fast-path ───────────────────────────────────────────────────
#
# Real-device evidence (probe ``p-39924732cd``): on the cloud phone, the
# remote license check via ``safe_http → curl`` crashes the calling
# Python process with ``SIGSEGV`` *intermittently* (faulthandler's crash
# log is empty, suggesting the segfault is in libc fork/exec or a native
# extension that bypasses the Python signal trampoline).  The user has
# a valid license that was confirmed ``active`` the day before, so making
# them retry the network call on every menu open is both unnecessary AND
# the exact code path that kills the process.
#
# Trust the cached ``last_status`` for a bounded window (24 h) when it's
# ``active``, so the menu opens immediately and the user can run Start
# without ever touching the segfaulting network code.

_LICENSE_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours
# Offline grace: if the cached check was ``active`` within this window AND
# the live remote check fails *transiently* (server_unavailable / crash),
# treat the license as active so the user is never locked out by a single
# bad request.  Reset to ``not_configured`` only on definitive answers
# (wrong_device / not_found / revoked / expired / inactive).
_LICENSE_OFFLINE_GRACE_SECONDS = 30 * 24 * 60 * 60  # 30 days

_LICENSE_TRANSIENT_RESULTS = {
    "server_unavailable",
    "check_timeout",
    "error",
    "",  # parser couldn't determine anything
}


def _license_subprocess_timeout_seconds() -> int:
    raw = (os.environ.get("DENG_LICENSE_SUBPROCESS_TIMEOUT") or "55").strip()
    try:
        return max(15, min(120, int(raw)))
    except ValueError:
        return 55


def _license_server_host_for_log(cfg: dict[str, Any]) -> str:
    lic = cfg.setdefault("license", {})
    srv = (lic.get("server_url") or "").strip()
    if not srv:
        try:
            from . import api_config as _api_cfg  # noqa: PLC0415

            srv = _api_cfg.license_server_url()
        except Exception:  # noqa: BLE001
            return "unknown"
    try:
        from urllib.parse import urlparse  # noqa: PLC0415

        parsed = urlparse(srv)
        return parsed.netloc or parsed.path or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def _log_license_subprocess_timeout(cfg: dict[str, Any], *, timeout: int, op: str) -> None:
    import logging as _logging  # noqa: PLC0415

    _logging.getLogger("deng.rejoin").warning(
        "license %s subprocess timed out after %ds host=%s",
        op,
        timeout,
        _license_server_host_for_log(cfg),
    )


def _normalize_license_check_result(result: str, message: str) -> tuple[str, str]:
    """Map legacy timeout strings to structured ``check_timeout``."""
    norm = str(result or "").strip().lower()
    msg = str(message or "").strip()
    if norm == "server_unavailable" and "timed out" in msg.lower():
        return "check_timeout", LICENSE_CHECK_TIMEOUT_USER_MESSAGE
    return norm, msg


def _license_failure_user_message(result: str, msg: str) -> str:
    norm = str(result or "").strip().lower()
    text = str(msg or "").strip()
    if norm in ("wrong_device", "requires_manual_rebind"):
        return WRONG_DEVICE_USER_MESSAGE
    if norm == "check_timeout":
        return LICENSE_CHECK_TIMEOUT_USER_MESSAGE
    if norm == "expired":
        return text or "This license key has expired."
    if norm in ("not_found", "invalid", "inactive", "revoked"):
        return text or "License key is not valid."
    if norm == "key_not_redeemed":
        return text or "This key has not been activated."
    if norm in _LICENSE_TRANSIENT_RESULTS:
        return text or "Could not reach the license server. Check your connection."
    if norm == "missing_key":
        return "No license key found."
    return text or f"License check failed ({norm or 'unknown'})."


def _license_cache_age_seconds(lic: dict[str, Any]) -> float | None:
    """Return seconds since ``last_check_at`` or ``None`` when unparseable."""
    if not isinstance(lic, dict):
        return None
    raw = lic.get("last_check_at")
    if not raw or not isinstance(raw, str):
        return None
    try:
        norm = raw.strip().replace("Z", "+00:00")
        from datetime import datetime as _dt, timezone as _tz  # noqa: PLC0415
        ts = _dt.fromisoformat(norm)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_tz.utc)
        return (_dt.now(_tz.utc) - ts).total_seconds()
    except (TypeError, ValueError, OSError):
        return None


def _license_cache_is_fresh_active(lic: dict[str, Any]) -> bool:
    """Return True when the cached license check is recent AND active.

    Cached check is considered fresh when:
        * ``last_status == 'active'``, AND
        * ``last_check_at`` parses as an ISO-8601 timestamp, AND
        * the timestamp is within :data:`_LICENSE_CACHE_TTL_SECONDS`.

    Failures during parsing return False so we fall back to remote check.
    Never raises.
    """
    if not isinstance(lic, dict):
        return False
    status = str(lic.get("last_status") or "").strip().lower()
    if status != "active":
        return False
    age = _license_cache_age_seconds(lic)
    if age is None:
        return False
    return 0 <= age <= _LICENSE_CACHE_TTL_SECONDS


def _license_should_offline_grace(lic: dict[str, Any]) -> bool:
    """Return True when a transient remote failure can fall back to cache.

    Only triggers when the most recent SUCCESSFUL answer was ``active``
    and is within :data:`_LICENSE_OFFLINE_GRACE_SECONDS`.  Treating a
    transient ``server_unavailable`` (or subprocess SIGSEGV) as ``active``
    in that window prevents the public user from being locked out by
    a single bad network request.
    """
    status = str((lic or {}).get("last_status") or "").strip().lower()
    if status != "active":
        return False
    age = _license_cache_age_seconds(lic)
    if age is None:
        return False
    return 0 <= age <= _LICENSE_OFFLINE_GRACE_SECONDS


def _clear_cached_license_key(cfg: dict[str, Any]) -> dict[str, Any]:
    """Remove the locally cached license key after a definitive server rejection."""
    global _license_session_validated
    _license_session_validated = False
    lic = cfg.setdefault("license", {})
    lic["key"] = ""
    cfg["license_key"] = ""
    lic.pop("last_status", None)
    lic.pop("last_check_at", None)
    try:
        from .license_session import clear_session  # noqa: PLC0415

        clear_session()
    except Exception:  # noqa: BLE001
        pass
    try:
        return save_config(cfg)
    except Exception:  # noqa: BLE001
        return cfg


def _load_license_key_from_cfg(cfg: dict[str, Any]) -> str:
    lic = cfg.setdefault("license", {})
    return (str(lic.get("key") or "").strip() or str(cfg.get("license_key") or "").strip())


def _set_license_key_in_memory(cfg: dict[str, Any], key: str) -> None:
    lic = cfg.setdefault("license", {})
    lic["key"] = key
    cfg["license_key"] = key


def _license_gate_user_exit() -> bool:
    """User chose Exit from the license gate — avoid Termux teardown segfault."""
    _termux_exit_clean()
    return False


def _print_license_gate_retry_menu() -> None:
    print("\n1. Enter Different Key\n0. Exit", flush=True)


def _print_license_gate_input_unavailable(use_color: bool) -> None:
    _print_license_err(safe_io.LICENSE_GATE_INPUT_UNAVAILABLE_MSG, use_color)


def _prompt_license_gate_choice() -> str | None:
    """Read menu choice; block until user types 0/1. None = Ctrl-C only."""
    safe_io.restore_terminal()
    while True:
        try:
            choice_raw = safe_io.read_interactive_line("Choose [1/0]: ", allow_blank=True)
        except safe_io.InteractiveInputUnavailable:
            raise
        if choice_raw is None:
            return None
        choice = choice_raw.strip()
        if not choice:
            continue
        if choice in ("0", "1", "2"):
            return choice


def _prompt_fresh_license_key() -> str | None:
    safe_io.restore_terminal()
    try:
        raw = safe_io.read_interactive_line("Enter license key: ", allow_blank=True)
    except safe_io.InteractiveInputUnavailable:
        raise
    if raw is None:
        return None
    return raw.strip()


def _handle_license_gate_failure(
    cfg: dict[str, Any],
    result: str,
    msg: str,
    use_color: bool,
) -> tuple[dict[str, Any], str]:
    """Clear rejected key/session, show failure + numbered menu.

    Returns ``(cfg, action)`` where *action* is one of:
    ``"retry"`` (enter different key), ``"exit"`` (user chose 0),
    ``"unavailable"`` (no TTY), ``"cancel"`` (Ctrl-C).
    """
    cfg = _clear_cached_license_key(cfg)
    _print_license_err(_license_failure_user_message(result, msg), use_color)
    while True:
        _print_license_gate_retry_menu()
        try:
            choice = _prompt_license_gate_choice()
        except safe_io.InteractiveInputUnavailable:
            _print_license_gate_input_unavailable(use_color)
            return cfg, "unavailable"
        if choice is None:
            return cfg, "cancel"
        if choice in ("0", "2"):
            return cfg, "exit"
        if choice == "1":
            return cfg, "retry"


def _run_license_isolated_subprocess(
    cfg: dict[str, Any],
    payload: dict[str, Any],
    *,
    timeout: int,
    op: str,
) -> tuple[str, str]:
    """Run license check/bind in a child process with timeout kill + logging."""
    import subprocess as _sp  # noqa: PLC0415

    try:
        from pathlib import Path as _Path  # noqa: PLC0415

        _agent_parent = str(_Path(__file__).resolve().parent.parent)
    except Exception:  # noqa: BLE001
        _agent_parent = os.path.expanduser("~/.deng-tool/rejoin")
    code = (
        "import json, os, sys\n"
        f"sys.path.insert(0, {_agent_parent!r})\n"
        "_home = os.environ.get('DENG_REJOIN_HOME')\n"
        "if _home and _home not in sys.path:\n"
        "    sys.path.insert(0, _home)\n"
        "try:\n"
        "    from agent.license import (\n"
        "        check_remote_license_status, bind_remote_license_key,\n"
        "        hash_install_id, DEFAULT_LICENSE_SERVER_URL, get_public_device_model,\n"
        "    )\n"
        "    from agent.constants import VERSION\n"
        "except Exception as _imp_exc:\n"
        "    sys.stdout.write(json.dumps({\n"
        "        'result': 'server_unavailable',\n"
        "        'message': f'license import error: {_imp_exc}',\n"
        "    }))\n"
        "    sys.exit(0)\n"
        "p = json.loads(sys.stdin.read())\n"
        "srv = p['server_url'] or DEFAULT_LICENSE_SERVER_URL\n"
        "try:\n"
        "    model = get_public_device_model() or 'unknown'\n"
        "except Exception:\n"
        "    model = 'unknown'\n"
        "try:\n"
        "    op = p.get('op') or 'check'\n"
        "    if op == 'bind':\n"
        "        r, m = bind_remote_license_key(\n"
        "            srv, license_key=p['key'], install_id=p['install_id'],\n"
        "            device_model=model, app_version=VERSION,\n"
        "            device_label=p['device_label'],\n"
        "        )\n"
        "    else:\n"
        "        r, m = check_remote_license_status(\n"
        "            srv, license_key=p['key'], install_id=p['install_id'],\n"
        "            device_model=model, app_version=VERSION,\n"
        "            device_label=p['device_label'],\n"
        "        )\n"
        "except Exception as exc:\n"
        "    r, m = 'server_unavailable', f'{op} exception: {exc}'\n"
        "sys.stdout.write(json.dumps({'result': r, 'message': m}))\n"
    )
    env = dict(os.environ)
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        _agent_parent + (os.pathsep + prev_pp if prev_pp else "")
    )
    try:
        proc = _sp.Popen(
            [sys.executable, "-c", code],
            stdin=_sp.PIPE,
            stdout=_sp.PIPE,
            stderr=_sp.PIPE,
            env=env,
        )
        try:
            stdout, stderr = proc.communicate(
                input=json.dumps(payload).encode("utf-8"),
                timeout=timeout,
            )
        except _sp.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            _log_license_subprocess_timeout(cfg, timeout=timeout, op=op)
            return "check_timeout", LICENSE_CHECK_TIMEOUT_USER_MESSAGE
    except OSError as exc:
        return "server_unavailable", f"License {op} subprocess launch error: {exc}"

    if proc.returncode < 0:
        return "server_unavailable", f"License {op} crashed safely (signal {-proc.returncode})."
    if proc.returncode != 0:
        stderr_line = (stderr or b"").decode("utf-8", errors="replace").splitlines()
        hint = stderr_line[0][:80] if stderr_line else ""
        return "server_unavailable", f"License {op} exited rc={proc.returncode} ({hint})"
    try:
        data = json.loads((stdout or b"").decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return "server_unavailable", f"License {op} returned invalid JSON."
    result = str(data.get("result") or "server_unavailable").strip().lower()
    message = str(data.get("message") or "").strip()
    return _normalize_license_check_result(result, message)


def _run_start_batch_cache_clear(
    packages: list[str],
    *,
    root_info: android.RootInfo | None = None,
) -> dict[str, str]:
    """Start prep phase 1: mass cache clear for every selected package."""
    from .cache_clear_phases import run_start_mass_cache_clear

    return run_start_mass_cache_clear(packages, root_info=root_info)


def _remote_license_isolated(
    cfg: dict[str, Any],
    *,
    op: str = "check",
    timeout: int | None = None,
) -> tuple[str, str]:
    timeout = _license_subprocess_timeout_seconds() if timeout is None else timeout
    payload = {
        "key": (cfg.setdefault("license", {}).get("key") or "").strip(),
        "install_id": (cfg.setdefault("license", {}).get("install_id") or "").strip(),
        "device_label": str(cfg.setdefault("license", {}).get("device_label") or "")[:80],
        "server_url": (cfg.setdefault("license", {}).get("server_url") or "").strip(),
    }
    if op != "check":
        payload["op"] = op
    return _run_license_isolated_subprocess(cfg, payload, timeout=timeout, op=op)


def _remote_license_check_isolated(cfg: dict[str, Any], *, timeout: int | None = None) -> tuple[str, str]:
    """Run the remote license check inside a child Python process.

    Real-device cause: on Termux/Python 3.13.13, the network code path in
    :func:`check_remote_license_status` segfaults the *parent* process
    (probe ``p-39924732cd`` showed ``last_step == 'license_remote_check'``
    with rc = -11).  Doing the call in a short-lived child process means
    the worst case is a clean transient result — the menu never crashes.
    """
    return _remote_license_isolated(cfg, op="check", timeout=timeout)


def _ensure_install_id_saved(cfg: dict[str, Any]) -> dict[str, Any]:
    lic = cfg.setdefault("license", {})
    before = lic.get("install_id")
    sync_install_id_with_config(lic)
    if lic.get("install_id") != before:
        return save_config(cfg)
    return cfg


def _remote_license_check_direct(cfg: dict[str, Any]) -> tuple[str, str]:
    """Run validate-only ``/api/license/check`` in-process (legacy path).

    On Termux/Python 3.13.13 this code path can segfault the *parent*
    process (probe ``p-39924732cd`` showed ``rc=-11`` here).  Prefer
    :func:`_remote_license_run_check`, which dispatches to subprocess
    isolation in production.
    """
    lic = cfg.setdefault("license", {})
    key = (lic.get("key") or "").strip()
    if not key:
        return "missing_key", "No license key configured."
    install_id = sync_install_id_with_config(lic)
    device = get_device_summary()
    srv = (lic.get("server_url") or "").strip()
    if not srv:
        from . import api_config as _api_cfg
        srv = _api_cfg.license_server_url()
    return check_remote_license_status(
        srv,
        license_key=key,
        install_id=install_id,
        device_model=device.get("model") or "unknown",
        app_version=VERSION,
        device_label=str(lic.get("device_label") or ""),
    )


def _remote_license_bind_direct(cfg: dict[str, Any]) -> tuple[str, str]:
    """Run explicit ``POST /api/license/bind`` after manual key entry."""
    lic = cfg.setdefault("license", {})
    key = (lic.get("key") or "").strip()
    if not key:
        return "missing_key", "No license key configured."
    install_id = sync_install_id_with_config(lic)
    device = get_device_summary()
    srv = (lic.get("server_url") or "").strip()
    if not srv:
        from . import api_config as _api_cfg
        srv = _api_cfg.license_server_url()
    return bind_remote_license_key(
        srv,
        license_key=key,
        install_id=install_id,
        device_model=device.get("model") or "unknown",
        app_version=VERSION,
        device_label=str(lic.get("device_label") or ""),
    )


def _remote_license_bind_isolated(cfg: dict[str, Any], *, timeout: int | None = None) -> tuple[str, str]:
    """Run manual bind inside a child process (same SIGSEGV isolation as check)."""
    return _remote_license_isolated(cfg, op="bind", timeout=timeout)


def _should_isolate_license_check() -> bool:
    """Decide whether to run the license check in a child subprocess.

    Real-device evidence (probe ``p-39924732cd``): on Termux the
    in-process network code segfaults the menu intermittently.  We
    isolate by default on Termux and on Android in general, and let
    a power user override via ``DENG_LICENSE_INLINE=1``.

    Returns False during ``unittest`` so existing tests that mock
    :func:`_remote_license_run_check` continue to work without ever
    spawning subprocesses (subprocess can't see test mocks).
    """
    override = (os.environ.get("DENG_LICENSE_INLINE") or "").strip().lower()
    if override in ("1", "true", "yes", "on"):
        return False
    # Tests mock this function and don't want subprocess isolation —
    # otherwise the child wouldn't see the mock and would do real HTTP.
    # Detect by the unittest module being imported AND a pytest/unittest
    # runner currently in sys.argv.
    if "unittest" in sys.modules and any(
        "unittest" in a or "pytest" in a or "_test_runner" in a for a in sys.argv[:2]
    ):
        return False
    # Default: isolate when we can see we're on Termux/Android (the
    # cloud-phone environment where the segfault was observed).  Be
    # conservative on dev machines so behavior matches the old code.
    if os.environ.get("TERMUX_VERSION"):
        return True
    if os.environ.get("ANDROID_ROOT") or os.environ.get("ANDROID_DATA"):
        return True
    return False


def _remote_license_run_check(cfg: dict[str, Any]) -> tuple[str, str]:
    """Validate-only ``/api/license/check`` — never binds or rebinds."""
    if _should_isolate_license_check():
        result, msg = _remote_license_check_isolated(cfg)
    else:
        result, msg = _remote_license_check_direct(cfg)
    return _normalize_license_check_result(result, msg)


def _remote_license_run_bind(cfg: dict[str, Any]) -> tuple[str, str]:
    """Explicit manual bind via ``POST /api/license/bind`` only."""
    if _should_isolate_license_check():
        result, msg = _remote_license_bind_isolated(cfg)
    else:
        result, msg = _remote_license_bind_direct(cfg)
    return _normalize_license_check_result(result, msg)


def verify_remote_license_noninteractive(cfg: dict[str, Any], *, use_color: bool) -> bool:
    """Return True when remote license check is ``active`` (updates ``last_status``).

    Quiet by design: valid licenses produce NO public output so the clean
    logo + menu appears without "License OK" spam on every startup.
    """
    global _license_session_validated
    result, msg = _remote_license_run_check(cfg)
    if result == "active":
        # Silent success — do not print "License OK" on every startup.
        _license_session_validated = True
        _persist_license_status(cfg, "active")
        return True
    # Cache integrity: see _ensure_remote_license_menu_loop for rationale.
    # Never persist transient results, they would clobber a valid cached
    # ``last_status == "active"`` and lock the user out.
    if result not in _LICENSE_TRANSIENT_RESULTS:
        try:
            _persist_license_status(cfg, result)
        except Exception:  # noqa: BLE001
            pass
    if result == "wrong_device":
        _print_license_err(WRONG_DEVICE_USER_MESSAGE, use_color)
    elif result == "requires_manual_rebind":
        _print_license_err(HWID_RESET_REENTRY_MESSAGE, use_color)
        try:
            _clear_cached_license_key(cfg)
        except Exception:  # noqa: BLE001
            pass
    elif result == "key_not_redeemed":
        _print_license_err(msg, use_color)
    elif result == "missing_key":
        _print_license_err("No License Key Found", use_color)
    else:
        _print_license_err(f"License Invalid: {msg}", use_color)
    print_beginner_license_gate_help(
        show_hwid_footer=(result not in ("wrong_device", "key_not_redeemed"))
    )
    return False


def _license_gate_after_prompt(raw: str | None) -> str:
    """Classify a fresh-key prompt result for the license gate loop."""
    if raw is None:
        return "cancel"
    if not raw:
        return "blank"
    return "ok"


def _license_gate_finish_action(action: str) -> bool:
    """Apply terminal action from the license gate; return loop success flag."""
    if action == "exit":
        _license_gate_user_exit()
        return False
    return False


def _ensure_local_license_menu_loop(cfg: dict[str, Any], args: argparse.Namespace, use_color: bool) -> bool:
    force_fresh_prompt = False
    while True:
        try:
            cfg = load_config()
        except ConfigError as exc:
            _print_license_err(str(exc), use_color)
            return False
        lic = cfg.setdefault("license", {})
        key = _load_license_key_from_cfg(cfg)
        if force_fresh_prompt:
            force_fresh_prompt = False
            cfg = _clear_cached_license_key(cfg)
            try:
                raw = _prompt_fresh_license_key()
            except safe_io.InteractiveInputUnavailable:
                _print_license_gate_input_unavailable(use_color)
                return False
            prompt_action = _license_gate_after_prompt(raw)
            if prompt_action == "cancel":
                return _license_gate_finish_action("exit")
            if prompt_action == "blank":
                continue
            try:
                key = validate_license_key(raw)
            except ConfigError as exc:
                _print_license_err(str(exc), use_color)
                continue
            _set_license_key_in_memory(cfg, key)
        elif not key:
            if not _is_interactive():
                _print_license_err("No License Key Found", use_color)
                print_beginner_license_gate_help()
                return False
            print_beginner_menu_license_prompt()
            if not keystore.prompt_and_verify_key():
                return _license_gate_user_exit()
            global _license_manual_verification_success
            _license_manual_verification_success = True
            return True
        ok, msg = keystore.verify_key(key)
        if ok:
            try:
                cfg = save_config(cfg)
            except Exception:  # noqa: BLE001
                pass
            return True
        if not _is_interactive():
            _print_license_err(msg, use_color)
            return False
        cfg, gate_action = _handle_license_gate_failure(cfg, "invalid", msg, use_color)
        if gate_action == "retry":
            force_fresh_prompt = True
            continue
        return _license_gate_finish_action(gate_action)


def _ensure_remote_license_menu_loop(cfg: dict[str, Any], args: argparse.Namespace, use_color: bool) -> bool:
    """Gate the menu behind a remote license check. Allows bounded retries without recursion."""
    _MAX_RETRIES = 10
    attempt = 0
    force_fresh_prompt = False
    while attempt < _MAX_RETRIES:
        attempt += 1
        manual_key_entry = False
        try:
            cfg = load_config()
        except ConfigError as exc:
            _print_license_err(str(exc), use_color)
            return False

        try:
            cfg = _ensure_install_id_saved(cfg)
        except Exception:  # noqa: BLE001
            pass

        lic = cfg.setdefault("license", {})
        key = _load_license_key_from_cfg(cfg)

        if force_fresh_prompt:
            force_fresh_prompt = False
            cfg = _clear_cached_license_key(cfg)
            try:
                raw = _prompt_fresh_license_key()
            except safe_io.InteractiveInputUnavailable:
                _print_license_gate_input_unavailable(use_color)
                return False
            prompt_action = _license_gate_after_prompt(raw)
            if prompt_action == "cancel":
                return _license_gate_finish_action("exit")
            if prompt_action == "blank":
                continue
            try:
                norm = validate_license_key(raw)
            except ConfigError as exc:
                _print_license_err(str(exc), use_color)
                continue
            _set_license_key_in_memory(cfg, norm)
            key = norm
            manual_key_entry = True
        elif not key:
            if not _is_interactive():
                _print_license_err("No License Key Found", use_color)
                print_beginner_license_gate_help()
                return False
            _print_missing_license_prompt(use_color)
            try:
                raw = _prompt_fresh_license_key()
            except safe_io.InteractiveInputUnavailable:
                _print_license_gate_input_unavailable(use_color)
                return False
            prompt_action = _license_gate_after_prompt(raw)
            if prompt_action == "cancel":
                return _license_gate_finish_action("exit")
            if prompt_action == "blank":
                continue
            try:
                norm = validate_license_key(raw)
            except ConfigError as exc:
                _print_license_err(str(exc), use_color)
                continue
            _set_license_key_in_memory(cfg, norm)
            key = norm
            manual_key_entry = True

        try:
            if manual_key_entry:
                result, msg = _remote_license_run_bind(cfg)
            else:
                result, msg = _remote_license_run_check(cfg)
        except Exception as exc:  # noqa: BLE001
            _print_license_err(f"License check failed: {exc}", use_color)
            result, msg = "error", str(exc)
        result, msg = _normalize_license_check_result(result, msg)

        if result == "active":
            global _license_session_validated, _license_manual_verification_success
            _license_session_validated = True
            if manual_key_entry:
                _license_manual_verification_success = True
            try:
                cfg = save_config(cfg)
                _persist_license_status(cfg, "active")
            except Exception:  # noqa: BLE001
                pass
            return True

        # Offline grace: a transient failure (license check timed out / server
        # unavailable) must never lock out a user whose license was confirmed
        # active recently. This now applies on a COLD START too — previously it
        # ALSO required ``_license_session_validated`` (a successful check earlier
        # in THIS process run), which is always False right after launch, so a
        # returning active user who restarted the tool during a license-server /
        # Supabase outage was wrongly dropped to the failure menu. ``_license_
        # should_offline_grace`` already gates this safely: it only returns True
        # when the cached ``last_status`` is ``active`` and within the 30-day
        # grace window (transient failures never overwrite that cache; definitive
        # answers like revoked/expired do). We never grant grace for a freshly-
        # typed key (``manual_key_entry``): a brand-new key must be verified by
        # the server before we trust it.
        if (
            result in _LICENSE_TRANSIENT_RESULTS
            and not manual_key_entry
            and _license_should_offline_grace(lic)
        ):
            return True

        if result not in _LICENSE_TRANSIENT_RESULTS:
            try:
                cfg = _persist_license_status(cfg, result)
            except Exception:  # noqa: BLE001
                pass

        if not _is_interactive():
            if result not in _LICENSE_TRANSIENT_RESULTS:
                cfg = _clear_cached_license_key(cfg)
            _print_license_err(_license_failure_user_message(result, msg), use_color)
            return False

        cfg, gate_action = _handle_license_gate_failure(cfg, result, msg, use_color)
        if gate_action == "retry":
            force_fresh_prompt = True
            continue
        return _license_gate_finish_action(gate_action)
    return False


def ensure_menu_can_open(_args: argparse.Namespace) -> bool:
    """Gate checking is performed inside cmd_menu(); this function is kept for compatibility."""
    return True


def _is_interactive() -> bool:
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


def _prompt(text: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    result = safe_io.safe_prompt(f"{text}{suffix}: ", default=default or None)
    if result is None:  # EOF / Ctrl-C → treat as default
        print()
        return default
    return result or default


def _prompt_yes_no(text: str, default: bool = False) -> bool:
    marker = "Y/n" if default else "y/N"
    while True:
        result = safe_io.safe_prompt(f"{text} [{marker}]: ")
        if result is None:  # EOF / Ctrl-C
            print()
            return default
        value = result.strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def _prompt_int(text: str, default: int, minimum: int) -> int:
    while True:
        value = _prompt(text, str(default))
        try:
            number = int(value)
        except ValueError:
            print("Please enter a number.")
            continue
        if number < minimum:
            print(f"Value must be at least {minimum}.")
            continue
        return number


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def _yes_no(value: bool) -> str:
    return "Enabled" if value else "Disabled"


def _launch_mode_label(value: str) -> str:
    return {
        "app": "Open Roblox app only",
        "deeplink": "Roblox deep link",
        "web_url": "Roblox web/private-server URL",
    }.get(value, value)


def _screen_mode_label(value: Any) -> str:
    return {
        "landscape": "Landscape",
        "portrait": "Portrait",
    }[validate_screen_mode(value)]


def _has_configured_launch_link(config_data: dict[str, Any], entries: list[dict[str, Any]] | None = None) -> bool:
    rows = entries if entries is not None else list(config_data.get("roblox_packages") or [])
    if rows:
        for entry in rows:
            try:
                if str(effective_private_server_url(entry, config_data) or "").strip():
                    return True
            except Exception:  # noqa: BLE001
                continue
    return bool(str(config_data.get("launch_url") or config_data.get("private_server_url") or "").strip())


def _package_list_label(packages: list[Any]) -> str:
    if not packages:
        return "Not set"
    entries = validate_package_entries(packages)
    return ", ".join(package_display_name(entry) for entry in entries if entry.get("enabled", True)) or "Not set"


def _package_row_label(entry: dict[str, Any]) -> str:
    return package_display_name(entry, include_package=True)


def _account_username_value(entry: dict[str, Any]) -> str:
    return get_package_display_username(entry)


def _package_username_display(entry: dict[str, Any]) -> str:
    """Username for package menus / tables — root scan, never Unknown."""
    return get_package_display_username(entry)


def _package_username_display_for_config(entry: dict[str, Any], config_data: dict[str, Any]) -> str:
    return get_package_display_username(entry, config_data)


def _short_package_display(package: Any) -> str:
    """Short public table package display; never changes internal package IDs."""
    raw = str(package or "").strip()
    if not raw:
        return ""
    if "." in raw:
        tail = raw.rsplit(".", 1)[-1] or raw
        return f"..{tail}"
    if len(raw) > 14:
        return f"..{raw[-12:]}"
    return raw


def _hint_list_label(hints: list[str]) -> str:
    return ", ".join(hints) if hints else "Default"


def _safe_detection_hints(config_data: dict[str, Any] | None = None) -> list[str]:
    source = None
    if config_data:
        source = config_data.get("package_detection_hints")
    if source is None:
        try:
            source = load_config().get("package_detection_hints")
        except ConfigError:
            source = DEFAULT_ROBLOX_PACKAGE_HINTS
    try:
        return validate_package_detection_hints(source)
    except ConfigError:
        return list(DEFAULT_ROBLOX_PACKAGE_HINTS)


def _package_detection_options(config_data: dict[str, Any]) -> tuple[list[str], bool, bool]:
    pd = config_data.get("package_detection")
    if not isinstance(pd, dict):
        pd = {}
    hints_src = pd.get("hints")
    if hints_src in (None, "", []):
        hints_src = config_data.get("package_detection_hints")
    try:
        hints = validate_package_detection_hints(hints_src)
    except ConfigError:
        hints = list(DEFAULT_ROBLOX_PACKAGE_HINTS)
    return hints, bool(pd.get("include_launchable_only", True)), bool(pd.get("enabled", True))


def _gather_roblox_candidates_for_ui(config_data: dict[str, Any]) -> list[android.RobloxPackageCandidate]:
    hints2, inc_launch, det_en = _package_detection_options(config_data)
    candidates = android.discover_roblox_package_candidates(
        hints2,
        include_launchable_only=inc_launch,
        detection_enabled=det_en,
    )
    if not candidates:
        for pkg in android.find_roblox_packages(hints2):
            candidates.append(
                android.RobloxPackageCandidate(
                    package=pkg,
                    app_name=android.get_application_label(pkg) or pkg.rsplit(".", 1)[-1],
                    launchable=android.is_launchable_package(pkg),
                )
            )
    return candidates


def _print_full_discovery_table(candidates: list[android.RobloxPackageCandidate], config_data: dict[str, Any] | None = None) -> None:
    print()
    print("Detected Roblox Packages (root-first scan)")
    print(termux_ui.fit_line("# | Package | Username"))
    print(termux_ui.fit_line("-" * min(40, safe_io.terminal_columns())))
    for idx, c in enumerate(candidates, start=1):
        username = "unknown"
        reason = ""
        if config_data is not None:
            scan = package_username.scan_package_username(c.package, config_data)
            if scan.username:
                username = scan.username
            elif scan.reason:
                reason = scan.reason[:60]
        print(
            termux_ui.fit_line(f"[{idx}] {c.package} | username: {username}")
        )
        if reason:
            print(termux_ui.fit_line(f"     reason: {reason}"))


def _safe_package_detect_progress() -> None:
    termux_ui.print_warning("Detecting Packages...")
    termux_ui.print_warning("This May Take A Few Seconds.")


def _safe_reason_text(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    text = _ANSI_RE.sub("", text).replace("\r", " ").replace("\n", " ")
    return text[:90]


def _interactive_discover_package_entries(
    config_data: dict[str, Any],
    existing_entries: list[dict[str, Any]],
    *,
    exclude_packages: set[str] | frozenset[str] | None = None,
    config_for_detect: dict[str, Any] | None = None,
    candidates: list[android.RobloxPackageCandidate] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Full discovery table + multiselect (same path as first-time Roblox package step).

    Returns ``(entries, reason)`` where reason is ``ok``, ``no_candidates``, or ``empty_choice``.

    When ``candidates`` is passed (e.g. Add Package after pre-filter), discovery is not re-run.
    """
    if candidates is None:
        exclude = frozenset(exclude_packages or ())
        candidates = _gather_roblox_candidates_for_ui(config_data)
        candidates = [c for c in candidates if c.package not in exclude]
    if not candidates:
        return [], "no_candidates"
    if len(candidates) == 1:
        c0 = candidates[0]
        print(f"Auto-selected: {c0.package}")
        entry = _entry_for_package(c0.package, existing_entries, app_name=c0.app_name)
        return [entry], "ok"
    _print_full_discovery_table(candidates, config_data)
    print("  A. Select all")
    _raw = safe_io.safe_prompt("Choose packages (e.g. 1,2 or A) [1]: ", default="1")
    if _raw is None:
        return [], "empty_choice"
    raw = (_raw or "1").strip().lower()
    picked: list[android.RobloxPackageCandidate] = []
    if raw == "a":
        picked = list(candidates)
    else:
        seen: set[str] = set()
        for part in [p.strip() for p in raw.split(",") if p.strip()]:
            if part.isdigit():
                index = int(part)
                if 1 <= index <= len(candidates):
                    c = candidates[index - 1]
                    if c.package not in seen:
                        seen.add(c.package)
                        picked.append(c)
    if not picked:
        return [], "empty_choice"
    base_entries = [_entry_for_package(c.package, existing_entries, app_name=c.app_name) for c in picked]
    return base_entries, "ok"


def _safe_url_label(value: str | None) -> str:
    if not value:
        return "Not set"
    return safe_config_view({"launch_url": value}).get("launch_url") or "Not set"


def _refresh_detected_fields(config_data: dict[str, Any]) -> dict[str, Any]:
    config_data["root_available"] = android.detect_root().available
    config_data["android_release"] = get_android_release()
    config_data["android_sdk"] = get_android_sdk()
    config_data["download_dir"] = detect_public_download_dir()
    return config_data


def _enforce_termux_left_layout(config_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resize Termux to the left dock pane, silently, with probe logging.

    Bug 2 (probe ``p-52aeb6420f``): on the SM-N9810 the Termux dock-resize
    path called ``cmd activity set-task-windowing-mode 5`` and ``am stack
    resize`` against the Termux task while Termux was the user's active
    foreground session.  Even when individual commands returned non-zero
    (which they did on this device), the partial state changes manifested
    visually as Termux + other apps disappearing, black bars, and a
    portrait-shaped layout — the user perceived this as "Termux closed"
    and "device went home".

    Per user spec we now treat the dock-resize as **opt-in**:

      - default: SKIP entirely (no commands sent to Termux's task).
      - opt-in:  set ``termux_dock_enabled=True`` in config to restore the
        old behaviour for operators who explicitly want the dock layout.

    A ``[DENG_REJOIN_TERMUX_LAYOUT]`` event with ``success=skipped`` is
    still emitted so probes can confirm the inhibition is in effect.
    Termux is never force-stopped from this code path.
    """
    result: dict[str, Any] = {}
    try:
        from .logger import configure_logging, log_event

        cfg = config_data or {}
        dock_enabled = bool(cfg.get("termux_dock_enabled", False))
        if not dock_enabled:
            logger = configure_logging()
            log_event(
                logger,
                "info",
                "[DENG_REJOIN_TERMUX_LAYOUT]",
                screen_w=0,
                screen_h=0,
                termux_package="com.termux",
                desired_bounds="",
                actual_before="",
                actual_after="",
                success="skipped",
                method="opt-in disabled (termux_dock_enabled=false)",
                reason="bug2_protection_probe_p-52aeb6420f",
            )
            return {
                "ok": True,
                "skipped": True,
                "reason": "termux_dock_enabled is false (default)",
            }

        from . import termux_minimize as _tm

        frac = float(cfg.get("termux_dock_fraction", 0.50))
        frac = 0.50 if abs(frac - 0.50) > 0.001 else frac
        before = None
        try:
            before = _tm._read_back_termux_bounds()
        except Exception:  # noqa: BLE001
            before = None
        res = _tm.minimize_termux_to_dock(fraction=frac)
        result = res.as_dict()
        logger = configure_logging()
        screen_w = screen_h = 0
        if res.display:
            screen_w, screen_h = res.display
        log_event(
            logger,
            "info",
            "[DENG_REJOIN_TERMUX_LAYOUT]",
            screen_w=screen_w,
            screen_h=screen_h,
            termux_package="com.termux",
            desired_bounds=res.desired or "",
            actual_before=before or "",
            actual_after=res.actual or "",
            success=str(bool(res.ok)).lower(),
            method=res.method or res.reason or "",
        )
    except Exception as exc:  # noqa: BLE001
        try:
            from .logger import configure_logging, log_event
            log_event(
                configure_logging(),
                "info",
                "[DENG_REJOIN_TERMUX_LAYOUT]",
                screen_w=0,
                screen_h=0,
                termux_package="com.termux",
                desired_bounds="",
                actual_before="",
                actual_after="",
                success="false",
                method=f"error: {exc}",
            )
        except Exception:  # noqa: BLE001
            pass
        result = {"ok": False, "skipped": True, "reason": str(exc)[:160]}
    return result


def _enforce_configured_screen_mode(
    config_data: dict[str, Any] | None = None,
    protected_packages: list[str] | None = None,
    phase: str = "before_start",
) -> dict[str, Any]:
    """Detect or honor configured screen mode, then lock orientation before layout."""
    result: dict[str, Any] = {}
    try:
        from .logger import configure_logging, log_event
        from .resize_mode import resolve_runtime_screen_mode

        cfg = config_data or {}
        mode, mode_info = resolve_runtime_screen_mode(
            configured=str(cfg.get("screen_mode") or "auto"),
            previous_mode=cfg.get("last_resize_mode"),
        )
        cfg["screen_mode"] = mode
        landscape_state = android.enforce_landscape_home_state(
            phase=phase,
            screen_mode_config=mode,
        )
        protected = list(protected_packages or [])
        if not protected:
            try:
                protected = enabled_package_names(validate_config(cfg))
            except Exception:  # noqa: BLE001
                protected = []
        result = android.enforce_screen_orientation(mode, protected_packages=protected)
        result["resize_mode"] = mode_info
        log_event(
            configure_logging(),
            "info",
            "[DENG_REJOIN_ORIENTATION_ENFORCE]",
            requested=result.get("requested", mode),
            actual_before=result.get("actual_before", "unknown"),
            actual_after=result.get("actual_after", "unknown"),
            root_available=str(bool(result.get("root_available"))).lower(),
            success=str(bool(result.get("success"))).lower(),
            override_detected=str(bool(result.get("override_detected"))).lower(),
            override_package=result.get("override_package", ""),
            override_action=result.get("override_action", "none"),
            error=result.get("error", ""),
        )
        log_event(
            configure_logging(),
            "info",
                "[DENG_REJOIN_LANDSCAPE_STATE]",
                phase=str(landscape_state.get("phase", phase)),
                wm_size=json.dumps(landscape_state.get("wm_size", {}), sort_keys=True),
                wm_density=json.dumps(landscape_state.get("wm_density", {}), sort_keys=True),
            user_rotation=landscape_state.get("user_rotation", ""),
            accelerometer_rotation=landscape_state.get("accelerometer_rotation", ""),
                display_rect=json.dumps(landscape_state.get("display_rect", {}), sort_keys=True),
            final_layout_mode=landscape_state.get("final_layout_mode", mode),
            screen_mode_config=landscape_state.get("screen_mode_config", mode),
                correction_applied=json.dumps(landscape_state.get("correction_applied", []), sort_keys=True),
                launcher_bounds=json.dumps(landscape_state.get("launcher_bounds", {}), sort_keys=True),
            black_bar_suspected=landscape_state.get("black_bar_suspected", "no"),
        )
        result["landscape_state"] = landscape_state
    except Exception as exc:  # noqa: BLE001
        try:
            from .logger import configure_logging, log_event
            log_event(
                configure_logging(),
                "info",
                "[DENG_REJOIN_ORIENTATION_ENFORCE]",
                requested=mode,
                actual_before="unknown",
                actual_after="unknown",
                root_available="false",
                success="false",
                override_detected="false",
                override_package="",
                override_action="none",
                error=str(exc)[:160],
            )
        except Exception:  # noqa: BLE001
            pass
        result = {"success": False, "error": str(exc)[:160]}
    return result


def render_public_setup_confirmation(config_data: dict[str, Any]) -> str:
    cfg = safe_config_view(validate_config(config_data))
    entries = validate_package_entries(cfg["roblox_packages"])
    enabled_entries = [entry for entry in entries if entry.get("enabled", True)]
    lines: list[str] = ["Roblox Packages:"]
    if enabled_entries:
        for idx, entry in enumerate(enabled_entries, start=1):
            lines.append(f"  {idx}. {entry['package']}")
    else:
        lines.append("  Not set")
    lines.append("")
    mode = validate_private_url_mode(cfg.get("private_url_mode"))
    lines.append(f"Private URL mode: {'Global' if mode == 'global' else 'Separate'}")
    if mode == "global":
        lines.append(f"Global Private URL: {_safe_url_label(cfg.get('private_server_url'))}")
    else:
        lines.append("Package Private URLs:")
        for idx, entry in enumerate(enabled_entries, start=1):
            status = "Set" if str(entry.get("private_server_url") or "").strip() else "Blank / App Only"
            lines.append(f"  {idx}. {entry['package']}: {status}")
    return "\n".join(lines)


def _print_public_setup_confirmation(config_data: dict[str, Any]) -> None:
    print(render_public_setup_confirmation(config_data))


def _print_config_summary(config_data: dict[str, Any]) -> None:
    print("DENG Tool: Rejoin Settings")
    print()
    _print_public_setup_confirmation(config_data)


def _print_setup_menu(config_data: dict[str, Any], title: str = "DENG Tool: Rejoin Setup") -> None:
    cfg = safe_config_view(validate_config(config_data))
    print(termux_ui.separator("-"))
    print(title)
    print(termux_ui.separator("-"))
    print(f"1. Device Name: {cfg['device_name']}")
    print(f"2. Roblox Package: {cfg['roblox_package']}")
    print(f"3. Private URL: {_safe_url_label(cfg.get('private_server_url'))}")
    print("4. Layout: Landscape")
    print(f"5. Auto Rejoin: {_yes_no(cfg['auto_rejoin_enabled'])}")
    print(f"6. Reconnect Delay: {cfg['reconnect_delay_seconds']} seconds")
    print(f"7. Root Mode: {_yes_no(cfg['root_mode_enabled'])}")
    print(f"8. Health Check Interval: {cfg['health_check_interval_seconds']} seconds")
    print("9. Save and Finish")
    print("A. Advanced Info")
    print("0. Cancel")
    print(termux_ui.separator("-"))


def _choose_package() -> str:
    return _choose_package_menu(DEFAULT_ROBLOX_PACKAGE)


def _ordered_roblox_packages(package_detection_hints: list[str] | None = None) -> list[str]:
    packages = android.find_roblox_packages(package_detection_hints or _safe_detection_hints())
    ordered: list[str] = []
    if DEFAULT_ROBLOX_PACKAGE in packages:
        ordered.append(DEFAULT_ROBLOX_PACKAGE)
    for package in packages:
        if package not in ordered:
            ordered.append(package)
    return ordered


def _prompt_manual_package(default: str = DEFAULT_ROBLOX_PACKAGE) -> str | None:
    print("\nEnter Roblox package name")
    print("Example: com.roblox.client")
    while True:
        value = _prompt("Package name", default).strip()
        if not value:
            return None
        try:
            return validate_package_name(value)
        except ConfigError:
            print("That does not look like a safe Android package name. Use a format like com.roblox.client.")


def _entry_for_package(package: str, current_entries: list[dict[str, Any]], *, app_name: str = "") -> dict[str, Any]:
    for entry in current_entries:
        if entry["package"] == package:
            existing = dict(entry)
            if app_name and not str(existing.get("app_name") or "").strip():
                existing["app_name"] = str(app_name or "")[:120]
            return existing
    return package_entry(package, "", True, "not_set", app_name=str(app_name or "")[:120])


def _bounded_post_add_username_detection(
    draft: dict[str, Any],
    packages: list[str],
    *,
    total_deadline_seconds: float = 5.0,
    per_package_timeout_seconds: float = 1.5,
) -> dict[str, Any]:
    """Run :func:`package_username.safe_detect_username_for_package` for new packages.

    Strictly bounded.  Never calls Refresh Mapping / account_detect /
    cookie scanners.  Saves detected names to ``account_username`` (only
    when previously empty) and to ``package_username_cache`` so future
    renders can show the label without re-detecting.
    """
    if not packages:
        return draft
    targets = [str(p or "").strip() for p in packages if str(p or "").strip()]
    if not targets:
        return draft
    try:
        detected = package_username.collect_safe_usernames_for_packages(
            targets,
            per_package_timeout_seconds=per_package_timeout_seconds,
            total_deadline_seconds=total_deadline_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger("deng_tool_rejoin").debug(
            "bounded post-add username detection failed: %s", exc
        )
        return draft

    cache = dict(draft.get("package_username_cache") or {})
    changed = False
    entries = draft.get("roblox_packages") or []
    new_entries: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            new_entries.append(entry)
            continue
        pkg = str(entry.get("package") or "")
        if pkg in detected:
            name = detected[pkg]
            if name and name != "Unknown":
                cache[pkg] = name
                current = validate_account_username(entry.get("account_username") or "")
                source = str(entry.get("username_source") or "not_set")
                if not current or source in {"not_set", "auto", "manual", "config_manual"}:
                    entry = dict(entry)
                    entry["account_username"] = name
                    entry["username_source"] = validate_username_source(
                        "detected_safe_pref", name
                    )
                    changed = True
        new_entries.append(entry)
    if cache != (draft.get("package_username_cache") or {}):
        draft["package_username_cache"] = cache
        changed = True
    if changed:
        draft["roblox_packages"] = new_entries
        try:
            draft = save_config(draft)
        except Exception:  # noqa: BLE001
            pass
    return draft


def _detect_or_prompt_account_username(entry: dict[str, Any], config_data: dict[str, Any] | None = None) -> dict[str, Any]:
    """LEGACY DISABLED: account username detection is not part of package setup."""
    if _ACCOUNT_MAPPING_DISABLED:
        return dict(entry)
    updated = dict(entry)
    if validate_account_username(updated.get("account_username", "")):
        return updated
    cfg_valid = validate_config(config_data) if config_data is not None else None
    try:
        result = account_detect.detect_account_username(
            updated["package"],
            entry=updated,
            config=cfg_valid,
            use_root=True,
            respect_config_manual=False,
        )
    except PermissionError as exc:
        import logging as _logging
        _logging.getLogger("deng_tool_rejoin").debug(
            "Permission denied during username auto-detect for %s: %s",
            updated.get("package", "?"),
            exc,
        )
        result = None
    except Exception as exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger("deng_tool_rejoin").debug(
            "Username auto-detect error for %s: %s",
            updated.get("package", "?"),
            exc,
        )
        result = None
    if result:
        updated["account_username"] = validate_account_username(result.username)
        updated["username_source"] = validate_username_source(result.source, result.username)
        return updated
    updated["username_source"] = "not_set"
    return updated


def _try_detect_user_id(entry: dict[str, Any], draft: dict[str, Any]) -> tuple[int, str]:
    """LEGACY DISABLED: root/user ID mapping is not part of setup/start.

    Returns (user_id, source_label).  user_id=0 means not found.
    Never raises — setup must never crash here.
    """
    if _ACCOUNT_MAPPING_DISABLED:
        return 0, "disabled"
    pkg = str(entry.get("package") or "").strip()
    if not pkg:
        return 0, "not_found"

    # Already configured — respect it.
    existing = entry.get("roblox_user_id")
    if isinstance(existing, int) and existing > 0:
        return existing, "config"
    if isinstance(existing, str) and existing.isdigit() and int(existing) > 0:
        return int(existing), "config"

    try:
        cfg_valid = validate_config(draft) if draft else None
        uid = account_detect.detect_roblox_user_id(pkg, entry=entry, config=cfg_valid, use_root=True)
        if uid:
            return uid, "root_prefs"
    except Exception:  # noqa: BLE001
        pass

    # Fallback: try resolving from known username via Presence API.
    username = str(entry.get("account_username") or "").strip()
    if username:
        try:
            from . import roblox_presence as _rp
            uid = _rp.lookup_user_id(username)
            if uid and uid > 0:
                return uid, "api_resolved"
        except Exception:  # noqa: BLE001
            pass

    return 0, "not_found"


def _mapping_detect_config(draft: dict[str, Any], seconds_left: float) -> dict[str, Any]:
    detect_cfg = dict(draft or {})
    settings = dict((detect_cfg.get("account_detection") or {}))
    timeout = max(1, min(_SETUP_MAPPING_PER_PACKAGE_TIMEOUT_SECONDS, int(seconds_left) or 1))
    settings["scan_timeout_seconds"] = timeout
    settings["max_file_size_kb"] = min(int(settings.get("max_file_size_kb", 128) or 128), 128)
    detect_cfg["account_detection"] = settings
    return detect_cfg


def _safe_refresh_account_mapping_entries(
    entries: list[dict[str, Any]],
    draft: dict[str, Any],
    *,
    save: bool = False,
    selected_only: bool = True,
    print_rows: bool = True,
) -> list[dict[str, Any]]:
    """LEGACY DISABLED: return package entries without account mapping scans."""
    if _ACCOUNT_MAPPING_DISABLED:
        return [dict(e) for e in entries if isinstance(e, dict)]
    started = time.monotonic()
    hard_deadline = started + _REFRESH_MAPPING_HARD_BUDGET_SECONDS
    normal_deadline = started + _REFRESH_MAPPING_NORMAL_BUDGET_SECONDS
    out: list[dict[str, Any]] = []
    source_entries = [dict(e) for e in entries if isinstance(e, dict)]
    try:
        root_access.clear_cache()
    except Exception:  # noqa: BLE001
        if print_rows:
            termux_ui.print_warning("Root Cache Refresh Failed; Using Safe Fallback")

    for idx, entry in enumerate(source_entries, start=1):
        if time.monotonic() >= hard_deadline:
            if print_rows:
                termux_ui.print_warning("Refresh Mapping Timed Out; Remaining Packages Skipped")
            break

        original_pkg = str(entry.get("package") or "").strip()
        updated = dict(entry)
        status = "Detected"
        reason = ""
        try:
            pkg = validate_package_name(original_pkg)
        except ConfigError:
            if print_rows:
                print(f"{idx}. Unknown — Skipped — Invalid Package")
            updated["account_mapping_status"] = "Skipped"
            updated["account_mapping_source"] = "Invalid Package"
            out.append(updated)
            continue

        item_deadline = min(
            hard_deadline,
            time.monotonic() + _REFRESH_MAPPING_PER_PACKAGE_TIMEOUT_SECONDS,
        )
        username = validate_account_username(updated.get("account_username", "")) or ""
        try:
            if not username and time.monotonic() < item_deadline:
                detect_cfg = _mapping_detect_config(draft, item_deadline - time.monotonic())
                det = account_detect.detect_account_username(
                    pkg,
                    entry=updated,
                    config=detect_cfg,
                    use_root=True,
                )
                if det and det.username:
                    username = validate_account_username(det.username) or ""
                    updated["account_username"] = username
                    updated["username_source"] = validate_username_source(det.source, det.username)
                    updated["account_mapping_source"] = str(det.source or "detected")
            if time.monotonic() >= item_deadline:
                status = "Skipped"
                reason = "Timeout"
        except (PermissionError, FileNotFoundError):
            status = "Skipped"
            reason = "Permission Denied"
        except (TimeoutError, subprocess.TimeoutExpired):
            status = "Skipped"
            reason = "Timeout"
        except (UnicodeDecodeError, OSError, ValueError, ConfigError) as exc:
            status = "Skipped"
            reason = _safe_reason_text(exc)
        except Exception as exc:  # noqa: BLE001
            status = "Skipped"
            reason = _safe_reason_text(exc)

        if not username:
            username = "Unknown"
        updated["package"] = pkg
        updated["account_username"] = "" if username == "Unknown" else username
        updated["account_mapping_status"] = status if status == "Skipped" else "Detected"
        if reason:
            updated["account_mapping_source"] = reason
        elif not updated.get("account_mapping_source"):
            updated["account_mapping_source"] = "detected" if username != "Unknown" else "not_found"
        updated["account_mapping_updated_at"] = datetime.now(timezone.utc).isoformat()
        out.append(updated)

        if print_rows:
            short_pkg = _short_package_display(pkg)
            suffix = f" — {reason}" if reason else ""
            print(f"{idx}. {short_pkg} — {status} — User: {username}{suffix}", flush=True)
        if time.monotonic() >= normal_deadline:
            if print_rows:
                termux_ui.print_warning("Refresh Mapping Time Budget Reached; Saved Partial Results")
            break

    if save:
        try:
            if selected_only:
                refreshed = {str(e.get("package") or ""): e for e in out if isinstance(e, dict)}
                existing = validate_package_entries(draft.get("roblox_packages") or [])
                draft["roblox_packages"] = [
                    refreshed.get(str(e.get("package") or ""), e) for e in existing
                ]
            else:
                draft["roblox_packages"] = out
            active = [e for e in draft.get("roblox_packages", []) if isinstance(e, dict) and e.get("enabled", True)]
            if active:
                draft["roblox_package"] = active[0]["package"]
                draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
            save_config(draft)
        except Exception as exc:  # noqa: BLE001
            if print_rows:
                termux_ui.print_error(f"Refresh Mapping Finished But Could Not Save Config: {_safe_reason_text(exc)}")
    return out


def _validate_user_id_with_presence(user_id: int, *, cookie: str = "") -> str:
    """Quick non-blocking presence check to validate a detected user ID.

    Returns one of: "Validated", "API Unavailable", "Invalid".
    Never raises.
    """
    if user_id <= 0:
        return "Invalid"
    try:
        from . import roblox_presence as _rp
        auth_cookie = validate_roblosecurity_cookie(cookie) if cookie else None
        result = _rp.fetch_presence_one(user_id, cookie=auth_cookie or None)
        if result is not None:
            return "Validated"
        return "API Unavailable"
    except Exception:  # noqa: BLE001
        return "API Unavailable"


def _mapping_status_for(
    uid: int,
    src: str,
    entry: dict[str, Any],
    presence_status: str | None = None,
    is_display_name_only: bool = False,
) -> str:
    """Compute the canonical mapping status label for display and saving."""
    if src in ("skipped",):
        return "Skipped"
    if src == "manual":
        return "Manual"
    if src == "config":
        existing = entry.get("account_mapping_status") or ""
        return existing if existing in MAPPING_STATUSES else "Validated"
    if uid <= 0:
        if str(entry.get("account_username") or "").strip():
            return "Needs Confirmation"
        return "Not Mapped"
    if presence_status == "Validated":
        return "Validated"
    if presence_status == "API Unavailable":
        return "Detected"
    if presence_status == "Invalid":
        return "Invalid"
    if is_display_name_only:
        return "Needs Confirmation"
    return "Detected"


def _safe_table_cell(value: Any, *, limit: int = 40, fallback: str = "-") -> str:
    """Return a printable table cell that cannot break row width assumptions."""
    text = str(value if value is not None else fallback)
    text = _ANSI_RE.sub("", text).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = " ".join(text.split()) or fallback
    if len(text) > limit:
        return text[: max(1, limit - 3)] + "..."
    return text


def _print_account_mapping_plain(rows: list[tuple[str, str, str, str, str, str]]) -> None:
    for row in rows or [("", "-", "-", "-", "-", "Not Mapped")]:
        cells = tuple(_safe_table_cell(row[i] if i < len(row) else "-", limit=48) for i in range(6))
        print(
            f"  {cells[0] or '-'} | Package: {cells[1]} | Username: {cells[2]} | "
            f"User ID: {cells[3]} | Source: {cells[4]} | Status: {cells[5]}"
        )


def _run_account_mapping_table(
    entries: list[dict[str, Any]],
    draft: dict[str, Any],
    *,
    show_root_message: bool = True,
) -> list[dict[str, Any]]:
    """LEGACY DISABLED: account mapping table is unreachable in this release.

    Shows: # | Package | Username | User ID | Source | Status
    Allows: A=accept all, <number>=edit that entry, B=back (cancel mapping only).
    Returns entries updated with any detected/confirmed roblox_user_id values
    and account_mapping_source / account_mapping_status / account_mapping_updated_at.
    Never blocks Start — missing mapping is just silently skipped.
    """
    if _ACCOUNT_MAPPING_DISABLED:
        return [dict(e) for e in entries if isinstance(e, dict)]
    if not entries:
        return entries
    entries = [dict(e) for e in entries if isinstance(e, dict)]
    if not entries:
        return []

    # --- Show one-time root availability message ---
    if show_root_message and _is_interactive():
        if not root_access.has_root():
            print()
            termux_ui.print_warning(
                "Root access was not available. Username/User ID detection will use fallback/manual mapping."
            )

    # --- Run detection for entries that don't have a user_id yet ---
    if _is_interactive():
        print("Detecting accounts...", flush=True)
    detected: list[tuple[int, str]] = []
    for entry in entries:
        try:
            uid, src = _try_detect_user_id(entry, draft)
        except Exception:  # noqa: BLE001
            uid, src = 0, "not_found"
        detected.append((uid, src))

    # --- Run presence validation (use cached cookie only — no root scan here) ---
    presence_statuses: list[str] = []
    for i, entry in enumerate(entries):
        uid, src = detected[i]
        if src == "config":
            existing = str(entries[i].get("account_mapping_status") or "")
            presence_statuses.append(existing if existing in MAPPING_STATUSES else "Validated")
            continue
        cookie = str(entry.get("roblox_cookie") or "").strip()
        if uid > 0:
            try:
                presence_statuses.append(_validate_user_id_with_presence(uid, cookie=cookie))
            except Exception:  # noqa: BLE001
                presence_statuses.append("API Unavailable")
        else:
            presence_statuses.append("")

    def _row_status(i: int, entry: dict[str, Any]) -> str:
        uid, src = detected[i]
        ps = presence_statuses[i]
        return _mapping_status_for(uid, src, entry, presence_status=ps)

    def _mapping_rows() -> list[tuple[str, str, str, str, str, str]]:
        rows: list[tuple[str, str, str, str, str, str]] = []
        for i, entry in enumerate(entries):
            uid, src = detected[i] if i < len(detected) else (0, "not_found")
            username = _package_username_display(entry) or "-"
            uid_disp = str(uid) if uid > 0 else "-"
            src_disp = src or "-"
            status = _row_status(i, entry)
            rows.append((
                str(i + 1),
                _safe_table_cell(_short_package_display(str(entry.get("package") or "-")), limit=36),
                _safe_table_cell(username, limit=20),
                _safe_table_cell(uid_disp, limit=18),
                _safe_table_cell(src_disp, limit=14),
                _safe_table_cell(status, limit=22),
            ))
        return rows

    def _print_mapping_table() -> None:
        print()
        print("Account Mapping")
        rows = _mapping_rows()
        try:
            print(build_account_mapping_table(rows))
        except Exception:  # noqa: BLE001
            termux_ui.print_warning("Table rendering failed; showing simple mapping list.")
            _print_account_mapping_plain(rows)
        print()
        print("  A. Accept all  |  1-N. Skip entry  |  B. Back")
        print()
        try:
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass

    if not _is_interactive():
        # Non-interactive: apply detected mappings silently with full metadata.
        return _apply_mapping_to_entries(entries, detected, presence_statuses, config=draft)

    while True:
        _print_mapping_table()
        raw_input = safe_io.safe_prompt("Choose [A]: ", default="a")
        if raw_input is None:
            print()
            break
        raw = raw_input.strip().lower() or "a"

        if raw in ("a", ""):
            break
        if raw in ("b", "back", "0"):
            return list(entries)

        if raw.isdigit():
            idx = int(raw) - 1
            if not (0 <= idx < len(entries)):
                print(f"  Invalid number. Enter 1-{len(entries)}, A, or B.")
                safe_io.press_enter()
                continue
            entry = dict(entries[idx])
            uid_cur, _ = detected[idx]
            pkg = str(entry.get("package") or "")
            print(f"\n  Editing: {pkg}")
            cur_name = str(entry.get("account_username") or "").strip()
            if cur_name:
                print(f"  Current username: {cur_name}")
            if uid_cur > 0:
                print(f"  Current user ID:  {uid_cur}")
            print("  Manual username editing is disabled. This entry will be skipped.")
            detected[idx] = (0, "skipped")
            presence_statuses[idx] = ""
        else:
            print("  Enter A, B, or a package number.")
            safe_io.press_enter()

    return _apply_mapping_to_entries(entries, detected, presence_statuses, config=draft)


def _ensure_presence_auth_for_entries(
    entries: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Root-scan ROBLOSECURITY cookies for watchdog presence checks."""
    from agent.roblox_presence import detect_roblox_cookie

    out: list[dict[str, Any]] = []
    for entry in entries:
        e = dict(entry)
        pkg = str(e.get("package") or "").strip()
        if not pkg:
            out.append(e)
            continue
        if not str(e.get("roblox_cookie") or "").strip():
            try:
                cookie = detect_roblox_cookie(
                    pkg,
                    entry=e,
                    config=config,
                    use_root=True,
                )
                if cookie:
                    e["roblox_cookie"] = validate_roblosecurity_cookie(cookie)
            except Exception:  # noqa: BLE001
                pass
        out.append(e)
    return out


def _auto_detect_cookies_for_entries(
    entries: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    *,
    force_refresh: bool = False,
    announce: bool = True,
) -> list[dict[str, Any]]:
    """LEGACY DISABLED: cookie scanning is not run by setup/start."""
    if _ACCOUNT_MAPPING_DISABLED:
        return [dict(e) for e in entries if isinstance(e, dict)]
    from agent.roblox_presence import detect_roblox_cookie

    out: list[dict[str, Any]] = []
    detected_count = 0
    for entry in entries:
        e = dict(entry)
        pkg = str(e.get("package") or "").strip()
        if not pkg:
            out.append(e)
            continue
        if force_refresh:
            e.pop("roblox_cookie", None)
        elif str(e.get("roblox_cookie") or "").strip():
            out.append(e)
            continue
        try:
            cookie = detect_roblox_cookie(
                pkg,
                entry=e,
                config=config,
                use_root=True,
                force_rescan=force_refresh,
            )
            if cookie:
                e["roblox_cookie"] = validate_roblosecurity_cookie(cookie)
                detected_count += 1
        except Exception:  # noqa: BLE001
            pass
        out.append(e)
    if announce and detected_count and _is_interactive():
        print(f"Detected ROBLOSECURITY cookie for {detected_count} package(s).")
    return out


def _apply_mapping_to_entries(
    entries: list[dict[str, Any]],
    detected: list[tuple[int, str]],
    presence_statuses: list[str],
    *,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """LEGACY DISABLED: mapping metadata is no longer applied."""
    if _ACCOUNT_MAPPING_DISABLED:
        return [dict(e) for e in entries if isinstance(e, dict)]
    now_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    mapped = _auto_detect_cookies_for_entries(entries, config, announce=False)
    out = []
    for i, entry in enumerate(mapped):
        e = dict(entry)
        uid, src = detected[i]
        ps = presence_statuses[i] if i < len(presence_statuses) else ""
        status = _mapping_status_for(uid, src, e, presence_status=ps)

        if status == "Invalid":
            # Do not save invalid IDs.
            uid = 0

        if uid > 0:
            e["roblox_user_id"] = uid

        if src not in ("config", "not_found", ""):
            e["account_mapping_source"] = src
            e["account_mapping_status"] = status
            e["account_mapping_updated_at"] = now_ts
        elif "account_mapping_status" not in e:
            e["account_mapping_status"] = status
        out.append(e)
    return out


def _print_package_entries(entries: list[dict[str, Any]]) -> None:
    if not entries:
        print("  No packages selected.")
        return
    for idx, entry in enumerate(entries, start=1):
        enabled = "" if entry.get("enabled", True) else " [Disabled]"
        print(f"  {idx}. {_package_username_display(entry):<20} {entry['package']}{enabled}")


def _choose_package_menu(current_package: str = DEFAULT_ROBLOX_PACKAGE, package_detection_hints: list[str] | None = None) -> str:
    try:
        cfg_ctx = load_config()
    except ConfigError:
        cfg_ctx = {}
    entries, _hints = _choose_packages_menu(
        [package_entry(current_package, "", True, "not_set")],
        package_detection_hints,
        cfg_ctx,
    )
    enabled = [entry for entry in entries if entry.get("enabled", True)]
    return enabled[0]["package"] if enabled else current_package


def _choose_packages_menu(
    current_packages: list[Any] | None = None,
    package_detection_hints: list[str] | None = None,
    config_data: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    cfg_ctx = config_data if isinstance(config_data, dict) else {}
    merged_hints_ctx = dict(cfg_ctx)
    if package_detection_hints is not None:
        merged_hints_ctx["package_detection_hints"] = package_detection_hints
    selected = validate_package_entries(current_packages or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")])
    hints = _safe_detection_hints(merged_hints_ctx)
    if not _is_interactive():
        return selected, hints

    print()
    print(termux_ui.separator("-"))
    print("Roblox Package Setup")
    print(termux_ui.separator("-"))
    print("1. Auto detect Roblox packages")
    print("2. Enter package name manually")
    print(termux_ui.separator("-"))
    _ch = safe_io.safe_prompt("Choose [1]: ", default="1")
    choice = (_ch or "1").strip() or "1"

    if choice == "1":
        new_sel, reason = _interactive_discover_package_entries(
            cfg_ctx,
            selected,
            exclude_packages=None,
            config_for_detect=None,
        )
        if reason == "no_candidates":
            print()
            print("No Roblox Package Detected")
            print()
            print("Try:")
            print("  1. Install Roblox or your Roblox clone APK.")
            print("  2. Open Roblox once manually.")
            print("  3. Return to Termux.")
            print("  4. Run package detection again.")
            print("  5. Use manual package entry if needed.")
            print()
            return selected, hints
        if reason == "ok":
            selected = new_sel
        return selected, hints
    elif choice == "2":
        default_package = selected[0]["package"] if selected else DEFAULT_ROBLOX_PACKAGE
        manual = _prompt_manual_package(default_package)
        if manual:
            if android.package_installed(manual):
                print(f"Package Found: {manual}")
            else:
                termux_ui.print_warning("Package was not launch-validated; saving manual entry anyway")
            selected = [_entry_for_package(manual, selected)]

    return selected, hints


def _choose_launch_settings() -> tuple[str, str]:
    if not _is_interactive():
        return "app", ""
    print("Launch mode:")
    print("  1. app only")
    print("  2. roblox deeplink")
    print("  3. roblox web/private-server URL")
    while True:
        choice = _prompt("Choose launch mode", "1")
        mode = {"1": "app", "2": "deeplink", "3": "web_url"}.get(choice, choice.strip().lower())
        if mode not in {"app", "deeplink", "web_url"}:
            print("Choose 1, 2, 3, app, deeplink, or web_url.")
            continue
        if mode == "app":
            return mode, ""
        while True:
            url = _prompt("Launch URL")
            inferred = detect_launch_mode_from_url(url)
            if mode == "deeplink" and inferred != "deeplink":
                print("Deep link mode needs a roblox:// URL.")
                continue
            if mode == "web_url" and inferred != "web_url":
                print("Web URL mode needs a Roblox https:// URL.")
                continue
            try:
                result = validate_launch_url(url, mode, allow_uncertain=True)
                if result.warning:
                    print(f"Warning: {result.warning}")
                return mode, url
            except UrlValidationError as exc:
                print(f"Invalid URL: {exc}")


def _choose_launch_mode(current_mode: str) -> str:
    if not _is_interactive():
        return current_mode
    print()
    print("Launch Mode")
    print("1. Open Roblox app only")
    print("2. Roblox deep link")
    print("3. Roblox web/private-server URL")
    while True:
        choice = _prompt("Choose launch mode", {"app": "1", "deeplink": "2", "web_url": "3"}.get(current_mode, "1"))
        mode = {"1": "app", "2": "deeplink", "3": "web_url"}.get(choice, choice.strip().lower())
        if mode in {"app", "deeplink", "web_url"}:
            return mode
        print("Choose 1, 2, or 3.")


def _prompt_launch_url(current_url: str, launch_mode: str) -> str:
    if launch_mode == "app":
        print("App-only mode does not need a URL. DENG will open Roblox normally.")
        return ""
    print()
    print("Paste a Roblox link, or leave blank to skip.")
    if launch_mode == "deeplink":
        print("Example: roblox://experiences/start?placeId=123")
    else:
        print("Example: https://www.roblox.com/games/123/name?privateServerLinkCode=...")
    while True:
        value = _prompt("Launch URL (blank to skip)", current_url).strip()
        if not value:
            print("Skipped. No launch URL set.")
            return ""
        try:
            result = validate_launch_url(value, launch_mode, allow_uncertain=True)
            if result.warning:
                print(f"Note: {result.warning}")
            return value
        except UrlValidationError as exc:
            print(f"That URL cannot be used yet: {exc}")
            print("Enter a valid URL or leave blank to skip.")


def _private_url_prompt(default: str = "", *, global_label: bool = False) -> str | None:
    label = (
        "[?] Enter Global Private Server URL Or Leave Blank For App Only"
        if global_label
        else "[?] Enter Private Server URL Or Leave Blank For App Only"
    )
    value = _prompt(label, default).strip()
    if not value:
        return ""
    scheme = value.split(":", 1)[0].lower() if ":" in value else ""
    mode = "deeplink" if scheme == "roblox" else "web_url"
    try:
        result = validate_launch_url(value, mode, allow_uncertain=True)
        if result.warning:
            print(f"Note: {result.warning}")
        return value
    except UrlValidationError as exc:
        print(f"That URL cannot be used: {exc}")
        return None


def _set_global_private_url(draft: dict[str, Any], value: str) -> None:
    draft["private_url_mode"] = "global"
    draft["private_server_url"] = value
    if value:
        draft["launch_mode"] = "deeplink" if value.lower().startswith("roblox:") else "web_url"
        draft["launch_url"] = value
    else:
        draft["launch_mode"] = "app"
        draft["launch_url"] = ""


def _setup_global_private_url(draft: dict[str, Any]) -> None:
    current = str(draft.get("private_server_url") or draft.get("launch_url") or "")
    value = _private_url_prompt(current, global_label=True)
    if value is None:
        print("Keeping previous Global Private URL.")
        return
    _set_global_private_url(draft, value)
    if value:
        print("Saved. All packages will use the Global Private URL.")
    else:
        print("Saved. All packages will open app-only.")


def _setup_separate_private_urls(draft: dict[str, Any]) -> None:
    draft["private_url_mode"] = "separate"
    entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    total = len(entries)
    updated: list[dict[str, Any]] = []
    for idx, entry in enumerate(entries, start=1):
        item = dict(entry)
        print()
        print(f"Package {idx}/{total}:")
        print(item["package"])
        value = _private_url_prompt(str(item.get("private_server_url") or ""))
        if value is not None:
            item["private_server_url"] = value
        updated.append(item)
    draft["roblox_packages"] = updated
    draft["launch_mode"] = "app"
    draft["launch_url"] = ""
    print("Saved. Each package will use its own Private URL setting.")


def _setup_launch_link(draft: dict[str, Any], *, allow_back: bool = True) -> None:
    """Configure Global or Separate Private URL mode."""
    while True:
        print()
        print("[?] Private URL Mode")
        print()
        print("1. Global Private URL - Use one URL for all packages")
        print("2. Separate Private URL - Set a different URL for each package")
        if allow_back:
            print("0. Back")
        else:
            print("3. Skip / Not Set")
        choice = _prompt("Choose", "1").strip()
        if allow_back and choice == "0":
            return
        if choice == "1":
            _setup_global_private_url(draft)
            return
        if choice == "2":
            _setup_separate_private_urls(draft)
            return
        if not allow_back and choice == "3":
            _set_global_private_url(draft, "")
            print("Saved. All packages will open app-only.")
            return
        print("Choose 1, 2, or 3." if not allow_back else "Choose 0, 1, or 2.")


def _setup_webhook(draft: dict[str, Any]) -> None:
    print()
    print("Webhook")
    print("1. Edit")
    print("2. New Post")
    print("3. None")
    choice = _prompt("Choose webhook mode", "1").strip()
    if choice == "3":
        draft["webhook_enabled"] = False
        draft["webhook_mode"] = "none"
        draft["webhook_url"] = ""
        draft["webhook_last_message_id"] = ""
        return
    draft["webhook_enabled"] = True
    draft["webhook_mode"] = "edit" if choice == "1" else "new_post"
    _setup_webhook_interval(draft)
    while True:
        value = _prompt("Enter Discord webhook URL", "").strip()
        try:
            draft["webhook_url"] = webhook.validate_webhook_url(value)
            break
        except ValueError as exc:
            print(f"Webhook URL is not valid: {exc}")


def _setup_webhook_interval(draft: dict[str, Any]) -> None:
    print()
    while True:
        value = _prompt("Enter webhook interval in minutes (5-1440)", str(draft.get("webhook_interval_minutes", 5))).strip()
        try:
            draft["webhook_interval_minutes"] = webhook.validate_webhook_interval(value)
            draft["webhook_interval_seconds"] = draft["webhook_interval_minutes"] * 60
            return
        except ValueError as exc:
            print(exc)


def _setup_screen_mode(draft: dict[str, Any]) -> None:
    """Legacy no-op: public setup no longer exposes layout mode selection."""
    draft["screen_mode"] = DEFAULT_SCREEN_MODE
    _enforce_configured_screen_mode(draft)


def _setup_yescaptcha_key(draft: dict[str, Any]) -> None:
    """Prompt the user to enter or clear their YesCaptcha API key."""
    print()
    print("YesCaptcha API Key")
    print("Obtain your key from https://yescaptcha.com — used for CAPTCHA solving.")
    print("Leave blank to skip or clear.")
    current = draft.get("yescaptcha_key", "") or ""
    print(f"Current: {'Configured (hidden)' if current else 'Not set'}")
    raw = _prompt("YesCaptcha key", "").strip()
    if not raw:
        return
    draft["yescaptcha_key"] = raw[:256]
    print("YesCaptcha API key saved.")


def _write_termux_boot_script() -> None:
    TERMUX_BOOT_SCRIPT.parent.mkdir(parents=True, exist_ok=True)
    script = """#!/data/data/com.termux/files/usr/bin/sh
sleep 15
APP_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"
cd "$APP_HOME" || exit 0
sh scripts/start-agent.sh >> "$APP_HOME/logs/agent.log" 2>&1
"""
    TERMUX_BOOT_SCRIPT.write_text(script, encoding="utf-8")
    TERMUX_BOOT_SCRIPT.chmod(0o755)


# ─── Config Menu Submenus ─────────────────────────────────────────────────────

def _config_menu_package(draft: dict[str, Any]) -> dict[str, Any]:
    """Package submenu: clean public menu.

    Options:
      1. Auto Detect Package
      2. Add Package
      0. Back
    """
    if not _is_interactive():
        return draft
    with safe_io.tty_session():
        return _config_menu_package_loop(draft)


def _config_menu_package_loop(draft: dict[str, Any]) -> dict[str, Any]:
    while True:
        print()
        entries = validate_package_entries(
            draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
        )
        username_diag: list[dict[str, Any]] = []
        menu_scans: dict[str, package_username.UsernameScanReport] = {}
        enabled_entries = [e for e in entries if e.get("enabled", True)]
        for entry in entries:
            pkg = str(entry.get("package") or "").strip()
            if not pkg:
                continue
            scan = package_username.scan_package_username_for_menu(pkg, draft)
            menu_scans[pkg] = scan
            uname, src = package_username.menu_username_display(scan, entry, draft)
            username_diag.append({
                "package": pkg,
                "display_username": uname,
                "username_source": src,
                "username_supported": scan.supported,
                "username_reason": scan.reason,
                "methods_attempted": list(scan.methods_attempted),
                "detector_used": scan.root_used,
                "root_used": scan.root_used,
                "confidence": scan.confidence,
                "root_read_status": scan.root_read_status,
                "detector_duration_ms": scan.duration_ms,
                "mapping_refresh_called": False,
            })
        draft["_package_menu_username_diag"] = username_diag
        current_lines = ["Current Packages:"]
        if enabled_entries:
            for idx, entry in enumerate(enabled_entries, start=1):
                pkg = entry["package"]
                scan = menu_scans.get(pkg) or package_username.scan_package_username_for_menu(pkg, draft)
                uname, _src = package_username.menu_username_display(scan, entry, draft)
                current_lines.append(
                    termux_ui.fit_line(
                        f"  [{idx}] {pkg} | username: {uname}"
                    )
                )
        else:
            current_lines.append("  No Packages Configured.")
        termux_ui.print_submenu(
            "Packages",
            [
                ("1", "Auto Detect Package"),
                ("2", "Add Package"),
                ("3", "Remove Package"),
                ("0", "Back"),
            ],
            current_lines=current_lines,
        )
        _mc = safe_io.safe_prompt(f"{termux_ui.choose_prompt('0')} ", default="0")
        if _mc is None:
            break
        choice = _mc.strip() or "0"
        try:
            if choice == "0":
                break
            elif choice == "1":
                draft = _package_menu_auto_detect(draft)
            elif choice == "2":
                draft = _package_menu_add(draft)
            elif choice == "3":
                draft = _package_menu_remove(draft)
            else:
                termux_ui.print_invalid_option()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        except Exception as exc:  # noqa: BLE001
            termux_ui.print_error(f"Package menu error: {str(exc)[:120]}")
            safe_io.press_enter()
    return draft


def _package_menu_detect_refresh(draft: dict[str, Any]) -> dict[str, Any]:
    """LEGACY DISABLED: hidden username refresh handler is unreachable."""
    if _ACCOUNT_MAPPING_DISABLED:
        return draft
    print()
    print("Detect / Refresh Usernames")
    entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    cfg = validate_config(draft)
    settings = cfg.get("account_detection") or {}
    if not settings.get("enabled", True):
        print("Account detection is disabled.")
        input("\nPress Enter to continue...")
        return draft

    pairs = account_detect.detect_account_usernames_for_packages(
        [dict(e) for e in entries],
        config=cfg,
        use_root=bool(settings.get("use_root", True)),
        respect_config_manual=False,
    )
    new_entries: list[dict[str, Any]] = []
    print()
    print(f"  {'Package':<40} {'Old':<16} {'New':<16} {'Source':<18} {'Status':<12}")
    print(f"  {'-'*40} {'-'*16} {'-'*16} {'-'*18} {'-'*12}")
    changed = False
    for idx, prior in enumerate(entries):
        base_entry, res = pairs[idx]
        pkg = str(base_entry.get("package") or "")
        old_u = validate_account_username(prior.get("account_username", "")) or "Unknown"
        if res:
            new_u = validate_account_username(res.username) or "Unknown"
            src = res.source
            merged = dict(prior)
            merged["account_username"] = validate_account_username(res.username)
            merged["username_source"] = validate_username_source(res.source, res.username)
            if old_u != new_u:
                status = "updated"
                changed = True
            else:
                status = "unchanged"
            new_entries.append(merged)
        else:
            new_u = old_u
            src = "—"
            status = "not found"
            new_entries.append(dict(prior))
        print(f"  {pkg:<40} {str(old_u)[:16]:<16} {str(new_u)[:16]:<16} {str(src)[:18]:<18} {status:<12}")

    if new_entries:
        draft["roblox_packages"] = new_entries
        active = enabled_package_entries(draft)
        if active:
            draft["roblox_package"] = active[0]["package"]
        draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
        if changed:
            draft = save_config(draft)
            print("\nSaved updated usernames to config.")
        else:
            print("\nNo username changes to save.")
    input("\nPress Enter to continue...")
    return draft


def _package_menu_set_user_id(draft: dict[str, Any]) -> dict[str, Any]:
    """Set per-package Roblox user-id (or auto-resolve from a username).

    Used to enable Roblox presence-API state detection.  Old configs without
    ``roblox_user_id`` continue to work — supervisor simply falls back to
    local heuristics for those packages.
    """
    print()
    print("Set Roblox User ID")
    print("(needed for presence-API state — visible-in-game truth)")
    entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    if not entries:
        print("No packages configured.")
        return draft
    print()
    for idx, entry in enumerate(entries, start=1):
        username = _package_username_display(entry)
        uid = int(entry.get("roblox_user_id") or 0)
        uid_disp = str(uid) if uid > 0 else "not set"
        print(f"  {idx}. {entry['package']} — {username} (user_id: {uid_disp})")
    print("  0. Back")
    _pc = safe_io.safe_prompt("Choose package [0]: ", default="0")
    choice = (_pc or "0").strip() or "0"
    if choice == "0" or not choice.isdigit():
        return draft
    i = int(choice) - 1
    if not (0 <= i < len(entries)):
        print("Invalid choice.")
        return draft
    target = dict(entries[i])
    current_uid = int(target.get("roblox_user_id") or 0)
    print()
    print(f"Package: {target['package']}")
    if current_uid:
        print(f"User ID:  {current_uid}")
    print()
    print("Enter a numeric Roblox user ID directly. Leave blank to cancel.")
    raw_inp = safe_io.safe_prompt("User ID: ")
    raw = (raw_inp or "").strip()
    if not raw:
        print("Skipped.")
        return draft

    if not raw.isdigit() or int(raw) <= 0:
        print("Invalid user ID.")
        return draft
    new_uid = int(raw)
    print(f"Set user ID: {new_uid}")

    try:
        target["roblox_user_id"] = int(new_uid) if new_uid > 0 else 0
    except ConfigError as exc:
        print(f"Invalid input: {exc}")
        safe_io.press_enter()
        return draft

    entries = [target if e["package"] == target["package"] else dict(e) for e in entries]
    draft["roblox_packages"] = entries
    draft = save_config(draft)
    print(f"Saved user ID {new_uid} for {target['package']}.")
    safe_io.press_enter()
    return draft


def _package_menu_add(draft: dict[str, Any]) -> dict[str, Any]:
    """Add Package: detect available Roblox-like packages first, then let user choose or type manually.

    Flow: detect → show table → number/A=all/M=manual/B=back → confirm before saving.
    """
    current_entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    current_pkgs = {e["package"] for e in current_entries}

    print()
    print("Add Package")
    print()

    # Run detection first, filter to not-yet-configured packages
    _safe_package_detect_progress()
    all_candidates = _gather_roblox_candidates_for_ui(draft)
    fresh_candidates = [c for c in all_candidates if c.package not in current_pkgs]

    if fresh_candidates:
        print(f"Detected {len(fresh_candidates)} new Roblox-like package(s):")
        _print_full_discovery_table(fresh_candidates, draft)
        print(f"  A. Add all ({len(fresh_candidates)})")
        print("  M. Enter package name manually")
        print("  B. Back")
        print()
    else:
        if all_candidates:
            print("All detected packages are already configured.")
        else:
            print("No Roblox-like packages detected automatically.")
        print()
        print("  M. Enter package name manually")
        print("  B. Back")

    raw_in = safe_io.safe_prompt("Choose (e.g. 1,2 or A, M, B) [M]: ", default="m")
    if raw_in is None:
        print()
        return draft
    raw = raw_in.strip().lower() or "m"

    if raw in ("b", "back", "0"):
        return draft

    new_entries_to_append: list[dict[str, Any]] = []

    if raw == "m":
        # Manual entry
        default_pkg = current_entries[0]["package"] if current_entries else DEFAULT_ROBLOX_PACKAGE
        manual = _prompt_manual_package(default_pkg)
        if not manual:
            return draft
        if manual in current_pkgs:
            print(f"Package already configured: {manual}")
            return draft
        if android.package_installed(manual):
            print(f"Package Found: {manual}")
        else:
            termux_ui.print_warning("Package was not launch-validated; saving manual entry anyway")
        entry = _entry_for_package(manual, current_entries)
        print()
        print(f"  Package:  {manual}")
        print("  Username: Detecting automatically after save")
        print()
        confirm_in = safe_io.safe_prompt("Add this package? [Y/n]: ", default="y")
        if confirm_in is None:
            print()
            return draft
        confirm = confirm_in.strip().lower()
        if confirm in ("n", "no"):
            print("Cancelled.")
            return draft
        new_entries_to_append = [entry]

    elif raw == "a" and fresh_candidates:
        # Add all detected
        new_entries_to_append = [
            _entry_for_package(c.package, current_entries, app_name=c.app_name)
            for c in fresh_candidates
        ]
        print()
        print("Packages to add:")
        for i, entry in enumerate(new_entries_to_append, start=1):
            print(f"  {i}. {entry['package']}")
        print()
        confirm_in = safe_io.safe_prompt("Add all these packages? [Y/n]: ", default="y")
        if confirm_in is None:
            print()
            return draft
        confirm = confirm_in.strip().lower()
        if confirm in ("n", "no"):
            print("Cancelled.")
            return draft

    elif fresh_candidates:
        # Number selection from detected list
        picked: list = []
        seen: set[str] = set()
        for part in [p.strip() for p in raw.split(",") if p.strip()]:
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(fresh_candidates):
                    c = fresh_candidates[idx]
                    if c.package not in seen:
                        seen.add(c.package)
                        picked.append(c)
        if not picked:
            print("No valid selection.")
            return draft
        new_entries_to_append = [
            _entry_for_package(c.package, current_entries, app_name=c.app_name)
            for c in picked
        ]
        print()
        print("Packages to add:")
        for i, entry in enumerate(new_entries_to_append, start=1):
            print(f"  {i}. {entry['package']}")
        print()
        confirm_in = safe_io.safe_prompt("Add these packages? [Y/n]: ", default="y")
        if confirm_in is None:
            print()
            return draft
        confirm = confirm_in.strip().lower()
        if confirm in ("n", "no"):
            print("Cancelled.")
            return draft
    else:
        print("No valid selection.")
        return draft

    if not new_entries_to_append:
        return draft

    added_any = False
    for entry in new_entries_to_append:
        if entry["package"] in current_pkgs:
            print(f"Already configured: {entry['package']}")
            continue
        current_entries.append(entry)
        current_pkgs.add(entry["package"])
        added_any = True

    if added_any:
        draft["roblox_packages"] = current_entries
        active = enabled_package_entries(draft)
        draft["roblox_package"] = active[0]["package"]
        draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
        draft = save_config(draft)
        new_packages = [
            e["package"]
            for e in new_entries_to_append
            if e["package"] in current_pkgs
            and not validate_account_username(e.get("account_username") or "")
        ]
        if new_packages:
            draft = _bounded_post_add_username_detection(draft, new_packages)
        print(f"Saved {sum(1 for e in new_entries_to_append if e['package'] in current_pkgs)} package(s).")
    return draft


def _package_menu_remove(draft: dict[str, Any]) -> dict[str, Any]:
    """Remove a configured package by number. Confirms before removing."""
    entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    enabled = [e for e in entries if e.get("enabled", True)]
    if not enabled:
        print("No Packages Configured.")
        return draft
    print()
    print("Remove Package")
    for idx, entry in enumerate(enabled, start=1):
        print(f"  {idx}. {entry['package']}")
    print("  0. Back")
    _rc = safe_io.safe_prompt("Choose package to remove [0]: ", default="0")
    choice = (_rc or "0").strip() or "0"
    if choice == "0" or not choice.isdigit():
        return draft
    i = int(choice) - 1
    if not (0 <= i < len(enabled)):
        print("Invalid choice.")
        return draft
    target = enabled[i]
    _cf = safe_io.safe_prompt(f"Remove {target['package']}? [y/N]: ")
    confirm = (_cf or "").strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled.")
        return draft
    remaining = [e for e in entries if e["package"] != target["package"]]
    if not remaining:
        print("Cannot Remove The Last Package.")
        return draft
    draft["roblox_packages"] = remaining
    active = [e for e in remaining if e.get("enabled", True)]
    if active:
        draft["roblox_package"] = active[0]["package"]
    draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
    draft = save_config(draft)
    print(f"Package Removed: {target['package']}")
    return draft


def _package_menu_refresh_mapping(draft: dict[str, Any]) -> dict[str, Any]:
    """LEGACY DISABLED: hidden mapping refresh handler is unreachable.

    Probe p-d35129b645 showed this path could leave Termux unusable after
    table rendering/account detection.  This flow intentionally avoids Rich
    tables, nested prompts, dynamic redraw, and unbounded scans.
    """
    if _ACCOUNT_MAPPING_DISABLED:
        return draft
    def _restore_terminal() -> None:
        try:
            sys.stdout.write("\033[0m\033[?25h\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            pass

    def _load_refresh_entries() -> list[dict[str, Any]]:
        raw_entries = draft.get("roblox_packages") or []
        try:
            return validate_package_entries(raw_entries)
        except ConfigError:
            if not isinstance(raw_entries, (list, tuple)):
                return []
            recovered: list[dict[str, Any]] = []
            for raw in raw_entries:
                if isinstance(raw, dict):
                    recovered.append(dict(raw))
                elif isinstance(raw, str):
                    recovered.append({"package": raw, "account_username": "", "enabled": True})
            return recovered

    try:
        print()
        termux_ui.print_warning("Refreshing Package Mapping...")
        termux_ui.print_warning("This May Take A Few Seconds")
        entries = _load_refresh_entries()

        enabled_entries = [dict(e) for e in entries if isinstance(e, dict) and e.get("enabled", True)]
        if not enabled_entries:
            termux_ui.print_warning("No Packages Configured")
            return draft

        refreshed = _safe_refresh_account_mapping_entries(enabled_entries, draft, print_rows=True)
        refreshed_by_pkg: dict[str, dict[str, Any]] = {
            str(e.get("package") or ""): e for e in refreshed if isinstance(e, dict)
        }
        skipped_count = sum(
            1 for e in refreshed
            if str(e.get("account_mapping_status") or "") == "Skipped"
        )
        merged = [refreshed_by_pkg.get(str(e.get("package") or ""), e) for e in entries]
        draft["roblox_packages"] = merged
        active = [e for e in merged if isinstance(e, dict) and e.get("enabled", True)]
        if active:
            draft["roblox_package"] = active[0]["package"]
            draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
        try:
            save_config(draft)
        except ConfigError as exc:
            termux_ui.print_error(f"Refresh Mapping Finished But Could Not Save Config: {_safe_reason_text(exc)}")
            return draft
        if skipped_count:
            termux_ui.print_warning(f"Refresh Mapping Finished With {skipped_count} Skipped Step(s)")
        else:
            print(f"{termux_ui.GREEN}[✓] Refresh Mapping Finished.{termux_ui.RESET}")
        return draft
    except KeyboardInterrupt:
        print()
        termux_ui.print_warning("Refresh Mapping Cancelled")
        return draft
    except EOFError:
        print()
        termux_ui.print_warning("Refresh Mapping Stopped")
        return draft
    except Exception as exc:  # noqa: BLE001
        termux_ui.print_error(f"Refresh Mapping Failed: {str(exc)[:120]}")
        return draft
    finally:
        _restore_terminal()
        try:
            safe_io.press_enter(f"{termux_ui.prompt_prefix('Press Enter To Continue')} ")
        except Exception:  # noqa: BLE001
            pass


def _package_menu_auto_detect(draft: dict[str, Any]) -> dict[str, Any]:
    """Auto-detect Roblox-like packages, let user select, then confirm before saving."""
    print()
    print("Auto Detect Package")
    print()
    _safe_package_detect_progress()
    candidates = _gather_roblox_candidates_for_ui(draft)
    if not candidates:
        print("No Roblox-like packages were detected.")
        print("Try: install Roblox or your clone APK, open it once, then try again.")
        print("Or use Add Package → manual entry as a fallback.")
        safe_io.press_enter()
        return draft

    current_entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    current_pkgs = {e["package"] for e in current_entries}

    _print_full_discovery_table(candidates, draft)
    new_candidates = [c for c in candidates if c.package not in current_pkgs]
    already_all = len(new_candidates) == 0
    if already_all:
        print("All detected packages are already configured.")
        safe_io.press_enter()
        return draft

    print(f"  A. Select all ({len(new_candidates)} new)")
    print("  B. Back (no change)")
    print()
    raw_in = safe_io.safe_prompt("Choose packages (e.g. 1,2 or A for all) [A]: ", default="a")
    if raw_in is None:
        print()
        return draft
    raw = raw_in.strip().lower() or "a"

    if raw in ("b", "back", "0"):
        return draft

    to_add_candidates: list = []
    if raw == "a":
        to_add_candidates = list(new_candidates)
    else:
        seen: set[str] = set()
        for part in [p.strip() for p in raw.split(",") if p.strip()]:
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(candidates):
                    c = candidates[idx]
                    if c.package not in current_pkgs and c.package not in seen:
                        seen.add(c.package)
                        to_add_candidates.append(c)

    if not to_add_candidates:
        print("No packages selected.")
        safe_io.press_enter()
        return draft

    to_add_entries = [
        _entry_for_package(c.package, current_entries, app_name=c.app_name)
        for c in to_add_candidates
    ]

    for entry in to_add_entries:
        if entry["package"] not in current_pkgs:
            current_entries.append(entry)
            current_pkgs.add(entry["package"])

    draft["roblox_packages"] = current_entries
    active = enabled_package_entries(draft)
    draft["roblox_package"] = active[0]["package"]
    draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
    draft = save_config(draft)
    draft = _bounded_post_add_username_detection(
        draft,
        [e["package"] for e in to_add_entries],
    )
    print(f"Saved {len(to_add_entries)} package(s).")
    return draft


def _package_menu_list(draft: dict[str, Any]) -> None:
    """Display all configured packages (tests / internal; not a submenu action)."""
    print()
    print("List Packages")
    entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    if not entries:
        print("  No Packages Configured.")
    else:
        print(f"  {'#':<3} {'Username':<20} Package")
        print(f"  {'-'*3} {'-'*20} {'-'*40}")
        for idx, entry in enumerate(entries, start=1):
            username = _package_username_display(entry)
            status = "" if entry.get("enabled", True) else " [Disabled]"
            print(f"  {idx:<3} {username:<20} {entry['package']}{status}")
    input("\nPress Enter to continue...")


def _config_menu_launch_link(draft: dict[str, Any]) -> dict[str, Any]:
    """Private URL submenu with Global and Separate modes."""
    if not _is_interactive():
        return draft
    while True:
        mode = validate_private_url_mode(draft.get("private_url_mode"))
        if mode == "global":
            global_url = str(draft.get("private_server_url") or draft.get("launch_url") or "")
            current_line = f"Current Mode: Global ({'Set' if global_url else 'Blank / App Only'})"
            items = [
                ("1", "Change Mode"),
                ("2", "Edit Global Private URL"),
                ("0", "Back"),
            ]
        else:
            current_line = "Current Mode: Separate"
            items = [
                ("1", "Change Mode"),
                ("2", "Edit Package URLs"),
                ("3", "Set Same URL For All Packages"),
                ("4", "Clear All Package URLs"),
                ("0", "Back"),
            ]
        termux_ui.print_submenu(
            "Private URL",
            items,
            current_lines=[current_line],
        )
        _llc = safe_io.safe_prompt(f"{termux_ui.choose_prompt('0')} ", default="0")
        if _llc is None:
            break
        choice = _llc.strip() or "0"
        if choice == "0":
            break
        if choice == "1":
            draft = _private_url_change_mode_menu(draft)
            draft = save_config(draft)
            termux_ui.print_success("Private URL Mode Saved")
        elif mode == "global" and choice == "2":
            _setup_global_private_url(draft)
            draft = save_config(draft)
            termux_ui.print_success("Global Private URL Saved")
        elif mode == "separate" and choice == "2":
            draft = _private_url_edit_package_urls(draft)
        elif mode == "separate" and choice == "3":
            draft = _private_url_set_same_for_all(draft)
        elif mode == "separate" and choice == "4":
            draft = _private_url_clear_all_package_urls(draft)
        else:
            termux_ui.print_invalid_option()
    return draft


def _private_url_change_mode_menu(draft: dict[str, Any]) -> dict[str, Any]:
    print()
    print("Private URL Mode")
    print()
    print("1. Global Private URL - One URL for all packages")
    print("2. Separate Private URL - Different URL per package")
    print("0. Back")
    raw = safe_io.safe_prompt("Choose [0]: ", default="0")
    choice = (raw or "0").strip() or "0"
    if choice == "0":
        return draft
    if choice == "2":
        old_mode = validate_private_url_mode(draft.get("private_url_mode"))
        draft["private_url_mode"] = "separate"
        if old_mode == "global" and str(draft.get("private_server_url") or "").strip():
            copy = _prompt_yes_no("Copy current Global Private URL to all packages?", False)
            if copy:
                url = str(draft.get("private_server_url") or "").strip()
                entries = [dict(e) for e in validate_package_entries(draft.get("roblox_packages"))]
                for entry in entries:
                    entry["private_server_url"] = url
                draft["roblox_packages"] = entries
        draft["launch_mode"] = "app"
        draft["launch_url"] = ""
        return draft
    if choice == "1":
        draft["private_url_mode"] = "global"
        _setup_global_private_url(draft)
        return draft
    termux_ui.print_invalid_option()
    return draft


def _private_url_edit_package_urls(draft: dict[str, Any]) -> dict[str, Any]:
    entries = [dict(e) for e in validate_package_entries(draft.get("roblox_packages"))]
    while True:
        print()
        print("Edit Package URLs")
        print()
        for idx, entry in enumerate(entries, start=1):
            status = "Set" if str(entry.get("private_server_url") or "").strip() else "Blank / App Only"
            print(f"{idx}. {entry['package']} - {status}")
        print("0. Back")
        raw = safe_io.safe_prompt("Choose [0]: ", default="0")
        choice = (raw or "0").strip() or "0"
        if choice == "0":
            break
        if not choice.isdigit() or not (1 <= int(choice) <= len(entries)):
            termux_ui.print_invalid_option()
            continue
        idx = int(choice) - 1
        entry = entries[idx]
        value = _private_url_prompt(str(entry.get("private_server_url") or ""))
        if value is not None:
            entry["private_server_url"] = value
            entries[idx] = entry
            draft["roblox_packages"] = entries
            draft["private_url_mode"] = "separate"
            draft = save_config(draft)
            termux_ui.print_success("Package Private URL Saved")
    return draft


def _private_url_set_same_for_all(draft: dict[str, Any]) -> dict[str, Any]:
    value = _private_url_prompt("", global_label=True)
    if value is None:
        return draft
    entries = [dict(e) for e in validate_package_entries(draft.get("roblox_packages"))]
    for entry in entries:
        entry["private_server_url"] = value
    draft["private_url_mode"] = "separate"
    draft["roblox_packages"] = entries
    draft = save_config(draft)
    termux_ui.print_success("Applied URL To All Packages")
    return draft


def _private_url_clear_all_package_urls(draft: dict[str, Any]) -> dict[str, Any]:
    if not _prompt_yes_no("Clear all package Private URLs?", False):
        print("Cancelled.")
        return draft
    entries = [dict(e) for e in validate_package_entries(draft.get("roblox_packages"))]
    for entry in entries:
        entry["private_server_url"] = ""
    draft["private_url_mode"] = "separate"
    draft["roblox_packages"] = entries
    draft = save_config(draft)
    termux_ui.print_success("Package Private URLs Cleared")
    return draft


def _config_menu_screen_mode(draft: dict[str, Any]) -> dict[str, Any]:
    """Legacy no-op kept for old callers; option is not reachable from UI."""
    draft["screen_mode"] = DEFAULT_SCREEN_MODE
    draft = save_config(draft)
    return draft


# ─── Package Key Config ───────────────────────────────────────────────────────
# These functions handle PER-PACKAGE keys written to each Roblox/package
# internal license file — NOT the DENG Tool license key.
#
# File path formula (probe p-52aeb6420f — ``Cache`` segment required):
#   /storage/emulated/0/Android/data/{package}/files/gloop/external/Internals/Cache/license
#
# This does NOT touch:
#   - DENG Tool license file / license server / Supabase / Discord panel
#   - any shared_prefs, databases, cookies, tokens, or login data


def _config_menu_key(draft: dict[str, Any]) -> dict[str, Any]:
    """Menu 4 package-key submenu."""
    if not _is_interactive():
        return draft
    from .config import enabled_package_entries

    entries = enabled_package_entries(draft)
    if not entries:
        print()
        print("No package(s) configured yet. Add package(s) first.")
        safe_io.press_enter()
        return draft
    if len(entries) == 1:
        return _package_key_menu_for_package(draft, entries[0]["package"])

    while True:
        print()
        print("Select Package For Package Key")
        print()
        for i, entry in enumerate(entries, 1):
            print(f"{i}. {entry['package']}")
        print("A. All Packages")
        print("0. Back")
        raw = safe_io.safe_prompt("Choose [0]: ", default="0")
        if raw is None:
            break
        choice = raw.strip() or "0"
        if choice == "0":
            break
        if choice.upper() == "A":
            draft = _package_key_menu_for_all_packages(draft, [e["package"] for e in entries])
            continue
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(entries):
                raise ValueError
        except (TypeError, ValueError):
            print("Please choose a package, A, or 0.")
            safe_io.press_enter()
        else:
            draft = _package_key_menu_for_package(draft, entries[idx]["package"])
    return draft


def _prompt_yes_no_capitalized(text: str) -> bool | None:
    """Kaeru-style Y/N prompt with first-letter capitalization."""
    while True:
        result = safe_io.safe_prompt(f"{termux_ui.CYAN}[?] {text}? (Y/N){termux_ui.RESET} ")
        if result is None:
            return None
        value = result.strip().upper()
        if not value:
            print("Please answer Y or N.")
            continue
        if value == "Y":
            return True
        if value == "N":
            return False
        print("Please answer Y or N.")


def _print_package_key_file_info(package: str) -> None:
    from .package_key import package_key_license_info

    info = package_key_license_info(package)
    print()
    print("Package Key File Info")
    print(f"Package: {info.get('package') or package}")
    print("File name: license")
    print(f"Type: {info.get('mime_type') or 'application/octet-stream'}")
    if not info.get("exists"):
        print("Package key file not found.")
        print(f"Full path: {info.get('path')}")
        print(f"FS path: {info.get('dir')}")
        print(f"FS type: {info.get('fs_type') or ''}")
        if info.get("error"):
            print(f"Error: {info.get('error')}")
        return
    print(f"Size: {info.get('size_bytes')} bytes")
    print(f"Last modification: {info.get('modified_iso') or ''}")
    print(f"Permissions: {info.get('permissions') or ''}")
    print(f"Full path: {info.get('path')}")
    print(f"FS path: {info.get('dir')}")
    print(f"FS type: {info.get('fs_type') or ''}")
    print(f"MD5: {info.get('md5') or ''}")


def _save_package_key_for_packages(
    draft: dict[str, Any], packages: list[str], key: str, *, save_global: bool
) -> dict[str, Any]:
    from .package_key import write_package_key_file

    for pkg in packages:
        result = write_package_key_file(pkg, key)
        if not result["success"]:
            err = result.get("error", "")
            print(f"Could not write package key for {pkg}: {err[:80]}")
    pkg_keys = dict(draft.get("package_keys") or {})
    if not isinstance(pkg_keys, dict):
        pkg_keys = {}
    if not isinstance(pkg_keys.get("per_package"), dict):
        pkg_keys["per_package"] = {}
    if save_global:
        pkg_keys["global"] = key
    else:
        for pkg in packages:
            pkg_keys["per_package"][pkg] = key
    draft["package_keys"] = pkg_keys
    return save_config(draft)


def _package_key_menu_for_package(draft: dict[str, Any], package: str) -> dict[str, Any]:
    return _package_key_menu_for_packages(draft, [package], package_label=package, save_global=False)


def _package_key_menu_for_all_packages(draft: dict[str, Any], packages: list[str]) -> dict[str, Any]:
    return _package_key_menu_for_packages(draft, packages, package_label="All Packages", save_global=True)


def _package_key_menu_for_packages(
    draft: dict[str, Any],
    packages: list[str],
    *,
    package_label: str,
    save_global: bool,
) -> dict[str, Any]:
    from .package_key import is_valid_package_key, mask_package_key, package_key_license_path

    while True:
        print()
        print("Package Key menu:")
        print()
        print("1. Enter / Update Package Key")
        print("2. Show Package Key File Info")
        print("3. Remove Saved Package Key")
        print("0. Back")
        raw = safe_io.safe_prompt("Choose [0]: ", default="0")
        if raw is None:
            break
        choice = raw.strip() or "0"
        if choice == "0":
            break
        if choice == "1":
            print()
            print(f"Package: {package_label}")
            print("Leave blank to cancel.")
            key = (safe_io.safe_prompt("Enter / Update Package Key: ", default="") or "").strip()
            if not key:
                print("Cancelled.")
                continue
            if not is_valid_package_key(key):
                print("Package key must start with FREE_ for this version.")
                safe_io.press_enter()
                continue
            draft = _save_package_key_for_packages(draft, packages, key, save_global=save_global)
            print(f"Package key saved: {mask_package_key(key)}")
            safe_io.press_enter()
        elif choice == "2":
            for pkg in packages:
                _print_package_key_file_info(pkg)
                if len(packages) == 1 and package_key_license_path(pkg):
                    print("Enter / Update Package Key from this menu to create it.")
            safe_io.press_enter()
        elif choice == "3":
            pkg_keys = dict(draft.get("package_keys") or {})
            per_pkg = pkg_keys.get("per_package") if isinstance(pkg_keys.get("per_package"), dict) else {}
            if save_global:
                pkg_keys["global"] = ""
                for pkg in packages:
                    per_pkg.pop(pkg, None)
            else:
                for pkg in packages:
                    per_pkg.pop(pkg, None)
            pkg_keys["per_package"] = per_pkg
            draft["package_keys"] = pkg_keys
            draft = save_config(draft)
            print("Saved package key removed.")
            safe_io.press_enter()
        else:
            print("Please choose 1, 2, 3, or 0.")
            safe_io.press_enter()
    return draft


def _resolve_per_package_key_display(draft: dict[str, Any], package: str) -> str:
    """Return a short display hint showing whether a package key is configured."""
    from .package_key import mask_package_key
    pkg_keys = draft.get("package_keys") or {}
    if not isinstance(pkg_keys, dict):
        return ""
    per_pkg = pkg_keys.get("per_package") or {}
    key = per_pkg.get(package) or ""
    if key:
        return f" [{mask_package_key(key)}]"
    global_key = pkg_keys.get("global") or ""
    if global_key:
        return f" [global: {mask_package_key(global_key)}]"
    return ""


def _config_menu_webhook(draft: dict[str, Any]) -> dict[str, Any]:
    """Webhook submenu: Mode, Interval, URL, Discord Mention, and test send."""
    if not _is_interactive():
        return draft
    while True:
        print()
        print(termux_ui.separator("-"))
        print("Webhook")
        print(termux_ui.separator("-"))
        url = draft.get("webhook_url", "") or ""
        interval = draft.get("webhook_interval_minutes", 5)
        mode = draft.get("webhook_mode", "none")
        mode_label = {"edit": "Edit", "new_post": "New Post", "none": "None"}.get(mode, "None")
        tag_id = str(draft.get("webhook_tag_user_id") or "").strip()
        tag_enabled = bool(draft.get("webhook_tag_enabled")) and bool(tag_id)
        print("Current Webhook:")
        if mode == "none":
            print("  Mode: None")
        else:
            print(f"  Mode: {mode_label}")
            print(f"  Interval: {interval}m")
        url_status = "configured" if url and mode != "none" else "not configured"
        print(f"  URL: {url_status}")
        print(f"  Discord Mention: {'Enabled' if tag_enabled else 'Disabled'}")
        print()
        print("1. Mode")
        print("2. Interval")
        print("3. URL")
        print("4. Discord Mention")
        print("5. Test Webhook Now")
        print("6. Back")
        print(termux_ui.separator("-"))
        _whc = safe_io.safe_prompt("Choose [6]: ", default="6")
        if _whc is None:
            break
        choice = _whc.strip() or "6"
        if choice in {"0", "6"}:
            break
        elif choice == "1":
            _config_webhook_mode(draft)
            draft = save_config(draft)
        elif choice == "2":
            _setup_webhook_interval(draft)
            draft = save_config(draft)
            print("Webhook Interval Saved.")
        elif choice == "3":
            _config_webhook_url(draft)
            draft = save_config(draft)
        elif choice == "4":
            _config_webhook_tag_discord(draft)
            draft = save_config(draft)
        elif choice == "5":
            _test_webhook_now(draft)
        else:
            print("Please choose 1-6.")
    return draft


def _config_webhook_tag_discord(draft: dict[str, Any]) -> None:
    """Discord Mention submenu: enable/disable Account Dead user mention."""
    while True:
        print()
        print("Discord Mention")
        enabled = bool(draft.get("webhook_tag_enabled"))
        tag_id = str(draft.get("webhook_tag_user_id") or "").strip()
        if enabled and tag_id:
            print(f"  Status: Enabled for <@{tag_id}>")
        else:
            print("  Status: Disabled")
        print()
        print("1. Enable")
        print("2. Disable")
        print("0. Back")
        choice = (_prompt("Choose", "0") or "0").strip()
        if choice == "0":
            break
        if choice == "1":
            raw = _prompt("Discord user ID (17-20 digits)", "").strip()
            try:
                tag_id = webhook.validate_discord_tag_user_id(raw)
            except ValueError as exc:
                print(f"Invalid Discord user ID: {exc}")
                continue
            draft["webhook_tag_enabled"] = True
            draft["webhook_tag_user_id"] = tag_id
            print(f"Discord Mention enabled for <@{tag_id}>")
            break
        if choice == "2":
            draft["webhook_tag_enabled"] = False
            draft["webhook_tag_user_id"] = ""
            print("Discord Mention disabled. Account Dead webhooks will not mention anyone.")
            break
        print("Please choose 1, 2, or 0.")


def _config_webhook_url(draft: dict[str, Any]) -> None:
    """Set or update the webhook URL. The full URL is never printed."""
    print()
    print("Webhook URL")
    print("Enter a new URL, or leave blank to keep the current value.")
    current = draft.get("webhook_url", "") or ""
    if current:
        print(f"Current: {webhook.mask_webhook_url(current)}")
    else:
        print("Current: Not Set")
    value = _prompt("Discord Webhook URL", "").strip()
    if not value:
        print("Skipped.")
        return
    try:
        draft["webhook_url"] = webhook.validate_webhook_url(value)
        if value != current:
            draft["webhook_last_message_id"] = ""
        print("Webhook URL Saved.")
    except ValueError as exc:
        print(f"Webhook URL Is Not Valid: {exc}")


def _config_webhook_mode(draft: dict[str, Any]) -> None:
    """Set the webhook operating mode."""
    print()
    print("Webhook Mode")
    print("1. Edit")
    print("2. New Post")
    print("3. None")
    current_mode = draft.get("webhook_mode", "none")
    default = {"edit": "1", "new_post": "2", "none": "3"}.get(current_mode, "3")
    _wmc = safe_io.safe_prompt(f"Choose [{default}]: ", default=default)
    choice = (_wmc or default).strip() or default
    if choice == "3":
        draft["webhook_enabled"] = False
        draft["webhook_mode"] = "none"
        draft["webhook_url"] = ""
        draft["webhook_last_message_id"] = ""
    elif choice == "2":
        draft["webhook_enabled"] = True
        draft["webhook_mode"] = "new_post"
    elif choice == "1":
        draft["webhook_enabled"] = True
        draft["webhook_mode"] = "edit"
    else:
        print("Unknown choice. Keeping current mode.")


def _test_webhook_now(draft: dict[str, Any]) -> None:
    """Installed-menu action using the same production sender as the reporter."""
    try:
        ok, result = webhook.send_periodic_status(draft, supervisor_snapshot=[], app_stats={})
        if ok:
            print(f"Webhook test sent: {result}")
        else:
            print(f"Webhook test failed: {result}")
    except Exception as exc:  # noqa: BLE001
        print(f"Webhook test failed: {type(exc).__name__}")


def _auto_execute_package_names(draft: dict[str, Any]) -> list[str]:
    if not draft.get("roblox_packages"):
        return []
    try:
        return [entry["package"] for entry in enabled_package_entries(draft)]
    except ConfigError:
        return []


def _auto_execute_choose_executor() -> str | None:
    print()
    print("Executor")
    choices = auto_execute.executor_choices()
    for idx, spec in enumerate(choices, start=1):
        print(f"{idx}. {spec.label}")
    print("0. Back")
    choice = safe_io.safe_prompt("Choose [0]: ", default="0")
    if choice is None:
        return None
    choice = choice.strip() or "0"
    if choice == "0":
        return None
    try:
        idx = int(choice)
    except ValueError:
        print("Please choose a listed executor.")
        return None
    if 1 <= idx <= len(choices):
        return choices[idx - 1].key
    print("Please choose a listed executor.")
    return None


def _auto_execute_failure_reason(row: dict[str, Any]) -> str:
    reason = str(row.get("error") or "unknown error").strip()
    if not reason:
        reason = "unknown error"
    return reason.split(":", 1)[0].strip() or reason


def _print_auto_execute_failures(results: list[dict[str, Any]]) -> None:
    failures = [row for row in results if not row.get("success")]
    if not failures:
        return
    print("Failed:")
    for row in failures:
        print(f"- {row.get('package') or 'unknown'}: {_auto_execute_failure_reason(row)}")


def _print_auto_execute_write_results(filename: str, results: list[dict[str, Any]]) -> None:
    total = len(results)
    ok = sum(1 for row in results if row.get("success"))
    if ok == total:
        print(f"Added {filename} to {total} package{'s' if total != 1 else ''}.")
    else:
        print(f"Added {filename} to {ok}/{total} packages.")
    _print_auto_execute_failures(results)


def _print_auto_execute_remove_results(filename: str, results: list[dict[str, Any]]) -> None:
    total = len(results)
    ok = sum(1 for row in results if row.get("success"))
    if ok == total:
        print(f"Removed {filename} from {total} package{'s' if total != 1 else ''}.")
    else:
        print(f"Removed {filename} from {ok}/{total} packages.")
    _print_auto_execute_failures(results)


def _print_auto_execute_remove_all_results(results: list[dict[str, Any]]) -> None:
    total = len(results)
    ok = sum(1 for row in results if row.get("success"))
    deleted = sum(int(row.get("deleted_count") or 0) for row in results if row.get("success"))
    if ok == total:
        print(f"Removed {deleted} DENG-managed script file{'s' if deleted != 1 else ''} from {total} package{'s' if total != 1 else ''}.")
    else:
        print(f"Removed DENG-managed scripts from {ok}/{total} packages.")
    _print_auto_execute_failures(results)


def _print_auto_execute_inventory(draft: dict[str, Any], *, executor: str = "delta") -> None:
    packages = _auto_execute_package_names(draft)
    inventory = auto_execute.managed_filenames_by_package(packages, executor=executor) if packages else {}
    filenames = sorted({name for names in inventory.values() for name in names})
    print(f"Scripts set: {len(filenames)}")
    for filename in filenames:
        print(f"- {filename}")
    if auto_execute.filenames_mismatch(inventory):
        print("Warning: scripts differ across packages. Use Remove All Scripts to reset.")


def _inject_detection_scripts(
    packages: list[str],
    *,
    executor: str = "delta",
    quiet: bool = False,
) -> None:
    """Drop the DENG detection bootstrap (``deng.txt``) next to user scripts.

    Best-effort: a failure (e.g. on a non-Android dev host where the storage
    path does not exist) must never break the calling flow.
    """
    if not packages:
        return
    try:
        results = auto_execute.write_detection_script(list(packages), executor=executor)
    except Exception:  # noqa: BLE001
        return
    ok = sum(1 for r in results if r.get("success"))
    if ok and not quiet:
        print(f"[+] Detection script installed ({ok}/{len(results)} package(s)).")


def _config_auto_execute_add(draft: dict[str, Any]) -> None:
    packages = _auto_execute_package_names(draft)
    if not packages:
        print("No Roblox packages configured. Configure packages first.")
        return
    executor = _auto_execute_choose_executor()
    if not executor:
        return
    script_no = 1
    detection_injected = False
    while True:
        answer = safe_io.safe_prompt(f"Add script #{script_no}? Y/N: ", default="N")
        if answer is None:
            return
        if answer.strip().lower() not in {"y", "yes"}:
            return
        script = safe_io.safe_prompt("Paste/type script content: ", allow_blank=True)
        if script is None:
            return
        filename = auto_execute.next_managed_filename(packages, executor=executor)
        results = auto_execute.write_script_to_packages(packages, script, executor=executor, filename=filename)
        _print_auto_execute_write_results(filename, results)
        if not detection_injected:
            # Whenever the user installs their own script we also install our
            # in-game detection bootstrap so the watchdog gets fast, reliable
            # truth via the loopback push channel.
            _inject_detection_scripts(packages, executor=executor)
            detection_injected = True
        script_no += 1


def _config_auto_execute_remove(draft: dict[str, Any]) -> None:
    packages = _auto_execute_package_names(draft)
    if not packages:
        print("No Roblox packages configured. Configure packages first.")
        return
    executor = _auto_execute_choose_executor()
    if not executor:
        return
    filenames = auto_execute.list_managed_filenames(packages, executor=executor)
    if not filenames:
        print("No DENG Auto Execute scripts found.")
        return
    print()
    print("DENG Auto Execute Scripts")
    for idx, filename in enumerate(filenames, start=1):
        print(f"{idx}. {filename}")
    print("0. Back")
    choice = safe_io.safe_prompt("Choose [0]: ", default="0")
    if choice is None:
        return
    try:
        idx = int((choice or "0").strip() or "0")
    except ValueError:
        print("Please choose a listed script.")
        return
    if idx == 0:
        return
    if not 1 <= idx <= len(filenames):
        print("Please choose a listed script.")
        return
    results = auto_execute.remove_script_from_packages(packages, filenames[idx - 1], executor=executor)
    _print_auto_execute_remove_results(filenames[idx - 1], results)


def _config_auto_execute_remove_all(draft: dict[str, Any]) -> None:
    packages = _auto_execute_package_names(draft)
    if not packages:
        print("No Roblox packages configured. Configure packages first.")
        return
    executor = _auto_execute_choose_executor()
    if not executor:
        return
    if not _prompt_yes_no("Remove all DENG Auto Execute scripts from all configured packages?", False):
        return
    results = auto_execute.remove_all_scripts_from_packages(packages, executor=executor)
    _print_auto_execute_remove_all_results(results)


def _config_menu_auto_execute(draft: dict[str, Any]) -> dict[str, Any]:
    """Auto Execute submenu: file management only; never executes scripts."""
    if not _is_interactive():
        return draft
    while True:
        print()
        print(termux_ui.separator("-"))
        print("Auto Execute")
        _print_auto_execute_inventory(draft)
        print()
        print(termux_ui.separator("-"))
        print("1. Add Script")
        print("2. Remove Script")
        print("3. Remove All Scripts")
        print("0. Back")
        print(termux_ui.separator("-"))
        choice = safe_io.safe_prompt("Choose [0]: ", default="0")
        if choice is None:
            break
        choice = choice.strip() or "0"
        if choice == "0":
            break
        if choice == "1":
            _config_auto_execute_add(draft)
        elif choice == "2":
            _config_auto_execute_remove(draft)
        elif choice == "3":
            _config_auto_execute_remove_all(draft)
        else:
            print("Please choose 1-3 or 0.")
    return draft


def _test_webhook(draft: dict[str, Any]) -> None:
    """Send a test message to the configured webhook. URL is masked in all output."""
    from . import safe_http as _safe_http  # noqa: PLC0415
    url = draft.get("webhook_url", "") or ""
    if not url:
        print("No Webhook URL Is Set. Set One First.")
        safe_io.press_enter()
        return
    masked = webhook.mask_webhook_url(url)
    print(f"Sending Test Webhook To {masked}...")
    try:
        payload = {"content": "DENG Tool: Rejoin — Test Webhook"}
        _safe_http.post_json(url, payload, timeout=10)
        print("Test Webhook Sent Successfully.")
    except _safe_http.SafeHttpStatusError as exc:
        print(f"Webhook Returned Status {exc.status_code}.")
    except _safe_http.SafeHttpNetworkError as exc:
        print(f"Test Webhook Failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"Test Webhook Failed: {exc}")
    safe_io.press_enter()


def _config_menu_yescaptcha(draft: dict[str, Any]) -> dict[str, Any]:
    """YesCaptcha submenu: Set / Clear API key, Check Balance / Points."""
    if not _is_interactive():
        return draft
    while True:
        print()
        print(termux_ui.separator("-"))
        print("YesCaptcha")
        print(termux_ui.separator("-"))
        current_key = draft.get("yescaptcha_key", "") or ""
        if current_key:
            masked = current_key[:4] + "..." if len(current_key) > 4 else "****"
            print(f"API Key: Configured ({masked})")
        else:
            print("YesCaptcha API Key Not Set.")
        print()
        print("1. Set YesCaptcha API Key")
        print("2. Clear YesCaptcha API Key")
        print("3. Check Balance / Points")
        print("0. Back")
        print(termux_ui.separator("-"))
        _yc = safe_io.safe_prompt("Choose [0]: ", default="0")
        if _yc is None:
            break
        choice = _yc.strip() or "0"
        if choice == "0":
            break
        elif choice == "1":
            _config_yescaptcha_set(draft)
            draft = save_config(draft)
        elif choice == "2":
            draft["yescaptcha_key"] = ""
            draft = save_config(draft)
            print("YesCaptcha API Key Cleared.")
        elif choice == "3":
            _config_yescaptcha_balance(draft)
        else:
            print("Please choose 1-3 or 0.")
    return draft


def _config_yescaptcha_set(draft: dict[str, Any]) -> None:
    """Prompt for YesCaptcha API key. The key is never printed in full."""
    print()
    print("Set YesCaptcha API Key")
    print("Leave blank to skip.")
    raw = _prompt("YesCaptcha API Key (blank to skip)", "").strip()
    if not raw:
        print("Skipped.")
        return
    draft["yescaptcha_key"] = raw[:256]
    print("YesCaptcha API Key Saved.")


def _config_yescaptcha_balance(draft: dict[str, Any]) -> None:
    """Check YesCaptcha account balance / points. The API key is not exposed in output."""
    key = draft.get("yescaptcha_key", "") or ""
    if not key:
        print("YesCaptcha API Key Not Set.")
        safe_io.press_enter()
        return
    print("Checking Balance...")
    try:
        from . import captcha as _captcha
        balance = _captcha.get_balance(key)
        print(f"Balance / Points: {balance}")
    except Exception as exc:  # noqa: BLE001
        print(f"Balance Check Failed: {exc}")
    safe_io.press_enter()


# ─── Setup / Config wizards ───────────────────────────────────────────────────

def _run_guided_config_menu(config_data: dict[str, Any], args: argparse.Namespace, *, title: str) -> tuple[dict[str, Any] | None, bool]:
    """Backward-compatible entrypoint for older setup callers."""
    return _run_edit_config_menu(config_data, args)


def _run_first_time_setup_wizard(config_data: dict[str, Any], args: argparse.Namespace, *, start_after_save: bool = False) -> tuple[dict[str, Any] | None, bool]:
    draft = _refresh_detected_fields(dict(config_data))
    if not _is_interactive():
        print_banner(use_color=not args.no_color)
        print("First Time Setup Config")
        print()
        print("This will prepare your device for DENG Tool: Rejoin.")
        print("You will set Roblox packages and optional Private URL, then save.")
        print("Package detection scans installed apps; manual entry is fallback.")
        print("Usernames are display-only in the Start table — Unknown is OK.")
        print()
        print("Run this command in interactive Termux to complete setup.")
        print()
        _print_config_summary(draft)
        return draft, False

    print_banner(use_color=True)
    termux_ui.header("First Time Setup Config")
    print("This Will Prepare Your Device For DENG Tool: Rejoin.")
    print()
    print("You will set:")
    print("  1. Roblox package / clone app (pick from detection, or manual fallback)")
    print("  2. Private URL")
    print("  3. Webhook (optional)")
    print("  4. Auto Execute (optional)")
    print()
    print("Package detection:")
    print("  The tool scans installed Roblox apps against safe hints. Pick from the table.")
    print("  Manual package entry is only a fallback if nothing is found.")
    print()
    print("Step 1 of 2: Packages")
    packages, hints = _choose_packages_menu(
        list(draft.get("roblox_packages") or [package_entry(draft.get("roblox_package", DEFAULT_ROBLOX_PACKAGE), "", True, "not_set")]),
        list(draft.get("package_detection_hints") or DEFAULT_ROBLOX_PACKAGE_HINTS),
        draft,
    )
    draft["roblox_packages"] = packages
    draft["package_detection_hints"] = hints
    active_entries = enabled_package_entries(draft)
    draft["roblox_package"] = active_entries[0]["package"]
    draft["selected_package_mode"] = "multiple" if len(active_entries) > 1 else "single"
    draft["screen_mode"] = DEFAULT_SCREEN_MODE
    print("\nStep 2 of 2: Private URL")
    _setup_launch_link(draft, allow_back=False)
    print("\nWebhook (optional)")
    _setup_webhook(draft)
    print("\nAuto Execute (optional)")
    _config_menu_auto_execute(draft)
    draft["first_setup_completed"] = True
    try:
        saved = save_config(draft)
    except ConfigError as exc:
        termux_ui.print_error(f"Setup Could Not Be Saved: {exc}")
        return None, False
    termux_ui.print_success("First-Time Setup Complete")
    _print_public_setup_confirmation(saved)
    if start_after_save or _prompt_yes_no("Start DENG now?", True):
        cmd_start(args)
    return saved, True


def _run_edit_config_menu(config_data: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any] | None, bool]:
    draft = _refresh_detected_fields(dict(config_data))
    if not _is_interactive():
        print_banner(use_color=True)
        termux_ui.print_config_menu()
        print("\nCurrent settings:")
        _print_config_summary(draft)
        return draft, False

    # Print banner once before the loop; do NOT reprint inside the loop to
    # prevent the logo from appearing multiple times as the user navigates.
    print_banner(use_color=True)
    with safe_io.tty_session():
        while True:
            termux_ui.print_config_menu()
            choice = safe_io.safe_prompt(f"{termux_ui.choose_prompt('0')} ", default="0")
            if choice is None:
                print("\nNo interactive input was available. Run this command in Termux to edit settings.")
                print("\nCurrent settings:")
                _print_config_summary(draft)
                return draft, False
            choice = choice.strip() or "0"
            if choice == "0":
                return draft, True
            if choice == "1":
                draft = _config_menu_package(draft)
            elif choice == "2":
                draft = _config_menu_launch_link(draft)
            elif choice == "3":
                draft = _config_menu_webhook(draft)
            elif choice == "4":
                draft = _config_menu_auto_execute(draft)
            else:
                termux_ui.print_invalid_option()
                safe_io.press_enter()


def cmd_setup(args: argparse.Namespace) -> int:
    ensure_app_dirs()
    db.init_db(DB_PATH)

    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()
    saved, did_save = _run_first_time_setup_wizard(cfg, args)
    if saved is None:
        return 0
    if not did_save:
        return 0

    if saved.get("termux_boot_enabled"):
        try:
            _write_termux_boot_script()
            print(f"Termux:Boot script created: {TERMUX_BOOT_SCRIPT}")
            if not android.package_installed("com.termux.boot"):
                print("Termux:Boot app was not detected. Install Termux:Boot, open it once, then Android will run this script after boot.")
        except OSError as exc:
            print(f"Warning: could not create Termux:Boot script: {exc}")

    created_launchers = create_market_launchers()
    if created_launchers:
        print("Launcher files:")
        for path in created_launchers:
            print(f"  {path}")

    print("\nDoctor report:")
    print_doctor(run_doctor(saved))
    print("\nNext commands:")
    print("  python agent/deng_tool_rejoin.py --once")
    print("  python agent/deng_tool_rejoin.py --start")
    print("  python agent/deng_tool_rejoin.py --status")
    print("  deng-rejoin")
    return 0


def cmd_first_setup(args: argparse.Namespace) -> int:
    ensure_app_dirs()
    db.init_db(DB_PATH)
    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()
    _run_first_time_setup_wizard(cfg, args)
    return 0


_LAYOUT_RESET_INSTRUCTIONS = """
Display Recovery — Fix sideways / black-bar home screen
=======================================================

If your home screen shows portrait UI squeezed in the middle with black
bars on the sides, or everything looks forced landscape:

Method 1 — One command (Termux, recommended):
  deng-rejoin doctor reset

Method 2 — Manual Termux commands:
  wm size reset
  wm density reset
  wm overscan reset
  settings put system accelerometer_rotation 1
  cmd window set-fix-to-user-rotation disabled
  cmd window set-user-rotation free
  settings put system user_rotation 0

Method 3 — Android Settings:
  Settings → Display → turn Auto-rotate ON, then rotate phone to portrait.

Method 4 — ADB (from a PC):
  adb shell wm size reset
  adb shell wm density reset
  adb shell settings put system accelerometer_rotation 1
  adb shell cmd window set-fix-to-user-rotation disabled
  adb shell settings put system user_rotation 0

After restoring:
  1. Press Home — launcher should fill the screen normally.
  2. Restart Termux if the terminal is still sideways.
  3. Run deng-rejoin start again — Roblox windows stay landscape; home UI is no longer forced.
""".strip()


def _packages_from_cfg(cfg: dict[str, Any] | None) -> list[str]:
    if not cfg:
        return []
    try:
        from .config import enabled_package_entries
        return [e["package"] for e in enabled_package_entries(cfg)]
    except Exception:  # noqa: BLE001
        return []


def _cmd_doctor_layout(cfg: dict[str, Any] | None, use_color: bool) -> int:
    """Internal diagnostic: compute, validate and apply window block layout.

    Hidden from public menu.  Concise terminal output, full details in log.
    """
    from .window_layout import (
        calculate_split_layout,
        detect_display_info,
        validate_layout_rects,
        OUTER_MARGIN,
        TERMUX_LOG_FRACTION,
    )
    from .window_apply import apply_window_layout

    print("Layout Diagnostic")
    print()

    disp = detect_display_info()
    print(f"  Display: {disp.width}x{disp.height} px  density={disp.density}")

    left_end = round(disp.width * TERMUX_LOG_FRACTION)
    pane_x0 = left_end + OUTER_MARGIN
    pane_y0 = OUTER_MARGIN
    pane_x1 = disp.width - OUTER_MARGIN
    pane_y1 = disp.height - OUTER_MARGIN

    packages = _packages_from_cfg(cfg)
    if not packages:
        print("  No packages configured.  Using example packages for preview.")
        packages = ["com.roblox.client", "com.roblox.client2"]

    rects = calculate_split_layout(packages, disp.width, disp.height)
    print(f"  Packages: {len(rects)}")
    for i, r in enumerate(rects, 1):
        w, h = r.win_w, r.win_h
        ratio = (w / h) if h else 0
        print(f"  [{i}] {r.package[:36]}  {w}x{h}  ratio={ratio:.2f}")

    errors = validate_layout_rects(rects, pane_x0, pane_y0, pane_x1, pane_y1)
    if errors:
        print(f"  Validation: FAIL ({len(errors)} issue(s) — see log)")
        for e in errors[:3]:
            print(f"    - {e}")
        return 1
    print("  Validation: PASS (landscape, no-touch, no-overlap, in-pane)")

    # Apply (only if we have a config)
    if cfg:
        results = apply_window_layout(rects, verify_after=True, retries=1)
        applied = sum(1 for r in results if r.final_ok)
        print(f"  Apply: {applied}/{len(results)} package(s) verified")
        for r in results:
            mark = "OK" if r.final_ok else "WARN"
            print(f"    [{mark}] {r.package[:36]}: {r.detail}")

    return 0


def _cmd_doctor_root_state(cfg: dict[str, Any] | None) -> int:
    """Internal diagnostic: show process/task/window evidence for each package."""
    print("Root State Diagnostic")
    print()

    root_info = android.detect_root()
    print(f"  Root: {'available' if root_info.available else 'unavailable'}"
          f" ({root_info.tool or 'none'})")

    packages = _packages_from_cfg(cfg)
    if not packages:
        print("  No packages configured.")
        return 0

    for pkg in packages:
        evidence = android.get_package_alive_evidence(pkg)
        fg = android.current_foreground_package()
        is_fg = (fg == pkg)
        if evidence["alive"]:
            inferred = "Online / Background"
        else:
            inferred = "Offline"
        print(
            f"  {pkg[:36]:<36}  "
            f"proc={evidence['running']} root_proc={evidence['root_running']} "
            f"task={evidence['task']} win={evidence['window']} "
            f"fg={is_fg}  -> {inferred}"
        )

    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """List detected Roblox packages with honest username scan results."""
    from . import launch_verify

    pre_err = launch_verify.root_preflight_error()
    if pre_err:
        print(pre_err)
        return 1
    cfg = None
    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()
    candidates = _gather_roblox_candidates_for_ui(cfg)
    if not candidates:
        print("No Roblox-like packages detected.")
        print("Install/open a Roblox clone once, then retry.")
        return 1
    _print_full_discovery_table(candidates, cfg)
    # Scanning also (re)installs the in-game detection bootstrap into every
    # configured package so detection works even before the user adds their
    # own auto-exec script.
    try:
        _inject_detection_scripts(
            auto_execute.configured_package_names(cfg),
            executor="delta",
        )
    except Exception:  # noqa: BLE001
        pass
    return 0


def cmd_list_packages(args: argparse.Namespace) -> int:
    from . import package_mapping as _pm

    cfg = load_config()
    rows = _pm.list_mapped_packages(cfg)
    print(f"{'Username':<18} {'Source':<16} Package")
    print(f"{'-'*18} {'-'*16} {'-'*40}")
    for row in rows:
        print(f"{row['username']:<18} {row['source']:<16} {row['package']}")
    return 0


def cmd_map(args: argparse.Namespace) -> int:
    from . import package_mapping as _pm

    rest = list(getattr(args, "extra_args", []) or [])
    if len(rest) < 2:
        print("Usage: deng-rejoin map <package> <username>")
        return 1
    package, username = rest[0], " ".join(rest[1:]).strip()
    try:
        _pm.map_package_username(package, username)
    except Exception as exc:  # noqa: BLE001
        print(f"map failed: {exc}")
        return 1
    print(f"mapped {package} -> {username}")
    return 0


def cmd_unmap(args: argparse.Namespace) -> int:
    from . import package_mapping as _pm

    rest = list(getattr(args, "extra_args", []) or [])
    if not rest:
        print("Usage: deng-rejoin unmap <package>")
        return 1
    try:
        _pm.unmap_package(rest[0])
    except Exception as exc:  # noqa: BLE001
        print(f"unmap failed: {exc}")
        return 1
    print(f"unmapped {rest[0]}")
    return 0


def cmd_launch(args: argparse.Namespace) -> int:
    from . import launch_verify

    rest = list(getattr(args, "extra_args", []) or [])
    if not rest:
        print("Usage: deng-rejoin launch <package>")
        return 1
    package = rest[0].strip()
    pre_err = launch_verify.root_preflight_error()
    if pre_err:
        print(pre_err)
        return 1
    try:
        validate_package_name(package)
    except Exception as exc:  # noqa: BLE001
        print(f"invalid package: {exc}")
        return 1
    if not android.package_installed(package):
        print(f"package not installed: {package}")
        return 1
    result, method = launch_verify.launch_package_root(package)
    verification = launch_verify.verify_launch(
        package,
        launch_result=result,
        launch_method=method,
        wait_seconds=20.0,
    )
    for line in verification.summary_lines():
        print(line)
    if verification.success:
        print("Launch verified.")
        return 0
    print("Launch failed.")
    return 1


def cmd_selftest(args: argparse.Namespace) -> int:
    from . import selftest as _selftest

    rest = list(getattr(args, "extra_args", []) or [])
    package = ""
    first = False
    kill_relaunch = False
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--package" and i + 1 < len(rest):
            package = rest[i + 1].strip()
            i += 2
            continue
        if tok == "--first":
            first = True
            i += 1
            continue
        if tok == "--kill-relaunch":
            kill_relaunch = True
            i += 1
            continue
        if tok.startswith("com."):
            package = tok
        i += 1
    upload = bool(getattr(args, "upload", False))
    summary_mode = not bool(getattr(args, "probe_full", False))
    result = _selftest.run_selftest(
        package=package,
        first=first,
        upload=upload,
        summary_probe=summary_mode,
        kill_relaunch=kill_relaunch,
    )
    _selftest.print_selftest_report(result)
    return 0 if result.ok else 1


def cmd_state(args: argparse.Namespace) -> int:
    from . import package_state as _ps
    from . import package_username as _pu
    from .config import enabled_package_entries, load_config
    from . import root_access

    pre = root_access.root_required_preflight()
    if not pre.ok:
        print(pre.public_error())
        return 1
    try:
        cfg = load_config()
    except Exception as exc:  # noqa: BLE001
        print(f"config error: {exc}")
        return 1
    packages = [str(e.get("package") or "") for e in enabled_package_entries(cfg)]
    if not packages:
        print("no enabled packages configured")
        return 1
    states = _ps.scan_all_package_states_root(packages)
    usernames = _pu.scan_all_username_displays(packages)
    print("package | username | account_status | state | root_alive | foreground | last_launch_age | reason")
    for pkg in packages:
        st = states.get(pkg)
        un = usernames.get(pkg)
        if not st or not un:
            continue
        age = "" if st.last_launch_age is None else str(st.last_launch_age)
        print(
            f"{pkg} | {un.username_display} | {un.account_status} | {st.state} | "
            f"{str(st.root_alive).lower()} | {str(st.foreground).lower()} | {age} | {st.reason}"
        )
    return 0


def _cmd_doctor_ram(cfg: dict[str, Any] | None) -> int:
    """Print PSS-based RAM report (hidden ``doctor ram`` subcommand)."""
    from .android_memory import build_ram_report_text
    from .config import enabled_package_entries

    packages: list[str] = []
    if cfg:
        packages = [str(e.get("package") or "") for e in enabled_package_entries(cfg)]
        packages = [p for p in packages if p]
    print(build_ram_report_text(packages))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Doctor command (internal).  Public users normally do NOT see this output.

    Subcommands (hidden from public menu):
      deng-rejoin doctor layout       — test landscape block layout
      deng-rejoin doctor root-state   — show process/task/window evidence
      deng-rejoin doctor reset        — print sideways-screen recovery steps
      deng-rejoin --layout-test       — same as `doctor layout`
      deng-rejoin --root-state        — same as `doctor root-state`
    """
    use_color = not args.no_color

    # Silence internal loggers in case this is called outside main()
    try:
        from .logger import silence_public_loggers
        silence_public_loggers()
    except Exception:  # noqa: BLE001
        pass

    # Subcommand routing
    if getattr(args, "layout_test", False):
        cfg = None
        try:
            cfg = load_config()
        except Exception:  # noqa: BLE001
            pass
        return _cmd_doctor_layout(cfg, use_color)

    if getattr(args, "root_state", False):
        cfg = None
        try:
            cfg = load_config()
        except Exception:  # noqa: BLE001
            pass
        return _cmd_doctor_root_state(cfg)

    if getattr(args, "layout_reset", False):
        if android.is_termux():
            try:
                restore_result = android.restore_display_defaults(portrait=True)
                print("Display restore applied on this device.")
                for step in restore_result.get("applied", []):
                    label = step.get("step", "?")
                    ok = bool(step.get("ok"))
                    print(f"  {label}: {'OK' if ok else 'FAIL'}")
                display = restore_result.get("display", {})
                if isinstance(display, dict):
                    print(
                        f"  orientation: {display.get('orientation', 'unknown')} "
                        f"({display.get('width', 0)}x{display.get('height', 0)})"
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"Display restore error: {exc}")
        print(_LAYOUT_RESET_INSTRUCTIONS)
        return 0

    if getattr(args, "doctor_versions", False):
        return _cmd_doctor_versions()

    if getattr(args, "doctor_ram", False):
        ram_cfg: dict[str, Any] | None = None
        try:
            ram_cfg = load_config()
        except ConfigError:
            ram_cfg = None
        return _cmd_doctor_ram(ram_cfg)

    doctor_package = str(getattr(args, "doctor_package", "") or "").strip()
    if doctor_package:
        from . import launch_verify

        for line in launch_verify.doctor_package_report(doctor_package):
            print(line)
        return 0

    # Default doctor: show standard health check
    print_banner(use_color=use_color)
    cfg = None
    try:
        cfg = load_config()
        root_info = android.detect_root()
        cfg["root_available"] = root_info.available
        cfg["android_release"] = get_android_release()
        cfg["android_sdk"] = get_android_sdk()
        cfg["download_dir"] = detect_public_download_dir()
        save_config(cfg)
    except ConfigError:
        pass
    items = run_doctor(cfg)
    print_doctor(items)
    return 1 if any(item.status == "FAIL" for item in items) else 0


def _print_latest(label: str, row: dict[str, Any] | None) -> None:
    if not row:
        print(f"  {label}: none")
        return
    ts = row.get("ts", "unknown time")
    if "status" in row:
        print(f"  {label}: {row.get('status')} at {ts}")
        return
    if "success" in row:
        result = "success" if row.get("success") else "failed"
        package = row.get("package", "unknown package")
        mode = _launch_mode_label(str(row.get("launch_mode", "")))
        url = _safe_url_label(str(row.get("masked_launch_url") or ""))
        print(f"  {label}: {result} at {ts}")
        print(f"    Package: {package}")
        print(f"    Launch mode: {mode}")
        if url != "Not set":
            print(f"    URL: {url}")
        if row.get("error"):
            print(f"    Error: {mask_urls_in_text(str(row.get('error')))}")
        return
    message = row.get("message") or row.get("type") or "event"
    print(f"  {label}: {mask_urls_in_text(str(message))} at {ts}")


def cmd_status(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()
    root_info = android.detect_root()
    cfg["root_available"] = root_info.available
    cfg["android_release"] = get_android_release()
    cfg["android_sdk"] = get_android_sdk()
    cfg["download_dir"] = detect_public_download_dir()
    save_config(cfg)
    safe = safe_config_view(cfg)
    platform_info = get_platform_info()

    manager = LockManager()
    running = manager.is_running()
    print("Device")
    print(f"  Name: {safe['device_name']}")
    print(f"  Install path: {CONFIG_PATH.parent}")
    print()
    print("Android")
    print(f"  Release: {platform_info.android_release}")
    print(f"  SDK: {platform_info.android_sdk}")
    print(f"  Download folder: {platform_info.download_dir or 'Not detected'}")
    print(f"  Root available: {'Yes' if root_info.available else 'No'} ({root_info.tool or 'no tool'})")
    print()
    print("Roblox")
    entries = enabled_package_entries(cfg)
    print("  Selected packages:")
    for idx, entry in enumerate(entries, start=1):
        print(f"    {idx}. {_account_username_value(entry):<16} {entry['package']}")
    print(f"  Detection hints: {_hint_list_label(cfg['package_detection_hints'])}")
    mode = validate_private_url_mode(cfg.get("private_url_mode"))
    print(f"  Private URL mode: {'Global' if mode == 'global' else 'Separate'}")
    if mode == "global":
        print(f"  Global Private URL: {_safe_url_label(cfg.get('private_server_url'))}")
    else:
        for idx, entry in enumerate(entries, start=1):
            status = "Set" if str(entry.get("private_server_url") or "").strip() else "Blank / App Only"
            print(f"  Package {idx} URL: {status}")
    print()
    print("Rejoin Settings")
    print(f"  First setup completed: {'Yes' if cfg['first_setup_completed'] else 'No'}")
    print(f"  Auto rejoin: {_yes_no(cfg['auto_rejoin_enabled'])}")
    print(f"  Reconnect delay: {cfg['reconnect_delay_seconds']} seconds")
    print(f"  Health check interval: {cfg['health_check_interval_seconds']} seconds")
    print(f"  Root mode: {_yes_no(cfg['root_mode_enabled'])}")
    print()
    print("Webhook")
    print(f"  Status updates: {_yes_no(cfg['webhook_enabled'])}")
    if cfg["webhook_enabled"]:
        print(f"  Mode: {cfg['webhook_mode']}")
        print(f"  Interval: {cfg['webhook_interval_minutes']} minutes")
        print(f"  URL: {safe.get('webhook_url') or 'Not set'}")
        tags = cfg.get("webhook_tags") or []
        if tags:
            print(f"  Tags: {', '.join(tags)}")
    else:
        print("  Interval: Disabled")
    print()
    print("License")
    print(f"  Key: {mask_license_key(cfg.get('license_key', ''))}")
    print(f"  YesCaptcha: {'Configured' if cfg.get('yescaptcha_key') else 'Not set'}")
    print()
    print("Window Layout")
    print("  Auto resize: Automatic")
    display = window_layout.detect_display_info()
    print(f"  Detected display: {display.width}x{display.height} density={display.density}")
    print()
    print("Runtime State")
    print(f"  Agent running: {'Yes' if running else 'No'}")
    _print_latest("Latest heartbeat", db.latest_row("heartbeats"))
    _print_latest("Latest rejoin attempt", db.latest_row("rejoin_attempts"))
    latest_event = db.latest_row("events")
    if latest_event and latest_event.get("level") == "ERROR":
        _print_latest("Latest error", latest_event)
    else:
        print("  Latest error: none")
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    cfg = load_config()
    result = perform_rejoin(cfg, reason="manual")
    if result.warning:
        print(f"Warning: {result.warning}")
    if result.success:
        print(f"Rejoin attempt succeeded. root_used={str(result.root_used).lower()}")
        return 0
    print(f"Rejoin attempt failed: {result.error}")
    return 1


def _account_username_for_table(entry: dict[str, Any]) -> str:
    """Return the display username for a Start table row via root scan."""
    return get_package_display_username(entry)


def _set_all_phase_keep_failed(
    phase: dict[str, str],
    new_label: str,
    entries: list[dict[str, Any]],
    *,
    note: str = "",   # accepted for callsite symmetry; unused here
) -> None:
    """Mutate ``phase`` so every package that isn't currently in a
    "terminal" state (``Failed`` / ``Closed``) gets ``new_label``.

    Used by the Start dashboard to advance the phase column without
    clobbering rows that already errored out.  The ``note`` argument is
    ignored (the caller passes it through to its own renderer); we
    accept it so the call sites read symmetrically.
    """
    del note   # accepted for callsite symmetry only
    terminal = {"Failed", "Closed", "Offline"}
    for entry in entries:
        pkg = entry["package"]
        if phase.get(pkg) in terminal:
            continue
        phase[pkg] = new_label


def _visible_len(s: str) -> int:
    """Return the printable width of a string, stripping ANSI escape codes."""
    return len(_ANSI_RE.sub("", s))


def _clear_terminal(*, clear_scrollback: bool = False) -> None:
    """Clear the visible terminal/dashboard. Compatible with Termux and Unix."""
    safe_io.safe_clear_screen(clear_scrollback=clear_scrollback)


def _colorize_status(status: str, *, use_color: bool = True) -> str:
    """Wrap a status string in the appropriate ANSI color code."""
    if not use_color:
        return status
    base_key = status.split(" (", 1)[0] if " (" in status else status
    color = {
        "Started":      _ANSI_GREEN,
        "Online":            _ANSI_GREEN,
        "Lobby":             _ANSI_GREEN,    # app open at home
        "In Server":         _ANSI_GREEN,    # strong evidence: experience loaded
        ("Join " + "Unconfirmed"):  _ANSI_YELLOW,   # legacy alias
        "Ready":             _ANSI_YELLOW,
        "Starting":          _ANSI_YELLOW,
        "Launching":         _ANSI_WHITE,
        "Relaunching":       _ANSI_WHITE,
        "No Heartbeat":      _ANSI_ORANGE,
        "Launched":          _ANSI_GREEN,    # Roblox process up, no URL yet
        "Disconnected":      _ANSI_RED,      # Roblox error code detected
        ("Join" + "ing"):    _ANSI_CYAN,     # legacy alias
        "Join Failed":       _ANSI_RED,
        "Wrong Game / Wrong Server": _ANSI_RED,
        # ── Prep-phase labels (visible while Start prepares each clone) ──
        "Preparing":    _ANSI_CYAN,
        "Clear Cache":  _ANSI_CYAN,
        "Boosting":     _ANSI_CYAN,   # legacy alias
        "Clearing":     _ANSI_CYAN,   # legacy alias
        "Layout":       _ANSI_CYAN,   # internal only (not shown in public UI)
        "Docking":      _ANSI_CYAN,   # internal only
        "Waiting":      _ANSI_CYAN,
        "Checking":     _ANSI_YELLOW,
        "Resizing":     _ANSI_CYAN,
        "Optimizing":   _ANSI_CYAN,
        "Reconnecting": _ANSI_CYAN,
        "Cleared":      _ANSI_GREEN,
        "Low Applied":  _ANSI_GREEN,
        "Skipped":      _ANSI_YELLOW,
        "Partial":      _ANSI_YELLOW,
            "Failed":       _ANSI_RED,
            "Dead":         _ANSI_RED,
            "Offline":      _ANSI_RED,
        "Closed":       _ANSI_RED,
        "Background":   _ANSI_YELLOW,
        "Warning":      _ANSI_YELLOW,
        "Unknown":      _ANSI_DIM,
        "Heartbeat OK":          _ANSI_GREEN,
        "Launch command sent":   _ANSI_GREEN,
    }.get(status) or {
        "Started":      _ANSI_GREEN,
        "Online":            _ANSI_GREEN,
        "Lobby":             _ANSI_GREEN,
        "In Server":         _ANSI_GREEN,
        "Ready":             _ANSI_YELLOW,
        "Launching":         _ANSI_WHITE,
        "Relaunching":       _ANSI_WHITE,
        "Dead":              _ANSI_RED,
        "Failed":            _ANSI_RED,
    }.get(base_key, "")
    return f"{color}{status}{_ANSI_RESET}" if color else status


def _cap_plain_cell(raw: str, max_w: int) -> str:
    text = str(raw or "")
    if _visible_len(text) <= max_w:
        return text
    if max_w <= 3:
        return text[:max_w]
    return text[: max_w - 3] + "..."


def build_start_table(rows: list[tuple], *, use_color: bool = False) -> str:
    """Build the live Start table: #, Package, Username, State.

    Rows may be 4-tuples (idx, pkg, username, state) for backward compatibility,
    5-tuples (idx, pkg, username, state, runtime), or
    6-tuples (idx, pkg, username, state, runtime, usage).
    Runtime/usage telemetry may still be supplied by callers but is not shown
    in the user-visible Start table.

    Every line is hard-clamped via ``termux_ui.fit_line()`` for narrow screens.
    Static package management lists use separate simplified renderers.
    """
    headers = ("#", "Package", "Username", "State")
    str_rows = [
        (
            str(r[0]),
            _short_package_display(r[1]),
            str(r[2]) if len(r) > 2 else "",
            str(r[3]) if len(r) > 3 else "",
        )
        for r in rows
    ]

    term_cols = safe_io.terminal_columns()
    border_budget = 2 + (len(headers) * 3)
    data_budget = max(24, term_cols - border_budget)
    col_caps = [4, 12, 14, 22]
    capped_total = sum(col_caps)
    if capped_total > data_budget:
        scale = data_budget / capped_total
        col_caps = [max(3, int(c * scale)) for c in col_caps]
    str_rows = [
        tuple(_cap_plain_cell(str_rows[i][j], col_caps[j]) for j in range(len(headers)))
        for i in range(len(str_rows))
    ]

    widths = [
        max(len(headers[i]), max((_visible_len(r[i]) for r in str_rows), default=0))
        for i in range(len(headers))
    ]

    def _bold(text: str) -> str:
        if not use_color or not text:
            return text
        return f"{_ANSI_BOLD}{text}{_ANSI_RESET}"

    colored_rows = [
        (
            _bold(r[0]),
            _bold(r[1]),
            _bold(r[2]),
            _colorize_status(r[3], use_color=use_color),
        )
        for r in str_rows
    ]

    def _cell(s: str, w: int, raw: str | None = None) -> str:
        pad = w - _visible_len(raw if raw is not None else s)
        return f" {s}{' ' * max(0, pad)} "

    def _hline(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def _header_row(cells: tuple[str, ...]) -> str:
        return "│" + "│".join(
            _cell(_bold(str(cells[i])), widths[i], str(cells[i]))
            for i in range(len(widths))
        ) + "│"

    def _data_row(colored: tuple[str, ...], raw: tuple[str, ...]) -> str:
        parts = [_cell(str(colored[i]), widths[i], str(raw[i])) for i in range(len(widths))]
        return "│" + "│".join(parts) + "│"

    lines = [
        _hline("┌", "┬", "┐"),
        _header_row(headers),
        _hline("├", "┼", "┤"),
        *(_data_row(colored_rows[i], str_rows[i]) for i in range(len(colored_rows))),
        _hline("└", "┴", "┘"),
    ]
    return "\n".join(termux_ui.fit_line(line, term_cols) for line in lines)


def build_account_mapping_table(rows: list[tuple[str, str, str, str, str, str]]) -> str:
    """Build the account mapping table: #, Package, Username, User ID, Source, Status.

    Uses the same box-drawing layout as :func:`build_start_table` so columns
    stay aligned on narrow Termux screens.
    """
    headers = ("#", "Package", "Username", "User ID", "Source", "Status")
    if not rows:
        rows = [("", "", "", "", "", "")]
    safe_rows: list[tuple[str, str, str, str, str, str]] = []
    for row in rows:
        padded = tuple(_safe_table_cell(row[i] if i < len(row) else "-") for i in range(6))
        safe_rows.append(padded)
    rows = safe_rows
    widths = [
        max(len(headers[i]), max((_visible_len(r[i]) for r in rows), default=0))
        for i in range(6)
    ]

    def _cell(s: str, w: int) -> str:
        pad = w - _visible_len(s)
        return f" {s}{' ' * max(0, pad)} "

    def _hline(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (w + 2) for w in widths) + right

    def _row(cells: tuple[str, ...]) -> str:
        return "│" + "│".join(_cell(str(cells[i]), widths[i]) for i in range(6)) + "│"

    lines = [
        _hline("┌", "┬", "┐"),
        _row(headers),
        _hline("├", "┼", "┤"),
        *(_row(r) for r in rows),
        _hline("└", "┴", "┘"),
    ]
    return "\n".join(lines)


_FINAL_SUMMARY_ORDER: tuple[tuple[str, str], ...] = (
    ("online", "online."),
    ("reconnecting", "reconnecting."),
    ("launching", "launching."),
    ("preparing", "preparing."),
    ("optimizing", "optimizing."),
    ("in background", "in background."),
    ("warning", "with warnings."),
    ("failed", "failed."),
    ("dead", "dead (recovering)."),
    ("offline", "offline."),
    ("unknown", "unknown."),
)

_STATE_TO_SUMMARY: dict[str, str] = {
    "Online":            "online",
    "Lobby":             "lobby",
    "In Server":         "online",         # confirmed in target server
    ("Join " + "Unconfirmed"):  "launching",
    ("Join" + "ing"):    "launching",
    "Reconnecting":      "reconnecting",
    "Launching":         "launching",
    "Relaunching":       "reconnecting",
    "Failed":            "failed",
    "Join Failed":       "failed",
    "Wrong Game / Wrong Server": "failed",
    "Dead":              "dead",           # process confirmed gone
    "Offline":           "offline",
    "Closed":            "offline",
    "Warning":           "warning",
    "Background":        "in background",
    "Unknown":           "unknown",
    "Preparing":         "preparing",
    "Optimizing":        "optimizing",
}


def _final_summary_line(count: int, tail: str) -> str:
    word = "package" if count == 1 else "packages"
    return f"{count} {word} {tail}"


def build_final_summary(entries: list[Any], results: dict[str, str]) -> str:
    """Short multi-line summary tallying packages by public state."""
    tallies: dict[str, int] = {}
    for e in entries:
        pkg = e["package"] if isinstance(e, dict) else str(e)
        raw = str(results.get(pkg, "Unknown"))
        bucket = _STATE_TO_SUMMARY.get(raw, "unknown")
        tallies[bucket] = tallies.get(bucket, 0) + 1
    lines = ["Final:"]
    any_line = False
    for key, tail in _FINAL_SUMMARY_ORDER:
        n = tallies.get(key, 0)
        if n:
            lines.append(_final_summary_line(n, tail))
            any_line = True
    if not any_line:
        lines.append("0 packages started.")
    return "\n".join(lines)


def build_start_verbose_details(rows: list[dict[str, str]], *, use_color: bool = False) -> str:
    """Per-package cache / graphics / launch detail for ``--verbose`` / ``--debug`` / log DEBUG only."""
    if not rows:
        return ""
    lines = ["Details (verbose / debug):"]
    for row in rows:
        pkg = row.get("package", "")
        lines.append(
            f"  {pkg}: cache={row.get('cache', '')}; graphics={row.get('graphics', '')}; "
            f"launch={row.get('launch_detail', '')}"
        )
    block = "\n".join(lines)
    if use_color:
        return f"{_ANSI_DIM}{block}{_ANSI_RESET}"
    return block


def _progress_line(index: int, total: int, entry: dict[str, Any], message: str) -> str:
    username = get_package_display_username(entry)
    if username == "Unknown":
        username = entry["package"]
    return f"[{index}/{total}] {username}: {message}"


def _prepare_automatic_layout(
    cfg: dict[str, Any], entries: list[dict[str, Any]]
) -> tuple[dict[str, Any], str]:
    """Pre-launch layout pipeline.

    Steps (all silent — public Start UI never sees output here):
      1. Compute landscape-block layout for the selected packages.
      2. Run layout-key discovery so the writer knows every key alias the
         real App Cloner build uses (incl. ``Set window position``, auto-DPI
         landscape, freeform, etc.).
      3. Write XML for each package with all known aliases + Set-enable flags.
      4. Force-stop the selected packages so the next launch re-reads prefs.

    Verification and direct-resize happen post-launch in
    :func:`_verify_layout_post_launch`.
    """
    from .logger import configure_logging
    _layout_log = configure_logging()
    try:
        from .resize_engine import run_resize_pipeline

        pipeline = run_resize_pipeline(cfg, entries, trigger="startup", force=True)
        if pipeline.skipped and pipeline.skipped_reason == "no trusted packages":
            cfg["_layout_abort_reason"] = "resize skipped: no trusted packages"
            return cfg, "resize skipped: no trusted packages"

        packages = [r.package for r in pipeline.rects]
        n = len(packages)
        rects = pipeline.rects
        _screen_mode = str(pipeline.mode or DEFAULT_SCREEN_MODE).lower()
        cfg["screen_mode"] = _screen_mode

        display_w = int(pipeline.layout.get("screen_width") or 0)
        display_h = int(pipeline.layout.get("screen_height") or 0)
        if display_w <= 0 or display_h <= 0:
            try:
                display = window_layout.detect_display_info()
                display_w, display_h = display.width, display.height
            except Exception:  # noqa: BLE001
                display_w, display_h = 1080, 1920

        cfg.pop("_layout_abort_reason", None)
        cfg.pop("_layout_abort_mode", None)
        _termux_frac = float(cfg.get("termux_dock_fraction") or 0.0)

        from .logger import log_event as _log_event
        _log_event(
            _layout_log,
            "info",
            "[DENG_REJOIN_RESIZE_MODE]",
            mode=pipeline.mode,
            confidence=pipeline.confidence,
            basis=pipeline.basis,
            home_landscape_wm_portrait_conflict=str(
                bool(pipeline.signals.get("home_landscape_wm_portrait_conflict"))
            ).lower(),
            wm_size_raw=str(pipeline.signals.get("wm_size_raw") or ""),
            logical_size=str(pipeline.signals.get("logical_size") or ""),
        )
        _log_event(
            _layout_log,
            "info",
            "[DENG_REJOIN_RESIZE_LAYOUT]",
            screen_w=display_w,
            screen_h=display_h,
            columns=int(pipeline.layout.get("columns") or 0),
            rows=int(pipeline.layout.get("rows") or 0),
            left_offset=int(pipeline.layout.get("left_offset") or 0),
            package_count=n,
            summary=json.dumps(pipeline.summary, sort_keys=True),
        )

        # ── [DENG_REJOIN_LAYOUT_BOUNDS] — per-package desired bounds + overlap check
        try:
            _cols = max(1, int(pipeline.layout.get("columns") or 1))
            _seen_bounds: list[tuple[int, int, int, int]] = []
            for _bi, _r in enumerate(rects):
                _slot = _bi
                _row = _slot // _cols
                _col = _slot % _cols
                _overlap = any(
                    not (_r.right <= _o[0] or _o[2] <= _r.left or
                         _r.bottom <= _o[1] or _o[3] <= _r.top)
                    for _o in _seen_bounds
                )
                _seen_bounds.append((_r.left, _r.top, _r.right, _r.bottom))
                _layout_log.info(
                    "[DENG_REJOIN_LAYOUT_BOUNDS] package=%s index=%d slot=%d row=%d col=%d"
                    " desired_x=%d desired_y=%d desired_w=%d desired_h=%d overlap_detected=%s",
                    _r.package, _bi, _slot, _row, _col,
                    _r.left, _r.top, _r.win_w, _r.win_h,
                    "true" if _overlap else "false",
                )
        except Exception:  # noqa: BLE001
            pass

        try:
            save_config(cfg)
        except Exception:  # noqa: BLE001
            pass

        # ── Discovery: identify real key aliases per package ──────────────
        try:
            from . import layout_discovery as _ld
            try:
                root_info = android.detect_root()
                root_tool = root_info.tool if root_info.available else None
            except Exception:  # noqa: BLE001
                root_tool = None
            log_path, _discs = _ld.run_discovery_and_log(
                packages, root_tool=root_tool, refresh=False,
            )
            _layout_log.debug("layout-key discovery saved: %s", log_path)
        except Exception as exc:  # noqa: BLE001
            _layout_log.debug("layout-key discovery error (non-fatal): %s", exc)

        for row in pipeline.packages:
            _layout_log.debug(
                "pre-launch resize: %s status=%s reason=%s backup=%s",
                row.get("package"),
                row.get("status"),
                row.get("reason"),
                row.get("backup_created"),
            )

        return cfg, f"layout_prepared n={n} mode={pipeline.mode}"
    except Exception as exc:  # noqa: BLE001
        _layout_log.debug("Layout error (non-fatal): %s", exc)
        return cfg, "layout_error"


def _save_start_diagnostics(payload: dict[str, Any]) -> None:
    """Write ``~/.deng-tool/rejoin/data/last_start_diagnostics.json`` (silent).

    Used by Start to drop a small JSON file with desired vs actual bounds,
    layout method used, state evidence, and a few other fields.  Public
    users never see this file; it exists so we can diagnose without asking
    them to run support-bundle.

    Never raises.
    """
    try:
        import json as _json
        import logging as _logging
        from pathlib import Path

        target_dir = Path.home() / ".deng-tool" / "rejoin" / "data"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "last_start_diagnostics.json"
        # Sanitize: convert any non-JSON-serializable values to strings.
        def _scrub(x: Any) -> Any:
            if isinstance(x, dict):
                return {str(k): _scrub(v) for k, v in x.items()}
            if isinstance(x, (list, tuple)):
                return [_scrub(v) for v in x]
            if isinstance(x, (str, int, float, bool)) or x is None:
                return x
            return str(x)
        with target.open("w", encoding="utf-8") as fh:
            _json.dump(_scrub(payload), fh, indent=2, sort_keys=True)
        _logging.getLogger("deng.rejoin").debug(
            "Start diagnostics saved to %s", target,
        )
    except Exception as exc:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger("deng.rejoin").debug(
            "save_start_diagnostics error: %s", exc,
        )


class StartSessionLogger:
    """Persist Start step markers for post-crash diagnosis."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.path = LOG_DIR / f"start-session-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log"
        self.last_step = ""
        self.completed = False
        self._started_at = datetime.now(timezone.utc).isoformat()
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                f"[START_SESSION] id={self.session_id} pid={os.getpid()} started_at={self._started_at}\n",
                encoding="utf-8",
            )
            self._write_state()
        except Exception:  # noqa: BLE001
            pass

    def mark(self, step: str, **fields: Any) -> None:
        self.last_step = str(step)
        parts = [f"[START_STEP] {self.last_step}"]
        for key, value in sorted(fields.items()):
            text = str(value).replace("\n", " ").replace("\r", " ")[:240]
            parts.append(f"{key}={text}")
        try:
            with self.path.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(" ".join(parts) + "\n")
            safe_io.set_crash_context(
                start_step=self.last_step,
                start_session_log=str(self.path),
                session_id=self.session_id,
            )
            self._write_state()
        except Exception:  # noqa: BLE001
            pass

    def finish(self, status: str = "completed") -> None:
        self.completed = True
        try:
            with self.path.open("a", encoding="utf-8", errors="replace") as fh:
                fh.write(f"[START_SESSION_DONE] status={status}\n")
            self._write_state(status=status)
        except Exception:  # noqa: BLE001
            pass

    def _write_state(self, *, status: str = "running") -> None:
        try:
            START_CRASH_STATE_PATH.write_text(
                json.dumps(
                    {
                        "session_id": self.session_id,
                        "status": status,
                        "last_step": self.last_step,
                        "start_session_log": str(self.path),
                        "crash_log": str(CRASH_LOG_PATH),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass


def _previous_start_crash_notice() -> str | None:
    try:
        if not START_CRASH_STATE_PATH.exists():
            return None
        data = json.loads(START_CRASH_STATE_PATH.read_text(encoding="utf-8"))
        if str(data.get("status") or "") != "running":
            return None
        return (
            "Previous Start may have crashed. "
            f"Last step: {data.get('last_step') or 'unknown'}. "
            f"Start session log: {data.get('start_session_log')}. "
            f"Crash log: {data.get('crash_log') or CRASH_LOG_PATH}."
        )
    except Exception:  # noqa: BLE001
        return None


def _verify_layout_post_launch(
    cfg: dict[str, Any], entries: list[dict[str, Any]]
) -> tuple[dict[str, bool], list[dict[str, Any]]]:
    """Read-only post-launch layout verification.

    Runs silently after the launch grace.  Does **not** re-write XML/pb99 or
    run ``am stack resize`` on packages that are already online — those steps
    were probe p-b8a026e11e evidence for mass force-closes after the last
    package joined.  Returns a tuple of ``({package: applied_ok},
    [diagnostic_rows...])`` so callers can include per-package details in the
    start diagnostics JSON.
    """
    import logging as _logging
    _layout_log = _logging.getLogger("deng.rejoin.layout")
    out: dict[str, bool] = {}
    diag_rows: list[dict[str, Any]] = []
    try:
        _screen_mode = str(cfg.get("screen_mode") or "auto").lower()
        if _screen_mode not in ("landscape", "portrait"):
            from .resize_mode import resolve_runtime_screen_mode

            _screen_mode, _ = resolve_runtime_screen_mode(
                configured=_screen_mode,
                previous_mode=cfg.get("last_resize_mode"),
            )
        cfg["screen_mode"] = _screen_mode
        try:
            _display_state = android.get_display_orientation_state()
        except Exception:  # noqa: BLE001
            _display_state = {"orientation": "unknown", "width": 0, "height": 0, "rotation": ""}
        stored_rects = (
            cfg.get("_layout_rects") or cfg.get("last_layout_preview")
            if cfg.get("last_layout_mode") in (None, _screen_mode)
            else None
        )
        selected = set()
        try:
            from .logger import log_event as _log_event
        except Exception:  # noqa: BLE001
            _log_event = None
        for e in entries:
            pkg = e["package"]
            reason = window_layout.layout_exclusion_reason(pkg)
            excluded = bool(reason)
            if _log_event:
                _log_event(
                    _layout_log,
                    "info",
                    "[DENG_REJOIN_LAYOUT_EXCLUSION]",
                    package=pkg,
                    reason=reason or "selected_package",
                    excluded=str(excluded).lower(),
                )
            if not excluded:
                selected.add(pkg)
        rects: list[window_layout.WindowRect] = []
        if isinstance(stored_rects, list):
            for item in stored_rects:
                if not isinstance(item, dict) or item.get("package") not in selected:
                    continue
                try:
                    rects.append(window_layout.WindowRect(
                        package=str(item["package"]),
                        left=int(item["left"]),
                        top=int(item["top"]),
                        right=int(item["right"]),
                        bottom=int(item["bottom"]),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
        if {r.package for r in rects} != selected:
            from .resize_engine import compute_layout_rects

            rects, layout, _screen_mode = compute_layout_rects(cfg, entries)
            cfg["screen_mode"] = _screen_mode
            cfg["last_layout_mode"] = _screen_mode
            cfg["last_layout_preview"] = [r.as_dict() for r in rects]
            cfg["_layout_rects"] = [r.as_dict() for r in rects]
            try:
                save_config(cfg)
            except Exception:  # noqa: BLE001
                pass
        elif not rects:
            from .resize_engine import run_resize_pipeline

            pipeline = run_resize_pipeline(cfg, entries, trigger="auto", force=True)
            rects = pipeline.rects
            _screen_mode = str(pipeline.mode or DEFAULT_SCREEN_MODE).lower()
        from . import window_apply
        results = window_apply.apply_window_layout(
            rects,
            force_stop_before=False,
            relaunch_after=False,
            verify_after=True,
            pre_write=False,
            allow_direct_resize=False,
            retries=0,
            screen_mode=_screen_mode,
            touch_probe=False,
        )
        for r in results:
            out[r.package] = r.final_ok
            _desired_tuple = (r.desired.left, r.desired.top, r.desired.right, r.desired.bottom)
            _readback_task_id = None
            if isinstance(r.layer_readback, dict):
                _readback_task_id = r.layer_readback.get("task_id")
            _clamped_axes = []
            if isinstance(r.layer_readback, dict):
                _clamped_axes = r.layer_readback.get("clamped_axes") or []
            _click_result = {
                "target": r.touch_probe_center,
                "inside_actual_bounds": bool(
                    r.touch_probe_center and r.actual_bounds
                    and r.actual_bounds[0] <= r.touch_probe_center[0] <= r.actual_bounds[2]
                    and r.actual_bounds[1] <= r.touch_probe_center[1] <= r.actual_bounds[3]
                ),
                "tap_ok": r.touch_probe_ok,
                "detail": r.touch_probe_detail,
            }
            diag_rows.append({
                "package":        r.package,
                "mode":           _screen_mode,
                "display_size":    {
                    "width": _display_state.get("width", 0),
                    "height": _display_state.get("height", 0),
                },
                "rotation":        _display_state.get("rotation", ""),
                "desired":        r.desired.as_dict() if hasattr(r.desired, "as_dict") else {
                    "left": r.desired.left, "top": r.desired.top,
                    "right": r.desired.right, "bottom": r.desired.bottom,
                },
                "expected_bounds": _desired_tuple,
                "task_id":        r.task_id if r.task_id is not None else _readback_task_id,
                "task_package":   r.layer_readback.get("task_package") if isinstance(r.layer_readback, dict) else "",
                "task_package_expected": r.task_package_expected,
                "actual_bounds":  r.actual_bounds,
                "actual_method":  r.actual_method,
                "task_bounds":    r.task_bounds,
                "surface_bounds": r.surface_bounds,
                "input_region":   r.input_region,
                "touchable_region": r.touchable_region,
                "window_frame":   r.window_frame,
                "content_frame":  r.content_frame,
                "stable_frame":   r.stable_frame,
                "visible_frame":  r.visible_frame,
                "title_bar_height": r.title_bar_height,
                "corrected_task_bounds": r.corrected_task_bounds,
                "density":        r.density_info,
                "mismatch_classification": r.mismatch_classification,
                "clamped_axes":    _clamped_axes,
                "layer_readback": r.layer_readback,
                "input_transform": "actual_bounds_center",
                "click_target_result": _click_result,
                "status":         r.status,
                "pre_write_ok":   r.pre_write_ok,
                "pre_write_method": r.pre_write_method,
                "direct_resize_ok": r.direct_resize_ok,
                "validation":      r.validation,
                "touch_probe_ok":  r.touch_probe_ok,
                "touch_probe_center": r.touch_probe_center,
                "touch_probe_detail": r.touch_probe_detail,
                "attempts":       r.attempts,
                "final_ok":       r.final_ok,
            })
            _layout_log.debug(
                "post-launch verify: %s ok=%s actual=%s method=%s attempts=%s",
                r.package, r.final_ok, r.actual_bounds, r.actual_method, "; ".join(r.attempts),
            )
            _layout_log.info(
                "[DENG_REJOIN_LAYOUT_VERIFY] package=%s mode=%s display=%sx%s rotation=%s"
                " expected=%s actual=%s task_id=%s task=%s surface=%s input=%s title_bar=%s"
                " class=%s clamped_axes=%s input_transform=actual_bounds_center click=%s status=%s",
                r.package,
                _screen_mode,
                _display_state.get("width", 0),
                _display_state.get("height", 0),
                _display_state.get("rotation", ""),
                _desired_tuple,
                r.actual_bounds,
                r.task_id if r.task_id is not None else _readback_task_id,
                r.task_bounds,
                r.surface_bounds,
                r.input_region,
                r.title_bar_height,
                ",".join(r.mismatch_classification),
                ",".join(str(x) for x in _clamped_axes),
                _click_result,
                r.status,
            )
    except Exception as exc:  # noqa: BLE001
        _layout_log.debug("verify_layout_post_launch error: %s", exc)
    return out, diag_rows


def _resolve_presence_user_id(entry: dict[str, Any]) -> int:
    """Resolve Roblox user id from a package entry. Never raises."""
    raw_uid = entry.get("roblox_user_id")
    if isinstance(raw_uid, int) and raw_uid > 0:
        return int(raw_uid)
    if isinstance(raw_uid, str) and raw_uid.isdigit():
        return int(raw_uid)
    uname = str(entry.get("account_username") or "").strip()
    if not uname:
        return 0
    try:
        from agent.roblox_presence import lookup_user_id

        resolved = lookup_user_id(uname)
        return int(resolved) if resolved else 0
    except Exception:  # noqa: BLE001
        return 0


def _wait_for_sequential_presence_online(
    entry: dict[str, Any],
    cfg: dict[str, Any],
    *,
    poll_seconds: float = 7.0,
    timeout_seconds: float = 900.0,
    render_callback: Any = None,
) -> str:
    """Block until Roblox presence type 2 confirms the package is Online."""
    from agent.roblox_presence import detect_roblox_cookie, poll_presence_gate_state

    pkg = str(entry.get("package") or "").strip()
    if not pkg:
        return "Failed"
    uid = _resolve_presence_user_id(entry)
    cookie = str(entry.get("roblox_cookie") or "").strip()
    if not cookie:
        try:
            cookie = detect_roblox_cookie(pkg, entry=entry, config=cfg, use_root=True)
        except Exception:  # noqa: BLE001
            cookie = ""
    prep_root = android.detect_root()
    deadline = time.monotonic() + max(30.0, float(timeout_seconds))
    poll = max(5.0, min(10.0, float(poll_seconds)))
    while time.monotonic() < deadline:
        pid = android.get_package_pid(pkg, prep_root)
        process_alive = bool(pid)
        gate = poll_presence_gate_state(
            uid,
            cookie=cookie or None,
            process_alive=process_alive,
        )
        if gate == "Online":
            return "Online"
        if gate == "Dead":
            return "Dead"
        if callable(render_callback):
            try:
                render_callback()
            except Exception:  # noqa: BLE001
                pass
        time.sleep(max(0.0, poll))
    return "Checking"


def _run_preparation_phase(
    entries: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    use_color: bool = True,
    verbose: bool = True,
) -> None:
    """Preparation phase: stop background Roblox apps, clear cache.

    Set ``verbose=False`` to run silently (no terminal output).
    Steps:
    1. Force-stop all Roblox packages NOT in the selected set (close background APKs).
    2. Clear the cache directory for each selected package (root required).
    """
    G   = _ANSI_GREEN  if use_color else ""
    Y   = _ANSI_YELLOW if use_color else ""
    C   = _ANSI_CYAN   if use_color else ""
    RST = _ANSI_RESET  if use_color else ""

    packages = [e["package"] for e in entries]
    hints = cfg.get("package_detection_hints")

    # Step 1 — close background Roblox processes
    if verbose:
        print(f"  {C}⏳ Stopping background Roblox processes...{RST}")
    stopped = android.force_stop_packages_except(packages, hints)
    if verbose:
        if stopped:
            for pkg in stopped:
                print(f"  {G}✓{RST} Stopped: {pkg}")
        else:
            print(f"  {Y}ℹ{RST}  No background Roblox processes found.")

    # Step 2 — safe cache dirs for each selected package (mandatory when Start calls prep)
    if verbose:
        print(f"  {C}⏳ Clearing safe cache (cache, code_cache, files/tmp)...{RST}")
    any_cleared = False
    for entry in entries:
        pkg = entry["package"]
        username = _account_username_for_table(entry)
        label = android.clear_safe_package_cache(pkg)
        if verbose:
            print(f"  {G if label == 'Cleared' else Y}•{RST} {pkg}: {label} ({username})")
        if label == "Cleared":
            any_cleared = True
    if verbose:
        if not any_cleared:
            print(f"  {Y}ℹ{RST}  Cache cleanup: mostly skipped (root/offline) or no cache dirs.")
        print(f"  {G}✓{RST} Preparation complete.")
        print()


def _termux_exit_clean() -> None:
    """Bypass Python finalization on Termux to avoid libc-shutdown segfaults."""
    safe_io.termux_exit_clean()


def cmd_package_key(args: argparse.Namespace) -> int:
    """Manage per-package license files (not shown in the top menu)."""
    print_banner(use_color=not args.no_color)
    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()
    if not _is_interactive():
        print("Run this command in interactive Termux to manage package keys.")
        return 0
    _config_menu_key(cfg)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    use_color = not args.no_color
    _start_lock: LockManager | None = None
    _shutdown_reason = "normal_exit"
    _supervisor_ref: WatchdogSupervisor | None = None
    _lifecycle_state = "STARTING"
    _start_session_id = f"start-{int(time.time() * 1000)}-{os.getpid()}"
    previous_crash_notice = _previous_start_crash_notice()
    _start_session = StartSessionLogger(_start_session_id)
    _start_screen_mode = "auto"
    # Silence all internal loggers so warnings/errors go to file, never stdout.
    from .logger import silence_public_loggers
    silence_public_loggers()

    def _transition_lifecycle(to_state: str, reason: str) -> None:
        nonlocal _lifecycle_state
        try:
            from .logger import configure_logging, log_event
            safe_io.set_crash_context(
                phase=to_state.lower(),
                reason=reason,
                session_id=_start_session_id,
                screen_mode=_start_screen_mode,
            )
            log_event(
                configure_logging(),
                "info",
                "[DENG_REJOIN_LIFECYCLE]",
                session_id=_start_session_id,
                **{"from": _lifecycle_state, "to": to_state, "reason": reason},
            )
        except Exception:  # noqa: BLE001
            pass
        _lifecycle_state = to_state

    def _log_stop_request(source: str, *, allowed: bool, stack: str = "") -> None:
        try:
            import traceback as _traceback
            from .logger import configure_logging, log_event
            log_event(
                configure_logging(),
                "info",
                "[DENG_REJOIN_STOP_REQUEST]",
                source=source,
                stack=stack or "".join(_traceback.format_stack(limit=8))[:1800],
                allowed=str(bool(allowed)).lower(),
            )
        except Exception:  # noqa: BLE001
            pass

    def _release_start_lock(reason: str) -> None:
        nonlocal _start_lock
        if _start_lock is None:
            return
        lock_removed = "false"
        try:
            _start_lock.release()
            lock_removed = "true"
        except Exception:  # noqa: BLE001
            pass
        try:
            from .logger import configure_logging, log_event
            _lg = configure_logging()
            log_event(
                _lg, "info", "[DENG_REJOIN_SHUTDOWN]",
                reason=reason,
                supervisor_stopped=str(bool(_supervisor_ref)).lower(),
                children_stopped="true",
                lock_removed=lock_removed,
            )
        except Exception:  # noqa: BLE001
            pass
        _start_lock = None

    _start_session.mark("config_load_begin")
    _clear_terminal(clear_scrollback=True)
    try:
        _transition_lifecycle("STARTING", "cmd_start")
        cfg = load_config()
        _start_session.mark("config_load_done")
        cfg = _ensure_install_id_saved(cfg)
        try:
            from .build_info import collect_version_info
            _version_info = collect_version_info()
        except Exception:  # noqa: BLE001
            _version_info = {}
        _enforce_configured_screen_mode(cfg, phase="before_start")
        _start_screen_mode = str(cfg.get("screen_mode") or "auto")
        safe_io.set_crash_context(
            phase="starting",
            session_id=_start_session_id,
            screen_mode=_start_screen_mode,
            package_count=len(enabled_package_entries(cfg)),
            git_commit=_version_info.get("git_commit_short", ""),
            artifact_sha=_version_info.get("artifact_sha256_short", ""),
            build_probe_id=_version_info.get("probe_id", ""),
        )
        _enforce_termux_left_layout(cfg)
        if previous_crash_notice:
            try:
                from .logger import configure_logging, log_event
                log_event(
                    configure_logging(),
                    "warning",
                    "[DENG_REJOIN_PREVIOUS_START_CRASH]",
                    message=previous_crash_notice,
                )
            except Exception:  # noqa: BLE001
                pass

        # ── License gate (re-validated on every Start, BEFORE any launch) ──────
        # This runs before package detection, watchdog/supervisor, webhook
        # reporter, private-server URL launch and any Android VIEW intent, so an
        # expired / wrong-device / invalid key can never reach a package launch
        # even if the menu was left open. Server time is authoritative (the
        # remote check returns server_now/expires_at), so changing the device
        # clock cannot bypass the 48-hour expiry.
        if is_test_license_bypass_active():
            _print_test_license_bypass_active(use_color)
        elif not keystore.DEV_MODE:
            license_cfg = cfg.get("license") or {}
            if not license_cfg.get("disabled_by_user") and license_cfg.get("enabled", True):
                if str(license_cfg.get("mode") or "remote").strip().lower() == "local":
                    _key = (license_cfg.get("key") or "").strip() or (cfg.get("license_key") or "").strip()
                    if _key:
                        ok, msg = keystore.verify_key(_key)
                        if not ok:
                            _print_license_err(f"License key error: {msg}", use_color)
                            print_beginner_license_gate_help()
                            _start_session.finish("license_failed")
                            return 1
                    else:
                        _print_license_err("No License Key Found", use_color)
                        print_beginner_license_gate_help()
                        _start_session.finish("license_missing")
                        return 1
                else:
                    if not verify_remote_license_noninteractive(cfg, use_color=use_color):
                        _start_session.finish("license_failed")
                        return 1

        entries = enabled_package_entries(cfg)
        safe_io.set_crash_context(package_count=len(entries))
        if not cfg.get("first_setup_completed"):
            print("First-time setup is required before starting.")
            if _is_interactive():
                _run_first_time_setup_wizard(cfg, args, start_after_save=True)
                _start_session.finish("setup_required")
                return 0
            print("Run: deng-rejoin and choose First Time Setup Config.")
            _start_session.finish("setup_required")
            return 2

        if not entries:
            print("No Roblox Package Selected")
            print()
            print("Run Setup / Edit Config, then choose Roblox Package Setup.")
            _start_session.finish("no_packages")
            return 2
        _enforce_configured_screen_mode(cfg, [entry["package"] for entry in entries], phase="before_start")
        _start_session.mark("package_preparation_begin", package_count=len(entries))

        try:
            from .logger import configure_logging, log_event
            _lock_logger = configure_logging()
            _start_lock = LockManager()
            try:
                _start_lock.acquire()
                log_event(
                    _lock_logger, "info", "[DENG_REJOIN_INSTANCE_LOCK]",
                    action="created",
                    pid=os.getpid(),
                    lock_path=str(LOCK_PATH),
                )
            except LockError:
                stopped, stop_msg = stop_running_agent(timeout=5)
                log_event(
                    _lock_logger, "info", "[DENG_REJOIN_INSTANCE_LOCK]",
                    action="active_existing",
                    pid="",
                    lock_path=str(LOCK_PATH),
                    result=stop_msg,
                )
                if stopped:
                    _start_lock.acquire()
                    log_event(
                        _lock_logger, "info", "[DENG_REJOIN_INSTANCE_LOCK]",
                        action="created",
                        pid=os.getpid(),
                        lock_path=str(LOCK_PATH),
                    )
                else:
                    print("DENG Tool: Rejoin is already running.")
                    print("Stop the existing Start session, then run Start again.")
                    _start_session.finish("already_running")
                    return 1
        except Exception as exc:  # noqa: BLE001
            print(f"Could not create Start lock: {exc}")
            _start_session.finish("lock_failed")
            return 1

        # Start launch selection is driven only by URL presence.
        # [DENG_REJOIN_PRIVATE_URL_LAUNCH] probe_id=p-ea167faf5f
        # Blank URL launches app-only; configured URL uses the private-server launcher.
        runtime_cfg = cfg
        runtime_entries = entries
        runtime_entry_by_pkg = {entry["package"]: entry for entry in runtime_entries}

        # ── Detect packages (silently; go to debug log only) ─────────────────
        import logging as _logging
        _start_log = _logging.getLogger("deng.rejoin.start")

        hints2, inc_launch, det_en = _package_detection_options(cfg)
        detected_n = len(
            android.discover_roblox_package_candidates(
                hints2,
                include_launchable_only=inc_launch,
                detection_enabled=det_en,
            )
        )
        _start_log.debug(
            "start: detected_packages=%d configured_packages=%d",
            detected_n,
            len(entries),
        )
        if detected_n == 0:
            _start_log.info(
                "start: live package scan found 0 candidates; using %d persisted package(s)",
                len(entries),
            )

        if cfg.get("root_mode_enabled"):
            root_info = android.detect_root()
            _start_log.debug("start: root_available=%s tool=%s", root_info.available, root_info.tool)

        n = len(entries)
        sup = cfg.get("supervisor") if isinstance(cfg.get("supervisor"), dict) else {}

        # ── Phase-tracked dashboard ───────────────────────────────────────────
        # User feedback: "text state never change from preparing because
        # preparing has many stages (preparing, boosting, clearing cache,
        # etc) meaning this never work and must copy kaeru."  The old
        # code rendered ONE "Preparing" table at the top of cmd_start and
        # didn't update it until 30 seconds later when supervisor took
        # over — so the user thought the tool was frozen.  We now re-
        # render the table after each prep phase with a distinct label.

        # Per-package phase label.  Updated in place by _phase() below.
        phase: dict[str, str] = {e["package"]: "Preparing" for e in entries}

        # RAM info is cached between renders (updated every ~9s) to avoid
        # reading /proc/meminfo on every 3-second render tick.  Defined here
        # (before _render_phase) so _render_phase can call _get_ram_label
        # safely during the Preparing / Boosting / Launching phases.
        _ram_cache: dict[str, Any] = {"info": None, "next_update": 0.0}

        def _get_ram_label() -> str:
            """Return compact 'RAM: XMB (Y%)\\n[████░░░░]' or brief fallback.

            Two-line format:  line 1 = bold coloured label,
                              line 2 = ASCII progress bar matching available %.
            Caller should split on '\\n' and indent each line individually.
            """
            try:
                import time as _t
                now = _t.monotonic()
                if now >= _ram_cache["next_update"]:
                    try:
                        info = android.get_memory_info()
                        _ram_cache["info"] = info
                    except Exception:  # noqa: BLE001
                        _ram_cache["info"] = None
                    _ram_cache["next_update"] = now + 9.0
                info = _ram_cache["info"]
                if not info:
                    return "RAM: Unknown"
                free_mb  = int(info.get("free_mb", 0))
                pct_free = int(info.get("percent_free", 0))

                label = f"RAM: {free_mb}MB ({pct_free}%)"
                if use_color:
                    col = (
                        _ANSI_GREEN if pct_free >= 40
                        else (_ANSI_YELLOW if pct_free >= 20 else _ANSI_RED)
                    )
                    label = f"{_ANSI_BOLD}{col}{label}{_ANSI_RESET}"

                _bar_width = 20
                _filled = max(0, min(_bar_width, int(pct_free / 100 * _bar_width)))
                _bar = "[" + "\u2588" * _filled + "\u2591" * (_bar_width - _filled) + "]"
                return f"{label}\n{_bar}"
            except Exception:  # noqa: BLE001
                return "RAM: Unknown"

        def _phase_table_state(pkg: str) -> str:
            """During stagger: hot-lane truth for opened clones; phase labels for the rest."""
            ph = str(phase.get(pkg) or "").strip()
            if _supervisor_ref is None:
                return ph or "Checking..."
            opened = pkg in getattr(_supervisor_ref, "_package_opened", set())
            sup_st = str(_supervisor_ref.status_map.get(pkg) or "").strip()
            if getattr(_supervisor_ref, "_all_launches_completed", False):
                return sup_st or ph or "Checking..."
            if opened:
                if sup_st in ("Online", "In Lobby", "In Game", "Launched", "In Server", "Background"):
                    return "Online"
                if sup_st == "No Heartbeat":
                    return "No Heartbeat"
                if sup_st in ("Dead", "Join Failed", "Disconnected", "Offline", "Unknown"):
                    return "Dead"
                if sup_st == "Wrong Game / Wrong Server":
                    return "Dead"
                if sup_st in ("Relaunching", "Reconnecting"):
                    return "Relaunching"
                if sup_st in ("Launching", "Waiting", "Checking", "Pending", "Join Unconfirmed"):
                    return "Launching"
                if sup_st == "Failed":
                    return "Failed"
                if sup_st:
                    return sup_st
                return "Launching"
            if ph in ("Launching", "Failed", "Preparing", "Clear Cache"):
                return ph
            return ph or "Ready"

        _stagger_render_last = 0.0

        def _render_phase(_unused_note: str = "") -> None:
            """Atomically redraw the dashboard with the current phase per package.

            Shows only: logo + available RAM + table.
            State labels in the table already describe what is happening;
            additional notes below the table are suppressed (user feedback:
            "useless text explaining state" when state is in the table).
            Uses clear_scrollback=True on every call so old banner/table lines
            from prior phases never bleed through on slow Termux terminals.
            """
            nonlocal _stagger_render_last
            if (
                _supervisor_ref is not None
                and not getattr(_supervisor_ref, "_all_launches_completed", False)
            ):
                try:
                    _supervisor_ref.sync_stagger_display_status()
                except Exception:  # noqa: BLE001
                    pass
            rows = [
                (
                    i + 1,
                    e["package"],
                    _account_username_for_table(e),
                    _phase_table_state(e["package"]),
                    "0s",
                    "0 MB",
                )
                for i, e in enumerate(entries)
            ]
            lines = [banner_text(use_color=use_color), ""]
            try:
                ram = _get_ram_label()
                if ram:
                    for _ram_line in ram.split("\n"):
                        lines.append(f"  {_ram_line}")
                    lines.append("")
            except Exception:  # noqa: BLE001
                pass
            lines.append(build_start_table(rows, use_color=use_color))
            try:
                _clear_terminal(clear_scrollback=True)
                safe_io.write_stdout_block("\n".join(lines) + "\n")
            except OSError:
                pass

        def _render_phase_throttled(_unused_note: str = "") -> None:
            nonlocal _stagger_render_last
            import time as _rt

            now = _rt.monotonic()
            if now - _stagger_render_last < 0.35:
                return
            _stagger_render_last = now
            _render_phase(_unused_note)

        def _set_all_phase(label: str, note: str = "") -> None:
            for pkg in phase:
                phase[pkg] = label
            _render_phase(note)

        def _set_all_phase_labels(label: str) -> None:
            for pkg in phase:
                phase[pkg] = label

        # 1) "Preparing" — force-stop each configured package individually,
        #    verify it is dead, and clear background apps to free RAM.
        #    Only configured/selected packages are targeted; Termux and
        #    system apps are never touched.
        _transition_lifecycle("PREPARING", "prepare_packages")
        _start_session.mark("package_preparation_begin", package_count=len(entries))
        _set_all_phase("Preparing", "Stopping configured packages...")
        packages_sl = [e["package"] for e in entries]
        keep_alive  = ["com.termux"] + packages_sl

        # 1a) Do not run a global background kill during normal Start.
        # Probe p-52aeb6420f showed post-launch visual disruption; keep Start
        # bounded to selected package prep only.
        _start_log.info(
            "[DENG_REJOIN_START_SAFETY] action=skip_global_kill "
            "reason=normal_start_must_not_close_all_apps keep_alive=%s",
            ",".join(keep_alive),
        )

        # Cloud Phone Extreme memory behavior is the default and only memory
        # preparation path for this tool. It is automatic, never selectable,
        # and keeps Termux/Roblox/core Android packages protected.
        try:
            _cloud_mem = android.optimize_cloud_phone_memory(keep_alive)
            _start_log.info(
                "[DENG_REJOIN_CLOUD_PHONE_MEMORY] disabled=%s stopped=%s skipped=%s"
                " failed=%s recovery=%s cooldown_skipped=%s uninstall_used=false",
                ",".join(str(p) for p in _cloud_mem.get("disabled") or []) or "none",
                ",".join(str(p) for p in _cloud_mem.get("stopped") or []) or "none",
                json.dumps(_cloud_mem.get("skipped") or [], separators=(",", ":")),
                json.dumps(_cloud_mem.get("failed") or [], separators=(",", ":")),
                _cloud_mem.get("recovery_command", "pm enable com.google.android.gms"),
                str(_cloud_mem.get("cooldown_skipped", False)).lower(),
            )
            if not _cloud_mem.get("cooldown_skipped"):
                _start_log.info("[*] Recovery: %s", _cloud_mem.get("recovery_command"))
        except Exception as _exc:  # noqa: BLE001
            _start_log.debug("cloud phone memory optimization error (non-fatal): %s", _exc)

        # 1b) Also force-stop any OTHER detected Roblox packages not in our list.
        try:
            android.force_stop_packages_except(packages_sl, cfg.get("package_detection_hints"))
        except Exception:  # noqa: BLE001
            _start_log.debug("start: force_stop_packages_except error (non-fatal)")

        # 1c) Force-stop each CONFIGURED package individually with PID verification.
        #     Termux (com.termux) and system apps are excluded by design.
        import logging as _logging
        _prep_root = android.detect_root()
        for _prep_entry in entries:
            _prep_pkg = _prep_entry["package"]
            _pid_before = ""
            _pid_after  = ""
            _stop_ok    = False
            _stop_err   = ""
            try:
                _pid_before = android.get_package_pid(_prep_pkg, _prep_root)
                _stop_res   = android.force_stop_package(_prep_pkg, _prep_root)
                _stop_ok    = bool(_stop_res.ok)
                if not _stop_ok:
                    _stop_err = (_stop_res.stderr or "")[:80]
                # Verify process is dead; retry once if still alive.
                import time as _t
                _t.sleep(0.3)
                _pid_after = android.get_package_pid(_prep_pkg, _prep_root)
                if _pid_after:
                    _t.sleep(0.8)
                    android.force_stop_package(_prep_pkg, _prep_root)
                    _t.sleep(0.3)
                    _pid_after = android.get_package_pid(_prep_pkg, _prep_root)
                _stop_ok = not bool(_pid_after)
            except Exception as _exc:  # noqa: BLE001
                _stop_err = str(_exc)[:80]
            _start_log.info(
                "[DENG_REJOIN_PREPARE_PACKAGE] package=%s force_stop_attempt=1"
                " pid_before=%s pid_after=%s success=%s error=%s",
                _prep_pkg, _pid_before or "none", _pid_after or "none",
                str(_stop_ok).lower(), _stop_err,
            )

        # ── PHASE 1 (continued): batch Clear Cache — all packages, no delays ───
        opt = cfg.get("optimization") if isinstance(cfg.get("optimization"), dict) else {}
        prep_gfx: dict[str, str] = {}
        prep_cache: dict[str, str] = {}
        package_names = [entry["package"] for entry in entries]
        _start_session.mark("batch_clear_cache_begin", package_count=len(entries))
        safe_io.set_crash_context(phase="batch_clear_cache", package_count=len(entries))
        # Label silently before the mass clear (avoid render during root shells —
        # Termux/Python 3.13 SIGSEGV risk), then redraw as soon as it finishes.
        _set_all_phase_labels("Clear Cache")
        try:
            prep_cache = _run_start_batch_cache_clear(
                package_names,
                root_info=_prep_root,
            )
        except Exception as _exc:  # noqa: BLE001
            _start_log.debug("start: batch cache clear error: %s", _exc)
            prep_cache = {pkg: "Failed" for pkg in package_names}
        _set_all_phase("Preparing", "Applying settings...")
        _start_session.mark("batch_low_graphics_begin", package_count=len(entries))
        for entry in entries:
            package = entry["package"]
            low = bool(opt.get("low_graphics_enabled", True)) and bool(
                entry.get("low_graphics_enabled", True)
            )
            try:
                prep_gfx[package] = android.apply_low_graphics_optimization(
                    package, enabled=low
                )
            except Exception:  # noqa: BLE001
                prep_gfx[package] = "error"
        _start_session.mark("batch_clear_cache_done", package_count=len(entries))
        _start_session.mark("package_preparation_done", package_count=len(entries))

        # 2) Compute window layout silently (no public phase change).
        _set_all_phase("Preparing", "Computing layout...")
        try:
            _start_session.mark("layout_begin")
            cfg, _layout_note = _prepare_automatic_layout(cfg, entries)
            _start_session.mark("layout_done", note=_layout_note)
            _start_log.debug("start: layout note=%s", _layout_note)
        except Exception as _exc:  # noqa: BLE001
            _start_log.debug("start: layout error (non-fatal): %s", _exc)
            _start_session.mark("layout_done", error=str(_exc)[:160])

        # 4) Dock Termux silently (no public phase change).
        _termux_minimize_result: dict[str, Any] = {}
        try:
            _dock_enabled = bool(cfg.get("termux_dock_enabled", False))
            if _dock_enabled:
                _termux_minimize_result = _enforce_termux_left_layout(cfg)
                _start_log.debug("termux_minimize: %s", _termux_minimize_result)
            else:
                _termux_minimize_result = {"ok": False, "skipped": True,
                                           "reason": "disabled by config"}
        except Exception as _exc:  # noqa: BLE001
            _start_log.debug("termux_minimize error (non-fatal): %s", _exc)
            _termux_minimize_result = {"ok": False, "skipped": True,
                                       "reason": f"exception: {_exc}"}

        now_iso = datetime.now(timezone.utc).isoformat()
        start_times: dict[str, str] = dict(cfg.get("package_start_times") or {})
        for entry in entries:
            start_times[entry["package"]] = now_iso
        cfg["package_start_times"] = start_times
        cfg["monitor_started_at"] = time.time()

        # ── Launch URL confirmation (safe — never expose the raw URL) ────────
        _any_url = any(
            str(effective_private_server_url(entry, runtime_cfg) or "").strip()
            for entry in runtime_entries
        )
        if _any_url:
            _start_log.info("start: Launch URL configured — sending private server deep link to each clone")
        else:
            _start_log.info("start: No launch URL configured — clones will open Roblox home")

        # ── Bootstrap watchdog daemon BEFORE staggered launch ─────────────────
        from .supervisor import (
            STATUS_FAILED as _STATUS_FAILED,
            STATUS_LAUNCHING as _STATUS_LAUNCHING,
            STATUS_ONLINE as _STATUS_ONLINE,
            STATUS_PENDING as _STATUS_PENDING,
            STATUS_READY as _STATUS_READY,
            STATUS_WAITING as _STATUS_WAITING,
        )

        _live_cfg_boot = dict(runtime_cfg)
        _live_cfg_boot["package_start_times"] = dict(start_times)
        _live_cfg_boot["monitor_started_at"] = cfg.get("monitor_started_at")
        _sup_sub_boot = dict(sup)
        _hci_boot = int(_sup_sub_boot.get("health_check_interval_seconds", 10))
        _sup_sub_boot["health_check_interval_seconds"] = max(10, _hci_boot)
        _live_cfg_boot["supervisor"] = _sup_sub_boot
        _live_cfg_boot["start_session_id"] = _start_session_id
        _live_cfg_boot["screen_mode"] = str(cfg.get("screen_mode") or _start_screen_mode)
        runtime_entries_boot = _ensure_presence_auth_for_entries(runtime_entries, cfg)
        _boot_status = {e["package"]: _STATUS_READY for e in entries}
        _supervisor = WatchdogSupervisor(
            runtime_entries_boot, _live_cfg_boot, initial_status=_boot_status
        )
        _supervisor_ref = _supervisor
        try:
            from . import monitor_autostart as _mon_auto_boot
            _mon_auto_boot.set_active_supervisor(_supervisor)
            _try_autostart_monitor_bridge(cfg)
        except Exception:  # noqa: BLE001
            pass
        _supervisor.start_daemon(
            display_interval=WatchdogSupervisor.DASHBOARD_RENDER_INTERVAL_SECONDS,
            render_callback=None,
        )
        _start_session.mark("watchdog_daemon_started", package_count=len(entries))
        try:
            from .termux_session import ensure_termux_session_alive as _ensure_termux_alive

            _ensure_termux_alive(cfg)
        except Exception:  # noqa: BLE001
            pass
        for _boot_entry in entries:
            phase[_boot_entry["package"]] = "Ready"

        for _prep_entry in entries:
            try:
                from .package_key import ensure_package_key_for_start as _epkfs_batch

                _epkfs_batch(
                    _prep_entry["package"],
                    runtime_cfg,
                    root_enabled=bool(runtime_cfg.get("root_mode_enabled", False)),
                )
            except Exception:  # noqa: BLE001
                pass

        # ── PHASE 2: staggered launching (30s between packages) ───────────────
        launch_ok: dict[str, bool] = {}
        launch_err: dict[str, str] = {}
        launch_attempted: dict[str, bool] = {}
        _transition_lifecycle("LAUNCHING", "launch_packages")
        _start_session.mark("package_launch_begin", package_count=len(entries))
        _enforce_configured_screen_mode(cfg, packages_sl, phase="before_launch")

        def _on_stagger_launch_sent(launched_pkg: str) -> None:
            if launched_pkg not in _supervisor._package_opened:
                _supervisor.mark_package_launched(launched_pkg)
            try:
                _supervisor.sync_stagger_display_status()
            except Exception:  # noqa: BLE001
                pass

        try:
            for index, entry in enumerate(entries, start=1):
                package = entry["package"]
                runtime_entry = runtime_entry_by_pkg.get(package, entry)
                launch_attempted[package] = True
                for later in entries[index:]:
                    phase[later["package"]] = "Ready"
                phase[package] = "Launching"
                _render_phase("Launching clone...")
                package_cfg = dict(runtime_cfg)
                package_cfg["roblox_package"] = package
                package_cfg["__on_launch_sent"] = _on_stagger_launch_sent
                from .config import private_url_launch_context as _purl_ctx
                _url_context = _purl_ctx(runtime_entry, runtime_cfg)
                _has_url = _url_context.get("url_mode") == "private_url"
                result = perform_rejoin(package_cfg, reason="start", package_entry=runtime_entry)
                launch_ok[package] = result.success
                launch_err[package] = result.error or ""
                if not result.success:
                    phase[package] = "Failed"
                    _supervisor._set_status(package, _STATUS_FAILED)
                    _render_phase()
                    continue

                if package not in _supervisor._package_opened:
                    _supervisor.mark_package_launched(package)
                _supervisor._set_status(package, _STATUS_WAITING)
                phase[package] = _STATUS_WAITING
                launch_ok[package] = True
                launch_err[package] = ""
                _render_phase("Launching...")
                _start_log.info(
                    "[DENG_REJOIN_STAGGERED_LAUNCH] package=%s index=%d/%d"
                    " launcher=%s phase=launching success=true watchdog_daemon=%s",
                    package,
                    index,
                    len(entries),
                    "private_url" if _has_url else "app_only",
                    str(_supervisor.watchdog_thread_alive()).lower(),
                )
                if index < len(entries):
                    import time as _t
                    from .supervisor import WatchdogSupervisor as _WS
                    _stagger_deadline = _t.monotonic() + _WS.LAUNCH_STAGGER_SECONDS
                    while _t.monotonic() < _stagger_deadline:
                        _render_phase_throttled()
                        _stagger_remain = _stagger_deadline - _t.monotonic()
                        _t.sleep(max(0.0, min(1.0, _stagger_remain)))
        finally:
            if not getattr(_supervisor, "_all_launches_completed", False):
                _supervisor.mark_all_launches_completed()

        _start_session.mark("package_launch_done", success_count=sum(1 for v in launch_ok.values() if v))
        _start_session.mark("all_launches_completed", package_count=len(entries))

        # 6) Grace wait before verifying layout — keep packages shown as
        #    "Launching" (no "Waiting" label shown in public UI).
        grace_wait = int(sup.get("launch_grace_seconds", 15))
        import time as _time
        _time.sleep(max(0.0, max(5, grace_wait)))

        # 8) Verify layout silently — no "Resizing" label shown (user feedback:
        #    showing "Resizing" after launching is confusing/useless; the
        #    supervisor's real-time detection will update the state next).
        _layout_verify: dict[str, bool] = {}
        _layout_diag: list[dict[str, Any]] = []
        try:
            _start_session.mark("layout_begin", phase="post_launch_verify")
            _layout_verify, _layout_diag = _verify_layout_post_launch(cfg, entries)
            _start_session.mark("layout_done", phase="post_launch_verify")
            _start_log.debug("post-launch layout verify: %s", _layout_verify)
        except Exception as _exc:  # noqa: BLE001
            _start_log.debug("post-launch verify error: %s", _exc)
            _start_session.mark("layout_done", phase="post_launch_verify", error=str(_exc)[:160])

        _home_landscape_state: dict[str, Any] = {}
        try:
            _home_landscape_state = android.enforce_landscape_home_state(
                phase="after_start",
                screen_mode_config=str(cfg.get("screen_mode") or "auto"),
            )
            from .logger import log_event as _log_event
            _log_event(
                configure_logging(),
                "info",
                "[DENG_REJOIN_LANDSCAPE_STATE]",
                phase=str(_home_landscape_state.get("phase", "after_start")),
                wm_size=json.dumps(_home_landscape_state.get("wm_size", {}), sort_keys=True),
                wm_density=json.dumps(_home_landscape_state.get("wm_density", {}), sort_keys=True),
                user_rotation=_home_landscape_state.get("user_rotation", ""),
                accelerometer_rotation=_home_landscape_state.get("accelerometer_rotation", ""),
                display_rect=json.dumps(_home_landscape_state.get("display_rect", {}), sort_keys=True),
                final_layout_mode=_home_landscape_state.get("final_layout_mode", cfg.get("screen_mode", "auto")),
                screen_mode_config=_home_landscape_state.get("screen_mode_config", cfg.get("screen_mode", "auto")),
                correction_applied=json.dumps(_home_landscape_state.get("correction_applied", []), sort_keys=True),
                launcher_bounds=json.dumps(_home_landscape_state.get("launcher_bounds", {}), sort_keys=True),
                black_bar_suspected=_home_landscape_state.get("black_bar_suspected", "no"),
            )
            _launcher_bounds = _home_landscape_state.get("launcher_bounds", {})
            _lb = _launcher_bounds.get("bounds") if isinstance(_launcher_bounds, dict) else None
            _display_rect = _home_landscape_state.get("display_rect", {})
            _match = "unknown"
            if isinstance(_lb, list) and len(_lb) == 4 and isinstance(_display_rect, dict):
                _bw = max(0, int(_lb[2]) - int(_lb[0]))
                _bh = max(0, int(_lb[3]) - int(_lb[1]))
                _dw = int(_display_rect.get("width") or 0)
                _dh = int(_display_rect.get("height") or 0)
                _match = "yes" if (_dw >= _dh and _bw >= _bh) else "no"
            _log_event(
                configure_logging(),
                "info",
                "[DENG_REJOIN_HOME_ORIENTATION_CHECK]",
                launcher_package=_launcher_bounds.get("launcher_package", "") if isinstance(_launcher_bounds, dict) else "",
                launcher_bounds=json.dumps(_lb, sort_keys=True),
                expected_landscape_bounds=json.dumps(_display_rect, sort_keys=True),
                match=_match,
            )
        except Exception as _exc:  # noqa: BLE001
            _start_log.debug("landscape home check error: %s", _exc)

        # ── Save start diagnostics JSON (silent, internal only) ───────────────
        try:
            import time as _t
            _evidence_summary: list[dict[str, Any]] = []
            for entry in entries:
                pkg = entry["package"]
                try:
                    ev = android.get_package_alive_evidence(pkg)
                except Exception:  # noqa: BLE001
                    ev = {}
                _evidence_summary.append({
                    "package":     pkg,
                    "running":     bool(ev.get("running")),
                    "root_running": bool(ev.get("root_running")),
                    "window":      bool(ev.get("window")),
                    "surface":     bool(ev.get("surface")),
                    "task":        bool(ev.get("task")),
                    "foreground":  bool(ev.get("foreground")),
                    "alive":       bool(ev.get("alive")),
                    "private_url_mode": _purl_ctx(runtime_entry_by_pkg.get(pkg, entry), runtime_cfg).get("private_url_mode", "global"),
                    "url_mode": _purl_ctx(runtime_entry_by_pkg.get(pkg, entry), runtime_cfg).get("url_mode", "app_only"),
                    "url_config_source": _purl_ctx(runtime_entry_by_pkg.get(pkg, entry), runtime_cfg).get("url_config_source", "blank"),
                    "private_url_set": _purl_ctx(runtime_entry_by_pkg.get(pkg, entry), runtime_cfg).get("url_mode") == "private_url",
                })
            try:
                from . import freeform_enable as _ff
                _ff_ok, _ff_total = _ff.setup_freeform_capabilities_silent()
            except Exception:  # noqa: BLE001
                _ff_ok, _ff_total = 0, 0
            _save_start_diagnostics({
                "timestamp_unix":     int(_t.time()),
                "artifact_sha256":    cfg.get("__artifact_sha256", ""),
                "version":            cfg.get("__version", ""),
                "selected_packages":  [e["package"] for e in entries],
                "freeform_caps":      {"ok": _ff_ok, "total": _ff_total},
                "layout_verify":      _layout_verify,
                "layout_diagnostics": _layout_diag,
                "evidence":           _evidence_summary,
                "termux_minimize":    _termux_minimize_result,
                "landscape_home_state": _home_landscape_state,
                "launches": {
                    pkg: {"ok": launch_ok.get(pkg, False),
                          "error": launch_err.get(pkg, "") or ""}
                    for pkg in launch_ok
                },
            })
        except Exception as _exc:  # noqa: BLE001
            _start_log.debug("save start diagnostics error: %s", _exc)

        # ── Build initial status table ────────────────────────────────────────
        from . import package_state as _ps

        initial_status: dict[str, str] = {}
        table_rows: list[tuple] = []
        detail_rows: list[dict[str, str]] = []
        for index, entry in enumerate(entries, start=1):
            pkg      = entry["package"]
            username = _account_username_for_table(entry)
            cstat    = prep_cache.get(pkg, "Skipped")
            gstat    = prep_gfx.get(pkg, "Skipped")
            if not launch_ok[pkg]:
                err = launch_err[pkg]
                state = "Failed"
                safe_err = mask_urls_in_text(err) or "Launch failed"
                stat_internal = (safe_err[:120] + "...") if len(safe_err) > 123 else safe_err
            else:
                from .supervisor import STATUS_WAITING
                state = STATUS_WAITING
                stat_internal = "launch command sent; watchdog will verify presence"
            initial_status[pkg] = state
            table_rows.append((index, pkg, username, state))
            detail_rows.append(
                {"package": pkg, "cache": cstat, "graphics": gstat, "launch_detail": stat_internal}
            )

        # Log verbose detail to debug log only — never to stdout
        show_detail = (
            bool(getattr(args, "verbose", False))
            or bool(getattr(args, "debug", False))
            or str(cfg.get("log_level", "")).upper() == "DEBUG"
        )
        if show_detail:
            for row in detail_rows:
                _start_log.debug(
                    "start detail: pkg=%s cache=%s gfx=%s launch=%s",
                    row["package"], row["cache"], row["graphics"], row["launch_detail"],
                )

        # ── Webhook ─────────────────────────────────────────────────────────
        cfg["package_start_times"] = start_times
        save_config(cfg)

        # ── Check if any package launched ───────────────────────────────────
        attempted_count = sum(1 for v in launch_attempted.values() if v)
        success_count = sum(1 for pkg, attempted in launch_attempted.items() if attempted and launch_ok.get(pkg))
        if attempted_count and success_count == 0:
            reasons = [v for v in launch_err.values() if v]
            best_reason = reasons[0][:80] if reasons else "all launch attempts failed"
            _clear_terminal()
            print_banner(use_color=use_color)
            print()
            print("Launch Failed")
            print()
            print("  Package was selected but Android did not launch it.")
            print()
            detail = best_reason
            if len(detail) > 240 and "\n" not in detail:
                detail = detail[:240] + "..."
            print(f"  Detail: {detail}")
            if "\n" in best_reason:
                print()
                print("  Evidence:")
                for ln in best_reason.splitlines()[1:12]:
                    print(f"    {ln}")
            _release_start_lock("normal_exit")
            _start_session.finish("launch_failed")
            return 1

        # ── Supervisor dashboard — watchdog already running on daemon thread ─
        _live_cfg = dict(runtime_cfg)
        _live_cfg["package_start_times"] = start_times
        _live_cfg["monitor_started_at"] = cfg.get("monitor_started_at")
        _sup_sub = dict(
            cfg.get("supervisor") if isinstance(cfg.get("supervisor"), dict) else {}
        )
        _hci_raw = int(_sup_sub.get("health_check_interval_seconds", 10))
        _sup_sub["health_check_interval_seconds"] = max(10, _hci_raw)
        _live_cfg["supervisor"] = _sup_sub
        _live_cfg["start_session_id"] = _start_session_id
        _live_cfg["screen_mode"] = _start_screen_mode
        _supervisor.cfg = _live_cfg
        safe_io.set_crash_context(
            phase="supervisor_start",
            session_id=_start_session_id,
            screen_mode=_start_screen_mode,
            package_count=len(runtime_entries),
        )
        for index, entry in enumerate(entries, start=1):
            pkg = entry["package"]
            if not launch_ok.get(pkg):
                _supervisor._set_status(pkg, _STATUS_FAILED)
            elif _supervisor.status_map.get(pkg) not in {_STATUS_LAUNCHING, _STATUS_WAITING, _STATUS_ONLINE}:
                _supervisor._set_status(pkg, _STATUS_WAITING)
        _live_map = _supervisor.status_map
        _start_session.mark("supervisor_begin", package_count=len(runtime_entries))

        # Public state map: internal states → user-facing labels.
        # Allowed public states: Layout, Launching, Online, Relaunching, Dead, Failed.
        # Internal/noisy states (Docking, Layout, Waiting, Checking etc.)
        # never reach the live supervisor dashboard.
        _STATE_DISPLAY_MAP: dict[str, str] = {
            # Live watchdog states — keep as-is.
            "Online":           "Online",
            "In Game":          "Online",
            "In Lobby":         "Online",
            "Join Failed":      "Dead",
            "Wrong Game / Wrong Server": "Dead",
            "Dead":             "Dead",
            "Relaunching":      "Relaunching",
            "Launching":        "Launching",
            "No Heartbeat":     "No Heartbeat",
            "Waiting":          "Launching",
            "Checking":         "Launching",
            "Pending":          "Launching",
            "Join Unconfirmed": "Launching",
            "Preparing":        "Preparing",
            "Clear Cache":      "Clear Cache",
            "Unknown":          "Dead",
            # Alive + in-game states → Online
            "Launched":         "Online",
            "In Server":        "Online",
            "Background":       "Online",
            "Warning":          "Online",
            # App open but not in game yet — allow lobby transition window.
            "Lobby":            "Online",
            # Recovery / disconnect states
            "Reconnecting":     "Relaunching",
            "Disconnected":     "Dead",
            "Offline":          "Dead",
        }

        def _live_dashboard() -> None:
            """Clear screen and redraw banner + live telemetry table."""
            safe_io.set_crash_context(phase="render_loop", session_id=_start_session_id)
            import time as _ts_time
            _now_ts = _ts_time.time()
            _usage_cache = getattr(_live_dashboard, "_usage_cache", None)
            if not isinstance(_usage_cache, dict):
                _usage_cache = {}
                setattr(_live_dashboard, "_usage_cache", _usage_cache)

            _metric_states = {"Online", "In Lobby", "No Heartbeat"}

            def _get_runtime(pkg: str) -> str:
                raw_state = _live_map.get(pkg, "Unknown")
                disp = _STATE_DISPLAY_MAP.get(raw_state, raw_state)
                if disp not in _metric_states and raw_state not in _metric_states:
                    return "0s"
                start_ts = getattr(_supervisor, "_online_start_ts", {}).get(pkg, 0.0)
                if not start_ts:
                    try:
                        from agent.status_monitor_runtime import effective_runtime_seconds
                        frozen = effective_runtime_seconds(pkg, _now_ts)
                        if frozen is not None:
                            return format_runtime_compact(max(0.0, float(frozen)))
                    except Exception:  # noqa: BLE001
                        pass
                    return "0s"
                return format_runtime_compact(max(0.0, _now_ts - start_ts))

            def _get_usage(pkg: str) -> str:
                raw_state = _live_map.get(pkg, "Unknown")
                disp = _STATE_DISPLAY_MAP.get(raw_state, raw_state)
                if disp in ("Dead", "Failed"):
                    return "N/A"
                if disp in ("Preparing", "Clear Cache", "Launching", "Waiting", "Relaunching", "Checking", "Checking..."):
                    return "0 MB"
                cached = _usage_cache.get(pkg)
                if isinstance(cached, tuple) and _now_ts - float(cached[0]) < 9.0:
                    return str(cached[1] or "0 MB")
                try:
                    usage = android.get_package_ram_usage(
                        pkg, getattr(_supervisor, "_root_info", None),
                    )
                    label = str(usage.get("usage_mb") or "").strip() or "0 MB"
                except Exception:  # noqa: BLE001
                    label = "0 MB"
                _usage_cache[pkg] = (_now_ts, label)
                return label

            def _display_state(pkg: str) -> str:
                raw_state = str(_live_map.get(pkg, "") or "").strip()
                if not raw_state or raw_state == "Unknown":
                    return "Checking..."
                disp = _STATE_DISPLAY_MAP.get(raw_state, raw_state) or "Checking..."
                return disp

            lines = [banner_text(use_color=use_color), ""]
            ram_label = _get_ram_label()
            if ram_label:
                for _ram_line in ram_label.split("\n"):
                    lines.append(termux_ui.fit_line(f"  {_ram_line}"))
                lines.append("")
            live_rows = [
                (
                    i + 1,
                    e["package"],
                    _account_username_for_table(e),
                    _display_state(e["package"]),
                    _get_runtime(e["package"]),
                    _get_usage(e["package"]),
                )
                for i, e in enumerate(entries)
            ]
            lines.append(build_start_table(live_rows, use_color=use_color))
            _clear_terminal(clear_scrollback=False)
            safe_io.write_stdout_block("\n".join(lines) + "\n")

        _transition_lifecycle("MONITORING", "launch_complete")
        try:
            from .logger import configure_logging, log_event
            log_event(
                configure_logging(),
                "info",
                "[DENG_REJOIN_MONITOR_ENTER]",
                packages=[e["package"] for e in entries],
                after_launch="true",
                main_thread_alive="true",
                watchdog_alive=str(_supervisor.watchdog_thread_alive()).lower(),
            )
        except Exception:  # noqa: BLE001
            pass

        _supervisor.set_render_callback(_live_dashboard)
        # The reporter is deliberately separate from the watchdog: it starts
        # only after Start reaches live monitoring and cannot alter package state.
        webhook.record_webhook_trace(
            source="cmd_start", start_selected=True, config_path_read=str(CONFIG_PATH),
            webhook_mode=cfg.get("webhook_mode"),
        )
        _webhook_reporter = webhook.WebhookStatusReporter(cfg, _supervisor, entries, save_config)
        _webhook_reporter.start()
        signal.signal(signal.SIGTERM, _supervisor._handle_stop)
        signal.signal(signal.SIGINT, _supervisor._handle_stop)
        _allowed_stop_sources = {"sigterm", "sigint", "ctrl_c", "user_exit", "fatal_error"}
        try:
            _start_session.mark("supervisor_loop")
            import time as _monitor_time
            _dashboard_interval = WatchdogSupervisor.DASHBOARD_RENDER_INTERVAL_SECONDS
            _termux_keepalive_at = 0.0
            while not _supervisor.stop_event.is_set():
                _render_started = _monitor_time.monotonic()
                _live_dashboard()
                if (_monitor_time.monotonic() - _termux_keepalive_at) >= 1800.0:
                    try:
                        from .termux_session import ensure_termux_session_alive as _ensure_termux_alive

                        _ensure_termux_alive(cfg)
                    except Exception:  # noqa: BLE001
                        pass
                    _termux_keepalive_at = _monitor_time.monotonic()
                _sleep_for = max(0.0, _dashboard_interval - (_monitor_time.monotonic() - _render_started))
                if _supervisor.stop_event.wait(_sleep_for):
                    break
        except KeyboardInterrupt:
            _shutdown_reason = "ctrl_c"
            _log_stop_request("ctrl_c", allowed=True)
            _supervisor.stop_source = "ctrl_c"
            _supervisor.stop("ctrl_c")
        except Exception as exc:  # noqa: BLE001
            _start_log.exception("Supervisor monitor terminated with error: %s", exc)
            _shutdown_reason = "fatal_error"
            _log_stop_request("fatal_error", allowed=True, stack=str(exc)[:1000])
            _supervisor.stop_source = "fatal_error"
            _supervisor.stop("fatal_error")
            _transition_lifecycle("STOPPING", "fatal_error")
            try:
                from . import monitor_autostart as _mon_auto
                _mon_auto.set_active_supervisor(None)
            except Exception:  # noqa: BLE001
                pass
            _release_start_lock("fatal_error")
            _transition_lifecycle("STOPPED", "fatal_error")
            _start_session.finish("fatal_error")
            return _report_start_dashboard_crash(exc, session=_start_session)
        finally:
            _webhook_reporter.stop()

        _stop_source = str(getattr(_supervisor, "stop_source", "") or "").strip()
        if _stop_source in _allowed_stop_sources:
            _shutdown_reason = _stop_source
        elif _stop_source:
            try:
                from .logger import configure_logging, log_event
                log_event(
                    configure_logging(),
                    "info",
                    "[DENG_REJOIN_UNEXPECTED_EXIT_GUARD]",
                    reason=_stop_source or "monitor_loop_exited",
                    prevented="false",
                )
            except Exception:  # noqa: BLE001
                pass

        _transition_lifecycle("STOPPING", _shutdown_reason)
        # Best-effort clean exit: clear screen so terminal is not littered.
        try:
            _clear_terminal()
        except Exception:  # noqa: BLE001
            pass
        # Drop the supervisor reference from the monitor autostart so the
        # bridge stops pushing package state once Start ends. The bridge
        # itself keeps running so the device still reports as connected
        # (with an empty packages list) when the user returns to the menu.
        try:
            from . import monitor_autostart as _mon_auto
            _mon_auto.set_active_supervisor(None)
        except Exception:  # noqa: BLE001
            pass
        _release_start_lock(_shutdown_reason)
        _transition_lifecycle("STOPPED", _shutdown_reason)
        _start_session.finish(_shutdown_reason)
        try:
            from .termux_session import release_termux_wake_lock as _release_termux_wake

            _release_termux_wake()
        except Exception:  # noqa: BLE001
            pass
        # Real-device evidence (probe ``p-47fa33562a``): on Termux, Python
        # finalization after a supervisor stop sometimes segfaults inside
        # libc cleanup (atexit handlers / threading shutdown / file-handle
        # close on the curl subprocess pipes), printing ``Segmentation
        # fault`` to the public terminal even though everything stopped
        # cleanly.  Skip Python finalization by calling ``os._exit`` when
        # running on Termux/Android — the supervisor has already flushed
        # logs and persisted state.  Non-Termux contexts (CI, tests, dev
        # box) keep the normal return path so unittest can introspect.
        _termux_exit_clean()
        return 0
    except KeyboardInterrupt:
        # Pre-supervisor Ctrl+C: just exit quietly, no traceback.
        _shutdown_reason = "ctrl_c"
        _transition_lifecycle("STOPPING", "ctrl_c")
        _log_stop_request("ctrl_c", allowed=True)
        try:
            if _supervisor_ref is not None:
                _supervisor_ref.stop("ctrl_c")
        except Exception:  # noqa: BLE001
            pass
        _release_start_lock(_shutdown_reason)
        _transition_lifecycle("STOPPED", _shutdown_reason)
        _start_session.finish(_shutdown_reason)
        try:
            _clear_terminal()
        except Exception:  # noqa: BLE001
            pass
        _termux_exit_clean()
        return 0
    except Exception as exc:  # noqa: BLE001 - command boundary.
        import logging as _logging
        _logging.getLogger("deng.rejoin.start").debug("cmd_start error: %s", exc)
        _transition_lifecycle("STOPPING", "fatal_error")
        _log_stop_request("fatal_error", allowed=True, stack=str(exc)[:1000])
        try:
            if _supervisor_ref is not None:
                _supervisor_ref.stop("fatal_error")
        except Exception:  # noqa: BLE001
            pass
        _release_start_lock("fatal_error")
        _transition_lifecycle("STOPPED", "fatal_error")
        _start_session.finish("fatal_error")
        print(f"Agent start failed: {exc}")
        return 1


def _run_diag_in_subprocess(args: list[str], *, timeout: int = 8) -> tuple[bool, str]:
    """Run a diagnostic shell command in an isolated child process.

    If the child crashes (SIGSEGV) the parent survives — no "Segmentation
    fault" text reaches the user's terminal.  Returns ``(ok, output)`` where
    output is bounded to ~4 KB.  Never raises.
    """
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
        )
        body = (result.stdout or "")[:4096]
        if result.returncode != 0 and not body:
            return False, (result.stderr or "").strip()[:4096]
        return True, body
    except FileNotFoundError:
        return False, ""
    except subprocess.TimeoutExpired:
        return False, "(timed out)"
    except Exception as exc:  # noqa: BLE001
        return False, f"(child error: {exc})"


def cmd_support_bundle(args: argparse.Namespace) -> int:
    """Hidden: write a support bundle for offline diagnosis.

    SAFETY CONTRACT (must not regress):
      * READ-ONLY.  Does NOT modify any config, prefs XML, database, or files
        outside the bundle path under LOG_DIR.
      * Does NOT start any supervisor / threads.
      * Does NOT alter terminal mode (no readline calls, no raw mode, no
        signal handler installs).
      * All risky shell commands (``dumpsys``, ``cmd activity``, ``pidof``)
        run in isolated subprocesses with captured stdout/stderr — a SIGSEGV
        in any child does NOT propagate to the parent terminal.
      * Catches every exception and returns 0 (or a small non-zero) — never
        raises a Python traceback to the public terminal.
      * Idempotent: running it then running ``deng-rejoin`` normally must work.
    """
    from datetime import datetime as _dt
    from .constants import LOG_DIR
    try:
        from .logger import silence_public_loggers
        silence_public_loggers()
    except Exception:  # noqa: BLE001
        pass

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass

    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    bundle_path = LOG_DIR / f"support-bundle-{ts}.txt"

    lines: list[str] = []
    def _add(s: str = "") -> None:
        try:
            lines.append(s if isinstance(s, str) else str(s))
        except Exception:  # noqa: BLE001
            pass

    _add(f"DENG Tool: Rejoin Support Bundle  {ts}")
    _add(f"Product version: {VERSION}")
    _add("(read-only diagnostic — no config or prefs were modified)")
    _add("")

    cfg: dict[str, Any] | None = None
    try:
        cfg = load_config()
    except Exception:  # noqa: BLE001
        cfg = None

    # ── Display (subprocess-isolated) ─────────────────────────────────────
    _add("== Display ==")
    ok_size, size_out = _run_diag_in_subprocess(["wm", "size"], timeout=4)
    ok_den, den_out = _run_diag_in_subprocess(["wm", "density"], timeout=4)
    _add(f"  wm size: {('OK' if ok_size else 'FAIL')}  {size_out.strip()}")
    _add(f"  wm density: {('OK' if ok_den else 'FAIL')}  {den_out.strip()}")
    _add("")

    # ── Capabilities (in-process, fully wrapped) ─────────────────────────
    _add("== Capabilities ==")
    try:
        from . import window_apply as _wa
        caps = _wa._capability_probes()
        for k, v in caps.items():
            _add(f"  {k}: {v}")
    except Exception as exc:  # noqa: BLE001
        _add(f"  capability probe error: {exc}")
    _add("")

    packages = _packages_from_cfg(cfg)
    _add(f"== Selected packages ({len(packages)}) ==")
    for pkg in packages:
        _add(f"  - {pkg}")
    _add("")

    # ── Desired layout (compute only — DO NOT WRITE) ─────────────────────
    if packages:
        _add("== Desired layout (compute-only) ==")
        try:
            from .window_layout import (
                OUTER_MARGIN, TERMUX_LOG_FRACTION,
                calculate_split_layout, detect_display_info,
                validate_layout_rects,
            )
            disp_ = detect_display_info()
            rects = calculate_split_layout(
                packages,
                disp_.width,
                disp_.height,
                screen_mode=validate_screen_mode((cfg or {}).get("screen_mode", DEFAULT_SCREEN_MODE)),
            )
            left_end = round(disp_.width * TERMUX_LOG_FRACTION)
            errs = validate_layout_rects(
                rects, left_end + OUTER_MARGIN, OUTER_MARGIN,
                disp_.width - OUTER_MARGIN, disp_.height - OUTER_MARGIN,
            )
            for i, r in enumerate(rects, 1):
                _add(
                    f"  [{i}] {r.package}: l={r.left} t={r.top} r={r.right} b={r.bottom} "
                    f"({r.win_w}x{r.win_h}, ratio={r.win_w/max(1,r.win_h):.2f})"
                )
            _add(f"  validation: {'PASS' if not errs else 'FAIL ('+str(len(errs))+')'}")
            for e in errs[:5]:
                _add(f"    - {e}")
        except Exception as exc:  # noqa: BLE001
            _add(f"  layout compute error: {exc}")
        _add("")

        # ── Layout key discovery (READ-ONLY: no XML written) ──────────────
        _add("== Layout key discovery ==")
        try:
            from . import layout_discovery as _ld
            try:
                root_info = android.detect_root()
                root_tool = root_info.tool if root_info.available else None
            except Exception:  # noqa: BLE001
                root_tool = None
            log_path, discs = _ld.run_discovery_and_log(
                packages, root_tool=root_tool, refresh=True,
            )
            _add(f"  saved: {log_path}")
            for pkg, disc in discs.items():
                counts = disc.summary()
                _add(f"  {pkg}: files={len(disc.files_scanned)} categories={counts}")
        except Exception as exc:  # noqa: BLE001
            _add(f"  discovery error: {exc}")
        _add("")

        # ── Per-package evidence (READ-ONLY, in-process safe) ─────────────
        _add("== Root state evidence (read-only) ==")
        for pkg in packages:
            try:
                ev = android.get_package_alive_evidence(pkg)
                fg = android.current_foreground_package()
                _add(
                    f"  {pkg}: proc={ev['running']} root_proc={ev['root_running']} "
                    f"task={ev['task']} win={ev['window']} alive={ev['alive']} "
                    f"is_foreground={fg == pkg}"
                )
            except Exception as exc:  # noqa: BLE001
                _add(f"  {pkg}: evidence error: {exc}")
        _add("")

        # ── Actual bounds readback per package (READ-ONLY, isolated) ─────
        _add("== Actual bounds readback (read-only) ==")
        for pkg in packages:
            try:
                from . import window_apply as _wa
                bounds, src = _wa.read_actual_bounds(pkg)
                _add(f"  {pkg}: bounds={bounds} source={src}")
            except Exception as exc:  # noqa: BLE001
                _add(f"  {pkg}: readback error: {exc}")
        _add("")

    # ── Recent log tail ──────────────────────────────────────────────────
    _add("== Recent log tail (last 50 lines) ==")
    try:
        log_lines = _tail_lines(LOG_PATH, 50)
        for line in log_lines:
            _add(f"  {line.rstrip()}")
    except Exception as exc:  # noqa: BLE001
        _add(f"  log read error: {exc}")

    # ── Atomic write of the bundle ────────────────────────────────────────
    try:
        bundle_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"Support bundle saved: {bundle_path}")
        return 0
    except Exception as exc:  # noqa: BLE001
        # Surface ONE concise line; never a traceback.
        print(f"Support bundle could not be saved: {exc}")
        return 1


def cmd_discover_layout_keys(args: argparse.Namespace) -> int:
    """Hidden: discover real App Cloner layout keys and write the discovery log.

    Single-line output for the public terminal; full details to the discovery
    log file under LOG_DIR.  Read-only: never writes to any pkg_preferences.xml.
    """
    try:
        from .logger import silence_public_loggers
        silence_public_loggers()
    except Exception:  # noqa: BLE001
        pass
    try:
        cfg = load_config()
    except Exception:  # noqa: BLE001
        cfg = None
    packages = _packages_from_cfg(cfg) or [DEFAULT_ROBLOX_PACKAGE]
    try:
        from . import layout_discovery as _ld
        try:
            root_info = android.detect_root()
            root_tool = root_info.tool if root_info.available else None
        except Exception:  # noqa: BLE001
            root_tool = None
        log_path, _discs = _ld.run_discovery_and_log(
            packages, root_tool=root_tool, refresh=True,
        )
        print(f"Layout key discovery saved: {log_path}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"Layout key discovery could not be saved: {exc}")
        return 1


def cmd_stop(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    try:
        from .termux_session import release_termux_wake_lock

        release_termux_wake_lock()
    except Exception:  # noqa: BLE001
        pass
    stopped, message = stop_running_agent(PID_PATH, LOCK_PATH)
    print(message)
    return 0 if stopped or "not running" in message or "stale" in message else 1


def cmd_reset(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    if not _is_interactive():
        print("Reset requires interactive confirmation.")
        return 2
    if not _prompt_yes_no("Reset config to defaults", False):
        print("Reset cancelled.")
        return 0
    cfg = default_config()
    save_config(cfg)
    print("Config reset. Logs were kept.")
    if _prompt_yes_no("Wipe SQLite database", False):
        try:
            DB_PATH.unlink()
            db.init_db(DB_PATH)
            print("Database wiped and recreated.")
        except FileNotFoundError:
            db.init_db(DB_PATH)
    if _prompt_yes_no("Wipe logs", False):
        try:
            LOG_PATH.unlink()
            print("Logs wiped.")
        except FileNotFoundError:
            pass
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = _load_config_for_menu()
    if _CONFIG_RECOVERED_DEFAULTS:
        termux_ui.print_warning("Config file was missing or corrupt; recreated safe defaults")
    saved, _did_save = _run_edit_config_menu(cfg, args)
    if saved is None:
        return 0
    return 0


def _tail_lines(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    lines = max(1, min(int(lines), 1000))
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        content = handle.readlines()
    return content[-lines:]


def cmd_logs(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    lines = _tail_lines(LOG_PATH, args.lines)
    if not lines:
        print(f"No logs yet at {LOG_PATH}")
        return 0
    for line in lines:
        print(mask_urls_in_text(line.rstrip("\n")))
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    """Print runtime build proof.

    Output goes to stdout in a stable ``key: value`` format so installers,
    operators, and test harnesses can grep for the SHA or commit.
    """
    from . import build_info as _bi

    info = _bi.collect_version_info()
    lines: list[str] = []
    lines.append(f"product: {info['product']}")
    lines.append(f"product_version: {info['product_version']}")
    if info["channel"]:
        lines.append(f"channel: {info['channel']}")
    if info["git_commit_short"]:
        lines.append(f"git_commit: {info['git_commit_short']}")
    if info["artifact_sha256_short"]:
        lines.append(f"artifact_sha256: {info['artifact_sha256_short']}")
    if info["built_at_iso"]:
        lines.append(f"built_at: {info['built_at_iso']}")
    if info["install_time_iso"]:
        lines.append(f"installed_at: {info['install_time_iso']}")
    if info["install_api"]:
        lines.append(f"install_api: {info['install_api']}")
    lines.append(f"install_root: {info['install_root']}")
    lines.append(f"python: {info['python_executable']} ({info['python_version']})")
    if info["wrapper_path"]:
        lines.append(f"wrapper: {info['wrapper_path']}")
    lines.append("modules:")
    for mod, path in info["modules"].items():
        lines.append(f"  {mod}: {path or '<missing>'}")
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    """Hidden: capture sanitized device evidence for a real fix.

    Always runs read-only.  Writes a single JSON file at
    ``~/.deng-tool/rejoin/data/probes/probe-<ts>-<id>.json``.  With
    ``--upload`` it POSTs the same JSON to the install API and prints the
    short probe id the user can paste in chat.

    The command never raises; every collection step is guarded and any
    failure becomes a ``probe["errors"]`` entry.
    """
    from . import probe as _p
    from . import api_config as _api
    from .logger import configure_logging, log_event

    include_diag = bool(getattr(args, "diag", False))
    eta = "~30s" if include_diag else "~10s"
    safe_io.write_stdout(f"Collecting device evidence... ({eta})")
    started = _p.time.monotonic()
    first_run = True
    try:
        _p.PROBE_DIR.mkdir(parents=True, exist_ok=True)
        first_run = not any(_p.PROBE_DIR.glob("probe-*.json"))
    except Exception:  # noqa: BLE001
        pass
    try:
        data = _p.collect_probe(
            include_diag_startup=include_diag,
            include_heavy=bool(getattr(args, "probe_full", False) or getattr(args, "debug_heavy", False)),
            mode="full" if getattr(args, "probe_full", False) else "summary",
            last_command="probe --upload" if getattr(args, "upload", False) else "probe",
        )
    except Exception as exc:  # noqa: BLE001 — should be impossible, but be safe.
        safe_io.write_stdout(f"probe failed: {exc}")
        return 1
    elapsed = _p.time.monotonic() - started
    path = _p.save_probe(data)
    size = path.stat().st_size
    safe_io.write_stdout(
        f"probe saved: {path} ({size / 1024:.1f} KB, "
        f"{len(data.get('errors') or [])} step errors, {elapsed:.1f}s)"
    )

    if getattr(args, "upload", False):
        safe_io.write_stdout("uploading...")
        ok, info = _p.upload_probe(data)
        try:
            log_event(
                configure_logging(), "info", "[DENG_REJOIN_PROBE_UPLOAD]",
                first_run=str(first_run).lower(),
                probe_dir=str(_p.PROBE_DIR),
                probe_file=str(path),
                created_now="true",
                upload_attempted="true",
                upload_success=str(ok).lower(),
                error="" if ok else str(info)[:180],
            )
        except Exception:  # noqa: BLE001
            pass
        if ok:
            admin_url = _api.dev_probe_fetch_url(info)
            safe_io.write_stdout(f"probe uploaded: {info}")
            safe_io.write_stdout(f"probe path: {path}")
            fetch_ok = False
            fetch_detail = "skipped (no API base configured)"
            if admin_url:
                try:
                    from . import safe_http as _sh

                    status, body = _sh.get_raw(admin_url, timeout=(5, 15))
                    fetch_ok = status == 200 and info.encode() in (body or b"")
                    fetch_detail = f"HTTP {status}"
                except Exception as exc:  # noqa: BLE001
                    fetch_detail = str(exc)[:120]
            safe_io.write_stdout(
                f"probe fetch verify: {'ok' if fetch_ok else 'failed'} ({info}) [{fetch_detail}]"
            )
            if admin_url:
                safe_io.write_stdout(f"probe admin URL: {admin_url}")
        else:
            bundle_path = ""
            try:
                bundle_path = str(_p.save_upload_bundle(data, reason=str(info)))
            except Exception as exc:  # noqa: BLE001
                bundle_path = f"<bundle creation failed: {exc}>"
            safe_io.write_stdout(f"probe upload failed: {info}")
            safe_io.write_stdout(f"local probe saved: {path}")
            safe_io.write_stdout(f"upload bundle saved: {bundle_path}")
            safe_io.write_stdout("send this file manually if upload is blocked")
            return 1
    else:
        try:
            log_event(
                configure_logging(), "info", "[DENG_REJOIN_PROBE_UPLOAD]",
                first_run=str(first_run).lower(),
                probe_dir=str(_p.PROBE_DIR),
                probe_file=str(path),
                created_now="true",
                upload_attempted="false",
                upload_success="false",
                error="",
            )
        except Exception:  # noqa: BLE001
            pass
        safe_io.write_stdout("to share, either paste the JSON file in chat, or run:")
        safe_io.write_stdout("  deng-rejoin probe --upload")
    return 0


def cmd_doctor_install(args: argparse.Namespace) -> int:
    """Verify the on-disk install is wired to the new build.

    Prints one line per check (PASS / FAIL + detail) and exits 0 only if
    every check passes.  This is hidden from the public menu but documented
    for cloud-phone install verification.
    """
    from . import build_info as _bi

    results = _bi.doctor_install_checks()
    ok = _bi.doctor_install_overall_ok(results)
    for r in results:
        tag = "PASS" if r["ok"] else "FAIL"
        sys.stdout.write(f"[{tag}] {r['name']}: {r['detail']}\n")
    summary = "OK" if ok else "FAILED"
    sys.stdout.write(f"\ndoctor install: {summary}\n")
    return 0 if ok else 1


def cmd_diag_startup(args: argparse.Namespace) -> int:
    """Fast boot diagnostic: crash-log notice + minimal sanity, then hard exit.

    Never enters license checks, root scans, layout probes, or network I/O.
    Used by ``tests/hardware_verify.py`` and non-interactive crash rescue.
    """
    def _step(name: str) -> None:
        safe_io.write_stdout(f"STEP:{name}")

    def _ok(name: str, detail: str = "") -> None:
        suffix = f" {detail}" if detail else ""
        safe_io.write_stdout(f"OK:{name}{suffix}")

    def _err(name: str, exc: BaseException) -> None:
        safe_io.write_stdout(
            f"ERROR:{name} {type(exc).__name__}: {str(exc)[:160]}"
        )

    _step("entered")

    try:
        _step("ensure_app_dirs")
        ensure_app_dirs()
        _ok("ensure_app_dirs")
    except Exception as exc:  # noqa: BLE001
        _err("ensure_app_dirs", exc)
        sys.exit(2)

    notice: str | None = None
    try:
        _step("check_crash_log")
        notice = safe_io.check_and_report_crash_log()
        if notice:
            safe_io.write_stdout(notice.split("\n", 1)[0])
        _ok("check_crash_log", f"notice={'yes' if notice else 'no'}")
    except Exception as exc:  # noqa: BLE001
        _err("check_crash_log", exc)

    try:
        _step("keystore_dev_mode")
        dev = bool(keystore.DEV_MODE)
        _ok("keystore_dev_mode", f"dev={dev}")
    except Exception as exc:  # noqa: BLE001
        _err("keystore_dev_mode", exc)

    _step("finished")
    sys.exit(0)
    return 0  # pragma: no cover


def cmd_diag_startup_full(args: argparse.Namespace) -> int:
    """Full startup tracer: walk every menu-startup step for probe crash isolation.

    Each marker is flushed *before* the step runs.  If any sub-routine
    segfaults (real device incident: probe ``p-b30c47d37f``), the parent
    captures the last successful ``STEP:<name>`` line and the failing one
    is the next call.  Errors are caught and printed as ``ERROR:<step>``
    so a Python exception doesn't masquerade as a crash.

    Invoked by :func:`agent.probe._capture_diag_startup` via
    ``--diag-startup-full`` so the fast ``--diag-startup`` rescue path stays
    sub-second on live hardware.
    """
    def _step(name: str) -> None:
        sys.stdout.write(f"STEP:{name}\n")
        sys.stdout.flush()

    def _ok(name: str, detail: str = "") -> None:
        suffix = f" {detail}" if detail else ""
        sys.stdout.write(f"OK:{name}{suffix}\n")
        sys.stdout.flush()

    def _err(name: str, exc: BaseException) -> None:
        sys.stdout.write(f"ERROR:{name} {type(exc).__name__}: {str(exc)[:160]}\n")
        sys.stdout.flush()

    _step("entered")

    try:
        _step("ensure_app_dirs")
        ensure_app_dirs()
        _ok("ensure_app_dirs")
    except Exception as exc:  # noqa: BLE001
        _err("ensure_app_dirs", exc)
        return 2

    try:
        _step("check_crash_log")
        notice = safe_io.check_and_report_crash_log()
        _ok("check_crash_log", f"notice={'yes' if notice else 'no'}")
    except Exception as exc:  # noqa: BLE001
        _err("check_crash_log", exc)

    try:
        _step("keystore_dev_mode")
        dev = bool(keystore.DEV_MODE)
        _ok("keystore_dev_mode", f"dev={dev}")
    except Exception as exc:  # noqa: BLE001
        _err("keystore_dev_mode", exc)

    try:
        _step("load_config")
        cfg = load_config()
        _ok("load_config")
    except ConfigError as exc:
        _err("load_config_config_error", exc)
        cfg = default_config()
        _ok("load_config_fallback_default")
    except Exception as exc:  # noqa: BLE001
        _err("load_config", exc)
        # Don't bail — fall through with safe defaults so later steps
        # still surface evidence (the goal is *narrow*, not *abort*).
        cfg = default_config()
        _ok("load_config_fallback_default")

    try:
        _step("sync_install_id")
        cfg = _ensure_install_id_saved(cfg)
        _ok("sync_install_id")
    except Exception as exc:  # noqa: BLE001
        _err("sync_install_id", exc)

    try:
        _step("license_section_read")
        lic = cfg.setdefault("license", {})
        mode = str(lic.get("mode") or "remote").strip().lower()
        _ok("license_section_read", f"mode={mode}")
    except Exception as exc:  # noqa: BLE001
        _err("license_section_read", exc)
        return 4

    try:
        _step("license_cache_fast_path")
        cached = _license_cache_is_fresh_active(cfg.get("license") or {})
        _ok("license_cache_fast_path", f"hit={cached}")
    except Exception as exc:  # noqa: BLE001
        _err("license_cache_fast_path", exc)

    # Granular trace of the remote check.  Real-device probe
    # ``p-39924732cd`` showed the parent process dies at
    # ``STEP:license_remote_check`` — but didn't tell us WHICH sub-call.
    # We now print a STEP for each sub-call so the next crash points
    # at the exact line that segfaults libc / openssl / urllib.
    try:
        _step("license_sync_install_id")
        from .license import sync_install_id_with_config  # noqa: PLC0415
        _iid = sync_install_id_with_config(cfg.setdefault("license", {}))
        _ok("license_sync_install_id", f"iid_short={(_iid or '')[:8]}")
    except Exception as exc:  # noqa: BLE001
        _err("license_sync_install_id", exc)

    try:
        _step("license_get_device_model")
        from .license import get_public_device_model  # noqa: PLC0415
        _model = get_public_device_model()
        _ok("license_get_device_model", f"model={_model[:32]}")
    except Exception as exc:  # noqa: BLE001
        _err("license_get_device_model", exc)

    try:
        _step("license_safe_http_backend")
        from . import safe_http as _sh  # noqa: PLC0415
        _backend = _sh._http_backend()
        _ok("license_safe_http_backend", f"backend={_backend}")
    except Exception as exc:  # noqa: BLE001
        _err("license_safe_http_backend", exc)

    try:
        _step("license_curl_available")
        from . import safe_http as _sh2  # noqa: PLC0415
        _have = _sh2._curl_available()
        _ok("license_curl_available", f"have={_have}")
    except Exception as exc:  # noqa: BLE001
        _err("license_curl_available", exc)

    try:
        _step("license_remote_check_isolated")
        # Routes through the same code path cmd_menu now uses (child
        # subprocess), so a SIGSEGV here is captured as a clean
        # returncode in the diagnoser — the probe sees a normal
        # OK / ERROR line and we can keep tracing past it.
        result, msg = _remote_license_check_isolated(cfg, timeout=20)
        _ok("license_remote_check_isolated", f"result={result}")
    except Exception as exc:  # noqa: BLE001
        _err("license_remote_check_isolated", exc)

    try:
        _step("license_remote_check_direct")
        # The unprotected call — only run AFTER the isolated one so the
        # child still finishes the rest of the diagnostic if this dies.
        # If the child segfaults right here, last_step ==
        # license_remote_check_direct in the parent probe.
        result2, msg2 = _remote_license_run_check(cfg)
        _ok("license_remote_check_direct", f"result={result2}")
    except Exception as exc:  # noqa: BLE001
        _err("license_remote_check_direct", exc)

    try:
        _step("import_supervisor")
        from .supervisor import MultiPackageSupervisor  # noqa: F401, PLC0415
        _ok("import_supervisor")
    except Exception as exc:  # noqa: BLE001
        _err("import_supervisor", exc)

    try:
        _step("import_window_layout")
        from . import window_layout as _wl  # noqa: PLC0415
        _ok("import_window_layout")
    except Exception as exc:  # noqa: BLE001
        _err("import_window_layout", exc)

    try:
        _step("detect_display_info")
        from . import window_layout as _wl2  # noqa: PLC0415
        di = _wl2.detect_display_info()
        _ok("detect_display_info", f"{di.width}x{di.height} @ {di.density}")
    except Exception as exc:  # noqa: BLE001
        _err("detect_display_info", exc)

    try:
        _step("freeform_probe")
        from .freeform_enable import setup_freeform_capabilities_silent  # noqa: PLC0415
        ok_count, total = setup_freeform_capabilities_silent()
        _ok("freeform_probe", f"ok={ok_count}/{total}")
    except Exception as exc:  # noqa: BLE001
        _err("freeform_probe", exc)

    try:
        _step("banner_print")
        from .banner import banner_text  # noqa: PLC0415
        _ = banner_text(use_color=False)
        _ok("banner_print")
    except Exception as exc:  # noqa: BLE001
        _err("banner_print", exc)

    _step("finished")
    return 0


def cmd_enable_boot(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    try:
        _write_termux_boot_script()
        cfg = load_config()
        cfg["termux_boot_enabled"] = True
        save_config(cfg)
    except Exception as exc:  # noqa: BLE001 - command boundary.
        print(f"Could not enable Termux:Boot: {exc}")
        return 1
    print(f"Termux:Boot script created: {TERMUX_BOOT_SCRIPT}")
    print("Next:")
    print("  1. Install the Termux:Boot app if it is not already installed.")
    print("  2. Open Termux:Boot once.")
    print("  3. Disable battery optimization for Termux and Termux:Boot if possible.")
    print("  4. Reboot, then run deng-rejoin-status.")
    return 0


def _backup_user_data() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = CONFIG_PATH.parent / "backups" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for source in (CONFIG_PATH, DB_PATH, LOG_PATH):
        if source.exists():
            target = backup_dir / source.name
            shutil.copy2(source, target)
    return backup_dir


def _copy_update_source(source: Path, destination: Path) -> None:
    skip_names = {".git", "__pycache__", "data", "logs", "run", "backups", "config.json"}
    for item in source.iterdir():
        if item.name in skip_names:
            continue
        target = destination / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(item, target)


def cmd_update(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    print("Updating DENG Tool: Rejoin from GitHub...")
    if not shutil.which("git"):
        print("Git is not available in this Termux session.")
        print("Fallback update command:")
        print(f"  curl -fsSL {RAW_INSTALL_URL} -o install.sh && bash install.sh")
        return 1

    backup_dir = _backup_user_data()
    print(f"User config/database/log backup: {backup_dir}")
    app_dir = CONFIG_PATH.parent

    try:
        if (app_dir / ".git").exists():
            result = subprocess.run(
                ["git", "-C", str(app_dir), "pull", "--ff-only", "origin", "main"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                shell=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git pull failed")
        else:
            with tempfile.TemporaryDirectory(prefix="deng-rejoin-update-") as tmp:
                source = Path(tmp) / "repo"
                result = subprocess.run(
                    ["git", "clone", "--depth", "1", GITHUB_REMOTE, str(source)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=180,
                    shell=False,
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git clone failed")
                _copy_update_source(source, app_dir)
        created = create_market_launchers()
        print(f"Launcher files refreshed: {len(created)}")
        print("\nDoctor report:")
        print_doctor(run_doctor(load_config()))
        return 0
    except Exception as exc:  # noqa: BLE001 - update boundary.
        print(f"Update failed: {exc}")
        print("Fallback update command:")
        print(f"  curl -fsSL {RAW_INSTALL_URL} -o install.sh && bash install.sh")
        return 1


def cmd_license(args: argparse.Namespace) -> int:
    use_color = not args.no_color
    print_banner(use_color=use_color)
    if keystore.DEV_MODE:
        _print_dev_license_skipped(use_color)
        return 0
    try:
        cfg = load_config()
    except ConfigError as exc:
        _print_license_err(str(exc), use_color)
        return 1
    lic = cfg.setdefault("license", {})
    if lic.get("disabled_by_user") or not lic.get("enabled", True):
        print("License checks are turned off in your config.")
        return 0
    cfg = _ensure_install_id_saved(cfg)
    mode = str(lic.get("mode") or "remote").strip().lower()
    if mode == "local":
        ok = _ensure_local_license_menu_loop(cfg, args, use_color)
    else:
        ok = _ensure_remote_license_menu_loop(cfg, args, use_color)
    return 0 if ok else 1


def cmd_new_user_help(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    print()
    print(NEW_USER_HELP_TEXT)
    return 0


def _cmd_doctor_versions() -> int:
    """``deng-rejoin doctor versions`` — installed vs wrapper paths (no secrets)."""
    from . import build_info as _bi
    from .constants import APP_HOME, VERSION

    def _safe_dict(fn: Any) -> dict[str, Any]:
        try:
            data = fn()
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            _write_cli_crash_log(exc, context="doctor versions metadata")
            return {}

    def _safe_text(value: Any, default: str = "—") -> str:
        text = str(value or "").strip()
        return text if text else default

    version_info = _safe_dict(_bi.collect_version_info)
    try:
        wrapper = version_info.get("wrapper_path") or _bi.find_wrapper_path() or ""
    except Exception as exc:  # noqa: BLE001
        _write_cli_crash_log(exc, context="doctor versions wrapper")
        wrapper = ""
    installed = _safe_dict(_bi.load_installed_build)
    embedded = _safe_dict(_bi.load_build_info)

    latest_row: dict[str, Any] | None = None
    latest_err = ""
    try:
        from .install_registry import resolve_requested_public_version

        latest_row, latest_err = resolve_requested_public_version("latest")
    except ModuleNotFoundError as exc:
        # Protected client artifacts intentionally omit server-only registry
        # code; doctor should report that as unavailable, not as a crash-log
        # event.
        latest_err = exc.__class__.__name__
    except Exception as exc:  # noqa: BLE001
        _write_cli_crash_log(exc, context="doctor versions latest check")
        latest_err = exc.__class__.__name__
    latest_sha = str((latest_row or {}).get("artifact_sha256") or "").strip()
    latest_version = str((latest_row or {}).get("version") or "").strip()

    print("DENG Tool: Rejoin — version diagnostics")
    print(f"  Product version:        {VERSION}")
    print(f"  Agent VERSION:          {VERSION}")
    print(f"  Wrapper path:           {wrapper or '— not on PATH —'}")
    print(f"  Agent home:             {APP_HOME}")
    print(f"  Build commit:           {_safe_text(version_info.get('git_commit_short') or version_info.get('git_commit'))}")
    print(f"  Artifact SHA:           {_safe_text(version_info.get('artifact_sha256_short'))}")
    print(f"  Installed artifact SHA: {_safe_text(version_info.get('artifact_sha256'))}")
    print(f"  Protected runtime SHA:  {_safe_text(version_info.get('protected_runtime_sha256'))}")
    print(f"  Runtime build date:     {_safe_text(version_info.get('runtime_build_date'))}")
    print(f"  Install channel:        {_safe_text(version_info.get('channel'))}")
    if latest_version or latest_sha:
        print(f"  Latest public stable:   {latest_version or '—'}")
        print(f"  Latest channel SHA:     {latest_sha or '—'}")
    else:
        print(f"  Latest server version:  unavailable ({latest_err or 'not configured'})")
    if installed:
        print(f"  Installed build:        {installed.get('version') or installed.get('version_name') or '—'}")
        print(f"  Install time:           {installed.get('install_time_iso') or installed.get('installed_at') or '—'}")
    else:
        print("  Installed build:        (no .installed-build.json)")
    if embedded:
        print(f"  Embedded BUILD-INFO:    {embedded.get('version') or '—'}")
    print(f"  Release manifest:       {version_info.get('release_manifest_path') or '—'}")
    print(f"  Build info path:        {version_info.get('build_info_path') or '—'}")
    print(f"  Installed build path:   {version_info.get('installed_build_path') or '—'}")
    print(f"  Monitor worker present: {'yes' if version_info.get('monitor_worker_present') else 'no'}")
    print(f"  Monitor implementation: {version_info.get('monitor_command_implementation') or '—'}")
    print(f"  Persistent worker command available: {'yes' if version_info.get('persistent_worker_command_available') else 'no'}")
    print(f"  Legacy shell path reachable: {'yes' if version_info.get('legacy_shell_path_reachable') else 'no'}")
    if version_info.get("monitor_detector_detail"):
        print(f"  Monitor detector:       {version_info.get('monitor_detector_detail')}")
    print(f"  Bridge launcher path:   {version_info.get('bridge_launcher_path') or '—'}")
    print(f"  PID path:               {MONITOR_PID_PATH}")
    print(f"  Lock path:              {MONITOR_LOCK_PATH}")
    print(f"  Status JSON path:       {MONITOR_STATUS_PATH}")
    print(f"  Log path:               {MONITOR_LOG_PATH}")
    print("  Snapshot-test available: yes")
    print("  Snapshot-test latest upload available: yes")

    try:
        from . import monitor_autostart

        summary = monitor_autostart.get_monitor_status_summary()
        summary.update(_monitor_status_from_disk())
        print(f"  Bridge URL:             {summary.get('bridge_url')}")
        print(f"  Bridge process:         {'yes' if summary.get('worker_running') else 'no'}")
        print(f"  Bridge PID:             {summary.get('worker_pid') or '—'}")
        print(f"  Bridge running:         {'yes' if summary.get('bridge_running') else 'no'}")
        print(f"  Bridge log:             {MONITOR_LOG_PATH}")
        print(f"  Bridge status file:     {MONITOR_STATUS_PATH}")
        push_interval = summary.get("push_interval_seconds")
        print(f"  Push interval:          {f'{push_interval:g}s' if isinstance(push_interval, (int, float)) and push_interval > 0 else '—'}")
        print(f"  Snapshot interval:      {summary.get('snapshot_interval_seconds') or 0}s")
        print(f"  Last push:              {summary.get('last_push_result') or '—'}")
    except Exception as exc:  # noqa: BLE001
        _write_cli_crash_log(exc, context="doctor versions monitor summary")
        print(f"  Monitor bridge:         unavailable ({exc.__class__.__name__})")

    try:
        from .safe_http import safe_get

        api = _bi._read_install_api() if hasattr(_bi, "_read_install_api") else ""
        if api:
            print(f"  Install API:            {api}")
    except Exception:  # noqa: BLE001
        pass
    if latest_version or latest_sha:
        print("  Latest server version:  checked")
    return 0


def _cmd_monitor_run_worker(args: argparse.Namespace) -> int:
    from . import monitor_autostart

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    pid = _monitor_worker_pid()
    if pid and is_process_alive(pid) and pid != os.getpid():
        return 0

    started_at = _monitor_now_iso()
    stop_requested = False

    def _persist(summary: dict[str, Any]) -> None:
        payload = dict(summary)
        payload["worker_pid"] = os.getpid()
        payload["worker_running"] = True
        payload["worker_started_at"] = started_at
        payload["updated_at"] = _monitor_now_iso()
        _write_json_atomic(MONITOR_STATUS_PATH, payload)

    def _handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_signal)
            except Exception:  # noqa: BLE001
                pass

    _write_json_atomic(
        MONITOR_LOCK_PATH,
        {
            "pid": os.getpid(),
            "product": PRODUCT_NAME,
            "command": "monitor-run-worker",
            "started_at": started_at,
        },
    )
    MONITOR_PID_PATH.write_text(f"{os.getpid()}\n", encoding="utf-8")

    try:
        while not stop_requested:
            try:
                cfg = load_config()
            except ConfigError:
                cfg = default_config()
            ok = _ensure_monitor_bridge_for_config(cfg if isinstance(cfg, dict) else {})
            summary = monitor_autostart.get_monitor_status_summary()
            _persist(summary)
            time.sleep(2.0 if ok else 5.0)
    finally:
        try:
            monitor_autostart.stop_monitor_bridge()
        except Exception:  # noqa: BLE001
            pass
        try:
            summary = monitor_autostart.get_monitor_status_summary()
        except Exception:  # noqa: BLE001
            summary = {}
        summary["bridge_running"] = False
        summary["connected"] = False
        summary["worker_pid"] = os.getpid()
        summary["worker_running"] = False
        summary["worker_started_at"] = started_at
        summary["updated_at"] = _monitor_now_iso()
        _write_json_atomic(MONITOR_STATUS_PATH, summary)
        _cleanup_monitor_worker_files(keep_status=True)
    return 0


def _cmd_monitor_start(args: argparse.Namespace) -> int:
    del args
    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()
    print("DENG Tool: Rejoin — monitor bridge start")
    if _monitor_worker_running():
        status = _monitor_status_from_disk()
        print("  Starting monitor bridge: already running")
        print(f"  Worker PID:             {status.get('worker_pid') or _monitor_worker_pid() or '—'}")
        print(f"  Status file:            {MONITOR_STATUS_PATH}")
        print(f"  Log file:               {MONITOR_LOG_PATH}")
        return 0
    if not _monitor_bridge_launch_material(cfg if isinstance(cfg, dict) else {}):
        print("  Start result:           failed — no valid license/device binding available")
        return 1
    print("  Starting monitor bridge...")
    ok = _spawn_monitor_worker(cfg if isinstance(cfg, dict) else {})
    status = _wait_for_monitor_status(timeout=10.0) if ok else _monitor_status_from_disk()
    first_heartbeat = "success" if status.get("last_push_result") == "success" else (status.get("last_error") or "pending")
    print(f"  Worker PID:             {status.get('worker_pid') or '—'}")
    print(f"  Status file:            {MONITOR_STATUS_PATH}")
    print(f"  Log file:               {MONITOR_LOG_PATH}")
    print(f"  First heartbeat:        {first_heartbeat}")
    print(f"  Device connected:       {'yes' if status.get('connected') else 'no'}")
    return 0 if ok else 1


def _cmd_monitor_stop(args: argparse.Namespace) -> int:
    del args
    print("DENG Tool: Rejoin — monitor bridge stop")
    stopped, message = _stop_monitor_worker()
    print(f"  Stop result:            {message}")
    return 0 if stopped else 1


def _cmd_monitor_restart(args: argparse.Namespace) -> int:
    """``deng-rejoin monitor restart`` — restart the persistent APK monitor bridge."""
    print("DENG Tool: Rejoin — monitor bridge restart")
    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()
    stopped, message = _stop_monitor_worker()
    print(f"  Stop result:            {message}")
    if not _monitor_bridge_launch_material(cfg if isinstance(cfg, dict) else {}):
        print("  Restart result:         failed — no valid license/device binding available")
        return 1
    print("  Starting monitor bridge...")
    ok = _spawn_monitor_worker(cfg if isinstance(cfg, dict) else {})
    status = _wait_for_monitor_status(timeout=10.0) if ok else _monitor_status_from_disk()
    print(f"  Restart result:         {'running' if ok else 'failed — check license / network'}")
    print(f"  Worker PID:             {status.get('worker_pid') or '—'}")
    print(f"  Status file:            {MONITOR_STATUS_PATH}")
    print(f"  Log file:               {MONITOR_LOG_PATH}")
    print(f"  First heartbeat:        {'success' if status.get('last_push_result') == 'success' else (status.get('last_error') or 'pending')}")
    print(f"  Device connected:       {'yes' if status.get('connected') else 'no'}")
    return 0 if ok else 1


def _upload_snapshot_test_image(cfg: dict[str, Any], data: bytes, mime: str = "image/png") -> tuple[bool, str]:
    from . import safe_http

    auth = _resolve_monitor_bridge_auth(cfg)
    if not auth:
        return False, "missing_license"
    last_status = "not_sent"
    for attempt in range(2):
        try:
            status, _body = safe_http.post_raw(
                auth["bridge_url"] + "/api/monitor/bridge/snapshot",
                data,
                content_type=mime,
                headers={
                    "Authorization": f"Bearer {auth['bridge_token']}",
                    "User-Agent": "DENG-Tool-Monitor-Bridge/1.0",
                },
                timeout=12,
            )
        except safe_http.SafeHttpNetworkError as exc:
            return False, f"network_{exc.__class__.__name__}"
        except Exception as exc:  # noqa: BLE001
            _write_cli_crash_log(exc, context="snapshot-test latest upload")
            return False, exc.__class__.__name__
        last_status = f"http_{status}"
        if 200 <= int(status) < 300:
            return True, last_status
        if int(status) in {401, 403} and attempt == 0:
            refreshed = _resolve_monitor_bridge_auth(cfg, refresh=True)
            if refreshed:
                auth = refreshed
                continue
    return False, last_status


def _verify_latest_snapshot_fetch(cfg: dict[str, Any]) -> tuple[bool, str, str]:
    from . import safe_http

    auth = _resolve_monitor_bridge_auth(cfg)
    if not auth:
        return False, "missing_license", ""
    url = auth["bridge_url"] + "/api/monitor/bridge/snapshot/latest"
    try:
        status, body = safe_http.get_raw(
            url,
            headers={
                "Authorization": f"Bearer {auth['bridge_token']}",
                "User-Agent": "DENG-Tool-Monitor-Bridge/1.0",
            },
            timeout=12,
        )
    except safe_http.SafeHttpNetworkError as exc:
        return False, f"network_{exc.__class__.__name__}", url
    except Exception as exc:  # noqa: BLE001
        return False, exc.__class__.__name__, url
    if int(status) == 200 and body:
        return True, f"http_{status}", url
    return False, f"http_{status}", url


def _cmd_monitor_snapshot_test(*, upload_probe: bool = False) -> int:
    """``deng-rejoin monitor snapshot-test [--upload-probe]``.

    Runs every snapshot provider rung and prints a per-provider report so the
    exact reason a capture succeeds/fails is visible. With ``--upload-probe`` it
    also uploads a dev-probe that carries a clear ``SNAPSHOT LIVE TEST`` section.
    """
    from pathlib import Path

    from . import build_info as _bi
    from . import snapshot as _snap

    installed = _bi.load_installed_build()
    inst_ver = (installed.get("version") or installed.get("version_name") or "") if installed else ""

    print("DENG Tool: Rejoin — SNAPSHOT LIVE TEST")
    print(f"  Agent VERSION:          {VERSION}")
    if inst_ver:
        print(f"  Installed build:        {inst_ver}")
    print("  Snapshot ladder:        v1.0.6+ multi-provider (normal / system / root)")
    report = _snap.snapshot_test_report()
    print(f"  su available:           {'yes' if report.get('su_available') else 'no'}")
    print(f"  root escalation:        {'disabled' if report.get('root_disabled') else 'enabled'}")
    print("  Providers (full ladder, every rung attempted):")
    for row in report.get("providers", []):
        print(f"    • provider:           {row.get('provider')}")
        print(f"      command:            {row.get('command')}")
        print(f"      exit code:          {row.get('exit_code')}")
        print(f"      timeout:            {row.get('timeout_seconds')}s"
              f"{' (TIMED OUT)' if row.get('timed_out') else ''}")
        print(f"      binary found:       {'yes' if row.get('found') else 'no'}")
        print(f"      output bytes:       {row.get('byte_length')}")
        print(f"      PNG valid:          {'yes' if row.get('png_valid') else 'no'}")
        print(f"      suspicious small:   {'yes' if row.get('suspicious_small') else 'no'}")
        if row.get("stderr"):
            print(f"      stderr:             {row.get('stderr')}")
        if row.get("note"):
            print(f"      note:               {row.get('note')}")
    sel = report.get("selected_provider")
    print(f"  Final result:           {report.get('final_result')}")
    print(f"  Selected provider:      {sel or '— none succeeded —'}")
    cap = None
    if sel:
        print(f"  Captured bytes:         {report.get('selected_bytes')}")
        try:
            from .snapshot import SNAPSHOT_DIR

            test_path = SNAPSHOT_DIR / f"snapshot-test-{int(time.time())}.png"
            cap = _snap.capture_snapshot_detailed()
            if cap.ok and cap.data:
                test_path.write_bytes(cap.data)
                print(f"  Test PNG saved:         {test_path}")
        except Exception as exc:  # noqa: BLE001
            print(f"  Test PNG saved:         (failed: {exc.__class__.__name__})")

    if not upload_probe:
        print("")
        if sel:
            print("  Next step: snapshot capture works — if APK still empty, check bridge upload / device pairing.")
        else:
            print("  Next step: fix the failing provider above, then re-run with --upload-probe.")
        return 0 if sel else 1

    # --upload-probe: attach the live-test evidence to a dev-probe and upload it.
    print("")
    uploaded = False
    detail = "skipped"
    visible = False
    fetch_status = "skipped"
    fetch_url = ""
    if cap is not None and getattr(cap, "ok", False) and getattr(cap, "data", None):
        try:
            cfg = load_config()
        except ConfigError:
            cfg = default_config()
        try:
            uploaded, detail = _upload_snapshot_test_image(
                cfg if isinstance(cfg, dict) else {},
                cap.data,
                getattr(cap, "mime", "image/png") or "image/png",
            )
        except Exception as exc:  # noqa: BLE001
            _write_cli_crash_log(exc, context="snapshot-test upload step")
            uploaded, detail = False, exc.__class__.__name__
        if uploaded:
            try:
                visible, fetch_status, fetch_url = _verify_latest_snapshot_fetch(cfg if isinstance(cfg, dict) else {})
            except Exception as exc:  # noqa: BLE001
                _write_cli_crash_log(exc, context="snapshot-test latest verify")
                visible, fetch_status, fetch_url = False, exc.__class__.__name__, ""
            print(f"  Snapshot latest upload: HTTP {detail.removeprefix('http_') if detail.startswith('http_') else '200'}")
            print(f"  Backend latest visible: {'yes' if visible else 'no'}")
            print(f"  Snapshot fetch URL:     {fetch_url or '—'}")
            print(f"  Snapshot fetch status:  {fetch_status}")
        else:
            print(f"  Snapshot latest upload: FAILED ({detail})")
    else:
        print("  Snapshot latest upload: skipped (no valid PNG to upload)")
    print("  Uploading probe with SNAPSHOT LIVE TEST section…")
    probe_doc: dict[str, Any] = {}
    probe_module: Any | None = None
    try:
        from . import probe as _probe
        probe_module = _probe
        probe_doc = _probe.collect_probe()
    except Exception as exc:  # noqa: BLE001
        _write_cli_crash_log(exc, context="snapshot-test collect probe")
        probe_doc = {
            "probe_version": 1,
            "errors": [{"step": "collect_probe", "error": exc.__class__.__name__}],
        }
    try:
        probe_doc["snapshot_live_test"] = report
        probe_doc["snapshot_latest_verify"] = {
            "upload_ok": uploaded,
            "upload_result": detail,
            "backend_visible": visible,
            "fetch_status": fetch_status,
            "fetch_url": fetch_url,
        }
    except Exception as exc:  # noqa: BLE001
        _write_cli_crash_log(exc, context="snapshot-test probe section")
        probe_doc = {
            "probe_version": 1,
            "snapshot_live_test": report,
            "snapshot_latest_verify": {
                "upload_ok": uploaded,
                "upload_result": detail,
                "backend_visible": visible,
                "fetch_status": fetch_status,
                "fetch_url": fetch_url,
            },
            "errors": [{"step": "probe_section", "error": exc.__class__.__name__}],
        }
    try:
        if probe_module is None:
            from . import probe as _probe
            probe_module = _probe
        ok, msg = probe_module.upload_probe(probe_doc)
        if ok:
            print("  Probe upload:           OK")
            if "probe_id=" in msg or msg.startswith("p-"):
                print(f"  Probe ID:               {msg.split('probe_id=')[-1].strip() if 'probe_id=' in msg else msg}")
            else:
                print(f"  Probe detail:           {msg}")
            print("  Next step: open the probe in admin tools or share the Probe ID with support.")
            return 0 if sel else 1
        print(f"  Probe upload:           FAILED ({msg})")
        return 1
    except Exception as exc:  # noqa: BLE001
        _write_cli_crash_log(exc, context="snapshot-test upload probe")
        print(f"  Probe upload:           ERROR ({str(exc)[:160]})")
        return 1


def cmd_monitor(args: argparse.Namespace) -> int:
    """``deng-rejoin monitor [status|start|stop|restart|snapshot-test]``.

    Subcommands:
      status         (default) print connection / push / snapshot summary
      start          start the persistent bridge worker process
      stop           stop the persistent bridge worker process
      restart        restart the persistent bridge worker process
      snapshot-test  run every snapshot provider rung and print the report
                     (``--upload-probe`` also uploads a SNAPSHOT LIVE TEST probe)
    """
    sub = (getattr(args, "monitor_subcommand", "") or "status").lower().strip()
    if sub in {"run_worker", "run-worker", "worker"}:
        return _cmd_monitor_run_worker(args)
    if sub in {"snapshot_test", "snapshot-test", "snapshottest"}:
        return _cmd_monitor_snapshot_test(
            upload_probe=bool(getattr(args, "snapshot_upload_probe", False))
        )
    if sub in {"start"}:
        return _cmd_monitor_start(args)
    if sub in {"stop"}:
        return _cmd_monitor_stop(args)
    if sub in {"restart"}:
        return _cmd_monitor_restart(args)
    if sub not in {"status", ""}:
        print(f"Unknown monitor subcommand: {sub}")
        print("Usage: deng-rejoin monitor [status|start|stop|restart|snapshot-test [--upload-probe]]")
        return 2

    try:
        cfg = load_config()
    except ConfigError:
        cfg = default_config()
    # Register cfg so configured_packages count is accurate even when
    # the bridge thread isn't running in this short-lived CLI invocation.
    from . import monitor_autostart
    try:
        monitor_autostart.set_config(cfg)
    except Exception as exc:  # noqa: BLE001
        _write_cli_crash_log(exc, context="monitor status set_config")
    summary: dict[str, Any] = {}
    try:
        summary = monitor_autostart.get_monitor_status_summary()
    except Exception as exc:  # noqa: BLE001
        _write_cli_crash_log(exc, context="monitor status live summary")
        summary = {
            "bridge_url": "—",
            "autostart_enabled": True,
            "token_cache": {"present": False},
        }
    summary.update(_monitor_status_from_disk())

    sha = ""
    try:
        bi_path = Path(APP_HOME) / "BUILD-INFO.json"
        if bi_path.exists():
            sha = (json.loads(bi_path.read_text(encoding="utf-8")) or {}).get("artifact_sha256_short") or ""
    except Exception:  # noqa: BLE001
        sha = ""

    def _fmt_time(epoch: float | None) -> str:
        if not epoch:
            return "never"
        try:
            dt = datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone()
            return dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:  # noqa: BLE001
            return "never"

    snap_iv = int(summary.get("snapshot_interval_seconds") or 0)
    snap_iv_str = "Off" if snap_iv == 0 else f"{snap_iv}s"
    push_iv = float(summary.get("push_interval_seconds") or 0)
    push_iv_str = f"{push_iv:g}s" if push_iv > 0 else "—"
    stale_ttl = max(int(push_iv or 5) * 2, 60) if push_iv > 0 else 60
    disc_ttl = max(int(push_iv or 5) * 3, 90) if push_iv > 0 else 90

    print("DENG Tool: Rejoin APK Monitor")
    print(f"  Installed version:      {VERSION}")
    if sha:
        print(f"  Installed artifact SHA: {sha}")
    print(f"  Bridge URL:             {summary.get('bridge_url')}")
    worker_state = "yes" if summary.get("worker_running") else "no"
    if summary.get("worker_running") and not summary.get("status_file_present"):
        worker_state = "starting"
    print(f"  Bridge worker running:  {worker_state}")
    print(f"  Worker PID:             {summary.get('worker_pid') or '—'}")
    print(f"  Monitor autostart:      {'enabled' if summary.get('autostart_enabled') else 'disabled'}")
    print(f"  Bridge session active:  {'yes' if summary.get('bridge_running') else 'no'}")
    print(f"  Push interval:          {push_iv_str}")
    print(f"  Stale threshold:       ~{stale_ttl}s (2× interval, min 60)")
    print(f"  Disconnect threshold:   ~{disc_ttl}s (3× interval, min 90)")
    print(f"  Device connected:       {'yes' if summary.get('connected') else 'no'}")
    print(f"  Last heartbeat:         {summary.get('last_push_result') or 'never'}")
    print(f"  Last heartbeat at:      {_fmt_time(summary.get('last_push_at'))}")
    print(f"  Last push result:       {summary.get('last_push_result') or '—'}")
    if summary.get("last_error"):
        print(f"  Last push error:        {summary.get('last_error')}")
    if summary.get("consecutive_failures"):
        print(f"  Consecutive failures:   {summary.get('consecutive_failures')}")
    print(f"  Configured packages:    {summary.get('configured_packages')}")
    print(f"  Reported packages:      {summary.get('reported_packages')}")
    print(f"  RAM:                    {_format_monitor_ram(summary.get('device_ram'))}")
    print(f"  Device name:            {summary.get('device_label') or '—'}")
    print(f"  Snapshot interval:      {snap_iv_str}")
    print(f"  Last snapshot:          {_fmt_time(summary.get('snapshot_last_sent_at'))}")
    print(f"  Last snapshot result:   {summary.get('snapshot_last_result') or '—'}")
    if not summary.get("status_file_present"):
        print(f"  Status file:            missing ({MONITOR_STATUS_PATH})")
        if summary.get("worker_running"):
            print("  Last heartbeat:         not yet (worker is starting)")
    print(f"  Status file updated:    {summary.get('updated_at') or 'never'}")
    print(f"  Bridge log path:        {MONITOR_LOG_PATH}")
    print(f"  Supervisor active:      {'yes' if summary.get('supervisor_active') else 'no'}")
    cache = summary.get("token_cache") or {}
    if cache.get("present"):
        print(f"  Bridge token cache:     present (expires {cache.get('expires_at') or 'unknown'})")
    else:
        print("  Bridge token cache:     not present")
    print(f"  Last issue attempt:     {summary.get('last_issue_result') or '—'}")
    return 0


def _run_top_menu_with_clean_exit(args: argparse.Namespace) -> int:
    """Run the top menu once; on clean exit use the single Termux teardown workaround."""
    try:
        rc = run_menu(args, _handlers())
    except KeyboardInterrupt:
        print("Goodbye.")
        rc = 0
    except EOFError:
        print("Goodbye.")
        rc = 0
    if rc == 0:
        _termux_exit_clean()
    return rc


def cmd_disable_test_license_bypass(args: argparse.Namespace) -> int:
    """Remove the test-build key-free bypass; restores the normal key gate."""
    use_color = not getattr(args, "no_color", False)
    disable_test_license_bypass()
    msg = "Test license bypass disabled. This build now requires a valid key."
    if use_color:
        print(termux_ui.success_line(msg))
    else:
        print(msg)
    return 0


def cmd_menu(args: argparse.Namespace) -> int:
    """Open the main menu, gated by a license check on first run."""
    global _license_manual_verification_success
    ensure_app_dirs()
    use_color = not args.no_color

    _menu_cfg = _load_config_for_menu()
    _enforce_configured_screen_mode(_menu_cfg)
    _enforce_termux_left_layout(_menu_cfg)

    # Notify user if a recent crash was detected (but never show the stack).
    crash_notice = safe_io.check_and_report_crash_log()
    if crash_notice:
        safe_io.restore_terminal()
        first_line = crash_notice.split("\n", 1)[0]
        if use_color:
            safe_io.write_stdout("")
            safe_io.write_stdout(termux_ui.warning_line(first_line))
            safe_io.write_stdout("")
        else:
            safe_io.write_stdout(f"\n⚠  {first_line}\n")

    # Dev mode: skip license gate entirely
    if keystore.DEV_MODE:
        _print_dev_license_skipped(use_color)
        safe_io.safe_clear_screen()
        return _run_top_menu_with_clean_exit(args)

    # Load config (use defaults if not yet created)
    cfg = _menu_cfg

    lic = cfg.setdefault("license", {})

    # Skip gate when license checking is disabled in config
    if lic.get("disabled_by_user") or not lic.get("enabled", True):
        safe_io.safe_clear_screen()
        return _run_top_menu_with_clean_exit(args)

    # Test-only key-free bypass (test/latest / main-dev builds ONLY).
    if is_test_license_bypass_active():
        _print_test_license_bypass_active(use_color)
        _try_autostart_monitor_bridge(cfg)
        safe_io.safe_clear_screen()
        return _run_top_menu_with_clean_exit(args)

    cfg = _ensure_install_id_saved(cfg)
    mode = str(lic.get("mode") or "remote").strip().lower()
    if mode == "local":
        ok = _ensure_local_license_menu_loop(cfg, args, use_color)
    else:
        ok = _ensure_remote_license_menu_loop(cfg, args, use_color)

    if not ok:
        return 0

    if _license_manual_verification_success:
        termux_ui.print_license_success(pause_seconds=0.8)
        _license_manual_verification_success = False
    # License gate has passed → auto-start the Rejoin APK monitor bridge
    # so the cloud phone appears in the Android app without any manual
    # env-var setup. Never raises; backend offline is logged silently.
    _try_autostart_monitor_bridge(cfg)
    safe_io.safe_clear_screen()
    return _run_top_menu_with_clean_exit(args)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="deng_tool_rejoin.py", description=f"{PRODUCT_NAME} local Termux reconnect helper")
    parser.add_argument("command", nargs="?", choices=sorted(COMMANDS), help="command to run")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--first-setup", action="store_true")
    parser.add_argument("--doctor", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--config", dest="config_flag", action="store_true")
    parser.add_argument("--logs", action="store_true")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--menu", action="store_true")
    parser.add_argument("--license", action="store_true", help="enter or update your license key")
    parser.add_argument(
        "--disable-test-license-bypass",
        dest="disable_test_bypass",
        action="store_true",
        help="remove the test-build key-free bypass (test/main-dev builds only)",
    )
    parser.add_argument("--new-user-help", dest="new_user_help", action="store_true", help="print the built-in tutorial for beginners")
    parser.add_argument("--enable-boot", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="show extra Start diagnostics (cache/graphics/launch)")
    parser.add_argument("--debug", action="store_true", help="same as --verbose for Start diagnostics")
    parser.add_argument("--lines", type=int, default=50, help="number of log lines for logs command")
    parser.add_argument("--no-color", action="store_true", help="disable ANSI banner color")
    parser.add_argument("--layout-reset", dest="layout_reset", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--layout-test", dest="layout_test", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--root-state", dest="root_state", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--support-bundle", dest="support_bundle", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--discover-layout-keys", dest="discover_layout_keys",
                        action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--probe", dest="probe", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--upload", dest="upload", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--diag-startup", dest="diag_startup", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--diag-startup-full", dest="diag_startup_full", action="store_true",
                        help=argparse.SUPPRESS)
    # ``--diag`` opts INTO the heavy child-subprocess diag step (45 s
    # timeout) inside ``deng-rejoin probe``.  Off by default — without
    # it the probe completes in ~10 s and reliably fits the 4 MB upload
    # cap even after long test sessions.
    parser.add_argument("--diag", dest="diag", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--full", dest="probe_full", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--summary", dest="probe_summary", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--debug-heavy", dest="debug_heavy", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--package", dest="doctor_package", default="",
                        help=argparse.SUPPRESS)

    # Pre-process argv so hidden positional subcommands don't trip choices validation.
    import sys as _sys
    if argv is None:
        argv = _sys.argv[1:]
    argv = list(argv)

    ns_extra_args: list[str] = []
    if argv and argv[0] in {"map", "unmap", "launch", "selftest"}:
        ns_extra_args = argv[1:]
        argv = [argv[0]]
    elif argv and argv[0] == "doctor":
        cleaned: list[str] = []
        skip_next = False
        for i, tok in enumerate(argv):
            if skip_next:
                skip_next = False
                continue
            if tok == "--package" and i + 1 < len(argv):
                skip_next = True
                continue
            cleaned.append(tok)
        argv = cleaned

    # Standalone aliases: hidden subcommand spellings → flag.
    if argv and argv[0] in ("support-bundle", "support_bundle"):
        argv[0] = "--support-bundle"
    if argv and argv[0] in ("discover-layout-keys", "discover_layout_keys"):
        argv[0] = "--discover-layout-keys"
    if argv and argv[0] in ("probe",):
        argv[0] = "--probe"

    # Use parse_known_args so that `deng-rejoin doctor layout` doesn't fail
    # with "unrecognized arguments: layout".
    try:
        ns, _unknown = parser.parse_known_args(argv)
    except SystemExit:
        # argparse choice-validation failure — fall back to safe defaults.
        ns = parser.parse_args([])
        _unknown = list(argv)

    # Capture sub-action for `monitor`: "monitor status" / "monitor snapshot-test".
    ns.monitor_subcommand = ""
    ns.snapshot_upload_probe = False
    if ns.command == "monitor" and _unknown:
        # First non-flag token is the subcommand; flags like --upload-probe are
        # collected separately so `monitor snapshot-test --upload-probe` works.
        positional = [t for t in _unknown if not str(t).startswith("-")]
        flags = {str(t).lower().replace("-", "_") for t in _unknown if str(t).startswith("-")}
        sub = (positional[0] if positional else "").lower().replace("-", "_").strip()
        if sub in {"status", ""}:
            ns.monitor_subcommand = "status"
        else:
            ns.monitor_subcommand = sub
        ns.snapshot_upload_probe = ("__upload_probe" in flags) or ("upload_probe" in flags)

    # Map positional sub-subcommands for `doctor`: "doctor layout", "doctor root-state".
    ns.doctor_install = False
    ns.doctor_ram = False
    if ns.command == "doctor" and _unknown:
        sub = (_unknown[0] or "").lower().replace("-", "_")
        if sub in ("layout", "layout_test"):
            ns.layout_test = True
        elif sub in ("root_state",):
            ns.root_state = True
        elif sub in ("reset", "layout_reset"):
            ns.layout_reset = True
        elif sub in ("bundle", "support_bundle"):
            ns.support_bundle = True
        elif sub in ("install", "installation", "build"):
            ns.doctor_install = True
        elif sub in ("versions", "version_info"):
            ns.doctor_versions = True
        elif sub in ("ram", "ram_report", "memory"):
            ns.doctor_ram = True
        elif sub.startswith("com.") or "." in sub:
            ns.doctor_package = _unknown[0]
        # Any other doctor sub is just dropped silently — no traceback.

    ns.extra_args = ns_extra_args

    flag_to_command = {
        "setup": ns.setup,
        "first-setup": ns.first_setup,
        "doctor": ns.doctor,
        "status": ns.status,
        "once": ns.once,
        "start": ns.start,
        "stop": ns.stop,
        "reset": ns.reset,
        "config": ns.config_flag,
        "logs": ns.logs,
        "version": ns.version,
        "menu": ns.menu,
        "license": ns.license,
        "new-user-help": ns.new_user_help,
        "enable-boot": ns.enable_boot,
        "update": ns.update,
    }
    selected = [command for command, enabled in flag_to_command.items() if enabled]
    if ns.command and selected:
        parser.error("use either positional command or --command flag, not both")
    if len(selected) > 1:
        parser.error("choose only one command")

    # Hidden diagnostics: route to internal commands without showing in public menu.
    if getattr(ns, "disable_test_bypass", False):
        ns.resolved_command = "disable-test-license-bypass"
    elif getattr(ns, "discover_layout_keys", False):
        ns.resolved_command = "discover-layout-keys"
    elif getattr(ns, "support_bundle", False):
        ns.resolved_command = "support-bundle"
    elif getattr(ns, "probe", False):
        ns.resolved_command = "probe"
    elif getattr(ns, "diag_startup_full", False):
        ns.resolved_command = "diag-startup-full"
    elif getattr(ns, "diag_startup", False):
        ns.resolved_command = "diag-startup"
    elif getattr(ns, "doctor_install", False):
        ns.resolved_command = "doctor-install"
    elif getattr(ns, "layout_test", False) or getattr(ns, "root_state", False) or getattr(ns, "layout_reset", False):
        ns.resolved_command = "doctor"
    else:
        ns.resolved_command = ns.command or (selected[0] if selected else "menu")
    return ns


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint with global safety wrappers.

    faulthandler is enabled with a FILE target (never stderr) so SIGSEGV crash
    tracebacks never reach the public terminal.  All Python-level exceptions
    are caught here; the public user never sees a raw traceback or signal text.
    """
    safe_io.restore_terminal()
    try:
        from . import signal_handler as _signal_handler  # noqa: PLC0415

        _signal_handler.install_signal_handlers()
    except Exception:  # noqa: BLE001
        pass
    try:
        args = parse_args(argv)
    except KeyboardInterrupt:
        # Parsing can be interrupted before command dispatch; preserve the
        # same silent, clean exit contract as an in-command Ctrl+C.
        _termux_exit_clean()
        return 0
    except EOFError:
        _termux_exit_clean()
        return 0
    except SystemExit as _parse_exc:
        _code = _parse_exc.code
        return _code if isinstance(_code, int) else (0 if _code is None else 1)

    if args.resolved_command == "diag-startup":
        try:
            cmd_diag_startup(args)
        except SystemExit as _diag_exc:
            _code = _diag_exc.code
            return _code if isinstance(_code, int) else 0
        return 0

    safe_io.setup_faulthandler()
    _record_last_command(str(args.resolved_command or "menu"))
    # Silence internal namespace loggers so warnings/errors never leak to terminal.
    try:
        from .logger import silence_public_loggers
        silence_public_loggers()
    except Exception:  # noqa: BLE001
        pass
    try:
        return _handlers()[args.resolved_command](args)
    except KeyboardInterrupt:
        # Silent exit — return cleanly to shell, no public text.
        # On Termux, also skip Python finalization (libc cleanup
        # segfaults — probe ``p-47fa33562a``).
        _termux_exit_clean()
        return 0
    except EOFError:
        _termux_exit_clean()
        return 0
    except SystemExit as _exc:
        _code = _exc.code
        return _code if isinstance(_code, int) else (0 if _code is None else 1)
    except Exception as exc:  # noqa: BLE001
        import logging as _logging
        safe_io.restore_terminal()
        _logging.getLogger("deng.rejoin.cli").debug("Unhandled CLI error", exc_info=True)
        _write_cli_crash_log(exc, context="main")
        print(
            "\nThe tool hit an internal error. "
            "Please send support the latest crash log.",
            file=sys.stderr,
        )
        return 1


def _handlers() -> dict[str, Any]:
    return {
        "setup": cmd_setup,
        "first-setup": cmd_first_setup,
        "doctor": cmd_doctor,
        "status": cmd_status,
        "once": cmd_once,
        "start": cmd_start,
        "stop": cmd_stop,
        "reset": cmd_reset,
        "config": cmd_config,
        "logs": cmd_logs,
        "version": cmd_version,
        "menu": cmd_menu,
        "package-key": cmd_package_key,
        "license": cmd_license,
        "disable-test-license-bypass": cmd_disable_test_license_bypass,
        "monitor": cmd_monitor,
        "new-user-help": cmd_new_user_help,
        "enable-boot": cmd_enable_boot,
        "update": cmd_update,
        "support-bundle": cmd_support_bundle,
        "discover-layout-keys": cmd_discover_layout_keys,
        "doctor-install": cmd_doctor_install,
        "probe": cmd_probe,
        "diag-startup": cmd_diag_startup,
        "diag-startup-full": cmd_diag_startup_full,
        "scan": cmd_scan,
        "map": cmd_map,
        "list": cmd_list_packages,
        "unmap": cmd_unmap,
        "launch": cmd_launch,
        "selftest": cmd_selftest,
        "state": cmd_state,
    }


if __name__ == "__main__":
    raise SystemExit(main())
