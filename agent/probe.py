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
* per-package: process detection (pidof + excluded ps + /proc cmdline),
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
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import android
from .android import CommandResult, run_command, run_root_command
from .constants import CONFIG_PATH, DATA_DIR, LOG_PATH, START_CRASH_STATE_PATH
from .url_utils import mask_urls_in_text

PROBE_VERSION = 1
PROBE_DIR = DATA_DIR / "probes"
UPLOAD_BUNDLE_DIR = DATA_DIR / "probe_upload_bundles"

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
    ("deng_license_key", re.compile(r"\bDENG-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}\b")),
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
    return mask_urls_in_text(s)


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
    if not args or not str(args[0] or "").strip():
        errors.append({"step": label, "error": "empty command skipped"})
        return CommandResult(tuple(str(a) for a in args), 127, "", "empty command skipped", False)
    if args[0] == "sh" and len(args) >= 2:
        if args[1] == "-c" and (len(args) < 3 or not str(args[2]).strip()):
            errors.append({"step": label, "error": "empty shell command skipped"})
            return CommandResult(tuple(args), 127, "", "empty shell command skipped", False)
        if args[1] != "-c" and not str(args[1]).strip():
            errors.append({"step": label, "error": "empty shell script path skipped"})
            return CommandResult(tuple(args), 127, "", "empty shell script path skipped", False)
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
    out["ps_excluded"] = _run(
        f"ps -ef excluded {package}",
        android.process_ps_scan_args(package),
        errors,
        root=android.detect_root().available,
        timeout=4,
    ).stdout
    # Exact /proc fallback: it excludes the scanner and relaunch helper rather
    # than treating a package substring in a script filename as app evidence.
    if android.detect_root().available:
        out["proc_cmdline_exact"] = _run(
            "/proc/*/cmdline exact scan",
            android.process_cmdline_scan_args(package),
            errors,
            root=True,
            timeout=8,
        ).stdout[:1500]
    else:
        out["proc_cmdline_exact"] = ""
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
        ("surfaceflinger_full", ["dumpsys", "SurfaceFlinger"]),
        ("input_windows", ["dumpsys", "input"]),
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


