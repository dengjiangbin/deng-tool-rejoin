"""Comprehensive device evidence collector for live debugging.

The agent has been re-implemented many times based on what we *think* the
phone returns from ``dumpsys`` / ``settings`` / ``cmd activity``.  When the
real phone disagrees with our parser the whole tool silently degrades.

``deng-rejoin probe`` (hidden command) captures everything we would need to
write a real fix:

* runtime build proof (version, install root, modules)
* device info (Android release, SDK, model, kernel, screen, density)
* freeform / resizable / DPI / window settings (global + secure + system)
* available ``cmd activity`` / ``am`` / ``cmd window`` verbs
* per-package: process detection (pidof + pgrep + /proc cmdline),
  dumpsys window / activities / recents / SurfaceFlinger, shared_prefs XML,
  Roblox presence-API result
* recent agent logs and the latest Start-time self-diagnostics

Everything is captured into a single JSON file.  Secrets are masked
(cookies, tokens, license keys, Discord webhooks, HMAC signing keys, GitHub
PATs).  Private server URLs are kept because we need them to reason about
join state — the user explicitly opted in.

The module never raises: every ``run_command`` call is guarded and any
failure is recorded under the ``errors`` list with a short reason.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import android
from .android import CommandResult, run_command, run_root_command
from .constants import CONFIG_PATH, LOG_PATH

PROBE_VERSION = 1
DATA_DIR = Path(os.path.expanduser("~/.deng-tool/rejoin/data"))
PROBE_DIR = DATA_DIR / "probes"

# ─── Sanitization ──────────────────────────────────────────────────────────────

# Heuristic secret patterns — applied to every captured string field.  We
# err on the side of dropping suspicious content; the user can re-run with a
# tighter scope if needed.
# Order matters — specific identifiers run BEFORE broad key-value matchers
# so each secret is labelled with the most informative kind.  The broad
# ``env_secret_assign`` pattern uses a negative lookahead so it skips
# fields that already contain a ``<masked:...>`` placeholder.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("hmac_signing", re.compile(r"(?i)LICENSE_KEY_EXPORT_SECRET\s*=\s*\S+")),
    ("github_pat", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("roblosecurity", re.compile(r"(?i)\.?ROBLOSECURITY[^;\s]*")),
    ("discord_webhook", re.compile(
        r"https?://(?:discord(?:app)?\.com|canary\.discord\.com)/api/webhooks/\S+"
    )),
    ("bearer", re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+")),
    ("license_key", re.compile(r"(?i)\blic_[A-Za-z0-9_-]{8,}\b")),
    ("rk_key", re.compile(r"\brk_[A-Za-z0-9_-]{8,}\b")),
    ("env_secret_assign", re.compile(
        r"(?i)\b(?:password|secret|api[_-]?key|webhook[_-]?url)\s*[=:]\s*(?!<masked:)\S+"
    )),
)


def mask(text: str | None) -> str:
    """Return ``text`` with every detected secret replaced by ``<masked:KIND>``.

    Never raises on weird input — converts to str first.  Empty input is
    returned unchanged.
    """
    if text is None:
        return ""
    s = str(text)
    if not s:
        return s
    for kind, rx in _SECRET_PATTERNS:
        s = rx.sub(f"<masked:{kind}>", s)
    return s


def _short_id() -> str:
    return uuid.uuid4().hex[:10]


# ─── Helper for guarded calls ─────────────────────────────────────────────────


def _safe(label: str, fn, errors: list[dict[str, str]], default: Any = None) -> Any:
    """Run ``fn`` and append a structured error on any exception.

    Returns ``default`` on failure so the probe JSON stays well-shaped.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — probe never raises out.
        errors.append({"step": label, "error": str(exc)[:200]})
        return default


def _run(label: str, args: list[str], errors: list[dict[str, str]], *, root: bool = False, timeout: int = 8) -> CommandResult:
    """Run a command with timeout; record failure under ``errors`` if it dies."""
    try:
        if root:
            res = run_root_command(args, timeout=timeout)
        else:
            res = run_command(args, timeout=timeout)
        if not res.ok and not res.stdout:
            # Useful failures still record stderr/timeout; we don't fail loud.
            errors.append({"step": f"{label}: rc={res.returncode}", "error": res.stderr[:200]})
        return res
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": label, "error": str(exc)[:200]})
        return CommandResult(tuple(args), 1, "", str(exc)[:200], False)


# ─── Capture sections ─────────────────────────────────────────────────────────


def _capture_build_info(errors: list[dict[str, str]]) -> dict[str, Any]:
    from . import build_info as _bi
    return _safe("build_info.collect", _bi.collect_version_info, errors, default={}) or {}


