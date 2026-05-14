"""Optional Android screenshot/snapshot support for webhooks."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from .constants import SNAPSHOT_DIR


def capture_snapshot() -> tuple[Path | None, str]:
    """Capture a temporary Android screenshot if screencap is available."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"snapshot-{int(time.time())}.png"
    try:
        with path.open("wb") as handle:
            completed = subprocess.run(
                ["screencap", "-p"],
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.PIPE,
                timeout=10,
                shell=False,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return None, f"screenshot unavailable: {exc}"
    if completed.returncode != 0 or not path.exists() or path.stat().st_size == 0:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return None, completed.stderr.decode("utf-8", errors="replace") if completed.stderr else "screenshot failed"
    return path, "snapshot captured"


def cleanup_old_snapshots(max_age_seconds: int = 300) -> None:
    cutoff = time.time() - max(30, int(max_age_seconds))
    if not SNAPSHOT_DIR.exists():
        return
    for item in SNAPSHOT_DIR.glob("snapshot-*.png"):
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
        except OSError:
            continue