def _capture_portrait_input_readback(package: str, errors: list[dict[str, str]]) -> dict[str, Any]:
    """Capture parsed portrait task/surface/input alignment evidence."""
    try:
        from . import window_apply, window_layout
        cfg = _safe("load_config_for_portrait_readback", _load_config_safe, errors, default={}) or {}
        display = window_layout.detect_display_info()
        selected = [
            e.get("package") for e in cfg.get("roblox_packages", [])
            if isinstance(e, dict) and e.get("enabled", True) and e.get("package")
        ]
        if package not in selected:
            selected.append(package)
        screen_mode = "landscape"
        rects = window_layout.calculate_split_layout(
            selected,
            display.width,
            display.height,
            termux_log_fraction=float(cfg.get("termux_dock_fraction", 0.0) or 0.0),
            screen_mode=screen_mode,
        )
        desired = next((r for r in rects if r.package == package), None)
        if desired is None:
            return {"available": False, "reason": "desired bounds unavailable"}
        readback = window_apply.collect_portrait_layer_readback(package, desired)
        readback["available"] = True
        resolved = window_layout.resolve_layout_mode(display.width, display.height, screen_mode)
        readback["layout_mode"] = {
            "configured_screen_mode": resolved.configured_screen_mode,
            "android_reported_orientation": resolved.android_orientation,
            "final_layout_mode": resolved.final_layout_mode,
            "coordinate_space": resolved.coordinate_space,
            "reason": resolved.reason,
            "raw_display": [resolved.raw_width, resolved.raw_height],
            "normalized_display": [resolved.normalized_width, resolved.normalized_height],
        }
        slot = None
        readback["slot"] = {
            "row": (slot // 2) if slot is not None else None,
            "col": (slot % 2) if slot is not None else None,
        }
        return readback
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": f"portrait_input_readback {package}", "error": str(exc)[:200]})
        return {"available": False, "error": str(exc)[:200]}


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
    cookie = str(entry.get("roblox_cookie") or "").strip() or None
    out: dict[str, Any] = {"configured": False, "user_id": uid, "username": username}
    if uid <= 0 and not username:
        return out
    out["configured"] = True
    try:
        if uid <= 0 and username:
            uid = int(_safe("lookup_user_id", lambda: _rp.lookup_user_id(username), errors, default=0) or 0)
            out["resolved_user_id"] = uid
        if uid > 0:
            p = _safe(
                "fetch_presence_one",
                lambda: _rp.fetch_presence_one(uid, cookie=cookie),
                errors,
                default=None,
            )
            if p is not None:
                ptype = getattr(p, "presence_type", None)
                out["presence"] = {
                    "presence_type": int(ptype) if ptype is not None else None,
                    "presence_type_name": getattr(ptype, "name", None),
                    "presence_profile": _rp.map_presence_profile(p),
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


def _load_config_safe() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


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


def _capture_latest_crash_log(logs: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded, pinned traceback proof independent of bulk log tails."""
    for name in ("crash.log", "crash_faulthandler.log"):
        item = logs.get(name)
        if isinstance(item, dict):
            tail = str(item.get("tail") or "")
            return {"name": name, "tail": tail[-16_384:]}
    return {"name": "", "tail": ""}


def _capture_last_failing_command() -> dict[str, Any]:
    path = DATA_DIR / "last-command.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {"command": str(data.get("command") or ""), "at": str(data.get("at") or "")}
    except (OSError, ValueError, TypeError):
        return {"command": "", "at": ""}


def _capture_webhook_debug() -> dict[str, Any]:
    """Read the bounded, redacted scheduler/send record written at runtime."""
    trace_path = DATA_DIR / "webhook-trace.jsonl"
    trace: list[dict[str, Any]] = []
    try:
        trace = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()[-32:] if line.strip()]
    except (OSError, ValueError, TypeError):
        trace = []
    path = DATA_DIR / "webhook-debug.json"
    try:
        from .config import load_config
        from . import webhook as webhook_mod

        cfg = load_config()
    except Exception:  # noqa: BLE001
        cfg = {}
        webhook_mod = None
    latest = trace[-1] if trace else {}
    trace_merged: dict[str, Any] = {}
    for row in trace:
        if isinstance(row, dict):
            trace_merged.update(row)
    state_message_id = str(cfg.get("webhook_last_message_id") or cfg.get("webhook_message_id") or "")
    probe_fields = {
        "webhook_mode": str(cfg.get("webhook_mode") or "none"),
        "webhook_url_present_redacted": bool(cfg.get("webhook_url")),
        "discord_mention_enabled": bool(cfg.get("webhook_tag_enabled")),
        "discord_mention_user_id_masked": (
            webhook_mod._mask_discord_user_id(str(cfg.get("webhook_tag_user_id") or "").strip() or None)
            if webhook_mod is not None
            else ""
        ),
        "last_lifecycle_event": trace_merged.get("lifecycle_event") or trace_merged.get("event") or "",
        "last_lifecycle_title": trace_merged.get("lifecycle_title") or "",
        "last_lifecycle_runtime_present": trace_merged.get("lifecycle_runtime_present"),
        "last_lifecycle_runtime_value": trace_merged.get("lifecycle_runtime_value") or "",
        "monitor_started_at": trace_merged.get("monitor_started_at"),
        "package_launch_started_at": trace_merged.get("package_launch_started_at"),
        "status_monitor_runtime_started_at": trace_merged.get("status_monitor_runtime_started_at"),
        "runtime_source": trace_merged.get("runtime_source") or "",
        "runtime_value": trace_merged.get("runtime_value") or "",
        "current_state": trace_merged.get("current_state") or "",
        "state_path": str(CONFIG_PATH),
        "state_message_id_present": bool(state_message_id),
        "state_message_id_redacted": mask(state_message_id) if state_message_id else "",
        "last_send_started_at": trace_merged.get("timestamp", ""),
        "last_payload_build_ok": trace_merged.get("payload_build_result") == "success",
        "last_payload_fallback_used": trace_merged.get("payload_build_result") == "failure",
        "last_http_method": trace_merged.get("last_http_method") or trace_merged.get("http_method") or "",
        "last_http_status": trace_merged.get("last_http_status") or trace_merged.get("http_status") or "",
        "last_http_url_kind": trace_merged.get("last_http_url_kind") or "",
        "last_discord_message_id_redacted": trace_merged.get("last_discord_message_id_redacted") or "",
        "last_exception_type": trace_merged.get("last_exception_type") or "",
        "last_exception_message_redacted": trace_merged.get("last_exception_message_redacted") or "",
        "edit_bootstrap_post_started": bool(trace_merged.get("edit_bootstrap_post_started")),
        "edit_bootstrap_message_id_saved": bool(trace_merged.get("edit_bootstrap_message_id_saved")),
        "edit_patch_started": bool(trace_merged.get("edit_patch_started")),
        "edit_patch_message_id_used": trace_merged.get("edit_patch_message_id_used") or "",
        "discord_duplicate_messages_created": "unknown",
    }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        result = sanitize_probe(data) if isinstance(data, dict) else {"available": False}
        result.update(probe_fields)
        result["trace_file_missing"] = not bool(trace)
        result["trace"] = trace
        result["trace_path"] = str(trace_path)
        result["latest_trace"] = latest
        result["missing_markers"] = _missing_webhook_trace_markers(trace)
        return result
    except (OSError, ValueError, TypeError):
        mode = str(cfg.get("webhook_mode") or "none")
        url = str(cfg.get("webhook_url") or "")
        return {
            **probe_fields,
            "available": True,
            "mode": mode,
            "interval_minutes": cfg.get("webhook_interval_minutes", 5),
            "url_present": bool(url),
            "url_masked": mask(url),
            "raw_url_never_included": True,
            "edit_message_id_present": bool(cfg.get("webhook_last_message_id")),
            "last_message_id_present": bool(cfg.get("webhook_last_message_id")),
            "scheduler_enabled": False,
            "scheduler_running": False,
            "scheduler_loop_count": 0,
            "last_send_result": "skipped",
            "reason_skipped": "start_not_reached_or_no_runtime_record",
            "last_http_status": "",
            "last_http_error_redacted": "",
            "last_exception_type": "",
            "last_exception_message_redacted": "",
            "last_response_body_redacted": "",
            "next_scheduled_send_at": "",
            "trace_file_missing": not bool(trace),
            "trace_path": str(trace_path),
            "trace": trace,
            "latest_trace": trace[-1] if trace else {},
            "missing_markers": _missing_webhook_trace_markers(trace),
        }


def _missing_webhook_trace_markers(trace: list[dict[str, Any]]) -> list[str]:
    required = (
        "start_selected", "config_path_read", "webhook_mode", "timer_armed",
        "reporter_tick_started", "telemetry_result", "send_periodic_status_entered",
        "telemetry_build_started", "telemetry_build_result", "payload_build_started",
        "payload_build_result", "send_attempted", "http_method", "http_status",
        "send_result", "last_http_method", "last_http_status", "last_http_url_kind",
        "edit_mode_selected", "state_read_path", "state_write_path", "state_write_ok",
        "config_read_path", "webhook_url_present_redacted", "reporter_tick_completed",
    )
    return [marker for marker in required if not any(marker in row for row in trace)]


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
    """Exec ``deng-rejoin --diag-startup-full`` as a child subprocess.

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
        from . import subprocess_isolated as _iso  # noqa: PLC0415

        rc, stdout, stderr, timed_out = _iso.run_isolated_text(
            [wrapper, "--diag-startup-full"],
            timeout=45.0,
        )
        out["returncode"] = rc
        out["sigsegv"] = (rc == -11)
        out["crashed"] = rc < 0
        out["stdout"] = mask(stdout[-16384:])
        out["stderr"] = mask(stderr[-16384:])
        if timed_out:
            errors.append({"step": "diag_startup", "error": "timeout"})
            out["returncode"] = None
            return out
        last_step = ""
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("STEP:"):
                last_step = line
        out["last_step"] = last_step
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "diag_startup", "error": str(exc)[:200]})
        out["returncode"] = None
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


def _capture_start_crash_state(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Read the last Start session marker state if it exists."""
    if not START_CRASH_STATE_PATH.is_file():
        return {
            "previous_crash_detected": False,
            "last_start_step": "",
        }
    try:
        data = json.loads(START_CRASH_STATE_PATH.read_text(encoding="utf-8"))
        return {
            **data,
            "previous_crash_detected": str(data.get("status") or "") == "running",
            "last_start_step": str(data.get("last_step") or ""),
        }
    except (OSError, json.JSONDecodeError) as exc:
        errors.append({"step": "start_crash_state", "error": str(exc)[:200]})
        return {
            "previous_crash_detected": False,
            "last_start_step": "",
        }


def _capture_rjn_style_detection(errors: list[dict[str, str]]) -> dict[str, Any]:
    path = DATA_DIR / "rjn-style-detection.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception as exc:  # noqa: BLE001
            errors.append({"step": "rjn_style_detection", "error": str(exc)[:200]})
    try:
        from .rjn_lifecycle_monitor import RjnLifecycleMonitor

        cfg_packages: list[str] = []
        from .config import load_config

        cfg = load_config()
        entries = cfg.get("roblox_packages") or []
        for entry in entries:
            if isinstance(entry, dict) and entry.get("package"):
                cfg_packages.append(str(entry["package"]))
        if cfg_packages:
            mon = RjnLifecycleMonitor(cfg_packages)
            mon.refresh_uid_map()
            for pkg in cfg_packages:
                mon.evaluate_package(pkg)
            return {"rjn_style_detection": mon.probe_snapshot()}
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "rjn_style_detection_live", "error": str(exc)[:200]})
    return {
        "rjn_style_detection": {
            "enabled": False,
            "reason": "no rjn-style-detection.json and live probe failed",
        },
    }


def _capture_landscape_debug_state(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Capture current landscape/home evidence using the same Start checks."""
    try:
        from . import android

        state = android.enforce_landscape_home_state(phase="probe", screen_mode_config="landscape")
        launcher_bounds = state.get("launcher_bounds", {})
        display_rect = state.get("display_rect", {})
        bounds = launcher_bounds.get("bounds") if isinstance(launcher_bounds, dict) else None
        match = "unknown"
        if isinstance(bounds, list) and len(bounds) == 4 and isinstance(display_rect, dict):
            bw = max(0, int(bounds[2]) - int(bounds[0]))
            bh = max(0, int(bounds[3]) - int(bounds[1]))
            dw = int(display_rect.get("width") or 0)
            dh = int(display_rect.get("height") or 0)
            match = "yes" if (dw >= dh and bw >= bh) else "no"
        return {
            "[DENG_REJOIN_LANDSCAPE_STATE]": state,
            "[DENG_REJOIN_HOME_ORIENTATION_CHECK]": {
                "launcher_package": launcher_bounds.get("launcher_package", "") if isinstance(launcher_bounds, dict) else "",
                "launcher_bounds": bounds,
                "expected_landscape_bounds": display_rect,
                "match": match,
            },
        }
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "landscape_debug_state", "error": str(exc)[:200]})
        return {}


