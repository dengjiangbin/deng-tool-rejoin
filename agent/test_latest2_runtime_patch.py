"""test/latest2-only runtime patches (v1.3.0 base + lime overlay).

Applied when ``lime_detection_enabled()`` — never on stable/main-dev channels.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_PATCHED = False


def apply_test_latest2_runtime_patches() -> None:
    """Monkey-patch v1.3.0 behaviors that block lime speed tests or hard stagger."""
    global _PATCHED
    if _PATCHED:
        return
    try:
        from .lime_channel import lime_detection_enabled
    except Exception:  # noqa: BLE001
        return
    if not lime_detection_enabled():
        return

    _patch_supervisor_recovery_gate()
    _patch_supervisor_stagger_safety()
    _patch_stagger_interval_15s()
    _patch_monitoring_relay()
    _patch_fast_start_cache_clear()
    _patch_landscape_readonly()
    _patch_orientation_readonly()
    _patch_termux_safe_terminal()
    _patch_no_direct_window_resize()
    _patch_delta_bypass_at_start()
    _PATCHED = True


def _patch_stagger_interval_15s() -> None:
    """Hardcode 15s between stagger launches on test/latest2."""
    try:
        from . import supervisor as sup
    except Exception:  # noqa: BLE001
        return
    cls = sup.WatchdogSupervisor
    cls.LAUNCH_STAGGER_SECONDS = 15
    cls._test_latest2_stagger_interval_patched = True


def _patch_supervisor_recovery_gate() -> None:
    """Do not halt the watchdog round-robin on one package's recovery gate.

    v1.3.0 ``_run_blocking_recovery_gate`` spins until Online/Dead for the
    current package only.  While blocked, the main round-robin never reaches
    other clones — force-close on them looks "stuck on the last package".
    Lime's parallel dead hot lane + force_close_race still run, but the UX
    and supervisor relaunch cadence suffer.  On test/latest2 we cap the gate
    at 15s then continue the round so every package keeps getting checked.
    """
    try:
        from . import supervisor as sup
        from .logger import log_event
    except Exception:  # noqa: BLE001
        return

    cls = sup.WatchdogSupervisor
    if getattr(cls, "_test_latest2_recovery_gate_patched", False):
        return
    orig = cls._run_blocking_recovery_gate

    def _bounded_gate(
        self: Any,
        pkg: str,
        entry: dict[str, Any],
        *,
        package_index: int = 0,
        package_total: int = 0,
        render_callback: Any = None,
    ) -> None:
        started = time.time()
        deadline = started + 15.0
        while time.time() < deadline and not self.stop_event.is_set():
            try:
                state = self._evaluate_package_presence_isolated(pkg, entry)
            except Exception:  # noqa: BLE001
                break
            self._set_status(pkg, state)
            self._prev_state[pkg] = state
            if state in {sup.STATUS_ONLINE, sup.STATUS_DEAD}:
                log_event(
                    self._logger,
                    "info",
                    "[DENG_REJOIN_RECOVERY_GATE_EXIT]",
                    package=pkg,
                    result=state.lower(),
                    mode="test_latest2_bounded",
                )
                return
            try:
                cb = render_callback or self._render_callback
                if callable(cb):
                    cb()
            except Exception:  # noqa: BLE001
                pass
            self._interruptible_sleep(min(2.0, max(0.5, self.RECOVERY_GATE_POLL_SECONDS)))

        log_event(
            self._logger,
            "info",
            "[DENG_REJOIN_RECOVERY_GATE_SKIPPED]",
            package=pkg,
            mode="test_latest2_continue_round_robin",
            waited_sec=round(time.time() - started, 1),
        )

    cls._run_blocking_recovery_gate = _bounded_gate  # type: ignore[method-assign]
    cls._test_latest2_recovery_gate_patched = True


def _patch_supervisor_stagger_safety() -> None:
    """Backport stagger-safe recovery/display guards missing from v1.3.0 supervisor.

    v1.3.0 starts the watchdog as soon as clone 1 opens.  A brief process
    absence during load can map to Dead → cache clear → Relaunching while the
    Start thread is still staggering the rest (probe p-5e86495d7b).
    """
    try:
        from . import supervisor as sup
        from .logger import log_event
    except Exception:  # noqa: BLE001
        return

    cls = sup.WatchdogSupervisor
    if getattr(cls, "_test_latest2_stagger_safety_patched", False):
        return

    _recovery_states = {
        sup.STATUS_DEAD,
        sup.STATUS_DISCONNECTED,
        sup.STATUS_JOIN_FAILED,
    }

    if not hasattr(cls, "_test_latest2_orig_init"):
        cls._test_latest2_orig_init = cls.__init__

        def _init_with_initial_launch(self: Any, *args: Any, **kwargs: Any) -> None:
            cls._test_latest2_orig_init(self, *args, **kwargs)
            self._initial_launch_inflight = set()

        cls.__init__ = _init_with_initial_launch  # type: ignore[method-assign]

    if not hasattr(cls, "_test_latest2_orig_mark_launched"):
        cls._test_latest2_orig_mark_launched = cls.mark_package_launched

        def _mark_with_initial_launch(self: Any, pkg: str) -> None:
            with self._state_lock:
                first_open = pkg not in self._package_opened
            cls._test_latest2_orig_mark_launched(self, pkg)
            if first_open:
                self._initial_launch_inflight.add(pkg)

        cls.mark_package_launched = _mark_with_initial_launch  # type: ignore[method-assign]

    if not hasattr(cls, "_test_latest2_orig_detect"):
        cls._test_latest2_orig_detect = cls._detect_package_state

        def _detect_with_stagger_grace(
            self: Any, pkg: str, entry: dict[str, Any]
        ) -> tuple[str, dict[str, Any]]:
            state, detail = cls._test_latest2_orig_detect(self, pkg, entry)
            if state == sup.STATUS_ONLINE:
                self._initial_launch_inflight.discard(pkg)
                return state, detail
            if state in _recovery_states and (
                self._in_loading_grace(pkg)
                or (not self._all_launches_completed and pkg in self._initial_launch_inflight)
            ):
                detail = dict(detail)
                detail["reason"] = "stagger_launch_grace_suppress_dead"
                detail["reason_internal"] = "stagger_launch_grace_suppress_dead"
                return sup.STATUS_LAUNCHING, detail
            if state in {sup.STATUS_RELAUNCHING, sup.STATUS_REOPENING} and (
                pkg in self._initial_launch_inflight
            ):
                detail = dict(detail)
                detail["reason"] = "initial_launch_no_dead_event"
                return sup.STATUS_LAUNCHING, detail
            return state, detail

        cls._detect_package_state = _detect_with_stagger_grace  # type: ignore[method-assign]

    if not hasattr(cls, "_test_latest2_orig_handle_state"):
        cls._test_latest2_orig_handle_state = cls._handle_state

        def _handle_with_stagger_defer(
            self: Any,
            pkg: str,
            entry: dict[str, Any],
            state: str,
            prev: str,
            now: float,
            render_callback: Any = None,
            immediate_recovery: bool = False,
            detail: dict[str, Any] | None = None,
        ) -> bool:
            if state in _recovery_states and not self._all_launches_completed:
                log_event(
                    self._logger,
                    "debug",
                    "[DENG_REJOIN_STAGGER_RECOVERY_DEFERRED]",
                    package=pkg,
                    state=state,
                    action="wait_for_all_launches_completed",
                    mode="test_latest2",
                )
                return False
            if (
                state in _recovery_states
                and self._in_loading_grace(pkg)
                and pkg in self._initial_launch_inflight
            ):
                log_event(
                    self._logger,
                    "debug",
                    "[DENG_REJOIN_STAGGER_RECOVERY_DEFERRED]",
                    package=pkg,
                    state=state,
                    action="loading_grace_initial_launch",
                    mode="test_latest2",
                )
                return False
            return cls._test_latest2_orig_handle_state(
                self,
                pkg,
                entry,
                state,
                prev,
                now,
                render_callback=render_callback,
                immediate_recovery=immediate_recovery,
                detail=detail,
            )

        cls._handle_state = _handle_with_stagger_defer  # type: ignore[method-assign]

    cls._test_latest2_stagger_safety_patched = True


def _patch_monitoring_relay() -> None:
    """Route real presence commits through test/latest2 Monitoring relay only."""
    try:
        from . import supervisor as sup
        from .logger import log_event
        from .test_latest2_monitoring_relay import start_monitoring_relay
    except Exception:  # noqa: BLE001
        return

    cls = sup.WatchdogSupervisor
    if getattr(cls, "_test_latest2_monitoring_relay_patched", False):
        return

    _presence_states = frozenset(
        {
            sup.STATUS_ONLINE,
            sup.STATUS_DEAD,
            getattr(sup, "STATUS_NO_HEARTBEAT", "No Heartbeat"),
            sup.STATUS_DISCONNECTED,
            "No Heartbeat",
        }
    )

    if not hasattr(cls, "_test_latest2_orig_set_status"):
        cls._test_latest2_orig_set_status = cls._set_status

        def _set_status_relay_gated(self: Any, pkg: str, status: str) -> None:
            if (
                str(status or "").strip() in _presence_states
                and not getattr(self, "_monitoring_relay_commit", False)
            ):
                try:
                    from .test_latest2_monitoring_relay import submit_raw_evidence

                    hint = "online" if status == sup.STATUS_ONLINE else "dead"
                    if status == sup.STATUS_DISCONNECTED:
                        hint = "kicked"
                    submit_raw_evidence(
                        pkg,
                        hint=hint,
                        source="supervisor_blocked",
                        evidence=f"blocked_direct_set:{status}",
                    )
                except Exception:  # noqa: BLE001
                    pass
                log_event(
                    self._logger,
                    "debug",
                    "[DENG_REJOIN_MONITORING_RELAY_BLOCKED]",
                    package=pkg,
                    attempted_state=str(status),
                )
                return
            cls._test_latest2_orig_set_status(self, pkg, status)

        cls._set_status = _set_status_relay_gated  # type: ignore[method-assign]

    if not hasattr(cls, "_test_latest2_orig_mark_for_relay"):
        prior = cls.mark_package_launched

        def _mark_start_monitoring(self: Any, pkg: str) -> None:
            with self._state_lock:
                first_open = pkg not in self._package_opened
            prior(self, pkg)
            if first_open:
                try:
                    entries = {
                        p: self.entry_by_pkg.get(p) or {}
                        for p in self.packages
                    }
                    relay = start_monitoring_relay(
                        self,
                        list(self.packages),
                        entries=entries,
                        direct_set_status=cls._test_latest2_orig_set_status,
                    )
                    if relay is not None:
                        relay.note_launch(pkg)
                        log_event(
                            self._logger,
                            "info",
                            "[DENG_REJOIN_MONITORING_RELAY_STARTED]",
                            package_count=len(self.packages),
                        )
                except Exception as exc:  # noqa: BLE001
                    log_event(
                        self._logger,
                        "warning",
                        "[DENG_REJOIN_MONITORING_RELAY_START_FAILED]",
                        error=str(exc)[:120],
                    )
            else:
                try:
                    from .test_latest2_monitoring_relay import get_active_relay

                    relay = get_active_relay()
                    if relay is not None:
                        relay.note_launch(pkg)
                except Exception:  # noqa: BLE001
                    pass

        cls.mark_package_launched = _mark_start_monitoring  # type: ignore[method-assign]
        cls._test_latest2_orig_mark_for_relay = prior

    cls._test_latest2_monitoring_relay_patched = True


def _patch_fast_start_cache_clear() -> None:
    """Replace v1.3.0 per-package verified cache clear with one mass batch.

    v1.3.0 ``commands.py`` loops ``android.clear_package_cache_verified`` per
    package on the Start UI thread (table freezes for N×timeout).  The first
    call mass-clears every enabled clone once; later loop iterations are instant.
    """
    try:
        from . import android
    except Exception:  # noqa: BLE001
        return
    if getattr(android, "_test_latest2_fast_cache_clear_patched", False):
        return

    import shlex

    _orig = android.clear_package_cache_verified
    _mass_batch: dict[str, dict[str, object]] | None = None
    _mass_batch_at: float = 0.0
    _MASS_BATCH_TTL_S = 120.0

    def _enabled_start_packages() -> list[str]:
        try:
            from .config import enabled_package_names, load_config, validate_config

            return list(enabled_package_names(validate_config(load_config())))
        except Exception:  # noqa: BLE001
            return []

    def _status_to_verified(status: str) -> dict[str, object]:
        st = str(status or "").strip()
        return {
            "success": st in {"Cleared", "OK", "cleared"},
            "skipped": st in {"Skipped", "Aborted"},
            "skipped_reason": st if st in {"Skipped", "Aborted"} else "",
            "cache_paths": [],
            "size_before_bytes": -1,
            "size_after_bytes": 0,
            "attempts": 1,
            "error": "" if st in {"Cleared", "OK", "Skipped", "Aborted"} else st[:120],
            "method": "test_latest2_mass_batch",
        }

    def _run_mass_batch(packages: list[str]) -> dict[str, dict[str, object]]:
        nonlocal _mass_batch, _mass_batch_at
        root_info = android.detect_root()
        if not root_info.available or not root_info.tool:
            empty = _status_to_verified("Skipped")
            empty["skipped_reason"] = "root_unavailable"
            return {p: dict(empty) for p in packages}
        mass = android.clear_packages_cache_mass_batch(
            packages,
            root_info=root_info,
            per_package_timeout_s=8,
            batch_max_s=max(12.0, 8.0 * len(packages)),
        )
        out: dict[str, dict[str, object]] = {}
        for pkg in packages:
            out[pkg] = _status_to_verified(str(mass.get(pkg) or "Failed"))
        _mass_batch = out
        _mass_batch_at = time.time()
        try:
            from . import safe_io

            safe_io.restore_terminal()
        except Exception:  # noqa: BLE001
            pass
        return out

    def _fast_verified(package: str, *, max_retries: int = 2) -> dict[str, object]:
        nonlocal _mass_batch, _mass_batch_at
        package = android.validate_package_name(package)
        if _mass_batch is not None and (time.time() - _mass_batch_at) > _MASS_BATCH_TTL_S:
            _mass_batch = None
        if _mass_batch is None:
            pkgs = _enabled_start_packages() or [package]
            if package not in pkgs:
                pkgs = [package, *pkgs]
            _run_mass_batch(pkgs)
        if _mass_batch and package in _mass_batch:
            return dict(_mass_batch[package])
        # Fallback: single-package fast clear (manual / out-of-start calls).
        root_info = android.detect_root()
        if not root_info.available or not root_info.tool:
            return {
                "success": False,
                "skipped": True,
                "skipped_reason": "root_unavailable",
                "cache_paths": [],
                "size_before_bytes": 0,
                "size_after_bytes": 0,
                "attempts": 0,
                "error": "",
            }
        paths = [
            f"/data/user/0/{package}/cache",
            f"/data/user/0/{package}/code_cache",
            f"/data/user/0/{package}/files/tmp",
            f"/data/user/0/{package}/files/http",
            f"/data/data/{package}/cache",
            f"/data/data/{package}/code_cache",
            f"/data/data/{package}/files/tmp",
            f"/data/data/{package}/files/http",
        ]
        quoted = " ".join(shlex.quote(p) for p in paths)
        sh = (
            f"for p in {quoted}; do "
            f'[ -d "$p" ] && find "$p" -mindepth 1 -delete 2>/dev/null; '
            f"done"
        )
        res = android.run_root_command(
            ["sh", "-c", sh],
            root_tool=root_info.tool,
            timeout=8,
        )
        ok = bool(getattr(res, "ok", False)) or getattr(res, "returncode", 1) in (0, 1)
        if getattr(res, "timed_out", False):
            ok = False
        return {
            "success": ok,
            "skipped": False,
            "skipped_reason": "",
            "cache_paths": paths,
            "size_before_bytes": -1,
            "size_after_bytes": 0 if ok else -1,
            "attempts": 1,
            "error": "" if ok else (getattr(res, "stderr", "") or "clear_failed")[:120],
            "method": "test_latest2_fast_find_delete",
        }

    android.clear_package_cache_verified = _fast_verified  # type: ignore[assignment]
    android._test_latest2_fast_cache_clear_patched = True
    android._test_latest2_orig_clear_package_cache_verified = _orig


def _landscape_readonly_state(
    *,
    phase: str,
    screen_mode_config: str,
) -> dict[str, object]:
    from . import android

    before_display = android.get_display_orientation_state()
    wm_state = android.get_wm_size()
    density = android.get_wm_density()
    rotation = android.get_rotation_settings()
    return {
        "phase": phase,
        "screen_mode_config": screen_mode_config,
        "before_display": before_display,
        "after_display": before_display,
        "wm_size": wm_state,
        "density": density,
        "rotation": rotation,
        "correction_applied": [],
        "apply_correction": False,
        "test_latest2_readonly": True,
    }


def _patch_landscape_readonly() -> None:
    """Never rotate the display on test/latest2 — preserves Termux touch + table."""
    try:
        from . import android
    except Exception:  # noqa: BLE001
        return
    if getattr(android, "_test_latest2_landscape_readonly_patched", False):
        return
    import inspect

    sig = inspect.signature(android.enforce_landscape_home_state)
    if "apply_correction" in sig.parameters:
        orig = android.enforce_landscape_home_state

        def _enforce_with_apply_correction(
            *,
            phase: str = "before_start",
            screen_mode_config: str = "landscape",
            apply_correction: bool = False,
            **kwargs: Any,
        ) -> dict[str, object]:
            if apply_correction:
                return orig(
                    phase=phase,
                    screen_mode_config=screen_mode_config,
                    apply_correction=True,
                    **kwargs,
                )
            return _landscape_readonly_state(
                phase=phase,
                screen_mode_config=screen_mode_config,
            )

        android.enforce_landscape_home_state = _enforce_with_apply_correction  # type: ignore[assignment]
    else:
        orig = android.enforce_landscape_home_state

        def _enforce_readonly_v130(
            *,
            phase: str = "before_start",
            screen_mode_config: str = "landscape",
            apply_correction: bool = False,
            **kwargs: Any,
        ) -> dict[str, object]:
            if apply_correction:
                return orig(phase=phase, screen_mode_config=screen_mode_config, **kwargs)
            return _landscape_readonly_state(
                phase=phase,
                screen_mode_config=screen_mode_config,
            )

        android.enforce_landscape_home_state = _enforce_readonly_v130  # type: ignore[assignment]

    android._test_latest2_landscape_readonly_patched = True
    android._test_latest2_orig_enforce_landscape_home_state = orig


def _patch_orientation_readonly() -> None:
    """Skip ``enforce_screen_orientation`` rotation lock during Start on test/latest2."""
    try:
        from . import android
    except Exception:  # noqa: BLE001
        return
    if getattr(android, "_test_latest2_orientation_readonly_patched", False):
        return

    def _orientation_readonly(
        screen_mode: str,
        *,
        protected_packages: Any | None = None,
        allow_disable: bool = False,
    ) -> dict[str, object]:
        from . import android as _android

        before = _android.get_display_orientation_state()
        requested = str(screen_mode or "auto").strip().lower()
        return {
            "requested": requested,
            "actual_before": before.get("orientation", "unknown"),
            "actual_after": before.get("orientation", "unknown"),
            "before": before,
            "after": before,
            "root_available": bool(_android.detect_root().available),
            "success": True,
            "override_detected": False,
            "override_package": "",
            "override_candidates": [],
            "override_action": "none",
            "apply_results": [],
            "error": "",
            "test_latest2_rotation_skipped": True,
        }

    android.enforce_screen_orientation = _orientation_readonly  # type: ignore[assignment]
    android._test_latest2_orientation_readonly_patched = True


def _patch_termux_safe_terminal() -> None:
    """Never erase Termux scrollback during Start table redraws (breaks input)."""
    try:
        from . import safe_io
    except Exception:  # noqa: BLE001
        return
    if getattr(safe_io, "_test_latest2_termux_safe_terminal_patched", False):
        return

    _orig_clear = safe_io.safe_clear_screen

    def _termux_safe_clear(*, clear_scrollback: bool = False) -> None:
        _orig_clear(clear_scrollback=False)
        try:
            safe_io.restore_terminal()
        except Exception:  # noqa: BLE001
            pass

    safe_io.safe_clear_screen = _termux_safe_clear  # type: ignore[assignment]
    safe_io._test_latest2_termux_safe_terminal_patched = True
    safe_io._test_latest2_orig_safe_clear_screen = _orig_clear


def _patch_no_direct_window_resize() -> None:
    """Block ``am stack resize`` / task resize — same Termux freeze as probe p-e70faf05a3."""
    try:
        from . import window_apply as wa
    except Exception:  # noqa: BLE001
        return
    if getattr(wa, "_test_latest2_no_direct_resize_patched", False):
        return

    _orig_apply = wa.apply_window_layout
    _orig_direct = wa._direct_resize_via_root
    _orig_force = wa.force_resize_package

    def _apply_without_direct_resize(
        rects: Any,
        *,
        allow_direct_resize: bool = True,
        **kwargs: Any,
    ) -> Any:
        return _orig_apply(rects, allow_direct_resize=False, **kwargs)

    def _direct_resize_disabled(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
        return False, "test_latest2 direct resize disabled"

    def _force_resize_disabled(*_args: Any, **_kwargs: Any) -> tuple[bool, str]:
        return False, "test_latest2 force resize disabled"

    wa.apply_window_layout = _apply_without_direct_resize  # type: ignore[assignment]
    wa._direct_resize_via_root = _direct_resize_disabled  # type: ignore[assignment]
    wa.force_resize_package = _force_resize_disabled  # type: ignore[assignment]
    wa._test_latest2_no_direct_resize_patched = True
    wa._test_latest2_orig_apply_window_layout = _orig_apply


def _patch_delta_bypass_at_start() -> None:
    """After first clone Start launch: Lime OCR → Receive Key → token → inject → relaunch."""
    try:
        from . import launcher as launcher_mod
    except Exception:  # noqa: BLE001
        return
    if getattr(launcher_mod, "_test_latest2_lime_bypass_patched", False):
        return

    orig = launcher_mod.perform_rejoin
    launcher_mod._test_latest2_orig_perform_rejoin = orig

    def _perform_rejoin_with_lime_bypass(
        config_data: dict[str, Any],
        *,
        reason: str = "manual",
        package_entry: dict[str, Any] | None = None,
        no_force_stop: bool = False,
    ) -> Any:
        result = orig(
            config_data,
            reason=reason,
            package_entry=package_entry,
            no_force_stop=no_force_stop,
        )
        if reason != "start" or not result.ok:
            return result
        pkg = str((package_entry or {}).get("package") or config_data.get("roblox_package") or "")
        try:
            from .lime_delta_key_bypass import is_first_stagger_package, run_lime_delta_bypass_flow

            if not is_first_stagger_package(pkg, config_data):
                return result

            def _background_bypass() -> None:
                try:
                    flow = run_lime_delta_bypass_flow(pkg, config_data)
                    if flow.get("relaunch_requested"):
                        orig(
                            config_data,
                            reason=reason,
                            package_entry=package_entry,
                            no_force_stop=no_force_stop,
                        )
                except Exception:  # noqa: BLE001
                    pass

            threading.Thread(
                target=_background_bypass,
                name=f"lime-delta-bypass-{pkg}",
                daemon=True,
            ).start()
        except Exception:  # noqa: BLE001
            pass
        return result

    launcher_mod.perform_rejoin = _perform_rejoin_with_lime_bypass  # type: ignore[assignment]
    launcher_mod._test_latest2_lime_bypass_patched = True
