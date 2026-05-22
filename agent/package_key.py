"""Package key helper for DENG Tool: Rejoin.

This module handles PER-PACKAGE keys that are written to each Roblox/package
internal license file:

    /storage/emulated/0/Android/data/{package}/files/gloop/external/Internals/Cache/license

Note (probe p-52aeb6420f): the license file lives in the ``Cache`` sub-
directory of ``Internals``, NOT directly under ``Internals``.  Earlier
builds wrote ``…/Internals/license`` which the Roblox clone runtime did
not pick up — the cloner reads ``…/Internals/Cache/license`` only.

IMPORTANT: This is completely separate from the DENG Tool license system.
- Does NOT use the DENG Tool license server.
- Does NOT touch the DENG Tool license file.
- Does NOT call license.py validation functions.
- Does NOT touch Supabase / Discord license panel / license_keys.
- Does NOT touch DENG paid license system in any way.

Package keys are FREE_ prefixed keys written directly to each Roblox/package
Android data folder's internal license file.

The file name is exactly ``license`` (no extension).  Content type is
``application/octet-stream``.  The file is written atomically with the
``FREE_*`` key string (no JSON, no headers, no trailing newline).
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
import stat
import tempfile
import logging
from typing import Any

from .constants import PACKAGE_NAME_REGEX

_log = logging.getLogger("deng.rejoin.package_key")

# ── Constants ─────────────────────────────────────────────────────────────────

_PACKAGE_KEY_FREE_PREFIX = "FREE_"
# IMPORTANT: ``Internals/Cache/license`` — the ``Cache`` segment is required
# (probe p-52aeb6420f).  ``Internals`` and ``Cache`` are both case-sensitive
# (matches the on-device folder names exactly).
_PACKAGE_KEY_LICENSE_SUBPATH = "files/gloop/external/Internals/Cache/license"
_PACKAGE_KEY_LICENSE_DIR = "files/gloop/external/Internals/Cache"
_PACKAGE_KEY_INTERNALS_DIR = "files/gloop/external/Internals"  # parent of Cache
_ANDROID_DATA_BASE = "/storage/emulated/0/Android/data"
# Content type the cloner expects for the license blob.  Surfaced via
# :func:`package_key_license_mime_type` so the Menu 4 file-info card can
# display it without hard-coding the string in the UI layer.
_PACKAGE_KEY_LICENSE_MIME = "application/octet-stream"

# ── Validation ────────────────────────────────────────────────────────────────


def _validate_package_name(package: str) -> str:
    """Validate and return a clean Android package name. Raises ValueError on bad input."""
    cleaned = (package or "").strip()
    if not cleaned:
        raise ValueError("package name cannot be empty")
    if not re.fullmatch(PACKAGE_NAME_REGEX, cleaned):
        raise ValueError(f"invalid Android package name: {cleaned!r}")
    # Extra guard: no path separators allowed (prevent traversal).
    if "/" in cleaned or "\\" in cleaned or ".." in cleaned:
        raise ValueError(f"package name contains path separators: {cleaned!r}")
    return cleaned


# ── Public helpers ────────────────────────────────────────────────────────────


def mask_package_key(key: str) -> str:
    """Return a display-safe masked version of a package key.

    Examples:
        FREE_ABCDEFGHIJ1234  →  FREE_...1234
        FREE_XY              →  FREE_****
        (empty)              →  (empty)

    The full key is NEVER returned here. Only the masked form.
    """
    key = (key or "").strip()
    if not key:
        return ""
    if not key.startswith(_PACKAGE_KEY_FREE_PREFIX):
        # Unknown prefix — show first 4 chars + asterisks
        return key[:4] + "****"
    suffix = key[len(_PACKAGE_KEY_FREE_PREFIX):]
    if len(suffix) <= 4:
        return _PACKAGE_KEY_FREE_PREFIX + "****"
    return _PACKAGE_KEY_FREE_PREFIX + "..." + suffix[-4:]


def package_key_license_path(package: str) -> str:
    """Return the absolute path to the internal license file for a package.

    Formula::

        /storage/emulated/0/Android/data/{package}/files/gloop/external/Internals/Cache/license

    Both ``Internals`` and ``Cache`` are case-sensitive — do NOT lowercase.
    """
    pkg = _validate_package_name(package)
    return f"{_ANDROID_DATA_BASE}/{pkg}/{_PACKAGE_KEY_LICENSE_SUBPATH}"


def package_key_license_dir(package: str) -> str:
    """Return the parent directory of the internal license file for a package.

    Formula::

        /storage/emulated/0/Android/data/{package}/files/gloop/external/Internals/Cache
    """
    pkg = _validate_package_name(package)
    return f"{_ANDROID_DATA_BASE}/{pkg}/{_PACKAGE_KEY_LICENSE_DIR}"


def package_key_internals_dir(package: str) -> str:
    """Return the ``Internals`` directory (parent of ``Cache``) for a package."""
    pkg = _validate_package_name(package)
    return f"{_ANDROID_DATA_BASE}/{pkg}/{_PACKAGE_KEY_INTERNALS_DIR}"


def package_key_license_mime_type() -> str:
    """Return the MIME type used to render the license blob (``application/octet-stream``).

    The package key file is a small opaque ASCII payload (``FREE_<id>``).
    The cloner reads it as octet-stream; we expose the constant here so the
    Menu 4 file-info display can show ``Type: application/octet-stream``
    without baking the string into the UI layer.
    """
    return _PACKAGE_KEY_LICENSE_MIME


def is_valid_package_key(key: str) -> bool:
    """Return True if the key starts with FREE_ (case-sensitive)."""
    cleaned = (key or "").strip()
    return cleaned.startswith(_PACKAGE_KEY_FREE_PREFIX)


def resolve_package_key(config: dict[str, Any], package: str) -> str | None:
    """Resolve the package key for a given package.

    Priority:
    1. Per-package key (overrides global).
    2. Global/all-package key.
    3. None (no key configured).

    Returns the key string (stripped) or None.
    """
    try:
        pkg = _validate_package_name(package)
    except ValueError:
        return None
    pkg_keys = config.get("package_keys") or {}
    per_pkg = pkg_keys.get("per_package") if isinstance(pkg_keys, dict) else {}
    if not isinstance(per_pkg, dict):
        per_pkg = {}
    key = (per_pkg.get(pkg) or "").strip()
    if not key:
        key = (pkg_keys.get("global") or "").strip() if isinstance(pkg_keys, dict) else ""
    return key if key else None


# ── File writing ──────────────────────────────────────────────────────────────


def _write_via_python(path: str, key: str) -> tuple[bool, str]:
    """Attempt to write the key using Python file I/O. Returns (success, error).

    Creates the parent ``…/Internals/Cache`` directory if missing, then
    writes atomically via a temp file in the same directory followed by
    ``os.replace`` so a partial write never leaves an empty/half file in
    place.
    """
    try:
        parent = os.path.dirname(path)
        os.makedirs(parent, exist_ok=True)
        # Write atomically via a temp file in the same directory so a crash
        # mid-write leaves the previous contents intact.
        tmp_fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".deng-pkg-key-")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                fh.write(key)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    # fsync may not work on FUSE-mounted external storage;
                    # the atomic rename below is the actual safety net.
                    pass
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        try:
            # 0o644 — owner rw, group/other r.  Matches what the cloner
            # writes when it generates the file natively.
            os.chmod(path, 0o644)
        except OSError:
            pass
        return True, ""
    except OSError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _write_via_root(path: str, key: str, root_tool: str) -> tuple[bool, str]:
    """Write the key via root/su. Returns (success, error)."""
    from . import android as _android  # local import to avoid circular
    parent = os.path.dirname(path)

    # 1. Ensure parent directory exists via root mkdir -p
    mkdir_res = _android.run_root_command(
        ["sh", "-c", f"mkdir -p {shlex.quote(parent)}"],
        root_tool=root_tool,
        timeout=10,
    )
    if not mkdir_res.ok:
        return False, f"mkdir -p failed: {(mkdir_res.stderr or '')[:80]}"

    # 2. Write via sh -c echo/printf to avoid shell injection.
    # Use printf %s to avoid issues with newlines/backslashes in key.
    # Key is passed as a quoted argument to printf.
    quoted_key = shlex.quote(key)
    quoted_path = shlex.quote(path)
    write_cmd = f"printf '%s' {quoted_key} > {quoted_path}"
    write_res = _android.run_root_command(
        ["sh", "-c", write_cmd],
        root_tool=root_tool,
        timeout=10,
    )
    if not write_res.ok:
        return False, f"write failed (rc={write_res.returncode}): {(write_res.stderr or '')[:80]}"

    # 3. Verify the file content matches.
    verify_res = _android.run_root_command(
        ["cat", path],
        root_tool=root_tool,
        timeout=8,
    )
    if not verify_res.ok:
        return False, "write succeeded but verify read failed"
    actual = (verify_res.stdout or "").rstrip("\n")
    if actual != key:
        return False, f"verify mismatch (expected len={len(key)}, got len={len(actual)})"
    return True, ""


def write_package_key_file(
    package: str,
    key: str,
    *,
    root_enabled: bool = True,
) -> dict[str, Any]:
    """Write the package key to the internal license file.

    Does not clear package data.
    Does NOT delete any other files.
    Does NOT touch shared_prefs, databases, cookies, tokens, or login data.
    Only replaces the exact license file content.

    Returns a result dict:
        success      — bool
        method       — "python" | "root_su" | "skipped"
        path         — absolute path written
        key_masked   — masked key (never the full key)
        write_needed — bool (True if file was actually written)
        error        — error message (empty on success)
    """
    result: dict[str, Any] = {
        "success": False,
        "method": "skipped",
        "path": "",
        "key_masked": "",
        "write_needed": False,
        "error": "",
    }
    try:
        pkg = _validate_package_name(package)
    except ValueError as exc:
        result["error"] = str(exc)
        return result

    key = (key or "").strip()
    if not key:
        result["error"] = "package key is empty"
        return result
    if not is_valid_package_key(key):
        result["error"] = "package key must start with FREE_"
        return result

    path = package_key_license_path(pkg)
    result["path"] = path
    result["key_masked"] = mask_package_key(key)

    _log.info(
        "[DENG_REJOIN_PACKAGE_KEY] package=%s mode=write path=%s key_masked=%s",
        pkg, path, result["key_masked"],
    )

    # ── Strategy 1: Python file write ────────────────────────────────────────
    ok, err = _write_via_python(path, key)
    if ok:
        result.update({"success": True, "method": "python", "write_needed": True})
        return result
    python_err = err

    # ── Strategy 2: Root/su write ─────────────────────────────────────────────
    if root_enabled:
        from . import android as _android
        root_info = _android.detect_root()
        if root_info.available and root_info.tool:
            ok2, err2 = _write_via_root(path, key, root_info.tool)
            if ok2:
                result.update({"success": True, "method": "root_su", "write_needed": True})
                return result
            result["error"] = f"python: {python_err}; root: {err2}"
            return result
        result["error"] = f"python: {python_err}; root: unavailable"
    else:
        result["error"] = f"python: {python_err}"

    return result


def _read_license_file(path: str, root_tool: str | None) -> str | None:
    """Read the license file content. Returns content or None on failure."""
    # Try Python first.
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        pass
    # Try root if available.
    if root_tool:
        from . import android as _android
        res = _android.run_root_command(["cat", path], root_tool=root_tool, timeout=6)
        if res.ok:
            return (res.stdout or "").strip()
    return None


# ── Public Menu 4 file-info ───────────────────────────────────────────────────


def package_key_license_info(
    package: str,
    *,
    root_enabled: bool = True,
) -> dict[str, Any]:
    """Return a small file-info dict for the package's license file.

    Used by the Menu 4 "Key" UI to show whether the file exists and to
    display human-readable metadata (size, permissions, MD5).  Never raises.

    Returned keys:
        package        — validated package name
        path           — absolute expected path
        dir            — parent dir (``…/Internals/Cache``)
        file_name      — always ``"license"``
        mime_type      — ``application/octet-stream``
        exists         — bool
        size_bytes     — int or None
        modified_iso   — UTC ISO timestamp or ""
        permissions    — ``"rw-r--r--"``-style string, or "" when stat failed
        md5            — hex digest of the file content, or "" on read failure
        key_masked     — masked content (``FREE_...XXXX``) — NEVER the full key
        read_method    — ``"python"`` | ``"root"`` | ``"unavailable"``
        error          — error message (empty on success)

    The full key is NEVER returned by this helper.  Callers that need the
    raw key for comparison should use :func:`_read_license_file` directly
    (it stays module-private).
    """
    info: dict[str, Any] = {
        "package":      "",
        "path":         "",
        "dir":          "",
        "file_name":    "license",
        "mime_type":    _PACKAGE_KEY_LICENSE_MIME,
        "exists":       False,
        "size_bytes":   None,
        "modified_iso": "",
        "permissions":  "",
        "md5":          "",
        "key_masked":   "",
        "read_method":  "unavailable",
        "error":        "",
    }
    try:
        pkg = _validate_package_name(package)
    except ValueError as exc:
        info["error"] = str(exc)
        return info
    info["package"] = pkg
    info["path"]    = package_key_license_path(pkg)
    info["dir"]     = package_key_license_dir(pkg)

    # ── stat (python first, root fallback) ─────────────────────────────────
    try:
        st = os.stat(info["path"])
        info["exists"]      = True
        info["size_bytes"]  = int(st.st_size)
        info["permissions"] = stat.filemode(st.st_mode)[1:]  # drop the leading file-type char
        try:
            from datetime import datetime, timezone
            info["modified_iso"] = (
                datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ")
            )
        except Exception:  # noqa: BLE001
            info["modified_iso"] = ""
    except FileNotFoundError:
        # Try via root before giving up — external storage often refuses
        # unprivileged stat under scoped storage.
        if root_enabled:
            try:
                from . import android as _android
                ri = _android.detect_root()
                if ri.available and ri.tool:
                    res = _android.run_root_command(
                        ["sh", "-c", f"ls -l {shlex.quote(info['path'])} 2>/dev/null"],
                        root_tool=ri.tool, timeout=6,
                    )
                    if res.ok and (res.stdout or "").strip():
                        info["exists"] = True
                        info["read_method"] = "root"
            except Exception:  # noqa: BLE001
                pass
    except OSError as exc:
        info["error"] = str(exc)

    # ── read content for MD5 + masked key ─────────────────────────────────
    root_tool = None
    if root_enabled:
        try:
            from . import android as _android
            ri = _android.detect_root()
            root_tool = ri.tool if ri.available else None
        except Exception:  # noqa: BLE001
            root_tool = None

    content = _read_license_file(info["path"], root_tool)
    if content is not None:
        info["exists"]     = True
        info["md5"]        = hashlib.md5(content.encode("utf-8")).hexdigest()
        info["key_masked"] = mask_package_key(content)
        info["read_method"] = (
            "root" if info["read_method"] == "root" else "python"
        )
    return info


def ensure_package_key_for_start(
    package: str,
    config: dict[str, Any],
    root_enabled: bool = True,
) -> dict[str, Any]:
    """Before launch, ensure the package key file is correct if a key is configured.

    Only rewrites the file when:
    - File does not exist, OR
    - File content differs from the resolved key.

    Does NOT rewrite every call if the file already contains the correct key.
    Does NOT call DENG Tool license server.
    Does NOT touch DENG Tool license file.

    Returns a result dict:
        success      — bool
        method       — "python" | "root_su" | "skipped" | "already_correct"
        path         — license file path
        key_masked   — masked key
        key_prefix   — "FREE_" or ""
        write_needed — bool
        write_attempted — bool
        error        — error string
    """
    result: dict[str, Any] = {
        "success": True,
        "method": "skipped",
        "path": "",
        "key_masked": "",
        "key_prefix": "",
        "write_needed": False,
        "write_attempted": False,
        "error": "",
    }

    key = resolve_package_key(config, package)
    if not key:
        # No package key configured — continue with no change.
        _log.debug(
            "[DENG_REJOIN_PACKAGE_KEY] package=%s mode=start_ensure "
            "write_needed=false reason=no_key_configured",
            package,
        )
        return result

    result["key_masked"] = mask_package_key(key)
    result["key_prefix"] = key[:5] if len(key) >= 5 else key

    if not is_valid_package_key(key):
        result["success"] = False
        result["error"] = "package key must start with FREE_"
        return result

    try:
        pkg = _validate_package_name(package)
    except ValueError as exc:
        result["success"] = False
        result["error"] = str(exc)
        return result

    path = package_key_license_path(pkg)
    result["path"] = path

    # Check if rewrite is needed.
    root_tool = None
    if root_enabled:
        try:
            from . import android as _android
            ri = _android.detect_root()
            root_tool = ri.tool if ri.available else None
        except Exception:  # noqa: BLE001
            root_tool = None

    existing = _read_license_file(path, root_tool)
    if existing is not None and existing == key:
        # File already has the correct key — no rewrite needed.
        result.update({
            "success": True,
            "method": "already_correct",
            "write_needed": False,
            "write_attempted": False,
        })
        _log.debug(
            "[DENG_REJOIN_PACKAGE_KEY] package=%s mode=start_ensure "
            "write_needed=false method=already_correct key_masked=%s",
            pkg, result["key_masked"],
        )
        return result

    result["write_needed"] = True
    result["write_attempted"] = True

    write_result = write_package_key_file(package, key, root_enabled=root_enabled)
    result["success"] = write_result["success"]
    result["method"] = write_result["method"]
    result["error"] = write_result.get("error", "")

    _log.info(
        "[DENG_REJOIN_PACKAGE_KEY] package=%s mode=start_ensure path=%s "
        "key_prefix=FREE_ key_masked=%s write_needed=true write_attempted=true "
        "method=%s success=%s error=%s",
        pkg, path, result["key_masked"], result["method"],
        str(result["success"]).lower(), result["error"] or "",
    )
    return result