# ─── Public entrypoint ────────────────────────────────────────────────────────


def _build_probe_summary(
    out: dict[str, Any],
    *,
    selftest: dict[str, Any] | None = None,
    last_command: str = "",
) -> dict[str, Any]:
    build = out.get("build") if isinstance(out.get("build"), dict) else {}
    device = out.get("device") if isinstance(out.get("device"), dict) else {}
    root = device.get("root") if isinstance(device.get("root"), dict) else {}
    menu = out.get("package_menu_diagnostics") if isinstance(out.get("package_menu_diagnostics"), list) else []
    usernames_found = sum(
        1 for row in menu
        if isinstance(row, dict) and str(row.get("display_username") or "").strip()
        and str(row.get("display_username")) not in {"Unknown", "unavailable", ""}
    )
    summary: dict[str, Any] = {
        "probe_id": out.get("probe_id") or "",
        "product_version": build.get("product_version") or build.get("version") or "",
        "artifact_sha256": build.get("artifact_sha256_short") or build.get("artifact_sha256") or "",
        "git_commit": build.get("git_commit_short") or build.get("git_commit") or "",
        "install_time_iso": build.get("install_time_iso") or "",
        "root_available": bool(root.get("available")),
        "root_required_mode": True,
        "packages_found": len(menu),
        "usernames_found": usernames_found,
        "selected_package": "",
        "last_command": last_command or "probe",
        "launch_attempted": False,
        "launch_state": "",
        "launch_reason": "",
        "strong_success_evidence": False,
        "blocking_errors": [
            str(e.get("error") or e.get("step") or "")[:120]
            for e in (out.get("errors") or [])
            if isinstance(e, dict) and (e.get("error") or e.get("step"))
        ][:8],
    }
    if selftest:
        summary.update({
            k: selftest.get(k)
            for k in (
                "selected_package",
                "launch_attempted",
                "launch_state",
                "launch_reason",
                "strong_success_evidence",
                "username_scan",
                "launch",
                "usernames",
                "states_before",
                "states_after",
                "launch_attempt",
                "kill_relaunch",
                "packages_total",
                "version",
                "build_commit",
            )
            if k in selftest
        })
        if selftest.get("package"):
            summary["selected_package"] = selftest.get("package")
        launch = selftest.get("launch") if isinstance(selftest.get("launch"), dict) else {}
        launch_attempt = selftest.get("launch_attempt") if isinstance(selftest.get("launch_attempt"), dict) else launch
        if launch_attempt:
            summary["launch_attempted"] = bool(launch_attempt.get("attempted"))
            summary["launch_state"] = launch_attempt.get("state_after") or launch_attempt.get("state") or ""
            summary["launch_reason"] = launch_attempt.get("failure_reason") or launch_attempt.get("reason") or ""
            summary["strong_success_evidence"] = bool(
                launch_attempt.get("success_evidence") or launch_attempt.get("strong_success_evidence")
            )
        usernames = selftest.get("usernames")
        if isinstance(usernames, list):
            summary["usernames_found"] = sum(
                1 for row in usernames
                if isinstance(row, dict)
                and str(row.get("username_display") or "") not in {"", "Unknown"}
                and row.get("account_status") == "logged_in"
            )
    return summary


