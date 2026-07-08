#!/usr/bin/env python3
"""Install-safe version metadata — stdlib only, never imports ``agent``.

Used by the ``deng-rejoin`` shell wrapper and installer final verification.
Must not boot protected runtime, network, subprocess, or heavy agent modules.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_SHA64 = re.compile(r"^[0-9a-f]{64}$")


def _install_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _read_json(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _normalize_sha(value: object) -> str:
    sha = str(value or "").strip().lower()
    return sha if _SHA64.match(sha) else ""


def _resolve_artifact_sha(root: Path) -> str:
    """Return full 64-char lowercase artifact SHA from on-disk metadata."""
    for path, key in (
        (root / ".installed-build.json", "artifact_sha256"),
        (root / ".deng_build.json", "artifact_sha"),
        (root / "RELEASE-MANIFEST.json", "artifact_sha256"),
    ):
        sha = _normalize_sha(_read_json(path).get(key))
        if sha:
            return sha
    return ""


def _pick_str(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    json_mode = "--json" in args
    root = _install_root()

    sha = _resolve_artifact_sha(root)
    if not sha:
        return 1

    installed = _read_json(root / ".installed-build.json")
    deng_build = _read_json(root / ".deng_build.json")
    build_info = _read_json(root / "BUILD-INFO.json")

    version = _pick_str(
        installed.get("version"),
        deng_build.get("version"),
        build_info.get("version"),
    )
    channel = _pick_str(
        installed.get("channel"),
        deng_build.get("channel"),
        build_info.get("channel"),
    )
    build_id = _pick_str(
        installed.get("probe_id"),
        deng_build.get("build_id"),
        build_info.get("probe_id"),
    )
    build_time = _pick_str(
        installed.get("install_time_iso"),
        deng_build.get("build_time"),
        build_info.get("built_at_iso"),
    )
    git_commit = _pick_str(
        installed.get("git_commit"),
        deng_build.get("git_commit"),
        build_info.get("git_commit"),
    )

    if json_mode:
        payload = {
            "artifact_sha": sha,
            "version": version,
            "channel": channel,
            "build_id": build_id,
            "build_time": build_time,
            "git_commit": git_commit,
            "install_root": str(root),
        }
        sys.stdout.write(json.dumps(payload, sort_keys=True) + "\n")
        return 0

    # Mandatory machine-parseable line for installer grep.
    sys.stdout.write(f"artifact_sha={sha}\n")
    if version:
        sys.stdout.write(f"product_version: {version}\n")
    if channel:
        sys.stdout.write(f"channel: {channel}\n")
    if build_id:
        sys.stdout.write(f"build_id: {build_id}\n")
    if build_time:
        sys.stdout.write(f"build_time: {build_time}\n")
    if git_commit:
        sys.stdout.write(f"git_commit: {git_commit}\n")
    sys.stdout.write(f"install_root: {root}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
