#!/usr/bin/env python3
"""DENG Tool: Rejoin — Release Package Builder.

Usage
─────
    python scripts/package_release.py [options]

    Options:
      --channel  stable|beta|dev   Release channel (default: stable)
      --version  X.Y.Z             Override version (default: read from VERSION)
      --notes    "text"            Release notes for the manifest
      --dist     /path/to/dist     Output directory (default: <project>/dist)
      --force                      Overwrite existing release

Output (in dist/releases/<channel>/<version>/):
    deng-tool-rejoin-<version>-<channel>.zip
    manifest.json
    SHA256SUMS.txt

The package includes ONLY client-side files needed on Android/Termux.

Included:
  agent/        — client Python modules
  scripts/      — selected client scripts (see CLIENT_SCRIPTS below)
  examples/     — config.example.json
  VERSION
  README.md
  INSTALL_TERMUX.md  (if present)
  SECURITY.md        (if present)
  install.sh

Excluded (never in the package):
  .env / *.env            secrets
  bot/                    Discord bot server code
  tests/                  test suite
  supabase/               DB migrations
  .git/                   version control data
  __pycache__/            Python bytecode
  dist/                   build outputs
  docs/                   server/dev documentation
  keydb.json              local key database
  license_store.json      local license store
  ecosystem.bot.json      PM2 server config
  requirements-bot.txt    server dependencies
  *.log / *.pid / *.lock  runtime files

Security guarantee: the builder verifies the output zip does NOT contain
any of the forbidden paths before writing the manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Locate project root ────────────────────────────────────────────────────────
_SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT: Path = _SCRIPTS_DIR.parent

# ── Channel definitions ────────────────────────────────────────────────────────
VALID_CHANNELS: frozenset[str] = frozenset({"stable", "beta", "dev"})

# ── Files / dirs to walk and include (relative to project root) ───────────────
_INCLUDE_DIRS: tuple[str, ...] = (
    "agent",
    "examples",
)

_INCLUDE_FILES: tuple[str, ...] = (
    "VERSION",
    "README.md",
    "INSTALL_TERMUX.md",
    "SECURITY.md",
    "install.sh",
)

# Only these script files are allowed in the client package
CLIENT_SCRIPTS: frozenset[str] = frozenset({
    "start-agent.sh",
    "stop-agent.sh",
    "status-agent.sh",
    "update.sh",
    "reset-agent.sh",
    "bootstrap_install.sh",
    "enable-termux-boot.sh",
    "termux-boot-template.sh",
})

# ── File/path exclusion rules ─────────────────────────────────────────────────
# Any path component matching one of these is excluded entirely
_EXCLUDED_COMPONENTS: frozenset[str] = frozenset({
    "bot",
    "tests",
    "supabase",
    "dist",
    ".git",
    "__pycache__",
})

# Any file whose name matches one of these is excluded
_EXCLUDED_FILENAMES: frozenset[str] = frozenset({
    ".env",
    "keydb.json",
    "license_store.json",
    "ecosystem.bot.json",
    "requirements-bot.txt",
    ".gitignore",
})

# Any file whose name matches these suffixes is excluded
_EXCLUDED_SUFFIXES: tuple[str, ...] = (
    ".env",
    ".pyc",
    ".pyo",
    ".log",
    ".pid",
    ".lock",
    ".bak",
    ".tmp",
    ".swp",
    ".secret",
)


# ── Exclusion predicate ────────────────────────────────────────────────────────

def _should_exclude(rel_path: str) -> bool:
    """Return True if *rel_path* (POSIX, relative to project root) should be excluded."""
    parts = rel_path.replace("\\", "/").split("/")

    # Excluded directory components anywhere in the path
    for part in parts[:-1]:  # all parts except the final filename
        if part in _EXCLUDED_COMPONENTS:
            return True
        # __pycache__ may appear at any depth
        if part == "__pycache__":
            return True

    filename = parts[-1]

    # Excluded exact filenames
    if filename in _EXCLUDED_FILENAMES:
        return True

    # .env at any name: config.env, production.env, etc.
    if re.search(r"\.env(\.|$)", filename.lower()):
        return True

    # Excluded suffixes
    fname_lower = filename.lower()
    if any(fname_lower.endswith(s) for s in _EXCLUDED_SUFFIXES):
        return True

    return False


def _verify_no_secrets(zip_path: Path) -> list[str]:
    """Return a list of forbidden paths found inside *zip_path*.

    Checks for .env, bot/, tests/, supabase/ — raises ValueError if any found.
    Used as a post-build security gate.
    """
    forbidden: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if _should_exclude(name):
                forbidden.append(name)
    return forbidden


# ── Collect files to package ──────────────────────────────────────────────────

def collect_package_files(project_root: Path) -> list[tuple[Path, str]]:
    """Return list of (absolute_path, zip_arcname) for the client package.

    The arcname is the relative path as it will appear inside the zip.
    """
    files: list[tuple[Path, str]] = []

    # Walk included directories
    for dir_name in _INCLUDE_DIRS:
        src_dir = project_root / dir_name
        if not src_dir.is_dir():
            continue
        for item in sorted(src_dir.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(project_root).as_posix()
            if _should_exclude(rel):
                continue
            files.append((item, rel))

    # Include scripts/ but only CLIENT_SCRIPTS
    scripts_dir = project_root / "scripts"
    if scripts_dir.is_dir():
        for item in sorted(scripts_dir.iterdir()):
            if item.is_file() and item.name in CLIENT_SCRIPTS:
                rel = item.relative_to(project_root).as_posix()
                if not _should_exclude(rel):
                    files.append((item, rel))

    # Include top-level files
    for fname in _INCLUDE_FILES:
        src = project_root / fname
        if src.is_file():
            rel = src.relative_to(project_root).as_posix()
            if not _should_exclude(rel):
                files.append((src, rel))

    return files


# ── Hash computation ──────────────────────────────────────────────────────────

def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── Package builder ───────────────────────────────────────────────────────────

def build_package(
    project_root: Path,
    channel: str,
    version: str,
    dist_root: Path,
    *,
    notes: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Build the release package and return the manifest dict.

    Creates:
      dist_root/releases/<channel>/<version>/
          deng-tool-rejoin-<version>-<channel>.zip
          manifest.json
          SHA256SUMS.txt

    Raises:
      ValueError  if channel is invalid, version is malformed, or secrets found.
      FileExistsError if the release already exists and force=False.
    """
    if channel not in VALID_CHANNELS:
        raise ValueError(f"Invalid channel '{channel}'. Must be one of: {sorted(VALID_CHANNELS)}")

    # Validate version format (X.Y.Z or X.Y or X)
    if not re.fullmatch(r"\d+(\.\d+){0,2}", version):
        raise ValueError(f"Invalid version format '{version}'. Expected X.Y.Z")

    release_dir = dist_root / "releases" / channel / version
    zip_name = f"deng-tool-rejoin-{version}-{channel}.zip"
    zip_path = release_dir / zip_name
    manifest_path = release_dir / "manifest.json"
    sums_path = release_dir / "SHA256SUMS.txt"

    if zip_path.exists() and not force:
        raise FileExistsError(
            f"Release already exists: {zip_path}\nUse --force to overwrite."
        )

    release_dir.mkdir(parents=True, exist_ok=True)

    # Collect files
    files = collect_package_files(project_root)
    if not files:
        raise ValueError("No files found to package. Check project structure.")

    # Build zip
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for abs_path, arcname in files:
            zf.write(abs_path, arcname)

    # Security gate: verify no secrets made it in
    forbidden = _verify_no_secrets(zip_path)
    if forbidden:
        zip_path.unlink()
        raise ValueError(
            f"Security check FAILED — {len(forbidden)} forbidden path(s) found in package:\n"
            + "\n".join(f"  {p}" for p in forbidden[:10])
            + ("\n  ..." if len(forbidden) > 10 else "")
        )

    sha256 = compute_sha256(zip_path)
    size_bytes = zip_path.stat().st_size
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    manifest: dict[str, Any] = {
        "app": "DENG Tool: Rejoin",
        "version": version,
        "channel": channel,
        "filename": zip_name,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "created_at": created_at,
        "min_client_version": "1.0.0",
        "notes": notes or f"Release {version} ({channel})",
        "file_count": len(files),
    }

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    sums_path.write_text(f"{sha256}  {zip_name}\n", encoding="utf-8")

    return manifest


