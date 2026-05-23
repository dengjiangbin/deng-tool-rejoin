"""CLI command handlers for DENG Tool: Rejoin."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import account_detect, android, db, root_access, safe_io, termux_ui
from .banner import banner_text, print_banner
from .config import (
    ConfigError,
    DEFAULT_SCREEN_MODE,
    default_config,
    effective_private_server_url,
    enabled_package_entries,
    enabled_package_names,
    ensure_app_dirs,
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
    validate_roblosecurity_cookie,
    validate_screen_mode,
    validate_username_source,
)
from .constants import (
    CONFIG_PATH,
    DB_PATH,
    DEFAULT_LICENSE_SERVER_URL,
    DEFAULT_ROBLOX_PACKAGE,
    DEFAULT_ROBLOX_PACKAGE_HINTS,
    GITHUB_REMOTE,
    LOCK_PATH,
    LOG_PATH,
    PID_PATH,
    PRODUCT_NAME,
    RAW_INSTALL_URL,
    TERMUX_BOOT_SCRIPT,
    VERSION,
)
from .doctor import print_doctor, run_doctor
from .launcher import RejoinResult, perform_rejoin
from .launcher_file import create_market_launchers
from .lockfile import LockError, LockManager, stop_running_agent
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
    WRONG_DEVICE_USER_MESSAGE,
    bind_remote_license_key,
    check_remote_license_status,
    get_device_summary,
    sync_install_id_with_config,
)

# Set True only after a successful validate-only check or manual bind this process.
_license_session_validated = False
# Set True when the user manually entered a license key and verification succeeded.
_license_manual_verification_success = False
from . import snapshot, webhook, window_layout
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
    "auto-execute",
    "license",
    "new-user-help",
    "enable-boot",
    "update",
}

# ─── ANSI color constants (used only when a tty is available) ─────────────────
# Termux's default monospace font is very thin and the previous status
# colors were unreadable on a cloud-phone screen (user feedback: "i cant
# read shit so thin").  Every status color now starts with the BOLD
# attribute (\033[1;<color>m) so the foreground text uses the BRIGHT
# weight glyphs that all Termux monospace fonts have.
_ANSI_GREEN   = "\033[1;92m"   # bold bright green
_ANSI_YELLOW  = "\033[1;93m"   # bold bright yellow
_ANSI_RED     = "\033[1;91m"   # bold bright red
_ANSI_CYAN    = "\033[1;96m"   # bold bright cyan
_ANSI_BOLD    = "\033[1m"      # plain bold (no color)
_ANSI_DIM     = "\033[2;37m"   # dim grey (Unknown only — intentionally low-contrast)
_ANSI_RESET   = "\033[0m"
_ANSI_RE      = re.compile(r"\x1b\[[0-9;]*m")
_CONFIG_RECOVERED_DEFAULTS = False


def _print_dev_license_skipped(use_color: bool) -> None:
    msg = "Dev Mode: License Check Skipped"
    if use_color:
        print(f"{_ANSI_YELLOW}{msg}{_ANSI_RESET}")
    else:
        print(msg)


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
    "error",
    "",  # parser couldn't determine anything
}


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
        return save_config(cfg)
    except Exception:  # noqa: BLE001
        return cfg


def _remote_license_check_isolated(cfg: dict[str, Any], *, timeout: int = 35) -> tuple[str, str]:
    """Run the remote license check inside a child Python process.

    Real-device cause: on Termux/Python 3.13.13, the network code path in
    :func:`check_remote_license_status` segfaults the *parent* process
    (probe ``p-39924732cd`` showed ``last_step == 'license_remote_check'``
    with rc = -11).  Doing the call in a short-lived child process means
    the worst case is a clean ``"server_unavailable"`` result — the menu
    never crashes.

    Returns (result, message).  On any subprocess failure (non-zero exit,
    timeout, signal kill, malformed output), returns
    ``("server_unavailable", <reason>)``.
    """
    import subprocess as _sp  # noqa: PLC0415

    payload = {
        "key": (cfg.setdefault("license", {}).get("key") or "").strip(),
        "install_id": (cfg.setdefault("license", {}).get("install_id") or "").strip(),
        "device_label": str(cfg.setdefault("license", {}).get("device_label") or "")[:80],
        "server_url": (cfg.setdefault("license", {}).get("server_url") or "").strip(),
    }
    # Real-device evidence (probe p-09484eaab4): the child Python was
    # exiting with rc=1 because ``python3 -c "from agent.license ..."``
    # doesn't have the parent's ``sys.path``.  Pass the agent package's
    # parent directory via PYTHONPATH so the child can find it, and add
    # the same path explicitly inside the code string so ``DENG_REJOIN_HOME``
    # overrides work too.  Without this fix every check returned rc=1
    # and the menu loop persisted ``last_status = server_unavailable``,
    # corrupting the cache permanently.
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
        "    r, m = 'server_unavailable', f'check exception: {exc}'\n"
        "sys.stdout.write(json.dumps({'result': r, 'message': m}))\n"
    )
    # Pass PYTHONPATH as a belt-and-braces so even subprocess implementations
    # that strip ``-c`` script directory still find ``agent``.
    env = dict(os.environ)
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        _agent_parent + (os.pathsep + prev_pp if prev_pp else "")
    )
    try:
        proc = _sp.run(
            [sys.executable, "-c", code],
            input=json.dumps(payload).encode("utf-8"),
            stdout=_sp.PIPE,
            stderr=_sp.PIPE,
            timeout=timeout,
            check=False,
            env=env,
        )
    except _sp.TimeoutExpired:
        return "server_unavailable", "License check subprocess timed out."
    except OSError as exc:
        return "server_unavailable", f"License check subprocess launch error: {exc}"

    if proc.returncode < 0:
        # Crashed (SIGSEGV etc.) — DO NOT pollute terminal; treat as a
        # transient network problem.  Cached license keeps the user
        # running until the next clean check.
        return "server_unavailable", f"License check crashed safely (signal {-proc.returncode})."
    if proc.returncode != 0:
        # Capture stderr's first line so future probes pinpoint *why*
        # the child failed (ImportError / SyntaxError / etc.).
        stderr_line = (proc.stderr or b"").decode("utf-8", errors="replace").splitlines()
        hint = stderr_line[0][:80] if stderr_line else ""
        return "server_unavailable", f"License check exited rc={proc.returncode} ({hint})"
    try:
        data = json.loads((proc.stdout or b"").decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return "server_unavailable", "License check returned invalid JSON."
    result = str(data.get("result") or "server_unavailable").strip().lower()
    message = str(data.get("message") or "").strip()
    return result, message


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
    srv = (lic.get("server_url") or "").strip() or DEFAULT_LICENSE_SERVER_URL
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
    srv = (lic.get("server_url") or "").strip() or DEFAULT_LICENSE_SERVER_URL
    return bind_remote_license_key(
        srv,
        license_key=key,
        install_id=install_id,
        device_model=device.get("model") or "unknown",
        app_version=VERSION,
        device_label=str(lic.get("device_label") or ""),
    )


def _remote_license_bind_isolated(cfg: dict[str, Any], *, timeout: int = 35) -> tuple[str, str]:
    """Run manual bind inside a child process (same SIGSEGV isolation as check)."""
    import subprocess as _sp  # noqa: PLC0415

    payload = {
        "op": "bind",
        "key": (cfg.setdefault("license", {}).get("key") or "").strip(),
        "install_id": (cfg.setdefault("license", {}).get("install_id") or "").strip(),
        "device_label": str(cfg.setdefault("license", {}).get("device_label") or "")[:80],
        "server_url": (cfg.setdefault("license", {}).get("server_url") or "").strip(),
    }
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
        "    r, m = 'server_unavailable', f'bind exception: {exc}'\n"
        "sys.stdout.write(json.dumps({'result': r, 'message': m}))\n"
    )
    env = dict(os.environ)
    prev_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        _agent_parent + (os.pathsep + prev_pp if prev_pp else "")
    )
    try:
        proc = _sp.run(
            [sys.executable, "-c", code],
            input=json.dumps(payload).encode("utf-8"),
            stdout=_sp.PIPE,
            stderr=_sp.PIPE,
            timeout=timeout,
            check=False,
            env=env,
        )
    except _sp.TimeoutExpired:
        return "server_unavailable", "License bind subprocess timed out."
    except OSError as exc:
        return "server_unavailable", f"License bind subprocess launch error: {exc}"

    if proc.returncode < 0:
        return "server_unavailable", f"License bind crashed safely (signal {-proc.returncode})."
    if proc.returncode != 0:
        stderr_line = (proc.stderr or b"").decode("utf-8", errors="replace").splitlines()
        hint = stderr_line[0][:80] if stderr_line else ""
        return "server_unavailable", f"License bind exited rc={proc.returncode} ({hint})"
    try:
        data = json.loads((proc.stdout or b"").decode("utf-8", errors="replace") or "{}")
    except json.JSONDecodeError:
        return "server_unavailable", "License bind returned invalid JSON."
    result = str(data.get("result") or "server_unavailable").strip().lower()
    message = str(data.get("message") or "").strip()
    return result, message


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
        return _remote_license_check_isolated(cfg)
    return _remote_license_check_direct(cfg)


def _remote_license_run_bind(cfg: dict[str, Any]) -> tuple[str, str]:
    """Explicit manual bind via ``POST /api/license/bind`` only."""
    if _should_isolate_license_check():
        return _remote_license_bind_isolated(cfg)
    return _remote_license_bind_direct(cfg)


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


def _report_key_execution_if_public(cfg: dict[str, Any]) -> None:
    """Fire-and-forget: report one execution to the license API on public builds.

    Only runs when the build channel is a public stable release.
    main-dev / internal / test builds are silently skipped.
    Errors are swallowed — this must never interrupt the Start flow.
    """
    try:
        from agent.build_info import collected_version_info
        info = collected_version_info()
        channel = (info.get("channel") or "").strip().lower()
        version = (info.get("version") or "").strip()
        # Only public stable builds count.
        _PUBLIC_CHANNELS = {"stable", "release", "public"}
        _SKIP_CHANNELS = {"main-dev", "dev", "internal", "test", "test-latest"}
        if channel in _SKIP_CHANNELS or channel not in _PUBLIC_CHANNELS:
            return
        lic = cfg.get("license") or {}
        raw_key = (lic.get("key") or cfg.get("license_key") or "").strip()
        install_id = (cfg.get("install_id") or "").strip()
        if not raw_key or not install_id:
            return
        # Hash the install ID if it looks unhashed (not 64-char hex).
        import hashlib
        if len(install_id) != 64:
            install_id = hashlib.sha256(install_id.encode()).hexdigest()
        import threading
        def _report() -> None:
            try:
                from agent.safe_http import post as _post
                _post(
                    "/api/execute",
                    {"key": raw_key, "install_id_hash": install_id, "version": version, "channel": channel},
                    timeout=6,
                )
            except Exception:  # noqa: BLE001
                pass
        t = threading.Thread(target=_report, daemon=True, name="key-exec-report")
        t.start()
    except Exception:  # noqa: BLE001
        pass


def _ensure_local_license_menu_loop(cfg: dict[str, Any], args: argparse.Namespace, use_color: bool) -> bool:
    while True:
        try:
            cfg = load_config()
        except ConfigError as exc:
            _print_license_err(str(exc), use_color)
            return False
        lic = cfg.setdefault("license", {})
        key = (lic.get("key") or "").strip() or (cfg.get("license_key") or "").strip()
        if not key:
            if not _is_interactive():
                _print_license_err("No License Key Found", use_color)
                print_beginner_license_gate_help()
                return False
            print_beginner_menu_license_prompt()
            if not keystore.prompt_and_verify_key():
                return False
            global _license_manual_verification_success
            _license_manual_verification_success = True
            return True
        ok, msg = keystore.verify_key(key)
        if ok:
            return True
        _print_license_err(msg, use_color)
        if not _is_interactive():
            return False
        print("\n1. Enter Different Key\n2. Exit")
        _lc = safe_io.safe_prompt("Choose [2]: ", default="2")
        if _lc is None:
            return False
        choice = _lc.strip() or "2"
        if choice != "1":
            return False
        lic["key"] = ""
        cfg["license_key"] = ""
        cfg = save_config(cfg)


def _ensure_remote_license_menu_loop(cfg: dict[str, Any], args: argparse.Namespace, use_color: bool) -> bool:
    """Gate the menu behind a remote license check. Allows bounded retries without recursion."""
    _MAX_RETRIES = 10
    attempt = 0
    while attempt < _MAX_RETRIES:
        attempt += 1
        manual_key_entry = False
        # Always reload config fresh to pick up any changes from save_config
        try:
            cfg = load_config()
        except ConfigError as exc:
            _print_license_err(str(exc), use_color)
            return False

        try:
            cfg = _ensure_install_id_saved(cfg)
        except Exception:  # noqa: BLE001
            pass  # non-fatal; continue with current cfg

        lic = cfg.setdefault("license", {})

        key = (lic.get("key") or "").strip()

        if not key:
            if not _is_interactive():
                _print_license_err("No License Key Found", use_color)
                print_beginner_license_gate_help()
                return False
            _print_missing_license_prompt(use_color)
            raw = safe_io.safe_prompt(f"{termux_ui.prompt_prefix('Enter License Key')} ")
            if raw is None:
                return False
            if not raw:
                continue
            try:
                norm = validate_license_key(raw)
            except ConfigError as exc:
                _print_license_err(str(exc), use_color)
                continue
            lic["key"] = norm
            cfg["license_key"] = norm
            try:
                cfg = save_config(cfg)
            except Exception as exc:  # noqa: BLE001
                _print_license_err(f"Could not save license key: {exc}", use_color)
                continue
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

        if result == "active":
            global _license_session_validated, _license_manual_verification_success
            _license_session_validated = True
            if manual_key_entry:
                _license_manual_verification_success = True
            try:
                _persist_license_status(cfg, "active")
            except Exception:  # noqa: BLE001
                pass
            return True

        # Offline grace only after a successful validate/bind this session,
        # so stale ``last_status == active`` cannot bypass HWID reset when
        # the server is temporarily unreachable.
        if (
            result in _LICENSE_TRANSIENT_RESULTS
            and _license_session_validated
            and _license_should_offline_grace(lic)
        ):
            return True  # Silent — cached license still valid, no user action needed

        # Cache integrity: ONLY persist *definitive* answers
        # (active / wrong_device / not_found / revoked / expired / inactive /
        # key_not_redeemed / missing_key).  Persisting a transient
        # ``server_unavailable`` / ``error`` would overwrite a previously
        # valid ``last_status == "active"``, permanently disabling the
        # cache fast-path and offline grace.  Real-device evidence
        # (probe p-09484eaab4) showed this bug locking the user out
        # after a single failed subprocess attempt.
        if result not in _LICENSE_TRANSIENT_RESULTS:
            try:
                cfg = _persist_license_status(cfg, result)
            except Exception:  # noqa: BLE001
                pass

        if result == "requires_manual_rebind":
            _print_license_err(HWID_RESET_REENTRY_MESSAGE, use_color)
            cfg = _clear_cached_license_key(cfg)
            if not _is_interactive():
                return False
            continue

        if result == "wrong_device":
            _print_license_err(WRONG_DEVICE_USER_MESSAGE, use_color)
        elif result == "key_not_redeemed":
            _print_license_err(msg, use_color)
        else:
            _print_license_err(f"License Invalid: {msg}", use_color)

        if not _is_interactive():
            return False

        if result == "wrong_device":
            print("\n1. Enter Different Key\n2. Exit")
        else:
            print("\n1. Try Another Key\n2. Exit")
        _lc2 = safe_io.safe_prompt("Choose [2]: ", default="2")
        if _lc2 is None:
            return False
        choice = _lc2.strip() or "2"
        if choice != "1":
            return False
        # Clear bad key and retry
        lic["key"] = ""
        cfg["license_key"] = ""
        try:
            cfg = save_config(cfg)
        except Exception:  # noqa: BLE001
            pass  # Will be reloaded at top of loop
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
    return validate_account_username(entry.get("account_username", "")) or "Unknown"


def _package_username_display(entry: dict[str, Any]) -> str:
    """Username for package menus / tables — empty becomes Unknown."""
    u = validate_account_username(entry.get("account_username", ""))
    return u if u else "Unknown"


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


def _print_full_discovery_table(candidates: list[android.RobloxPackageCandidate]) -> None:
    print()
    print("Detected Roblox Packages")
    print(f"{'#':<4} {'Package':<40} {'App Name':<22} {'Launchable':<10}")
    print(f"{'-'*4} {'-'*40} {'-'*22} {'-'*10}")
    for idx, c in enumerate(candidates, start=1):
        launch_cell = "Yes" if c.launchable else "No"
        print(f"{idx:<4} {c.package:<40} {c.app_name[:20]:<22} {launch_cell:<10}")


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
        entry = _detect_or_prompt_account_username(
            _entry_for_package(c0.package, existing_entries, app_name=c0.app_name),
            config_for_detect,
        )
        mapped = _run_account_mapping_table([entry], config_for_detect or {})
        return mapped, "ok"
    _print_full_discovery_table(candidates)
    print("  A. Select all")
    _raw = safe_io.safe_prompt("Choose packages (e.g. 1,2 or A) [1]: ", default="1")
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
    base_entries = [
        _detect_or_prompt_account_username(
            _entry_for_package(c.package, existing_entries, app_name=c.app_name),
            config_for_detect,
        )
        for c in picked
    ]
    mapped = _run_account_mapping_table(base_entries, config_for_detect or {})
    return mapped, "ok"


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
) -> dict[str, Any]:
    """Apply stored screen mode with root and log probe-only evidence."""
    result: dict[str, Any] = {}
    try:
        from .logger import configure_logging, log_event

        cfg = config_data or {}
        mode = validate_screen_mode(cfg.get("screen_mode", DEFAULT_SCREEN_MODE))
        protected = list(protected_packages or [])
        if not protected:
            try:
                protected = enabled_package_names(validate_config(cfg))
            except Exception:  # noqa: BLE001
                protected = []
        result = android.enforce_screen_orientation(mode, protected_packages=protected)
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
    except Exception as exc:  # noqa: BLE001
        try:
            from .logger import configure_logging, log_event
            log_event(
                configure_logging(),
                "info",
                "[DENG_REJOIN_ORIENTATION_ENFORCE]",
                requested=validate_screen_mode((config_data or {}).get("screen_mode", DEFAULT_SCREEN_MODE)),
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


def _print_config_summary(config_data: dict[str, Any]) -> None:
    cfg = safe_config_view(validate_config(config_data))
    entries = validate_package_entries(cfg["roblox_packages"])
    enabled_entries = [entry for entry in entries if entry.get("enabled", True)]
    print("DENG Tool: Rejoin Settings")
    print()
    print("Roblox Packages:")
    print("  Roblox Username / Account Name")
    if enabled_entries:
        for idx, entry in enumerate(enabled_entries, start=1):
            username = _account_username_value(entry)
            print(f"  {idx}. {username:<16} {entry['package']}")
    else:
        print("  Not set")
    print(f"  Detection hints: {_hint_list_label(cfg['package_detection_hints'])}")
    print()
    print("Launch:")
    print(f"  Mode: {_launch_mode_label(cfg['launch_mode'])}")
    print(f"  URL: {_safe_url_label(cfg['launch_url'])}")
    print(f"  Screen Mode: {_screen_mode_label(cfg.get('screen_mode', DEFAULT_SCREEN_MODE))}")
    print()
    print("License:")
    print(f"  Key: {cfg.get('license_key') or 'Not set'}")
    print()
    # Advanced config is hidden from public summary in this version.
    print("Auto Resize:")
    print("  Automatic based on selected package count and device DPI")
    if len(enabled_entries) > 1:
        print("  Multi-package: 50% left reserved for Termux status panel, 50% right for Roblox")
    print()
    scripts = cfg.get("auto_execute_scripts") or []
    count = len(scripts) if isinstance(scripts, list) else 0
    print("Auto Execute:")
    print(f"  Saved scripts: {count}")


def _print_setup_menu(config_data: dict[str, Any], title: str = "DENG Tool: Rejoin Setup") -> None:
    cfg = safe_config_view(validate_config(config_data))
    print(termux_ui.separator("-"))
    print(title)
    print(termux_ui.separator("-"))
    print(f"1. Device Name: {cfg['device_name']}")
    print(f"2. Roblox Package: {cfg['roblox_package']}")
    print(f"3. Private Server URL: {_safe_url_label(cfg['launch_url'])}")
    print(f"4. Screen Mode: {_screen_mode_label(cfg.get('screen_mode', DEFAULT_SCREEN_MODE))}")
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
            return dict(entry)
    return package_entry(package, "", True, "not_set", app_name=str(app_name or "")[:120])


def _detect_or_prompt_account_username(entry: dict[str, Any], config_data: dict[str, Any] | None = None) -> dict[str, Any]:
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
    if _is_interactive():
        print(f"DENG could not safely detect a Roblox username/account name for {updated['package']}.")
        print("This name is only used to make the Start table easy to read.")
        manual = _prompt(f"Enter Roblox username/account name for {updated['package']}, or press Enter to skip", "").strip()
        if manual:
            updated["account_username"] = validate_account_username(manual)
            updated["username_source"] = "manual"
    return updated


def _try_detect_user_id(entry: dict[str, Any], draft: dict[str, Any]) -> tuple[int, str]:
    """Attempt root-assisted user ID detection for one package entry.

    Returns (user_id, source_label).  user_id=0 means not found.
    Never raises — setup must never crash here.
    """
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
    """Show account mapping table for the given package entries and let user edit/confirm.

    Shows: # | Package | Username | User ID | Source | Status
    Allows: A=accept all, <number>=edit that entry, B=back (cancel mapping only).
    Returns entries updated with any detected/confirmed roblox_user_id values
    and account_mapping_source / account_mapping_status / account_mapping_updated_at.
    Never blocks Start — missing mapping is just silently skipped.
    """
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
        print("  A. Accept all  |  1-N. Edit entry  |  B. Back")
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
            print("  Enter Roblox username or numeric user ID (blank to skip):")
            try:
                sys.stdout.flush()
            except Exception:  # noqa: BLE001
                pass
            inp_raw = safe_io.safe_prompt("  > ", allow_blank=True)
            if inp_raw is None:
                print()
                continue
            inp = inp_raw.strip()
            if not inp:
                detected[idx] = (0, "skipped")
                presence_statuses[idx] = ""
                continue
            if inp.isdigit() and int(inp) > 0:
                new_uid = int(inp)
                entry["roblox_user_id"] = new_uid
                detected[idx] = (new_uid, "manual")
                ps = _validate_user_id_with_presence(new_uid)
                presence_statuses[idx] = ps
                print(f"  Set user_id = {new_uid}  [{ps}]")
            else:
                try:
                    from . import roblox_presence as _rp
                    resolved = _rp.lookup_user_id(inp)
                except Exception:  # noqa: BLE001
                    resolved = None
                try:
                    entry["account_username"] = validate_account_username(inp)
                    entry["username_source"] = validate_username_source("manual", inp)
                except Exception:  # noqa: BLE001
                    pass
                if resolved and int(resolved) > 0:
                    entry["roblox_user_id"] = int(resolved)
                    detected[idx] = (int(resolved), "manual")
                    ps = _validate_user_id_with_presence(int(resolved))
                    presence_statuses[idx] = ps
                    print(f"  Resolved {inp} -> user_id {resolved}  [{ps}]")
                else:
                    print("  Username stored. Could not resolve user ID right now.")
                    detected[idx] = (0, "manual")
                    presence_statuses[idx] = ""
            entries = list(entries)
            entries[idx] = entry
        else:
            print("  Enter A, B, or a package number.")
            safe_io.press_enter()

    return _apply_mapping_to_entries(entries, detected, presence_statuses, config=draft)


def _auto_detect_cookies_for_entries(
    entries: list[dict[str, Any]],
    config: dict[str, Any] | None = None,
    *,
    force_refresh: bool = False,
    announce: bool = True,
) -> list[dict[str, Any]]:
    """Detect and attach .ROBLOSECURITY cookies for package entries via root."""
    from . import roblox_cookie_detect as _rcd

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
            cookie = _rcd.detect_roblox_cookie(
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
    """Apply detected user IDs and mapping metadata to a list of package entries."""
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
            selected = [_detect_or_prompt_account_username(_entry_for_package(manual, selected), cfg_ctx)]
            selected = _auto_detect_cookies_for_entries(selected, cfg_ctx)
            selected = _run_account_mapping_table(selected, cfg_ctx or {})

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


def _setup_launch_link(draft: dict[str, Any]) -> None:
    """Single-prompt URL setup — no mode toggle.

    The user pastes ONE Private Server URL.  We accept any of:

      * ``https://www.roblox.com/share?code=X&type=Server``    (new share link)
      * ``https://www.roblox.com/share-links?code=X&type=Server``
      * ``https://www.roblox.com/games/<id>/<name>?privateServerLinkCode=X``  (legacy)
      * ``roblox://navigation/share_links?code=X&type=Server``  (deep link)
      * ``roblox://placeId=<id>&privateServerLinkCode=X``       (legacy deep link)
      * blank — disable URL-based join, app-only mode

    Internally the URL is converted to its ``roblox://`` deep-link form
    at launch time (see ``url_utils.to_roblox_deep_link``); the user
    never has to know about modes.  We pick the storage ``launch_mode``
    based on the URL scheme so existing config plumbing keeps working.
    """
    print()
    print("Private Server URL")
    print("Paste a Roblox Private Server URL (or leave blank to skip).")
    print("Examples:")
    print("  https://www.roblox.com/share?code=XXXX&type=Server")
    print("  https://www.roblox.com/games/123/Game?privateServerLinkCode=YYY")
    print("  roblox://navigation/share_links?code=XXXX&type=Server")
    current = str(draft.get("launch_url") or "")
    value = _prompt("Private Server URL (blank to skip)", current).strip()

    if not value:
        draft["launch_mode"] = "app"
        draft["launch_url"] = ""
        print("Skipped. Clones will open the Roblox app without auto-joining a server.")
        return

    # Pick storage launch_mode by scheme; let validate_launch_url do the
    # final sanity check.  We accept the URL on any validation warning
    # (allow_uncertain=True) so the user isn't blocked by an over-strict
    # path allow-list — at launch time the deep-link converter handles
    # any unmapped path.
    scheme = value.split(":", 1)[0].lower() if ":" in value else ""
    mode = "deeplink" if scheme == "roblox" else "web_url"
    try:
        result = validate_launch_url(value, mode, allow_uncertain=True)
        if result.warning:
            print(f"Note: {result.warning}")
    except UrlValidationError as exc:
        print(f"That URL cannot be used: {exc}")
        print("Keeping previous launch link settings.")
        return
    draft["launch_mode"] = mode
    draft["launch_url"] = value
    print("Saved. At launch, this URL will be sent to Roblox as a deep link so")
    print("each clone joins the private server directly.")


def _setup_webhook(draft: dict[str, Any]) -> None:
    print()
    print("Discord Webhook Setup")
    print("DENG can send safe status updates to Discord. The webhook URL is stored locally and masked in screens/logs.")
    print("1. No")
    print("2. Yes, send new messages")
    print("3. Yes, edit one existing status message when possible")
    choice = _prompt("Choose webhook mode", "1").strip()
    if choice == "1":
        draft["webhook_enabled"] = False
        draft["webhook_snapshot_enabled"] = False
        draft["webhook_send_snapshot"] = False
        draft["webhook_mode"] = "new_message"
        return
    draft["webhook_enabled"] = True
    draft["webhook_mode"] = "new_message" if choice == "2" else "edit_message"
    while True:
        value = _prompt("Discord webhook URL", str(draft.get("webhook_url") or "")).strip()
        try:
            draft["webhook_url"] = webhook.validate_webhook_url(value)
            break
        except ValueError as exc:
            print(f"Webhook URL is not valid: {exc}")
    if draft["webhook_mode"] == "edit_message":
        draft["webhook_message_id"] = _prompt("Existing message ID (optional)", str(draft.get("webhook_message_id") or ""))


def _setup_snapshot(draft: dict[str, Any]) -> None:
    print()
    print("Phone Snapshot For Webhook")
    print("A snapshot may include private information visible on screen. Only enable it on your own phone/cloud phone.")
    print("1. No")
    print("2. Yes, attach snapshot")
    enabled = _prompt("Choose snapshot option", "1").strip() == "2"
    draft["webhook_snapshot_enabled"] = enabled
    draft["webhook_send_snapshot"] = enabled


def _setup_webhook_interval(draft: dict[str, Any]) -> None:
    print()
    print("Webhook Info Interval")
    print("Short intervals can spam Discord or hit rate limits. Minimum is 30 seconds.")
    choices = {"1": 30, "2": 60, "3": 300, "4": 600}
    print("1. 30 seconds")
    print("2. 1 minute")
    print("3. 5 minutes")
    print("4. 10 minutes")
    print("5. Custom")
    choice = _prompt("Choose interval", "3").strip()
    if choice in choices:
        draft["webhook_interval_seconds"] = choices[choice]
        return
    while True:
        value = _prompt_int("Webhook interval seconds", int(draft.get("webhook_interval_seconds", 300)), 30)
        try:
            draft["webhook_interval_seconds"] = webhook.validate_webhook_interval(value)
            return
        except ValueError as exc:
            print(exc)


def _setup_screen_mode(draft: dict[str, Any]) -> None:
    print()
    print("Screen Mode")
    print("1. Landscape")
    print("2. Portrait")
    current = validate_screen_mode(draft.get("screen_mode", DEFAULT_SCREEN_MODE))
    default = {
        "landscape": "1",
        "portrait": "2",
    }.get(current, "1")
    choices = {
        "1": "landscape",
        "2": "portrait",
        "landscape": "landscape",
        "portrait": "portrait",
        "potrait": "portrait",
    }
    while True:
        raw = _prompt("Choose screen mode", default).strip().lower()
        mode = choices.get(raw.replace("-", "_").replace(" ", "_"))
        if mode:
            draft["screen_mode"] = mode
            _enforce_configured_screen_mode(draft)
            return
        print("Choose 1 or 2.")


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
      3. Refresh Account Mapping
      4. Remove Package
      0. Back

    Manual username entry is an advanced fallback within Refresh Account Mapping,
    not a top-level menu item.
    """
    if not _is_interactive():
        return draft
    while True:
        print()
        entries = validate_package_entries(
            draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
        )
        enabled_entries = [e for e in entries if e.get("enabled", True)]
        current_lines = ["Current Packages:"]
        if enabled_entries:
            for idx, entry in enumerate(enabled_entries, start=1):
                username = _package_username_display(entry)
                current_lines.append(f"  {idx}. {entry['package']} — {username}")
        else:
            current_lines.append("  No Packages Configured.")
        termux_ui.print_submenu(
            "Packages",
            [
                ("1", "Auto Detect Package"),
                ("2", "Add Package"),
                ("3", "Refresh Account Mapping"),
                ("4", "Remove Package"),
                ("0", "Back"),
            ],
            current_lines=current_lines,
        )
        _mc = safe_io.safe_prompt(f"{termux_ui.choose_prompt('0')}: ", default="0")
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
                draft = _package_menu_refresh_mapping(draft)
            elif choice == "4":
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
    """Re-run username detection for each package; optionally persist results."""
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


