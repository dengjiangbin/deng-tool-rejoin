#!/usr/bin/env python3
"""Build or seed the isolated test/latest2 channel artifact.

Phase 1 (baseline proof — true v1.3.0 tag source, no main-dev start flow):
  python scripts/build_test_latest2_artifact.py --copy-v130

Phase 2 (Lime detection on v1.3.0 tag — NOT main-dev HEAD or rebuilt stable tarball):
  python scripts/build_test_latest2_artifact.py --lime-on-v130

Never use the default no-flag build: that compiles current main HEAD and
behaves like test/latest.  Always pass --copy-v130 or --lime-on-v130.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.internal_test_artifact import (  # noqa: E402
    TEST_LATEST2_ARCHIVE_REL_PATH,
    build_internal_test_tarball,
    verify_tarball_exclusions,
)

_V130_TAG = "refs/tags/v1.3.0"
_MANIFEST_VERSION = "test-latest2"
_SOURCE_VERSION = "v1.3.0"

# Lime-only overlay onto the v1.3.0 **tag** source tree (refs/tags/v1.3.0).
# Do NOT overlay commands.py, start_lifecycle.py, checker_pointer.py, or other
# post-v1.3.0 main-dev deltas — those make test/latest2 behave like test/latest.
_LIME_OVERLAY_FILES = (
    "agent/lime_channel.py",
    "agent/lime_detection_speed.py",
    "agent/rjn_lifecycle_monitor.py",
    "agent/force_close_race.py",
    "agent/roblox_disconnect_reasons.py",
    "agent/ocr_screen_detector.py",
    "agent/webhook.py",
    "agent/detection_speed_test.py",
    "agent/lime_cli_dispatch.py",
    "agent/test_latest2_runtime_patch.py",
    "agent/test_latest2_monitoring_relay.py",
    "agent/lime_package_discovery.py",
    "agent/launcher.py",
    "agent/banner.py",
    "agent/package_online_evidence.py",
    "agent/probe.py",
    "agent/license.py",
    "agent/build_info.py",
)


def _update_manifest(repo: Path, *, sha: str, mode: str, base_git_commit: str = "") -> None:
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
        if base_git_commit:
            row["base_git_commit"] = base_git_commit
        break
    manifest.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {manifest} test-latest2 artifact_sha256={sha} mode={mode}")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _v130_tag_commit(repo: Path) -> str:
    """Git commit for the frozen public v1.3.0 tag (NOT rebuilt tarball BUILD-INFO).

    The published ``releases/v1.3.0/*.tar.gz`` has been rebuilt with post-v1.3.0
    main-dev commits (Preparing/Monitoring checker flow).  test/latest2 must
    compile from ``refs/tags/v1.3.0`` so Start UX matches real stable v1.3.0.
    """
    proc = subprocess.run(
        ["git", "rev-parse", f"{_V130_TAG}^{{commit}}"],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    commit = proc.stdout.strip()
    if not commit:
        raise RuntimeError(f"{_V130_TAG} did not resolve to a commit")
    return commit


def _assert_not_post_v130_main_dev(repo: Path, commit: str) -> None:
    """Fail fast when the base commit already contains main-dev checker start flow."""
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}:agent/checker_pointer.py"],
        cwd=str(repo),
        capture_output=True,
    )
    if proc.returncode == 0:
        raise RuntimeError(
            f"test/latest2 base commit {commit[:12]} includes checker_pointer.py "
            "(post-v1.3.0 main-dev start flow); use refs/tags/v1.3.0"
        )


def copy_v130_baseline(repo: Path, out: Path) -> tuple[str, str]:
    """Build a true v1.3.0-tag artifact (no lime overlay, no main-dev start flow)."""
    base_commit = _v130_tag_commit(repo)
    _assert_not_post_v130_main_dev(repo, base_commit)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deng-test-latest2-copy-") as tmp:
        wt = Path(tmp) / "worktree"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), base_commit],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            sha = build_internal_test_tarball(
                wt,
                out,
                channel="test-latest2",
                version=_MANIFEST_VERSION,
                source_version=_SOURCE_VERSION,
            )
            verify_tarball_exclusions(out.read_bytes(), require_license_gate_strings=False)
        finally:
            subprocess.run(
                ["git", "worktree", "remove", str(wt), "--force"],
                cwd=str(repo),
                check=False,
                capture_output=True,
                text=True,
            )
            subprocess.run(["git", "worktree", "prune"], cwd=str(repo), check=False, capture_output=True)
    return sha, base_commit


def _overlay_lime_files(source_repo: Path, worktree: Path) -> None:
    for rel in _LIME_OVERLAY_FILES:
        src = source_repo / rel
        dst = worktree / rel
        if not src.is_file():
            raise FileNotFoundError(f"Lime overlay missing: {rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    _validate_lime_overlay_deps(worktree)
    _validate_v130_supervisor_webhook_api(worktree)


def _validate_v130_supervisor_webhook_api(worktree: Path) -> None:
    """v1.3.0-tag supervisor calls webhook helpers added after the tag."""
    import ast
    import re

    supervisor = worktree / "agent" / "supervisor.py"
    webhook = worktree / "agent" / "webhook.py"
    if not supervisor.is_file() or not webhook.is_file():
        return
    needed = set(re.findall(r"lifecycle_webhook\.([A-Za-z_][A-Za-z0-9_]*)", supervisor.read_text(encoding="utf-8")))
    tree = ast.parse(webhook.read_text(encoding="utf-8"))
    defined = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = sorted(name for name in needed if name not in defined)
    if missing:
        raise RuntimeError(
            "v1.3.0 supervisor expects webhook API missing after overlay: "
            + ", ".join(missing)
        )


def _validate_lime_overlay_deps(worktree: Path) -> None:
    """Ensure overlaid modules do not import missing agent files on v1.3.0 base."""
    import ast

    overlay_set = set(_LIME_OVERLAY_FILES)

    def _resolve(path: Path, node: ast.ImportFrom) -> str | None:
        mod = str(node.module or "")
        if mod.startswith("agent."):
            return "agent/" + mod[len("agent.") :].replace(".", "/") + ".py"
        if node.level and path.parent.name == "agent" and node.level == 1:
            if mod:
                return f"agent/{mod.replace('.', '/')}.py"
            if len(node.names) == 1 and node.names[0].name != "*":
                return f"agent/{node.names[0].name}.py"
        return None

    missing: set[str] = set()
    for rel in _LIME_OVERLAY_FILES:
        path = worktree / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            dep = _resolve(path, node)
            if not dep or dep in overlay_set or (worktree / dep).is_file():
                continue
            missing.add(dep.replace("\\", "/"))
    if missing:
        raise RuntimeError(
            "lime overlay imports missing on v1.3.0 base: "
            + ", ".join(sorted(missing))
        )


def build_lime_on_v130(repo: Path, out: Path) -> tuple[str, str]:
    """Compile v1.3.0 tag source + lime-only overlay — never current main HEAD."""
    base_commit = _v130_tag_commit(repo)
    _assert_not_post_v130_main_dev(repo, base_commit)
    with tempfile.TemporaryDirectory(prefix="deng-test-latest2-") as tmp:
        wt = Path(tmp) / "worktree"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(wt), base_commit],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            _overlay_lime_files(repo, wt)
            sha = build_internal_test_tarball(
                wt,
                out,
                channel="test-latest2",
                version=_MANIFEST_VERSION,
                source_version=_SOURCE_VERSION,
            )
            verify_tarball_exclusions(out.read_bytes(), require_license_gate_strings=False)
            with tarfile.open(out, mode="r:gz") as tf:
                bi = json.loads(tf.extractfile("BUILD-INFO.json").read().decode("utf-8"))
            built_from = str(bi.get("git_commit") or "")
            if not built_from.startswith(base_commit[:8]):
                raise RuntimeError(
                    f"test/latest2 lime build git_commit {built_from!r} != v1.3.0 base {base_commit!r}"
                )
            if str(bi.get("source_version") or "") != _SOURCE_VERSION:
                raise RuntimeError("BUILD-INFO missing source_version=v1.3.0")
            return sha, base_commit
        finally:
            subprocess.run(
                ["git", "worktree", "remove", str(wt), "--force"],
                cwd=str(repo),
                check=False,
                capture_output=True,
                text=True,
            )
            subprocess.run(["git", "worktree", "prune"], cwd=str(repo), check=False, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build or seed test/latest2 artifact.")
    ap.add_argument("--repo-root", type=Path, default=_PROJECT_ROOT)
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output tarball (default: <repo>/{TEST_LATEST2_ARCHIVE_REL_PATH}).",
    )
    mode_group = ap.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--copy-v130",
        action="store_true",
        help="Build true v1.3.0 tag source with no lime overlay (not rebuilt stable tarball bytes).",
    )
    mode_group.add_argument(
        "--lime-on-v130",
        action="store_true",
        help="Build v1.3.0 source + lime-only overlay (NOT main-dev HEAD).",
    )
    args = ap.parse_args()
    repo = args.repo_root.resolve()
    out = (args.out or (repo / TEST_LATEST2_ARCHIVE_REL_PATH)).resolve()

    if args.copy_v130:
        sha, base_commit = copy_v130_baseline(repo, out)
        mode = "v1.3.0_copy"
    else:
        sha, base_commit = build_lime_on_v130(repo, out)
        mode = "lime_on_v130"

    _update_manifest(repo, sha=sha, mode=mode, base_git_commit=base_commit)
    print(f"Wrote {out}")
    print(f"artifact_sha256={sha}")
    print(f"base_git_commit={base_commit}")
    print(f"installer_endpoint=/install/test/latest2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
