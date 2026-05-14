"""Centralized Android and optional-root command execution."""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import ConfigError, is_valid_package_name, normalize_package_detection_hint, validate_package_name
from .constants import (
    DEFAULT_ROBLOX_PACKAGE,
    DEFAULT_ROBLOX_PACKAGE_HINTS,
    PROCESS_TIMEOUT_SECONDS,
    ROOT_TIMEOUT_SECONDS,
)
from .platform_detect import detect_public_download_dir, get_android_release, get_android_sdk
from .url_utils import mask_launch_url, validate_launch_url


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


def launch_app(package: str) -> CommandResult:
    package = validate_package_name(package)
    return run_command(["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"], timeout=PROCESS_TIMEOUT_SECONDS)


def launch_url(package: str, url: str, launch_mode: str) -> CommandResult:
    package = validate_package_name(package)
    validate_launch_url(url, launch_mode, allow_uncertain=True)
    # Android's am tool accepts the package name as the final selector argument.
    return run_command(
        ["am", "start", "-a", "android.intent.action.VIEW", "-d", url, package],
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
