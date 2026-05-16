"""Enable Android freeform / resizable-activity capabilities (root only).

Why this exists
───────────────
On many cloud-phone builds, freeform multi-window is DISABLED at the system
level (``enable_freeform_support=0``).  When freeform is disabled, no amount
of App Cloner XML or per-task resize calls will actually move/resize a
Roblox clone — the system refuses to honor a non-fullscreen windowing mode
for "non-resizable" activities like Roblox.

Cheap market "resize window" tools work because the FIRST thing they do is
flip the four global settings below.  Once those are set to 1, the system
allows freeform windows and treats every activity as resizable, so launch
bounds and ``cmd activity resize-task`` actually take effect.

What this module does
─────────────────────
Probes ``settings get global <key>`` for each capability flag, then (if root
is available) writes ``1`` for any that are 0/unset.  Always logs the
result to the layout-discovery log; never prints publicly.

Idempotent and safe-to-call-many-times.  Never raises.

We deliberately do NOT touch ``adb_enabled``, ``usb_debugging``, any
SELinux mode, any cookie/token/credential store, or any user-data path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from . import android

_log = logging.getLogger("deng.rejoin.freeform_enable")

# Global settings that unlock freeform window mode and force-resizable apps.
# Source: AOSP services/core/java/com/android/server/wm/ActivityTaskManagerService
# (force_resizable_activities, enable_freeform_support) and frameworks/base
# (freeform_window_management).  development_settings_enabled is the master
# gate for the other three on many OEM builds.
_FREEFORM_GLOBAL_KEYS: tuple[str, ...] = (
    "enable_freeform_support",
    "force_resizable_activities",
    "freeform_window_management",
    "development_settings_enabled",
)

# These are "secure" namespace mirrors used by some OEM forks (Samsung One UI,
# MIUI, ColorOS, OxygenOS).  Probing both namespaces covers more devices.
_FREEFORM_SECURE_KEYS: tuple[str, ...] = (
    "enable_freeform_support",
    "force_resizable_activities",
    "freeform_window_management",
)


@dataclass
class _ProbeResult:
    key: str
    namespace: str           # "global" | "secure" | "system"
    before: str | None       # value before write (or None if unreadable)
    after: str | None        # value after write (or None if write failed)
    wrote: bool = False
    error: str = ""


@dataclass
class FreeformSetupResult:
    """Outcome of enabling freeform/resizable capabilities."""

    root_available: bool = False
    root_tool: str | None = None
    probes: list[_ProbeResult] = field(default_factory=list)
    enabled_keys: list[str] = field(default_factory=list)
    already_enabled_keys: list[str] = field(default_factory=list)
    failed_keys: list[str] = field(default_factory=list)

    @property
    def any_change(self) -> bool:
        return bool(self.enabled_keys)

    @property
    def healthy(self) -> bool:
        """True when every required flag is currently 1."""
        ok_keys = set(self.enabled_keys) | set(self.already_enabled_keys)
        return all(k in ok_keys for k in _FREEFORM_GLOBAL_KEYS)


def _settings_get(namespace: str, key: str) -> str | None:
    """``settings get <ns> <key>`` — returns stripped value or None.  Never raises."""
    try:
        res = android.run_command(["settings", "get", namespace, key], timeout=4)
        if not res.ok:
            return None
        v = (res.stdout or "").strip()
        # "null" is the literal Android returns for unset keys
        if v == "" or v.lower() == "null":
            return None
        return v
    except Exception:  # noqa: BLE001
        return None


def _settings_put_root(
    namespace: str, key: str, value: str, *, root_tool: str
) -> bool:
    """``su -c "settings put <ns> <key> <value>"`` — returns True on success."""
    try:
        # Use a single-string command via sh -c so the SELinux domain matches
        # what App Ops / settings expects when called from root context.
        res = android.run_root_command(
            ["sh", "-c", f"settings put {namespace} {key} {value}"],
            root_tool=root_tool,
            timeout=5,
        )
        return res.ok
    except Exception:  # noqa: BLE001
        return False


def _is_truthy(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def setup_freeform_capabilities(
    *,
    desired_value: str = "1",
    keys_global: Iterable[str] = _FREEFORM_GLOBAL_KEYS,
    keys_secure: Iterable[str] = _FREEFORM_SECURE_KEYS,
) -> FreeformSetupResult:
    """Probe and (with root) enable freeform-window capabilities.

    Never raises.  Always returns a populated :class:`FreeformSetupResult`.

    Pipeline:
      1. Detect root.
      2. For every (namespace, key) pair, ``settings get`` the current value.
      3. If the value is not truthy AND root is available, write
         ``desired_value`` and read back to verify.
      4. Record the result on the FreeformSetupResult.
    """
    out = FreeformSetupResult()
    try:
        root = android.detect_root()
    except Exception:  # noqa: BLE001
        root = None  # type: ignore[assignment]

    if root is not None and root.available and root.tool:
        out.root_available = True
        out.root_tool = root.tool

    def _try(ns: str, key: str) -> None:
        before = _settings_get(ns, key)
        probe = _ProbeResult(key=key, namespace=ns, before=before, after=before)
        if _is_truthy(before):
            out.already_enabled_keys.append(key)
            out.probes.append(probe)
            return
        if not out.root_available or not out.root_tool:
            # Without root we can probe but cannot write.
            probe.error = "root unavailable"
            out.failed_keys.append(key)
            out.probes.append(probe)
            return
        wrote = _settings_put_root(ns, key, desired_value, root_tool=out.root_tool)
        if not wrote:
            probe.error = "settings put failed"
            out.failed_keys.append(key)
            out.probes.append(probe)
            return
        after = _settings_get(ns, key)
        probe.after = after
        probe.wrote = True
        if _is_truthy(after):
            out.enabled_keys.append(key)
        else:
            probe.error = "post-write value still not truthy"
            out.failed_keys.append(key)
        out.probes.append(probe)

    for k in keys_global:
        _try("global", k)
    for k in keys_secure:
        _try("secure", k)

    _log.debug(
        "freeform_setup: root=%s enabled=%s already=%s failed=%s",
        out.root_available, out.enabled_keys, out.already_enabled_keys, out.failed_keys,
    )
    return out


def setup_freeform_capabilities_silent() -> tuple[int, int]:
    """Silent wrapper: returns ``(enabled+already, total_global)``.  Never raises."""
    try:
        r = setup_freeform_capabilities()
    except Exception as exc:  # noqa: BLE001
        _log.debug("setup_freeform_capabilities_silent error: %s", exc)
        return 0, len(_FREEFORM_GLOBAL_KEYS)
    ok = len(r.enabled_keys) + len(
        [k for k in r.already_enabled_keys if k in _FREEFORM_GLOBAL_KEYS]
    )
    return ok, len(_FREEFORM_GLOBAL_KEYS)


def render_freeform_log_block(result: FreeformSetupResult) -> list[str]:
    """Render lines for the discovery log (no public output)."""
    lines: list[str] = [
        "== Freeform / resizable-activity capabilities ==",
        f"  root_available: {result.root_available} (tool={result.root_tool})",
        f"  enabled_keys: {result.enabled_keys}",
        f"  already_enabled_keys: {result.already_enabled_keys}",
        f"  failed_keys: {result.failed_keys}",
    ]
    for p in result.probes:
        lines.append(
            f"  - [{p.namespace}] {p.key}: before={p.before!r} after={p.after!r} "
            f"wrote={p.wrote} error={p.error!r}"
        )
    return lines
