"""Lime-style Delta executor key-dialog bypass (test/latest2 only).

Lime Rejoiner APK exposes ``autokey_enabled``, ``key_markers``, and
``key_text_markers`` — an OCR scan that dismisses Delta / executor key prompts
so Roblox load is not blocked.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .constants import DATA_DIR
from .lime_channel import lime_detection_enabled

STATE_PATH = DATA_DIR / "lime-delta-key-bypass-state.json"
SCAN_INTERVAL_S = float(os.environ.get("DENG_REJOIN_DELTA_KEY_SCAN_SEC", "2.5") or "2.5")
OCR_TIMEOUT_S = float(os.environ.get("DENG_REJOIN_DELTA_KEY_OCR_TIMEOUT_SEC", "4") or "4")

DELTA_KEY_DIALOG_MARKERS = (
    "enter key",
    "license key",
    "paste key",
    "delta key",
    "script key",
    "key required",
    "enter your key",
)

DELTA_KEY_BYPASS_MARKERS = (
    "key bypass",
    "bypass key",
    "skip",
    "continue without",
)

_active: "DeltaKeyBypassScanner | None" = None
_active_lock = threading.Lock()


@dataclass
class DeltaKeyBypassState:
    last_scan_at: float | None = None
    last_bypass_at: float | None = None
    last_marker: str = ""
    last_action: str = ""
    scan_count: int = 0
    bypass_count: int = 0
    last_error: str = ""


class DeltaKeyBypassScanner:
    """Background OCR scan; sends BACK or ENTER when a bypass label is seen."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._state = DeltaKeyBypassState()

    def start(self) -> None:
        if not lime_detection_enabled():
            return
        if os.environ.get("DENG_REJOIN_DISABLE_DELTA_KEY_BYPASS", "").strip() in {
            "1",
            "true",
            "yes",
        }:
            return
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop,
                name="lime-delta-key-bypass",
                daemon=True,
            )
            self._thread.start()
            set_active_delta_key_bypass(self)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan_once()
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._state.last_error = str(exc)[:120]
            self._write_state()
            self._stop.wait(max(0.25, SCAN_INTERVAL_S))

    def _scan_once(self) -> None:
        now = self._clock()
        with self._lock:
            self._state.scan_count += 1
            self._state.last_scan_at = now
        text = self._ocr_screen_text()
        if not text:
            return
        lower = text.lower()
        if not any(m in lower for m in DELTA_KEY_DIALOG_MARKERS + DELTA_KEY_BYPASS_MARKERS):
            return
        marker = next(
            (m for m in DELTA_KEY_DIALOG_MARKERS + DELTA_KEY_BYPASS_MARKERS if m in lower),
            "key_dialog",
        )
        action = self._attempt_bypass(lower)
        with self._lock:
            self._state.last_marker = marker
            self._state.last_action = action
            if action.startswith("bypass_"):
                self._state.bypass_count += 1
                self._state.last_bypass_at = now

    def _ocr_screen_text(self) -> str:
        try:
            from . import snapshot as _snap

            cap = _snap.capture_snapshot_detailed()
            if not cap.ok or not cap.data:
                return ""
        except Exception:  # noqa: BLE001
            return ""
        tmp = DATA_DIR / f"lime-delta-key-{int(self._clock())}.png"
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(cap.data)
            import shutil

            if shutil.which("termux-ocr"):
                res = subprocess.run(
                    ["termux-ocr", "-i", str(tmp)],
                    capture_output=True,
                    text=True,
                    timeout=OCR_TIMEOUT_S,
                    errors="replace",
                )
                return (res.stdout or "").strip()
            if shutil.which("tesseract"):
                res = subprocess.run(
                    ["tesseract", str(tmp), "stdout", "-l", "eng"],
                    capture_output=True,
                    text=True,
                    timeout=OCR_TIMEOUT_S,
                    errors="replace",
                )
                return (res.stdout or "").strip()
        except Exception:  # noqa: BLE001
            return ""
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        return ""

    def _attempt_bypass(self, lower_text: str) -> str:
        try:
            from . import android

            root = android.detect_root()
            tool = str(root.tool or "") if root.available else ""
        except Exception:  # noqa: BLE001
            tool = ""
        cmds: list[list[str]] = []
        if any(m in lower_text for m in DELTA_KEY_BYPASS_MARKERS):
            cmds.append(["input", "keyevent", "66"])
        cmds.append(["input", "keyevent", "4"])
        for cmd in cmds:
            try:
                if tool:
                    from .android import run_root_command

                    run_root_command(cmd, root_tool=tool, timeout=2)
                else:
                    subprocess.run(cmd, capture_output=True, timeout=2)
            except Exception:  # noqa: BLE001
                continue
        return "bypass_back" if len(cmds) == 1 else "bypass_enter_back"

    def _write_state(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(probe_snapshot(), indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass

    def probe_row(self) -> dict[str, Any]:
        with self._lock:
            st = self._state
            return {
                "enabled": True,
                "last_scan_at": st.last_scan_at,
                "last_bypass_at": st.last_bypass_at,
                "last_marker": st.last_marker or None,
                "last_action": st.last_action or None,
                "scan_count": st.scan_count,
                "bypass_count": st.bypass_count,
                "last_error": st.last_error or None,
                "scan_interval_s": SCAN_INTERVAL_S,
            }


def get_active_delta_key_bypass() -> DeltaKeyBypassScanner | None:
    with _active_lock:
        return _active


def set_active_delta_key_bypass(scanner: DeltaKeyBypassScanner | None) -> None:
    global _active
    with _active_lock:
        _active = scanner


def start_delta_key_bypass() -> DeltaKeyBypassScanner | None:
    if not lime_detection_enabled():
        return None
    scanner = DeltaKeyBypassScanner()
    scanner.start()
    return scanner


def probe_snapshot() -> dict[str, Any]:
    scanner = get_active_delta_key_bypass()
    if scanner is not None:
        snap = scanner.probe_row()
        snap["live_process"] = True
        return snap
    try:
        if STATE_PATH.is_file() and (time.time() - STATE_PATH.stat().st_mtime) <= 30.0:
            parsed = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                parsed["live_process"] = False
                return parsed
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {"enabled": lime_detection_enabled(), "live_process": False, "bypass_count": 0}
