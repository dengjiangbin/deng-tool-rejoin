"""Security utilities for DENG Tool: Rejoin.

Provides:
  - secure_install_permissions(path)  — chmod 700 dir / 600 files (Unix only)
  - secure_file_permissions(path)     — chmod 600 on a single file (Unix only)
  - compute_file_sha256(path)         — SHA-256 hex digest
  - verify_sha256(path, expected)     — True if digest matches expected
  - mask_secret(value, show=4)        — first N + *** + last N

All functions are safe to call on Windows; chmod operations are silently
skipped there (Windows ACLs would need to be set via icacls instead).
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

_IS_UNIX: bool = sys.platform != "win32"

# ── Permission helpers ─────────────────────────────────────────────────────────


def secure_install_permissions(path: Path | str) -> None:
    """Set secure permissions on an install directory tree.

    Unix:
      - Directories → 0o700  (rwx for owner only)
      - Files       → 0o600  (rw for owner only)

    Windows: no-op; Windows ACLs must be managed separately (icacls).

    Errors on individual items are silently ignored so the function never
    raises — best-effort protection is better than a hard crash.
    """
    if not _IS_UNIX:
        return
    root = Path(path)
    try:
        if root.is_dir():
            os.chmod(root, 0o700)
            for item in root.rglob("*"):
                try:
                    if item.is_symlink():
                        continue  # Do not follow or chmod symlinks
                    if item.is_dir():
                        os.chmod(item, 0o700)
                    elif item.is_file():
                        os.chmod(item, 0o600)
                except OSError:
                    pass
        elif root.is_file():
            os.chmod(root, 0o600)
    except OSError:
        pass


def secure_file_permissions(path: Path | str) -> None:
    """Set 0o600 on a single file (owner read/write only). Unix only."""
    if not _IS_UNIX:
        return
    try:
        os.chmod(Path(path), 0o600)
    except OSError:
        pass


# ── Hash helpers ───────────────────────────────────────────────────────────────


def compute_file_sha256(path: Path | str) -> str:
    """Return the SHA-256 hex digest of a file.

    Reads in 64 KB chunks so it works on large packages without loading them
    entirely into memory.

    Raises:
        OSError: if the file cannot be read.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256(path: Path | str, expected: str) -> bool:
    """Return True if the file's SHA-256 matches *expected* (case-insensitive).

    Returns False (not raises) if the file cannot be read or the hash is empty.
    """
    if not expected or not expected.strip():
        return False
    try:
        actual = compute_file_sha256(path)
    except OSError:
        return False
    return actual.lower() == expected.strip().lower()


# ── Secret masking ─────────────────────────────────────────────────────────────


def mask_secret(value: str, *, show: int = 4) -> str:
    """Return a display-safe version of a secret string.

    Shows up to *show* characters at the start and end; everything in between
    is replaced with ``***``.  If the value is shorter than ``2 × show``
    characters, the entire string is masked.

    Examples::

        mask_secret("DENG-ABCDEF012345", show=4)  → "DENG...2345"
        mask_secret("short")                        → "***"
        mask_secret("")                             → "***"
    """
    if not value:
        return "***"
    if len(value) <= show * 2:
        return "***"
    return f"{value[:show]}***{value[-show:]}"
