#!/usr/bin/env python3
"""Mint a one-time Delta bypass token (admin)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.delta_bypass_store import mint_bypass_token  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="Mint Delta bypass token for /bypass?token=")
    p.add_argument("key", help="Delta executor license key to embed in the token")
    p.add_argument("--hours", type=float, default=24.0, help="Token TTL (default 24h)")
    p.add_argument("--reuse", action="store_true", help="Allow token reuse (test only)")
    p.add_argument(
        "--base-url",
        default="https://rejoin.deng.my.id",
        help="Public base URL shown in the bypass link",
    )
    args = p.parse_args()
    expires_at = time.time() + max(0.5, float(args.hours)) * 3600.0
    token = mint_bypass_token(args.key, expires_at=expires_at, reuse=bool(args.reuse))
    base = str(args.base_url).rstrip("/")
    link = f"{base}/bypass?token={token}"
    print(json.dumps({"token": token, "link": link, "expires_at": expires_at}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
