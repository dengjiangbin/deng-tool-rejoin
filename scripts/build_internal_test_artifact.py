#!/usr/bin/env python3
"""Build sanitized ``main-dev`` tarball for REJOIN_ARTIFACT_ROOT.

Writes ``releases/main-dev/deng-tool-rejoin-main-dev.tar.gz`` under repo root
(unless overridden) and prints SHA-256 for ``data/rejoin_versions.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.internal_test_artifact import (  # noqa: E402
    MAIN_DEV_ARCHIVE_REL_PATH,
    build_internal_test_tarball,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build internal main-dev release tarball.")
    ap.add_argument(
        "--repo-root",
        type=Path,
        default=_PROJECT_ROOT,
        help="Repository root (default: parent of scripts/).",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output file (default: <repo-root>/{MAIN_DEV_ARCHIVE_REL_PATH}).",
    )
    args = ap.parse_args()
    repo = args.repo_root.resolve()
    out = (args.out or (repo / MAIN_DEV_ARCHIVE_REL_PATH)).resolve()

    sha = build_internal_test_tarball(repo, out)
    manifest = repo / "data" / "rejoin_versions.json"
    print(f"Wrote {out}")
    print(f"artifact_sha256={sha}")
    if manifest.is_file():
        rows = json.loads(manifest.read_text(encoding="utf-8"))
        for row in rows:
            if str(row.get("version") or "").strip() == "main-dev":
                row["artifact_path"] = MAIN_DEV_ARCHIVE_REL_PATH.replace("\\", "/")
                row["artifact_sha256"] = sha
                row["installer_endpoint"] = "/install/test/latest"
                row["visibility"] = "admin"
                row["enabled"] = True
                break
        manifest.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {manifest} main-dev artifact_sha256")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