def _package_menu_set_username(draft: dict[str, Any]) -> dict[str, Any]:
    """Manually set account_username for one package.

    Not offered in the Package submenu (detection covers typical cases). Kept for tests/tools.
    """
    print()
    print("Set / Edit Username")
    entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    for idx, entry in enumerate(entries, start=1):
        print(f"  {idx}. {entry['package']} — {_package_username_display(entry)}")
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
    current = validate_account_username(target.get("account_username", "")) or ""
    hint = f" [{current}]" if current else ""
    _ur = safe_io.safe_prompt(f"Roblox username / display name for {target['package']}{hint}: ")
    raw = (_ur or "").strip()
    if not raw:
        print("Skipped.")
        return draft
    try:
        target["account_username"] = validate_account_username(raw)
        target["username_source"] = validate_username_source("manual", target["account_username"])
    except ConfigError as exc:
        print(f"Invalid username: {exc}")
        safe_io.press_enter()
        return draft
    entries = [target if e["package"] == target["package"] else dict(e) for e in entries]
    draft["roblox_packages"] = entries
    draft = save_config(draft)
    print(f"Username saved for {target['package']}.")
    safe_io.press_enter()
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
    current_username = str(target.get("account_username") or "").strip()
    current_uid = int(target.get("roblox_user_id") or 0)
    print()
    print(f"Package: {target['package']}")
    if current_username:
        print(f"Username: {current_username}")
    if current_uid:
        print(f"User ID:  {current_uid}")
    print()
    print("Enter a Roblox username (we will auto-resolve to user ID),")
    print("or a numeric Roblox user ID directly.  Leave blank to cancel.")
    raw_inp = safe_io.safe_prompt("Username or user ID: ")
    raw = (raw_inp or "").strip()
    if not raw:
        print("Skipped.")
        return draft

    new_uid = 0
    new_username = current_username
    if raw.isdigit() and int(raw) > 0:
        new_uid = int(raw)
        print(f"Set user ID: {new_uid}")
    else:
        # Treat as username; auto-resolve.
        try:
            from . import roblox_presence as _rp

            resolved = _rp.lookup_user_id(raw)
        except Exception as exc:  # noqa: BLE001
            print(f"Username lookup failed: {exc}")
            print("Username stored without numeric ID; presence may not activate.")
            resolved = 0
        if resolved and int(resolved) > 0:
            new_uid = int(resolved)
            new_username = raw
            print(f"Resolved {raw} -> user_id {new_uid}")
        else:
            new_username = raw
            print("Could not resolve username via Roblox API right now.")
            print("Username stored — try again later or enter user ID directly.")

    try:
        target["account_username"] = validate_account_username(new_username)
        target["username_source"] = validate_username_source("manual", target["account_username"])
        target["roblox_user_id"] = int(new_uid) if new_uid > 0 else 0
    except ConfigError as exc:
        print(f"Invalid input: {exc}")
        safe_io.press_enter()
        return draft

    entries = [target if e["package"] == target["package"] else dict(e) for e in entries]
    draft["roblox_packages"] = entries
    draft = save_config(draft)
    if new_uid > 0:
        print(f"Saved user ID {new_uid} for {target['package']}.")
    else:
        print(f"Saved username for {target['package']}.")
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
    all_candidates = _gather_roblox_candidates_for_ui(draft)
    fresh_candidates = [c for c in all_candidates if c.package not in current_pkgs]

    if fresh_candidates:
        print(f"Detected {len(fresh_candidates)} new Roblox-like package(s):")
        _print_full_discovery_table(fresh_candidates)
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

    try:
        raw = input("Choose (e.g. 1,2 or A, M, B) [M]: ").strip().lower() or "m"
    except (EOFError, KeyboardInterrupt):
        print()
        return draft

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
        entry = _detect_or_prompt_account_username(_entry_for_package(manual, current_entries), draft)
        username = _package_username_display(entry)
        print()
        print(f"  Package:  {manual}")
        print(f"  Username: {username}")
        print()
        try:
            confirm = input("Add this package? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return draft
        if confirm in ("n", "no"):
            print("Cancelled.")
            return draft
        new_entries_to_append = [entry]

    elif raw == "a" and fresh_candidates:
        # Add all detected
        new_entries_to_append = [
            _detect_or_prompt_account_username(
                _entry_for_package(c.package, current_entries, app_name=c.app_name),
                draft,
            )
            for c in fresh_candidates
        ]
        print()
        print("Packages to add:")
        for i, entry in enumerate(new_entries_to_append, start=1):
            username = _package_username_display(entry)
            print(f"  {i}. {entry['package']} — Username: {username}")
        print()
        try:
            confirm = input("Add all these packages? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return draft
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
            _detect_or_prompt_account_username(
                _entry_for_package(c.package, current_entries, app_name=c.app_name),
                draft,
            )
            for c in picked
        ]
        print()
        print("Packages to add:")
        for i, entry in enumerate(new_entries_to_append, start=1):
            username = _package_username_display(entry)
            print(f"  {i}. {entry['package']} — Username: {username}")
        print()
        try:
            confirm = input("Add these packages? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return draft
        if confirm in ("n", "no"):
            print("Cancelled.")
            return draft
    else:
        print("No valid selection.")
        return draft

    if not new_entries_to_append:
        return draft

    new_entries_to_append = _auto_detect_cookies_for_entries(new_entries_to_append, draft)
    new_entries_to_append = _run_account_mapping_table(new_entries_to_append, draft)
    if not new_entries_to_append:
        print("Cancelled.")
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
        username = _package_username_display(entry)
        print(f"  {idx}. {entry['package']} — {username}")
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
    """Refresh Account Mapping: re-run root detection, username resolution, and Presence validation.

    Only available in Package Setup menu. NOT called from Start or supervisor.
    """
    print()
    print("Refresh Account Mapping")
    print()
    try:
        entries = validate_package_entries(
            draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
        )
    except ConfigError as exc:
        termux_ui.print_error(f"Could not read package mapping: {exc}")
        safe_io.press_enter()
        return draft

    enabled_entries = [e for e in entries if e.get("enabled", True)]
    if not enabled_entries:
        termux_ui.print_warning("No packages configured")
        safe_io.press_enter()
        return draft

    print(f"Re-running detection for {len(enabled_entries)} package(s)...")
    try:
        root_access.clear_cache()
    except Exception:  # noqa: BLE001
        termux_ui.print_warning("Root cache refresh failed; continuing with fallback mapping")

    fresh_entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    for entry in enabled_entries:
        e = dict(entry)
        pkg = str(e.get("package") or "").strip() or "Unknown"
        e["package"] = pkg
        e.pop("account_mapping_status", None)
        e.pop("account_mapping_source", None)
        e.pop("account_mapping_updated_at", None)
        e.pop("roblox_cookie", None)
        if not str(e.get("account_username") or "").strip():
            try:
                det = account_detect.detect_account_username(
                    pkg,
                    entry=e,
                    config=draft,
                    use_root=True,
                )
                if det and det.username:
                    e["account_username"] = validate_account_username(det.username)
                    e["username_source"] = validate_username_source(det.source, det.username)
            except PermissionError:
                skipped.append(f"{pkg}: permission denied")
            except Exception:
                skipped.append(f"{pkg}: username detection unavailable")
        fresh_entries.append(e)

    try:
        refreshed = _run_account_mapping_table(fresh_entries, draft, show_root_message=True)
    except Exception as exc:  # noqa: BLE001
        termux_ui.print_error(f"Refresh failed: {str(exc)[:120]}")
        safe_io.press_enter()
        return draft
    if not refreshed:
        termux_ui.print_warning("Refresh cancelled")
        safe_io.press_enter()
        return draft

    refreshed_by_pkg = {str(e.get("package") or ""): e for e in refreshed if isinstance(e, dict)}
    merged = [refreshed_by_pkg.get(str(e.get("package") or ""), e) for e in entries]
    draft["roblox_packages"] = merged
    try:
        draft = save_config(draft)
    except ConfigError as exc:
        termux_ui.print_error(f"Refresh completed but could not save config: {exc}")
        safe_io.press_enter()
        return draft
    if skipped:
        termux_ui.print_warning(f"Partial refresh: skipped {len(skipped)} detection step(s)")
    termux_ui.print_success("Account mapping refreshed and saved")
    safe_io.press_enter()
    return draft


def _package_menu_auto_detect(draft: dict[str, Any]) -> dict[str, Any]:
    """Auto-detect Roblox-like packages, let user select, then confirm before saving."""
    print()
    print("Auto Detect Package")
    print()
    candidates = _gather_roblox_candidates_for_ui(draft)
    if not candidates:
        print("No Roblox-like packages were detected.")
        print("Try: install Roblox or your clone APK, open it once, then try again.")
        print("Or use Add Package → manual entry as a fallback.")
        try:
            input("\nPress Enter to continue...")
        except EOFError:
            pass
        return draft

    current_entries = validate_package_entries(
        draft.get("roblox_packages") or [package_entry(DEFAULT_ROBLOX_PACKAGE, "", True, "not_set")]
    )
    current_pkgs = {e["package"] for e in current_entries}

    _print_full_discovery_table(candidates)
    new_candidates = [c for c in candidates if c.package not in current_pkgs]
    already_all = len(new_candidates) == 0
    if already_all:
        print("All detected packages are already configured.")
        try:
            input("\nPress Enter to continue...")
        except EOFError:
            pass
        return draft

    print(f"  A. Select all ({len(new_candidates)} new)")
    print("  B. Back (no change)")
    print()
    try:
        raw = input("Choose packages (e.g. 1,2 or A for all) [A]: ").strip().lower() or "a"
    except (EOFError, KeyboardInterrupt):
        print()
        return draft

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

    # Detect usernames for selected packages
    to_add_entries = [
        _detect_or_prompt_account_username(
            _entry_for_package(c.package, current_entries, app_name=c.app_name),
            draft,
        )
        for c in to_add_candidates
    ]

    to_add_entries = _auto_detect_cookies_for_entries(to_add_entries, draft)
    # Run account mapping table (root-assisted userId detection + confirmation)
    to_add_entries = _run_account_mapping_table(to_add_entries, draft)

    if not to_add_entries:
        print("Cancelled.")
        return draft

    for entry in to_add_entries:
        if entry["package"] not in current_pkgs:
            current_entries.append(entry)
            current_pkgs.add(entry["package"])

    draft["roblox_packages"] = current_entries
    active = enabled_package_entries(draft)
    draft["roblox_package"] = active[0]["package"]
    draft["selected_package_mode"] = "multiple" if len(active) > 1 else "single"
    draft = save_config(draft)
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
    """Private Server URL submenu — one URL field, no mode toggle.

    User reported the old multi-mode menu was "complicated and broken" —
    after a launch the clone landed in the Roblox lobby instead of the
    private server.  We now take a single URL and convert it to a
    ``roblox://`` deep link at launch time (see
    :func:`agent.url_utils.to_roblox_deep_link`).
    """
    if not _is_interactive():
        return draft
    while True:
        current_url = draft.get("launch_url", "") or ""
        if current_url:
            current_line = f"Current: {_safe_url_label(current_url)}"
        else:
            current_line = "Current: Not Set. Clones Will Open Roblox Without Joining A Server."
        termux_ui.print_submenu(
            "Private Server URL",
            [
                ("1", "Set Private Server URL"),
                ("2", "Clear Private Server URL"),
                ("3", "Show Current URL"),
                ("0", "Back"),
            ],
            current_lines=[current_line],
        )
        _llc = safe_io.safe_prompt(f"{termux_ui.choose_prompt('0')}: ", default="0")
        if _llc is None:
            break
        choice = _llc.strip() or "0"
        if choice == "0":
            break
        elif choice == "1":
            _setup_launch_link(draft)
            draft = save_config(draft)
            termux_ui.print_success("Private Server URL Saved")
        elif choice == "2":
            draft["launch_mode"] = "app"
            draft["launch_url"] = ""
            draft = save_config(draft)
            termux_ui.print_success("Private Server URL Cleared")
        elif choice == "3":
            url = draft.get("launch_url") or ""
            if url:
                print(f"  Private Server URL: {_safe_url_label(url)}")
            else:
                print("  Not set.")
            safe_io.press_enter()
        else:
            termux_ui.print_invalid_option()
    return draft


def _config_menu_screen_mode(draft: dict[str, Any]) -> dict[str, Any]:
    if not _is_interactive():
        return draft
    termux_ui.print_submenu_header("Screen Mode")
    print(f"Current: {_screen_mode_label(draft.get('screen_mode', DEFAULT_SCREEN_MODE))}")
    _setup_screen_mode(draft)
    draft = save_config(draft)
    termux_ui.print_success("Screen Mode Saved")
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
        result = safe_io.safe_prompt(f"{text}? (Y/N): ")
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


def _read_auto_execute_script(script_number: int) -> str | None:
    print()
    print(f"Paste Script #{script_number} Below.")
    print("Type END On A New Line When Finished.")
    lines: list[str] = []
    while True:
        raw = safe_io.safe_prompt("", default="", allow_blank=True)
        if raw is None:
            return None
        if raw == "END":
            break
        lines.append(raw)
    content = "\n".join(lines)
    if not content.strip():
        print("Script Cannot Be Empty.")
        return ""
    return content


def _add_auto_execute_scripts_interactive(scripts: list[str]) -> tuple[list[str], list[str]]:
    """Collect one or more scripts, numbered from the next saved slot."""
    from .auto_execute import MAX_AUTO_EXECUTE_SCRIPTS, script_id

    added: list[str] = []
    script_number = len(scripts) + 1
    while len(scripts) < MAX_AUTO_EXECUTE_SCRIPTS:
        answer = _prompt_yes_no_capitalized(f"Add Script #{script_number}")
        if answer is None:
            break
        if not answer:
            break
        script = _read_auto_execute_script(script_number)
        if script is None:
            break
        if not script:
            continue
        if script in scripts:
            print("Script already saved.")
            continue
        scripts.append(script)
        added.append(script)
        print(f"Auto Execute script #{script_number} saved: {script_id(script)}")
        script_number += 1
    if len(scripts) >= MAX_AUTO_EXECUTE_SCRIPTS:
        print("Auto Execute script limit reached.")
    return scripts, added


def _config_menu_auto_execute(draft: dict[str, Any]) -> dict[str, Any]:
    """Menu 5: manage saved Auto Execute scripts."""
    if not _is_interactive():
        return draft
    from .auto_execute import normalize_scripts, script_id, script_preview

    while True:
        try:
            scripts = normalize_scripts(draft.get("auto_execute_scripts"))
            draft["auto_execute_scripts"] = scripts
            current_lines = [f"Saved Scripts: {len(scripts)}"]
            if scripts:
                for idx, script in enumerate(scripts, 1):
                    current_lines.append(f"  {idx}. {script_preview(script)} [{script_id(script)}]")
            else:
                current_lines.append("  No saved scripts.")
            termux_ui.print_submenu(
                "Auto Execute",
                [
                    ("1", "Add Script"),
                    ("2", "Remove Script"),
                    ("3", "Remove All Scripts"),
                    ("0", "Back"),
                ],
                current_lines=current_lines,
            )
            raw = safe_io.safe_prompt(f"{termux_ui.choose_prompt('0')}: ", default="0")
            if raw is None:
                break
            choice = raw.strip() or "0"
            if choice == "0":
                break
            if choice == "1":
                scripts, added = _add_auto_execute_scripts_interactive(scripts)
                if not added:
                    safe_io.press_enter()
                    continue
                draft["auto_execute_scripts"] = normalize_scripts(scripts)
                draft = save_config(draft)
                termux_ui.print_success(f"Saved {len(added)} Auto Execute Script(s)")
                safe_io.press_enter()
            elif choice == "2":
                if not scripts:
                    termux_ui.print_warning("No saved scripts")
                    safe_io.press_enter()
                    continue
                idx_raw = safe_io.safe_prompt("Remove script number [0]: ", default="0")
                if idx_raw is None:
                    continue
                try:
                    idx = int((idx_raw or "0").strip()) - 1
                    if idx < 0 or idx >= len(scripts):
                        raise ValueError
                except (TypeError, ValueError):
                    print("Cancelled.")
                    safe_io.press_enter()
                    continue
                removed = scripts.pop(idx)
                draft["auto_execute_scripts"] = scripts
                draft = save_config(draft)
                print(f"Removed Auto Execute script: {script_id(removed)}")
                safe_io.press_enter()
            elif choice == "3":
                if not scripts:
                    termux_ui.print_warning("No saved scripts")
                    safe_io.press_enter()
                    continue
                confirm = safe_io.safe_prompt("Remove all Auto Execute scripts? Type YES: ", default="")
                if (confirm or "").strip() == "YES":
                    draft["auto_execute_scripts"] = []
                    draft = save_config(draft)
                    print("All Auto Execute scripts removed.")
                else:
                    print("Cancelled.")
                safe_io.press_enter()
            else:
                termux_ui.print_invalid_option()
                safe_io.press_enter()
        except (KeyboardInterrupt, EOFError):
            print()
            break
        except ConfigError:
            termux_ui.print_warning("Auto Execute config was missing or corrupt; recreated safe defaults")
            draft["auto_execute_scripts"] = []
            try:
                draft = save_config(draft)
            except Exception:  # noqa: BLE001
                pass
            safe_io.press_enter()
        except Exception as exc:  # noqa: BLE001
            termux_ui.print_error(f"Auto Execute menu error: {str(exc)[:120]}")
            safe_io.press_enter()
    return draft


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
    """Webhook submenu: URL / Interval / Mode / Snapshot / Test Webhook."""
    if not _is_interactive():
        return draft
    while True:
        print()
        print(termux_ui.separator("-"))
        print("Webhook")
        print(termux_ui.separator("-"))
        url = draft.get("webhook_url", "") or ""
        masked_url = webhook.mask_webhook_url(url) if url else "Not Set"
        interval = draft.get("webhook_interval_seconds", 300)
        mode = draft.get("webhook_mode", "new_message")
        mode_label = "Edit Message" if mode == "edit_message" else "New Message"
        snap = draft.get("webhook_snapshot_enabled", False)
        snap_enabled = bool(url) and snap
        print("Current Webhook:")
        print(f"  URL: {masked_url}")
        print(f"  Interval: {interval} Seconds")
        print(f"  Mode: {mode_label}")
        print(f"  Snapshot: {'Enabled' if snap_enabled else 'Disabled'}")
        print()
        print("1. Webhook URL")
        print("2. Webhook Interval")
        print("3. Webhook Mode")
        print("4. Snapshot")
        print("5. Test Webhook")
        print("0. Back")
        print(termux_ui.separator("-"))
        _whc = safe_io.safe_prompt("Choose [0]: ", default="0")
        if _whc is None:
            break
        choice = _whc.strip() or "0"
        if choice == "0":
            break
        elif choice == "1":
            _config_webhook_url(draft)
            draft = save_config(draft)
        elif choice == "2":
            _setup_webhook_interval(draft)
            draft = save_config(draft)
            print("Webhook Interval Saved.")
        elif choice == "3":
            _config_webhook_mode(draft)
            draft = save_config(draft)
            print("Webhook Mode Saved.")
        elif choice == "4":
            if draft.get("webhook_url"):
                _setup_snapshot(draft)
                draft = save_config(draft)
                print("Snapshot Setting Saved.")
            else:
                print("Set Webhook URL First.")
                safe_io.press_enter()
        elif choice == "5":
            _test_webhook(draft)
        else:
            print("Please choose 1-5 or 0.")
    return draft


def _config_webhook_url(draft: dict[str, Any]) -> None:
    """Set or update the webhook URL. The full URL is never printed."""
    print()
    print("Webhook URL")
    print("Leave blank to skip.")
    current = draft.get("webhook_url", "") or ""
    if current:
        print(f"Current: {webhook.mask_webhook_url(current)}")
    else:
        print("Current: Not Set")
    value = _prompt("Discord Webhook URL (blank to skip)", "").strip()
    if not value:
        print("Skipped.")
        return
    try:
        draft["webhook_url"] = webhook.validate_webhook_url(value)
        draft["webhook_enabled"] = True
        print("Webhook URL Saved.")
    except ValueError as exc:
        print(f"Webhook URL Is Not Valid: {exc}")


def _config_webhook_mode(draft: dict[str, Any]) -> None:
    """Set the webhook operating mode."""
    print()
    print("Webhook Mode")
    print("1. Off")
    print("2. Status Monitor (New Messages)")
    print("3. Alert Only (New Messages)")
    print("4. Status + Alerts (Edit Message)")
    current_enabled = draft.get("webhook_enabled", False)
    current_mode = draft.get("webhook_mode", "new_message")
    default = "1" if not current_enabled else ("4" if current_mode == "edit_message" else "2")
    _wmc = safe_io.safe_prompt(f"Choose [{default}]: ", default=default)
    choice = (_wmc or default).strip() or default
    if choice == "1":
        draft["webhook_enabled"] = False
        draft["webhook_snapshot_enabled"] = False
        draft["webhook_send_snapshot"] = False
    elif choice in {"2", "3"}:
        draft["webhook_enabled"] = True
        draft["webhook_mode"] = "new_message"
    elif choice == "4":
        draft["webhook_enabled"] = True
        draft["webhook_mode"] = "edit_message"
    else:
        print("Unknown choice. Keeping current mode.")


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
        print("You will set Roblox packages (scan or manual), optional private URL, screen mode, webhook, then save.")
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
    print("  2. Roblox public / private server link")
    print("  3. Screen mode")
    print("  4. Discord webhook setup")
    print("  5. Phone snapshot for webhook (only when webhook is enabled)")
    print("  6. Webhook info interval (only when webhook is enabled)")
    print("  7. Auto Execute scripts (optional)")
    print("  8. Save and start")
    print()
    print("Package detection:")
    print("  The tool scans installed Roblox apps against safe hints. Pick from the table.")
    print("  Manual package entry is only a fallback if nothing is found.")
    print()
    print("Step 1 of 8: Roblox Package Setup")
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
    print("\nStep 2 of 8: Roblox Public / Private Server Link")
    _setup_launch_link(draft)
    print("\nStep 3 of 8: Screen Mode")
    _setup_screen_mode(draft)
    print("\nStep 4 of 8: Discord Webhook Setup")
    _setup_webhook(draft)
    if draft.get("webhook_enabled"):
        print("\nStep 5 of 8: Phone Snapshot For Webhook")
        _setup_snapshot(draft)
        print("\nStep 6 of 8: Webhook Info Interval")
        _setup_webhook_interval(draft)
    print("\nStep 7 of 8: Auto Execute (Optional)")
    if _prompt_yes_no_capitalized("Configure Auto Execute Scripts Now") is True:
        scripts, _added = _add_auto_execute_scripts_interactive(list(draft.get("auto_execute_scripts") or []))
        draft["auto_execute_scripts"] = scripts
    print("\nStep 8 of 8: Save And Start")
    draft["first_setup_completed"] = True
    try:
        saved = save_config(draft)
    except ConfigError as exc:
        termux_ui.print_error(f"Setup Could Not Be Saved: {exc}")
        return None, False
    termux_ui.print_success("First-Time Setup Complete")
    _print_config_summary(saved)
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
    while True:
        termux_ui.print_config_menu()
        choice = safe_io.safe_prompt(f"{termux_ui.choose_prompt('0')}: ", default="0")
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
            draft = _config_menu_screen_mode(draft)
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
Layout Recovery — Restoring Normal Orientation
===============================================

If your Termux terminal or screen is sideways after a layout operation:

Method 1 — Rotate the phone back manually:
  Rotate your physical device to portrait and lock portrait mode in Settings.

Method 2 — Android Settings:
  Settings → Display → Auto-rotate → turn OFF, then set Portrait.

Method 3 — Quick Settings tile:
  Swipe down the notification shade and tap the Auto-Rotate toggle.

Method 4 — ADB (from a PC):
  adb shell settings put system user_rotation 0

Method 5 — Inside Termux:
  settings put system user_rotation 0

Important:
  DENG Tool: Rejoin never issues global rotation commands.
  If Termux appears sideways, the likely cause is App Cloner applying
  a window position that forces landscape mode on your device.
  The window layout only writes to the Roblox app clone preferences,
  NOT to Termux or system UI.

After restoring orientation:
  1. Restart Termux.
  2. Run: deng-rejoin
  3. If the issue recurs, disable layout in config (auto_resize_enabled = false).
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
        print(_LAYOUT_RESET_INSTRUCTIONS)
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
    print(f"  Launch mode: {_launch_mode_label(cfg['launch_mode'])}")
    print(f"  Launch URL: {_safe_url_label(cfg.get('launch_url'))}")
    print(f"  Screen mode: {_screen_mode_label(cfg.get('screen_mode', DEFAULT_SCREEN_MODE))}")
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
        print(f"  Snapshot: {_yes_no(cfg['webhook_snapshot_enabled'])}")
        print(f"  Interval: {cfg['webhook_interval_seconds']} seconds")
        print(f"  URL: {safe.get('webhook_url') or 'Not set'}")
        tags = cfg.get("webhook_tags") or []
        if tags:
            print(f"  Tags: {', '.join(tags)}")
    else:
        print("  Snapshot: Disabled")
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
    """Return the display username for a Start table row — shows 'Unknown' if not set."""
    return validate_account_username(entry.get("account_username", "")) or "Unknown"


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
    color = {
        "Started":      _ANSI_GREEN,
        "Online":            _ANSI_GREEN,
        "Lobby":             _ANSI_GREEN,    # app open at home
        "In Server":         _ANSI_GREEN,    # strong evidence: experience loaded
        ("Join " + "Unconfirmed"):  _ANSI_YELLOW,   # legacy alias
        "Ready":             _ANSI_YELLOW,
        "Starting":          _ANSI_YELLOW,
        "Launching":         _ANSI_YELLOW,
        "Relaunching":       _ANSI_YELLOW,
        "Launched":          _ANSI_GREEN,    # Roblox process up, no URL yet
        "Disconnected":      _ANSI_RED,      # Roblox error code detected
        "No Heartbeat":      _ANSI_RED,      # running but not playing normally
        "In-Lobby":          _ANSI_YELLOW,
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
        "Waiting":      _ANSI_YELLOW, # internal only
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
    }.get(status, "")
    return f"{color}{status}{_ANSI_RESET}" if color else status


def build_start_table(rows: list[tuple], *, use_color: bool = False) -> str:
    """Build the public start summary table: #, Package, Username, State, Runtime, Usage.

    Rows may be 4-tuples (idx, pkg, username, state) for backward compatibility,
    5-tuples (idx, pkg, username, state, runtime), or
    6-tuples (idx, pkg, username, state, runtime, usage).
    Missing trailing columns are shown as empty cells.

    With ``use_color=True`` every cell is rendered in bold so the table
    is readable on the Termux default monospace font (which renders the
    regular weight as a hairline).  Status cells additionally carry
    their colour code from :func:`_colorize_status`; the rest of the
    cells get the plain ``BOLD`` escape only.
    """
    headers = ("#", "Package", "Username", "State", "Runtime", "Usage")
    str_rows = [
        (
            str(r[0]), _short_package_display(r[1]), str(r[2]), str(r[3]),
            str(r[4]) if len(r) > 4 else "",
            str(r[5]) if len(r) > 5 else "",
        )
        for r in rows
    ]

    widths = [
        max(len(headers[i]), max((_visible_len(r[i]) for r in str_rows), default=0))
        for i in range(6)
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
            _bold(r[4]),
            _bold(r[5]),
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
    return "\n".join(lines)


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
    ("no heartbeat", "no heartbeat (recovering)."),
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
    "Lobby":             "online",         # healthy at home screen
    "In Server":         "online",         # confirmed in target server
    ("Join " + "Unconfirmed"):  "launching",
    "No Heartbeat":      "no heartbeat",   # was in game, heartbeat stalled
    "In-Lobby":          "in lobby",
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
    username = validate_account_username(entry.get("account_username", "")) or entry["package"]
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
        packages = [entry["package"] for entry in entries]
        n        = len(packages)

        # Compute layout rectangles
        try:
            display = window_layout.detect_display_info()
        except Exception:  # noqa: BLE001
            display = window_layout.DisplayInfo(width=1080, height=1920, density=420)

        # Use the full physical screen width for the package grid (probe
        # p-cf20e97a18: packages were confined to right 33% because
        # left_fraction=0.50 reserved the left half THEN lte6 skipped col0
        # of that half, leaving only cols 1+2 of the right 640px).  With
        # left_fraction=0.0 the grid spans the full display width; lte6 keeps
        # col0 naturally empty for Termux, giving packages 2/3 of the screen.
        _dock_frac = 0.0
        _screen_mode = validate_screen_mode(cfg.get("screen_mode", DEFAULT_SCREEN_MODE))
        filtered_packages = [p for p in packages if not window_layout._is_layout_excluded(p)]
        rects = window_layout.calculate_split_layout(
            filtered_packages,
            display.width, display.height,
            termux_log_fraction=_dock_frac,
            screen_mode=_screen_mode,
        )
        # Derive Termux dock fraction from the actual package bounds so that
        # _enforce_termux_left_layout (called later in cmd_start) minimises
        # Termux to exactly the empty col0 area rather than a hard-coded 50%.
        if rects and display.width > 0:
            _termux_frac = min(r.left for r in rects) / display.width
        else:
            _termux_frac = 0.0
        cfg["termux_dock_fraction"] = _termux_frac

        # ── release grid probe — emitted at INFO so it lands in probe
        try:
            from . import window_layout as _wl
            _orient = _wl.detect_layout_orientation(display.width, display.height)
            _sb_h   = _wl._detect_status_bar_height()
            _left_end = min(r.left for r in rects) if rects else 0
            if _screen_mode == "portrait":
                _cols, _rows = 2, 5
                _slot_order = "7,8,9,10,1,2,3,4,5,6"
                _landscape_rule = ""
            else:
                _cols, _rows = 3, 3
                _rule = getattr(_wl, "LANDSCAPE_SLOT_RULES", {}).get(
                    len(filtered_packages),
                    (1, 2, 3, 4, 5, 6, 7, 8, 9),
                )
                _slot_order = ",".join(str(x) if x else "empty" for x in _rule)
                _landscape_rule = f"count_{len(filtered_packages)}_3x3"
            _pane_w = max(160, display.width - _left_end)
            _usable_h = max(90, display.height - _sb_h)
            _cell_w = max(160, _pane_w // _cols)
            _cell_h = max(90, _usable_h // _rows)
            _layout_log.info(
                "[DENG_REJOIN_SCREEN_MODE] configured=%s detected_orientation=%s applied_mode=%s root_available=%s",
                _screen_mode, _orient, _screen_mode, str(android.detect_root().available).lower(),
            )
            _layout_log.info(
                "[DENG_REJOIN_SPLIT_LAYOUT] screen_w=%d screen_h=%d termux_area=left_col0 "
                "roblox_area=right_cols1_plus termux_desired=%s termux_actual=%s "
                "roblox_grid_area=%s full_width_used=true",
                display.width,
                display.height,
                (0, 0, _left_end, display.height),
                "",
                (0, _sb_h, display.width, display.height),
            )
            if _screen_mode == "landscape":
                _layout_log.info(
                    "[DENG_REJOIN_LANDSCAPE_SLOT_MAP] package_count=%d rule=%s grid=3x3 "
                    "slot_map=%s roblox_grid_area=%s bounds=%s",
                    len(filtered_packages),
                    _landscape_rule,
                    _slot_order,
                    (0, _sb_h, display.width, display.height),
                    [(r.left, r.top, r.right, r.bottom) for r in rects],
                )
            _layout_log.info(
                "[DENG_REJOIN_LAYOUT_GRID] mode=%s package_count=%d grid=%dx%d slot_order=%s "
                "top_inset=%d screen_w=%d screen_h=%d termux_bounds=%s package_bounds=%s",
                _screen_mode,
                len(filtered_packages),
                _cols,
                _rows,
                _slot_order,
                _sb_h,
                display.width,
                display.height,
                (0, 0, _left_end, display.height),
                [(r.left, r.top, r.right, r.bottom) for r in rects],
            )
        except Exception:  # noqa: BLE001
            pass

        # ── [DENG_REJOIN_LAYOUT_BOUNDS] — per-package desired bounds + overlap check
        try:
            _seen_bounds: list[tuple[int, int, int, int]] = []
            for _bi, _r in enumerate(rects):
                _row = _bi // max(1, _cols)
                _col = _bi % max(1, _cols)
                _overlap = any(
                    not (_r.right <= _o[0] or _o[2] <= _r.left or
                         _r.bottom <= _o[1] or _o[3] <= _r.top)
                    for _o in _seen_bounds
                )
                _seen_bounds.append((_r.left, _r.top, _r.right, _r.bottom))
                _layout_log.info(
                    "[DENG_REJOIN_LAYOUT_BOUNDS] package=%s index=%d row=%d col=%d"
                    " desired_x=%d desired_y=%d desired_w=%d desired_h=%d"
                    " actual_before=pending actual_after=pending overlap_detected=%s",
                    _r.package, _bi, _row, _col,
                    _r.left, _r.top, _r.win_w, _r.win_h,
                    "true" if _overlap else "false",
                )
        except Exception:  # noqa: BLE001
            pass

        try:
            cfg["last_layout_preview"] = [r.as_dict() for r in rects]
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

        # ── Pre-launch apply: write XML + force-stop so prefs are honored
        # ── on the imminent relaunch (cmd_start will issue the launches).
        try:
            from . import window_apply
            results = window_apply.apply_window_layout(
                rects,
                force_stop_before=True,    # MUST force-stop so prefs reload
                relaunch_after=False,
                verify_after=False,        # verification deferred to post-launch
                retries=0,
            )
            for r in results:
                _layout_log.debug(
                    "pre-launch apply: %s ok=%s method=%s status=%s attempts=%s",
                    r.package, r.pre_write_ok, r.pre_write_method, r.status,
                    "; ".join(r.attempts),
                )
            cfg["_layout_rects"] = [r.desired.as_dict() for r in results]
        except Exception as exc:  # noqa: BLE001
            _layout_log.debug("pre-launch apply error (non-fatal): %s", exc)

        return cfg, f"layout_prepared n={n}"
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


def _verify_layout_post_launch(
    cfg: dict[str, Any], entries: list[dict[str, Any]]
) -> tuple[dict[str, bool], list[dict[str, Any]]]:
    """Post-launch verification + direct-resize retry.

    Runs silently after the launch grace.  Returns a tuple of
    ``({package: applied_ok}, [diagnostic_rows...])`` so callers can include
    per-package details in the start diagnostics JSON.
    """
    import logging as _logging
    _layout_log = _logging.getLogger("deng.rejoin.layout")
    out: dict[str, bool] = {}
    diag_rows: list[dict[str, Any]] = []
    try:
        display = window_layout.detect_display_info()
        _dock_frac2 = 0.50
        rects = window_layout.calculate_split_layout(
            [e["package"] for e in entries if not window_layout._is_layout_excluded(e["package"])],
            display.width, display.height,
            termux_log_fraction=_dock_frac2,
            screen_mode=validate_screen_mode(cfg.get("screen_mode", DEFAULT_SCREEN_MODE)),
        )
        from . import window_apply
        results = window_apply.apply_window_layout(
            rects,
            force_stop_before=False,
            relaunch_after=False,
            verify_after=True,
            retries=0,  # MUST be 0: retries>0 force-stops running apps
        )
        for r in results:
            out[r.package] = r.final_ok
            diag_rows.append({
                "package":        r.package,
                "desired":        r.desired.as_dict() if hasattr(r.desired, "as_dict") else {
                    "left": r.desired.left, "top": r.desired.top,
                    "right": r.desired.right, "bottom": r.desired.bottom,
                },
                "actual_bounds":  r.actual_bounds,
                "actual_method":  r.actual_method,
                "status":         r.status,
                "pre_write_ok":   r.pre_write_ok,
                "pre_write_method": r.pre_write_method,
                "direct_resize_ok": r.direct_resize_ok,
                "attempts":       r.attempts,
                "final_ok":       r.final_ok,
            })
            _layout_log.debug(
                "post-launch verify: %s ok=%s actual=%s method=%s attempts=%s",
                r.package, r.final_ok, r.actual_bounds, r.actual_method, "; ".join(r.attempts),
            )
    except Exception as exc:  # noqa: BLE001
        _layout_log.debug("verify_layout_post_launch error: %s", exc)
    return out, diag_rows


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
    # Silence all internal loggers so warnings/errors go to file, never stdout.
    from .logger import silence_public_loggers
    silence_public_loggers()

    def _transition_lifecycle(to_state: str, reason: str) -> None:
        nonlocal _lifecycle_state
        try:
            from .logger import configure_logging, log_event
            log_event(
                configure_logging(),
                "info",
                "[DENG_REJOIN_LIFECYCLE]",
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

    _clear_terminal(clear_scrollback=True)
    try:
        _transition_lifecycle("STARTING", "cmd_start")
        cfg = load_config()
        cfg = _ensure_install_id_saved(cfg)
        _enforce_configured_screen_mode(cfg)
        _enforce_termux_left_layout(cfg)

        # ── License gate ─────────────────────────────────────────────────────
        if not keystore.DEV_MODE:
            license_cfg = cfg.get("license") or {}
            if not license_cfg.get("disabled_by_user") and license_cfg.get("enabled", True):
                if str(license_cfg.get("mode") or "remote").strip().lower() == "local":
                    _key = (license_cfg.get("key") or "").strip() or (cfg.get("license_key") or "").strip()
                    if _key:
                        ok, msg = keystore.verify_key(_key)
                        if not ok:
                            _print_license_err(f"License key error: {msg}", use_color)
                            print_beginner_license_gate_help()
                            return 1
                    else:
                        _print_license_err("No License Key Found", use_color)
                        print_beginner_license_gate_help()
                        return 1
                else:
                    if not verify_remote_license_noninteractive(cfg, use_color=use_color):
                        return 1

        # Report tool execution for public stable builds (silently, non-blocking).
        _report_key_execution_if_public(cfg)

        entries = enabled_package_entries(cfg)
        if not cfg.get("first_setup_completed"):
            print("First-time setup is required before starting.")
            if _is_interactive():
                _run_first_time_setup_wizard(cfg, args, start_after_save=True)
                return 0
            print("Run: deng-rejoin and choose First Time Setup Config.")
            return 2

        if not entries:
            print("No Roblox Package Selected")
            print()
            print("Run Setup / Edit Config, then choose Roblox Package Setup.")
            return 2
        _enforce_configured_screen_mode(cfg, [entry["package"] for entry in entries])

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
                    return 1
        except Exception as exc:  # noqa: BLE001
            print(f"Could not create Start lock: {exc}")
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
        _start_log.debug("start: detected_packages=%d", detected_n)
        if detected_n == 0:
            # Still report this to stdout so user knows something is wrong
            print("No Roblox Package Detected")
            print()
            print("  1. Install Roblox or your clone APK.")
            print("  2. Open Roblox once manually, then return to Termux.")
            print("  3. Run package detection again.")
            print()

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

        def _render_phase(_unused_note: str = "") -> None:
            """Atomically redraw the dashboard with the current phase per package.

            Shows only: logo + available RAM + table.
            State labels in the table already describe what is happening;
            additional notes below the table are suppressed (user feedback:
            "useless text explaining state" when state is in the table).
            Uses clear_scrollback=True on every call so old banner/table lines
            from prior phases never bleed through on slow Termux terminals.
            """
            rows = [
                (i + 1, e["package"], _account_username_for_table(e),
                 phase.get(e["package"], "Preparing"), "", "")
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
            _clear_terminal(clear_scrollback=True)
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()

        def _set_all_phase(label: str, note: str = "") -> None:
            for pkg in phase:
                phase[pkg] = label
            _render_phase(note)

        # 1) "Preparing" — force-stop each configured package individually,
        #    verify it is dead, and clear background apps to free RAM.
        #    Only configured/selected packages are targeted; Termux and
        #    system apps are never touched.
        _transition_lifecycle("PREPARING", "prepare_packages")
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

        # 2) "Clear Cache" — clear only cache/code_cache for each configured
        #    package.  shared_prefs/databases/files are never touched.
        #    Size is verified to reach 0; retry on non-zero remainder.
        prep_gfx: dict[str, str] = {}
        prep_cache: dict[str, str] = {}   # per-package cache-clear status label
        opt = cfg.get("optimization") if isinstance(cfg.get("optimization"), dict) else {}
        # Set all packages to "Clear Cache" and render ONCE before the loop.
        _transition_lifecycle("CLEARING_CACHE", "clear_cache")
        for entry in entries:
            phase[entry["package"]] = "Clear Cache"
        _render_phase()
        # Re-apply the selected Screen Mode after background cleanup.  The
        # previous auto-rotation restore let third-party rotation apps win and
        # could flip the device away from the user's selected mode.
        _enforce_configured_screen_mode(cfg, packages_sl)
        for entry in entries:
            pkg = entry["package"]
            _cache_result: dict[str, object] = {}
            try:
                _cache_result = android.clear_package_cache_verified(pkg)
            except Exception as _exc:  # noqa: BLE001
                _cache_result = {
                    "success": False, "skipped": False, "skipped_reason": "",
                    "cache_paths": [], "size_before_bytes": 0, "size_after_bytes": 0,
                    "attempts": 0, "error": str(_exc)[:80],
                }
            _start_log.info(
                "[DENG_REJOIN_CLEAR_CACHE] package=%s cache_paths=%s"
                " size_before_bytes=%s size_after_bytes=%s attempt=%s"
                " success=%s error=%s",
                pkg,
                ",".join(str(p) for p in _cache_result.get("cache_paths") or []) or "none",
                _cache_result.get("size_before_bytes", 0),
                _cache_result.get("size_after_bytes", 0),
                _cache_result.get("attempts", 0),
                str(_cache_result.get("success", False)).lower(),
                _cache_result.get("error", ""),
            )
            if _cache_result.get("success"):
                prep_cache[pkg] = "Cleared"
            elif _cache_result.get("skipped"):
                prep_cache[pkg] = "Skipped"
            else:
                prep_cache[pkg] = "Failed"
            low = bool(opt.get("low_graphics_enabled", True)) and bool(entry.get("low_graphics_enabled", True))
            try:
                prep_gfx[pkg] = android.apply_low_graphics_optimization(pkg, enabled=low)
            except Exception:  # noqa: BLE001
                prep_gfx[pkg] = "error"
            _start_log.debug("start: prep pkg=%s cache_ok=%s gfx=%s",
                             pkg, _cache_result.get("success"), prep_gfx[pkg])

        # 3) Compute window layout silently (no public phase change).
        try:
            cfg, _layout_note = _prepare_automatic_layout(cfg, entries)
            _start_log.debug("start: layout note=%s", _layout_note)
        except Exception as _exc:  # noqa: BLE001
            _start_log.debug("start: layout error (non-fatal): %s", _exc)

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

        # ── Launch URL confirmation (safe — never expose the raw URL) ────────
        _global_url = str(effective_private_server_url(runtime_entries[0] if runtime_entries else {}, runtime_cfg) or "").strip() \
            if runtime_entries else str(runtime_cfg.get("private_server_url") or runtime_cfg.get("launch_url") or "").strip()
        if _global_url:
            _start_log.info("start: Launch URL configured — sending private server deep link to each clone")
        else:
            _start_log.info("start: No launch URL configured — clones will open Roblox home")

        # ── Launch each package ───────────────────────────────────────────────
        # 5) "Launching" — set ALL packages to "Launching" at once before
        # the loop and render a single clean "all starting" screen.  Only
        # re-render AFTER each launch (to show success/failure result).
        _transition_lifecycle("LAUNCHING", "launch_packages")
        launch_ok:  dict[str, bool] = {}
        launch_err: dict[str, str]  = {}
        launch_attempted: dict[str, bool] = {}
        for entry in entries:
            phase[entry["package"]] = "Launching"
        _render_phase("Launching clones...")
        for index, entry in enumerate(entries, start=1):
            package = entry["package"]
            runtime_entry = runtime_entry_by_pkg.get(package, entry)
            launch_attempted[package] = True
            package_cfg = dict(runtime_cfg)
            package_cfg["roblox_package"] = package
            _has_url = bool(str(
                effective_private_server_url(runtime_entry, runtime_cfg) or ""
            ).strip())
            # Ensure package key file is correct before launch.
            # Writes: /storage/emulated/0/Android/data/{pkg}/files/gloop/external/Internals/Cache/license
            # Only if a FREE_ key is configured; does NOT touch DENG Tool license.
            try:
                from .package_key import ensure_package_key_for_start as _epkfs
                _pk_result = _epkfs(
                    package, runtime_cfg,
                    root_enabled=bool(runtime_cfg.get("root_mode_enabled", False)),
                )
                _start_log.info(
                    "[DENG_REJOIN_PACKAGE_KEY] package=%s mode=start_ensure path=%s"
                    " key_prefix=%s key_masked=%s write_needed=%s write_attempted=%s"
                    " method=%s success=%s error=%s",
                    package,
                    _pk_result.get("path", ""),
                    _pk_result.get("key_prefix", ""),
                    _pk_result.get("key_masked", ""),
                    str(_pk_result.get("write_needed", False)).lower(),
                    str(_pk_result.get("write_attempted", False)).lower(),
                    _pk_result.get("method", "skipped"),
                    str(_pk_result.get("success", True)).lower(),
                    _pk_result.get("error", ""),
                )
            except Exception as _pk_exc:  # noqa: BLE001
                _start_log.debug("package_key ensure error (non-fatal): %s", _pk_exc)
            result = perform_rejoin(package_cfg, reason="start", package_entry=runtime_entry)
            launch_ok[package]  = result.success
            launch_err[package] = result.error or ""
            _start_log.info(
                "[DENG_REJOIN_LAUNCH_PACKAGE] package=%s launcher=%s"
                " result=%s return_code=%s success=%s",
                package,
                "private_url" if _has_url else "app_only",
                result.error or "ok", 0 if result.success else 1,
                str(result.success).lower(),
            )
            # Try to mute the package audio (per-package, root, non-fatal).
            if result.success:
                try:
                    _mute = android.mute_package_audio(package)
                    _start_log.info(
                        "[DENG_REJOIN_PACKAGE_VOLUME] package=%s method=%s"
                        " target_volume=0 success=%s skipped_reason=%s error=%s",
                        package,
                        _mute.get("method", ""),
                        str(_mute.get("success", False)).lower(),
                        _mute.get("skipped_reason", ""),
                        _mute.get("error", ""),
                    )
                except Exception:  # noqa: BLE001
                    pass
            # Update result state and render once per completed launch.
            phase[package] = "Launching" if result.success else "Failed"
            _render_phase()
            _start_log.debug(
                "start: launch pkg=%s ok=%s err=%s",
                package, result.success, result.error or "",
            )

        # 6) Grace wait before verifying layout — keep packages shown as
        #    "Launching" (no "Waiting" label shown in public UI).
        grace_wait = int(sup.get("launch_grace_seconds", 15))
        import time as _time
        _time.sleep(max(5, grace_wait))

        # 8) Verify layout silently — no "Resizing" label shown (user feedback:
        #    showing "Resizing" after launching is confusing/useless; the
        #    supervisor's real-time detection will update the state next).
        _layout_verify: dict[str, bool] = {}
        _layout_diag: list[dict[str, Any]] = []
        try:
            _layout_verify, _layout_diag = _verify_layout_post_launch(cfg, entries)
            _start_log.debug("post-launch layout verify: %s", _layout_verify)
        except Exception as _exc:  # noqa: BLE001
            _start_log.debug("post-launch verify error: %s", _exc)

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
                    "private_url_set": bool(
                        str(effective_private_server_url(runtime_entry_by_pkg.get(pkg, entry), runtime_cfg) or "").strip()
                    ),
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
                "launches": {
                    pkg: {"ok": launch_ok.get(pkg, False),
                          "error": launch_err.get(pkg, "") or ""}
                    for pkg in launch_ok
                },
            })
        except Exception as _exc:  # noqa: BLE001
            _start_log.debug("save start diagnostics error: %s", _exc)

        # ── Build initial status table ────────────────────────────────────────
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
            elif android.is_process_running(pkg):
                # Process is up.  WatchdogSupervisor will classify on first check.
                # Use Launching for all cases.
                state = "Launching"
                stat_internal = "process running"
            else:
                state = "Launching"
                stat_internal = "launch command sent"
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
        snapshot_path = None
        if cfg.get("webhook_enabled") and cfg.get("webhook_snapshot_enabled"):
            from . import snapshot as _snapshot
            _snapshot.cleanup_old_snapshots(int(cfg.get("snapshot_max_age_seconds", 300)))
            snapshot_path, _snap_message = _snapshot.capture_snapshot()
            if snapshot_path:
                cfg["snapshot_temp_path"] = str(snapshot_path)

        if cfg.get("webhook_enabled"):
            mem_info = android.get_memory_info()
            cpu_pct = android.get_cpu_usage()
            temp_c = android.get_temperature()
            cfg["_mem_info"] = mem_info
            cfg["_cpu_pct"] = cpu_pct
            cfg["_temp_c"] = temp_c

            app_stats: dict[str, Any] = {}
            for entry in entries:
                pkg = entry["package"]
                is_online = initial_status.get(pkg) == "Online"
                mem_mb = android.get_app_memory_mb(pkg) if is_online else None
                app_stats[pkg] = {
                    "online": is_online,
                    "memory_mb": mem_mb,
                    "cpu_pct": None,
                    "uptime_start": start_times.get(pkg),
                }

            ok_wh, message_wh, message_id = webhook.send_webhook_update(
                cfg,
                event="start",
                snapshot_path=snapshot_path,
                force=True,
                app_stats=app_stats,
            )
            if message_id:
                cfg["webhook_last_message_id"] = message_id

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
            print(f"  Detail: {best_reason}")
            _release_start_lock("normal_exit")
            return 1

        # ── Supervisor loop — dashboard takes over entirely ───────────────────
        # From this point on, _live_dashboard() clears the terminal and
        # redraws logo + table on every refresh.  No other text is printed.
        # Keep the live supervisor config valid.  The dashboard still refreshes
        # frequently below; worker health checks must stay within config bounds.
        _live_cfg = dict(runtime_cfg)
        _live_cfg["package_start_times"] = start_times
        _sup_sub = dict(
            cfg.get("supervisor") if isinstance(cfg.get("supervisor"), dict) else {}
        )
        _hci_raw = int(_sup_sub.get("health_check_interval_seconds", 10))
        _sup_sub["health_check_interval_seconds"] = max(10, _hci_raw)
        _live_cfg["supervisor"] = _sup_sub
        # [DENG_REJOIN_WATCHDOG_FIX] Use WatchdogSupervisor: sequential per-package
        # loop, process-check-first, 4 states only, never stops after Online.
        _supervisor = WatchdogSupervisor(runtime_entries, _live_cfg, initial_status=initial_status)
        _supervisor_ref = _supervisor
        _live_map = _supervisor.status_map  # dict mutated in-place by watchdog loop

        # Public state map: internal states → user-facing labels.
        # Allowed public states: Online, No Heartbeat, Dead, Launching,
        #   Clear Cache, Preparing, Failed.
        # Internal/noisy states (Docking, Layout, Waiting, Checking etc.)
        # never reach the live supervisor dashboard.
        _STATE_DISPLAY_MAP: dict[str, str] = {
            # Live watchdog states — keep as-is.
            "No Heartbeat":     "No Heartbeat",
            "Online":           "Online",
            "In-Lobby":         "In-Lobby",
            "Join Failed":      "Join Failed",
            "Wrong Game / Wrong Server": "Wrong Game / Wrong Server",
            "Dead":             "Dead",
            "Relaunching":       "Relaunching",
            "Launching":         "Launching",
            # Transient post-launch / startup → Launching
            "Preparing":        "Launching",
            "Unknown":          "Launching",
            # Alive + in-game states → Online
            "Launched":         "Online",
            "In Server":        "Online",
            "Background":       "Online",
            "Warning":          "Online",
            # App open but not in game -> No Heartbeat.
            "Lobby":            "No Heartbeat",
            # Recovery / disconnect states
            "Reconnecting":     "No Heartbeat",
            "Disconnected":     "Dead",
            "Offline":          "Dead",
        }

        def _fmt_runtime(secs: float) -> str:
            """Format elapsed seconds as compact d/h/m/s string."""
            s = int(secs)
            if s <= 0:
                return "0s"
            d, s = divmod(s, 86400)
            h, s = divmod(s, 3600)
            m, s = divmod(s, 60)
            if d:
                return f"{d}d {h}h {m}m {s}s"
            if h:
                return f"{h}h {m}m {s}s"
            if m:
                return f"{m}m {s}s"
            return f"{s}s"

        def _live_dashboard() -> None:
            """Clear screen and redraw banner + table with live status values.

            Uses clear_scrollback=True so the prep-phase banner/table cannot
            bleed through at the Start→supervisor transition (dirty-UI fix).
            Checking Package X/Y text is removed from public UI (probe-only).
            Runtime column shows elapsed time since package first became Online.
            Usage column shows per-package RAM consumption.
            """
            import time as _ts_time
            _now_ts = _ts_time.time()

            def _get_runtime(pkg: str) -> str:
                raw_state = _live_map.get(pkg, "Unknown")
                disp = _STATE_DISPLAY_MAP.get(raw_state, raw_state)
                if disp != "Online":
                    return ""
                start_ts = getattr(_supervisor, "_online_start_ts", {}).get(pkg, 0.0)
                if not start_ts:
                    return ""
                return _fmt_runtime(max(0.0, _now_ts - start_ts))

            def _get_usage(pkg: str) -> str:
                """Return per-package RAM usage string (e.g. '256MB') or '0MB'."""
                raw_state = _live_map.get(pkg, "Unknown")
                disp = _STATE_DISPLAY_MAP.get(raw_state, raw_state)
                if disp in ("Dead", "Preparing", "Clear Cache"):
                    return "0MB"
                try:
                    result = android.get_package_ram_usage(pkg)
                    return str(result.get("usage_mb", "0MB"))
                except Exception:  # noqa: BLE001
                    return "0MB"

            lines = [banner_text(use_color=use_color), ""]
            ram_label = _get_ram_label()
            if ram_label:
                for _ram_line in ram_label.split("\n"):
                    lines.append(f"  {_ram_line}")
                lines.append("")
            live_rows = [
                (i + 1, e["package"], _account_username_for_table(e),
                 _STATE_DISPLAY_MAP.get(
                     _live_map.get(e["package"], "Unknown"),
                     _live_map.get(e["package"], "Unknown"),
                 ),
                 _get_runtime(e["package"]),
                 _get_usage(e["package"]))
                for i, e in enumerate(entries)
            ]
            lines.append(build_start_table(live_rows, use_color=use_color))
            _clear_terminal(clear_scrollback=False)
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()

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
                watchdog_alive="true",
            )
        except Exception:  # noqa: BLE001
            pass

        # Use 3-second display interval (like Kaeru's blinking real-time table).
        _allowed_stop_sources = {"sigint", "sigterm", "ctrl_c", "user_exit", "fatal_error"}
        _unexpected_restart_count = 0
        while True:
            try:
                _supervisor.run_forever(
                    render_callback=_live_dashboard,
                    display_interval=3.0,
                )
            except KeyboardInterrupt:
                _shutdown_reason = "ctrl_c"
                _log_stop_request("ctrl_c", allowed=True)
                _supervisor.stop_source = "ctrl_c"
                _supervisor.stop("ctrl_c")
            except Exception as exc:  # noqa: BLE001
                _start_log.debug("Supervisor terminated with error: %s", exc)
                _shutdown_reason = "fatal_error"
                _log_stop_request("fatal_error", allowed=True, stack=str(exc)[:1000])
                _supervisor.stop_source = "fatal_error"
                _supervisor.stop("fatal_error")

            _stop_source = str(getattr(_supervisor, "stop_source", "") or "").strip()
            if _stop_source in _allowed_stop_sources:
                _shutdown_reason = _stop_source
                break

            _unexpected_restart_count += 1
            try:
                from .logger import configure_logging, log_event
                log_event(
                    configure_logging(),
                    "info",
                    "[DENG_REJOIN_UNEXPECTED_EXIT_GUARD]",
                    reason=_stop_source or "watchdog_returned_without_stop_source",
                    prevented="true",
                    restart_count=_unexpected_restart_count,
                )
            except Exception:  # noqa: BLE001
                pass
            _supervisor.stop_event.clear()
            _supervisor.stop_source = ""
            _transition_lifecycle("MONITORING", "unexpected_watchdog_return_prevented")
            import time as _guard_time
            _guard_time.sleep(0.5)

        _transition_lifecycle("STOPPING", _shutdown_reason)
        # Best-effort clean exit: clear screen so terminal is not littered.
        try:
            _clear_terminal()
        except Exception:  # noqa: BLE001
            pass
        _release_start_lock(_shutdown_reason)
        _transition_lifecycle("STOPPED", _shutdown_reason)
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
    from .logger import configure_logging, log_event

    include_diag = bool(getattr(args, "diag", False))
    eta = "~30s" if include_diag else "~10s"
    sys.stdout.write(f"Collecting device evidence... ({eta})\n")
    sys.stdout.flush()
    started = _p.time.monotonic()
    first_run = True
    try:
        _p.PROBE_DIR.mkdir(parents=True, exist_ok=True)
        first_run = not any(_p.PROBE_DIR.glob("probe-*.json"))
    except Exception:  # noqa: BLE001
        pass
    try:
        data = _p.collect_probe(include_diag_startup=include_diag)
    except Exception as exc:  # noqa: BLE001 — should be impossible, but be safe.
        sys.stdout.write(f"probe failed: {exc}\n")
        return 1
    elapsed = _p.time.monotonic() - started
    path = _p.save_probe(data)
    size = path.stat().st_size
    sys.stdout.write(
        f"probe saved: {path} ({size / 1024:.1f} KB, "
        f"{len(data.get('errors') or [])} step errors, {elapsed:.1f}s)\n"
    )

    if getattr(args, "upload", False):
        sys.stdout.write("uploading...\n")
        sys.stdout.flush()
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
            sys.stdout.write(f"probe_id: {info}\n")
            sys.stdout.write("share this id in chat.\n")
        else:
            sys.stdout.write(f"upload failed: {info}\n")
            sys.stdout.write(f"probe file: {path}\n")
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
        sys.stdout.write("to share, either paste the JSON file in chat, or run:\n")
        sys.stdout.write("  deng-rejoin probe --upload\n")
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
    """Hidden: walk every step of the menu-startup chain, printing markers.

    Each marker is flushed *before* the step runs.  If any sub-routine
    segfaults (real device incident: probe ``p-b30c47d37f``), the parent
    captures the last successful ``STEP:<name>`` line and the failing one
    is the next call.  Errors are caught and printed as ``ERROR:<step>``
    so a Python exception doesn't masquerade as a crash.

    Designed to be invoked by :func:`agent.probe._capture_diag_startup`
    via ``subprocess.run`` so a SIGSEGV in any module is *isolated to
    this child* — the probe parent survives and uploads the partial
    output as evidence.
    """
    def _step(name: str) -> None:
        sys.stdout.write(f"STEP:{name}\n")
        sys.stdout.flush()

    def _ok(name: str, detail: str = "") -> None:
        suffix = f" {detail}" if detail else ""
        sys.stdout.write(f"OK:{name}{suffix}\n")
        sys.stdout.flush()

    def _err(name: str, exc: BaseException) -> None:
        # Truncated single line.  Real trace goes to the file logger
        # via the global except in main(); this is just a marker.
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


def cmd_auto_execute(args: argparse.Namespace) -> int:
    print_banner(use_color=not args.no_color)
    cfg = _load_config_for_menu()
    if _CONFIG_RECOVERED_DEFAULTS:
        termux_ui.print_warning("Config file was missing or corrupt; recreated safe defaults")
    if not _is_interactive():
        _print_config_summary(cfg)
        return 0
    _config_menu_auto_execute(cfg)
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
        if use_color:
            print()
            print(termux_ui.warning_line(crash_notice.split("\n", 1)[0]))
            print()
        else:
            print(f"\n⚠  {crash_notice}\n")

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

    cfg = _ensure_install_id_saved(cfg)
    mode = str(lic.get("mode") or "remote").strip().lower()
    if mode == "local":
        ok = _ensure_local_license_menu_loop(cfg, args, use_color)
    else:
        ok = _ensure_remote_license_menu_loop(cfg, args, use_color)

    if not ok:
        return 1

    if _license_manual_verification_success:
        termux_ui.print_license_success(pause_seconds=0.8)
        _license_manual_verification_success = False
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
    parser.add_argument("--auto-execute", dest="auto_execute", action="store_true")
    parser.add_argument("--license", action="store_true", help="enter or update your license key")
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
    # ``--diag`` opts INTO the heavy child-subprocess diag step (45 s
    # timeout) inside ``deng-rejoin probe``.  Off by default — without
    # it the probe completes in ~10 s and reliably fits the 4 MB upload
    # cap even after long test sessions.
    parser.add_argument("--diag", dest="diag", action="store_true",
                        help=argparse.SUPPRESS)

    # Pre-process argv so hidden positional subcommands don't trip choices validation.
    import sys as _sys
    if argv is None:
        argv = _sys.argv[1:]
    argv = list(argv)

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

    # Map positional sub-subcommands for `doctor`: "doctor layout", "doctor root-state".
    ns.doctor_install = False
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
        # Any other doctor sub is just dropped silently — no traceback.

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
        "auto-execute": ns.auto_execute,
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
    if getattr(ns, "discover_layout_keys", False):
        ns.resolved_command = "discover-layout-keys"
    elif getattr(ns, "support_bundle", False):
        ns.resolved_command = "support-bundle"
    elif getattr(ns, "probe", False):
        ns.resolved_command = "probe"
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
    safe_io.setup_faulthandler()
    # Silence internal namespace loggers so warnings/errors never leak to terminal.
    try:
        from .logger import silence_public_loggers
        silence_public_loggers()
    except Exception:  # noqa: BLE001
        pass
    try:
        args = parse_args(argv)
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
    except Exception:  # noqa: BLE001
        import logging as _logging
        _logging.getLogger("deng.rejoin.cli").debug("Unhandled CLI error", exc_info=True)
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
        "auto-execute": cmd_auto_execute,
        "license": cmd_license,
        "new-user-help": cmd_new_user_help,
        "enable-boot": cmd_enable_boot,
        "update": cmd_update,
        "support-bundle": cmd_support_bundle,
        "discover-layout-keys": cmd_discover_layout_keys,
        "doctor-install": cmd_doctor_install,
        "probe": cmd_probe,
        "diag-startup": cmd_diag_startup,
    }


if __name__ == "__main__":
    raise SystemExit(main())
