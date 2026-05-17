"""Sanitized tarball for internal ``main-dev`` protected installs.

Built offline from the repo tree — excludes secrets, databases, caches, VCS,
and paths whose segments suggest credentials or sessions.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import subprocess
import tarfile
import time
from pathlib import Path

MAIN_DEV_ARCHIVE_REL_PATH = "releases/main-dev/deng-tool-rejoin-main-dev.tar.gz"

_TOPLEVEL_DIRS = ("agent", "bot", "scripts", "docs", "examples", "assets")

_ROOT_FILES = (
    "install.sh",
    "README.md",
    "VERSION",
    "SECURITY.md",
    "requirements-bot.txt",
    "requirements.txt",
    "pyproject.toml",
)

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        "node_modules",
        "htmlcov",
        ".idea",
        ".vscode",
        ".cursor",
        "logs",
        "run",
        "backups",
        "launcher",
        "dist",
        "build",
        ".egg-info",
    }
)

_SENSITIVE_MARKERS = ("token", "secret", "password", "credential", "cookie", "session")


def path_should_exclude(rel_posix: str) -> bool:
    """Return True if *rel_posix* must not appear in the internal test tarball."""
    lower = rel_posix.strip().lower().replace("\\", "/")
    if not lower or lower.startswith("../"):
        return True
    parts = lower.split("/")
    # Junk / secrets-by-location
    if parts[0] in {"data", "tests"}:
        return True
    if lower == ".env" or lower.endswith("/.env"):
        return True
    if lower.endswith((".db", ".sqlite", ".sqlite3")):
        return True
    for p in parts:
        pl = p.lower()
        if pl == ".env.example":
            continue
        if p in _SKIP_DIR_NAMES or (p.startswith(".") and pl != ".env.example"):
            return True
        if pl.endswith(".pyc") or pl.endswith(".pyo"):
            return True
        for m in _SENSITIVE_MARKERS:
            if m in pl:
                return True
    # Root-only junk Filenames
    if lower.endswith(
        (
            ".pid",
            ".sock",
            "pm2-out.log",
            "pm2-error.log",
            "ecosystem.config.js.map",
        )
    ):
        return True
    return False


def iter_internal_test_pack_files(repo_root: Path) -> list[tuple[str, Path]]:
    """Sorted (archive path posix, absolute file path) pairs."""
    repo_root = repo_root.resolve()
    out: list[tuple[str, Path]] = []

    for name in _ROOT_FILES:
        p = repo_root / name
        if p.is_file():
            arc = name.replace("\\", "/")
            if not path_should_exclude(arc):
                out.append((arc, p))

    for dirname in _TOPLEVEL_DIRS:
        base = repo_root / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_dir():
                continue
            try:
                rel = path.relative_to(repo_root).as_posix()
            except ValueError:
                continue
            if path_should_exclude(rel):
                continue
            out.append((rel, path))

    out.sort(key=lambda x: x[0])
    return out


def _git_commit_short(repo_root: Path) -> str:
    """Best-effort: ``git rev-parse --short=12 HEAD`` from the repo.

    Returns empty string if git is unavailable or the directory is not a repo.
    """
    try:
        res = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        sha = res.stdout.strip()
        return sha if res.returncode == 0 and sha else ""
    except Exception:  # noqa: BLE001
        return ""


def _make_build_info_bytes(
    repo_root: Path, *, channel: str = "main-dev",
) -> bytes:
    """Render the BUILD-INFO.json payload embedded into the tarball.

    The hash of the tarball itself is computed AFTER write, so it's added
    to ``.installed-build.json`` at install time, not here.  This file
    carries the parts the runtime cannot otherwise discover: git commit
    hash, build time, channel, repo origin.
    """
    info = {
        "channel": channel,
        "git_commit": _git_commit_short(repo_root),
        "built_at_iso": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "built_at_unix": int(time.time()),
        "product": "DENG Tool: Rejoin",
        "artifact_format_version": 1,
    }
    return json.dumps(info, indent=2, sort_keys=True).encode("utf-8")


def build_internal_test_tarball(repo_root: Path, output_tar_gz: Path) -> str:
    """Write gzip tarball and return lowercase SHA-256 hex digest of the file.

    Also embeds a top-level ``BUILD-INFO.json`` with git commit + build time
    so the runtime can prove what build it is even before the installer
    drops ``.installed-build.json``.
    """
    repo_root = repo_root.resolve()
    output_tar_gz = output_tar_gz.resolve()
    output_tar_gz.parent.mkdir(parents=True, exist_ok=True)
    pairs = iter_internal_test_pack_files(repo_root)
    if not pairs:
        raise RuntimeError("No files matched internal test artifact rules.")

    build_info_bytes = _make_build_info_bytes(repo_root)

    buf = io.BytesIO()
    digest = hashlib.sha256()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tf:
        # Sources first.
        for arcname, path in pairs:
            tf.add(path, arcname=arcname, recursive=False)
        # Then the build-info file.
        bi = tarfile.TarInfo(name="BUILD-INFO.json")
        bi.size = len(build_info_bytes)
        bi.mtime = int(time.time())
        bi.mode = 0o644
        tf.addfile(bi, io.BytesIO(build_info_bytes))
    raw = buf.getvalue()
    digest.update(raw)
    output_tar_gz.write_bytes(raw)
    return digest.hexdigest()


def verify_tarball_exclusions(tar_bytes: bytes) -> None:
    """Raise AssertionError if forbidden names appear inside tarball bytes.

    Also asserts the tarball contains a top-level ``BUILD-INFO.json`` so
    every published artifact carries its own build proof.
    """
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tf:
        names = [n for n in tf.getnames() if n.rstrip("/")]
    lowered = [n.lower().replace("\\", "/") for n in names]
    forbidden_roots = frozenset({"data", "tests", ".git"})
    for n in lowered:
        segs = n.split("/")
        assert forbidden_roots.isdisjoint(segs), n
        assert "node_modules" not in segs, n
        assert "__pycache__" not in segs, n
        assert ".pytest_cache" not in segs, n
        assert not n.endswith(".db"), n
        assert not n.endswith(".sqlite"), n
        assert not n.endswith(".sqlite3"), n
        if n == ".env" or n.endswith("/.env"):
            raise AssertionError(f"unexpected env file in tarball: {n}")
    if "BUILD-INFO.json" not in names:
        raise AssertionError("tarball missing BUILD-INFO.json — build proof is required")
