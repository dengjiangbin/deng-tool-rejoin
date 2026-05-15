"""Install version list for DENG Tool: Rejoin (GitHub tags + manifest).

Supports **public** stable installs (tags / visible manifest rows) and **internal**
dev/beta/manifest-only rows for owner/admin/tester visibility only.

No discord.py imports — safe for agent tests and the license panel builders.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class RejoinVersionInfo:
    """One selectable install target."""

    version: str
    channel: str = "stable"
    label: str = ""
    install_ref: str = ""
    recommended: bool = False
    description: str = ""
    #: True → show internal/testing disclaimer in install text; hidden from public list.
    internal_only: bool = False

    def __post_init__(self) -> None:
        if not self.install_ref:
            object.__setattr__(self, "install_ref", f"refs/tags/{self.version}" if self.version else "")
        if not self.label:
            ch = self.channel.capitalize()
            object.__setattr__(self, "label", f"{self.version} {ch}")


def default_versions_manifest_path() -> Path:
    """JSON list path (repo root ``data/rejoin_versions.json``)."""
    override = (os.environ.get("REJOIN_VERSIONS_MANIFEST") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[1] / "data" / "rejoin_versions.json"


def github_owner() -> str:
    return (os.environ.get("REJOIN_GITHUB_OWNER") or "dengjiangbin").strip()


def github_repo() -> str:
    return (os.environ.get("REJOIN_GITHUB_REPO") or "deng-tool-rejoin").strip()


def fetch_github_tag_names(*, timeout: float = 12.0) -> list[str] | None:
    """Return tag names from GitHub API, or ``None`` if the request failed."""
    owner, repo = github_owner(), github_repo()
    url = f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=100"
    req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "deng-tool-rejoin"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, HTTPError, URLError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    names: list[str] = []
    for row in data:
        if isinstance(row, dict):
            name = str(row.get("name") or "").strip()
            if name:
                names.append(name)
    return names


def _load_manifest_raw() -> list[dict[str, Any]]:
    path = default_versions_manifest_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _norm_version(v: str) -> str:
    return str(v or "").strip()


def _channel_rank(ch: str) -> int:
    c = (ch or "stable").strip().lower()
    return {"stable": 0, "beta": 1, "dev": 2}.get(c, 99)


def _manifest_row_public_allowed(row: dict[str, Any]) -> bool:
    """Manifest rows marked ``visible: false`` or ``visibility: admin`` are never public."""
    vis = str(row.get("visibility") or "").strip().lower()
    if vis in {"admin", "internal", "private", "owner"}:
        return False
    if row.get("visible") is False:
        return False
    return True


def _install_ref_public_allowed(ref: str) -> bool:
    """Branch installs (``refs/heads/…``) are never offered on the public list."""
    r = (ref or "").strip()
    if r in {"", "main"}:
        return False
    if r.startswith("refs/heads/"):
        return False
    return True


def _compute_internal_only(
    channel: str,
    install_ref: str,
    manifest_public: bool,
    public_beta: bool,
) -> bool:
    """Internal builds get extra disclosure text and are omitted from the public picker."""
    ch = channel.lower()
    if not _install_ref_public_allowed(install_ref):
        return True
    if not manifest_public:
        return True
    if ch == "dev":
        return True
    if ch == "beta" and not public_beta:
        return True
    return False


def merge_version_sources(
    *,
    tag_names: list[str] | None,
    include_internal_channels: bool = False,
    include_dev_for_admin: bool | None = None,
) -> list[RejoinVersionInfo]:
    """Combine GitHub tags with ``data/rejoin_versions.json``.

    When ``include_internal_channels`` is False (default), only rows safe for the
    public picker are returned (no ``refs/heads/…``, no admin-only manifest rows,
    no dev channel, beta only if ``REJOIN_PUBLIC_BETA``).

    ``include_dev_for_admin`` is a deprecated alias for ``include_internal_channels``.
    """
    if include_dev_for_admin is not None:
        include_internal_channels = bool(include_dev_for_admin)

    raw_manifest = _load_manifest_raw()
    by_version: dict[str, dict[str, Any]] = {}
    for row in raw_manifest:
        if not isinstance(row, dict):
            continue
        ver = _norm_version(str(row.get("version") or ""))
        if not ver:
            continue
        by_version[ver] = row

    public_beta = (os.environ.get("REJOIN_PUBLIC_BETA") or "").strip().lower() in {"1", "true", "yes", "on"}

    out: dict[str, RejoinVersionInfo] = {}

    tags = list(tag_names) if tag_names else []
    for ver in tags:
        row = by_version.get(ver)
        if row is not None and row.get("enabled") is False:
            continue
        if row is not None:
            ref = str(row.get("install_ref") or row.get("ref") or f"refs/tags/{ver}")
            ch = str(row.get("channel") or "stable").lower()
            label = str(row.get("label") or row.get("title") or f"{ver} {ch.capitalize()}")
            desc = str(row.get("description") or row.get("notes") or "")[:200]
            rec = bool(row.get("recommended"))
            mpub = _manifest_row_public_allowed(row)
        else:
            ref = f"refs/tags/{ver}"
            ch = "stable"
            label = f"{ver} Stable"
            desc = ""
            rec = False
            mpub = True
        io = _compute_internal_only(ch, ref, mpub, public_beta)
        out[ver] = RejoinVersionInfo(
            version=ver,
            channel=ch,
            label=label[:256],
            install_ref=ref,
            recommended=rec,
            description=desc,
            internal_only=io,
        )

    for ver, row in by_version.items():
        if row.get("enabled") is False:
            continue
        if ver in out:
            continue
        ref = str(row.get("install_ref") or row.get("ref") or f"refs/tags/{ver}")
        ch = str(row.get("channel") or "stable").lower()
        label = str(row.get("label") or row.get("title") or f"{ver} {ch.capitalize()}")
        desc = str(row.get("description") or row.get("notes") or "")[:200]
        rec = bool(row.get("recommended"))
        mpub = _manifest_row_public_allowed(row)
        io = _compute_internal_only(ch, ref, mpub, public_beta)
        out[ver] = RejoinVersionInfo(
            version=ver,
            channel=ch,
            label=label[:256],
            install_ref=ref,
            recommended=rec,
            description=desc,
            internal_only=io,
        )

    items = list(out.values())

    def sort_key(v: RejoinVersionInfo) -> tuple[int, int, str]:
        rec = 0 if v.recommended else 1
        return (_channel_rank(v.channel), rec, v.version)

    items.sort(key=sort_key)

    if include_internal_channels:
        return items
    return [v for v in items if not v.internal_only]


def list_public_rejoin_versions(
    *,
    include_internal_channels: bool = False,
    include_dev_for_admin: bool | None = None,
) -> list[RejoinVersionInfo]:
    """GitHub tags + manifest merge. Pass ``include_internal_channels=True`` for internal picker."""
    if include_dev_for_admin is not None:
        include_internal_channels = bool(include_dev_for_admin)
    raw = fetch_github_tag_names()
    tag_list: list[str] = [] if raw is None else list(raw)
    return merge_version_sources(tag_names=tag_list, include_internal_channels=include_internal_channels)


def build_full_install_command(owner: str, repo: str, install_ref: str) -> str:
    """Legacy one-liner using GitHub **raw** ``install.sh`` (internal/dev only).

    Public panel copy uses :func:`build_public_install_curl_command` instead.
    """
    ref = install_ref.strip()
    raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/install.sh"
    return (
        f"DENG_REJOIN_INSTALL_REF={ref} curl -fsSL {raw} -o install.sh && "
        f"DENG_REJOIN_INSTALL_REF={ref} bash install.sh"
    )


def build_public_install_curl_command(info: RejoinVersionInfo) -> str:
    """Public tutorial / panel curl — ``https://rejoin.deng.my.id/install/...``."""
    from agent.install_registry import public_install_base_url, resolve_latest_public_stable

    base = public_install_base_url().rstrip("/")
    if info.internal_only:
        return f"curl -fsSL {base}/install/test/latest -o install.sh && bash install.sh"

    latest = resolve_latest_public_stable()
    latest_ver = str(latest.get("version") or "").strip() if latest else ""
    if latest_ver and latest_ver == info.version.strip():
        path = "/install/latest"
    else:
        path = f"/install/{info.version.strip()}"
    return f"curl -fsSL {base}{path} -o install.sh && bash install.sh"


def format_install_instructions_plain(info: RejoinVersionInfo) -> str:
    """Plain text for Discord (Desktop + Mobile copy blocks)."""
    cmd = build_public_install_curl_command(info)
    lines = [
        f"DENG Tool: Rejoin Install — {info.version}",
        "",
    ]
    if info.internal_only:
        lines.extend([
            "Selected version:",
            info.version,
            f"Channel: {info.channel}",
            "Visibility: owner/admin/tester only",
            "",
            "Internal testing only — not a public stable release.",
            "",
        ])
    else:
        lines.extend([
            "Selected version:",
            info.label,
            "",
        ])
    lines.extend([
        "Desktop Copy:",
        f"```{cmd}```",
        "",
        "Mobile Copy:",
        f"```{cmd}```",
        "",
        "After install:",
        "deng-rejoin",
    ])
    return "\n".join(lines)


NO_PUBLIC_VERSIONS_MESSAGE = (
    "No public versions are available yet.\n\nPlease wait for the next public release."
)
