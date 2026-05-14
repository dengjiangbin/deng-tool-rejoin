"""DENG Tool: Rejoin — Development key generator.

Usage (run from project root):
    python -m agent.keygen              # generate one key
    python -m agent.keygen list         # list all keys in local DB
    python -m agent.keygen revoke KEY   # revoke a key
    python -m agent.keygen reset KEY    # unbind key from device

Only works when DENG_DEV=1 is set or the keydb.json is present.
"""

from __future__ import annotations

import sys

from .keystore import (
    create_key_in_db,
    generate_key,
    list_keys_in_db,
    revoke_key_in_db,
    unbind_key,
    KEYDB_PATH,
    KeyError,  # noqa: A004
)


def _print_header() -> None:
    print()
    print("━" * 56)
    print("  DENG Tool: Rejoin — Dev Key Generator")
    print(f"  DB: {KEYDB_PATH}")
    print("━" * 56)


def cmd_generate(note: str = "") -> None:
    key = create_key_in_db(note=note)
    print(f"  ✓ Generated key: {key}")
    print(f"    Note: {note or '(none)'}")
    print()


def cmd_list() -> None:
    rows = list_keys_in_db()
    if not rows:
        print("  No keys in database.")
        return
    header = f"  {'Key':<42} {'Device':<12} {'Valid':<6} Note"
    print(header)
    print("  " + "─" * 70)
    for row in rows:
        key_display = row["key"][:40] + ".." if len(row["key"]) > 40 else row["key"]
        uuid_display = str(row["device_uuid"])[:10]
        valid = "Yes" if row["valid"] else "REVOKED"
        note = row["note"][:20]
        print(f"  {key_display:<42} {uuid_display:<12} {valid:<6} {note}")


def cmd_revoke(key: str) -> None:
    try:
        revoke_key_in_db(key)
        print(f"  ✓ Revoked: {key}")
    except KeyError as exc:
        print(f"  ✗ {exc}")


def cmd_reset(key: str) -> None:
    try:
        unbind_key(key)
        print(f"  ✓ Device binding removed for: {key}")
    except KeyError as exc:
        print(f"  ✗ {exc}")


def main() -> int:
    _print_header()
    argv = sys.argv[1:]
    sub = argv[0].lower() if argv else "generate"

    if sub in ("generate", "gen", "new"):
        note = " ".join(argv[1:])
        cmd_generate(note=note)
    elif sub in ("list", "ls"):
        cmd_list()
    elif sub in ("revoke",) and len(argv) >= 2:
        cmd_revoke(argv[1])
    elif sub in ("reset", "unbind") and len(argv) >= 2:
        cmd_reset(argv[1])
    else:
        print(f"  Unknown sub-command: {sub!r}")
        print("  Usage: python -m agent.keygen [generate|list|revoke KEY|reset KEY]")
        return 2
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
