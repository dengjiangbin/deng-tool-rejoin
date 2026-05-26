#!/usr/bin/env python3
"""Build protected stable and main-dev artifacts and update release registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.internal_test_artifact import build_internal_test_tarball  # noqa: E402


def _update_row(rows: list[dict], version: str, *, path: str, sha: str) -> None:
    for row in rows:
        if str(row.get("version") or "") == version:
            row["artifact_path"] = path.replace("\\", "/")
            row["artifact_sha256"] = sha
            row["enabled"] = True
            if version == "v1.0.0":
                row["channel"] = "stable"
                row["visibility"] = "public"
                row["installer_endpoint"] = "/install/v1.0.0"
            elif version == "main-dev":
                row["channel"] = "dev"
                row["visibility"] = "admin"
                row["installer_endpoint"] = "/install/test/latest"
            return
    raise RuntimeError(f"release registry row not found: {version}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build protected v1.0.0 and main-dev artifacts.")
    ap.add_argument("--repo-root", type=Path, default=_PROJECT_ROOT)
    args = ap.parse_args()
    repo = args.repo_root.resolve()
    manifest_path = repo / "data" / "rejoin_versions.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))

    stable_rel = "releases/v1.0.0/deng-tool-rejoin-v1.0.0.tar.gz"
    dev_rel = "releases/main-dev/deng-tool-rejoin-main-dev.tar.gz"
    stable_sha = build_internal_test_tarball(
        repo,
        repo / stable_rel,
        channel="stable",
        version="v1.0.0",
    )
    dev_sha = build_internal_test_tarball(
        repo,
        repo / dev_rel,
        channel="main-dev",
        version="main-dev",
    )
    _update_row(rows, "v1.0.0", path=stable_rel, sha=stable_sha)
    _update_row(rows, "main-dev", path=dev_rel, sha=dev_sha)
    for row in rows:
        if row.get("kind") == "channel_pointers":
            row["stable_latest"] = "v1.0.0"
            row["test_latest"] = "main-dev"
    manifest_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"v1.0.0 artifact_sha256={stable_sha}")
    print(f"main-dev artifact_sha256={dev_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
