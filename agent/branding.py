"""Discord embed branding: hosted logo URL for thumbnails only.

Discord embeds cannot use local filesystem paths as thumbnail URLs.

Priority for the default logo URL:

1. ``DENG_BRANDING_LOGO_URL`` (full HTTPS URL).
2. ``LICENSE_API_PUBLIC_URL`` + ``/assets/denghub_logo.png`` when the public
   base URL is set (served by :mod:`bot.license_api`).
3. Otherwise no thumbnail (localhost-only API URLs are not used — Discord
   cannot fetch them).
"""

from __future__ import annotations

import os
from typing import Any

_PUBLIC_LOGO_PATH = "/assets/denghub_logo.png"


def get_branding_logo_url() -> str:
    """Return a non-empty URL when a hosted logo can be resolved."""
    explicit = (os.environ.get("DENG_BRANDING_LOGO_URL") or "").strip()
    if explicit:
        return explicit
    pub = (os.environ.get("LICENSE_API_PUBLIC_URL") or "").strip().rstrip("/")
    if pub:
        low = pub.lower()
        if low.startswith("http://localhost") or low.startswith("http://127."):
            return ""
        return f"{pub}{_PUBLIC_LOGO_PATH}"
    return ""


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
