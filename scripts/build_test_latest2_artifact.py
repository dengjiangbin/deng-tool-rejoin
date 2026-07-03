#!/usr/bin/env python3
"""Build or seed the isolated test/latest2 channel artifact.

Phase 1 (baseline proof):
  python scripts/build_test_latest2_artifact.py --copy-v130

Phase 2 (Lime detection build):
  python scripts/build_test_latest2_artifact.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.internal_test_artifact import (  # noqa: E402
    TEST_LATEST2_ARCHIVE_REL_PATH,
    build_internal_test_tarball,
    verify_tarball_exclusions,
)

_V130_REL = "releases/v1.3.0/deng-tool-rejoin-v1.3.0.tar.gz"
_MANIFEST_VERSION = "test-latest2"
_SOURCE_VERSION = "v1.3.0"


def _update_manifest(repo: Path, *, sha: str, mode: str) -> None:
    manifest = repo / "data" / "rejoin_versions.json"
    rows = json.loads(manifest.read_text(encoding="utf-8"))
    for row in rows:
        if str(row.get("version") or "").strip() != _MANIFEST_VERSION:
            continue
        row["artifact_path"] = TEST_LATEST2_ARCHIVE_REL_PATH.replace("\\", "/")
        row["artifact_sha256"] = sha
        row["installer_endpoint"] = "/install/test/latest2"
        row["source_version"] = _SOURCE_VERSION
        row["enabled"] = True
        row["build_mode"] = mode
        break
    manifest.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {manifest} test-latest2 artifact_sha256={sha} mode={mode}")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_v130_baseline(repo: Path, out: Path) -> str:
    src = repo / _V130_REL
    if not src.is_file():
        raise FileNotFoundError(f"Missing v1.3.0 artifact: {src}")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, out)
    sha = _sha256_file(out)
    v130_sha = ""
    for row in json.loads((repo / "data" / "rejoin_versions.json").read_text(encoding="utf-8")):
        if str(row.get("version") or "") == _SOURCE_VERSION:
            v130_sha = str(row.get("artifact_sha256") or "").strip()
            break
    if v130_sha and sha != v130_sha:
        raise RuntimeError(f"test/latest2 copy sha {sha} != v1.3.0 manifest sha {v130_sha}")
    return sha


def build_lime_channel(repo: Path, out: Path) -> str:
    sha = build_internal_test_tarball(
        repo,
        out,
        channel="test-latest2",
        version=_MANIFEST_VERSION,
        source_version=_SOURCE_VERSION,
    )
    verify_tarball_exclusions(out.read_bytes(), require_license_gate_strings=False)
    return sha


def main() -> int:
    ap = argparse.ArgumentParser(description="Build or seed test/latest2 artifact.")
    ap.add_argument("--repo-root", type=Path, default=_PROJECT_ROOT)
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output tarball (default: <repo>/{TEST_LATEST2_ARCHIVE_REL_PATH}).",
    )
    ap.add_argument(
        "--copy-v130",
        action="store_true",
        help="Seed test/latest2 as an exact byte copy of the v1.3.0 tarball.",
    )
    args = ap.parse_args()
    repo = args.repo_root.resolve()
    out = (args.out or (repo / TEST_LATEST2_ARCHIVE_REL_PATH)).resolve()

    if args.copy_v130:
        sha = copy_v130_baseline(repo, out)
        mode = "v1.3.0_copy"
    else:
        sha = build_lime_channel(repo, out)
        mode = "lime_detection"
    _update_manifest(repo, sha=sha, mode=mode)
    print(f"Wrote {out}")
    print(f"artifact_sha256={sha}")
    print(f"installer_endpoint=/install/test/latest2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
