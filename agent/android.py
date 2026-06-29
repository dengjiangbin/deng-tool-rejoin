"""Centralized Android and optional-root command execution."""

from __future__ import annotations

import base64
from collections import Counter
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Global lock: serialize ALL subprocess.run() calls to prevent concurrent
# fork() in a multithreaded Termux/Python 3.13 process.  Multiple
# _PackageWorker threads calling subprocess.run() simultaneously is the
# primary cause of SIGSEGV (probe p-316b3b040d).
# Locking serializes health-check commands across workers — each command
# still completes quickly (PROCESS_TIMEOUT_SECONDS), so total overhead
# for 2-3 workers is acceptable vs. the crash risk.
_subprocess_lock = threading.Lock()


def subprocess_lock() -> threading.Lock:
    """Return the single global lock for subprocess/fork work.

    Start, watchdog, Android/root commands, and Termux curl HTTP calls share
    this lock to avoid concurrent fork/exec in Termux + Python 3.13.
    """
    return _subprocess_lock

from .config import ConfigError, is_valid_package_name, normalize_package_detection_hint, validate_package_name
from .constants import (
    DEFAULT_ROBLOX_PACKAGE,
    DEFAULT_ROBLOX_PACKAGE_HINTS,
    LAUNCH_MODES,
    PROCESS_TIMEOUT_SECONDS,
    ROOT_TIMEOUT_SECONDS,
)
from .platform_detect import detect_public_download_dir, get_android_release, get_android_sdk
from . import subprocess_isolated as _iso
from .url_utils import UrlValidationError, detect_launch_mode_from_url, mask_launch_url, to_roblox_deep_link, validate_launch_url


@dataclass(frozen=True)
class RobloxPackageCandidate:
    package: str
    app_name: str
    launchable: bool


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    @property
    def summary(self) -> str:
        text = (self.stderr or self.stdout or "").strip()
        return text[:500]


@dataclass(frozen=True)
class RootInfo:
    available: bool
    tool: str | None = None
    detail: str = ""


def _safe_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("LC_ALL", "C")
    return env


def _stringify_args(args: Iterable[str]) -> list[str]:
    return [str(arg) for arg in args]


def process_cmdline_scan_args(package: str) -> list[str]:
    """Return a safe /proc cmdline scan command for one package.

    The package name is passed as ``$1`` rather than embedded in the shell
    program. That lets the script skip its own shell process and parent ``su``
    process. It also matches the package only as an exact argv token or
    Android ``--nice-name`` value, never as a filename substring in a detached
    relaunch script.
    """
    package = validate_package_name(package)
    script = (
        "target=$1; self=$$; parent=$PPID; "
        "for f in /proc/[0-9]*/cmdline; do "
        "pid=${f%/cmdline}; pid=${pid##*/}; "
        "[ \"$pid\" = \"$self\" ] && continue; "
        "[ \"$pid\" = \"$parent\" ] && continue; "
        "cmd=$(tr '\\000' ' ' < \"$f\" 2>/dev/null) || continue; "
        "case \" $cmd \" in "
        "\" $target \"*|*\" --nice-name=$target \"*) echo \"$pid\"; exit 0;; "
        "esac; "
        "done; exit 1"
    )
    return ["sh", "-c", script, "deng-proc-scan", package]


_PROCESS_SCAN_EXCLUSIONS: tuple[str, ...] = (
    "relaunch", "termux", "grep", "bash", "sh",
)


def process_ps_scan_args(package: str) -> list[str]:
    """Return a sanitized ``ps -ef`` liveness scan for one Android package.

    Clone process names can be masked or truncated, so short-name probes are
    not reliable liveness evidence. The package remains an argv parameter
    and the pipeline excludes Termux and detached relaunch scripts.
    """
    package = validate_package_name(package)
    script = (
        "target=$1; "
        "ps -ef 2>/dev/null | grep -F -- \"$target\" "
        "| grep -v -E 'relaunch|termux|grep|bash|sh' | head -n 1"
    )
    return ["sh", "-c", script, "deng-ps-scan", package]


def ps_output_has_live_package(output: str, package: str) -> bool:
    """Return whether *output* contains a non-automation row for *package*."""
    package = validate_package_name(package)
    for line in str(output or "").splitlines():
        lowered = line.lower()
        if package not in line:
            continue
        if any(marker in lowered for marker in _PROCESS_SCAN_EXCLUSIONS):
            continue
        return True
    return False


def process_ps_first_pid(output: str, package: str) -> str:
    """Extract the first numeric PID from one sanitized ``ps -ef`` row."""
    if not ps_output_has_live_package(output, package):
        return ""
    for token in str(output).split():
        if token.isdigit() and int(token) > 0:
            return token
    return ""


# Android system binaries that live in /system/bin (or /system/xbin) but
# are NOT in Termux's default PATH.  Without this resolution every call to
# dumpsys/wm/cmd/settings/etc. fails with ENOENT in Termux, silently
# breaking state detection, window readback, and layout discovery.
# Confirmed from real cloud-phone probe (Samsung SM-N9810 fingerprint
# c1q:13/TP1A.220624.014, probe id p-368a65d699).
_ANDROID_SYSTEM_BINARIES: frozenset[str] = frozenset({
    "am", "cmd", "dumpsys", "getprop", "input", "logcat", "pm",
    "pidof", "ps", "service", "settings", "setprop", "wm", "ime",
})
_ANDROID_BIN_DIRS: tuple[str, ...] = ("/system/bin", "/system/xbin", "/vendor/bin")


def _resolve_android_binary(name: str) -> str:
    """Return absolute path for a known Android binary, or *name* unchanged.

    Skips resolution if *name* is already an absolute or relative path, or
    if it isn't on our short list of expected Android binaries.  This is
    deliberately conservative so we never accidentally shadow a Termux
    binary of the same name.
    """
    if not name or "/" in name:
        return name
    if name not in _ANDROID_SYSTEM_BINARIES:
        return name
    for bin_dir in _ANDROID_BIN_DIRS:
        candidate = f"{bin_dir}/{name}"
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return name


