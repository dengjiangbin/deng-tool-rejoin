#!/usr/bin/env python3
"""Build protected Rejoin install artifacts for every enabled release row."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.internal_test_artifact import (  # noqa: E402
    build_internal_test_tarball,
    verify_tarball_exclusions,
)
from agent.install_registry import row_enabled  # noqa: E402


def _rows_to_build(rows: list[dict]) -> list[dict]:
    """Every enabled install row that ships a tarball (stable + main-dev)."""
    built: list[dict] = []
    for row in rows:
        if row.get("kind") == "channel_pointers":
            continue
        if not row_enabled(row):
            continue
        version = str(row.get("version") or "").strip()
        if not version:
            continue
        rel = str(row.get("artifact_path") or "").strip()
        if not rel:
            continue
        built.append(row)
    return built


def _channel_for_row(row: dict) -> str:
    version = str(row.get("version") or "").strip()
    if version == "main-dev":
        return "main-dev"
    return str(row.get("channel") or "stable").strip() or "stable"


def main() -> int:
    ap = argparse.ArgumentParser(description="Build all enabled Rejoin install artifacts.")
    ap.add_argument("--repo-root", type=Path, default=_PROJECT_ROOT)
    args = ap.parse_args()
    repo = args.repo_root.resolve()
    manifest_path = repo / "data" / "rejoin_versions.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))

    stable_latest = ""
    for row in rows:
        if row.get("kind") == "channel_pointers":
            stable_latest = str(row.get("stable_latest") or "").strip()
            break

    report: list[dict] = []
    for row in _rows_to_build(rows):
        version = str(row.get("version") or "").strip()
        rel = str(row.get("artifact_path") or "").strip()
        out = repo / rel
        channel = _channel_for_row(row)
        sha = build_internal_test_tarball(
            repo,
            out,
            channel=channel,
            version=version,
        )
        verify_tarball_exclusions(out.read_bytes(), require_license_gate_strings=True)
        row["artifact_sha256"] = sha
        row["enabled"] = True
        import tarfile

        with tarfile.open(out, mode="r:gz") as tf:
            build_info = json.loads(tf.extractfile("BUILD-INFO.json").read().decode("utf-8"))
        license_gate = "key-free (test channel only)" if version == "main-dev" else "license-gated"
        endpoint = str(row.get("installer_endpoint") or f"/install/{version}")
        report.append(
            {
                "version": version,
                "channel": channel,
                "installer_endpoint": endpoint,
                "artifact_path": rel.replace("\\", "/"),
                "artifact_sha256": sha,
                "git_commit": build_info.get("git_commit"),
                "built_at_iso": build_info.get("built_at_iso"),
                "license_gate": license_gate,
                "stable_latest": version == stable_latest,
            }
        )
        print(f"built {version} sha256={sha} commit={build_info.get('git_commit')}")

    for row in rows:
        if row.get("kind") == "channel_pointers" and stable_latest:
            row["stable_latest"] = stable_latest
            row["test_latest"] = "main-dev"

    manifest_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    proof_path = repo / "data" / "rejoin_artifact_build_proof.json"
    proof_path.write_text(json.dumps({"artifacts": report}, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {manifest_path}")
    print(f"Wrote {proof_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
