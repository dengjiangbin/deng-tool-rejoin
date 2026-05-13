"""Generate public market-style Python launcher files."""

from __future__ import annotations

from pathlib import Path

from .constants import LAUNCHER_DIR
from .platform_detect import PUBLIC_DOWNLOAD_CANDIDATES, fallback_launcher_path

LAUNCHER_FILENAME = "deng-rejoin.py"

LAUNCHER_CONTENT = '''import os
import subprocess
import sys

home = os.path.expanduser("~")
project = os.path.join(home, ".deng-tool", "rejoin")
main = os.path.join(project, "agent", "deng_tool_rejoin.py")

if not os.path.exists(main):
    print("DENG Tool: Rejoin is not installed.")
    print("Run the GitHub install command first.")
    sys.exit(1)

os.chdir(project)
raise SystemExit(subprocess.call(["python", main, "menu"] + sys.argv[1:]))
'''


def write_launcher(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(LAUNCHER_CONTENT, encoding="utf-8")
        return True
    except OSError:
        return False


def create_market_launchers() -> list[Path]:
    """Create launchers in public Download folders and a Termux-home fallback."""
    created: list[Path] = []
    for directory in PUBLIC_DOWNLOAD_CANDIDATES:
        if directory.exists():
            path = directory / LAUNCHER_FILENAME
            if write_launcher(path):
                created.append(path)

    fallback = fallback_launcher_path()
    if write_launcher(fallback):
        created.append(fallback)

    LAUNCHER_DIR.mkdir(parents=True, exist_ok=True)
    local = LAUNCHER_DIR / LAUNCHER_FILENAME
    if local != fallback and write_launcher(local):
        created.append(local)

    return created
