"""Android/Termux platform and public storage path detection."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROCESS_TIMEOUT_SECONDS = 5

PUBLIC_DOWNLOAD_CANDIDATES = (
    Path("/sdcard/Download"),
    Path("/sdcard/download"),
    Path("/storage/emulated/0/Download"),
    Path("/storage/emulated/0/download"),
)


@dataclass(frozen=True)
class PlatformInfo:
    android_release: str
    android_sdk: str
    termux_prefix: str
    home: str
    download_dir: str


def _run_getprop(prop: str) -> str:
    try:
        completed = subprocess.run(
            ["getprop", prop],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=PROCESS_TIMEOUT_SECONDS,
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def get_android_release() -> str:
    return _run_getprop("ro.build.version.release") or "unknown"


def get_android_sdk() -> str:
    return _run_getprop("ro.build.version.sdk") or "unknown"


def get_termux_prefix() -> str:
    return os.environ.get("PREFIX", "")


def detect_public_download_dir() -> str:
    """Return the best public Download folder, or empty string if inaccessible."""
    for candidate in PUBLIC_DOWNLOAD_CANDIDATES:
        if candidate.exists() and os.access(candidate, os.W_OK):
            return str(candidate)
    for candidate in PUBLIC_DOWNLOAD_CANDIDATES:
        if candidate.exists() and os.access(candidate, os.R_OK):
            return str(candidate)
    return ""


def fallback_launcher_path() -> Path:
    return Path.home() / ".deng-tool" / "rejoin" / "launcher" / "deng-rejoin.py"


def get_platform_info() -> PlatformInfo:
    return PlatformInfo(
        android_release=get_android_release(),
        android_sdk=get_android_sdk(),
        termux_prefix=get_termux_prefix(),
        home=str(Path.home()),
        download_dir=detect_public_download_dir(),
    )
