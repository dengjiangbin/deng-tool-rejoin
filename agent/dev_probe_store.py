"""Server-side persistence for dev-probe uploads.

We keep each probe under ``data/dev_probes/`` as a single JSON file named
``p-<short-id>.json``.  The operator reads them directly off the PM2 host
filesystem; the HTTP layer only writes.

Design rules:

* Never trust the client to choose an id — we generate one server-side.
* Keep storage cheap; we rotate when the directory exceeds 200 files by
  deleting the oldest.  No DB.
* Reads return the parsed JSON dict so the API can render it as-is.
* All paths derived from a single PROBE_ROOT.  Override via env var
  ``DENG_DEV_PROBE_DIR`` so tests can isolate.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PROBE_ROOT = Path(__file__).resolve().parent.parent / "data" / "dev_probes"
MAX_PROBES = 200


def _root() -> Path:
    override = os.environ.get("DENG_DEV_PROBE_DIR")
    return Path(override) if override else DEFAULT_PROBE_ROOT


def _ensure_root() -> Path:
    p = _root()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _next_id() -> str:
    return "p-" + uuid.uuid4().hex[:10]


def _rotate(root: Path) -> None:
    """Delete oldest files when count exceeds ``MAX_PROBES``."""
    try:
        files = sorted(root.glob("p-*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    excess = len(files) - MAX_PROBES
    for f in files[:excess]:
        try:
            f.unlink()
        except OSError:
            pass


def store_probe(payload: dict[str, Any]) -> tuple[str, Path]:
    """Persist *payload* and return (probe_id, file path).

    Adds ``received_at_iso`` so the operator can correlate against PM2 logs.
    """
    root = _ensure_root()
    probe_id = _next_id()
    enriched = dict(payload)
    enriched["received_at_iso"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    enriched["probe_id"] = probe_id
    path = root / f"{probe_id}.json"
    # Write atomically by writing to a temp file then renaming.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(enriched, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    _rotate(root)
    return probe_id, path


def read_probe(probe_id: str) -> dict[str, Any] | None:
    """Return the JSON dict for ``probe_id`` or None when missing/invalid."""
    root = _root()
    path = root / f"{probe_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_probes(limit: int = 50) -> list[dict[str, Any]]:
    """Return up-to ``limit`` probe metadata entries, newest first."""
    root = _root()
    if not root.is_dir():
        return []
    try:
        files = sorted(root.glob("p-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for path in files[:limit]:
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append({
            "probe_id": path.stem,
            "size_bytes": stat.st_size,
            "mtime_iso": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return out