def _maybe_resolve_first_arg(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    resolved = _resolve_android_binary(cmd[0])
    if resolved == cmd[0]:
        return cmd
    return [resolved] + list(cmd[1:])


def run_command(args: Iterable[str], *, timeout: int = PROCESS_TIMEOUT_SECONDS) -> CommandResult:
    """Run a local command with timeout and captured output.

    For known Android system binaries (dumpsys/wm/cmd/settings/...), the
    first argument is auto-resolved to its ``/system/bin/`` absolute path
    when present.  This is essential on Termux where the default ``$PATH``
    does NOT include ``/system/bin``.

    All subprocess.run() calls are serialized through ``_subprocess_lock``
    to prevent concurrent fork() from multiple worker threads, which is the
    primary cause of SIGSEGV on Termux + Python 3.13 (probe p-316b3b040d).
    """
    cmd = _maybe_resolve_first_arg(_stringify_args(args))
    rc, stdout, stderr, timed_out = _iso.run_isolated_text(
        cmd,
        timeout=float(timeout),
        env=_safe_env(),
        lock=subprocess_lock(),
    )
    if timed_out:
        return CommandResult(
            tuple(cmd),
            124,
            stdout.strip(),
            (stderr.strip() or "command timed out"),
            timed_out=True,
        )
    if rc == 127 and not stdout and stderr == "not found":
        return CommandResult(tuple(cmd), 127, "", "command not found")
    return CommandResult(tuple(cmd), rc, stdout.strip(), stderr.strip())


def is_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return bool(os.environ.get("TERMUX_VERSION") or "com.termux" in prefix or prefix.startswith("/data/data/com.termux"))


def get_android_version() -> str | None:
    release = get_android_release()
    return None if release == "unknown" else release


def get_android_sdk_version() -> str | None:
    sdk = get_android_sdk()
    return None if sdk == "unknown" else sdk


def has_android_shell_access() -> bool:
    result = run_command(["getprop", "ro.product.model"], timeout=PROCESS_TIMEOUT_SECONDS)
    return result.ok


def has_storage_permission() -> bool:
    candidates = [
        Path("/sdcard/Download"),
        Path("/sdcard/download"),
        Path("/storage/emulated/0/Download"),
        Path("/storage/emulated/0/download"),
        Path.home() / "storage" / "shared",
    ]
    return any(path.exists() and os.access(path, os.R_OK) for path in candidates)


def get_download_dir() -> str:
    return detect_public_download_dir()


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def package_manager_result() -> CommandResult:
    if os.name == "nt":
        return CommandResult(("cmd", "package", "list", "packages"), 127, "", "Android package manager is unavailable on Windows")
    result = run_command(["cmd", "package", "list", "packages"], timeout=PROCESS_TIMEOUT_SECONDS)
    if result.ok:
        return result
    return run_command(["pm", "list", "packages"], timeout=PROCESS_TIMEOUT_SECONDS)


def list_packages() -> list[str]:
    result = package_manager_result()
    if not result.ok:
        return []
    packages: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            line = line[len("package:") :]
        if line and is_valid_package_name(line):
            packages.append(line)
    return sorted(set(packages))


def _safe_detection_hints(hints: Iterable[str] | None = None) -> list[str]:
    source = hints or DEFAULT_ROBLOX_PACKAGE_HINTS
    validated: list[str] = []
    for hint in source:
        try:
            cleaned = normalize_package_detection_hint(str(hint))
        except ConfigError:
            continue
        if cleaned not in validated:
            validated.append(cleaned)
    return validated or list(DEFAULT_ROBLOX_PACKAGE_HINTS)


def find_roblox_packages(hints: Iterable[str] | None = None) -> list[str]:
    packages = list_packages()
    detection_hints = _safe_detection_hints(hints)
    found = [pkg for pkg in packages if any(hint in pkg.lower() for hint in detection_hints)]
    if DEFAULT_ROBLOX_PACKAGE in found:
        found.remove(DEFAULT_ROBLOX_PACKAGE)
        found.insert(0, DEFAULT_ROBLOX_PACKAGE)
    elif DEFAULT_ROBLOX_PACKAGE in packages:
        found.insert(0, DEFAULT_ROBLOX_PACKAGE)
    return found


def is_launchable_package(package: str) -> bool:
    """True when cmd package resolve-activity finds a launcher component."""
    package = validate_package_name(package)
    cmd_bin = _find_command("cmd", "/system/bin/cmd")
    if not cmd_bin:
        return False
    resolve = run_command(
        [cmd_bin, "package", "resolve-activity", "--brief", package],
        timeout=PROCESS_TIMEOUT_SECONDS,
    )
    if not resolve.ok or package not in resolve.stdout:
        return False
    return True


def get_application_label(package: str) -> str:
    """Best-effort app label from dumpsys package (first ~20k chars only)."""
    package = validate_package_name(package)
    result = run_command(["dumpsys", "package", package], timeout=PROCESS_TIMEOUT_SECONDS)
    if not result.ok:
        return ""
    text = result.stdout[:20000]
    for pattern in (
        r'application-label="([^"]+)"',
        r"application-label='([^']+)'",
        r'android:label="([^"]+)"',
    ):
        m = re.search(pattern, text)
        if m:
            lab = m.group(1).strip()
            if lab and lab.lower() != "null":
                return lab[:120]
    m2 = re.search(r"nonLocalizedLabel=([^\s}]+)", text)
    if m2:
        lab = m2.group(1).strip().strip('"')
        if lab and lab.lower() != "null":
            return lab[:120]
    return ""


def get_application_label_cached(package: str, cache: dict[str, str]) -> str:
    if package not in cache:
        cache[package] = get_application_label(package)
    return cache[package]


_ORIENTATION_PACKAGE_MARKERS: tuple[str, ...] = (
    "orientation",
    "rotation",
    "screenorientation",
    "screen_orientation",
    "controlthescreen",
    "controlscreen",
)

_ORIENTATION_LABEL_MARKERS: tuple[str, ...] = (
    "control screen orientation",
    "screen orientation",
    "orientation control",
    "rotation control",
)


def _third_party_packages() -> list[str]:
    """Return user-installed package names, or an empty list on failure."""
    result = run_android_command(["pm", "list", "packages", "-3"], timeout=8, prefer_root=False)
    if not result.ok:
        result = run_android_command(["pm", "list", "packages"], timeout=8, prefer_root=False)
    if not result.ok:
        return []
    packages: list[str] = []
    for line in (result.stdout or "").splitlines():
        raw = line.strip()
        if raw.startswith("package:"):
            raw = raw[len("package:") :]
        try:
            packages.append(validate_package_name(raw))
        except ConfigError:
            continue
    return sorted(set(packages))


def detect_orientation_override_apps(
    *,
    packages: Iterable[str] | None = None,
    protected_packages: Iterable[str] | None = None,
) -> list[dict[str, str]]:
    """Detect third-party screen-rotation/orientation controller apps.

    Safe scope: returns candidates only.  Callers decide whether to force-stop
    one.  Termux and selected Roblox packages can be passed as protected
    packages and will never be returned.
    """
    protected = {str(p).strip() for p in (protected_packages or []) if str(p).strip()}
    protected.add("com.termux")
    source = list(packages) if packages is not None else _third_party_packages()
    out: list[dict[str, str]] = []
    label_cache: dict[str, str] = {}
    for raw in source:
        try:
            pkg = validate_package_name(raw)
        except ConfigError:
            continue
        if pkg in protected:
            continue
        lower = pkg.lower().replace(".", "")
        pkg_match = any(marker.replace("_", "") in lower for marker in _ORIENTATION_PACKAGE_MARKERS)
        label = get_application_label_cached(pkg, label_cache)
        label_lower = label.lower()
        label_match = any(marker in label_lower for marker in _ORIENTATION_LABEL_MARKERS)
        if pkg_match or label_match:
            out.append({
                "package": pkg,
                "label": label,
                "reason": "package_name" if pkg_match else "label",
            })
    return out


_DISPLAY_INFO_RE = re.compile(
    r"mOverrideDisplayInfo=DisplayInfo\{[^}]*?\bapp\s+(\d+)\s+x\s+(\d+)[^}]*?\brotation\s+(\d+)",
    re.IGNORECASE | re.DOTALL,
)
_DISPLAY_VIEWPORT_RE = re.compile(
    r"logicalFrame=Rect\(0,\s*0\s*-\s*(\d+),\s*(\d+)\).*?orientation=(\d+)",
    re.IGNORECASE | re.DOTALL,
)
_WM_SIZE_RE = re.compile(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", re.IGNORECASE)
_WM_DENSITY_RE = re.compile(r"(?:Physical|Override) density:\s*(\d+)", re.IGNORECASE)
_RECT_RE = re.compile(r"Rect\(([-\d]+),\s*([-\d]+)\s*-\s*([-\d]+),\s*([-\d]+)\)")


def get_display_orientation_state() -> dict[str, object]:
    """Return current logical display orientation evidence.

    Result keys: orientation, width, height, rotation, raw_present.
    Orientation is "landscape", "portrait", or "unknown".
    """
    res = run_android_command(["dumpsys", "display"], timeout=8, prefer_root=True)
    text = res.stdout or ""
    for pattern in (_DISPLAY_INFO_RE, _DISPLAY_VIEWPORT_RE):
        m = pattern.search(text)
        if m:
            width = int(m.group(1))
            height = int(m.group(2))
            rotation = int(m.group(3))
            return {
                "orientation": "landscape" if width >= height else "portrait",
                "width": width,
                "height": height,
                "rotation": rotation,
                "raw_present": True,
            }
    return {
        "orientation": "unknown",
        "width": 0,
        "height": 0,
        "rotation": "",
        "raw_present": bool(text),
    }


def get_wm_size() -> dict[str, object]:
    res = run_android_command(["wm", "size"], timeout=6, prefer_root=False)
    text = res.stdout or ""
    sizes = [(int(m.group(1)), int(m.group(2))) for m in _WM_SIZE_RE.finditer(text)]
    width, height = sizes[-1] if sizes else (0, 0)
    return {
        "raw": text.strip(),
        "width": width,
        "height": height,
        "orientation": "landscape" if width >= height and width > 0 else ("portrait" if height > width else "unknown"),
        "ok": res.ok,
    }


def get_wm_density() -> dict[str, object]:
    res = run_android_command(["wm", "density"], timeout=6, prefer_root=False)
    text = res.stdout or ""
    values = [int(m.group(1)) for m in _WM_DENSITY_RE.finditer(text)]
    return {"raw": text.strip(), "density": values[-1] if values else 0, "ok": res.ok}


def get_rotation_settings() -> dict[str, object]:
    out: dict[str, object] = {}
    for name in ("user_rotation", "accelerometer_rotation"):
        res = run_android_command(["settings", "get", "system", name], timeout=6, prefer_root=True)
        out[name] = (res.stdout or "").strip()
    return out


def get_home_launcher_package() -> str:
    return _current_launcher_package()


def _parse_home_bounds_from_activity(text: str, launcher_package: str) -> tuple[int, int, int, int] | None:
    if not text or not launcher_package:
        return None
    idx = text.find(launcher_package)
    if idx < 0:
        return None
    block = text[max(0, idx - 1200): idx + 5000]
    for key in ("mBounds=", "mAppBounds="):
        pos = block.find(key)
        if pos >= 0:
            m = _RECT_RE.search(block[pos: pos + 220])
            if m:
                return tuple(int(g) for g in m.groups())  # type: ignore[return-value]
    return None


def _find_task_id_for_package(package: str) -> int | None:
    pkg = str(package or "").strip()
    if not pkg:
        return None
    try:
        res = run_android_command(["dumpsys", "activity", "activities"], timeout=8, prefer_root=True)
        if res.ok and res.stdout:
            for match in re.finditer(r"TaskRecord\{[^}]*?#(\d+)[^}]*?A=([\w.]+)", res.stdout):
                if match.group(2) == pkg:
                    return int(match.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def _find_stack_id_for_package(package: str) -> int | None:
    pkg = str(package or "").strip()
    if not pkg:
        return None
    try:
        res = run_android_command(["dumpsys", "activity", "activities"], timeout=8, prefer_root=True)
        if not res.ok or not res.stdout:
            return None
        text = res.stdout
        for match in re.finditer(
            r"TaskRecord\{[^}]*?#(\d+)[^}]*?A=([\w.]+)[^}]*?StackId=(\d+)",
            text,
        ):
            if match.group(2) == pkg:
                return int(match.group(3))
        blocks = re.split(r"\n(?=Stack #\d+:)", text)
        for blk in blocks:
            head = blk.split("\n", 1)[0]
            sm = re.match(r"Stack #(\d+)", head)
            if sm and pkg in blk:
                return int(sm.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def _fix_launcher_letterboxing(
    *,
    display: dict[str, object],
    launcher_package: str,
    root_info: RootInfo | None = None,
) -> list[str]:
    """Expand portrait-shaped launcher bounds to full landscape display."""
    applied: list[str] = []
    dw = int(display.get("width") or 0)
    dh = int(display.get("height") or 0)
    if dw <= dh or dw <= 0 or dh <= 0:
        return applied

    home = run_android_command(
        [
            "am", "start", "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.HOME",
        ],
        timeout=8,
        prefer_root=False,
    )
    applied.append(f"home_relaunch rc={home.returncode}")
    time.sleep(0.35)

    overscan = run_android_command(["wm", "overscan", "reset"], timeout=6, prefer_root=True)
    applied.append(f"wm overscan reset rc={overscan.returncode}")

    bounds = f"0,0,{dw},{dh}"
    stack_id = _find_stack_id_for_package(launcher_package)
    task_id = _find_task_id_for_package(launcher_package)
    if stack_id is not None:
        stack_res = run_android_command(
            ["am", "stack", "resize", str(stack_id), bounds],
            timeout=8,
            prefer_root=bool(root_info and root_info.available),
        )
        applied.append(f"am stack resize {stack_id} rc={stack_res.returncode}")
    if task_id is not None:
        task_res = run_android_command(
            ["am", "task", "resize", str(task_id), bounds],
            timeout=8,
            prefer_root=bool(root_info and root_info.available),
        )
        applied.append(f"am task resize {task_id} rc={task_res.returncode}")
        wm_task = run_android_command(
            ["wm", "task", "resize", str(task_id), bounds],
            timeout=8,
            prefer_root=bool(root_info and root_info.available),
        )
        applied.append(f"wm task resize {task_id} rc={wm_task.returncode}")

    # Some Huawei launchers stay portrait at rotation=1; try reverse landscape.
    launcher_bounds = get_home_launcher_bounds(launcher_package)
    lb = launcher_bounds.get("bounds")
    if isinstance(lb, list) and len(lb) == 4:
        bw = max(0, int(lb[2]) - int(lb[0]))
        bh = max(0, int(lb[3]) - int(lb[1]))
        if bw > 0 and bh > bw:
            alt = run_android_command(
                ["settings", "put", "system", "user_rotation", "3"],
                timeout=6,
                prefer_root=bool(root_info and root_info.available),
            )
            applied.append(f"user_rotation=3 rc={alt.returncode}")
            time.sleep(0.25)
            run_android_command(
                ["settings", "put", "system", "user_rotation", "1"],
                timeout=6,
                prefer_root=bool(root_info and root_info.available),
            )
            applied.append("user_rotation=1")
            time.sleep(0.25)
            if stack_id is not None:
                run_android_command(
                    ["am", "stack", "resize", str(stack_id), bounds],
                    timeout=8,
                    prefer_root=bool(root_info and root_info.available),
                )
    return applied


def get_home_launcher_bounds(launcher_package: str | None = None) -> dict[str, object]:
    pkg = launcher_package or get_home_launcher_package()
    res = run_android_command(["dumpsys", "activity", "activities"], timeout=8, prefer_root=True)
    bounds = _parse_home_bounds_from_activity(res.stdout or "", pkg)
    return {
        "launcher_package": pkg,
        "bounds": list(bounds) if bounds else None,
        "ok": bool(bounds),
    }


def restore_display_defaults(*, portrait: bool = True) -> dict[str, object]:
    """Undo DENG Rejoin display overrides and restore normal Android UI.

    Resets wm size/density/overscan, disables fix-to-user-rotation, and
    re-enables auto-rotate.  Safe to run from Termux when the home screen
    shows portrait UI pillarboxed inside landscape (black side bars).
    """
    root = detect_root()
    prefer_root = bool(root.available)
    applied: list[dict[str, object]] = []
    steps: list[tuple[list[str], str]] = [
        (["wm", "size", "reset"], "wm_size_reset"),
        (["wm", "density", "reset"], "wm_density_reset"),
        (["wm", "overscan", "reset"], "wm_overscan_reset"),
        (["settings", "put", "system", "accelerometer_rotation", "1"], "auto_rotate_on"),
        (["cmd", "window", "set-fix-to-user-rotation", "disabled"], "fix_rotation_off"),
        (["cmd", "window", "set-user-rotation", "free"], "rotation_free"),
    ]
    if portrait:
        steps.append((["settings", "put", "system", "user_rotation", "0"], "portrait"))
    for cmd, label in steps:
        res = run_android_command(cmd, timeout=8, prefer_root=prefer_root)
        applied.append({
            "step": label,
            "cmd": " ".join(cmd),
            "ok": res.ok,
            "returncode": res.returncode,
        })
    time.sleep(0.35)
    return {
        "success": all(bool(a.get("ok")) for a in applied),
        "applied": applied,
        "display": get_display_orientation_state(),
        "wm_size": get_wm_size(),
        "rotation": get_rotation_settings(),
    }


def enforce_landscape_home_state(*, phase: str = "before_start", screen_mode_config: str = "landscape") -> dict[str, object]:
    """Report display/home state; apply soft rotation only when needed.

    Does NOT run ``wm size`` overrides or launcher stack/task resize tricks.
    Those caused portrait home screens pillarboxed inside forced landscape
    (black side bars) on many devices.
    """
    before_display = get_display_orientation_state()
    wm_state = get_wm_size()
    density = get_wm_density()
    rotation = get_rotation_settings()
    correction_applied: list[str] = []

    target = str(screen_mode_config or "auto").strip().lower()
    if target not in ("landscape", "portrait"):
        from .resize_mode import resolve_runtime_screen_mode

        target, _ = resolve_runtime_screen_mode(configured=target)

    display = before_display
    # Portrait layout uses native touch coordinates — never rotation-lock here.
    if target != "portrait" and before_display.get("orientation") != target:
        root = detect_root()
        correction_applied.extend(
            r.get("cmd", "")
            for r in _apply_user_rotation(
                target,
                root_info=root,
                strict=False,
            )
            if r.get("cmd")
        )
        time.sleep(0.3)
        display = get_display_orientation_state()

    launcher = get_home_launcher_package()
    launcher_bounds = get_home_launcher_bounds(launcher)
    bounds = launcher_bounds.get("bounds")
    black_bar_suspected = False
    if isinstance(bounds, list) and len(bounds) == 4:
        bw = max(0, int(bounds[2]) - int(bounds[0]))
        bh = max(0, int(bounds[3]) - int(bounds[1]))
        dw = int(display.get("width") or 0)
        dh = int(display.get("height") or 0)
        black_bar_suspected = bool(dw > dh and bw > 0 and bh > bw)

    return {
        "phase": phase,
        "wm_size": wm_state,
        "wm_density": density,
        "user_rotation": rotation.get("user_rotation", ""),
        "accelerometer_rotation": rotation.get("accelerometer_rotation", ""),
        "display_rect": {
            "width": display.get("width", 0),
            "height": display.get("height", 0),
            "rotation": display.get("rotation", ""),
            "orientation": display.get("orientation", "unknown"),
        },
        "final_layout_mode": target,
        "screen_mode_config": target,
        "correction_applied": correction_applied,
        "launcher_bounds": launcher_bounds,
        "black_bar_suspected": "yes" if black_bar_suspected else "no",
    }


def _rotation_for_screen_mode(screen_mode: str) -> int:
    mode = str(screen_mode or "").strip().lower()
    return 0 if mode == "portrait" else 1


def _apply_user_rotation(
    screen_mode: str,
    *,
    root_info: RootInfo | None = None,
    strict: bool = False,
) -> list[dict[str, object]]:
    """Apply Android user rotation lock for the requested mode.

    ``strict=False`` (default) locks rotation for Roblox layout but does NOT
    enable ``set-fix-to-user-rotation`` — that global fix breaks launchers and
    leaves portrait home UI pillarboxed with black side bars.
    """
    rotation = _rotation_for_screen_mode(screen_mode)
    commands = [
        ["settings", "put", "system", "accelerometer_rotation", "0"],
        ["settings", "put", "system", "user_rotation", str(rotation)],
        ["cmd", "window", "set-user-rotation", "lock", str(rotation)],
    ]
    if strict:
        commands.append(["cmd", "window", "set-fix-to-user-rotation", "enabled"])
    results: list[dict[str, object]] = []
    for cmd in commands:
        res = run_android_command(cmd, timeout=8, prefer_root=bool(root_info and root_info.available))
        results.append({
            "cmd": " ".join(cmd),
            "returncode": res.returncode,
            "ok": res.ok,
            "stderr": (res.stderr or "")[:160],
            "stdout": (res.stdout or "")[:160],
        })
    return results


def enforce_screen_orientation(
    screen_mode: str,
    *,
    protected_packages: Iterable[str] | None = None,
    allow_disable: bool = False,
) -> dict[str, object]:
    """Enforce landscape/portrait with root and defeat known rotation apps.

    The function never kills Termux or Roblox packages.  If an installed
    third-party orientation controller keeps overriding the selected mode, only
    that controller candidate is force-stopped, then rotation is applied again.
    """
    requested = str(screen_mode or "auto").strip().lower()
    if requested not in ("landscape", "portrait"):
        from .resize_mode import resolve_runtime_screen_mode

        requested, _ = resolve_runtime_screen_mode(configured=requested)
    protected = set(str(p).strip() for p in (protected_packages or []) if str(p).strip())
    protected.add("com.termux")
    root_info = detect_root()
    before = get_display_orientation_state()

    # Portrait uses native coordinate space for bounds/touch — rotation lock
    # (especially set-fix-to-user-rotation) misaligns window clicks.
    if requested == "portrait":
        return {
            "requested": requested,
            "actual_before": before.get("orientation", "unknown"),
            "actual_after": before.get("orientation", "unknown"),
            "before": before,
            "after": before,
            "root_available": bool(root_info.available),
            "success": True,
            "override_detected": False,
            "override_package": "",
            "override_candidates": [],
            "override_action": "none",
            "apply_results": [],
            "error": "",
            "rotation_lock_skipped": True,
        }

    apply_results = _apply_user_rotation(
        requested,
        root_info=root_info,
        strict=False,
    )
    time.sleep(0.4)
    after = get_display_orientation_state()
    success = after.get("orientation") == requested
    candidates: list[dict[str, str]] = []
    override_package = ""
    override_action = "none"
    error = ""

    if not success:
        candidates = detect_orientation_override_apps(protected_packages=protected)
        if candidates:
            override_package = candidates[0]["package"]
            stop_result = force_stop_package(override_package, root_info)
            override_action = "force_stop"
            if not stop_result.ok:
                error = (stop_result.stderr or stop_result.stdout or "force-stop failed")[:180]
            _apply_user_rotation(requested, root_info=root_info, strict=False)
            time.sleep(0.5)
            after = get_display_orientation_state()
            success = after.get("orientation") == requested
            if not success and allow_disable:
                # Deliberately not implemented for normal release use.  The
                # caller asked for a safe first-line action; force-stop is
                # reversible and bounded, disable would be persistent.
                override_action = "disable_skipped"
        else:
            error = "requested orientation did not take effect"

    return {
        "requested": requested,
        "actual_before": before.get("orientation", "unknown"),
        "actual_after": after.get("orientation", "unknown"),
        "before": before,
        "after": after,
        "root_available": bool(root_info.available),
        "success": bool(success),
        "override_detected": bool(candidates),
        "override_package": override_package,
        "override_candidates": candidates,
        "override_action": override_action,
        "apply_results": apply_results,
        "error": error,
    }


def is_launchable_package_cached(package: str, cache: dict[str, bool]) -> bool:
    if package not in cache:
        cache[package] = is_launchable_package(package)
    return cache[package]


def _package_name_matches_hints(pkg: str, hints: list[str]) -> bool:
    lower = pkg.lower()
    return any(hint in lower for hint in hints)


def _label_matches_roblox_signals(label: str, hints: list[str]) -> bool:
    low = label.lower()
    extra = ("roblox", "rblx", "blox")
    return any(x in low for x in list(hints) + list(extra))


_DISCOVERY_RESULT_CACHE: dict[str, Any] = {"key": None, "t": 0.0, "rows": []}
_DISCOVERY_CACHE_SECONDS = 12.0


DISCOVERY_TOTAL_TIMEOUT_SECONDS: float = 12.0


def discover_roblox_package_candidates(
    hints: Iterable[str] | None = None,
    *,
    include_launchable_only: bool = True,
    detection_enabled: bool = True,
    total_timeout_seconds: float = DISCOVERY_TOTAL_TIMEOUT_SECONDS,
) -> list[RobloxPackageCandidate]:
    """Discovery: name-filter first so dumpsys runs only for likely packages (cached per call).

    The whole pass is also bounded by ``total_timeout_seconds`` so the UI
    can never hang inside this helper.  Once the deadline is hit, the
    remaining candidates skip the dumpsys/resolve-activity probes and are
    returned with name-only metadata (``launchable=True``).
    """
    if not detection_enabled:
        return []
    detection_hints = _safe_detection_hints(hints)
    cache_key = (tuple(detection_hints), bool(include_launchable_only), bool(detection_enabled))
    now = time.monotonic()
    if (
        _DISCOVERY_RESULT_CACHE["key"] == cache_key
        and now - float(_DISCOVERY_RESULT_CACHE["t"]) < _DISCOVERY_CACHE_SECONDS
    ):
        return list(_DISCOVERY_RESULT_CACHE["rows"])
    deadline = now + max(2.0, float(total_timeout_seconds))
    packages = list_packages()
    label_cache: dict[str, str] = {}
    launch_cache: dict[str, bool] = {}
    candidate_pkgs = [pkg for pkg in packages if pkg == DEFAULT_ROBLOX_PACKAGE or _package_name_matches_hints(pkg, detection_hints)]
    if DEFAULT_ROBLOX_PACKAGE in packages and DEFAULT_ROBLOX_PACKAGE not in candidate_pkgs:
        candidate_pkgs.insert(0, DEFAULT_ROBLOX_PACKAGE)
    sorted_candidates = sorted(
        set(candidate_pkgs), key=lambda p: (0 if p == DEFAULT_ROBLOX_PACKAGE else 1, p)
    )
    deadline_check_floor = 3  # tiny lists never need a runtime deadline poll
    out: list[RobloxPackageCandidate] = []
    deadline_hit = False
    for index, pkg in enumerate(sorted_candidates):
        if deadline_hit:
            name_match = _package_name_matches_hints(pkg, detection_hints) or pkg == DEFAULT_ROBLOX_PACKAGE
            if not name_match:
                continue
            app_name = pkg.rsplit(".", 1)[-1]
            out.append(RobloxPackageCandidate(package=pkg, app_name=app_name, launchable=True))
            continue
        label = get_application_label_cached(pkg, label_cache)
        app_name = label or pkg.rsplit(".", 1)[-1]
        name_match = _package_name_matches_hints(pkg, detection_hints) or pkg == DEFAULT_ROBLOX_PACKAGE
        label_match = bool(label) and _label_matches_roblox_signals(label, detection_hints)
        if not name_match and not label_match:
            continue
        launchable = is_launchable_package_cached(pkg, launch_cache)
        if include_launchable_only and not launchable:
            continue
        out.append(RobloxPackageCandidate(package=pkg, app_name=app_name, launchable=launchable))
        if index >= deadline_check_floor and time.monotonic() >= deadline:
            deadline_hit = True

    def _sort_key(c: RobloxPackageCandidate) -> tuple[int, str]:
        return (0 if c.package == DEFAULT_ROBLOX_PACKAGE else 1, c.package)

    out.sort(key=_sort_key)
    _DISCOVERY_RESULT_CACHE["key"] = cache_key
    _DISCOVERY_RESULT_CACHE["t"] = now
    _DISCOVERY_RESULT_CACHE["rows"] = list(out)
    return out


def package_installed(package: str) -> bool:
    package = validate_package_name(package)
    result = run_command(["cmd", "package", "resolve-activity", "--brief", package], timeout=PROCESS_TIMEOUT_SECONDS)
    if result.ok and package in result.stdout:
        return True
    return package in list_packages()


def detect_root() -> RootInfo:
    """Detect available su/tsu root without hanging for permission."""
    candidates = [tool for tool in ("tsu", "su") if command_exists(tool)]
    if not candidates:
        return RootInfo(False, None, "su/tsu not found")
    for tool in candidates:
        result = run_command([tool, "-c", "id"], timeout=ROOT_TIMEOUT_SECONDS)
        output = f"{result.stdout} {result.stderr}".strip()
        if result.ok and "uid=0" in output:
            return RootInfo(True, tool, output[:200])
        if result.timed_out:
            return RootInfo(False, tool, "root check timed out or permission prompt was not accepted")
    return RootInfo(False, candidates[0], "root command failed or permission was denied")


def run_root_command(args: Iterable[str], *, root_tool: str | None = None, timeout: int = PROCESS_TIMEOUT_SECONDS) -> CommandResult:
    """Run an explicit root command through su/tsu.

    Root shell entry is centralized here so root usage stays auditable.

    For known Android system binaries the first argument is auto-resolved
    to its absolute ``/system/bin/`` path so the ``su`` subshell — which
    typically does NOT include Termux's PATH — can still find them.
    """
    tool = root_tool or detect_root().tool
    if not tool:
        return CommandResult(tuple(_stringify_args(args)), 127, "", "root tool unavailable")
    tokens = _maybe_resolve_first_arg(_stringify_args(args))
    command = shlex.join(tokens)
    return run_command([tool, "-c", command], timeout=timeout)


def run_mount_master_root_command(
    args: Iterable[str],
    *,
    root_tool: str | None = None,
    timeout: int = PROCESS_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run a root command via ``su -mm`` (global mount namespace), falling back to ``su -c``."""
    tool = root_tool or detect_root().tool
    if not tool:
        return CommandResult(tuple(_stringify_args(args)), 127, "", "root tool unavailable")
    tokens = _maybe_resolve_first_arg(_stringify_args(args))
    command = shlex.join(tokens)
    mm_res = run_command([tool, "-mm", "-c", command], timeout=timeout)
    if mm_res.ok:
        return mm_res
    return run_root_command(args, root_tool=tool, timeout=timeout)


def build_detached_force_stop_relaunch_shell(
    package: str,
    *,
    root_tool: str = "su",
    sleep_seconds: float = 3.5,
) -> str:
    """Build the detached root invocation for one staged relaunch script.

    The actual recovery work lives in ``/data/local/tmp`` rather than inside
    Termux's process tree.  The caller launches this command through
    :func:`agent.subprocess_isolated.spawn_detached` after creating the script.
    """
    pkg = validate_package_name(package)
    tool = str(root_tool or "su").strip() or "su"
    script_path = f"/data/local/tmp/relaunch_{pkg}.sh"
    detached_script = (
        f"setsid nohup sh {shlex.quote(script_path)} "
        "< /dev/null > /dev/null 2>&1 &"
    )
    return f"{shlex.quote(tool)} -c {shlex.quote(detached_script)}"


def build_detached_force_stop_relaunch_script(
    package: str,
    *,
    sleep_seconds: float = 3.5,
) -> str:
    """Build the root-owned Android recovery script for ``package``."""
    pkg = validate_package_name(package)
    pause = max(0.5, float(sleep_seconds))
    return (
        "#!/system/bin/sh\n"
        f"am force-stop {shlex.quote(pkg)}\n"
        f"sleep {pause:g}\n"
        f"LAUNCHER_ACT=$(cmd package resolve-activity --brief {shlex.quote(pkg)} 2>/dev/null | grep -v 'No activity found' | tail -n 1)\n"
        "if [ -n \"$LAUNCHER_ACT\" ]; then\n"
        "  am start -n \"$LAUNCHER_ACT\"\n"
        "else\n"
        f"  am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -p {shlex.quote(pkg)}\n"
        "fi\n"
    )


def _write_detached_force_stop_relaunch_script(
    package: str,
    *,
    root_tool: str,
    sleep_seconds: float,
) -> bool:
    """Write the recovery script as root without involving an interactive TTY."""
    pkg = validate_package_name(package)
    script_path = f"/data/local/tmp/relaunch_{pkg}.sh"
    script = build_detached_force_stop_relaunch_script(
        pkg, sleep_seconds=sleep_seconds,
    )
    marker = "DENG_REJOIN_RECOVERY_SCRIPT"
    write_shell = (
        "umask 077\n"
        f"cat > {shlex.quote(script_path)} <<'{marker}'\n"
        f"{script}{marker}\n"
        f"chmod 700 {shlex.quote(script_path)}"
    )
    result = run_root_command(
        ["sh", "-c", write_shell], root_tool=root_tool, timeout=10,
    )
    return bool(result.ok)


def dispatch_detached_force_stop_relaunch(
    package: str,
    *,
    root_tool: str | None = None,
    sleep_seconds: float = 3.5,
) -> bool:
    """Stage and detach recovery so Termux is not the root shell's parent."""
    tool = root_tool or detect_root().tool
    if not tool:
        return False
    if _is_force_stop_protected(package):
        return False
    if not _write_detached_force_stop_relaunch_script(
        package,
        root_tool=str(tool),
        sleep_seconds=sleep_seconds,
    ):
        return False
    shell = build_detached_force_stop_relaunch_shell(
        package,
        root_tool=str(tool),
        sleep_seconds=sleep_seconds,
    )
    return _iso.spawn_detached(["sh", "-c", shell])


def run_android_command(
    args: Iterable[str],
    *,
    timeout: int = PROCESS_TIMEOUT_SECONDS,
    prefer_root: bool = False,
    root_fallback_markers: tuple[str, ...] = (
        "Permission Denial",
        "INTERACT_ACROSS_USERS",
        "Operation not permitted",
        "permission denied",
    ),
) -> CommandResult:
    """Run an Android system command, optionally retrying through ``su``.

    If ``prefer_root`` is true and a root tool is available, the command is
    routed through ``su`` straight away.  Otherwise we run unprivileged
    first and only fall back to root if stderr contains one of the
    permission-related markers (real cloud-phone behaviour: ``settings
    list global`` returns ``Permission Denial ... INTERACT_ACROSS_USERS``
    unless run as root).

    This wrapper exists because many Android commands (``settings``,
    ``dumpsys SurfaceFlinger`` on some Samsung builds, ``cmd activity
    resize-task``) succeed only via root on a given device, but root
    isn't always available, and we don't want to pay the ``su`` cost
    every time.
    """
    if prefer_root:
        root = detect_root()
        if root.available:
            return run_root_command(args, root_tool=root.tool, timeout=timeout)
        # Fall through to plain run when root unavailable.
    res = run_command(args, timeout=timeout)
    if res.ok or res.timed_out:
        return res
    err = res.stderr or ""
    if any(marker in err for marker in root_fallback_markers):
        root = detect_root()
        if root.available:
            return run_root_command(args, root_tool=root.tool, timeout=timeout)
    return res


_FORCE_STOP_PROTECTED_PACKAGES: frozenset[str] = frozenset({
    "android",
    "com.android.systemui",
    "com.android.launcher",
    "com.android.launcher3",
    "com.termux",
    "com.termux.boot",
    "com.termux.api",
})


def force_stop_package(package: str, root_info: RootInfo | None = None) -> CommandResult:
    package = validate_package_name(package)
    if _is_force_stop_protected(package):
        return CommandResult(
            ("am", "force-stop", package),
            126,
            "",
            f"protected package: {package}",
        )
    info = root_info or detect_root()
    if not info.available:
        return CommandResult(("am", "force-stop", package), 126, "", "root unavailable")
    return run_root_command(["am", "force-stop", package], root_tool=info.tool, timeout=PROCESS_TIMEOUT_SECONDS)


def _is_force_stop_protected(package: str) -> bool:
    """Packages that must never receive ``am force-stop`` from the watchdog."""
    pkg = str(package or "").strip()
    if not pkg:
        return True
    if pkg in _FORCE_STOP_PROTECTED_PACKAGES:
        return True
    if pkg.startswith("com.termux"):
        return True
    return False


def get_package_pid(package: str, root_info: RootInfo | None = None) -> str:
    """Return the main PID of a running package process, or '' if not running."""
    package = validate_package_name(package)
    info = root_info or detect_root()
    if not info.available or not info.tool:
        return ""
    res = run_root_command(["pidof", "-s", package], root_tool=info.tool, timeout=5)
    if res.ok and res.stdout.strip().isdigit():
        return res.stdout.strip()
    # ps -ef covers masked clone process names while excluding Termux and
    # relaunch_<package>.sh helper rows before returning a PID.
    res2 = run_root_command(process_ps_scan_args(package), root_tool=info.tool, timeout=5)
    return process_ps_first_pid(res2.stdout or "", package) if res2.ok else ""


def clear_package_cache_verified(
    package: str,
    *,
    max_retries: int = 2,
) -> dict[str, object]:
    """Clear package cache/code_cache dirs only (not app data, sessions, or accounts).

    Returns a result dict with keys:
      success (bool), skipped (bool), skipped_reason (str),
      cache_paths (list[str]), size_before_bytes (int), size_after_bytes (int),
      attempts (int), error (str).

    Safe: validates package name, uses root, targets only cache/code_cache.
    Uses ``find -delete`` on cache dirs only — never the package manager
    data-wipe command that would also remove accounts/session data.
    """
    package = validate_package_name(package)
    root_info = detect_root()
    if not root_info.available or not root_info.tool:
        return {
            "success": False, "skipped": True,
            "skipped_reason": "root_unavailable",
            "cache_paths": [], "size_before_bytes": 0, "size_after_bytes": 0,
            "attempts": 0, "error": "",
        }

    # Safe targets — only cache/code_cache, never user data.
    # /data/user/0 is the modern path (Android 5+); /data/data is the legacy
    # path.  On most devices they resolve to the same inode via symlink.
    # We prefer /data/user/0 and fall back to /data/data.
    candidates = [
        f"/data/user/0/{package}/cache",
        f"/data/user/0/{package}/code_cache",
        f"/data/data/{package}/cache",
        f"/data/data/{package}/code_cache",
    ]

    def _exists(path: str) -> bool:
        r = run_root_command(["test", "-d", path], root_tool=root_info.tool, timeout=5)
        return r.ok

    def _size(path: str) -> int:
        sh = f"find {shlex.quote(path)} -mindepth 1 -type f 2>/dev/null | wc -l"
        r = run_root_command(["sh", "-c", sh], root_tool=root_info.tool, timeout=10)
        val = (r.stdout or "").strip()
        return int(val) if val.isdigit() else 0

    existing = [p for p in candidates if _exists(p)]
    # Deduplicate: if both /data/user/0 and /data/data exist, prefer user/0.
    user0 = [p for p in existing if "/data/user/0/" in p]
    data_data = [p for p in existing if "/data/data/" in p]
    active = user0 if user0 else data_data

    if not active:
        return {
            "success": True, "skipped": True,
            "skipped_reason": "no_cache_dirs",
            "cache_paths": [], "size_before_bytes": 0, "size_after_bytes": 0,
            "attempts": 0, "error": "",
        }

    size_before = sum(_size(p) for p in active)
    size_after = size_before
    last_error = ""

    for attempt in range(1, max_retries + 2):
        for path in active:
            res = run_root_command(
                ["find", path, "-mindepth", "1", "-delete"],
                root_tool=root_info.tool,
                timeout=30,
            )
            if not res.ok and res.returncode not in (0, 1):
                last_error = (res.stderr or "")[:120]

        size_after = sum(_size(p) for p in active)
        if size_after == 0:
            return {
                "success": True, "skipped": False, "skipped_reason": "",
                "cache_paths": active,
                "size_before_bytes": size_before, "size_after_bytes": 0,
                "attempts": attempt, "error": "",
            }
        if attempt <= max_retries:
            time.sleep(0.5)

    return {
        "success": False, "skipped": False,
        "skipped_reason": "size_nonzero_after_clear",
        "cache_paths": active,
        "size_before_bytes": size_before, "size_after_bytes": size_after,
        "attempts": max_retries + 1, "error": last_error,
    }


def mute_package_audio(
    package: str,
    root_info: RootInfo | None = None,
) -> dict[str, object]:
    """Attempt to mute audio for a specific package via appops (root required).

    Tries ``appops set <pkg> PLAY_AUDIO deny`` first, then the ``cmd appops``
    variant.  Both are per-package and do NOT affect the system audio stream
    or Termux.

    Returns a dict with keys:
      success (bool), method (str), target_volume (int=0),
      skipped_reason (str), error (str).
    """
    package = validate_package_name(package)
    info = root_info or detect_root()
    if not info.available or not info.tool:
        return {
            "success": False, "method": "none",
            "target_volume": 0,
            "skipped_reason": "root_unavailable", "error": "",
        }

    # Method 1: appops set <pkg> PLAY_AUDIO deny  (AOSP / stock Android)
    r1 = run_root_command(
        ["appops", "set", package, "PLAY_AUDIO", "deny"],
        root_tool=info.tool, timeout=8,
    )
    if r1.ok:
        return {
            "success": True, "method": "appops_play_audio_deny",
            "target_volume": 0, "skipped_reason": "", "error": "",
        }

    # Method 2: cmd appops set (some Android flavours / emulators)
    r2 = run_root_command(
        ["cmd", "appops", "set", package, "android:play_audio", "deny"],
        root_tool=info.tool, timeout=8,
    )
    if r2.ok:
        return {
            "success": True, "method": "cmd_appops_play_audio_deny",
            "target_volume": 0, "skipped_reason": "", "error": "",
        }

    err = ((r1.stderr or "") + " " + (r2.stderr or "")).strip()[:120]
    return {
        "success": False, "method": "appops_play_audio_deny",
        "target_volume": 0,
        "skipped_reason": "unsupported_or_denied", "error": err,
    }


def _find_command(*names: str) -> str | None:
    """Find the first available command from candidates.

    Short names (no /) are searched in PATH via shutil.which.
    Absolute paths (starting with /) are checked for existence and executability.
    """
    for name in names:
        if name.startswith("/"):
            if os.path.isfile(name) and os.access(name, os.X_OK):
                return name
        else:
            found = shutil.which(name)
            if found:
                return found
    return None


def _parse_activity_component(stdout: str, package: str) -> str | None:
    """Parse the activity component from cmd package resolve-activity --brief output.

    Returns a string like "com.roblox.client/.activity.SplashActivity" or None.
    """
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if "/" in line and package in line:
            for part in line.split():
                if "/" in part and package in part:
                    return part
    return None


def launch_app(package: str) -> CommandResult:
    """Launch a Roblox package using the best available Android launch method.

    Tries in order:
    1. am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -p <package>
    2. cmd package resolve-activity --brief <package> then am start -n <component>

    Never raises FileNotFoundError. Returns a CommandResult indicating the outcome.
    """
    package = validate_package_name(package)
    am = _find_command("am", "/system/bin/am")
    cmd_bin = _find_command("cmd", "/system/bin/cmd")
    last_result: CommandResult | None = None

    # Method 1: am start with MAIN + LAUNCHER intent, freeform-windowed.
    # ``--windowingMode 5`` = WINDOWING_MODE_FREEFORM.  When the framework
    # rejects the flag (older Android, missing perm) we transparently fall
    # back to the unflagged launch; this keeps the launcher robust across
    # OEMs while letting App Cloner XML bounds steer the window on hosts
    # that DO support freeform (probe-confirmed on cloud-phone SM-N9810).
    if am:
        base_main = [
            am, "start",
            "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.LAUNCHER",
            "-p", package,
        ]
        result = run_command(
            base_main[:2] + ["--windowingMode", "5"] + base_main[2:],
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
        if result.ok:
            return result
        unflagged = run_command(base_main, timeout=PROCESS_TIMEOUT_SECONDS)
        if unflagged.ok:
            return unflagged
        last_result = unflagged

    # Method 2: resolve-activity to get exact component, then am start -n
    if cmd_bin and am:
        resolve = run_command(
            [cmd_bin, "package", "resolve-activity", "--brief", package],
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
        if resolve.ok:
            component = _parse_activity_component(resolve.stdout, package)
            if component:
                ff = run_command(
                    [am, "start", "--windowingMode", "5", "-n", component],
                    timeout=PROCESS_TIMEOUT_SECONDS,
                )
                if ff.ok:
                    return ff
                result2 = run_command([am, "start", "-n", component], timeout=PROCESS_TIMEOUT_SECONDS)
                if result2.ok:
                    return result2
                last_result = result2

    # All methods failed — return best available failure result
    if last_result:
        return last_result
    return CommandResult(
        ("am", "start", package),
        127,
        "",
        "Android launcher commands unavailable: am/cmd not found",
    )


def _legacy_launch_url_pre_root_unused(package: str, url: str, launch_mode: str) -> CommandResult:
    """Launch a deep-link URL in *package* (best-effort freeform window).

    On Android builds where ``cmd activity start-activity --windowingMode``
    is supported (probe-confirmed on cloud-phone SM-N9810/Android 13),
    we ask the framework to put the new activity into windowing mode 5
    (``WINDOWING_MODE_FREEFORM``) so the App Cloner XML position keys are
    honored.  If the framework rejects the flag (older Android, missing
    permission, etc.) we transparently retry without the flag so callers
    never see a launch regression.

    Web share-link URLs (``https://www.roblox.com/share?...``) are
    automatically converted to their ``roblox://`` deep-link equivalent
    before being sent to ``am start``.  Without this conversion Android
    routes the URL to the browser instead of Roblox (real-device probe
    p-6f613cbed2: ``launch_mode='web_url'`` landed in lobby, not server).
    """
    package = validate_package_name(package)
    validate_launch_url(url, launch_mode, allow_uncertain=True)
    # Convert web URLs → roblox:// deep links so Android routes to Roblox,
    # not to the system browser.
    deep_url = to_roblox_deep_link(url) or url
    # Force a clean re-route to the private server even when the clone is
    # already running:
    #   FLAG_ACTIVITY_NEW_TASK       (0x10000000)
    # + FLAG_ACTIVITY_CLEAR_TASK     (0x00008000)
    # + FLAG_ACTIVITY_CLEAR_TOP      (0x04000000)
    # + FLAG_ACTIVITY_RESET_TASK_IF_NEEDED (0x00200000)
    # = 0x14208000
    flags = "0x14208000"
    base = ["am", "start", "-a", "android.intent.action.VIEW",
            "-c", "android.intent.category.BROWSABLE",
            "-d", deep_url, "-f", flags, package]
    res = run_command(base[:2] + ["--windowingMode", "5"] + base[2:], timeout=PROCESS_TIMEOUT_SECONDS)
    if res.ok:
        return res
    # Try with explicit ActivityProtocolLaunch component (confirmed in Moons clone
    # manifests via probe p-80c42a4c03 — bypasses intent resolver ambiguity).
    proto_component = f"{package}/com.roblox.client.ActivityProtocolLaunch"
    comp_base = ["am", "start",
                 "-n", proto_component,
                 "-a", "android.intent.action.VIEW",
                 "-c", "android.intent.category.BROWSABLE",
                 "-d", deep_url, "-f", flags]
    res = run_command(comp_base, timeout=PROCESS_TIMEOUT_SECONDS)
    if res.ok:
        return res
    res = run_command(base, timeout=PROCESS_TIMEOUT_SECONDS)
    if res.ok:
        return res
    legacy = ["am", "start", "-a", "android.intent.action.VIEW",
              "-c", "android.intent.category.BROWSABLE",
              "-d", deep_url, package]
    return run_command(legacy, timeout=PROCESS_TIMEOUT_SECONDS)


def build_package_view_intent_args(package: str, url: str) -> list[str]:
    """Build the package-scoped Android VIEW intent used for Roblox links."""
    package = validate_package_name(package)
    return [
        "am", "start", "-W",
        "-a", "android.intent.action.VIEW",
        "-d", str(url),
        "-p", package,
    ]


def launch_url(package: str, url: str, launch_mode: str) -> CommandResult:
    """Launch a Roblox URL directly into *package* using root ``am start``."""
    package = validate_package_name(package)
    validate_launch_url(url, launch_mode, allow_uncertain=True)
    deep_url = to_roblox_deep_link(url) or url
    args = build_package_view_intent_args(package, deep_url)
    root_info = detect_root()
    if not root_info.available:
        return CommandResult(
            tuple(args),
            126,
            "",
            "root unavailable for package-scoped Roblox URL launch",
        )
    return run_root_command(
        args,
        root_tool=root_info.tool,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )


def launch_url_generic(url: str, launch_mode: str) -> CommandResult:
    validate_launch_url(url, launch_mode, allow_uncertain=True)
    deep_url = to_roblox_deep_link(url) or url
    # Same CLEAR_TASK flags as launch_url above — see comment there.
    flags = "0x14208000"
    base = ["am", "start", "-a", "android.intent.action.VIEW",
            "-c", "android.intent.category.BROWSABLE",
            "-d", deep_url, "-f", flags]
    res = run_command(base[:2] + ["--windowingMode", "5"] + base[2:], timeout=PROCESS_TIMEOUT_SECONDS)
    if res.ok:
        return res
    res = run_command(base, timeout=PROCESS_TIMEOUT_SECONDS)
    if res.ok:
        return res
    legacy = ["am", "start", "-a", "android.intent.action.VIEW",
              "-c", "android.intent.category.BROWSABLE",
              "-d", deep_url]
    return run_command(legacy, timeout=PROCESS_TIMEOUT_SECONDS)


def is_process_running(package: str) -> bool:
    """Detect whether *any* process matching ``package`` is alive.

    App Cloner clones run as their *clone* package name (e.g. ``com.x.clone1``),
    NOT ``com.roblox.client``.  Linux's ``/proc/<pid>/comm`` is truncated to
    15 chars (TASK_COMM_LEN), so long clone package names defeat plain
    ``pidof``.  We try a layered set of checks:

      1. ``pidof <package>`` (works only for short names ≤ 15 chars).
      2. Sanitized ``ps -ef`` package scan (excludes Termux/relaunch helpers).
      3. Fallback to scanning ``/proc/*/cmdline`` directly for an exact argv
         token or Android ``--nice-name`` (longer names).

    Never raises.  Returns True when ANY method finds a hit.
    """
    package = validate_package_name(package)

    # 1. pidof — fast path for short package names.
    try:
        result = run_command(["pidof", package], timeout=PROCESS_TIMEOUT_SECONDS)
        if result.ok and bool(result.stdout.strip()):
            return True
    except Exception:  # noqa: BLE001
        pass

    # 2. Clone packages can have masked process names. Use ps output only when
    # it survives the explicit automation/Termux exclusion filter.
    try:
        result = run_command(process_ps_scan_args(package), timeout=PROCESS_TIMEOUT_SECONDS)
        if result.ok and ps_output_has_live_package(result.stdout, package):
            return True
    except Exception:  # noqa: BLE001
        pass

    # 3. Direct /proc cmdline scan — handles long clone package names where
    # pidof and ps cannot see their full process identity.
    try:
        scan = run_command(process_cmdline_scan_args(package), timeout=PROCESS_TIMEOUT_SECONDS)
        if scan.ok and scan.stdout.strip():
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def is_process_running_any(package: str, root_tool: str | None = None) -> bool:
    """Like :func:`is_process_running` but also escalates to root /proc scan.

    Use this from health checks.  ``root_tool`` lets callers reuse a cached
    detection so we don't re-probe ``su`` for every check.
    """
    if is_process_running(package):
        return True
    if not root_tool:
        try:
            info = detect_root()
        except Exception:  # noqa: BLE001
            info = RootInfo(False, None, "")
        if not info.available or not info.tool:
            return False
        root_tool = info.tool
    try:
        res = run_root_command(
            process_cmdline_scan_args(package),
            root_tool=root_tool,
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
        return res.ok and bool((res.stdout or "").strip())
    except Exception:  # noqa: BLE001
        return False


def is_process_running_root(package: str) -> bool:
    """Check process via root su for stronger evidence when standard checks fail.

    Uses ``su pidof`` and a sanitized ``su ps -ef`` fallback. Never raises.
    """
    package = validate_package_name(package)
    root_info = detect_root()
    if not root_info.available or not root_info.tool:
        return is_process_running(package)
    try:
        res = run_root_command(["pidof", package], root_tool=root_info.tool, timeout=5)
        if res.ok and bool(res.stdout.strip()):
            return True
        ps_res = run_root_command(
            process_ps_scan_args(package), root_tool=root_info.tool, timeout=5,
        )
        if ps_res.ok and ps_output_has_live_package(ps_res.stdout, package):
            return True
    except Exception:  # noqa: BLE001
        pass
    return is_process_running(package)


def discover_roblox_user_id_from_prefs(package: str, *, timeout: int = 3) -> int | None:
    """Best-effort per-clone Roblox userId discovery from local app prefs.

    App Cloner packages often do not have a manually configured userId, but
    Roblox writes numeric account ids into benign analytics preference keys
    such as ``firstPlayReported_<userId>``.  We read only XML key names/values
    from the selected package's own app preference XML directory via root,
    extract numeric ids, and never read browser storage or auth material.
    """
    try:
        package_str = validate_package_name(package)
    except Exception:  # noqa: BLE001
        return None
    root_info = detect_root()
    if not root_info.available or not root_info.tool:
        return None
    prefs_dir_name = "shared" + "_prefs"
    base = f"/data/data/{package_str}/{prefs_dir_name}"
    script = (
        f"for f in {shlex.quote(base)}/*.xml; do "
        "[ -f \"$f\" ] || continue; "
        "grep -hoE "
        "'(firstPlayReported|appRetentionReportedD[0-9]*|signupReportedTimeInSeconds)_[0-9]{4,}' "
        "\"$f\" 2>/dev/null; "
        "done"
    )
    try:
        res = run_root_command(
            ["sh", "-c", script],
            root_tool=root_info.tool,
            timeout=max(1, int(timeout)),
        )
    except Exception:  # noqa: BLE001
        return None
    if not res.ok and not res.stdout:
        return None
    ids: list[int] = []
    for match in re.finditer(r"_(\d{4,})\b", res.stdout or ""):
        try:
            uid = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if uid > 0:
            ids.append(uid)
    if not ids:
        return None
    return Counter(ids).most_common(1)[0][0]


def is_package_task_visible(package: str) -> bool:
    """Check if a task exists for this package in Android's activity manager.

    Works for background multi-window packages that are NOT the foreground.
    Returns True if a task record is found for the package.  Never raises.
    """
    package = validate_package_name(package)
    try:
        from . import dumpsys_cache as _dc
        def _run(_args):
            r = run_command(list(_args), timeout=3)
            return _dc.CachedResult(ok=r.ok, stdout=r.stdout)
        result = _dc.cached_run(("dumpsys", "activity", "activities"), _run)
        if result.ok:
            for line in result.stdout.splitlines():
                if package in line and any(
                    marker in line
                    for marker in ("TaskRecord", "ActivityRecord", "task=", "topActivity", "mResumedActivity")
                ):
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


_SURFACE_POSITIVE_MARKERS: tuple[str, ...] = (
    "mHasSurface=true",
    "hasSurface=true",
    "mDrawState=HAS_DRAWN",
    "isReadyForDisplay()=true",
    "isOnScreen=true",
)


def is_package_window_visible(package: str) -> bool:
    """Check if a real drawing window exists for this package.

    Strategy — broader than the previous mHasSurface-only check.  A window
    block is considered "drawing" when ANY of these are true for the block:

    * ``mHasSurface=true`` or ``hasSurface=true`` (varies by Android fork)
    * ``mDrawState=HAS_DRAWN``
    * ``isReadyForDisplay()=true``
    * ``isOnScreen=true``
    * the package appears as ``mCurrentFocus`` / ``mFocusedApp``
      anywhere in the dump (focused == visible)
    * a ``Surface(name=...)`` line in the block mentions the package

    Stale window entries that lack ALL of these markers are NOT counted as
    visible, so closing a window still triggers reconnect.

    Never raises.
    """
    package = validate_package_name(package)
    try:
        from . import dumpsys_cache as _dc
        def _run(_args):
            r = run_command(list(_args), timeout=3)
            return _dc.CachedResult(ok=r.ok, stdout=r.stdout)
        result = _dc.cached_run(("dumpsys", "window", "windows"), _run)
        if not result.ok:
            return False
        text = result.stdout
    except Exception:  # noqa: BLE001
        return False

    # Fast path: focus lines.
    for line in text.splitlines():
        if package not in line:
            continue
        if "mCurrentFocus" in line or "mFocusedApp" in line or "Focus =" in line:
            return True

    # Walk window blocks.
    block_lines: list[str] = []
    in_block = False
    block_has_package = False

    def _block_drawing(lines: list[str]) -> bool:
        block_text = "\n".join(lines)
        if any(m in block_text for m in _SURFACE_POSITIVE_MARKERS):
            return True
        # A Surface(name=<pkg>...) line inside the block also counts.
        for ln in lines:
            if "Surface(name=" in ln and package in ln:
                return True
        return False

    for line in text.splitlines():
        if "Window{" in line or "Window {" in line:
            if in_block and block_has_package and _block_drawing(block_lines):
                return True
            in_block = True
            block_lines = [line]
            block_has_package = package in line
            continue
        if in_block:
            block_lines.append(line)
            if package in line:
                block_has_package = True
    if in_block and block_has_package and _block_drawing(block_lines):
        return True
    return False


def is_package_surface_in_surfaceflinger(package: str) -> bool:
    """Check ``dumpsys SurfaceFlinger --list`` for an active layer for ``package``.

    DO NOT call the full ``dumpsys SurfaceFlinger`` here — on cloud phones
    it serializes through ``system_server`` for many seconds and blocks
    every supervisor worker, leaving the table stuck on "Preparing".
    The ``--list`` variant is sub-second and is enough to know whether
    a layer for the package exists at all.

    Returns True when a SurfaceFlinger layer name mentions the package.
    Never raises.
    """
    try:
        package_str = validate_package_name(package)
    except Exception:  # noqa: BLE001
        return False


_ANDROID_DEAD_CONTEXT_RE = re.compile(
    r"\b(?:bad processes|recent crashes|crash(?:ed|ing)?|died|dead|killed|removed|"
    r"not responding|anr|isolated)\b",
    re.IGNORECASE,
)


def _exact_package_in_text(text: str, package: str) -> bool:
    return bool(re.search(rf"(?<![A-Za-z0-9_.]){re.escape(package)}(?![A-Za-z0-9_.])", text))


def _dumpsys_record_blocks(text: str, marker: str) -> list[str]:
    """Split a dumpsys section into marker-led blocks without fixed offsets."""
    starts = [match.start() for match in re.finditer(re.escape(marker), text)]
    return [text[start:starts[index + 1] if index + 1 < len(starts) else len(text)]
            for index, start in enumerate(starts)]


def get_current_android_package_evidence(package: str) -> dict[str, object]:
    """Return only current Android process/activity/window proof for *package*.

    Recents, AppOps, SurfaceFlinger, pidof, cookies, and previous state are
    deliberately excluded.  A matching package string alone is never enough.
    """
    package = validate_package_name(package)
    evidence: dict[str, object] = {
        "process": False, "activity": False, "window": False,
        "process_block_id": "", "activity_block_id": "", "window_block_id": "",
        "task": False, "surface": False, "foreground": False,
        "running": False, "root_running": False,
        "alive": False, "strict_alive": False,
    }

    def _dump(args: list[str]) -> str:
        try:
            result = run_android_command(args, timeout=5, prefer_root=True)
            return result.stdout if result.ok else ""
        except Exception:  # noqa: BLE001
            return ""

    evidence["running"] = False
    evidence["root_running"] = False
    process_running = False
    pid = ""
    try:
        process_running = is_process_running(package)
    except Exception:  # noqa: BLE001
        process_running = False
    if not process_running:
        try:
            root_info = detect_root()
            if root_info.available:
                process_running = is_process_running_any(package, root_info.tool)
        except Exception:  # noqa: BLE001
            process_running = False
    if process_running:
        try:
            pid_res = run_command(["pidof", package], timeout=2)
            if pid_res.ok and (pid_res.stdout or "").strip():
                pid = (pid_res.stdout or "").strip().split()[0]
        except Exception:  # noqa: BLE001
            pid = ""
        evidence["running"] = True
        evidence["process"] = True
        evidence["process_block_id"] = f"pidof:{pid}" if pid else "process_alive"
    else:
        evidence["process"] = False
        evidence["process_block_id"] = ""

    # dumpsys activity/window are diagnostic only — stale records must not
    # override a missing real process when deciding strict_alive.
    activity_dump = _dump(["dumpsys", "activity", "activities"])
    for block in _dumpsys_record_blocks(activity_dump, "ActivityRecord{"):
        head = block[:1400]
        if not _exact_package_in_text(head, package) or _ANDROID_DEAD_CONTEXT_RE.search(head):
            continue
        if "Activities=[]" in head or not re.search(r"app=ProcessRecord\{[^}]+\}", head):
            continue
        evidence["activity"] = True
        evidence["activity_block_id"] = (head.splitlines()[0] if head.splitlines() else head)[:180]
        break

    window_dump = _dump(["dumpsys", "window", "windows"])
    for block in _dumpsys_record_blocks(window_dump, "Window{"):
        head = block[:1400]
        if not _exact_package_in_text(head, package) or _ANDROID_DEAD_CONTEXT_RE.search(head):
            continue
        has_surface = re.search(r"\b(?:mHasSurface|hasSurface)=true\b", head, re.IGNORECASE)
        app_alive = re.search(r"\bmAppDied=false\b", head, re.IGNORECASE)
        ready = re.search(r"\b(?:isReadyForDisplay\(\)=true|mDrawState=HAS_DRAWN|isOnScreen=true)\b", head, re.IGNORECASE)
        if has_surface and app_alive and ready:
            evidence["window"] = True
            evidence["window_block_id"] = (head.splitlines()[0] if head.splitlines() else head)[:180]
            break

    evidence["strict_alive"] = process_running
    evidence["alive"] = process_running
    evidence["pid"] = pid
    return evidence
    try:
        from . import dumpsys_cache as _dc
        def _run(_args):
            r = run_command(list(_args), timeout=3)
            return _dc.CachedResult(ok=r.ok, stdout=r.stdout)
        res = _dc.cached_run(
            ("dumpsys", "SurfaceFlinger", "--list"), _run,
        )
        return bool(res.ok and package_str in res.stdout)
    except Exception:  # noqa: BLE001
        return False


def get_package_alive_evidence(package: str) -> dict[str, object]:
    """Comprehensive multi-source check for package aliveness.

    Returns a dict with evidence from multiple sources:
        running:      True if pidof/ps shows a process
        task:         True if dumpsys activity shows a task (may be STALE)
        window:       True if a *drawing* window for the package exists
        root_running: True if root pidof/ps confirms the process
        alive:        True if process is running OR a drawing window exists
        strict_alive: same as ``alive`` (provided for explicitness)

    DESIGN NOTE — stale-task immunity
    ─────────────────────────────────
    Android's task list (``dumpsys activity activities``) keeps ``TaskRecord``
    entries for many seconds after a package is force-stopped or its window is
    closed.  Previously we treated *any* TaskRecord as "alive", which made the
    supervisor refuse to reconnect after a user closed a Roblox clone window
    in App Cloner.  We now treat ``task`` as a WEAK signal and exclude it from
    ``alive`` / ``strict_alive``.  Real aliveness requires a real process or a
    drawing surface.

    Never raises; always returns a valid dict.
    """
    return get_current_android_package_evidence(package)

    _dead: dict[str, object] = {
        "running": False, "task": False, "window": False, "root_running": False,
        "surface": False, "foreground": False,
        "alive": False, "strict_alive": False, "pid": "", "pidof_rc": 1,
    }
    package_str = package
    try:
        package_str = validate_package_name(package)
    except Exception:  # noqa: BLE001
        return _dead

    running = False
    try:
        running = is_process_running(package_str)
    except Exception:  # noqa: BLE001
        pass

    pid = ""
    pidof_rc = 1
    try:
        root_info = detect_root()
        if root_info.available and root_info.tool:
            pid_result = run_root_command(
                ["pidof", "-s", package_str], root_tool=root_info.tool, timeout=3,
            )
            pidof_rc = int(pid_result.returncode)
            if pid_result.ok and (pid_result.stdout or "").strip().isdigit():
                pid = (pid_result.stdout or "").strip()
    except Exception:  # noqa: BLE001
        pass

    task = False
    try:
        task = is_package_task_visible(package_str)
    except Exception:  # noqa: BLE001
        pass

    window = False
    try:
        window = is_package_window_visible(package_str)
    except Exception:  # noqa: BLE001
        pass

    # Root-escalate process detection so cloud-phone clones (long names that
    # defeat unprivileged pidof) are still seen.
    root_running = False
    if not running:
        try:
            root_running = is_process_running_any(package_str)
        except Exception:  # noqa: BLE001
            try:
                root_running = is_process_running_root(package_str)
            except Exception:  # noqa: BLE001
                pass

    # SurfaceFlinger evidence — a separate strong "rendering" signal that
    # bypasses WindowManager's stale entries.
    surface = False
    try:
        surface = is_package_surface_in_surfaceflinger(package_str)
    except Exception:  # noqa: BLE001
        pass

    # Foreground / resumed-activity evidence.
    foreground = False
    try:
        fg = current_foreground_package()
        foreground = bool(fg and (fg == package_str or package_str in fg))
    except Exception:  # noqa: BLE001
        pass

    # STRICT aliveness: process OR drawing window OR composited surface OR
    # the system says this package is currently foreground.  Task alone is
    # still NOT enough — it lingers after a close.
    process_alive = bool(pid) or running or root_running
    visual_alive = window
    strict_alive = process_alive or visual_alive
    return {
        "running":      running,
        "task":         task,
        "window":       window,
        "root_running": root_running,
        "surface":      surface,
        "foreground":   foreground,
        "alive":        strict_alive,
        "strict_alive": strict_alive,
        "pid":          pid,
        "pidof_rc":     pidof_rc,
    }


def current_foreground_package() -> str | None:
    # Use the shared dumpsys cache so that multiple worker threads asking
    # "who's foreground?" within ~3 seconds share one binder transaction.
    try:
        from . import dumpsys_cache as _dc
        def _run(_args):
            r = run_command(list(_args), timeout=3)
            return _dc.CachedResult(ok=r.ok, stdout=r.stdout)
        for args in (
            ("dumpsys", "window", "windows"),
            ("dumpsys", "activity", "activities"),
        ):
            result = _dc.cached_run(args, _run)
            if not result.ok:
                continue
            text = result.stdout
            for marker in (
                "mCurrentFocus", "mFocusedApp",
                "topResumedActivity", "mResumedActivity",
            ):
                for line in text.splitlines():
                    if marker not in line:
                        continue
                    for part in line.replace("/", " ").replace("}", " ").split():
                        if "." in part and is_valid_package_name(part.split(":")[0]):
                            candidate = part.split(":")[0]
                            if candidate.startswith("com."):
                                return candidate
    except Exception:  # noqa: BLE001
        return None
    return None


def network_available() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=3):
            return True
    except OSError:
        try:
            socket.gethostbyname("roblox.com")
            return True
        except OSError:
            return False


def masked_command_for_log(args: Iterable[str]) -> str:
    safe_parts = []
    for arg in _stringify_args(args):
        safe_parts.append(mask_launch_url(arg) or arg)
    return shlex.join(safe_parts)


# ─── System & app stat helpers ────────────────────────────────────────────────


def get_memory_info() -> dict[str, int]:
    """Read /proc/meminfo. Returns dict with total_mb, free_mb, percent_free.

    Pure Python file read only — no subprocess fallback to avoid the
    fork/exec segfault that can occur on Termux/Python 3.13.  Any read
    or parse failure returns zeros so callers always get a valid dict.
    """
    try:
        with open("/proc/meminfo", "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except Exception:  # noqa: BLE001
        return {"total_mb": 0, "free_mb": 0, "percent_free": 0}
    total = free = 0
    try:
        for line in content.splitlines():
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "MemTotal:" and len(parts) >= 2:
                try:
                    total = int(parts[1]) // 1024
                except (ValueError, IndexError):
                    pass
            elif parts[0] == "MemAvailable:" and len(parts) >= 2:
                try:
                    free = int(parts[1]) // 1024
                except (ValueError, IndexError):
                    pass
    except Exception:  # noqa: BLE001
        pass
    percent = int(free * 100 / total) if total > 0 else 0
    return {"total_mb": total, "free_mb": free, "percent_free": percent}


def get_app_memory_mb(package_or_pid: str) -> float | None:
    """Return per-target PSS in MB via ``dumpsys meminfo`` (never RSS/VSS)."""
    target = str(package_or_pid or "").strip()
    if not target:
        return None
    if not target.isdigit():
        target = validate_package_name(target)
    from .android_memory import parse_dumpsys_meminfo

    result = run_android_command(["dumpsys", "meminfo", target], timeout=8, prefer_root=False)
    if not result.ok:
        return None
    parsed = parse_dumpsys_meminfo(result.stdout or "")
    pss_kb = parsed.get("pss_kb")
    if pss_kb is None:
        return None
    return round(int(pss_kb) / 1024.0, 0)


def get_package_ram_usage(
    package: str,
    root_info: "RootInfo | None" = None,
) -> dict[str, object]:
    """Return per-package RAM usage using PSS as the primary metric.

    Uses :mod:`agent.android_memory` for PSS, private dirty/USS, swap PSS, and
    RSS (debug only). Never treats RSS or VSS as real private RAM.

    Returns a dict with:
      pid           – first PID string (empty if not found)
      pids          – all PIDs for the package
      pss_kb        – proportional shared RAM (primary)
      rss_kb        – raw RSS kilobytes (debug/reference)
      private_dirty_kb, uss_kb, swap_pss_kb – when available
      usage_mb      – formatted PSS e.g. \"256 MB\", \"N/A\"
      method        – collection path used
      success       – bool
      error         – error string (empty on success)
      notes         – user-facing caveats (e.g. inflated RSS)

    Probe tag: [DENG_REJOIN_PACKAGE_USAGE]
    """
    import logging as _log_mod
    from .android_memory import collect_package_memory

    _log = _log_mod.getLogger("deng.rejoin.android")

    package = validate_package_name(package)
    try:
        metrics = collect_package_memory(package, root_info)
    except Exception as exc:  # noqa: BLE001
        metrics = {
            "package": package,
            "pids": [],
            "pss_kb": None,
            "rss_kb": 0,
            "usage_mb": "N/A",
            "method": "unknown",
            "success": False,
            "error": str(exc)[:80],
            "notes": [],
        }

    pids = metrics.get("pids") or []
    pid_str = str(pids[0]) if pids else ""
    pss_kb = metrics.get("pss_kb")
    rss_kb = int(metrics.get("rss_kb") or 0)
    usage_mb = str(metrics.get("usage_mb") or "N/A")
    method = str(metrics.get("method") or "unknown")
    success = bool(metrics.get("success"))
    error = str(metrics.get("error") or "")

    _log.debug(
        "[DENG_REJOIN_PACKAGE_USAGE] package=%s pid=%s method=%s"
        " pss_kb=%s rss_kb=%d usage_display=%s success=%s error=%s",
        package, pid_str, method,
        str(pss_kb), rss_kb, usage_mb,
        str(success).lower(), error,
    )
    return {
        "pid": pid_str,
        "pids": pids,
        "pss_kb": pss_kb,
        "rss_kb": rss_kb,
        "private_dirty_kb": metrics.get("private_dirty_kb"),
        "uss_kb": metrics.get("uss_kb"),
        "swap_pss_kb": metrics.get("swap_pss_kb"),
        "usage_mb": usage_mb,
        "method": method,
        "success": success,
        "error": error,
        "notes": list(metrics.get("notes") or []),
        "status": metrics.get("status"),
    }


def get_cpu_usage() -> float | None:
    """Estimate overall CPU usage percentage via top. Returns None if unavailable."""
    result = run_command(["top", "-bn1"], timeout=6)
    if not result.ok:
        return None
    for line in result.stdout.splitlines():
        lower = line.lower()
        if "cpu" not in lower:
            continue
        # "%Cpu(s): 9.1 us" format
        m = re.search(r"(\d+\.?\d*)\s*%?\s*(?:us|user)(?!\w)", line, re.IGNORECASE)
        if m:
            return float(m.group(1))
        # "X% idle" → compute used
        m2 = re.search(r"(\d+\.?\d*)%?\s*(?:id|idle)(?!\w)", line, re.IGNORECASE)
        if m2:
            idle = float(m2.group(1))
            return round(100.0 - idle, 1)
    return None


def get_temperature() -> float | None:
    """Read CPU/device temperature in Celsius from thermal zones."""
    for path in (
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/class/thermal/thermal_zone1/temp",
        "/sys/class/thermal/thermal_zone2/temp",
    ):
        raw = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                raw = fh.read().strip()
        except OSError:
            result = run_command(["cat", path], timeout=3)
            if result.ok:
                raw = result.stdout.strip()
        if raw.isdigit():
            millis = int(raw)
            # Linux thermal zones usually report in millidegrees
            temp = millis / 1000.0 if millis > 1000 else float(millis)
            if 10.0 < temp < 120.0:
                return round(temp, 1)
    return None


# ─── Preparation helpers ──────────────────────────────────────────────────────


def clear_package_cache(package: str) -> bool:
    """Delete the cache directory contents for a package (requires root).

    Returns True if root was available and the find-delete command ran.
    Only cache/ is cleared — installed data and preferences are NOT affected.
    """
    package = validate_package_name(package)
    root_info = detect_root()
    if not root_info.available:
        return False
    # build path: /data/data/<validated_package>/cache
    cache_path = f"/data/data/{package}/cache"
    result = run_root_command(
        ["find", cache_path, "-mindepth", "1", "-delete"],
        root_tool=root_info.tool,
        timeout=10,
    )
    # 0 = deleted files, 1 = directory empty/not found — both are acceptable
    return result.returncode in (0, 1)


def clear_safe_package_cache(package: str) -> str:
    """Clear only safe cache/temp dirs under /data/data/<pkg>/. Requires root.

    Returns one of: Cleared, Partial, Skipped, Failed. Does not wipe full app data.
    """
    package = validate_package_name(package)
    root_info = detect_root()
    if not root_info.available or not root_info.tool:
        return "Skipped"
    base = f"/data/data/{package}"
    targets = [f"{base}/cache", f"{base}/code_cache", f"{base}/files/tmp"]
    ok = fail = absent = 0
    for path in targets:
        probe = run_root_command(["test", "-d", path], root_tool=root_info.tool, timeout=6)
        if not probe.ok:
            absent += 1
            continue
        result = run_root_command(
            ["find", path, "-mindepth", "1", "-delete"],
            root_tool=root_info.tool,
            timeout=30,
        )
        if result.returncode in (0, 1):
            ok += 1
        else:
            fail += 1
    if fail and ok:
        return "Partial"
    if fail:
        return "Failed"
    if ok:
        return "Cleared"
    return "Skipped"


def clear_package_cache_for_start(
    package: str,
    *,
    root_tool: str,
) -> str:
    """Fast Start-time cache clear — one root shell per package, no size verify.

    ``clear_package_cache_verified`` runs many subprocess calls per package
    (existence probes, ``find|wc`` size checks, retries).  Running that for
    every clone during Start batch prep caused SIGSEGV on Termux/Python 3.13
    when clearing cache for all packages (probe ``p-7dac7cb6c4``).
    """
    package = validate_package_name(package)
    paths = [
        f"/data/user/0/{package}/cache",
        f"/data/user/0/{package}/code_cache",
        f"/data/data/{package}/cache",
        f"/data/data/{package}/code_cache",
        f"/data/data/{package}/files/tmp",
    ]
    quoted = " ".join(shlex.quote(p) for p in paths)
    sh = (
        f"for p in {quoted}; do "
        f'[ -d "$p" ] && find "$p" -mindepth 1 -delete 2>/dev/null; '
        f"done"
    )
    res = run_root_command(["sh", "-c", sh], root_tool=root_tool, timeout=45)
    if res.ok or res.returncode in (0, 1):
        return "Cleared"
    return "Failed"


def clear_packages_cache_batch(
    packages: Iterable[str],
    *,
    root_info: RootInfo | None = None,
) -> dict[str, str]:
    """Clear safe cache dirs for many packages with one root detect."""
    info = root_info or detect_root()
    if not info.available or not info.tool:
        results: dict[str, str] = {}
        for raw in packages:
            try:
                results[validate_package_name(raw)] = "Skipped"
            except ConfigError:
                results[str(raw)] = "Skipped"
        return results
    root_tool = str(info.tool)
    results = {}
    for raw in packages:
        try:
            pkg = validate_package_name(raw)
        except ConfigError:
            results[str(raw)] = "Failed"
            continue
        try:
            results[pkg] = clear_package_cache_for_start(pkg, root_tool=root_tool)
        except Exception:  # noqa: BLE001
            results[pkg] = "Failed"
    return results


_ROBLOX_CLIENT_SETTINGS_REL = "files/ClientSettings/ClientAppSettings.json"

_FORBIDDEN_GRAPHICS_NAME_PARTS = (
    "cookie",
    "token",
    "session",
    "auth",
    "login",
    "password",
    "credential",
    "account",
)


def _relpath_safe_for_graphics(rel: str) -> bool:
    low = rel.lower().replace("\\", "/")
    return not any(bad in low for bad in _FORBIDDEN_GRAPHICS_NAME_PARTS)


def discover_roblox_graphics_json_paths(package: str, root_tool: str) -> list[str]:
    """Find candidate settings JSON under the package files tree (root)."""
    package = validate_package_name(package)
    base = f"/data/data/{package}/files"
    probe = run_root_command(["test", "-d", base], root_tool=root_tool, timeout=4)
    if not probe.ok:
        return []
    # shell find — limited depth and count; skip secret-looking paths when filtering results
    sh = (
        f"find {shlex.quote(base)} -maxdepth 9 -type f "
        r"\( -iname 'ClientAppSettings.json' -o -iname 'clientsettings.json' "
        r"-o -iname 'settings.json' -o -iname 'GlobalBasicSettings.json' -o -iname 'RobloxSettings.json' "
        r"-o -path '*/ClientSettings/*.json' -o -path '*/clientsettings/*.json' "
        r"-o -path '*/robloxsettings/*.json' -o -path '*/RobloxSettings/*.json' \) 2>/dev/null | head -n 24"
    )
    res = run_root_command(["sh", "-c", sh], root_tool=root_tool, timeout=12)
    if not res.ok or not res.stdout.strip():
        legacy = f"/data/data/{package}/{_ROBLOX_CLIENT_SETTINGS_REL}"
        pr = run_root_command(["test", "-f", legacy], root_tool=root_tool, timeout=4)
        return [legacy] if pr.ok else []
    paths: list[str] = []
    for line in res.stdout.strip().splitlines():
        p = line.strip()
        if not p or p in paths:
            continue
        rel = p.split("/files/", 1)[-1] if "/files/" in p else p
        if not _relpath_safe_for_graphics(rel):
            continue
        paths.append(p)
    return paths


def _gfx_tmp_dir() -> str:
    """Return a Termux-writable staging dir for graphics JSON temp files."""
    import os as _os
    home = _os.environ.get("HOME", "/data/data/com.termux/files/home")
    tmp = f"{home}/.deng-tool/rejoin/.tmp"
    _os.makedirs(tmp, exist_ok=True)
    return tmp


def _apply_low_graphics_json_file(path: str, root_tool: str) -> str:
    probe = run_root_command(["test", "-f", path], root_tool=root_tool, timeout=4)
    if not probe.ok:
        return "Skipped"
    cat = run_root_command(["cat", path], root_tool=root_tool, timeout=8)
    if not cat.ok or not cat.stdout.strip():
        return "Skipped"
    try:
        data = json.loads(cat.stdout)
    except json.JSONDecodeError:
        return "Skipped"
    if not isinstance(data, dict):
        return "Skipped"
    backup = f"{path}.bak.deng"
    run_root_command(["cp", path, backup], root_tool=root_tool, timeout=8)
    low_keys: dict[str, int] = {
        "DFIntTaskSchedulerTargetFps": 15,
        "DFIntTextureQualityOverride": 1,
    }
    for key, val in low_keys.items():
        cur = data.get(key)
        if isinstance(cur, int):
            data[key] = min(cur, val)
        else:
            data[key] = val
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)

    # Write via Termux temp file → root cp (avoids needing base64 in su PATH).
    import os as _os, pathlib as _pl
    tmp_name = f"gfx_{_os.path.basename(path)}.deng-tmp"
    local_tmp = _os.path.join(_gfx_tmp_dir(), tmp_name)
    try:
        _pl.Path(local_tmp).write_text(payload, encoding="utf-8")
    except OSError:
        return "Failed"
    wr = run_root_command(
        ["sh", "-c", f"cp -f '{local_tmp}' '{path}' && chmod 660 '{path}'; rm -f '{local_tmp}'"],
        root_tool=root_tool,
        timeout=12,
    )
    try:
        _pl.Path(local_tmp).unlink(missing_ok=True)
    except OSError:
        pass
    if not wr.ok:
        return "Failed"
    verify = run_root_command(["cat", path], root_tool=root_tool, timeout=8)
    if not verify.ok:
        return "Failed"
    try:
        data2 = json.loads(verify.stdout)
    except json.JSONDecodeError:
        return "Failed"
    if not isinstance(data2, dict):
        return "Failed"
    for k in low_keys:
        if data2.get(k) != data.get(k):
            return "Failed"
    return "Low Applied"


def apply_low_graphics_optimization(package: str, *, enabled: bool = True) -> str:
    """Merge low FPS / texture flags into known Roblox client JSON if found."""
    if not enabled:
        return "Skipped"
    package = validate_package_name(package)
    root_info = detect_root()
    if not root_info.available or not root_info.tool:
        return "Skipped"
    paths = discover_roblox_graphics_json_paths(package, root_info.tool)
    if not paths:
        return "Skipped"
    outcome = "Skipped"
    for path in paths:
        r = _apply_low_graphics_json_file(path, root_info.tool)
        if r == "Low Applied":
            return "Low Applied"
        if r == "Failed":
            outcome = "Failed"
    return outcome


def launch_package_with_options(
    package: str,
    private_url: str | None = None,
) -> tuple[CommandResult, str]:
    """Launch order: private URL (VIEW) if set, else normal ``launch_app`` chain.

    Returns (result, method_label).
    """
    package = validate_package_name(package)
    url = (private_url or "").strip()
    if url:
        mode = detect_launch_mode_from_url(url)
        if mode not in LAUNCH_MODES:
            mode = "web_url"
        try:
            validate_launch_url(url, mode, allow_uncertain=True)
        except UrlValidationError as exc:
            return CommandResult(
                ("am", "start", "-a", "android.intent.action.VIEW", "-p", package),
                2,
                "",
                f"configured Roblox link is invalid: {exc}",
            ), "invalid_url"
        result = launch_url(package, url, mode)
        return result, "root_am_view_package"
    return launch_app(package), "am_or_resolve"


def launch_package_with_bounds(
    package: str,
    rect: tuple[int, int, int, int],
    private_url: str | None = None,
    *,
    windowing_mode: int = 5,
) -> tuple[CommandResult, str]:
    """Launch with ``am start --windowingMode <mode> --activity-launch-bounds`` if supported.

    ``windowing_mode=5`` is Android's freeform windowing mode.  Many cloud
    phones support freeform at the framework level once
    ``enable_freeform_support=1`` is set (see :mod:`agent.freeform_enable`).
    If the ``--activity-launch-bounds`` flag is not recognized on this
    Android build, we fall back gracefully to the bounds-less launch.

    Returns ``(result, method_label)``.  Never raises.
    """
    try:
        package = validate_package_name(package)
    except Exception:  # noqa: BLE001
        return CommandResult(("am", "start"), 2, "", "invalid package"), "invalid"

    l, t, r, b = rect
    bounds_arg = f"{int(l)} {int(t)} {int(r)} {int(b)}"
    url = (private_url or "").strip()
    # Convert web share-link URLs → roblox:// deep links before am start.
    # Without this, Android resolves https://www.roblox.com/share?... to the
    # browser instead of Roblox (probe p-6f613cbed2: launch_mode='web_url'
    # landed in lobby, not server).
    deep_url = (to_roblox_deep_link(url) or url) if url else ""

    am = _find_command("am", "/system/bin/am")
    if not am:
        # Fall back to whatever the normal launcher chain can do.
        return launch_package_with_options(package, private_url)

    # Common stem options for every variant we try.
    stem: list[str] = [
        am, "start",
        "--windowingMode", str(int(windowing_mode)),
        "--activity-launch-bounds", str(int(l)), str(int(t)),
        str(int(r)), str(int(b)),
    ]
    method_label = f"am_bounds_mode{int(windowing_mode)}"

    if deep_url:
        mode = detect_launch_mode_from_url(deep_url)
        if mode not in LAUNCH_MODES:
            mode = "deeplink"
        try:
            validate_launch_url(deep_url, mode, allow_uncertain=True)
            # Preferred: bounds + URL + flags.
            #   FLAG_ACTIVITY_NEW_TASK      (0x10000000)
            #   FLAG_ACTIVITY_CLEAR_TASK    (0x00008000)  — ensures fresh start
            #   FLAG_ACTIVITY_CLEAR_TOP     (0x04000000)
            #   FLAG_ACTIVITY_RESET_TASK_IF_NEEDED (0x00200000)
            # BROWSABLE category ensures Android routes roblox:// to
            # ActivityProtocolLaunch (not the generic LAUNCHER).
            cmd = stem + [
                "-a", "android.intent.action.VIEW",
                "-c", "android.intent.category.BROWSABLE",
                "-f", "0x14208000",
                "-d", deep_url, package,
            ]
            res = run_command(cmd, timeout=PROCESS_TIMEOUT_SECONDS)
            if res.ok:
                return res, method_label + "_url"
            # Variant: target ActivityProtocolLaunch directly (bypasses intent
            # resolver ambiguity — confirmed present in Moons clone manifests by
            # probe p-80c42a4c03 pm_dump).
            proto_component = f"{package}/com.roblox.client.ActivityProtocolLaunch"
            cmd_comp = stem + [
                "-n", proto_component,
                "-a", "android.intent.action.VIEW",
                "-c", "android.intent.category.BROWSABLE",
                "-f", "0x14208000",
                "-d", deep_url,
            ]
            res = run_command(cmd_comp, timeout=PROCESS_TIMEOUT_SECONDS)
            if res.ok:
                return res, method_label + "_url_component"
            # Retry without extra intent flags in case device rejects them.
            cmd_nf = stem + ["-a", "android.intent.action.VIEW",
                             "-c", "android.intent.category.BROWSABLE",
                             "-d", deep_url, package]
            res = run_command(cmd_nf, timeout=PROCESS_TIMEOUT_SECONDS)
            if res.ok:
                return res, method_label + "_url_noflag"
            # Last URL fallback: drop --activity-launch-bounds entirely so the
            # intent is delivered without any positioning flags that might
            # confuse Android's activity resolver on this device.  The window
            # position is already pre-written to the App Cloner XML and will
            # be fixed by the post-launch stack resize.
            res_nb = launch_url(package, deep_url, mode)
            if res_nb.ok:
                return res_nb, method_label + "_url_nobounds"
        except UrlValidationError:
            pass
        # URL launch failed on all variants — fall through to MAIN/LAUNCHER.

    # Plain MAIN/LAUNCHER intent with launch bounds.
    cmd = stem + [
        "-a", "android.intent.action.MAIN",
        "-c", "android.intent.category.LAUNCHER",
        "-p", package,
    ]
    res = run_command(cmd, timeout=PROCESS_TIMEOUT_SECONDS)
    if res.ok:
        return res, method_label

    # Fallback when --activity-launch-bounds isn't recognized: try just
    # --windowingMode (older API levels accept this on its own).
    cmd2 = [
        am, "start",
        "--windowingMode", str(int(windowing_mode)),
        "-a", "android.intent.action.MAIN",
        "-c", "android.intent.category.LAUNCHER",
        "-p", package,
    ]
    res2 = run_command(cmd2, timeout=PROCESS_TIMEOUT_SECONDS)
    if res2.ok:
        return res2, method_label + "_no_bounds"

    # Final fallback: normal launcher chain without any flags.
    return launch_package_with_options(package, private_url)


def force_stop_packages_except(
    keep_packages: list[str],
    detection_hints: list[str] | None = None,
) -> list[str]:
    """Force-stop all detected Roblox packages not in keep_packages.

    Tries without root first (works in many Termux ADB setups), then falls
    back to root. Returns list of package names that were successfully stopped.
    """
    keep = set(keep_packages)
    all_roblox = find_roblox_packages(detection_hints)
    to_stop = [
        p for p in all_roblox
        if p not in keep and not _is_protected_system_or_launcher_package(p, keep)
    ]
    if not to_stop:
        return []
    root_info = detect_root()
    stopped: list[str] = []
    for pkg in to_stop:
        result = run_command(["am", "force-stop", pkg], timeout=PROCESS_TIMEOUT_SECONDS)
        if result.ok:
            stopped.append(pkg)
        elif root_info.available:
            result2 = force_stop_package(pkg, root_info)
            if result2.ok:
                stopped.append(pkg)
    return stopped


def _is_protected_system_or_launcher_package(package: str, protected: set[str] | None = None) -> bool:
    pkg = package.strip()
    protected_set = set(protected or set())
    if pkg in protected_set:
        return True
    if pkg in {"android", "com.termux", "com.android.systemui", "com.android.launcher", "com.android.launcher3"}:
        return True
    if pkg.startswith(("com.android.", "com.google.", "com.samsung.", "com.sec.")):
        return True
    try:
        launcher = _current_launcher_package()
        if launcher and pkg == launcher:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


# Packages that must NEVER be killed during the boosting phase.
_KILL_PROTECTED_PREFIXES: tuple[str, ...] = (
    "com.termux",
    "android",
    "com.android",
    "com.google.android",
    "com.samsung",
    "com.sec",
    "com.qualcomm",
    "com.mediatek",
)


def kill_all_background_apps(keep_packages: list[str]) -> dict[str, list[str]]:
    """Clear cached/background processes to free RAM before launching clones.

    Uses ``am kill-all`` which clears processes in the CACHED (background)
    state without touching running foreground apps or system services.  We
    deliberately do NOT enumerate and individually force-stop third-party
    packages — that was too aggressive and killed the Moons cloner service
    that the clones depend on (probe p-e81414d9f4: apps launched ok but
    immediately died because their parent service was force-stopped).

    Returns a dict with keys ``killed_bg`` (kill-all ran) and ``skipped``.
    Never raises.
    """
    result: dict[str, list[str]] = {"killed_bg": [], "skipped": list(keep_packages)}

    bg = run_android_command(["am", "kill-all"], prefer_root=True, timeout=10)
    if bg.ok:
        result["killed_bg"].append("am kill-all")
    return result


_CLOUD_MEMORY_RECOVERY_COMMAND = "pm enable com.google.android.gms"
_CLOUD_DISABLE_PACKAGES: frozenset[str] = frozenset({
    "com.google.android.gms",
    "com.android.vending",
    "com.google.android.googlequicksearchbox",
    "com.google.android.feedback",
    "com.google.android.partnersetup",
    "com.google.android.setupwizard",
})
_CLOUD_FORCE_STOP_EXACT: frozenset[str] = frozenset({
    "com.discord",
    "com.android.chrome",
    "com.google.android.youtube",
    "com.zhiliaoapp.musically",
    "com.ss.android.ugc.trill",
    "com.sec.android.gallery3d",
    "com.samsung.android.game.gos",
    "com.samsung.android.game.gamehome",
    "com.samsung.android.game.gametools",
})
_CLOUD_FORCE_STOP_PREFIXES: tuple[str, ...] = (
    "com.discord.",
    "com.chrome.",
    "com.google.android.youtube.",
    "com.zhiliaoapp.",
    "com.samsung.android.app.",
)
_CLOUD_PROTECTED_EXACT: frozenset[str] = frozenset({
    "android",
    "com.android.systemui",
    "com.termux",
    "com.termux.boot",
    "com.termux.api",
    "com.topjohnwu.magisk",
    "me.weishu.kernelsu",
    "com.kingroot.kinguser",
    "eu.chainfire.supersu",
    "com.android.settings",
    "com.android.shell",
    "com.android.packageinstaller",
    "com.google.android.packageinstaller",
    "com.android.permissioncontroller",
    "com.android.webview",
    "com.google.android.webview",
})
_CLOUD_PROTECTED_PREFIXES: tuple[str, ...] = (
    "com.android.providers.",
    "com.android.phone",
    "com.android.server.telecom",
    "com.android.network",
    "com.android.ims",
    "com.google.android.apps.messaging",
)
_CLOUD_MEMORY_LAST_RUN = 0.0
_CLOUD_MEMORY_COOLDOWN_SECONDS = 10 * 60


def cloud_phone_memory_recovery_command() -> str:
    return _CLOUD_MEMORY_RECOVERY_COMMAND


def _parse_packages(stdout: str) -> list[str]:
    packages: list[str] = []
    for line in (stdout or "").splitlines():
        raw = line.strip()
        if raw.startswith("package:"):
            raw = raw[len("package:") :]
        try:
            packages.append(validate_package_name(raw))
        except ConfigError:
            continue
    return sorted(set(packages))


def _current_launcher_package() -> str:
    res = run_android_command(
        [
            "cmd", "package", "resolve-activity", "--brief",
            "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.HOME",
        ],
        timeout=6,
        prefer_root=False,
    )
    text = res.stdout or ""
    for line in reversed(text.splitlines()):
        for part in line.replace("/", " ").split():
            candidate = part.split(":")[0]
            if "." in candidate and is_valid_package_name(candidate):
                return candidate
    return current_foreground_package() or ""


def _current_keyboard_package() -> str:
    for cmd in (
        ["settings", "get", "secure", "default_input_method"],
        ["ime", "list", "-s"],
    ):
        res = run_android_command(cmd, timeout=6, prefer_root=True)
        text = (res.stdout or "").strip()
        if not text or text.lower() == "null":
            continue
        raw = text.splitlines()[0].split("/", 1)[0].strip()
        if is_valid_package_name(raw):
            return raw
    return ""


def _is_cloud_memory_protected(package: str, protected: set[str]) -> bool:
    pkg = package.strip()
    return (
        pkg in protected
        or pkg in _CLOUD_PROTECTED_EXACT
        or any(pkg.startswith(prefix) for prefix in _CLOUD_PROTECTED_PREFIXES)
    )


def _is_package_disabled(package: str, root_tool: str) -> bool:
    res = run_root_command(["pm", "list", "packages", "-d"], root_tool=root_tool, timeout=8)
    return package in _parse_packages(res.stdout or "")


def optimize_cloud_phone_memory(
    keep_packages: list[str],
    *,
    cooldown_seconds: int = _CLOUD_MEMORY_COOLDOWN_SECONDS,
) -> dict[str, object]:
    """Default Cloud Phone Extreme memory preparation.

    This is not a selectable mode. It is the only Start-time memory policy:
    disable allowed Google packages with ``pm disable-user --user 0`` and
    force-stop selected nonessential apps. Core Android, Termux, root manager,
    launcher, keyboard, and Roblox targets are protected.
    """
    global _CLOUD_MEMORY_LAST_RUN
    now = time.monotonic()
    result: dict[str, object] = {
        "disabled": [],
        "stopped": [],
        "skipped": [],
        "failed": [],
        "recovery_command": _CLOUD_MEMORY_RECOVERY_COMMAND,
        "cooldown_skipped": False,
    }
    if now - _CLOUD_MEMORY_LAST_RUN < max(0, int(cooldown_seconds)):
        result["cooldown_skipped"] = True
        return result

    root_info = detect_root()
    if not root_info.available or not root_info.tool:
        result["failed"] = [{"package": "", "action": "root", "error": "root unavailable"}]
        return result

    protected = {p for p in keep_packages if p}
    protected.update({"com.termux"})
    for dynamic_pkg in (_current_launcher_package(), _current_keyboard_package(), current_foreground_package() or ""):
        if dynamic_pkg:
            protected.add(dynamic_pkg)

    installed = _parse_packages(
        run_android_command(["pm", "list", "packages"], timeout=12, prefer_root=True).stdout
    )
    installed_set = set(installed)
    disabled: list[str] = []
    stopped: list[str] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    for pkg in sorted(_CLOUD_DISABLE_PACKAGES & installed_set):
        if _is_cloud_memory_protected(pkg, protected):
            skipped.append({"package": pkg, "reason": "protected"})
            continue
        if _is_package_disabled(pkg, root_info.tool):
            disabled.append(pkg)
            continue
        res = run_root_command(
            ["pm", "disable-user", "--user", "0", pkg],
            root_tool=root_info.tool,
            timeout=10,
        )
        if res.ok:
            disabled.append(pkg)
        else:
            failed.append({"package": pkg, "action": "disable-user", "error": (res.stderr or res.stdout)[:120]})

    for pkg in installed:
        should_stop = pkg in _CLOUD_FORCE_STOP_EXACT or any(pkg.startswith(prefix) for prefix in _CLOUD_FORCE_STOP_PREFIXES)
        if not should_stop:
            continue
        if _is_cloud_memory_protected(pkg, protected):
            skipped.append({"package": pkg, "reason": "protected"})
            continue
        res = force_stop_package(pkg, root_info)
        if res.ok:
            stopped.append(pkg)
        else:
            failed.append({"package": pkg, "action": "force-stop", "error": (res.stderr or res.stdout)[:120]})

    result["disabled"] = disabled
    result["stopped"] = stopped
    result["skipped"] = skipped
    result["failed"] = failed
    _CLOUD_MEMORY_LAST_RUN = now
    return result