def collect_probe(
    *,
    include_diag_startup: bool | None = None,
    include_heavy: bool = False,
    mode: str = "summary",
    selftest: dict[str, Any] | None = None,
    last_command: str = "",
) -> dict[str, Any]:
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
    if include_heavy or mode == "full":
        out["command_help"] = _capture_command_help(errors)
    else:
        out["command_help"] = {
            "skipped": True,
            "reason": "default summary mode; use probe --full or --debug-heavy",
        }
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
        if include_heavy or mode == "full":
            pkg_block: dict[str, Any] = {}
            pkg_block["process"] = _capture_process(pkg, errors)
            pkg_block["dumpsys"] = _capture_dumpsys_for_package(pkg, errors)
            pkg_block["portrait_input_readback"] = _capture_portrait_input_readback(pkg, errors)
            pkg_block["shared_prefs"] = _capture_shared_prefs(pkg, errors)
            pkg_block["presence"] = _capture_presence(entry, errors)
            pkgs[pkg] = pkg_block
        else:
            pkgs[pkg] = {
                "skipped": True,
                "reason": "summary mode; see resize_debug and lifecycle fields",
            }
    out["packages"] = pkgs
    out["log_tail"] = _capture_log_tail(errors)
    out["logs"] = _capture_all_logs(errors)
    out["latest_crash_log"] = _capture_latest_crash_log(out["logs"])
    out["installed_build"] = _capture_installed_build(errors)
    out["wrapper"] = _capture_wrapper_script(errors)
    out["last_start_diagnostics"] = _capture_last_diagnostics(errors)
    out["start_crash_state"] = _capture_start_crash_state(errors)
    out["last_failing_command"] = _capture_last_failing_command()
    out["webhook_debug"] = _capture_webhook_debug()
    try:
        from .package_identity import lifecycle_username_debug

        out["package_lifecycle_username"] = lifecycle_username_debug()
    except Exception as exc:  # noqa: BLE001
        out["package_lifecycle_username"] = {"error": str(exc)[:120]}
    out["landscape_debug_state"] = _capture_landscape_debug_state(errors)
    try:
        from .resize_trace import build_resize_debug_from_event, read_latest_resize_event

        out["resize_debug"] = build_resize_debug_from_event(read_latest_resize_event())
    except Exception as exc:  # noqa: BLE001
        out["resize_debug"] = {"error": str(exc)[:120]}
    out["rjn_style_detection"] = _capture_rjn_style_detection(errors)
    try:
        from .force_close_race import probe_force_close_race_snapshot

        out["force_close_race"] = probe_force_close_race_snapshot()
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "force_close_race", "error": str(exc)[:200]})
        out["force_close_race"] = {"enabled": False, "error": str(exc)[:120]}
    try:
        from . import checker_pointer as _checker_pointer

        _fc = _checker_pointer.probe_snapshot()
        out["focused_checker"] = _fc
        # Top-level launch scheduler proof (single-relay architecture).
        out["launch_scheduler"] = {
            "blocked_reason": _fc.get("launch_blocked_reason"),
            "waiting_for_online": bool(_fc.get("launch_waiting_for_online", False)),
            "first_launch_interval_s": _fc.get("first_launch_interval_s"),
            "last_launch_interval_s": _fc.get("last_launch_interval_s"),
            "first_launch_phase": _fc.get("first_launch_phase"),
            "first_launch_next_package_at": _fc.get("first_launch_next_package_at"),
            "valid_state_writer": _fc.get("valid_state_writer"),
        }
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "focused_checker", "error": str(exc)[:200]})
        out["focused_checker"] = {"error": str(exc)[:120]}
        out["launch_scheduler"] = {"error": str(exc)[:120]}
    try:
        from .launch_relaunch_trace import probe_snapshot as launch_probe_snapshot

        rjn = out.get("rjn_style_detection") or {}
        out["launch_relaunch"] = launch_probe_snapshot()
        lr = out["launch_relaunch"] if isinstance(out["launch_relaunch"], dict) else {}
        rjn_inner = rjn.get("rjn_style_detection") if isinstance(rjn, dict) else rjn
        if not isinstance(rjn_inner, dict):
            rjn_inner = {}
        pkg_row = {}
        if isinstance(rjn_inner, dict):
            pkgs = rjn_inner.get("packages") or {}
            if isinstance(pkgs, dict) and pkgs:
                pkg_row = next(iter(pkgs.values()), {})
        current_state = str((pkg_row or {}).get("state") or lr.get("last_launch_state") or "")
        online_confirmed = bool((pkg_row or {}).get("is_online_confirmed"))
        dead_detected = current_state in {"DEAD", "FAILED", "DISCONNECTED", "Disconnected", "Join Failed"}
        dead_internal = ""
        if dead_detected:
            dead_internal = str(
                (pkg_row or {}).get("last_transition_reason")
                or (pkg_row or {}).get("launch_failed_reason")
                or (pkg_row or {}).get("last_dead_reason")
                or ""
            )
        from .lifecycle_reasons import format_user_friendly_dead_reason

        dead_friendly = ""
        if dead_detected:
            dead_friendly = (pkg_row or {}).get("reason_user_friendly") or format_user_friendly_dead_reason(
                dead_internal
            )
        webhook_dbg = out.get("webhook_debug") if isinstance(out.get("webhook_debug"), dict) else {}
        out["rjn_detection_only"] = {
            "uid_map_ready": bool((rjn_inner or {}).get("uid_map")),
            "logcat_stream_alive": bool((rjn_inner or {}).get("logcat_stream_alive")),
            "logcat_pid": (rjn_inner or {}).get("logcat_pid"),
            "logcat_last_line_at": (rjn_inner or {}).get("logcat_last_line_at"),
            "logcat_last_uid_matched_line_at": (rjn_inner or {}).get(
                "logcat_last_uid_matched_line_at"
            ),
            "watched_phrases": (rjn_inner or {}).get("watched_phrases") or [],
            "last_gamejoinloadtime_at": (pkg_row or {}).get("last_gamejoinloadtime_at"),
            "last_positive_online_evidence_at": (pkg_row or {}).get(
                "last_positive_online_evidence_at"
            ),
            "last_with_reason_at": (pkg_row or {}).get("last_with_reason_at"),
            "last_doteleport_at": (pkg_row or {}).get("last_doteleport_at"),
            "online_confirmed_by": (pkg_row or {}).get("online_evidence_source")
            or (
                "uid_matched_gamejoinloadtime"
                if (pkg_row or {}).get("last_gamejoinloadtime_at")
                else "none"
            ),
            "detector_errors": (rjn_inner or {}).get("detector_errors") or [],
            "ignored_uid_lines": (rjn_inner or {}).get("ignored_uid_lines") or [],
            "detection_only": True,
        }
        # Compact per-package logcat diagnostic so it survives payload trimming
        # (the full rjn_style_detection block is dropped first under size pressure).
        # This reveals, for each ONLINE-but-not-recovering package, how long the
        # package's UID has been logcat-silent and a sample of the actual recent
        # Roblox log lines around the (GL-invisible) disconnect — the ground truth
        # needed to lock in a precise 278/disconnect detector.
        try:
            pkgs_map = (rjn_inner or {}).get("packages") or {}
            packages_logcat: dict[str, Any] = {}
            if isinstance(pkgs_map, dict):
                for _pkg, _row in pkgs_map.items():
                    if not isinstance(_row, dict):
                        continue
                    packages_logcat[_pkg] = {
                        "state": _row.get("state"),
                        "is_online_confirmed": _row.get("is_online_confirmed"),
                        "uid_line_silence_seconds": _row.get("uid_line_silence_seconds"),
                        "last_uid_line_at": _row.get("last_uid_line_at"),
                        "last_with_reason_at": _row.get("last_with_reason_at"),
                        "recent_uid_lines": (_row.get("recent_uid_lines") or [])[-10:],
                    }
            out["rjn_detection_only"]["packages_logcat"] = packages_logcat
        except Exception as exc:  # noqa: BLE001
            out["rjn_detection_only"]["packages_logcat_error"] = str(exc)[:160]
        out["decision"] = {
            "state": current_state,
            "reason_internal": dead_internal,
            "reason_user_friendly": dead_friendly,
            "is_online_confirmed": online_confirmed,
        }
        out["state_machine"] = {
            "current_state": current_state,
            "previous_state": lr.get("last_launch_state"),
            "last_transition_at": (pkg_row or {}).get("last_transition_at"),
            "last_transition_reason": (pkg_row or {}).get("last_transition_reason"),
            "active_monitored": bool((rjn_inner or {}).get("enabled")),
            "can_trigger_dead_from_current_state": True,
            "can_trigger_relaunch_from_dead": bool((lr.get("relaunch") or {}).get("relaunch_queued")),
            "can_trigger_webhook_from_dead": bool(webhook_dbg.get("webhook_enabled")),
        }
        out["online_detection"] = {
            "online_confirmed": online_confirmed,
            "online_since": (pkg_row or {}).get("online_since"),
            "runtime_source": (pkg_row or {}).get("runtime_source"),
            "primary_required_signal": "gamejoinloadtime",
            "last_gamejoinloadtime_at": (pkg_row or {}).get("last_gamejoinloadtime_at"),
            "last_positive_online_evidence_at": (pkg_row or {}).get(
                "last_positive_online_evidence_at"
            ),
            "fallback_evidence_checked": True,
            "fallback_evidence_result": (pkg_row or {}).get("online_evidence_source") or "none",
            "why_still_launching": (pkg_row or {}).get("why_still_launching") or (
                (pkg_row or {}).get("decision") if not online_confirmed else ""
            ),
        }
        out["dead_detection"] = {
            "process_exists": bool((pkg_row or {}).get("process_exists")),
            "pids": (pkg_row or {}).get("pids") or [],
            "last_process_check_at": (pkg_row or {}).get("last_process_check_at"),
            "dead_detected": dead_detected,
            "dead_reason_internal": dead_internal,
            "dead_reason_user_friendly": dead_friendly,
        }
        out["relaunch"] = lr.get("relaunch") or {}
        out["account_dead_webhook"] = {
            "enabled": bool(webhook_dbg.get("webhook_enabled")),
            "should_send_for_current_dead_event": current_state in {
                "DEAD",
                "FAILED",
                "DISCONNECTED",
                "Join Failed",
            },
            "sent": bool(webhook_dbg.get("last_lifecycle_send_ok")),
            "last_send_status": webhook_dbg.get("last_http_status"),
            "last_error": webhook_dbg.get("last_error"),
            "field_label": "Reason",
            "reason_user_friendly": dead_friendly,
        }
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "launch_relaunch_probe", "error": str(exc)[:200]})
    try:
        from .config import get_package_display_username
        from . import package_username as _pu
        menu_diag = []
        if isinstance(cfg, dict):
            for entry in pkg_entries:
                if not isinstance(entry, dict):
                    continue
                pkg = str(entry.get("package") or "")
                if not pkg:
                    continue
                scan = _pu.scan_package_username_root(pkg)
                menu_diag.append({
                    "package": pkg,
                    "display_username": scan.username or get_package_display_username(entry, cfg),
                    "username_source": scan.source if scan.username else (entry.get("username_source") or "not_set"),
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
        out["package_menu_diagnostics"] = menu_diag
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "package_menu_diagnostics", "error": str(exc)[:200]})
        out["package_menu_diagnostics"] = []

    # ── Third-party / global dumpsys — full probe only (noisy for resize proof)
    if include_heavy or mode == "full":
        out["installed_packages"] = _capture_installed_packages(errors)
        out["third_party_evidence"] = _capture_kaeru_evidence(
            out["installed_packages"], errors,
        )
        out["appops"] = _capture_appops(
            out["third_party_evidence"].get("targets", []) or [],
            errors,
        )
        out["termux_shared_prefs"] = _capture_termux_prefs(errors)
        out["getprop"] = _capture_getprop(errors)
        out["dumpsys_global"] = _capture_dumpsys_global(errors)
    else:
        out["installed_packages"] = {"skipped": True, "reason": "summary mode"}
        out["third_party_evidence"] = {"skipped": True, "reason": "summary mode"}
        out["appops"] = {"skipped": True, "reason": "summary mode"}
        out["termux_shared_prefs"] = {"skipped": True, "reason": "summary mode"}
        out["getprop"] = {"skipped": True, "reason": "summary mode"}
        out["dumpsys_global"] = {"skipped": True, "reason": "summary mode"}
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

    out["snapshot_proof"] = _capture_snapshot_proof(errors)
    if not include_heavy and mode != "full":
        out["logcat"] = {"skipped": True, "reason": "summary mode; use --full for logcat"}
    summary = _build_probe_summary(out, selftest=selftest, last_command=last_command)
    out["summary"] = summary
    if selftest:
        out["selftest"] = selftest
    ordered: dict[str, Any] = {"summary": summary}
    for key, value in out.items():
        if key != "summary":
            ordered[key] = value
    ordered["errors"] = compact_probe_errors(ordered.get("errors") or [])
    return ordered