def _capture_device(errors: list[dict[str, str]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    def _prop(name: str) -> str:
        return _run(f"getprop {name}", ["getprop", name], errors, timeout=4).stdout
    for key, prop in (
        ("android_release", "ro.build.version.release"),
        ("android_sdk", "ro.build.version.sdk"),
        ("model", "ro.product.model"),
        ("brand", "ro.product.brand"),
        ("manufacturer", "ro.product.manufacturer"),
        ("device", "ro.product.device"),
        ("fingerprint", "ro.build.fingerprint"),
        ("oem_freeform", "persist.wm.enable_remote_keyguard_animation"),
    ):
        out[key] = _prop(prop)
    out["hostname"] = _safe("hostname", socket.gethostname, errors, default="")
    out["is_termux"] = bool(_safe("is_termux", android.is_termux, errors, default=False))
    out["root"] = _safe(
        "detect_root",
        lambda: {"available": android.detect_root().available, "tool": android.detect_root().tool},
        errors,
        default={"available": False, "tool": None},
    )
    return out


def _capture_screen(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Capture screen size / density / display dump.

    Some Samsung builds reject ``dumpsys display`` from unprivileged
    callers; we route through :func:`android.run_android_command` so the
    helper transparently retries via ``su`` on permission denial.
    """
    def _grab(args: list[str], cap: int = 4000) -> str:
        try:
            res = android.run_android_command(args, timeout=8)
        except Exception as exc:  # noqa: BLE001
            errors.append({"step": " ".join(args), "error": str(exc)[:200]})
            return ""
        if not res.ok and not res.stdout:
            errors.append({"step": " ".join(args) + f": rc={res.returncode}", "error": res.stderr[:200]})
        return (res.stdout or "")[:cap]

    return {
        "wm_size": _grab(["wm", "size"], 200),
        "wm_density": _grab(["wm", "density"], 200),
        "dumpsys_display": _grab(["dumpsys", "display"], 8000),
    }


def _filter_settings(text: str) -> dict[str, str]:
    """Pick out keys we care about from ``settings list <ns>`` output."""
    keys_we_want = (
        "freeform",
        "force_resizable",
        "enable_freeform",
        "development_settings",
        "auto_dpi",
        "auto-dpi",
        "auto_landscape",
        "force_landscape",
        "display_size_forced",
        "rotation",
        "window_size",
        "window_position",
        "set_window",
        "kaeru",
        "app_cloner",
        "multi_window",
        "orientation",
    )
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        kl = k.strip().lower()
        if any(w in kl for w in keys_we_want):
            out[k.strip()] = v.strip()[:200]
    return out


def _capture_settings(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Capture ``settings list global/secure/system``.

    On many Android builds (confirmed: Samsung One UI Android 13 on the
    cloud phone) the unprivileged caller gets
    ``Security exception: Permission Denial: getCurrentUser() ...
    INTERACT_ACROSS_USERS``.  We retry through ``su`` automatically via
    :func:`android.run_android_command` so the probe actually returns
    settings on real hardware.
    """
    out: dict[str, Any] = {}
    for ns in ("global", "secure", "system"):
        try:
            res = android.run_android_command(["settings", "list", ns], timeout=10)
        except Exception as exc:  # noqa: BLE001
            errors.append({"step": f"settings list {ns}", "error": str(exc)[:200]})
            out[f"{ns}_filtered"] = {}
            out[f"{ns}_raw_len"] = 0
            continue
        if not res.ok and not res.stdout:
            errors.append({"step": f"settings list {ns}: rc={res.returncode}", "error": res.stderr[:200]})
        out[f"{ns}_filtered"] = _filter_settings(res.stdout)
        out[f"{ns}_raw_len"] = len(res.stdout)
    return out


def _capture_command_help(errors: list[dict[str, str]]) -> dict[str, str]:
    """Capture which verbs the host actually supports for resize / launch bounds.

    We bump the captured size to 12 KB so the full activity help (which
    can mention ``resize-task``, ``--activity-options``, ``--activity-bounds``,
    ``--windowingMode``) is included verbatim in the probe.  Anything not
    in the help text is most likely not supported on this build.
    """
    helps: dict[str, str] = {}
    for label, args in (
        ("cmd_activity_help", ["cmd", "activity", "help"]),
        ("am_help", ["am", "help"]),
        ("cmd_window_help", ["cmd", "window", "help"]),
        ("cmd_package_help", ["cmd", "package"]),
        # Discovery of free-form / resize-task availability.
        ("cmd_activity_compat", ["cmd", "activity", "compat"]),
        ("dumpsys_meminfo_one", ["dumpsys", "meminfo", "-a"]),
    ):
        res = _run(label, args, errors, timeout=8)
        helps[label] = (res.stdout or res.stderr)[:12000]
    return helps


# ─── Per-package capture ──────────────────────────────────────────────────────


def _capture_process(package: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    """Run every process-detection variant we care about on the same package."""
    out: dict[str, Any] = {}
    out["pidof"] = _run(f"pidof {package}", ["pidof", package], errors, timeout=4).stdout
    out["pgrep_f"] = _run(f"pgrep -f {package}", ["pgrep", "-f", package], errors, timeout=4).stdout
    # /proc cmdline scan (truncated process names) — only attempt if root
    # available.  We embed the package literally in a single-quoted ``sh -c``
    # string; package names contain only ``[A-Za-z0-9._]`` so no escaping
    # needed beyond rejecting anything weird.
    safe_pkg = package if re.fullmatch(r"[A-Za-z0-9._]+", package) else ""
    if safe_pkg and android.detect_root().available:
        cmd = (
            "for f in /proc/[0-9]*/cmdline; do "
            f"grep -lZ {safe_pkg} \"$f\" 2>/dev/null; "
            "done"
        )
        out["proc_cmdline_grep"] = _run(
            "/proc/*/cmdline scan",
            ["sh", "-c", cmd],
            errors,
            root=True,
            timeout=8,
        ).stdout[:1500]
    else:
        out["proc_cmdline_grep"] = ""
    # ps -A filtered to package.
    res = _run("ps -A", ["ps", "-A"], errors, timeout=6)
    out["ps_filtered"] = "\n".join(
        ln for ln in (res.stdout or "").splitlines() if package in ln
    )
    return out


def _capture_dumpsys_for_package(package: str, errors: list[dict[str, str]]) -> dict[str, str]:
    """Capture raw dumpsys output filtered to *package*.

    Uses :func:`android.run_android_command` so that:
      * the bare name ``dumpsys`` is auto-resolved to ``/system/bin/dumpsys``
        (Termux does not include ``/system/bin`` in ``$PATH``);
      * the call is auto-routed through ``su`` if the unprivileged call
        returns a permission-denial.

    We keep the unfiltered text length so we can prove the host did return
    something and we simply didn't match it.
    """
    out: dict[str, str] = {}
    for label, args in (
        ("window_windows", ["dumpsys", "window", "windows"]),
        ("activity_activities", ["dumpsys", "activity", "activities"]),
        ("activity_recents", ["dumpsys", "activity", "recents"]),
        ("surfaceflinger_list", ["dumpsys", "SurfaceFlinger", "--list"]),
    ):
        try:
            res = android.run_android_command(args, timeout=10)
        except Exception as exc:  # noqa: BLE001
            errors.append({"step": label, "error": str(exc)[:200]})
            out[label] = "<error>"
            out[f"{label}_total_len"] = "0"
            continue
        if not res.ok and not res.stdout:
            errors.append({"step": f"{label}: rc={res.returncode}", "error": res.stderr[:200]})
        full = res.stdout or ""
        lines = full.splitlines()
        matches: list[str] = []
        for i, ln in enumerate(lines):
            if package in ln:
                lo = max(0, i - 2)
                hi = min(len(lines), i + 6)
                matches.extend(lines[lo:hi])
                matches.append("---")
        out[label] = "\n".join(matches[:400])[:8000] or "<no package match>"
        out[f"{label}_total_len"] = str(len(full))
    return out


def _capture_shared_prefs(package: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    """Read every shared_prefs XML for the package (root required).

    Returns ``{filename: content}`` where each content is masked.  Without
    root we can only enumerate the directory.  We skip very large files
    (``cached_app_settings_prefs.xml`` on Roblox is 1.5 MB and contains no
    layout data — only experiment flags) so the probe stays under the
    upload size cap.
    """
    out: dict[str, Any] = {"available": False, "files": {}, "listing": ""}
    root = android.detect_root()
    if not root.available:
        out["error"] = "root unavailable"
        return out
    prefs_path = f"/data/data/{package}/shared_prefs"
    listing = _run(
        f"ls {prefs_path}",
        ["ls", "-la", prefs_path],
        errors,
        root=True,
        timeout=6,
    )
    out["listing"] = listing.stdout[:3000]
    if not listing.ok:
        return out
    out["available"] = True
    # Parse the listing.  Toybox ``ls -la`` produces 8 whitespace-separated
    # columns (mode, links, owner, group, size, date, time, name); GNU ls
    # may produce 9.  Match on the filename column (last) and the size
    # column (second-to-last numeric) instead of guessing column counts.
    PREFERRED_NAMES = (
        "pkg_preferences.xml",
        f"{package}_preferences.xml",
        "prefs.xml",
        "cloner_settings.xml",
        "settings.xml",
    )
    SKIP_LARGE = ("cached_app_settings_prefs.xml", "cached_flag_prefs.xml")
    MAX_FILE_BYTES = 64 * 1024  # 64 KB per file is plenty for prefs XML
    for line in listing.stdout.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        name = parts[-1]
        if not name.endswith(".xml"):
            continue
        if name in SKIP_LARGE:
            out["files"][name] = "<skipped: large cached prefs>"
            continue
        # Try to parse the size column; if it's enormous, skip the cat.
        size = 0
        for col in parts[:-1]:
            if col.isdigit():
                size = max(size, int(col))
        if size > MAX_FILE_BYTES and name not in PREFERRED_NAMES:
            out["files"][name] = f"<skipped: {size} bytes>"
            continue
        cat = _run(
            f"cat {name}",
            ["cat", f"{prefs_path}/{name}"],
            errors,
            root=True,
            timeout=6,
        )
        out["files"][name] = mask(cat.stdout)[:8000]
    return out


def _capture_presence(entry: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    from . import roblox_presence as _rp

    uid = int(entry.get("roblox_user_id") or 0)
    username = str(entry.get("account_username") or "").strip()
    out: dict[str, Any] = {"configured": False, "user_id": uid, "username": username}
    if uid <= 0 and not username:
        return out
    out["configured"] = True
    try:
        if uid <= 0 and username:
            uid = int(_safe("lookup_user_id", lambda: _rp.lookup_user_id(username), errors, default=0) or 0)
            out["resolved_user_id"] = uid
        if uid > 0:
            p = _safe("fetch_presence_one", lambda: _rp.fetch_presence_one(uid), errors, default=None)
            if p is not None:
                ptype = getattr(p, "presence_type", None)
                out["presence"] = {
                    "presence_type": int(ptype) if ptype is not None else None,
                    "presence_type_name": getattr(ptype, "name", None),
                    "last_location": mask(getattr(p, "last_location", "") or ""),
                    "place_id": getattr(p, "place_id", None),
                    "root_place_id": getattr(p, "root_place_id", None),
                    "last_online_iso": getattr(p, "last_online_iso", ""),
                    "is_in_game": getattr(p, "is_in_game", False),
                    "is_lobby": getattr(p, "is_lobby", False),
                    "is_offline": getattr(p, "is_offline", False),
                }
            else:
                out["presence_error"] = "fetch_presence_one returned None"
    except Exception as exc:  # noqa: BLE001
        out["presence_error"] = str(exc)[:200]
    return out


# ─── Config + logs ────────────────────────────────────────────────────────────


def _capture_config(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Load config.json and strip private URLs only if requested.  The user
    explicitly asked for everything → we keep URLs, mask only secrets."""
    if not CONFIG_PATH.is_file():
        return {"error": "config.json not present"}
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return {"error": str(exc)[:200]}
    # Pass through mask() so any cookies / webhook URLs / license keys
    # accidentally pasted into config are stripped.
    safe = mask(raw)
    try:
        return json.loads(safe)
    except json.JSONDecodeError:
        # If masking broke JSON (e.g., webhook URL replaced inside a string
        # making the whole string still valid but weird), still return text.
        return {"raw_text": safe[:8000]}


def _capture_log_tail(errors: list[dict[str, str]], lines: int = 200) -> str:
    """Return last *lines* lines of the agent log, masked."""
    if not LOG_PATH.is_file():
        return ""
    try:
        # Read end of file efficiently.
        with LOG_PATH.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            chunk = min(size, 256 * 1024)
            f.seek(size - chunk)
            tail = f.read().decode("utf-8", errors="replace")
        return mask("\n".join(tail.splitlines()[-lines:]))
    except OSError as exc:
        errors.append({"step": "log_tail", "error": str(exc)[:200]})
        return ""


def _capture_all_logs(errors: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    """Capture every file in ``~/.deng-tool/rejoin/logs/`` (crash.log, start-debug, …).

    Each entry: ``{size, mtime_iso, tail}`` (last 64 KiB, masked).

    crash.log is the C-level traceback written by :mod:`faulthandler` when a
    true segfault hits.  We always want to surface it when present so the
    diagnoser knows the exact Python frame the process died in.
    """
    out: dict[str, dict[str, Any]] = {}
    log_dir = LOG_PATH.parent
    if not log_dir.is_dir():
        return out
    try:
        files = sorted(log_dir.iterdir())
    except OSError as exc:
        errors.append({"step": "logs_dir_list", "error": str(exc)[:200]})
        return out
    for path in files:
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            size = stat.st_size
            with path.open("rb") as f:
                if size > 64 * 1024:
                    f.seek(size - 64 * 1024)
                    tail_bytes = f.read()
                else:
                    tail_bytes = f.read()
            tail = tail_bytes.decode("utf-8", errors="replace")
            out[path.name] = {
                "size": size,
                "mtime_iso": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "tail": mask(tail),
            }
        except OSError as exc:
            errors.append({"step": f"log_read:{path.name}", "error": str(exc)[:200]})
    return out


def _capture_installed_build(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Read ``~/.deng-tool/rejoin/.installed-build.json`` if present.

    Written by ``install.sh`` after a successful install; tells us *exactly*
    which artifact bytes the runtime is supposed to be running.
    """
    p = Path(os.path.expanduser("~/.deng-tool/rejoin/.installed-build.json"))
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append({"step": "installed_build", "error": str(exc)[:200]})
        return {}


def _capture_wrapper_script(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Capture the ``deng-rejoin`` shell wrapper that PATH resolves to.

    Real-device incident (probe ``p-b30c47d37f``): ``deng-rejoin version``
    succeeds but bare ``deng-rejoin`` segfaults.  If the wrapper points at a
    stale install path, this surfaces it immediately.
    """
    try:
        wrapper = shutil.which("deng-rejoin")
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "wrapper_which", "error": str(exc)[:200]})
        return {}
    if not wrapper:
        return {"path": None}
    out: dict[str, Any] = {"path": wrapper}
    try:
        out["resolved"] = os.path.realpath(wrapper)
    except OSError:
        pass
    try:
        with open(wrapper, "rb") as f:
            data = f.read(8 * 1024).decode("utf-8", errors="replace")
        out["head"] = mask(data)
    except OSError as exc:
        errors.append({"step": "wrapper_read", "error": str(exc)[:200]})
    return out


def _capture_diag_startup(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Exec ``deng-rejoin --diag-startup`` as a child subprocess.

    The child walks the ``cmd_menu`` steps one at a time and prints
    ``STEP:<name>`` before invoking each.  If the child segfaults, we get
    ``returncode == -11`` (SIGSEGV) without our probe process dying, and
    the last STEP line tells us *exactly* which sub-routine crashed.

    This is the live evidence loop: probe → diag-startup → captured output
    → real-device crash location.  No more guessing from logs.
    """
    out: dict[str, Any] = {}
    wrapper = shutil.which("deng-rejoin")
    if not wrapper:
        errors.append({"step": "diag_startup", "error": "deng-rejoin not on PATH"})
        return out
    try:
        proc = subprocess.run(
            [wrapper, "--diag-startup"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=45,
            check=False,
            text=True,
        )
        out["returncode"] = proc.returncode
        # SIGSEGV is -11 on Linux/Android; other crash signals also negative.
        out["sigsegv"] = (proc.returncode == -11)
        out["crashed"] = proc.returncode < 0
        out["stdout"] = mask((proc.stdout or "")[-16384:])
        out["stderr"] = mask((proc.stderr or "")[-16384:])
        # The last "STEP:<name>" line printed by the child tells us where
        # it died.  Parse it for convenience.
        last_step = ""
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("STEP:"):
                last_step = line
        out["last_step"] = last_step
    except subprocess.TimeoutExpired as exc:
        errors.append({"step": "diag_startup", "error": "timeout"})
        out["returncode"] = None
        out["timeout"] = True
        out["stdout"] = mask((exc.stdout or "")[-8192:] if isinstance(exc.stdout, str) else "")
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "diag_startup", "error": str(exc)[:200]})
    return out


# ─── Third-party tool discovery (Kaeru / multi-clone / window manager) ──────


# Package-name fragments that identify the tools we want to reverse-engineer.
# We keep the list curated so the probe doesn't accidentally exfiltrate every
# random app's private prefs — only tools that overlap our problem space.
_THIRD_PARTY_TOOL_HINTS = (
    # Kaeru — the user's reference benchmark for "this just works".
    "kaeru",
    "shiki",  # the dev's other handle is "kaerushiki" on some channels
    # App / multi-clone managers
    "applauncher",
    "appcloner",
    "appclone",
    "multispace",
    "parallel",
    "multiwindow",
    "dualspace",
    "clone",
    "isolate",
    # Free-form window managers / sidecars
    "taskbar",
    "sentry",
    "freeform",
    "floating",
    "windowmanager",
    "sidestore",
    # Roblox-specific launchers and helpers
    "rolauncher",
    "robloxstudio",
    "rolimons",
    "joiner",
    "rejoin",
    "autojoin",
)


def _capture_installed_packages(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Return the full ``pm list packages -3`` output and a curated subset.

    The curated subset includes:
      * every package whose id contains one of :data:`_THIRD_PARTY_TOOL_HINTS`,
      * every installed Moons / App Cloner clone (so we have a record of which
        clones the user has installed even if not in the cfg).
    """
    out: dict[str, Any] = {}
    try:
        res = android.run_android_command(
            ["pm", "list", "packages", "-3"], timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "pm list packages -3", "error": str(exc)[:200]})
        out["pm_list_third_party_raw"] = ""
        out["third_party_count"] = 0
        out["third_party_tools"] = []
        out["clone_packages"] = []
        return out

    text = res.stdout or ""
    out["pm_list_third_party_raw"] = text[:60000]  # cap to 60 KB

    pkgs = [
        ln.split(":", 1)[1].strip()
        for ln in text.splitlines()
        if ln.startswith("package:")
    ]
    out["third_party_count"] = len(pkgs)
    out["third_party_tools"] = sorted({
        p for p in pkgs
        if any(h in p.lower() for h in _THIRD_PARTY_TOOL_HINTS)
    })
    out["clone_packages"] = sorted({
        p for p in pkgs
        if (
            "moons" in p.lower()
            or "clone" in p.lower()
            or "applauncher" in p.lower()
            or "appcloner" in p.lower()
        )
    })
    return out


def _capture_kaeru_evidence(
    third_party: dict[str, Any],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    """For every third-party tool discovered, capture its install + prefs.

    This is the heart of the "observe what the working tool does" loop:
    every file under ``shared_prefs`` is read and masked, plus the
    package's full ``pm dump`` (declared permissions, intent filters,
    activity windowing flags).
    """
    out: dict[str, Any] = {}
    targets: list[str] = []
    for src in ("third_party_tools", "clone_packages"):
        for pkg in third_party.get(src, []) or []:
            if pkg not in targets:
                targets.append(pkg)

    out["targets"] = list(targets)
    out["pm_dump"] = {}
    out["shared_prefs"] = {}
    out["files_dir"] = {}

    for pkg in targets:
        # ── pm dump <pkg>: declared permissions, intent filters, launch config
        try:
            res = android.run_android_command(
                ["pm", "dump", pkg], timeout=12,
            )
            out["pm_dump"][pkg] = mask((res.stdout or "")[:24000])
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "step": f"pm dump {pkg}",
                "error": str(exc)[:200],
            })
            out["pm_dump"][pkg] = "<error>"

        # ── /data/data/<pkg>/shared_prefs/*  (full XML, masked)
        try:
            out["shared_prefs"][pkg] = _capture_shared_prefs(pkg, errors)
        except Exception as exc:  # noqa: BLE001
            errors.append({
                "step": f"shared_prefs {pkg}",
                "error": str(exc)[:200],
            })
            out["shared_prefs"][pkg] = {}

        # ── /data/data/<pkg>/files/   (root-only; surface filenames + small
        # text contents so we can spot per-clone window-bounds JSON / cfg).
        try:
            ls_res = android.run_root_command(
                ["sh", "-c", f"ls -la /data/data/{pkg}/files/ 2>/dev/null | head -200"],
                timeout=6,
            )
            out["files_dir"][pkg] = (ls_res.stdout or "")[:8000]
        except Exception:  # noqa: BLE001
            out["files_dir"][pkg] = ""

    return out


def _capture_appops(
    targets: list[str],
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    """Capture ``cmd appops get <pkg>`` — which Android permissions each
    third-party tool actually holds at runtime.  Useful to know whether
    Kaeru required SYSTEM_ALERT_WINDOW / MANAGE_ACTIVITY_STACKS.
    """
    out: dict[str, str] = {}
    for pkg in targets:
        try:
            res = android.run_android_command(
                ["cmd", "appops", "get", pkg], timeout=8,
            )
            out[pkg] = (res.stdout or "")[:8000]
        except Exception as exc:  # noqa: BLE001
            errors.append({"step": f"appops {pkg}", "error": str(exc)[:200]})
            out[pkg] = "<error>"
    return out


def _capture_getprop(errors: list[dict[str, str]]) -> dict[str, str]:
    """Capture ``getprop`` filtered to window/display/freeform-relevant keys.

    Looking for properties like ``persist.wm.disable_explicit_size_freeze``,
    ``ro.config.low_ram``, ``persist.sys.dalvik.vm.lib.2``, OEM flags that
    enable or disable freeform.  Full output is too large; we filter.
    """
    out: dict[str, str] = {"raw_filtered": "", "all_count": 0}
    try:
        res = android.run_android_command(["getprop"], timeout=10)
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "getprop", "error": str(exc)[:200]})
        return out
    raw = res.stdout or ""
    lines = raw.splitlines()
    out["all_count"] = len(lines)
    keep_re = re.compile(
        r"(?i)(window|freeform|resiz|multi|dpi|density|task|surface|"
        r"wm\.|persist\.sys|ro\.config|ro\.build|knox|samsung\.|game|"
        r"split|launcher|home|secur|low_ram|vm\.lib)",
    )
    filtered = [ln for ln in lines if keep_re.search(ln)]
    out["raw_filtered"] = "\n".join(filtered)[:32000]
    return out


def _capture_dumpsys_global(errors: list[dict[str, str]]) -> dict[str, str]:
    """Capture the FULL ``dumpsys window windows`` / activity / SurfaceFlinger
    output (not just per-package slices).

    HARD BUDGET — total ~250 KB across all sections (was ~700 KB which
    blew past the 4 MB compressed upload cap once we added third-party
    captures and the user hammered the device with tests).  We keep the
    highest-signal sections (window-windows + activity-activities) and
    drop the redundant ones.  Caller can use ``--diag`` to re-enable
    SurfaceFlinger when debugging that specifically.
    """
    out: dict[str, str] = {}
    # Reduced caps + tighter timeouts.  Each step has its own timeout so
    # one slow `dumpsys SurfaceFlinger` cannot block the whole probe.
    for label, args, cap, timeout in (
        ("window_windows_full",
            ["dumpsys", "window", "windows"], 80_000, 8),
        ("activity_activities_full",
            ["dumpsys", "activity", "activities"], 80_000, 8),
        ("activity_recents_full",
            ["dumpsys", "activity", "recents"], 32_000, 6),
        ("activity_top",
            ["dumpsys", "activity", "top"], 24_000, 5),
        ("activity_starter",
            ["dumpsys", "activity", "starter"], 12_000, 5),
        # SurfaceFlinger is the largest single source (often 80+ KB) and
        # the lowest signal for window-bounds debugging — keep it tiny.
        ("surfaceflinger_full",
            ["dumpsys", "SurfaceFlinger"], 24_000, 6),
    ):
        try:
            res = android.run_android_command(args, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            errors.append({"step": label, "error": str(exc)[:200]})
            out[label] = "<error>"
            continue
        out[label] = (res.stdout or "")[:cap]
    return out


def _capture_logcat(errors: list[dict[str, str]]) -> str:
    """Last ~1000 lines of logcat (dump mode), capped at 80 KB.

    Captures every WindowManager / ActivityTaskManager event in the
    recent history — which is where we see Kaeru actually doing
    ``startActivity ... mode=freeform bounds=[l,t][r,b]``.  Masked to
    drop any URL / cookie that might leak.
    """
    try:
        res = android.run_android_command(
            ["logcat", "-d", "-t", "1000",
             "WindowManager:V", "ActivityManager:V",
             "ActivityTaskManager:V", "AppOps:V",
             "TaskLaunchModes:V", "SurfaceFlinger:I",
             "*:W"],
            timeout=10,
        )
        return mask((res.stdout or "")[:80_000])
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "logcat -d", "error": str(exc)[:200]})
        return ""


def _capture_termux_prefs(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Termux's own shared_prefs — captures any window/UI state set by
    a tool that resizes Termux (the same trick we want to learn for
    our 50%-dock).
    """
    return _capture_shared_prefs("com.termux", errors)


def _capture_last_diagnostics(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Read ``data/last_start_diagnostics.json`` if it exists."""
    p = DATA_DIR / "last_start_diagnostics.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append({"step": "last_diagnostics", "error": str(exc)[:200]})
        return {}


# ─── Public entrypoint ────────────────────────────────────────────────────────


def collect_probe(*, include_diag_startup: bool | None = None) -> dict[str, Any]:
    """Run every capture step and return the full probe dict.

    ``include_diag_startup`` (default: env ``DENG_PROBE_DIAG_STARTUP``,
    else False) controls whether the in-process child-subprocess
    diagnostic runs.  That step can hang for up to 45 s on a stressed
    device and previously caused the probe to appear stuck (which the
    user reported as "I CANT UPLOAD PROBE AFTER TESTING").  It is now
    opt-in via ``--diag`` on the CLI.

    Never raises.  Errors are captured under ``probe["errors"]``.
    """
    if include_diag_startup is None:
        include_diag_startup = (
            os.environ.get("DENG_PROBE_DIAG_STARTUP", "").strip() in {"1", "true", "yes"}
        )

    errors: list[dict[str, str]] = []
    out: dict[str, Any] = {
        "probe_version": PROBE_VERSION,
        "captured_at_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "errors": errors,
    }
    out["build"] = _capture_build_info(errors)
    out["device"] = _capture_device(errors)
    out["screen"] = _capture_screen(errors)
    out["settings"] = _capture_settings(errors)
    out["command_help"] = _capture_command_help(errors)
    out["config"] = _capture_config(errors)
    # Per-package details.
    pkgs: dict[str, Any] = {}
    cfg = out["config"]
    pkg_entries = []
    if isinstance(cfg, dict):
        pkg_entries = cfg.get("roblox_packages") or []
        if not pkg_entries and cfg.get("roblox_package"):
            pkg_entries = [{"package": cfg.get("roblox_package")}]
    for entry in pkg_entries:
        if not isinstance(entry, dict):
            continue
        pkg = str(entry.get("package") or "").strip()
        if not pkg:
            continue
        pkg_block: dict[str, Any] = {}
        pkg_block["process"] = _capture_process(pkg, errors)
        pkg_block["dumpsys"] = _capture_dumpsys_for_package(pkg, errors)
        pkg_block["shared_prefs"] = _capture_shared_prefs(pkg, errors)
        pkg_block["presence"] = _capture_presence(entry, errors)
        pkgs[pkg] = pkg_block
    out["packages"] = pkgs
    out["log_tail"] = _capture_log_tail(errors)
    out["logs"] = _capture_all_logs(errors)
    out["installed_build"] = _capture_installed_build(errors)
    out["wrapper"] = _capture_wrapper_script(errors)
    out["last_start_diagnostics"] = _capture_last_diagnostics(errors)

    # ── Third-party tool discovery: the "observe what works" loop ─────────
    # Find any other launcher / multi-clone / window-manager / Kaeru-style
    # tool the user has installed.  Capture its shared_prefs, declared
    # permissions, and granted app-ops so we can copy whatever technique
    # makes their freeform resize actually stick on this exact device.
    out["installed_packages"] = _capture_installed_packages(errors)
    out["third_party_evidence"] = _capture_kaeru_evidence(
        out["installed_packages"], errors,
    )
    out["appops"] = _capture_appops(
        out["third_party_evidence"].get("targets", []) or [],
        errors,
    )
    # Termux's own prefs — Kaeru may set window/dock state on Termux too.
    out["termux_shared_prefs"] = _capture_termux_prefs(errors)
    # System-property snapshot — OEM freeform-window flags live here.
    out["getprop"] = _capture_getprop(errors)
    # Full (non-package-filtered) dumpsys for window/activity/surface state.
    # This is what we use to see the EXACT bounds Kaeru achieved.
    out["dumpsys_global"] = _capture_dumpsys_global(errors)
    # Recent logcat — `startActivity ... mode=freeform bounds=[...]` lives
    # here so we see the actual API calls Kaeru made.
    out["logcat"] = _capture_logcat(errors)

    # Heavy in-process child diag: optional, opt-in via --diag.  This
    # step can hang up to 45 s on a stressed device.  When skipped we
    # still attach a marker so the operator knows it was deliberate.
    if include_diag_startup:
        out["diag_startup"] = _capture_diag_startup(errors)
    else:
        out["diag_startup"] = {"skipped": True,
                               "reason": "default off; use --diag to enable"}
    return out


# ── Payload trimming for upload ─────────────────────────────────────────────

# The install API rejects payloads larger than this many bytes after
# gzip compression.  Match the server's hard cap in license_api.py so we
# can give the user a clear message instead of a 413.
_UPLOAD_HARD_CAP_BYTES         = 4 * 1024 * 1024
_UPLOAD_SOFT_BUDGET_BYTES      = 3 * 1024 * 1024  # leave headroom

# Order matters — when over budget, we drop the highest-cost,
# lowest-signal fields first.  Per-package shared_prefs/dumpsys win over
# the global captures because they're scoped to the user's installation.
_PROBE_DROP_ORDER: tuple[str, ...] = (
    "logcat",                       # ~80 KB, situational
    "dumpsys_global.surfaceflinger_full",
    "dumpsys_global.activity_starter",
    "dumpsys_global.activity_top",
    "dumpsys_global.activity_recents_full",
    "third_party_evidence.pm_dump",   # ~24 KB per package
    "third_party_evidence.shared_prefs",
    "installed_packages.pm_list_third_party_raw",
    "logs",                          # rotating log tails
    "dumpsys_global.activity_activities_full",
    "dumpsys_global.window_windows_full",
)


def _drop_field(probe: dict[str, Any], dotted: str) -> bool:
    """Drop a (possibly nested) key like ``dumpsys_global.activity_top``.

    Returns True if the field existed and was removed.  Never raises.
    """
    parts = dotted.split(".")
    cur: Any = probe
    for p in parts[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return False
        cur = cur[p]
    if not isinstance(cur, dict):
        return False
    last = parts[-1]
    if last not in cur:
        return False
    # Replace with marker so the operator knows it was dropped, not absent.
    cur[last] = "<dropped: payload size budget>"
    return True


def trim_probe_for_upload(
    probe: dict[str, Any], *,
    hard_cap: int = _UPLOAD_HARD_CAP_BYTES,
    soft_budget: int = _UPLOAD_SOFT_BUDGET_BYTES,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(trimmed_probe, report)`` where the gzipped JSON size
    fits under ``hard_cap`` and is targeted at ``soft_budget``.

    Strategy: serialize → gzip → measure → if over, drop the next entry
    from :data:`_PROBE_DROP_ORDER` and repeat.  Stop as soon as we are
    under ``soft_budget`` (or we run out of drop targets).  Never raises.
    """
    import gzip as _gz

    trimmed = dict(probe)
    dropped: list[str] = []
    measurements: list[int] = []

    def _measure(p: dict[str, Any]) -> int:
        try:
            return len(_gz.compress(
                json.dumps(p, separators=(",", ":")).encode("utf-8"),
            ))
        except Exception:  # noqa: BLE001
            return hard_cap + 1   # force-trim on any error

    size = _measure(trimmed)
    measurements.append(size)
    for field in _PROBE_DROP_ORDER:
        if size <= soft_budget:
            break
        if _drop_field(trimmed, field):
            dropped.append(field)
        size = _measure(trimmed)
        measurements.append(size)

    # If we're STILL over the hard cap after dropping everything,
    # truncate every remaining string field to 4 KB.  Last-ditch.
    if size > hard_cap:
        def _truncate(d: Any) -> None:
            if isinstance(d, dict):
                for k, v in list(d.items()):
                    if isinstance(v, str) and len(v) > 4000:
                        d[k] = v[:4000] + "...<truncated>"
                    elif isinstance(v, (dict, list)):
                        _truncate(v)
            elif isinstance(d, list):
                for v in d:
                    if isinstance(v, (dict, list)):
                        _truncate(v)
        _truncate(trimmed)
        size = _measure(trimmed)
        measurements.append(size)
        dropped.append("<truncated all strings to 4 KB>")

    trimmed["_upload_trim"] = {
        "final_gzipped_bytes": size,
        "hard_cap": hard_cap,
        "soft_budget": soft_budget,
        "dropped": dropped,
        "measurement_bytes_per_pass": measurements,
    }
    report = {
        "dropped": dropped,
        "final_gzipped_bytes": size,
        "passes": len(measurements),
    }
    return trimmed, report


def save_probe(probe: dict[str, Any]) -> Path:
    """Persist *probe* to ``$DATA/probes/probe-<short>.json`` and return path."""
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = PROBE_DIR / f"probe-{ts}-{_short_id()}.json"
    path.write_text(json.dumps(probe, indent=2, sort_keys=True), encoding="utf-8")
    return path


# ─── Upload ──────────────────────────────────────────────────────────────────


def _resolve_install_api() -> str:
    api_path = Path(os.path.expanduser("~/.deng-tool/rejoin/.install_api"))
    if api_path.is_file():
        try:
            return api_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return os.environ.get("DENG_REJOIN_INSTALL_API", "").strip()


def upload_probe(probe: dict[str, Any], *, timeout: float = 30.0) -> tuple[bool, str]:
    """POST the probe JSON to the install API's dev-probe endpoint.

    Trims the payload first so it fits the 4 MB compressed cap, then
    POSTs the gzipped JSON.  Returns ``(ok, info)`` where ``info`` is a
    short probe id on success, or a short, USER-ACTIONABLE error string
    on failure.  Never raises.
    """
    import gzip
    import urllib.error
    import urllib.request

    api = _resolve_install_api()
    if not api:
        return False, "install API URL not configured (set DENG_REJOIN_INSTALL_API)"
    url = api.rstrip("/") + "/api/dev-probe/upload"

    # ── Size budget ── trim ANY oversized payload BEFORE we POST so the
    # server's 4 MB cap doesn't reject us with an opaque 413.  This is
    # what was blocking the user after running many tests back-to-back.
    trimmed, trim_report = trim_probe_for_upload(probe)
    body = json.dumps(trimmed, separators=(",", ":")).encode("utf-8")
    payload = gzip.compress(body)

    if len(payload) > _UPLOAD_HARD_CAP_BYTES:
        return False, (
            f"payload still {len(payload)/1024/1024:.1f} MB after trim — "
            f"server cap is {_UPLOAD_HARD_CAP_BYTES/1024/1024:.0f} MB. "
            f"Dropped: {trim_report.get('dropped') or []!r}"
        )

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Content-Encoding", "gzip")
    req.add_header("User-Agent", "deng-rejoin-probe/1")
    req.add_header("X-Dev-Probe-Token", "deng-rejoin-dev-probe-v1")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return False, f"non-JSON response: {data[:200]}"
        pid = str(obj.get("probe_id") or "").strip()
        if not pid:
            return False, f"no probe_id in response: {data[:200]}"
        return True, pid
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        # Pretty-print common rejection cases so the user knows what to do.
        if exc.code == 413:
            return False, (
                f"server rejected size {len(payload)} bytes (413). "
                f"Trim dropped: {trim_report.get('dropped') or []!r}. "
                f"Server body: {body_text}"
            )
        if exc.code == 429:
            return False, (
                f"rate-limited (429) — wait ~60 seconds and try again. "
                f"Server body: {body_text}"
            )
        if exc.code == 401:
            return False, f"unauthorized (401) — check DENG_DEV_PROBE_TOKEN. {body_text}"
        return False, f"HTTP {exc.code}: {body_text}"
    except urllib.error.URLError as exc:
        return False, f"network error: {exc.reason}"
    except OSError as exc:
        return False, f"OS error: {exc}"
