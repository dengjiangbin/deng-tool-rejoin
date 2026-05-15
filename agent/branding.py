"""Discord embed branding: hosted logo URL for thumbnails only.

Discord embeds cannot use local filesystem paths as thumbnail URLs. Set
``DENG_BRANDING_LOGO_URL`` to a public HTTPS image URL (e.g. CDN-hosted
``D_96px.png``). When unset, embeds render without a thumbnail.
"""

from __future__ import annotations

import os
from typing import Any


def get_branding_logo_url() -> str:
    """Return a non-empty URL only when a hosted logo is configured."""
    return (os.environ.get("DENG_BRANDING_LOGO_URL") or "").strip()


def apply_branding_to_embed_dict(embed_dict: dict[str, Any]) -> None:
    """Mutate *embed_dict* in place: set ``thumbnail.url`` when configured."""
    url = get_branding_logo_url()
    if not url:
        return
    embed_dict["thumbnail"] = {"url": url}


def apply_branding_to_discord_embed(embed: Any) -> Any:
    """Apply thumbnail to a discord.py ``Embed`` when a logo URL is set."""
    url = get_branding_logo_url()
    if url and hasattr(embed, "set_thumbnail"):
        embed.set_thumbnail(url=url)
    return embed


def apply_branding(embed_dict: dict[str, Any]) -> None:
    """Alias for :func:`apply_branding_to_embed_dict` (mutates embed JSON dict)."""
    apply_branding_to_embed_dict(embed_dict)