def _capture_snapshot_proof(errors: list[dict[str, str]]) -> dict[str, Any]:
    """Run the snapshot capture ladder and summarize the evidence.

    Includes: providers attempted, the selected provider, su availability,
    root result, PNG byte length + signature validity, and the bridge's
    backend-visible snapshot status. Never raises.
    """
    proof: dict[str, Any] = {}
    try:
        from . import snapshot as _snap
        cap = _snap.capture_snapshot_detailed()
        proof["capture"] = cap.to_safe_dict()
        proof["summary"] = {
            "provider": cap.provider,
            "bytes": int(cap.byte_length or 0),
            "png_valid": bool(cap.png_valid),
            "su_available": bool(cap.su_available),
            "root_granted": cap.root_granted,
            "final_result": cap.result,
            "providers_attempted": [a.provider for a in cap.attempts],
        }
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "snapshot_proof.capture", "error": str(exc)[:200]})
        proof["capture"] = {"error": "capture_failed"}

    # Bridge / backend visibility (best-effort; safe if bridge not running).
    try:
        from . import monitor_autostart as _ma
        status = _ma.get_monitor_status_summary()
        proof["bridge_status"] = {
            k: status.get(k)
            for k in (
                "bridge_running", "connected", "snapshot_last_result",
                "snapshot_last_bytes", "snapshot_last_upload_status",
                "snapshot_provider", "snapshot_png_valid", "snapshot_root_granted",
                "snapshot_su_available", "snapshot_provider_called_count",
                "screencap_available",
            )
        }
    except Exception as exc:  # noqa: BLE001
        errors.append({"step": "snapshot_proof.bridge_status", "error": str(exc)[:200]})
        proof["bridge_status"] = {"error": "unavailable"}

    return proof


