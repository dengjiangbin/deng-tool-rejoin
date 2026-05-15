"""Centralized Android and optional-root command execution."""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import ConfigError, is_valid_package_name, normalize_package_detection_hint, validate_package_name
from .constants import (
    DEFAULT_ROBLOX_PACKAGE,
    DEFAULT_ROBLOX_PACKAGE_HINTS,
    LAUNCH_MODES,
    PROCESS_TIMEOUT_SECONDS,
    ROOT_TIMEOUT_SECONDS,
)
from .platform_detect import detect_public_download_dir, get_android_release, get_android_sdk
from .url_utils import UrlValidationError, detect_launch_mode_from_url, mask_launch_url, validate_launch_url


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


def run_command(args: Iterable[str], *, timeout: int = PROCESS_TIMEOUT_SECONDS) -> CommandResult:
    """Run a local command with timeout and captured output."""
    cmd = _stringify_args(args)
    try:
        completed = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
            env=_safe_env(),
        )
        return CommandResult(tuple(cmd), completed.returncode, completed.stdout.strip(), completed.stderr.strip())
    except FileNotFoundError as exc:
        return CommandResult(tuple(cmd), 127, "", str(exc))
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CommandResult(tuple(cmd), 124, stdout.strip(), stderr.strip() or "command timed out", timed_out=True)


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


