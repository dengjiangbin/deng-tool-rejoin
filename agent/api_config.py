"""Central API base URL resolution for DENG Tool: Rejoin runtime.

Runtime network calls (probe upload, license check, dev-probe fetch) MUST use
:func:`resolve_api_base_url`.  There are no silent fallbacks to production
when an operator sets ``DENG_API_URL`` or ``DENG_REJOIN_INSTALL_API``.

Priority (first non-empty wins — no mixing):
  1. ``DENG_API_URL``
  2. ``DENG_REJOIN_INSTALL_API``
  3. ``$DENG_REJOIN_HOME/.install_api`` (first line) when ``allow_install_file=True``
  4. Built-in default ONLY when ``allow_default=True`` (installer/bootstrap scripts)
"""

from __future__ import annotations

import os
from pathlib import Path

# Installer/bootstrap only — never used for runtime when allow_default=False.
_DEFAULT_PUBLIC_API = "https://rejoin.deng.my.id"

_ENV_DENG_API_URL = "DENG_API_URL"
_ENV_DENG_REJOIN_INSTALL_API = "DENG_REJOIN_INSTALL_API"


def _app_home() -> Path:
    raw = (os.environ.get("DENG_REJOIN_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".deng-tool" / "rejoin"


def _install_api_file(app_home: Path | None = None) -> Path:
    home = app_home if app_home is not None else _app_home()
    return home / ".install_api"


def resolve_api_base_url(
    *,
    app_home: Path | None = None,
    allow_install_file: bool = True,
    allow_default: bool = False,
) -> str:
    """Return API base URL without trailing slash, or ``""`` when unset."""
    for key in (_ENV_DENG_API_URL, _ENV_DENG_REJOIN_INSTALL_API):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            return raw.rstrip("/")

    if allow_install_file:
        path = _install_api_file(app_home)
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            line = text.strip().splitlines()[0].strip() if text.strip() else ""
            if line:
                return line.rstrip("/")

    if allow_default:
        return _DEFAULT_PUBLIC_API.rstrip("/")
    return ""


def resolve_install_api(app_home: Path | None = None) -> str:
    """Install API URL — same as runtime base; default allowed for fresh installs."""
    return resolve_api_base_url(
        app_home=app_home,
        allow_install_file=True,
        allow_default=True,
    )


def dev_probe_upload_url(*, app_home: Path | None = None) -> str:
    base = resolve_api_base_url(app_home=app_home, allow_install_file=True, allow_default=True)
    if not base:
        return ""
    return f"{base}/api/dev-probe/upload"


def dev_probe_fetch_url(probe_id: str, *, app_home: Path | None = None) -> str:
    probe_id = str(probe_id or "").strip()
    base = resolve_api_base_url(app_home=app_home, allow_install_file=True, allow_default=True)
    if not base or not probe_id:
        return ""
    return f"{base}/api/dev-probe/{probe_id}"


def license_server_url(*, app_home: Path | None = None) -> str:
    """License / authorize API base — respects env overrides only."""
    return resolve_api_base_url(
        app_home=app_home,
        allow_install_file=True,
        allow_default=True,
    )
