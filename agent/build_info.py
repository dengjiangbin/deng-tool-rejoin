"""Runtime build-proof helpers for DENG Tool: Rejoin.

The tool reads two on-disk files to prove which build is actually running on
the device:

* ``BUILD-INFO.json`` — embedded inside the published tarball by
  :func:`agent.internal_test_artifact.build_internal_test_tarball`.  Carries
  the git commit + build time + channel.  This file is shipped with the code
  and is always co-located with ``agent/`` after install.

* ``.installed-build.json`` — written by the installer bash script at install
  time, sitting at the install-root (one level above ``agent/``).  Carries
  the verified ``artifact_sha256``, ``install_time``, ``package_url`` and
  ``installer_url`` that the installer just downloaded.

``deng-rejoin version`` and ``deng-rejoin doctor install`` consume the merged
view from this module.  The functions here have no side effects and use only
the standard library so that even when modules are partially broken the
version / doctor commands still run.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

# ─── Paths ─────────────────────────────────────────────────────────────────────
# install root = directory containing this ``agent`` package
INSTALL_ROOT = Path(__file__).resolve().parent.parent
BUILD_INFO_PATH = INSTALL_ROOT / "BUILD-INFO.json"
INSTALLED_BUILD_PATH = INSTALL_ROOT / ".installed-build.json"
INSTALL_API_PATH = INSTALL_ROOT / ".install_api"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_build_info() -> dict[str, Any]:
    """Load the embedded ``BUILD-INFO.json``; empty dict if missing."""
    return _read_json(BUILD_INFO_PATH)


def load_installed_build() -> dict[str, Any]:
    """Load the installer-written ``.installed-build.json``; empty if missing."""
    return _read_json(INSTALLED_BUILD_PATH)


def _read_install_api() -> str:
    try:
        if not INSTALL_API_PATH.is_file():
            return ""
        return INSTALL_API_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def find_wrapper_path() -> str | None:
    """Best-effort: where the ``deng-rejoin`` shell wrapper lives on PATH."""
    return shutil.which("deng-rejoin")


# ─── Required modules / symbols that prove the new build is loaded ────────────

# Per task: doctor install must verify these modules exist and these symbols
# resolve.  Adding a key here is the only way to make a feature mandatory.
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
)

# (module, symbol) pairs that must resolve at import time.
REQUIRED_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("agent.roblox_presence", "fetch_presence"),
    ("agent.roblox_presence", "lookup_user_id"),
    ("agent.dumpsys_cache", "cached_run"),
    ("agent.freeform_enable", "setup_freeform_capabilities"),
    ("agent.playing_state", "StateTracker"),
    ("agent.android", "launch_package_with_bounds"),
)


def _module_file_path(modname: str) -> str | None:
    """Return the on-disk ``__file__`` for *modname* without importing it.

    We resolve through ``importlib.util.find_spec`` so a missing module simply
    returns ``None`` and never raises during ``doctor install``.
    """
    try:
        import importlib.util

        spec = importlib.util.find_spec(modname)
        if spec is None or not spec.origin:
            return None
        return spec.origin
    except Exception:  # noqa: BLE001 — best-effort probe.
        return None


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _short(sha: str, n: int = 12) -> str:
    sha = (sha or "").strip()
    return sha[:n] if sha else ""


def _wrapper_target_install_root() -> str | None:
    """Inspect the wrapper script and try to extract its install-root path.

    The wrapper we generate exports ``DENG_REJOIN_HOME=...`` and ultimately
    executes ``$DENG_REJOIN_HOME/agent/deng_tool_rejoin.py``.  Older wrappers
    set ``APP_HOME=...`` instead.  Return the path string if discoverable
    (with bash parameter expansion stripped to its default value), else
    ``None``.
    """
    wrapper = find_wrapper_path()
    if not wrapper:
        return None
    try:
        text = Path(wrapper).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        s = line.strip()
        # Examples we accept:
        #   export DENG_REJOIN_HOME="${DENG_REJOIN_HOME:-$HOME/.deng-tool/rejoin}"
        #   DENG_REJOIN_HOME="$HOME/.deng-tool/rejoin"
        #   APP_HOME="$HOME/.deng-tool/rejoin"
        for prefix in ("export DENG_REJOIN_HOME=", "DENG_REJOIN_HOME=", "APP_HOME="):
            if s.startswith(prefix):
                raw = s[len(prefix):].strip().strip('"').strip("'")
                # Strip ${VAR:-default} → default
                if raw.startswith("${") and ":-" in raw and raw.endswith("}"):
                    raw = raw[raw.index(":-") + 2 : -1]
                return raw
    return None


def collect_version_info() -> dict[str, Any]:
    """Return the merged dict consumed by ``deng-rejoin version``.

    Never raises; missing pieces produce empty strings.
    """
    from .constants import VERSION, PRODUCT_NAME

    bi = load_build_info()
    ib = load_installed_build()
    info: dict[str, Any] = {
        "product": PRODUCT_NAME,
        "product_version": str(VERSION),
        "channel": str(ib.get("channel") or bi.get("channel") or ""),
        "git_commit": str(ib.get("git_commit") or bi.get("git_commit") or ""),
        "git_commit_short": _short(
            str(ib.get("git_commit") or bi.get("git_commit") or "")
        ),
        "artifact_sha256": str(ib.get("artifact_sha256") or ""),
        "artifact_sha256_short": _short(str(ib.get("artifact_sha256") or "")),
        "built_at_iso": str(bi.get("built_at_iso") or ""),
        "install_time_iso": str(ib.get("install_time_iso") or ""),
        "install_api": str(ib.get("install_api") or _read_install_api() or ""),
        "package_url": str(ib.get("package_url") or ""),
        "installer_url": str(ib.get("installer_url") or ""),
        "install_root": str(INSTALL_ROOT),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "wrapper_path": find_wrapper_path() or "",
        "wrapper_target_root": _wrapper_target_install_root() or "",
        "build_info_path": str(BUILD_INFO_PATH) if BUILD_INFO_PATH.is_file() else "",
        "installed_build_path": str(INSTALLED_BUILD_PATH)
        if INSTALLED_BUILD_PATH.is_file()
        else "",
        "modules": {},
    }
    for mod in REQUIRED_MODULES:
        info["modules"][mod] = _module_file_path(mod) or ""
    return info


# ─── doctor install ────────────────────────────────────────────────────────────


def _orphan_pycache_dirs() -> list[str]:
    """Find ``__pycache__`` directories where the parent has no matching ``.py``.

    These are leftover compiled modules from a previous install and they can
    silently shadow source-not-present imports on some Pythons.  The installer
    purges them; doctor install reports them.
    """
    out: list[str] = []
    for root in (INSTALL_ROOT / "agent", INSTALL_ROOT / "bot", INSTALL_ROOT / "scripts"):
        if not root.is_dir():
            continue
        for pyc_dir in root.rglob("__pycache__"):
            try:
                for pyc in pyc_dir.iterdir():
                    if pyc.suffix != ".pyc":
                        continue
                    # __pycache__/foo.cpython-311.pyc → foo.py expected in parent
                    name = pyc.stem.split(".")[0] + ".py"
                    if not (pyc_dir.parent / name).is_file():
                        out.append(str(pyc))
            except OSError:
                continue
    return out


def doctor_install_checks() -> list[dict[str, Any]]:
    """Run the doctor-install probes and return one dict per check.

    Each dict has keys: ``name`` (str), ``ok`` (bool), ``detail`` (str).
    """
    out: list[dict[str, Any]] = []

    # 1) wrapper present
    wrapper = find_wrapper_path()
    out.append(
        {
            "name": "wrapper_present",
            "ok": bool(wrapper),
            "detail": wrapper or "deng-rejoin not on PATH",
        }
    )

    # 2) wrapper APP_HOME matches our install root
    wrapper_root = _wrapper_target_install_root()
    if wrapper_root:
        # Compare normalised paths; tolerate trailing slashes and Termux vars.
        expanded = os.path.expandvars(os.path.expanduser(wrapper_root))
        ok = Path(expanded).resolve() == INSTALL_ROOT
        out.append(
            {
                "name": "wrapper_targets_install_root",
                "ok": ok,
                "detail": f"wrapper APP_HOME={wrapper_root} install_root={INSTALL_ROOT}",
            }
        )

    # 3) BUILD-INFO.json present
    bi = load_build_info()
    out.append(
        {
            "name": "build_info_present",
            "ok": bool(bi),
            "detail": str(BUILD_INFO_PATH) if bi else "missing BUILD-INFO.json",
        }
    )

    # 4) .installed-build.json present
    ib = load_installed_build()
    out.append(
        {
            "name": "installed_build_metadata",
            "ok": bool(ib),
            "detail": str(INSTALLED_BUILD_PATH) if ib else "missing .installed-build.json",
        }
    )

    # 5) artifact SHA recorded by installer
    sha_present = bool(str(ib.get("artifact_sha256") or "").strip())
    out.append(
        {
            "name": "artifact_sha_recorded",
            "ok": sha_present,
            "detail": _short(str(ib.get("artifact_sha256") or "")) or "no SHA",
        }
    )

    # 6) Required modules importable (we use find_spec — no side effects).
    missing_modules: list[str] = []
    for mod in REQUIRED_MODULES:
        if not _module_file_path(mod):
            missing_modules.append(mod)
    out.append(
        {
            "name": "required_modules_present",
            "ok": not missing_modules,
            "detail": "all present" if not missing_modules else f"missing: {', '.join(missing_modules)}",
        }
    )

    # 7) Required symbols resolvable on import.
    missing_symbols: list[str] = []
    for mod, sym in REQUIRED_SYMBOLS:
        try:
            import importlib

            m = importlib.import_module(mod)
            if not hasattr(m, sym):
                missing_symbols.append(f"{mod}.{sym}")
        except Exception:  # noqa: BLE001
            missing_symbols.append(f"{mod}.{sym}")
    out.append(
        {
            "name": "required_symbols_resolvable",
            "ok": not missing_symbols,
            "detail": "all resolve"
            if not missing_symbols
            else f"missing: {', '.join(missing_symbols)}",
        }
    )

    # 8) No orphan __pycache__ shadowing missing sources.
    orphans = _orphan_pycache_dirs()
    out.append(
        {
            "name": "no_orphan_pycache",
            "ok": not orphans,
            "detail": "clean" if not orphans else f"{len(orphans)} orphan .pyc files",
        }
    )

    # 9) No stale "deferred installer" path from earlier builds.
    stale_candidates = [
        INSTALL_ROOT / "deferred_install.py",
        INSTALL_ROOT / "deferred_installer.py",
        INSTALL_ROOT / "agent" / "deferred_install.py",
        INSTALL_ROOT / "scripts" / "deferred_install.sh",
    ]
    stale = [str(p) for p in stale_candidates if p.is_file()]
    out.append(
        {
            "name": "no_stale_deferred_installer",
            "ok": not stale,
            "detail": "clean" if not stale else f"found: {', '.join(stale)}",
        }
    )

    # 10) Modules resolve under INSTALL_ROOT (not from some other PYTHONPATH).
    misrouted: list[str] = []
    for mod in REQUIRED_MODULES:
        p = _module_file_path(mod)
        if not p:
            continue
        try:
            Path(p).resolve().relative_to(INSTALL_ROOT)
        except ValueError:
            misrouted.append(f"{mod}={p}")
    out.append(
        {
            "name": "modules_under_install_root",
            "ok": not misrouted,
            "detail": "all routed correctly"
            if not misrouted
            else f"shadowed: {', '.join(misrouted)}",
        }
    )

    return out


def doctor_install_overall_ok(results: list[dict[str, Any]]) -> bool:
    """Return ``True`` only if every entry's ``ok`` is truthy."""
    return all(bool(r.get("ok")) for r in results)