def discover_roblox_package_candidates(
    hints: Iterable[str] | None = None,
    *,
    include_launchable_only: bool = True,
    detection_enabled: bool = True,
) -> list[RobloxPackageCandidate]:
    """Discovery: name-filter first so dumpsys runs only for likely packages (cached per call)."""
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
    packages = list_packages()
    label_cache: dict[str, str] = {}
    launch_cache: dict[str, bool] = {}
    candidate_pkgs = [pkg for pkg in packages if pkg == DEFAULT_ROBLOX_PACKAGE or _package_name_matches_hints(pkg, detection_hints)]
    if DEFAULT_ROBLOX_PACKAGE in packages and DEFAULT_ROBLOX_PACKAGE not in candidate_pkgs:
        candidate_pkgs.insert(0, DEFAULT_ROBLOX_PACKAGE)
    out: list[RobloxPackageCandidate] = []
    for pkg in sorted(set(candidate_pkgs), key=lambda p: (0 if p == DEFAULT_ROBLOX_PACKAGE else 1, p)):
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
    """
    tool = root_tool or detect_root().tool
    if not tool:
        return CommandResult(tuple(_stringify_args(args)), 127, "", "root tool unavailable")
    tokens = _stringify_args(args)
    command = shlex.join(tokens)
    return run_command([tool, "-c", command], timeout=timeout)


def force_stop_package(package: str, root_info: RootInfo | None = None) -> CommandResult:
    package = validate_package_name(package)
    info = root_info or detect_root()
    if not info.available:
        return CommandResult(("am", "force-stop", package), 126, "", "root unavailable")
    return run_root_command(["am", "force-stop", package], root_tool=info.tool, timeout=PROCESS_TIMEOUT_SECONDS)


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
    3. monkey -p <package> -c android.intent.category.LAUNCHER 1 (optional fallback)

    Never raises FileNotFoundError. Returns a CommandResult indicating the outcome.
    """
    package = validate_package_name(package)
    am = _find_command("am", "/system/bin/am")
    cmd_bin = _find_command("cmd", "/system/bin/cmd")
    monkey_bin = _find_command("monkey", "/system/bin/monkey")

    last_result: CommandResult | None = None

    # Method 1: am start with MAIN + LAUNCHER intent
    if am:
        result = run_command(
            [am, "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", "-p", package],
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
        if result.ok:
            return result
        last_result = result

    # Method 2: resolve-activity to get exact component, then am start -n
    if cmd_bin and am:
        resolve = run_command(
            [cmd_bin, "package", "resolve-activity", "--brief", package],
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
        if resolve.ok:
            component = _parse_activity_component(resolve.stdout, package)
            if component:
                result2 = run_command([am, "start", "-n", component], timeout=PROCESS_TIMEOUT_SECONDS)
                if result2.ok:
                    return result2
                last_result = result2

    # Method 3: monkey fallback (optional, may not be available)
    if monkey_bin:
        result3 = run_command(
            [monkey_bin, "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
            timeout=PROCESS_TIMEOUT_SECONDS,
        )
        if result3.ok:
            return result3
        last_result = result3

    # All methods failed — return best available failure result
    if last_result:
        return last_result
    return CommandResult(
        ("am", "start", package),
        127,
        "",
        "Android launcher commands unavailable: am/cmd/monkey not found",
    )


def launch_url(package: str, url: str, launch_mode: str) -> CommandResult:
    package = validate_package_name(package)
    validate_launch_url(url, launch_mode, allow_uncertain=True)
    # Android's am tool accepts the package name as the final selector argument.
    return run_command(
        ["am", "start", "-a", "android.intent.action.VIEW", "-d", url, package],
        timeout=PROCESS_TIMEOUT_SECONDS,
    )


def launch_url_generic(url: str, launch_mode: str) -> CommandResult:
    validate_launch_url(url, launch_mode, allow_uncertain=True)
    return run_command(
        ["am", "start", "-a", "android.intent.action.VIEW", "-d", url],
        timeout=PROCESS_TIMEOUT_SECONDS,
    )


def is_process_running(package: str) -> bool:
    package = validate_package_name(package)
    result = run_command(["pidof", package], timeout=PROCESS_TIMEOUT_SECONDS)
    return result.ok and bool(result.stdout.strip())


def current_foreground_package() -> str | None:
    checks = [
        ["dumpsys", "window", "windows"],
        ["dumpsys", "activity", "activities"],
    ]
    for args in checks:
        result = run_command(args, timeout=PROCESS_TIMEOUT_SECONDS)
        if not result.ok:
            continue
        text = result.stdout
        for marker in ("mCurrentFocus", "mFocusedApp", "topResumedActivity", "mResumedActivity"):
            for line in text.splitlines():
                if marker not in line:
                    continue
                for part in line.replace("/", " ").replace("}", " ").split():
                    if "." in part and is_valid_package_name(part.split(":")[0]):
                        candidate = part.split(":")[0]
                        if candidate.startswith("com."):
                            return candidate
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
    """Read /proc/meminfo. Returns dict with total_mb, free_mb, percent_free."""
    content = ""
    try:
        with open("/proc/meminfo", "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        result = run_command(["cat", "/proc/meminfo"], timeout=3)
        if result.ok:
            content = result.stdout
    total = free = 0
    for line in content.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "MemTotal:" and len(parts) >= 2:
            try:
                total = int(parts[1]) // 1024
            except ValueError:
                pass
        elif parts[0] == "MemAvailable:" and len(parts) >= 2:
            try:
                free = int(parts[1]) // 1024
            except ValueError:
                pass
    percent = int(free * 100 / total) if total > 0 else 0
    return {"total_mb": total, "free_mb": free, "percent_free": percent}


def get_app_memory_mb(package: str) -> float | None:
    """Get approximate RAM usage for a running package in MB via dumpsys meminfo."""
    package = validate_package_name(package)
    result = run_command(["dumpsys", "meminfo", package], timeout=8)
    if not result.ok:
        return None
    # Look for "TOTAL PSS:" or similar summary line (KB → MB)
    for line in result.stdout.splitlines():
        m = re.search(r"TOTAL\s+(?:PSS|RSS)?\s*:?\s*(\d+)", line, re.IGNORECASE)
        if m:
            return round(int(m.group(1)) / 1024.0, 0)
    for line in result.stdout.splitlines():
        if line.strip().upper().startswith("TOTAL"):
            for part in line.split():
                if part.isdigit() and int(part) > 100:
                    return round(int(part) / 1024.0, 0)
    return None


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
    b64 = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    wr = run_root_command(
        ["sh", "-c", f"printf '%s' '{b64}' | base64 -d > '{path}'"],
        root_tool=root_tool,
        timeout=12,
    )
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
        except UrlValidationError:
            result = launch_app(package)
            return result, "am_or_resolve"
        result = launch_url(package, url, mode)
        if result.ok:
            return result, "private_url"
        result2 = launch_url_generic(url, mode)
        if result2.ok:
            return result2, "private_url_generic"
        result3 = launch_app(package)
        return result3, "fallback_am"
    return launch_app(package), "am_or_resolve"


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
    to_stop = [p for p in all_roblox if p not in keep]
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
