"""Fast Roblox clone package discovery (Lime-style, test/latest2)."""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any

from .lime_channel import lime_detection_enabled

_LAST_RESULT: dict[str, Any] = {
    "packages": [],
    "package_count": 0,
    "discovered_at": None,
    "duration_ms": None,
    "command": "pm list packages 2>/dev/null | grep -i roblox",
    "error": "",
}


def discover_roblox_packages(*, force: bool = False, cache_ttl_s: float = 30.0) -> dict[str, Any]:
    """Return Roblox-related package names via root shell grep (Lime APK pattern)."""
    if not lime_detection_enabled() and not force:
        return dict(_LAST_RESULT)

    now = time.time()
    if (
        not force
        and _LAST_RESULT.get("discovered_at")
        and (now - float(_LAST_RESULT["discovered_at"])) < cache_ttl_s
    ):
        return dict(_LAST_RESULT)

    cmd = "pm list packages 2>/dev/null | grep -i roblox"
    started = time.perf_counter()
    packages: list[str] = []
    error = ""
    try:
        proc = subprocess.run(
            ["su", "-c", cmd],
            capture_output=True,
            text=True,
            timeout=4,
        )
        text = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode not in (0, 1):
            error = (proc.stderr or proc.stdout or f"exit={proc.returncode}")[:160]
        for line in text.splitlines():
            m = re.match(r"^package:(\S+)", line.strip())
            if m:
                pkg = m.group(1).strip()
                if pkg and pkg not in packages:
                    packages.append(pkg)
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:160]
        try:
            proc2 = subprocess.run(
                ["pm", "list", "packages"],
                capture_output=True,
                text=True,
                timeout=6,
            )
            for line in (proc2.stdout or "").splitlines():
                if "roblox" not in line.lower():
                    continue
                m = re.match(r"^package:(\S+)", line.strip())
                if m:
                    pkg = m.group(1).strip()
                    if pkg and pkg not in packages:
                        packages.append(pkg)
        except Exception as exc2:  # noqa: BLE001
            if not error:
                error = str(exc2)[:160]

    duration_ms = round((time.perf_counter() - started) * 1000.0, 1)
    result = {
        "packages": packages,
        "package_count": len(packages),
        "discovered_at": now,
        "duration_ms": duration_ms,
        "command": cmd,
        "error": error,
    }
    _LAST_RESULT.clear()
    _LAST_RESULT.update(result)
    return dict(result)


def probe_package_discovery_snapshot() -> dict[str, Any]:
    """Read-only snapshot for dev-probe (uses cache when fresh)."""
    snap = discover_roblox_packages(force=False)
    return {
        "packages": list(snap.get("packages") or []),
        "package_count": int(snap.get("package_count") or 0),
        "duration_ms": snap.get("duration_ms"),
        "discovered_at": snap.get("discovered_at"),
        "command": snap.get("command"),
        "error": snap.get("error") or None,
    }
