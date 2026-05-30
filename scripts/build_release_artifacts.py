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


def _stable_version(repo: Path) -> str:
    version = (repo / "VERSION").read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("VERSION file is empty")
    return version if version.startswith("v") else f"v{version}"


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


def _ensure_stable_row(rows: list[dict], version: str) -> dict:
    existing = next((row for row in rows if str(row.get("version") or "") == version), None)
    if existing is not None:
        return existing
    row = {
        "version": version,
        "id": version,
        "channel": "stable",
        "title": f"Version {version.lstrip('v')}",
        "label": version,
        "recommended": True,
        "visibility": "public",
        "install_ref": f"refs/tags/{version}",
        "git_ref": f"refs/tags/{version}",
        "release_stage": "stable",
        "frozen": True,
        "artifact_path": "",
        "artifact_sha256": "",
        "installer_endpoint": f"/install/{version}",
        "notes": f"Frozen public stable release. Immutable artifact built for {version}.",
        "enabled": True,
    }
    insert_at = next((i for i, existing_row in enumerate(rows) if existing_row.get("version") == "main-dev"), len(rows))
    rows.insert(insert_at, row)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="Build protected stable and main-dev artifacts.")
    ap.add_argument("--repo-root", type=Path, default=_PROJECT_ROOT)
    args = ap.parse_args()
    repo = args.repo_root.resolve()
    manifest_path = repo / "data" / "rejoin_versions.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    stable_version = _stable_version(repo)

    _ensure_stable_row(rows, stable_version)

    stable_rel = f"releases/{stable_version}/deng-tool-rejoin-{stable_version}.tar.gz"
    dev_rel = "releases/main-dev/deng-tool-rejoin-main-dev.tar.gz"
    stable_sha = build_internal_test_tarball(
        repo,
        repo / stable_rel,
        channel="stable",
        version=stable_version,
    )
    dev_sha = build_internal_test_tarball(
        repo,
        repo / dev_rel,
        channel="main-dev",
        version="main-dev",
    )
    _update_row(rows, stable_version, path=stable_rel, sha=stable_sha)
    _update_row(rows, "main-dev", path=dev_rel, sha=dev_sha)
    for row in rows:
        if row.get("kind") == "channel_pointers":
            row["stable_latest"] = stable_version
            row["test_latest"] = "main-dev"
    manifest_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"{stable_version} artifact_sha256={stable_sha}")
    print(f"main-dev artifact_sha256={dev_sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
