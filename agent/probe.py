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
    return {
        "wm_size": _run("wm size", ["wm", "size"], errors, timeout=4).stdout,
        "wm_density": _run("wm density", ["wm", "density"], errors, timeout=4).stdout,
        "dumpsys_display": _run(
            "dumpsys display",
            ["dumpsys", "display"],
            errors,
            timeout=10,
        ).stdout[:4000],
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
    out: dict[str, Any] = {}
    for ns in ("global", "secure", "system"):
        res = _run(f"settings list {ns}", ["settings", "list", ns], errors, timeout=8)
        # Keep filtered view for readability AND the full text for grep-ability.
        out[f"{ns}_filtered"] = _filter_settings(res.stdout)
        out[f"{ns}_raw_len"] = len(res.stdout)
    return out


def _capture_command_help(errors: list[dict[str, str]]) -> dict[str, str]:
    """Capture which verbs the host actually supports for resize / launch bounds."""
    helps: dict[str, str] = {}
    for label, args in (
        ("cmd_activity_help", ["cmd", "activity", "help"]),
        ("am_help", ["am", "help"]),
        ("cmd_window_help", ["cmd", "window", "help"]),
        ("cmd_package_help", ["cmd", "package"]),
    ):
        res = _run(label, args, errors, timeout=6)
        helps[label] = (res.stdout or res.stderr)[:4000]
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

    We keep the unfiltered text length so we can prove that the host did
    return something and we just didn't match it.
    """
    out: dict[str, str] = {}
    for label, args in (
        ("window_windows", ["dumpsys", "window", "windows"]),
        ("activity_activities", ["dumpsys", "activity", "activities"]),
        ("activity_recents", ["dumpsys", "activity", "recents"]),
        ("surfaceflinger_list", ["dumpsys", "SurfaceFlinger", "--list"]),
    ):
        res = _run(label, args, errors, timeout=10)
        full = res.stdout or ""
        # Take up to 200 lines that mention the package + a 200-char wider window.
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
    root we can only enumerate the directory.
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
    # Pull file names, then cat the small ones.
    for line in listing.stdout.splitlines():
        parts = line.split()
        if len(parts) < 9:
            continue
        name = parts[-1]
        if not name.endswith(".xml"):
            continue
        cat = _run(
            f"cat {name}",
            ["cat", f"{prefs_path}/{name}"],
            errors,
            root=True,
            timeout=6,
        )
        out["files"][name] = mask(cat.stdout)[:6000]
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


def collect_probe() -> dict[str, Any]:
    """Run every capture step and return the full probe dict.

    Never raises.  Errors are captured under ``probe["errors"]``.
    """
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
    out["last_start_diagnostics"] = _capture_last_diagnostics(errors)
    return out


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


def upload_probe(probe: dict[str, Any], *, timeout: float = 20.0) -> tuple[bool, str]:
    """POST the probe JSON to the install API's dev-probe endpoint.

    Returns ``(ok, info)`` where ``info`` is a short probe id on success or
    a short error string on failure.  Never raises.
    """
    import gzip
    import urllib.error
    import urllib.request

    api = _resolve_install_api()
    if not api:
        return False, "install API URL not configured"
    url = api.rstrip("/") + "/api/dev-probe/upload"
    body = json.dumps(probe, separators=(",", ":")).encode("utf-8")
    payload = gzip.compress(body)
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
        return False, f"HTTP {exc.code}: {body_text}"
    except urllib.error.URLError as exc:
        return False, f"network error: {exc.reason}"
    except OSError as exc:
        return False, f"OS error: {exc}"
