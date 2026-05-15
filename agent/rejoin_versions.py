"""Public install version list for DENG Tool: Rejoin (GitHub tags + manifest).

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


def merge_version_sources(
    *,
    tag_names: list[str] | None,
    include_dev_for_admin: bool = False,
) -> list[RejoinVersionInfo]:
    """Combine GitHub tags with ``data/rejoin_versions.json`` (overrides / visibility)."""
    raw_manifest = _load_manifest_raw()
    by_version: dict[str, dict[str, Any]] = {}
    for row in raw_manifest:
        if not isinstance(row, dict):
            continue
        ver = _norm_version(str(row.get("version") or ""))
        if not ver:
            continue
        by_version[ver] = row

    out: dict[str, RejoinVersionInfo] = {}

    tags = list(tag_names) if tag_names else []
    for ver in tags:
        if ver in by_version:
            row = by_version[ver]
            if row.get("visible") is False:
                continue
            ref = str(row.get("install_ref") or row.get("ref") or f"refs/tags/{ver}")
            ch = str(row.get("channel") or "stable").lower()
            label = str(row.get("label") or row.get("title") or f"{ver} {ch.capitalize()}")
            out[ver] = RejoinVersionInfo(
                version=ver,
                channel=ch,
                label=label[:256],
                install_ref=ref,
                recommended=bool(row.get("recommended")),
                description=str(row.get("description") or row.get("notes") or "")[:200],
            )
        else:
            out[ver] = RejoinVersionInfo(
                version=ver,
                channel="stable",
                label=f"{ver} Stable",
                install_ref=f"refs/tags/{ver}",
                recommended=False,
                description="",
            )

    for ver, row in by_version.items():
        if row.get("visible") is False:
            continue
        if ver in out:
            continue
        ref = str(row.get("install_ref") or row.get("ref") or f"refs/tags/{ver}")
        ch = str(row.get("channel") or "stable").lower()
        label = str(row.get("label") or row.get("title") or f"{ver} {ch.capitalize()}")
        out[ver] = RejoinVersionInfo(
            version=ver,
            channel=ch,
            label=label[:256],
            install_ref=ref,
            recommended=bool(row.get("recommended")),
            description=str(row.get("description") or row.get("notes") or "")[:200],
        )
    # Never offer raw main branch as a public install ref
    drop = [k for k, v in out.items() if v.install_ref in {"main", "refs/heads/main"}]
    for k in drop:
        del out[k]

    items = list(out.values())

    def sort_key(v: RejoinVersionInfo) -> tuple[int, int, str]:
        rec = 0 if v.recommended else 1
        return (_channel_rank(v.channel), rec, v.version)

    items.sort(key=sort_key)

    public_beta = (os.environ.get("REJOIN_PUBLIC_BETA") or "").strip() in {"1", "true", "yes"}
    filtered: list[RejoinVersionInfo] = []
    for v in items:
        ch = v.channel.lower()
        if ch in {"dev"} and not include_dev_for_admin:
            continue
        if ch in {"beta"} and not public_beta and not include_dev_for_admin:
            continue
        filtered.append(v)

    return filtered


def list_public_rejoin_versions(*, include_dev_for_admin: bool = False) -> list[RejoinVersionInfo]:
    """Preferred: GitHub tags; manifest merges metadata. If the API fails, manifest-only."""
    raw = fetch_github_tag_names()
    tag_list: list[str] = [] if raw is None else list(raw)
    return merge_version_sources(tag_names=tag_list, include_dev_for_admin=include_dev_for_admin)


def build_full_install_command(owner: str, repo: str, install_ref: str) -> str:
    """One-line install recommended for tagged refs (sets env for ``install.sh``)."""
    ref = install_ref.strip()
    raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/install.sh"
    return (
        f"DENG_REJOIN_INSTALL_REF={ref} curl -fsSL {raw} -o install.sh && "
        f"DENG_REJOIN_INSTALL_REF={ref} bash install.sh"
    )


def format_install_instructions_plain(info: RejoinVersionInfo) -> str:
    """Plain text for Discord (Desktop + Mobile copy blocks use the same command)."""
    owner, repo = github_owner(), github_repo()
    cmd = build_full_install_command(owner, repo, info.install_ref)
    lines = [
        f"DENG Tool: Rejoin Install — {info.version}",
        "",
        "Selected version:",
        info.label,
        "",
        "Desktop Copy:",
        f"```{cmd}```",
        "",
        "Mobile Copy:",
        f"```{cmd}```",
        "",
        "After install:",
        "deng-rejoin",
    ]
    return "\n".join(lines)


NO_PUBLIC_VERSIONS_MESSAGE = "No public versions are available yet."