# ── CLI entry point ───────────────────────────────────────────────────────────

def _read_version(project_root: Path) -> str:
    """Read version from VERSION file or agent/constants.py."""
    version_file = project_root / "VERSION"
    if version_file.exists():
        v = version_file.read_text(encoding="utf-8").strip()
        if v:
            return v

    constants_file = project_root / "agent" / "constants.py"
    if constants_file.exists():
        text = constants_file.read_text(encoding="utf-8")
        m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if m:
            return m.group(1)

    return "1.0.0"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a DENG Tool: Rejoin release package.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--channel",
        choices=sorted(VALID_CHANNELS),
        default="stable",
        help="Release channel (default: stable)",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Override version (default: read from VERSION file)",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Release notes to include in manifest.json",
    )
    parser.add_argument(
        "--dist",
        default=None,
        help="Output directory (default: <project>/dist)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing release",
    )
    parser.add_argument(
        "--project",
        default=str(PROJECT_ROOT),
        help="Project root directory (default: parent of this script)",
    )

    args = parser.parse_args(argv)

    project_root = Path(args.project).resolve()
    dist_root = Path(args.dist).resolve() if args.dist else project_root / "dist"
    version = args.version or _read_version(project_root)

    print(f"Building DENG Tool: Rejoin {version} ({args.channel})...")
    print(f"  Project root : {project_root}")
    print(f"  Output       : {dist_root}/releases/{args.channel}/{version}/")

    try:
        manifest = build_package(
            project_root,
            args.channel,
            version,
            dist_root,
            notes=args.notes,
            force=args.force,
        )
    except (ValueError, FileExistsError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\nUnexpected error: {exc}", file=sys.stderr)
        return 1

    zip_name = manifest["filename"]
    sha256 = manifest["sha256"]
    size_kb = manifest["size_bytes"] / 1024

    print(f"\nPackage built successfully:")
    print(f"  File    : {zip_name}")
    print(f"  SHA-256 : {sha256}")
    print(f"  Size    : {size_kb:.1f} KB")
    print(f"  Files   : {manifest['file_count']}")
    print(f"  Channel : {manifest['channel']}")
    print(f"  Notes   : {manifest['notes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
