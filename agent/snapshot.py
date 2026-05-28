"""Optional Android screenshot/snapshot support for webhooks.

v1.0.4: capture the **whole** Android display (terminal + every Roblox
window + system UI) — the same picture the user sees when they look at
the cloud phone — not just one app or a blank placeholder. The user
explicitly compared this to the Kaeru tool which is a full-display
capture; ours now does the same.

Implementation:
    * ``screencap -p`` is always the canonical path. On Android, this
      binary captures the entire framebuffer, regardless of which app
      is foreground, and emits a PNG to stdout — exactly what we want.
    * We try a small ladder of binaries so the capture still works on
      rooted devices that have moved ``screencap`` around, or on
      cloud-phone images where the framework wraps it behind ``sh``::

          1.  ``screencap -p``
          2.  ``/system/bin/screencap -p``
          3.  ``su -c "screencap -p"``  (only if su is on PATH and the
              user has explicitly opted into root via the env var
              DENG_REJOIN_SNAPSHOT_USE_SU=1)
    * Each rung carries the same 10-second hard timeout.
    * We never run an app-only capture — that's the bug class the user
      hit in v1.0.3.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .constants import SNAPSHOT_DIR


def _candidate_commands() -> list[list[str]]:
    """Return the list of capture commands to try, in priority order.

    Order is "fastest / least privileged first" so we never escalate
    unless an earlier attempt actually failed. We do not silently
    invoke ``su`` — users opt in with DENG_REJOIN_SNAPSHOT_USE_SU=1.
    """
    cmds: list[list[str]] = [["screencap", "-p"]]
    abs_path = "/system/bin/screencap"
    if os.path.isfile(abs_path):
        cmds.append([abs_path, "-p"])
    if os.environ.get("DENG_REJOIN_SNAPSHOT_USE_SU") == "1" and shutil.which("su"):
        cmds.append(["su", "-c", "screencap -p"])
    return cmds


def capture_snapshot() -> tuple[Path | None, str]:
    """Capture a whole-display Android screenshot, PNG, to disk.

    Returns ``(path, "snapshot captured")`` on success or
    ``(None, "<safe error message>")`` on failure. Never raises.
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"snapshot-{int(time.time())}.png"

    last_err = "screenshot unavailable"
    for cmd in _candidate_commands():
        try:
            with path.open("wb") as handle:
                completed = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=handle,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    shell=False,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            last_err = f"screenshot unavailable: {exc.__class__.__name__}"
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue

        if completed.returncode != 0 or not path.exists() or path.stat().st_size == 0:
            err_text = (
                completed.stderr.decode("utf-8", errors="replace").strip()
                if completed.stderr else "screenshot failed (empty)"
            )
            last_err = err_text or "screenshot failed"
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue

        # Sanity check: a real PNG starts with 0x89 0x50 0x4E 0x47.
        # If we got bytes but they aren't PNG, something proxied the
        # output (e.g. shell wrapped stderr into stdout). Treat as a
        # failure and try the next rung.
        try:
            head = path.open("rb").read(8)
        except OSError as exc:
            last_err = f"snapshot read failed: {exc.__class__.__name__}"
            continue
        if not head.startswith(b"\x89PNG\r\n\x1a\n"):
            last_err = "screencap did not return PNG bytes"
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            continue

        return path, "snapshot captured"

    return None, last_err


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
