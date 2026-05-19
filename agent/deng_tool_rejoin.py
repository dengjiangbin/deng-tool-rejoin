#!/usr/bin/env python3
"""DENG Tool: Rejoin CLI entrypoint."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Enable faulthandler early — writes SIGSEGV/SIGFPE stack traces to a file
# instead of silently crashing.  Probe context: p-316b3b040d.
try:
    import faulthandler as _fh
    _crash_dir = Path(
        os.environ.get("DENG_REJOIN_HOME", Path.home() / ".deng-tool" / "rejoin")
    ) / "data" / "logs"
    _crash_dir.mkdir(parents=True, exist_ok=True)
    _crash_file = _crash_dir / "crash_faulthandler.log"
    _crash_fh = open(_crash_file, "a", encoding="utf-8", errors="replace")  # noqa: WPS515
    _crash_fh.write(f"\n--- faulthandler session started (probe p-9e3f2a8d1c) ---\n")
    _crash_fh.flush()
    _fh.enable(file=_crash_fh, all_threads=True)
except Exception:  # noqa: BLE001
    try:
        import faulthandler as _fh
        _fh.enable()
    except Exception:  # noqa: BLE001
        pass

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agent.commands import main
else:
    from .commands import main


if __name__ == "__main__":
    raise SystemExit(main())
