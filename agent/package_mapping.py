"""Manual package → username mapping stored in config."""

from __future__ import annotations

from typing import Any

from .config import (
    load_config,
    save_config,
    validate_account_username,
    validate_package_name,
    validate_username_source,
)


def _entries(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    raw = cfg.get("roblox_packages") or []
    return [e for e in raw if isinstance(e, dict)]


def map_package_username(package: str, username: str) -> dict[str, Any]:
    """Persist manual mapping for *package*; returns updated config."""
    pkg = validate_package_name(package)
    name = validate_account_username(username)
    if not name:
        raise ValueError("username is required")
    cfg = load_config()
    cache = dict(cfg.get("package_username_cache") or {})
    cache[pkg] = name
    cfg["package_username_cache"] = cache
    entries = _entries(cfg)
    found = False
    new_entries: list[Any] = []
    for entry in entries:
        if str(entry.get("package") or "") != pkg:
            new_entries.append(entry)
            continue
        found = True
        updated = dict(entry)
        updated["account_username"] = name
        updated["username_source"] = validate_username_source("manual", name)
        new_entries.append(updated)
    if not found:
        from .config import package_entry

        new_entries.append(package_entry(pkg, name, True, "manual"))
    cfg["roblox_packages"] = new_entries
    if not str(cfg.get("roblox_package") or "").strip():
        cfg["roblox_package"] = pkg
    return save_config(cfg)


def unmap_package(package: str) -> dict[str, Any]:
    pkg = validate_package_name(package)
    cfg = load_config()
    cache = dict(cfg.get("package_username_cache") or {})
    cache.pop(pkg, None)
    cfg["package_username_cache"] = cache
    new_entries: list[Any] = []
    for entry in _entries(cfg):
        if str(entry.get("package") or "") != pkg:
            new_entries.append(entry)
            continue
        updated = dict(entry)
        updated["account_username"] = ""
        updated["username_source"] = "not_set"
        new_entries.append(updated)
    cfg["roblox_packages"] = new_entries
    return save_config(cfg)


def list_mapped_packages(cfg: dict[str, Any] | None = None) -> list[dict[str, str]]:
    data = cfg or load_config()
    out: list[dict[str, str]] = []
    cache = data.get("package_username_cache") if isinstance(data.get("package_username_cache"), dict) else {}
    for entry in _entries(data):
        pkg = str(entry.get("package") or "")
        if not pkg:
            continue
        source = str(entry.get("username_source") or "not_set")
        name = str(entry.get("account_username") or "").strip()
        if not name and isinstance(cache, dict):
            name = str(cache.get(pkg) or "").strip()
        out.append({"package": pkg, "username": name or "Unknown", "source": source})
    return out
