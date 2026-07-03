#!/usr/bin/env python3
"""Check lime overlay agent imports exist at v1.3.0 tag or in overlay list."""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = (
    "agent/lime_channel.py",
    "agent/lime_detection_speed.py",
    "agent/rjn_lifecycle_monitor.py",
    "agent/force_close_race.py",
    "agent/roblox_disconnect_reasons.py",
    "agent/ocr_screen_detector.py",
    "agent/detection_speed_test.py",
    "agent/probe.py",
    "agent/license.py",
    "agent/build_info.py",
)
BASE = subprocess.run(
    ["git", "rev-parse", "v1.3.0^{commit}"],
    cwd=str(ROOT),
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()


def _agent_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agent."):
            rel = "agent/" + node.module[len("agent.") :].replace(".", "/") + ".py"
            out.add(rel)
    return out


def main() -> int:
    overlay_set = set(OVERLAY)
    missing: set[str] = set()
    for rel in OVERLAY:
        for dep in _agent_imports(ROOT / rel):
            if dep in overlay_set:
                continue
            r = subprocess.run(
                ["git", "cat-file", "-e", f"{BASE}:{dep}"],
                cwd=str(ROOT),
                capture_output=True,
            )
            if r.returncode != 0:
                missing.add(dep)
    if missing:
        print("Missing at v1.3.0 tag (add to overlay):", file=sys.stderr)
        for m in sorted(missing):
            print(f"  {m}", file=sys.stderr)
        return 1
    print("OK: all overlay agent imports satisfied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
