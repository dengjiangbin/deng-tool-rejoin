"""Root screencap + OCR fallback for kick/disconnect/bot-challenge screens.

OCR is confirmation/fallback only — it must never block lifecycle launch.
No Roblox cookie or session storage is read.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .constants import DATA_DIR

OCR_STATE_PATH = DATA_DIR / "ocr-screen-detector-state.json"
OCR_STATE_MAX_AGE_SECONDS = 20.0
OCR_STATE_WRITE_MIN_INTERVAL_SECONDS = 1.0

# Kick / disconnect / bot-challenge phrases (case-insensitive substring match).
OCR_DEAD_PHRASES = (
    "kicked",
    "left game",
    "disconnected",
    "last reason for disconnect",
    "verifying you're not a bot",
    "verifying you’re not a bot",
    "not a bot",
    "complete the challenge",
    "solve the puzzle",
    "start puzzle",
    "use the arrows",
    "press the arrows",
)

OCR_SUSPICIOUS_PHRASES = OCR_DEAD_PHRASES

OCR_POLL_INTERVAL_SECONDS = float(os.environ.get("DENG_REJOIN_OCR_POLL_SEC", "2") or "2")
OCR_ENABLED = os.environ.get("DENG_REJOIN_DISABLE_OCR", "").strip() not in {"1", "true", "yes"}

_active_detector: "OcrScreenDetector | None" = None
_active_lock = threading.Lock()


def get_active_ocr_detector() -> "OcrScreenDetector | None":
    with _active_lock:
        return _active_detector


def set_active_ocr_detector(detector: "OcrScreenDetector | None") -> None:
    global _active_detector
    with _active_lock:
        _active_detector = detector


@dataclass
class OcrMatch:
    phrase: str
    matched_text: str
    category: str = "dead"


@dataclass
class PackageOcrState:
    package: str
    last_scan_at: float = 0.0
    last_match_at: float = 0.0
    last_match_phrase: str = ""
    last_match_text: str = ""
    scan_count: int = 0
    ocr_backend: str = ""
    ocr_available: bool = False
    last_error: str = ""
    suspicious: bool = False


class OcrScreenDetector:
    """Non-blocking OCR fallback using root screencap."""

    def __init__(
        self,
        packages: list[str],
        *,
        should_scan: Callable[[str], bool] | None = None,
        on_match: Callable[[str, float, OcrMatch], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.packages = [str(p).strip() for p in packages if str(p).strip()]
        self._should_scan = should_scan or (lambda _pkg: False)
        self._on_match = on_match
        self._clock = clock or time.time
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._session_active = False
        self._thread: threading.Thread | None = None
        self._ocr_backend = ""
        self._ocr_available = False
        self._last_state_write_at = 0.0
        self._packages: dict[str, PackageOcrState] = {
            pkg: PackageOcrState(package=pkg) for pkg in self.packages
        }

    def start(self) -> None:
        if not OCR_ENABLED:
            return
        with self._lock:
            if self._session_active:
                return
            self._probe_ocr_backend()
            self._session_active = True
            self._thread = threading.Thread(
                target=self._loop,
                name="lime-ocr-detector",
                daemon=True,
            )
            self._thread.start()
            set_active_ocr_detector(self)
        self._write_state_file(force=True)

    def stop(self) -> None:
        self._stop_event.set()
        with self._lock:
            self._session_active = False
        if get_active_ocr_detector() is self:
            set_active_ocr_detector(None)
        try:
            OCR_STATE_PATH.unlink()
        except OSError:
            pass

    def _probe_ocr_backend(self) -> None:
        for name, cmd in (
            ("termux-ocr", ["termux-ocr", "--help"]),
            ("tesseract", ["tesseract", "--version"]),
        ):
            if shutil.which(cmd[0]):
                self._ocr_backend = name
                self._ocr_available = True
                return
        self._ocr_backend = ""
        self._ocr_available = False

    def _run_ocr(self, png_bytes: bytes) -> str:
        if not self._ocr_available or not png_bytes:
            return ""
        tmp = DATA_DIR / f"lime-ocr-{int(self._clock())}.png"
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(png_bytes)
            if self._ocr_backend == "termux-ocr":
                res = subprocess.run(
                    ["termux-ocr", "-i", str(tmp)],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    errors="replace",
                )
                return (res.stdout or "").strip()
            if self._ocr_backend == "tesseract":
                res = subprocess.run(
                    ["tesseract", str(tmp), "stdout", "-l", "eng"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    errors="replace",
                )
                return (res.stdout or "").strip()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                for row in self._packages.values():
                    row.last_error = str(exc)[:120]
            return ""
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        return ""

    @staticmethod
    def match_text(text: str) -> OcrMatch | None:
        lower = str(text or "").lower()
        if not lower.strip():
            return None
        for phrase in OCR_DEAD_PHRASES:
            if phrase.lower() in lower:
                return OcrMatch(phrase=phrase, matched_text=text[:240], category="dead")
        return None

    def _scan_package_once(self, pkg: str, now: float) -> None:
        row = self._packages.setdefault(pkg, PackageOcrState(package=pkg))
        row.scan_count += 1
        row.last_scan_at = now
        row.ocr_backend = self._ocr_backend
        row.ocr_available = self._ocr_available
        if not self._ocr_available:
            row.last_error = row.last_error or "ocr_backend_unavailable"
            return
        try:
            from . import snapshot as _snap

            cap = _snap.capture_snapshot_detailed()
            if not cap.ok or not cap.data:
                row.last_error = cap.result or "screencap_failed"
                return
            text = self._run_ocr(cap.data)
            match = self.match_text(text)
            if match is None:
                return
            row.last_match_at = now
            row.last_match_phrase = match.phrase
            row.last_match_text = match.matched_text
            row.suspicious = True
            row.last_error = ""
            if self._on_match is not None:
                try:
                    self._on_match(pkg, now, match)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            row.last_error = str(exc)[:120]

    def _loop(self) -> None:
        interval = max(1.0, OCR_POLL_INTERVAL_SECONDS)
        while not self._stop_event.is_set():
            now = self._clock()
            for pkg in list(self.packages):
                if self._stop_event.is_set():
                    break
                try:
                    if self._should_scan(pkg):
                        self._scan_package_once(pkg, now)
                except Exception:  # noqa: BLE001
                    pass
            try:
                self._write_state_file()
            except Exception:  # noqa: BLE001
                pass
            self._stop_event.wait(interval)

    def _write_state_file(self, *, force: bool = False) -> None:
        now = self._clock()
        if not force and (now - self._last_state_write_at) < OCR_STATE_WRITE_MIN_INTERVAL_SECONDS:
            return
        self._last_state_write_at = now
        try:
            OCR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "written_at": time.time(),
                "snapshot": self.probe_snapshot(),
            }
            tmp = OCR_STATE_PATH.with_suffix(".json.tmp")
            tmp.write_text(__import__("json").dumps(payload), encoding="utf-8")
            os.replace(tmp, OCR_STATE_PATH)
        except Exception:  # noqa: BLE001
            pass

    def probe_snapshot(self) -> dict[str, Any]:
        with self._lock:
            packages_out = {
                pkg: {
                    "last_scan_at": row.last_scan_at or None,
                    "last_match_at": row.last_match_at or None,
                    "last_match_phrase": row.last_match_phrase or None,
                    "scan_count": row.scan_count,
                    "ocr_backend": row.ocr_backend or None,
                    "ocr_available": row.ocr_available,
                    "last_error": row.last_error or None,
                    "suspicious": row.suspicious,
                }
                for pkg, row in self._packages.items()
            }
            return {
                "enabled": self._session_active and OCR_ENABLED,
                "ocr_available": self._ocr_available,
                "ocr_backend": self._ocr_backend or None,
                "poll_interval_ms": round(OCR_POLL_INTERVAL_SECONDS * 1000.0, 0),
                "dead_phrases": list(OCR_DEAD_PHRASES),
                "packages": packages_out,
            }


def read_ocr_state_file(*, max_age_s: float = OCR_STATE_MAX_AGE_SECONDS) -> dict[str, Any] | None:
    try:
        data = __import__("json").loads(OCR_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        age = time.time() - float(data.get("written_at"))
    except (TypeError, ValueError):
        return None
    if age > max_age_s:
        return None
    snap = data.get("snapshot")
    if not isinstance(snap, dict):
        return None
    out = dict(snap)
    out["source"] = "state_file"
    out["state_file_age_s"] = round(age, 2)
    return out


def probe_ocr_snapshot() -> dict[str, Any]:
    live = get_active_ocr_detector()
    if live is not None and live._session_active:
        return live.probe_snapshot()
    disk = read_ocr_state_file()
    if disk is not None:
        return disk
    return {
        "enabled": False,
        "ocr_available": bool(shutil.which("termux-ocr") or shutil.which("tesseract")),
        "reason": "session_inactive",
        "packages": {},
    }