# ── Payload trimming for upload ─────────────────────────────────────────────

# curl error 63 rejects payloads over ~1.1 MB on some Termux builds.
# Upload transmission must stay under 200 KB gzip — never stream the raw
# on-disk probe file directly.
_PROBE_ERROR_MAX = 20
_PROBE_TRACE_MAX_LEN = 200
_UPLOAD_GZIP_MAX_BYTES = 200 * 1024
_UPLOAD_RAW_MAX_BYTES = 180 * 1024
_UPLOAD_HARD_CAP_BYTES = _UPLOAD_GZIP_MAX_BYTES
_UPLOAD_SOFT_BUDGET_BYTES = 180 * 1024
_UPLOAD_TMP_DIR = PROBE_DIR / "upload_tmp"

# Order matters — when over budget, we drop the highest-cost,
# lowest-signal fields first.  Per-package shared_prefs/dumpsys win over
# the global captures because they're scoped to the user's installation.
_PROBE_DROP_ORDER: tuple[str, ...] = (
    "command_help",
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

# These are the minimum facts needed to diagnose a runtime failure.  The
# generic payload clamp must never evict them after the large Android captures
# have been removed.
_PROBE_PINNED_FIELDS = frozenset({
    "latest_crash_log", "installed_build", "wrapper",
    "last_start_diagnostics", "start_crash_state", "last_failing_command", "webhook_debug",
    "rjn_detection_only", "online_detection", "decision", "state_machine",
    "dead_detection", "launch_relaunch", "relaunch", "account_dead_webhook",
    "resize_debug", "focused_checker", "launch_scheduler", "force_close_race",
})


def compact_probe_errors(errors: list[Any]) -> list[dict[str, str]]:
    """Dedupe and cap probe step errors before serialization."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for item in errors or []:
        if not isinstance(item, dict):
            continue
        step = str(item.get("step") or "")[:120]
        error = str(item.get("error") or "")[: _PROBE_TRACE_MAX_LEN]
        key = (step, error)
        if not (step or error) or key in seen:
            continue
        seen.add(key)
        out.append({"step": step, "error": error})
        if len(out) >= _PROBE_ERROR_MAX:
            break
    return out


def _strip_verbose_probe_lists(probe: dict[str, Any]) -> None:
    """Truncate list-heavy diagnostic sections in-place."""
    for key in ("errors", "steps", "traces", "blocking_errors"):
        val = probe.get(key)
        if isinstance(val, list):
            probe[key] = compact_probe_errors(val) if key == "errors" else val[:_PROBE_ERROR_MAX]
        elif isinstance(val, str) and len(val) > _PROBE_TRACE_MAX_LEN:
            probe[key] = val[:_PROBE_TRACE_MAX_LEN]


def clamp_probe_payload_size(
    probe: dict[str, Any],
    *,
    max_raw_bytes: int = _UPLOAD_RAW_MAX_BYTES,
) -> dict[str, Any]:
    """Ensure raw JSON size stays under ``max_raw_bytes`` before upload."""
    trimmed = dict(probe)
    trimmed["errors"] = compact_probe_errors(trimmed.get("errors") or [])

    def _raw_size(p: dict[str, Any]) -> int:
        try:
            return len(json.dumps(p, separators=(",", ":")).encode("utf-8"))
        except Exception:  # noqa: BLE001
            return max_raw_bytes + 1

    before = _raw_size(trimmed)
    if before <= max_raw_bytes:
        return trimmed

    slim, report = trim_probe_for_upload(trimmed)
    dropped = list(report.get("dropped") or [])
    if _raw_size(slim) > max_raw_bytes:
        for key in list(slim.keys()):
            if key in {"summary", "probe_version", "captured_at_iso", "errors", "_upload_trim"} | _PROBE_PINNED_FIELDS:
                continue
            val = slim.get(key)
            if isinstance(val, str) and len(val) > 4000:
                slim[key] = val[:4000] + "...<truncated>"
                dropped.append(f"truncate:{key}")
            elif isinstance(val, (dict, list)) and _raw_size(slim) > max_raw_bytes:
                slim[key] = "<dropped: payload size budget>"
                dropped.append(key)
        while _raw_size(slim) > max_raw_bytes:
            removed = False
            for field in _PROBE_DROP_ORDER:
                if _drop_field(slim, field):
                    dropped.append(field)
                    removed = True
                    break
            if not removed:
                for key in list(slim.keys()):
                    if key in {"summary", "probe_version", "captured_at_iso"} | _PROBE_PINNED_FIELDS:
                        continue
                    slim.pop(key, None)
                    dropped.append(f"pop:{key}")
                    break
            if _raw_size(slim) <= max_raw_bytes:
                break

    slim["_payload_clamp"] = {
        "raw_bytes_before": before,
        "raw_bytes_after": _raw_size(slim),
        "raw_budget": max_raw_bytes,
        "trim_dropped": dropped,
    }
    return slim


def prepare_probe_upload_payload(
    probe: dict[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    """Build an isolated gzip upload buffer under the 200 KB cap.

    Writes a minimized JSON temp file and a gzipped sibling — never reads
    the full historical probe file from disk for upload.
    """
    import gzip as _gz

    working = clamp_probe_payload_size(sanitize_probe(probe))
    _strip_verbose_probe_lists(working)
    trimmed, trim_report = trim_probe_for_upload(
        working,
        hard_cap=_UPLOAD_GZIP_MAX_BYTES,
        soft_budget=_UPLOAD_SOFT_BUDGET_BYTES,
    )
    _strip_verbose_probe_lists(trimmed)

    body = json.dumps(trimmed, separators=(",", ":")).encode("utf-8")
    payload = _gz.compress(body)
    passes = 0
    while len(payload) > _UPLOAD_GZIP_MAX_BYTES and passes < 24:
        passes += 1
        dropped = False
        for field in _PROBE_DROP_ORDER:
            if _drop_field(trimmed, field):
                dropped = True
                break
        if not dropped:
            for key in list(trimmed.keys()):
                if key in {"summary", "probe_version", "captured_at_iso", "errors"} | _PROBE_PINNED_FIELDS:
                    continue
                trimmed.pop(key, None)
                dropped = True
                break
        if not dropped:
            break
        body = json.dumps(trimmed, separators=(",", ":")).encode("utf-8")
        payload = _gz.compress(body)

    if len(payload) > _UPLOAD_GZIP_MAX_BYTES:
        raise ValueError(
            f"probe upload payload {len(payload)} bytes exceeds "
            f"{_UPLOAD_GZIP_MAX_BYTES} byte cap after trim",
        )

    _UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = _UPLOAD_TMP_DIR / f"probe_upload_tmp-{stamp}-{_short_id()}.json"
    gz_path = _UPLOAD_TMP_DIR / f"{json_path.name}.gz"
    json_path.write_bytes(body)
    gz_path.write_bytes(payload)

    report = {
        "dropped": trim_report.get("dropped") or [],
        "gzip_bytes": len(payload),
        "raw_bytes": len(body),
        "json_path": str(json_path),
        "gzip_path": str(gz_path),
        "passes": passes,
        "hard_cap": _UPLOAD_GZIP_MAX_BYTES,
    }
    return payload, report


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
    safe_probe = sanitize_probe(probe)
    path.write_text(json.dumps(safe_probe, indent=2, sort_keys=True), encoding="utf-8")
    return path


def sanitize_probe(value: Any) -> Any:
    """Deep-copy probe content while redacting secrets and private URLs."""
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if re.search(r"(?i)(cookie|token|secret|password|session_id)$", key_text):
                if item:
                    safe[key_text] = f"<masked:{key_text.lower()}>"
                else:
                    safe[key_text] = item
                continue
            safe[key_text] = sanitize_probe(item)
        return safe
    if isinstance(value, list):
        return [sanitize_probe(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_probe(item) for item in value]
    if isinstance(value, str):
        return mask(value)
    return value


def save_upload_bundle(probe: dict[str, Any], *, reason: str = "") -> Path:
    """Persist a sanitized offline upload bundle for manual support delivery."""
    UPLOAD_BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = UPLOAD_BUNDLE_DIR / f"probe-upload-bundle-{ts}-{_short_id()}.json"
    try:
        _, trim_report = prepare_probe_upload_payload(probe)
        json_path = Path(str(trim_report.get("json_path") or ""))
        trimmed = json.loads(json_path.read_text(encoding="utf-8")) if json_path.is_file() else probe
    except (ValueError, OSError, json.JSONDecodeError):
        trimmed, trim_report = trim_probe_for_upload(clamp_probe_payload_size(sanitize_probe(probe)))
    bundle = {
        "bundle_version": 1,
        "created_at_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": mask(reason)[:500],
        "upload_trim": trim_report,
        "probe": trimmed,
    }
    path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
    return path


# ─── Upload ──────────────────────────────────────────────────────────────────


def _resolve_install_api() -> str:
    from . import api_config

    return api_config.resolve_api_base_url(allow_install_file=True, allow_default=True)


def upload_probe(probe: dict[str, Any], *, timeout: float = 15.0) -> tuple[bool, str]:
    """POST a trimmed probe to the install API dev-probe endpoint.

    Builds an isolated temp gzip buffer (never streams the raw on-disk probe
    file).  Returns ``(ok, info)``.  Never raises.
    """
    from . import safe_http

    api = _resolve_install_api()
    if not api:
        return False, "install API URL not configured (set DENG_REJOIN_INSTALL_API)"
    url = api.rstrip("/") + "/api/dev-probe/upload"

    try:
        payload, trim_report = prepare_probe_upload_payload(probe)
    except ValueError as exc:
        return False, f"probe payload too large after trim: {exc}"
    except OSError as exc:
        return False, f"OS error preparing upload: {exc}"

    if len(payload) > _UPLOAD_GZIP_MAX_BYTES:
        return False, (
            f"payload still {len(payload)/1024:.1f} KB after trim — "
            f"cap is {_UPLOAD_GZIP_MAX_BYTES/1024:.0f} KB. "
            f"Dropped: {trim_report.get('dropped') or []!r}"
        )

    http_timeout = (safe_http.DEFAULT_CONNECT_TIMEOUT, int(max(5, min(float(timeout), 15))))
    try:
        http_status, raw = safe_http.post_raw(
            url,
            payload,
            content_type="application/json",
            headers={"Content-Encoding": "gzip", "User-Agent": "deng-rejoin-probe/1"},
            timeout=http_timeout,
        )
    except safe_http.SafeHttpNetworkError as exc:
        return False, f"network error: {exc}"
    except OSError as exc:
        return False, f"OS error: {exc}"

    body_text = (raw or b"").decode("utf-8", errors="replace")
    if http_status >= 400:
        if http_status == 413:
            return False, (
                f"server rejected size {len(payload)} bytes (413). "
                f"Trim dropped: {trim_report.get('dropped') or []!r}. "
                f"Server body: {body_text[:300]}"
            )
        if http_status == 429:
            return False, (
                f"rate-limited (429) — wait ~60 seconds and try again. "
                f"Server body: {body_text[:300]}"
            )
        if http_status == 401:
            return False, f"server rejected upload (401): {body_text[:300]}"
        return False, f"HTTP {http_status}: {body_text[:300]}"

    try:
        obj = json.loads(body_text) if body_text.strip() else {}
    except json.JSONDecodeError:
        return False, f"non-JSON response: {body_text[:200]}"
    pid = str(obj.get("probe_id") or "").strip()
    if not pid:
        return False, f"no probe_id in response: {body_text[:200]}"
    return True, pid
