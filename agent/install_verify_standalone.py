#!/usr/bin/env python3
"""Install-time integrity probe — stdlib only, never imports ``agent``.

Termux / cloud-phone installs were SIGSEGV'ing when the installer ran::

    PYTHONPATH="$h" python3 -c 'import agent._protected_runtime ...'

because ``agent/__init__.py`` boots the protected-runtime import hook before the
check finishes.  This script verifies the signed manifest and inspects the
marshalled bundle **without** importing or executing any ``agent`` modules.
"""

from __future__ import annotations

import base64
import hashlib
import json
import marshal
import re
import sys
import types
import zlib
from pathlib import Path

_MIN_CLIENT_PROTOCOL = 2

REQUIRED_MODULES: tuple[str, ...] = (
    "agent.commands",
    "agent.supervisor",
    "agent.roblox_presence",
    "agent.window_apply",
    "agent.freeform_enable",
    "agent.playing_state",
    "agent.dumpsys_cache",
    "agent.android",
    "agent.launcher",
    "agent.termux_minimize",
)

REQUIRED_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("agent.roblox_presence", "fetch_presence"),
    ("agent.roblox_presence", "lookup_user_id"),
    ("agent.dumpsys_cache", "cached_run"),
    ("agent.freeform_enable", "setup_freeform_capabilities"),
    ("agent.playing_state", "StateTracker"),
    ("agent.android", "launch_package_with_bounds"),
    ("agent.termux_minimize", "minimize_termux_to_dock"),
)

_PERSISTENT_WORKER_NAMES = frozenset(
    {
        "_cmd_monitor_run_worker",
        "_spawn_monitor_worker",
        "_monitor_worker_running",
        "_monitor_status_from_disk",
        "_stop_monitor_worker",
        "_MONITOR_WORKER_ENV",
    }
)

_PERSISTENT_WORKER_STRING_MARKERS = ("run-worker", "monitor-run-worker")


def _install_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_public_key() -> tuple[int, int]:
    """Read RSA (n, e) literals embedded in on-disk ``_protected_runtime.py``."""
    runtime_py = Path(__file__).resolve().parent / "_protected_runtime.py"
    text = runtime_py.read_text(encoding="utf-8", errors="replace")
    match_n = re.search(r"^_N=(\d+)", text, re.MULTILINE)
    match_e = re.search(r"^_E=(\d+)", text, re.MULTILINE)
    if not match_n or not match_e:
        raise SystemExit(1)
    return int(match_n.group(1)), int(match_e.group(1))


def _b64u(value: str) -> bytes:
    padded = value.encode() + b"=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded)


def _canon_json(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _rsa_verify(n: int, e: int, signature: bytes, message: bytes) -> bool:
    key_len = (n.bit_length() + 7) // 8
    if len(signature) != key_len:
        return False
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(key_len, "big")
    digest_info = (
        bytes.fromhex("3031300d060960864801650304020105000420")
        + hashlib.sha256(message).digest()
    )
    expected = b"\x00\x01" + b"\xff" * (key_len - len(digest_info) - 3) + b"\x00" + digest_info
    return em == expected


def _verify_signed_manifest(root: Path) -> None:
    public_n, public_e = _load_public_key()
    manifest_path = root / "RELEASE-MANIFEST.json"
    sig_path = root / "RELEASE-MANIFEST.sig"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sig_doc = json.loads(sig_path.read_text(encoding="utf-8"))
    if manifest.get("project") != "deng-tool-rejoin":
        raise SystemExit(1)
    if int(manifest.get("client_protocol") or 0) < _MIN_CLIENT_PROTOCOL:
        raise SystemExit(1)
    if sig_doc.get("algorithm") != "RS256":
        raise SystemExit(1)
    if not _rsa_verify(
        public_n,
        public_e,
        _b64u(str(sig_doc.get("signature") or "")),
        _canon_json(manifest),
    ):
        raise SystemExit(1)
    for item in manifest.get("files", []):
        rel = str(item.get("path") or "")
        if not rel or rel.startswith("/") or ".." in rel.replace("\\", "/").split("/"):
            raise SystemExit(1)
        raw = (root / rel).read_bytes()
        if len(raw) != int(item.get("size", -1)):
            raise SystemExit(1)
        if hashlib.sha256(raw).hexdigest() != str(item.get("sha256") or ""):
            raise SystemExit(1)


def _iter_code_objects(root: types.CodeType):
    stack = [root]
    while stack:
        item = stack.pop()
        yield item
        for const in getattr(item, "co_consts", ()):
            if isinstance(const, types.CodeType):
                stack.append(const)


def _code_object_names(root: types.CodeType) -> set[str]:
    names: set[str] = set()
    for code in _iter_code_objects(root):
        co_name = getattr(code, "co_name", "")
        if co_name:
            names.add(str(co_name))
        for name in getattr(code, "co_names", ()):
            names.add(str(name))
    return names


def _code_object_strings(root: types.CodeType) -> set[str]:
    strings: set[str] = set()
    for code in _iter_code_objects(root):
        for const in getattr(code, "co_consts", ()):
            if isinstance(const, str):
                strings.add(const)
    return strings


def _verify_protected_bundle(root: Path) -> None:
    bundle_path = root / "agent" / ".deng_runtime.bin"
    if not bundle_path.is_file():
        raise SystemExit(1)
    try:
        modules = marshal.loads(zlib.decompress(bundle_path.read_bytes()))
    except Exception:
        raise SystemExit(1) from None
    if not isinstance(modules, dict):
        raise SystemExit(1)

    missing_modules = [mod for mod in REQUIRED_MODULES if mod not in modules]
    if missing_modules:
        raise SystemExit(1)

    for mod, sym in REQUIRED_SYMBOLS:
        code = modules.get(mod)
        if code is None or sym not in _code_object_names(code):
            raise SystemExit(1)

    commands_code = modules.get("agent.commands")
    if commands_code is None:
        raise SystemExit(1)
    names = _code_object_names(commands_code)
    strings = _code_object_strings(commands_code)
    if _PERSISTENT_WORKER_NAMES - names:
        raise SystemExit(1)
    for marker in _PERSISTENT_WORKER_STRING_MARKERS:
        if not any(marker in item for item in strings):
            raise SystemExit(1)


def main() -> int:
    root = _install_root()
    for required in (
        root / "BUILD-INFO.json",
        root / "RELEASE-MANIFEST.json",
        root / "RELEASE-MANIFEST.sig",
        root / "agent" / ".deng_runtime.bin",
        root / "agent" / "deng_tool_rejoin.py",
        root / "agent" / "_protected_runtime.py",
    ):
        if not required.is_file():
            return 1
    try:
        _verify_signed_manifest(root)
        _verify_protected_bundle(root)
    except SystemExit:
        raise
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
