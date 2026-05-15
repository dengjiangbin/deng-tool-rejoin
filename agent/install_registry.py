"""Version registry + artifact paths for protected installs (data/rejoin_versions.json).

Used by the license API — no discord imports.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from agent.rejoin_versions import default_versions_manifest_path


def public_install_base_url() -> str:
    """HTTPS base for curl installers (no trailing slash)."""
    for key in ("REJOIN_PUBLIC_INSTALL_URL", "LICENSE_API_PUBLIC_URL"):
        raw = (os.environ.get(key) or "").strip().rstrip("/")
        if raw:
            return raw
    return "https://rejoin.deng.my.id"


def load_registry_rows() -> list[dict[str, Any]]:
    path = default_versions_manifest_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [r for r in data if isinstance(r, dict)]


def row_enabled(row: dict[str, Any]) -> bool:
    return row.get("enabled") is not False


def _semver_tuple(version: str) -> tuple[int, ...]:
    v = (version or "").strip().lstrip("v").strip()
    if not v:
        return ()
    parts: list[int] = []
    for seg in re.split(r"[^\d]+", v):
        if not seg:
            continue
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(-1)
    return tuple(parts)


def is_public_stable_row(row: dict[str, Any]) -> bool:
    """Row eligible for public /install/latest candidate list."""
    if not row_enabled(row):
        return False
    ch = str(row.get("channel") or "stable").strip().lower()
    if ch != "stable":
        return False
    vis = str(row.get("visibility") or "").strip().lower()
    if vis in {"admin", "internal", "private", "owner"}:
        return False
    if vis != "public":
        # Legacy rows without visibility: allow only if visible is not explicitly false
        if row.get("visible") is False:
            return False
    ref = str(row.get("install_ref") or row.get("ref") or "").strip()
    if ref.startswith("refs/heads/"):
        return False
    if ref in {"", "main"}:
        return False
    return True


def resolve_latest_public_stable() -> dict[str, Any] | None:
    rows = [r for r in load_registry_rows() if is_public_stable_row(r)]
    if not rows:
        return None
    rows.sort(key=lambda r: _semver_tuple(str(r.get("version") or "")), reverse=True)
    return rows[0]


def is_admin_internal_row(row: dict[str, Any]) -> bool:
    """Manifest-only internal builds (e.g. main-dev): admin visibility + branch ref."""
    if not row_enabled(row):
        return False
    vis = str(row.get("visibility") or "").strip().lower()
    if vis not in {"admin", "internal", "private", "owner", "tester"}:
        return False
    ref = str(row.get("install_ref") or row.get("ref") or "").strip()
    return ref.startswith("refs/heads/")


def get_exact_registry_row(version: str) -> dict[str, Any] | None:
    want = (version or "").strip()
    if not want:
        return None
    for row in load_registry_rows():
        if not row_enabled(row):
            continue
        if str(row.get("version") or "").strip() == want:
            return row
    return None


def resolve_requested_public_version(requested: str) -> tuple[dict[str, Any] | None, str | None]:
    """Return (row, error_message). requested is ``latest`` or ``vX.Y.Z``."""
    req = (requested or "").strip().lower()
    if req == "latest":
        row = resolve_latest_public_stable()
        if row is None:
            return None, "No public stable release is configured yet."
        return row, None
    row = get_exact_registry_row(requested.strip())
    if row is None:
        return None, f"Unknown or disabled version: {requested}"
    if not is_public_stable_row(row):
        return None, "This version is not available for public install."
    return row, None


def artifact_path_for_row(row: dict[str, Any], root: Path) -> Path | None:
    rel = str(row.get("artifact_path") or "").strip().strip("/")
    if not rel:
        return None
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def get_artifact_root() -> Path | None:
    for key in ("REJOIN_ARTIFACT_ROOT", "LICENSE_DOWNLOAD_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            return p
    return None
