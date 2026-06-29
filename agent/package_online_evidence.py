"""Fresh per-scan Roblox online evidence — not recents, not stale cache."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import android
from .config import validate_package_name
from .experience_detector import EvidenceLevel, detect_experience_state
from .roblox_health import analyze_disconnect_signals

# Internal lifecycle labels (probe + state machine).
STATE_STOPPED = "STOPPED"
STATE_LAUNCHING = "LAUNCHING"
STATE_ONLINE_CONFIRMED = "ONLINE_CONFIRMED"
STATE_DISCONNECTED = "DISCONNECTED"
STATE_DEAD = "DEAD"
STATE_RELAUNCHING = "RELAUNCHING"

_DISCONNECT_UI_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bDisconnected\b", re.I),
    re.compile(r"\bConnection lost\b", re.I),
    # ALL Roblox error codes are treated as dead, not just 278 (user request
    # p-1bc476d931).  A generic "Error Code: <n>" / "Error Code <n>" anywhere in
    # the kick dialog (e.g. 260-280, 517, 524, 529 HTTP error) is a real
    # disconnect — the numeric code is later parsed by parse_roblox_error_code.
    re.compile(r"\bError Code:?\s*\d+\b", re.I),
    re.compile(r"\bA?\s*Http error has occurred\b", re.I),
    re.compile(r"\bPlease close the client and try again\b", re.I),
    re.compile(r"\bdisconnected for being idle\b", re.I),
    re.compile(r"\bYou were disconnected\b", re.I),
    re.compile(r"\bYou were kicked\b", re.I),
    re.compile(r"\bsame account launched\b", re.I),
    re.compile(r"\bWaiting for an available server\b", re.I),
    re.compile(r"\bReconnect\b", re.I),
    re.compile(r"\bConnectionError\b", re.I),
]

# Captcha / bot-verification block (user request p-1bc476d931).  Roblox shows an
# Arkose/FunCaptcha "Verifying you're not a bot" challenge inside a WebView
# overlay.  This is NOT a normal disconnect and must NOT trigger recovery — the
# account is flagged Captcha and left hanging for a human.  These phrases are the
# stable, non-localized strings on that screen.
_CAPTCHA_UI_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Verifying you'?re not a bot", re.I),
    re.compile(r"\bnot a bot\b", re.I),
    re.compile(r"\bStart Puzzle\b", re.I),
    re.compile(r"solve this challenge", re.I),
    re.compile(r"know you are a real person", re.I),
    re.compile(r"\bFunCaptcha\b", re.I),
    re.compile(r"\barkose\b", re.I),
]

# Conservative idle-kick patterns for full window dumps (avoid generic Reconnect-only hits).
_IDLE_DISCONNECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bdisconnected for being idle\b", re.I),
    re.compile(r"\bError Code:\s*278\b", re.I),
    re.compile(r"\bError Code 278\b", re.I),
    re.compile(r"\bYou were disconnected\b.*\bidle\b", re.I),
    re.compile(r"\bidle\s+\d+\s+minutes\b", re.I),
]

_HOME_ONLY_FRAGMENTS = (
    "splashactivity",
    "launchactivity",
    "bootstrapactivity",
    "landingactivity",
    "mainactivity",
    "launcheractivity",
)

_INGAME_FRAGMENTS = (
    "gameactivity",
    "robloxgame",
    "gameclient",
    "experienceactivity",
    "placejoin",
    "activitynativemain",
)

_RESUMED_ACTIVITY_RE = re.compile(
    r"mResumedActivity:\s*ActivityRecord\{[^}]*\s+([^\s/]+)/([^\s}]+)",
)
_TOP_ACTIVITY_RE = re.compile(
    r"ACTIVITY\s+([^\s/]+)/([^\s]+)\s+pid=",
    re.I,
)
_FOCUS_RE = re.compile(
    r"mCurrentFocus=Window\{[^}]*\s+([^\s/]+)/([^\s}]+)",
)
_STOPPED_RE = re.compile(r"\bstopped=(true|1)\b", re.I)
_USER_STOPPED_RE = re.compile(r"\buserStopped=(true|1)\b", re.I)


@dataclass
class OnlineEvidenceScan:
    package: str
    scanned_at: float = 0.0
    pid_exists: bool = False
    pid: str = ""
    force_stopped: bool = False
    top_activity: str = ""
    resumed_activity: str = ""
    foreground_package: str = ""
    has_resumed_or_top_for_package: bool = False
    uiautomator_status: str = "not_available"
    disconnected_text_detected: bool = False
    matched_disconnect_text: str | None = None
    logcat_disconnect_detected: bool = False
    experience_level: str = ""
    recents_only: bool = False
    in_game_evidence: bool = False
    home_or_lobby_only: bool = False

    def as_probe_dict(self) -> dict[str, Any]:
        age = max(0.0, time.time() - self.scanned_at) if self.scanned_at else 0.0
        return {
            "pid_exists": self.pid_exists,
            "force_stopped": self.force_stopped,
            "top_activity": self.top_activity or "",
            "resumed_activity": self.resumed_activity or "",
            "foreground_package": self.foreground_package or "",
            "uiautomator_status": self.uiautomator_status,
            "disconnected_text_detected": self.disconnected_text_detected,
            "matched_disconnect_text": self.matched_disconnect_text,
            "logcat_disconnect_detected": self.logcat_disconnect_detected,
            "evidence_age_seconds": round(age, 1),
            "in_game_evidence": self.in_game_evidence,
            "home_or_lobby_only": self.home_or_lobby_only,
            "recents_only": self.recents_only,
            "experience_level": self.experience_level,
        }


@dataclass
class OnlineDecision:
    is_online_confirmed: bool
    reason: str
    failed_checks: list[str] = field(default_factory=list)
    is_disconnected: bool = False
    is_dead: bool = False


def _pid_for_package(package: str, root_info: Any = None) -> tuple[bool, str]:
    pkg = validate_package_name(package)
    root_tool = getattr(root_info, "tool", None) if root_info else None
    if getattr(root_info, "available", False) and root_tool:
        try:
            res = android.run_root_command(["pidof", pkg], root_tool=root_tool, timeout=2)
            if res.ok and (res.stdout or "").strip():
                return True, (res.stdout or "").strip().split()[0]
        except Exception:  # noqa: BLE001
            pass
    try:
        res = android.run_command(["pidof", pkg], timeout=2)
        if res.ok and (res.stdout or "").strip():
            return True, (res.stdout or "").strip().split()[0]
    except Exception:  # noqa: BLE001
        pass
    return False, ""


def _package_force_stopped(package: str) -> bool:
    try:
        res = android.run_android_command(["dumpsys", "package", package], timeout=6, prefer_root=True)
        text = res.stdout if res.ok else ""
        if not text:
            return False
        for line in text.splitlines():
            if package not in line:
                continue
            if _STOPPED_RE.search(line) or _USER_STOPPED_RE.search(line):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _activity_components_from_dumpsys(package: str) -> tuple[str, str, str]:
    """Return (resumed_activity, top_activity, foreground_package) from current dumpsys."""
    resumed = ""
    top = ""
    foreground_pkg = ""
    pkg = validate_package_name(package)
    try:
        act = android.run_command(["dumpsys", "activity", "activities"], timeout=6)
        if act.ok and act.stdout:
            for match in _RESUMED_ACTIVITY_RE.finditer(act.stdout):
                fg_pkg, act_name = match.group(1), match.group(2)
                if fg_pkg == pkg:
                    resumed = f"{fg_pkg}/{act_name}"
                    break
    except Exception:  # noqa: BLE001
        pass
    try:
        top_res = android.run_command(["dumpsys", "activity", "top"], timeout=5)
        if top_res.ok and top_res.stdout:
            for line in top_res.stdout.splitlines():
                if pkg not in line:
                    continue
                m = _TOP_ACTIVITY_RE.search(line)
                if m and m.group(1) == pkg:
                    top = f"{m.group(1)}/{m.group(2)}"
                    break
    except Exception:  # noqa: BLE001
        pass
    try:
        win = android.run_command(["dumpsys", "window", "windows"], timeout=6)
        if win.ok and win.stdout:
            for match in _FOCUS_RE.finditer(win.stdout):
                fg_pkg, act_name = match.group(1), match.group(2)
                if fg_pkg == pkg:
                    foreground_pkg = fg_pkg
                    if not top:
                        top = f"{fg_pkg}/{act_name}"
                    break
    except Exception:  # noqa: BLE001
        pass
    return resumed, top, foreground_pkg


def _activity_is_home_only(component: str) -> bool:
    comp = str(component or "").lower()
    return any(fragment in comp for fragment in _HOME_ONLY_FRAGMENTS)


def _activity_is_in_game(component: str) -> bool:
    comp = str(component or "").lower()
    return any(fragment in comp for fragment in _INGAME_FRAGMENTS)


def _scan_disconnect_text(blob: str) -> tuple[bool, str | None]:
    if not blob:
        return False, None
    for pattern in _DISCONNECT_UI_PATTERNS:
        match = pattern.search(blob)
        if match:
            return True, match.group(0)[:120]
    return False, None


def _scan_idle_disconnect_text(blob: str) -> tuple[bool, str | None]:
    if not blob:
        return False, None
    for pattern in _IDLE_DISCONNECT_PATTERNS:
        match = pattern.search(blob)
        if match:
            return True, match.group(0)[:120]
    return False, None


def _scan_captcha_text(blob: str) -> tuple[bool, str | None]:
    if not blob:
        return False, None
    for pattern in _CAPTCHA_UI_PATTERNS:
        match = pattern.search(blob)
        if match:
            return True, match.group(0)[:120]
    return False, None


def _extract_focus_window_block(stdout: str) -> str:
    lines = stdout.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if "mCurrentFocus=" in line:
            start = idx
            break
    if start < 0:
        return ""
    block: list[str] = []
    for line in lines[start : start + 96]:
        block.append(line)
        if len(block) > 1 and not line.strip():
            break
    return "\n".join(block)


def _probe_disconnect_ui(package: str) -> tuple[bool, str | None, str]:
    """Scan window dumpsys and optional uiautomator for disconnect/kick UI."""
    pkg = validate_package_name(package)
    blobs: list[str] = []
    full_window = ""
    try:
        win = android.run_command(["dumpsys", "window", "windows"], timeout=6)
        if win.ok and win.stdout:
            full_window = win.stdout
            focus = _extract_focus_window_block(full_window)
            if focus:
                blobs.append(focus)
            blocks: list[str] = []
            current: list[str] = []
            for line in full_window.splitlines():
                if pkg in line:
                    current.append(line)
                elif current:
                    blocks.append("\n".join(current))
                    current = []
            if current:
                blocks.append("\n".join(current))
            blobs.extend(blocks[:6])
    except Exception:  # noqa: BLE001
        pass

    for blob in blobs:
        found, text = _scan_disconnect_text(blob)
        if found:
            return True, text, "dumpsys_window"

    if full_window:
        found, text = _scan_idle_disconnect_text(full_window[:64000])
        if found:
            return True, text, "dumpsys_window_idle"

    try:
        top = android.run_command(["dumpsys", "activity", "top"], timeout=5)
        if top.ok and top.stdout:
            top_blob = top.stdout[:48000]
            if pkg in top.stdout:
                found, text = _scan_disconnect_text(top_blob)
                if found:
                    return True, text, "activity_top"
                found, text = _scan_idle_disconnect_text(top_blob)
                if found:
                    return True, text, "activity_top_idle"
            else:
                found, text = _scan_idle_disconnect_text(top_blob)
                if found:
                    return True, text, "activity_top_global_idle"
    except Exception:  # noqa: BLE001
        pass

    ui_status = "skipped"
    try:
        res = android.run_command(["uiautomator", "dump", "/dev/stdout"], timeout=6)
        if res.ok and res.stdout:
            ui_status = "ok"
            dump = res.stdout[:48000]
            found, text = _scan_disconnect_text(dump)
            if found:
                return True, text, "uiautomator"
            found, text = _scan_idle_disconnect_text(dump)
            if found:
                return True, text, "uiautomator_idle"
        else:
            ui_status = "failed"
    except Exception:  # noqa: BLE001
        ui_status = "failed"
    return False, None, ui_status


def collect_online_evidence(package: str, *, root_info: Any = None) -> OnlineEvidenceScan:
    """Collect one fresh evidence scan. Never reuses prior scan data."""
    pkg = validate_package_name(package)
    now = time.time()
    scan = OnlineEvidenceScan(package=pkg, scanned_at=now)
    scan.pid_exists, scan.pid = _pid_for_package(pkg, root_info)
    scan.force_stopped = _package_force_stopped(pkg) if scan.pid_exists else False
    resumed, top, foreground_pkg = _activity_components_from_dumpsys(pkg)
    scan.resumed_activity = resumed
    scan.top_activity = top
    scan.foreground_package = foreground_pkg
    scan.has_resumed_or_top_for_package = bool(resumed or top)

    logcat_ev = analyze_disconnect_signals(pkg)
    if logcat_ev:
        scan.logcat_disconnect_detected = True

    ui_found, ui_text, ui_status = _probe_disconnect_ui(pkg)
    scan.uiautomator_status = ui_status
    if ui_found:
        scan.disconnected_text_detected = True
        scan.matched_disconnect_text = ui_text

    if not scan.disconnected_text_detected and not scan.logcat_disconnect_detected:
        exp = detect_experience_state(pkg)
        scan.experience_level = str(exp.level.name if hasattr(exp.level, "name") else exp.level)
        if exp.level == EvidenceLevel.EXPERIENCE_LIKELY_LOADED:
            scan.in_game_evidence = True
        elif exp.level in {EvidenceLevel.ROBLOX_HOME_OR_LOBBY, EvidenceLevel.JOIN_FAILED_OR_HOME}:
            scan.home_or_lobby_only = True

        active_component = resumed or top
        if active_component and _activity_is_in_game(active_component):
            scan.in_game_evidence = True
        if active_component and _activity_is_home_only(active_component) and not scan.in_game_evidence:
            scan.home_or_lobby_only = True

    # Recents alone never proves online — flag when package only appears in recents dump.
    if not scan.has_resumed_or_top_for_package:
        try:
            rec = android.run_command(["dumpsys", "activity", "recents"], timeout=5)
            if rec.ok and pkg in rec.stdout and not scan.pid_exists:
                scan.recents_only = True
        except Exception:  # noqa: BLE001
            pass

    return scan


def evaluate_online_confirmed(scan: OnlineEvidenceScan) -> OnlineDecision:
    failed: list[str] = []
    if not scan.pid_exists:
        failed.append("no_pid")
    if scan.force_stopped:
        failed.append("force_stopped")
    if scan.disconnected_text_detected or scan.logcat_disconnect_detected:
        failed.append("disconnect_detected")
    if not scan.has_resumed_or_top_for_package:
        failed.append("no_resumed_top_activity")
    if scan.recents_only:
        failed.append("recents_only_not_online")
    if scan.home_or_lobby_only and not scan.in_game_evidence:
        failed.append("home_or_lobby_only")
    if not scan.in_game_evidence:
        failed.append("no_in_game_evidence")

    if scan.disconnected_text_detected or scan.logcat_disconnect_detected:
        return OnlineDecision(
            is_online_confirmed=False,
            reason="disconnect detected in UI or logcat",
            failed_checks=failed,
            is_disconnected=True,
        )
    if not scan.pid_exists or scan.force_stopped:
        return OnlineDecision(
            is_online_confirmed=False,
            reason="package process missing or force-stopped",
            failed_checks=failed,
            is_dead=True,
        )
    if failed:
        reason = "no fresh Roblox foreground/in-game evidence"
        if "recents_only_not_online" in failed:
            reason = "no fresh Roblox foreground/in-game evidence"
        return OnlineDecision(
            is_online_confirmed=False,
            reason=reason,
            failed_checks=failed,
        )
    return OnlineDecision(
        is_online_confirmed=True,
        reason="fresh foreground Roblox activity and no disconnect UI",
        failed_checks=[],
    )


def detect_live_disconnect(
    package: str,
    *,
    root_info: Any = None,
) -> tuple[str | None, str | None]:
    """Return (internal_reason, matched_text) when idle/disconnect UI or logcat is present."""
    from .roblox_disconnect_reasons import internal_reason_for_disconnect_code, parse_roblox_error_code

    scan = collect_online_evidence(package, root_info=root_info)
    decision = evaluate_online_confirmed(scan)
    if not decision.is_disconnected:
        return None, None
    matched = scan.matched_disconnect_text
    text = str(matched or "").lower()
    if scan.disconnected_text_detected and (
        "278" in text or "idle" in text or "being idle" in text
    ):
        return internal_reason_for_disconnect_code(278), matched
    code = parse_roblox_error_code(matched or "")
    if code is not None:
        return internal_reason_for_disconnect_code(code), matched
    if scan.disconnected_text_detected:
        return "ui_disconnect", matched
    if scan.logcat_disconnect_detected:
        try:
            from .roblox_health import analyze_disconnect_signals

            ev = analyze_disconnect_signals(package)
            if ev and ev.snippet:
                code = parse_roblox_error_code(ev.snippet)
                return internal_reason_for_disconnect_code(code), ev.snippet
        except Exception:  # noqa: BLE001
            pass
        return internal_reason_for_disconnect_code(None), matched
    return "ui_disconnect", matched


def detect_live_captcha(package: str) -> str | None:
    """Return the matched captcha-screen text when a bot-verification overlay is
    visible, else None.

    The Arkose/FunCaptcha challenge renders inside an Android WebView, so its
    text is visible to ``uiautomator dump`` / ``dumpsys window`` (unlike the
    GL-painted in-game surface).  Callers MUST treat a hit as a "let it hang"
    Captcha state, never as a recoverable disconnect (user request p-1bc476d931).
    """
    pkg = validate_package_name(package)
    blobs: list[str] = []
    try:
        win = android.run_command(["dumpsys", "window", "windows"], timeout=6)
        if win.ok and win.stdout:
            blobs.append(win.stdout[:64000])
    except Exception:  # noqa: BLE001
        pass
    try:
        top = android.run_command(["dumpsys", "activity", "top"], timeout=5)
        if top.ok and top.stdout:
            blobs.append(top.stdout[:48000])
    except Exception:  # noqa: BLE001
        pass
    try:
        res = android.run_command(["uiautomator", "dump", "/dev/stdout"], timeout=6)
        if res.ok and res.stdout:
            blobs.append(res.stdout[:48000])
    except Exception:  # noqa: BLE001
        pass
    for blob in blobs:
        found, text = _scan_captcha_text(blob)
        if found:
            return text
    return None


def capture_package_online_probe(
    package: str,
    *,
    root_info: Any = None,
    lifecycle_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured probe block for one package."""
    scan = collect_online_evidence(package, root_info=root_info)
    decision = evaluate_online_confirmed(scan)
    row = lifecycle_row or {}
    return {
        "package_state": {
            "state": row.get("state") or (
                STATE_ONLINE_CONFIRMED if decision.is_online_confirmed else STATE_LAUNCHING
            ),
            "online_since": row.get("online_since"),
            "runtime_source": "online_since",
            "last_transition_at": row.get("last_transition_at"),
            "last_transition_reason": row.get("last_transition_reason"),
            "last_online_evidence_at": row.get("last_online_evidence_at"),
            "last_offline_evidence_at": row.get("last_offline_evidence_at"),
            "package_launch_started_at": row.get("package_launch_started_at"),
        },
        "online_evidence": scan.as_probe_dict(),
        "decision": {
            "is_online_confirmed": decision.is_online_confirmed,
            "reason": decision.reason,
            "failed_checks": list(decision.failed_checks),
            "is_disconnected": decision.is_disconnected,
            "is_dead": decision.is_dead,
        },
    }
