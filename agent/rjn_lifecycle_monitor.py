"""rjn.txt-style Roblox lifecycle detection: UID logcat + process watchdog.

Source of truth for package ONLINE_CONFIRMED, disconnect, force-close, and runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from . import android
from .constants import DATA_DIR
from .roblox_disconnect_reasons import internal_reason_for_disconnect_code

WATCHED_PHRASES = (
    "gamejoinloadtime",
    "doTeleport",
    "with reason",
    "PlaceLauncher",
    "joinGameSuccess",
    "in_experience",
)
DEFAULT_LAUNCH_WATCHDOG_SECONDS = 120.0
PROCESS_MISSING_CONFIRM = 2
DISCONNECT_SCAN_INTERVAL_SECONDS = 5.0
# When the logcat stream is healthy it catches "Sending disconnect with reason"
# (e.g. 278) instantly, so the slow dumpsys/uiautomator disconnect scan only needs
# to run as an occasional safety net. While the stream is fresh for a package,
# throttle that heavy scan to this cadence instead of running it every round — this
# is the main fix for the ~8s/package, ~38s/round latency seen in probe
# p-daee3387a8 that delayed acting on other packages' disconnects/force-closes.
STREAM_FRESH_DISCONNECT_SKIP_SECONDS = float(
    os.environ.get("DENG_REJOIN_STREAM_FRESH_SKIP_SEC", "25") or "25"
)
HEAVY_DISCONNECT_FALLBACK_INTERVAL_SECONDS = float(
    os.environ.get("DENG_REJOIN_HEAVY_FALLBACK_INTERVAL_SEC", "30") or "30"
)
LAUNCH_ONLINE_FALLBACK_MIN_AGE_SECONDS = 20.0

# ── Authoritative full-dump scan (issue p-9c18ae51bc) ────────────────────────
# The live "logcat -v uid" stream catches the disconnect line instantly *while it
# is flowing*, but on a chatty device the reader can stall over a long run, and
# the small periodic poll (-t 400) gets buried in GC/shader spam, so a real idle
# kick ("Sending disconnect with reason: 278") was detected by a fresh probe yet
# the live recovery never fired. To make detection robust ("use dump logcat for
# full potential"), every round we also run a wide, PID-scoped `logcat -d` dump
# and parse the most-recent disconnect-reason / idle / join-identity lines
# directly, with device-local timestamps so we only act on events newer than the
# last online proof. This is independent of the stream, so a stalled reader can
# no longer swallow a disconnect, and it re-asserts each round so the watchdog
# reliably acts. PID-scoped dumps are cheap (<1s) unlike the dumpsys/uiautomator
# UI scan, so this does not regress the round latency fixed earlier.
DUMP_SCAN_MIN_INTERVAL_SECONDS = float(os.environ.get("DENG_REJOIN_DUMP_SCAN_INTERVAL", "3.0"))
DUMP_TAIL_LINES = int(os.environ.get("DENG_REJOIN_DUMP_TAIL_LINES", "4000"))
DUMP_MAX_PIDS = 4
DUMP_SCAN_ENABLED = os.environ.get("DENG_REJOIN_DISABLE_DUMP_SCAN", "").strip() not in {"1", "true", "yes"}

# ── In-game Lua detector logcat heartbeat-loss (issue p-af27350e40) ───────────
# detector.lua prints a "DENGRJN_HB|..." line to logcat every ~2s from inside a
# live server.  The PID-scoped dump above reads it as reliably as it reads the
# "online" join line (proven on cloud-phone clones, where the loopback HTTP port
# is sandboxed).  When that heartbeat goes SILENT while the process is still
# alive, the client has left the live server — a kick, an error code, a captcha,
# or a freeze whose GL/WebView dialog dumpsys/uiautomator cannot read.  We demote
# to Disconnected after this grace so recovery fires within ~10-13s, matching the
# online detection speed the user asked us to match for every other scenario.
INGAME_HB_LOSS_SECONDS = float(os.environ.get("DENG_REJOIN_INGAME_HB_LOSS_SEC", "15") or "15")
INGAME_HB_LOSS_ENABLED = os.environ.get("DENG_REJOIN_DISABLE_INGAME_HB_LOSS", "").strip() not in {
    "1",
    "true",
    "yes",
}
# Suppress heartbeat-loss while the device is in (or just out of) a launch storm.
# While a clone is loading, Android CPU-throttles the OTHER already-online
# background clones, stretching their in-game heartbeat far past the 2s interval.
# Demoting then is a FALSE positive (the package is fine, just starved), which the
# user reported as "2nd-to-last package killed while only loading" — and that
# false mass-dead is what storms recovery into force-stopping every clone +
# Termux (the critical kill-all bug).  We hold loss detection for this long after
# the most recent launch/relaunch of ANY package, then arm it for real kicks.
INGAME_HB_LOSS_LAUNCH_QUIET_SECONDS = float(
    os.environ.get("DENG_REJOIN_INGAME_HB_LOSS_LAUNCH_QUIET_SEC", "30") or "30"
)
# Parses the heartbeat payload: DENGRJN_HB|placeId|rootPlaceId|universeId|jobId|alive
_INGAME_HB_RE = re.compile(
    r"DENGRJN_HB\|(\d*)\|(\d*)\|(\d*)\|([^|\s]*)\|([01])"
)
WRONG_SERVER_ANCHOR_ENABLED = os.environ.get("DENG_REJOIN_WRONG_SERVER_ANCHOR", "1").strip() in {"1", "true", "yes"}

_UID_OPTIONAL_ONLINE_SOURCES = frozenset({
    "presence_in_experience",
    "activity_in_game",
    "online_evidence",
    "logcat_join_hint",
    # The in-game Lua detector only posts AFTER game.Loaded from inside a live
    # server, so it is definitive proof of being in-game even on a clone whose
    # device UID never resolved for log attribution.  Requiring a UID here was
    # silently keeping such clones from ever flipping Online (p-5d0df79c33).
    "push_heartbeat",
})

STATE_STOPPED = "STOPPED"
STATE_LAUNCHING = "LAUNCHING"
STATE_TELEPORTING = "TELEPORTING"
STATE_ONLINE_CONFIRMED = "ONLINE_CONFIRMED"
STATE_DISCONNECTED = "DISCONNECTED"
STATE_DEAD = "DEAD"
STATE_RELAUNCHING = "RELAUNCHING"
STATE_FAILED = "FAILED"

_ACTIVE_MONITOR_STATES = frozenset({
    STATE_LAUNCHING,
    STATE_RELAUNCHING,
    STATE_ONLINE_CONFIRMED,
    STATE_TELEPORTING,
    STATE_DISCONNECTED,
    STATE_FAILED,
})

_UID_RE = re.compile(r"userId=(\d+)")
_LOGCAT_HEADER_RE = re.compile(
    r"^\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}\s+(\d+)\s+(\d+)\s+(\d+)\s"
)
_LOGCAT_UID_RE = re.compile(r"uid=(\d+)")
_GAME_JOIN_RE = re.compile(r"gamejoinloadtime", re.I)
_DO_TELEPORT_RE = re.compile(r"doTeleport", re.I)
_WITH_REASON_RE = re.compile(r"with reason", re.I)
# Authoritative Roblox network disconnect line: "Sending disconnect with reason: <code>".
_DISCONNECT_REASON_CODE_RE = re.compile(r"with\s+reason:?\s*(\d+)", re.I)
_IDLE_DISCONNECT_RE = re.compile(
    r"(disconnected for being idle|Error Code:\s*278|idle\s+\d+\s+minutes|You were disconnected.*idle)",
    re.I,
)
# ANY Roblox error code is a real disconnect, not just 278 (user request
# p-1bc476d931): e.g. "Error Code: 529" (HTTP error), 524 (timeout), 517.
_ERROR_CODE_LINE_RE = re.compile(r"Error\s*Code:?\s*(\d+)", re.I)
# Observed joined-server identity extracted from Roblox join logcat lines. placeIds
# are public (not secret), so they are safe to compare/store. The negative
# lookbehind keeps the generic placeId pattern from matching inside "rootPlaceId".
_ROOT_PLACE_ID_RE = re.compile(r"root\s*place\s*id[\"']?\s*[:=]\s*\"?(\d{3,})", re.I)
_PLACE_ID_RE = re.compile(r"(?<![a-z])place\s*id[\"']?\s*[:=]\s*\"?(\d{3,})", re.I)
_UNIVERSE_ID_RE = re.compile(r"universe\s*id[\"']?\s*[:=]\s*\"?(\d{3,})", re.I)
# Lines worth scanning for a placeId (join/launch only) — avoids matching random ids.
_JOIN_IDENTITY_HINT_RE = re.compile(
    r"(placeId|rootPlaceId|universeId|jobId|gameId|GameJoin|PlaceLauncher|JoinGame|joinScript|"
    r"ActivityProtocolLaunch|share_links|robloxApp://|roblox://)",
    re.I,
)
# Private-server / share / deep-link code (used for Wrong-Server when the
# configured link only carries an opaque code, no placeId). Compared as a salted
# hash only — the raw code is NEVER stored or uploaded.
_PRIVATE_CODE_RE = re.compile(
    r"(?:privateServerLinkCode|linkCode|accessCode|gameInstanceId|[?&]code)=([A-Za-z0-9_\-]{4,})",
    re.I,
)
# Joined server-instance id (jobId / gameId) — a standard UUID in Roblox join
# logcat ("gameId:<guid>", "jobId=<guid>"). This is the server instance the
# client actually joined; it changes when the player moves to a different server
# of the same place. Compared as a salted hash only.
_JOB_ID_RE = re.compile(
    r"(?:jobId|gameId|game_id|serverId)[\"']?\s*[:=]\s*\"?"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
    re.I,
)
# When set, a change of joined server instance (jobId) away from the first
# server of the session is treated as Wrong Server for private-server configs.
WRONG_SERVER_JOBID_ENABLED = os.environ.get("DENG_REJOIN_WRONG_SERVER_JOBID", "1").strip() in {"1", "true", "yes"}
# logcat threadtime prefix: "MM-DD HH:MM:SS.mmm". Used to order dumped lines
# against wall-clock online evidence (the agent runs on the same device, so
# logcat local time and time.time() share the same clock).
_LOGCAT_TS_PREFIX_RE = re.compile(
    r"^(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})"
)


def _hash_code(code: object) -> str:
    text = str(code or "").strip()
    if not text:
        return ""
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:16]
_POSITIVE_ONLINE_RES: list[tuple[str, re.Pattern[str]]] = [
    ("gamejoinloadtime", re.compile(r"gamejoinloadtime", re.I)),
    (
        "logcat_place_launcher_join",
        re.compile(r"\bPlaceLauncher\b.*\b(join|joined|connected)\b", re.I),
    ),
    ("logcat_join_game_success", re.compile(r"\bjoinGameSuccess\b", re.I)),
    (
        "logcat_joined_experience",
        re.compile(r"\bJoined\s+(game|experience|place)\b", re.I),
    ),
    ("logcat_in_experience", re.compile(r"\bin[_ ]experience\b", re.I)),
    ("logcat_game_loaded", re.compile(r"\bGame\s+loaded\b", re.I)),
    (
        "logcat_experience_started",
        re.compile(r"\bExperience\s+started\b", re.I),
    ),
]


@dataclass
class UidResolution:
    package: str
    uid: str | None = None
    resolved_at: float = 0.0
    command_output_sample: str = ""
    error: str | None = None


@dataclass
class LogcatEvent:
    package: str
    uid: str
    phrase: str
    raw_line_sanitized: str
    seen_at: float
    action_taken: str = ""


@dataclass
class PackageRjnState:
    package: str
    uid: str = ""
    uid_error: str = ""
    internal_state: str = STATE_STOPPED
    online_since: float = 0.0
    runtime_source: str = ""
    launch_started_at: float = 0.0
    watchdog_active: bool = False
    launch_failed_reason: str = ""
    error_count: int = 0
    last_gamejoinloadtime_at: float = 0.0
    last_doteleport_at: float = 0.0
    last_with_reason_at: float = 0.0
    last_logcat_event_at: float = 0.0
    last_process_check_at: float = 0.0
    process_exists: bool = False
    pids: list[str] = field(default_factory=list)
    relaunching: bool = False
    force_close_detected: bool = False
    process_missing_streak: int = 0
    last_transition_at: float = 0.0
    last_transition_reason: str = ""
    last_online_evidence_at: float = 0.0
    last_positive_online_evidence_at: float = 0.0
    online_evidence_source: str = ""
    last_offline_evidence_at: float = 0.0
    last_dead_detected_at: float = 0.0
    last_dead_reason: str = ""
    last_disconnect_scan_at: float = 0.0
    disconnect_prompt_text: str = ""
    # Last numeric Roblox disconnect reason code parsed from the FLog::Network line
    # ("Sending disconnect with reason: <code>"). 0 = none seen.
    last_disconnect_code: int = 0
    # Wrong-server detection: configured ("expected") target derived from the
    # private-server/game URL vs the server the client actually joined ("observed",
    # parsed from join logcat). placeIds are public, so safe to store/compare.
    expected_place_id: int = 0
    expected_root_place_id: int = 0
    expected_universe_id: int = 0
    expected_private_code_hash: str = ""
    expected_share_type: str = ""
    observed_place_id: int = 0
    observed_root_place_id: int = 0
    observed_universe_id: int = 0
    observed_private_code_hash: str = ""
    # Joined server-instance identity (Roblox jobId / gameId GUID, the value
    # TeleportService#GetPlayerPlaceInstanceAsync returns as the instance id).
    # Stored only as a salted hash. Used to catch "same game, different server"
    # (user changed server) which placeId/universeId alone cannot see.
    observed_job_id_hash: str = ""
    anchor_job_id_hash: str = ""
    # Session anchor for the no-config / share-code-only case: the FIRST game
    # identity the client joins after a launch is treated as the configured
    # target, so a later join to a DIFFERENT game (user moved to another link)
    # can be flagged as Wrong Server even when the configured URL exposes no
    # placeId. Reset on every (re)launch so each session re-anchors cleanly.
    anchor_place_id: int = 0
    anchor_root_place_id: int = 0
    anchor_universe_id: int = 0
    anchor_set: bool = False
    last_wrong_server_at: float = 0.0
    # Latches once a provable server/game mismatch is observed.  Keeps the
    # package in DISCONNECTED even when a later (unchanged) heartbeat for the
    # SAME wrong server would otherwise re-confirm it Online — that flip/flop
    # was why a moved package never stayed flagged (p-5d0df79c33).
    wrong_server_active: bool = False
    # In-game Lua detector (detector.lua) logcat heartbeat bookkeeping. Once a
    # heartbeat has EVER been seen for this session the package is enrolled in
    # heartbeat-loss detection: if the printed "DENGRJN_HB|" line then stops
    # while the process is alive, the client left the live server (kick / error
    # code / captcha / freeze) and we demote → Disconnected. Reset every launch.
    last_ingame_hb_at: float = 0.0
    ingame_hb_ever: bool = False
    # Authoritative full-dump scan bookkeeping.
    last_dump_scan_at: float = 0.0
    last_dump_disconnect_epoch: float = 0.0
    # Diagnostic: freshness + sample of EVERY UID-matched logcat line (not just
    # watched phrases). A GL-rendered disconnect (e.g. Error 278) is invisible to
    # the UI scan and may not match any watched phrase, but the process keeps
    # emitting (or stops emitting) UID-tagged lines. Tracking the last line time
    # exposes "online but logcat-silent", and the ring buffer lets a probe reveal
    # what Roblox actually logs around the disconnect so detection can be fixed.
    last_uid_line_at: float = 0.0
    recent_uid_lines: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PackageEvaluateResult:
    package: str
    internal_state: str
    public_status: str
    reason: str
    is_online_confirmed: bool
    failed_checks: list[str]
    process_exists: bool
    detail: dict[str, Any]


def _sanitize_line(line: str) -> str:
    text = str(line or "")[:240]
    text = re.sub(r"(?i)ROBLOSECURITY[^\s]*", "<masked>", text)
    # Never leak private-server access codes / share links in a diagnostic upload.
    text = re.sub(
        r"(?i)(share\?code=|accessCode=|linkCode=|privateServerLinkCode=|code=)[^\s&\"']+",
        r"\1<masked>",
        text,
    )
    text = re.sub(r"https?://[^\s\"']+", "<url>", text)
    return text


def _package_force_stopped_quick(package: str) -> bool:
    try:
        from .package_online_evidence import _package_force_stopped

        return bool(_package_force_stopped(package))
    except Exception:  # noqa: BLE001
        return False


def resolve_package_uid(package: str) -> UidResolution:
    pkg = android.validate_package_name(package)
    now = time.time()
    try:
        result = android.run_android_command(
            ["dumpsys", "package", pkg],
            timeout=6,
            prefer_root=True,
        )
        text = result.stdout if result.ok else (result.stderr or "")
        match = _UID_RE.search(text)
        if match:
            return UidResolution(
                package=pkg,
                uid=match.group(1),
                resolved_at=now,
                command_output_sample=text[:400],
            )
        try:
            from .android_logcat_detector import package_uid_map

            mapped = package_uid_map([pkg]).get(pkg)
            if mapped:
                return UidResolution(
                    package=pkg,
                    uid=str(mapped),
                    resolved_at=now,
                    command_output_sample=text[:400],
                )
        except Exception:  # noqa: BLE001
            pass
        return UidResolution(
            package=pkg,
            uid=None,
            resolved_at=now,
            command_output_sample=text[:400],
            error="userId not found in dumpsys package",
        )
    except Exception as exc:  # noqa: BLE001
        return UidResolution(
            package=pkg,
            uid=None,
            resolved_at=now,
            error=str(exc)[:160],
        )


class RjnLifecycleMonitor:
    """Per-package lifecycle from UID-filtered logcat + process watchdog."""

    def __init__(
        self,
        packages: list[str],
        *,
        root_info: Any = None,
        stop_event: threading.Event | None = None,
        launch_watchdog_seconds: float = DEFAULT_LAUNCH_WATCHDOG_SECONDS,
    ) -> None:
        self.packages = [str(p).strip() for p in packages if str(p).strip()]
        self._root_info = root_info
        self._stop_event = stop_event or threading.Event()
        self._launch_watchdog_seconds = float(launch_watchdog_seconds)
        self._lock = threading.RLock()
        self._states: dict[str, PackageRjnState] = {
            pkg: PackageRjnState(package=pkg) for pkg in self.packages
        }
        self._uid_map: dict[str, str] = {}
        self._uid_to_package: dict[str, str] = {}
        self._pid_map: dict[str, str] = {}
        self._pid_to_package: dict[str, str] = {}
        self._last_pid_refresh_at: float = 0.0
        # Wall-clock of the most recent launch/relaunch of ANY package. Drives the
        # global heartbeat-loss launch-quiet window so a launch storm (which
        # CPU-throttles every online clone's heartbeat) cannot false-demote them.
        self._last_any_launch_at: float = 0.0
        self._uid_resolutions: dict[str, UidResolution] = {}
        self._recent_events: list[LogcatEvent] = []
        self._monitor_started_at: float = 0.0
        self._logcat_cleared_at: float = 0.0
        self._logcat_started_at: float = 0.0
        self._logcat_stream_alive: bool = False
        self._logcat_error: str = ""
        self._logcat_thread: threading.Thread | None = None
        self._logcat_proc: subprocess.Popen[str] | None = None
        self._logcat_pid: int = 0
        self._logcat_last_line_at: float = 0.0
        self._logcat_last_uid_matched_at: float = 0.0
        self._ignored_uid_lines: list[dict[str, Any]] = []
        self._detector_errors: list[str] = []
        self._session_started: bool = False
        self._last_logcat_poll_at: float = 0.0

    def refresh_uid_map(self) -> dict[str, str]:
        with self._lock:
            for pkg in self.packages:
                res = resolve_package_uid(pkg)
                self._uid_resolutions[pkg] = res
                row = self._states.setdefault(pkg, PackageRjnState(package=pkg))
                if res.uid:
                    self._uid_map[pkg] = res.uid
                    self._uid_to_package[res.uid] = pkg
                    row.uid = res.uid
                    row.uid_error = ""
                else:
                    row.uid = ""
                    row.uid_error = res.error or "uid_unresolved"
            return dict(self._uid_map)

    def refresh_pid_map(self) -> dict[str, str]:
        with self._lock:
            from .android_logcat_detector import package_pid_map

            self._pid_map = package_pid_map(self.packages)
            self._pid_to_package = {
                pid: pkg for pkg, pid in self._pid_map.items() if pid
            }
            for pkg, row in self._states.items():
                _exists, pids = self._process_check(pkg)
                for pid in pids:
                    self._pid_map[pkg] = pid
                    self._pid_to_package[pid] = pkg
            self._last_pid_refresh_at = time.time()
            return dict(self._pid_map)

    def clear_logcat(self) -> bool:
        try:
            res = android.run_command(["logcat", "-c"], timeout=6)
            self._logcat_cleared_at = time.time()
            return res.ok
        except Exception as exc:  # noqa: BLE001
            self._logcat_error = str(exc)[:160]
            return False

    def start_session(self) -> None:
        """Detection-only session start: clear logcat, build UID map, start reader."""
        with self._lock:
            if self._session_started:
                return
            self._session_started = True
            self._monitor_started_at = time.time()
            self.clear_logcat()
            self.refresh_uid_map()
            self.refresh_pid_map()
            self._start_logcat_thread()

    def stop_session(self) -> None:
        self._stop_event.set()
        proc = self._logcat_proc
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        self._logcat_stream_alive = False

    def _start_logcat_thread(self) -> None:
        if self._logcat_thread and self._logcat_thread.is_alive():
            return
        self._logcat_started_at = time.time()
        self._logcat_thread = threading.Thread(
            target=self._logcat_reader_loop,
            name="rjn-logcat-uid",
            daemon=True,
        )
        self._logcat_thread.start()

    def _ensure_logcat_stream(self) -> None:
        """Restart logcat reader only — never Termux or the main monitor process."""
        with self._lock:
            alive = bool(
                self._logcat_thread
                and self._logcat_thread.is_alive()
                and self._logcat_stream_alive
            )
            if alive:
                return
            if self._logcat_proc is not None:
                try:
                    self._logcat_proc.kill()
                except OSError:
                    pass
                self._logcat_proc = None
            self._logcat_stream_alive = False
            self._start_logcat_thread()

    def _logcat_reader_loop(self) -> None:
        try:
            # ``errors="replace"`` is CRITICAL: Roblox/Android log lines regularly
            # contain non-UTF-8 bytes (player-name/emoji fragments, e.g. 0xc0).
            # Without it, strict decoding raised UnicodeDecodeError, which killed
            # this whole reader thread and silently degraded detection to the slow
            # fallbacks — leaving force-closes / disconnects undetected for a very
            # long time (probe p-87b567bde8 #2). Now a bad byte is just replaced.
            proc = subprocess.Popen(
                ["logcat", "-v", "uid"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            self._logcat_proc = proc
            self._logcat_pid = int(proc.pid or 0)
            self._logcat_stream_alive = True
            while not self._stop_event.is_set():
                if proc.stdout is None:
                    break
                # A single malformed line must NEVER tear down the stream: catch
                # per-line read/parse errors and keep going so the fast detector
                # stays alive 24/7 (the whole point of the heartbeat system).
                try:
                    line = proc.stdout.readline()
                    if not line:
                        if proc.poll() is not None:
                            break
                        time.sleep(0.05)
                        continue
                    self._logcat_last_line_at = time.time()
                    self._handle_logcat_line(line.strip())
                except Exception as line_exc:  # noqa: BLE001
                    self._detector_errors.append(f"logcat_line:{line_exc}"[:120])
                    if len(self._detector_errors) > 16:
                        self._detector_errors = self._detector_errors[-16:]
                    if proc.poll() is not None:
                        break
                    continue
        except Exception as exc:  # noqa: BLE001
            self._logcat_error = str(exc)[:160]
            self._detector_errors.append(self._logcat_error)
            if len(self._detector_errors) > 16:
                self._detector_errors = self._detector_errors[-16:]
        finally:
            self._logcat_stream_alive = False
            self._logcat_pid = 0

    def _uid_for_line(self, line: str) -> str | None:
        match = _LOGCAT_HEADER_RE.match(line.strip())
        if match:
            return match.group(1)
        match = _LOGCAT_UID_RE.search(line)
        return match.group(1) if match else None

    def _pid_for_line(self, line: str) -> str | None:
        match = _LOGCAT_HEADER_RE.match(line.strip())
        return match.group(2) if match else None

    def _package_for_line(self, line: str, uid: str | None) -> str | None:
        if uid:
            pkg = self._uid_to_package.get(uid)
            if pkg:
                return pkg
        pid = self._pid_for_line(line)
        if pid:
            pkg = self._pid_to_package.get(pid)
            if pkg:
                return pkg
        matches = [pkg for pkg in self.packages if pkg and pkg in line]
        if len(matches) == 1:
            return matches[0]
        return None

    def _match_positive_online(self, line: str) -> str | None:
        for source, pattern in _POSITIVE_ONLINE_RES:
            if pattern.search(line):
                return source
        return None

    def _poll_recent_logcat(self) -> None:
        """Backfill join/disconnect hints from recent logcat when stream misses lines."""
        now = time.time()
        if now - self._last_logcat_poll_at < 5.0:
            return
        self._last_logcat_poll_at = now
        if now - self._last_pid_refresh_at >= 8.0:
            self.refresh_pid_map()
        try:
            from .android_logcat_detector import poll_logcat_events

            events, _state = poll_logcat_events(
                self.packages,
                uid_map=dict(self._uid_map),
                pid_map=dict(self._pid_map),
                max_lines=400,
            )
        except Exception as exc:  # noqa: BLE001
            self._detector_errors.append(f"logcat_poll:{exc}"[:120])
            return
        for event in events:
            if event.event == "package_logcat_ingame_hb":
                # In-game Lua detector heartbeat printed to logcat — clone-safe
                # online proof + identity (and resets the heartbeat-loss grace).
                # Use the line's DEVICE timestamp (not poll wall-clock) and only
                # act on a newer beat: this poll re-reads the tail every cycle, so
                # a stale buffered heartbeat must not refresh the loss grace after
                # the script has actually stopped.
                ep = self._logcat_line_epoch(event.line, event.at)
                row = self._states.get(event.package)
                if row is None or ep > (row.last_ingame_hb_at + 0.5):
                    self._ingest_logcat_heartbeat(event.package, event.line, ep)
            elif event.event == "package_logcat_game_join_loaded":
                self._confirm_online_evidence(
                    event.package,
                    event.at,
                    source="gamejoinloadtime",
                )
            elif event.event == "package_logcat_join_hint":
                self._confirm_online_evidence(
                    event.package,
                    event.at,
                    source="logcat_join_hint",
                )
            elif event.event == "package_logcat_reason":
                self._apply_phrase(
                    event.package,
                    "with reason",
                    event.at,
                    LogcatEvent(
                        package=event.package,
                        uid=self._uid_map.get(event.package, ""),
                        phrase="with reason",
                        raw_line_sanitized=_sanitize_line(event.line),
                        seen_at=event.at,
                    ),
                )
            elif event.event == "package_logcat_idle_disconnect":
                self._apply_phrase(
                    event.package,
                    "idle_disconnect_278",
                    event.at,
                    LogcatEvent(
                        package=event.package,
                        uid=self._uid_map.get(event.package, ""),
                        phrase="idle_disconnect_278",
                        raw_line_sanitized=_sanitize_line(event.line),
                        seen_at=event.at,
                    ),
                )
            elif event.event == "package_process_missing":
                row = self._states.get(event.package)
                if row and self._was_ever_online_confirmed(row):
                    row.process_missing_streak = PROCESS_MISSING_CONFIRM
                    row.force_close_detected = True
                    self._transition(
                        event.package,
                        STATE_DEAD,
                        "process_missing",
                        at=event.at,
                        offline=True,
                    )
            elif event.event == "package_logcat_teleport":
                self._apply_phrase(
                    event.package,
                    "doTeleport",
                    event.at,
                    LogcatEvent(
                        package=event.package,
                        uid=self._uid_map.get(event.package, ""),
                        phrase="doTeleport",
                        raw_line_sanitized=_sanitize_line(event.line),
                        seen_at=event.at,
                    ),
                )

    def _handle_logcat_line(self, line: str) -> None:
        if not line:
            return
        seen_at = time.time()
        if self._monitor_started_at and seen_at < self._monitor_started_at:
            return
        uid = self._uid_for_line(line)
        with self._lock:
            pkg = self._package_for_line(line, uid)
            if not pkg:
                if uid:
                    self._ignored_uid_lines.append({
                        "uid": uid,
                        "line": _sanitize_line(line),
                        "at": seen_at,
                        "reason": "uid_not_mapped",
                    })
                    if len(self._ignored_uid_lines) > 32:
                        self._ignored_uid_lines = self._ignored_uid_lines[-32:]
                return
            if uid:
                self._logcat_last_uid_matched_at = seen_at
            effective_uid = uid or self._uid_map.get(pkg) or ""
            # Diagnostic-only: record freshness + a small sample of EVERY line we
            # resolved to this package, even ones that match no watched phrase.
            _diag_row = self._states.get(pkg)
            if _diag_row is not None:
                _diag_row.last_uid_line_at = seen_at
                _diag_row.recent_uid_lines.append({"at": seen_at, "line": _sanitize_line(line)})
                if len(_diag_row.recent_uid_lines) > 14:
                    del _diag_row.recent_uid_lines[:-14]
            # Capture the joined-server identity (public placeIds only) from join
            # logcat so it can be compared against the configured target ("Wrong
            # Server"). Runs on the RAW line; only non-secret numeric ids are kept.
            if _JOIN_IDENTITY_HINT_RE.search(line):
                if self._capture_observed_identity(pkg, line, seen_at):
                    # Wrong server flagged from this very line — do not let the same
                    # join line re-confirm ONLINE below.
                    wrong_event = LogcatEvent(
                        package=pkg,
                        uid=effective_uid,
                        phrase="wrong_server",
                        raw_line_sanitized=_sanitize_line(line),
                        seen_at=seen_at,
                    )
                    wrong_event.action_taken = self._states[pkg].internal_state
                    self._recent_events.append(wrong_event)
                    if len(self._recent_events) > 128:
                        self._recent_events = self._recent_events[-128:]
                    return
            phrase = ""
            if _WITH_REASON_RE.search(line):
                phrase = "with reason"
            elif _IDLE_DISCONNECT_RE.search(line):
                phrase = "idle_disconnect_278"
            elif _ERROR_CODE_LINE_RE.search(line):
                # Any other "Error Code: N" line (e.g. 529 HTTP error) is a real
                # disconnect → route through the with-reason path which now also
                # parses the Error-Code form.
                phrase = "with reason"
            elif _DO_TELEPORT_RE.search(line):
                phrase = "doTeleport"
            else:
                positive = self._match_positive_online(line)
                if positive:
                    phrase = positive
                elif any(
                    hint in line.lower()
                    for hint in ("placelauncher", "joingame", "in_experience", "game loaded")
                ):
                    phrase = "logcat_join_hint"
                else:
                    return

            event = LogcatEvent(
                package=pkg,
                uid=effective_uid,
                phrase=phrase,
                raw_line_sanitized=_sanitize_line(line),
                seen_at=seen_at,
            )
            if phrase == "with reason":
                self._apply_phrase(pkg, phrase, seen_at, event)
            elif phrase == "idle_disconnect_278":
                self._apply_phrase(pkg, phrase, seen_at, event)
            elif phrase == "doTeleport":
                self._apply_phrase(pkg, phrase, seen_at, event)
            else:
                self._confirm_online_evidence(pkg, seen_at, source=phrase, event=event)
            event.action_taken = self._states[pkg].internal_state
            self._recent_events.append(event)
            if len(self._recent_events) > 128:
                self._recent_events = self._recent_events[-128:]

    def _transition(
        self,
        pkg: str,
        new_state: str,
        reason: str,
        *,
        at: float,
        offline: bool = False,
    ) -> None:
        row = self._states[pkg]
        row.internal_state = new_state
        row.last_transition_at = at
        row.last_transition_reason = reason
        if new_state in {STATE_DEAD, STATE_DISCONNECTED, STATE_FAILED}:
            row.last_dead_detected_at = at
            row.last_dead_reason = reason
            row.last_positive_online_evidence_at = 0.0
            row.last_gamejoinloadtime_at = 0.0
            row.online_evidence_source = ""
            row.watchdog_active = False
        if offline:
            row.online_since = 0.0
            row.runtime_source = ""
            row.last_offline_evidence_at = at
            from .status_monitor_runtime import clear_online_since, record_lifecycle_transition

            clear_online_since(pkg)
            record_lifecycle_transition(pkg, new_state, reason, now=at, offline=True)
        else:
            from .status_monitor_runtime import record_lifecycle_transition

            record_lifecycle_transition(pkg, new_state, reason, now=at)

    def _confirm_online_evidence(
        self,
        pkg: str,
        at: float,
        *,
        source: str,
        event: LogcatEvent | None = None,
    ) -> None:
        row = self._states[pkg]
        source_norm = str(source or "").strip()
        # ``gamejoinloadtime`` (logcat join marker) and ``push_heartbeat`` (the
        # in-game Lua detector, which only fires AFTER game.Loaded) are both
        # definitive proof the client is in a live server — they are NOT subject
        # to the launch-window debounce that protects the slow scrape fallbacks
        # from confirming online prematurely.  Gating push behind the 20s window
        # was the main cause of "relaunching → online 5 minutes" (p-5d0df79c33).
        definitive = source_norm in {"gamejoinloadtime", "push_heartbeat"}
        if row.launch_started_at > 0 and not definitive:
            if at < row.launch_started_at:
                return
            if (at - row.launch_started_at) < LAUNCH_ONLINE_FALLBACK_MIN_AGE_SECONDS:
                return
        row.last_logcat_event_at = at
        row.watchdog_active = False
        row.launch_failed_reason = ""
        prev = row.internal_state
        if prev != STATE_ONLINE_CONFIRMED:
            row.online_since = at
        row.runtime_source = source
        row.last_online_evidence_at = at
        row.last_positive_online_evidence_at = at
        row.online_evidence_source = source
        row.internal_state = STATE_ONLINE_CONFIRMED
        row.last_transition_at = at
        row.last_transition_reason = source
        row.process_missing_streak = 0
        row.force_close_detected = False
        row.relaunching = False
        if source == "gamejoinloadtime":
            row.last_gamejoinloadtime_at = at
            from .status_monitor_runtime import mark_online_confirmed_gamejoin

            mark_online_confirmed_gamejoin(pkg, at, previous_state=prev)
        else:
            from .status_monitor_runtime import mark_online_confirmed_evidence

            mark_online_confirmed_evidence(pkg, at, source=source, previous_state=prev)
        if event is not None:
            event.action_taken = "ONLINE_CONFIRMED"

    def confirm_online_evidence(
        self,
        package: str,
        at: float,
        *,
        source: str,
    ) -> None:
        """External online proof (e.g. Roblox Presence in_experience)."""
        pkg = str(package or "").strip()
        if not pkg:
            return
        with self._lock:
            self._confirm_online_evidence(pkg, float(at), source=str(source or "online_evidence"))

    def apply_disconnect(
        self,
        package: str,
        at: float,
        *,
        reason: str,
        matched_text: str | None = None,
    ) -> None:
        """External disconnect proof (idle UI, logcat, etc.)."""
        pkg = str(package or "").strip()
        internal_reason = str(reason or "ui_disconnect").strip() or "ui_disconnect"
        if not pkg:
            return
        with self._lock:
            self._states.setdefault(pkg, PackageRjnState(package=pkg))
            row = self._states[pkg]
            row.last_with_reason_at = float(at)
            prompt = str(matched_text or "").strip()
            if prompt:
                row.disconnect_prompt_text = prompt[:240]
            self._transition(
                pkg,
                STATE_DISCONNECTED,
                internal_reason,
                at=float(at),
                offline=True,
            )

    def _was_ever_online_confirmed(self, row: PackageRjnState) -> bool:
        # Include the in-game Lua heartbeat path: on cloud-phone clones the live
        # server is proven by the logcat "DENGRJN_HB|" beat (``ingame_hb_ever``)
        # and ``online_since``, NOT only by the slow-scrape positive-evidence
        # timestamp.  Counting only the latter let a force-closed heartbeat-online
        # clone evade the process-missing kill path (probe p-87b567bde8 #2).
        return (
            row.last_positive_online_evidence_at > 0
            or row.ingame_hb_ever
            or row.online_since > 0
        )

    @property
    def logcat_stream_alive(self) -> bool:
        return bool(getattr(self, "_logcat_stream_alive", False))

    def stream_fresh_for(self, package: str, max_age_seconds: float) -> bool:
        """True when the logcat stream is alive AND recently emitted a *watched*
        phrase for this package (join/disconnect/teleport — not perfdata spam).

        Using ``last_uid_line_at`` alone was wrong: rbx.perfdata / SessionL2 lines
        keep the stream looking fresh while the client is sitting on a disconnect
        dialog with no new join line, which suppressed the UI/logcat fallback scan
        and blocked recovery (probe p-50c74b40d4)."""
        if not getattr(self, "_logcat_stream_alive", False):
            return False
        pkg = str(package or "").strip()
        with self._lock:
            row = self._states.get(pkg)
            if row is None:
                return False
            watched_at = max(
                float(row.last_logcat_event_at or 0.0),
                float(row.last_with_reason_at or 0.0),
                float(row.last_gamejoinloadtime_at or 0.0),
            )
            if watched_at <= 0:
                return False
            return (time.time() - watched_at) <= float(max_age_seconds)

    def set_expected_target(
        self,
        package: str,
        *,
        place_id: object = None,
        root_place_id: object = None,
        universe_id: object = None,
        private_code: object = None,
        share_type: object = None,
    ) -> None:
        """Record the configured ("expected") server identity for Wrong-Server
        detection. Only public placeIds and a *salted hash* of the private/share
        code are stored — the raw share/private code is never kept or uploaded. A
        0/None/"" value means "unknown" and disables comparison for that field
        (fail-safe: never flags Wrong Server without a known expectation)."""
        pkg = str(package or "").strip()
        if not pkg:
            return

        def _as_id(value: object) -> int:
            try:
                ival = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return 0
            return ival if ival > 0 else 0

        with self._lock:
            row = self._states.setdefault(pkg, PackageRjnState(package=pkg))
            row.expected_place_id = _as_id(place_id)
            row.expected_root_place_id = _as_id(root_place_id)
            row.expected_universe_id = _as_id(universe_id)
            row.expected_private_code_hash = _hash_code(private_code)
            row.expected_share_type = str(share_type or "").strip()[:64]

    def set_observed_from_presence(self, package: str, presence: Any, at: float | None = None) -> None:
        """Feed Roblox Presence API place/universe ids as observed join identity."""
        pkg = str(package or "").strip()
        if not pkg or presence is None:
            return
        seen_at = float(at or time.time())
        with self._lock:
            row = self._states.setdefault(pkg, PackageRjnState(package=pkg))
            changed = False
            for attr, val in (
                ("observed_place_id", getattr(presence, "place_id", None)),
                ("observed_root_place_id", getattr(presence, "root_place_id", None)),
                ("observed_universe_id", getattr(presence, "universe_id", None)),
            ):
                try:
                    ival = int(val) if val not in (None, "", 0) else 0
                except (TypeError, ValueError):
                    ival = 0
                if ival > 0 and getattr(row, attr) != ival:
                    setattr(row, attr, ival)
                    changed = True
            if changed:
                self._maybe_flag_wrong_server(pkg, seen_at)

    def ingest_push_heartbeat(
        self,
        package: str,
        *,
        alive: bool,
        place_id: object = 0,
        root_place_id: object = 0,
        universe_id: object = 0,
        job_id: object = "",
        at: float | None = None,
    ) -> str:
        """Feed an in-game Lua push heartbeat (loopback detection worker).

        A fresh heartbeat is the strongest possible proof the client is really
        in a live server *and* tells us exactly which server (placeId/jobId/
        universeId).  We update the observed identity (driving Wrong-Server
        detection) and, unless that join is the wrong server, confirm online —
        bypassing the slow dumpsys/uiautomator/logcat scrape entirely.

        Returns ``"wrong_server"``, ``"online"`` or ``""``.
        """
        pkg = str(package or "").strip()
        if not pkg:
            return ""
        seen_at = float(at if at is not None else time.time())

        def _as_id(value: object) -> int:
            try:
                ival = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return 0
            return ival if ival > 0 else 0

        with self._lock:
            row = self._states.setdefault(pkg, PackageRjnState(package=pkg))
            # Enroll this package in heartbeat-loss detection and record liveness
            # (drives the silence demotion in ``evaluate_package``). Applies to
            # both the loopback HTTP push and the logcat "DENGRJN_HB|" print.
            if seen_at > row.last_ingame_hb_at:
                row.last_ingame_hb_at = seen_at
            row.ingame_hb_ever = True
            changed = False
            for attr, val in (
                ("observed_place_id", place_id),
                ("observed_root_place_id", root_place_id),
                ("observed_universe_id", universe_id),
            ):
                ival = _as_id(val)
                if ival > 0 and getattr(row, attr) != ival:
                    setattr(row, attr, ival)
                    changed = True
            jid = str(job_id or "").strip()
            if jid:
                job_hash = _hash_code(jid)
                if job_hash:
                    if not row.anchor_job_id_hash:
                        row.anchor_job_id_hash = job_hash
                    if job_hash != row.observed_job_id_hash:
                        row.observed_job_id_hash = job_hash
                        changed = True
            # First-join anchor for no-target / public configs (mirrors
            # _capture_observed_identity): pin the legit first server, never
            # flagging it as wrong.
            if (
                WRONG_SERVER_ANCHOR_ENABLED
                and not row.anchor_set
                and not self._has_expected_target(row)
                and (row.observed_universe_id or row.observed_root_place_id or row.observed_place_id)
            ):
                row.anchor_universe_id = row.observed_universe_id
                row.anchor_root_place_id = row.observed_root_place_id
                row.anchor_place_id = row.observed_place_id
                row.anchor_set = True
            else:
                # Re-evaluate EVERY heartbeat, not only when the ids changed: a
                # package that stays in the wrong server keeps sending the same
                # (unchanged) placeId, and the old "elif changed" gate let the
                # next heartbeat re-confirm it Online — so a moved package never
                # stayed flagged.  ``_is_wrong_server_now`` is the pure predicate;
                # ``_maybe_flag_wrong_server`` performs the (debounced) transition.
                if self._is_wrong_server_now(row):
                    self._maybe_flag_wrong_server(pkg, seen_at)
                    return "wrong_server"
                row.wrong_server_active = False
            if bool(alive):
                self._confirm_online_evidence(pkg, seen_at, source="push_heartbeat")
                return "online"
        return ""

    def _ingest_logcat_heartbeat(self, pkg: str, raw_line: str, at: float) -> str:
        """Parse a logcat ``DENGRJN_HB|...`` line and feed it as a heartbeat.

        This is the clone-safe twin of the loopback HTTP push: the in-game Lua
        detector ``print``s its heartbeat to logcat, the PID-scoped dump (or the
        live stream) reads it, and we route it through ``ingest_push_heartbeat``
        so online/wrong-server/heartbeat-loss all behave identically regardless
        of which channel delivered the beat.  Returns the monitor verdict.
        """
        m = _INGAME_HB_RE.search(raw_line or "")
        if not m:
            return ""
        place_id = m.group(1) or 0
        root_place_id = m.group(2) or 0
        universe_id = m.group(3) or 0
        job_id = m.group(4) or ""
        alive = (m.group(5) or "1") == "1"
        return self.ingest_push_heartbeat(
            pkg,
            alive=alive,
            place_id=place_id,
            root_place_id=root_place_id,
            universe_id=universe_id,
            job_id=job_id,
            at=at,
        )

    def _has_expected_target(self, row: PackageRjnState) -> bool:
        return bool(
            row.expected_place_id
            or row.expected_root_place_id
            or row.expected_universe_id
            or row.expected_private_code_hash
        )

    def _capture_observed_identity(self, pkg: str, raw_line: str, at: float) -> bool:
        """Extract the actually-joined placeId/rootPlaceId/universeId from a join
        logcat line and (when it mismatches the configured target) flag Wrong
        Server. Caller holds the lock. Only public numeric ids are read. Returns
        True when this line triggered a Wrong-Server disconnect."""
        row = self._states.get(pkg)
        if row is None:
            return False
        changed = False
        m = _ROOT_PLACE_ID_RE.search(raw_line)
        if m:
            try:
                val = int(m.group(1))
                if val > 0 and val != row.observed_root_place_id:
                    row.observed_root_place_id = val
                    changed = True
            except (TypeError, ValueError):
                pass
        m = _PLACE_ID_RE.search(raw_line)
        if m:
            try:
                val = int(m.group(1))
                if val > 0 and val != row.observed_place_id:
                    row.observed_place_id = val
                    changed = True
            except (TypeError, ValueError):
                pass
        m = _UNIVERSE_ID_RE.search(raw_line)
        if m:
            try:
                val = int(m.group(1))
                if val > 0 and val != row.observed_universe_id:
                    row.observed_universe_id = val
                    changed = True
            except (TypeError, ValueError):
                pass
        m = _PRIVATE_CODE_RE.search(raw_line)
        if m:
            code_hash = _hash_code(m.group(1))
            if code_hash and code_hash != row.observed_private_code_hash:
                row.observed_private_code_hash = code_hash
                changed = True
        m = _JOB_ID_RE.search(raw_line)
        if m:
            job_hash = _hash_code(m.group(1))
            if job_hash:
                # Anchor the first server-instance of the session, then detect a
                # later move to a different server (same game). Always anchor on
                # first sight so private-server configs (which expose no
                # comparable placeId) still get a wrong-server signal.
                if not row.anchor_job_id_hash:
                    row.anchor_job_id_hash = job_hash
                if job_hash != row.observed_job_id_hash:
                    row.observed_job_id_hash = job_hash
                    changed = True
        if not changed:
            return False
        # Session anchor: the first joined game identity after a launch is the
        # configured target. Pin it (only when nothing was configured) so a later
        # join to a different game can still be flagged. Pinning the legit first
        # join must never itself flag Wrong Server.
        if (
            WRONG_SERVER_ANCHOR_ENABLED
            and not row.anchor_set
            and not self._has_expected_target(row)
            and (row.observed_universe_id or row.observed_root_place_id or row.observed_place_id)
        ):
            row.anchor_universe_id = row.observed_universe_id
            row.anchor_root_place_id = row.observed_root_place_id
            row.anchor_place_id = row.observed_place_id
            row.anchor_set = True
            return False
        return self._maybe_flag_wrong_server(pkg, at)

    def _has_expected_private_server(self, row: PackageRjnState) -> bool:
        return bool(
            row.expected_private_code_hash
            or row.expected_share_type.strip().lower() == "server"
        )

    def _is_wrong_server_now(self, row: PackageRjnState) -> bool:
        """Pure predicate: does the observed server identity provably differ from
        the configured target / session anchor?  Fail-safe — only True when BOTH
        an expected/anchor id and an observed id of the same kind are known and
        they differ.  No side effects (no transition); used both to GATE online
        re-confirmation and (via ``_maybe_flag_wrong_server``) to TRANSITION."""
        pairs = (
            # Configured ("expected") target vs observed.
            (row.expected_place_id, row.observed_place_id),
            (row.expected_root_place_id, row.observed_root_place_id),
            (row.expected_universe_id, row.observed_universe_id),
            # Session anchor (first join of this session) vs observed — catches a
            # move to a different game when the configured link had no placeId.
            (row.anchor_universe_id, row.observed_universe_id),
            (row.anchor_root_place_id, row.observed_root_place_id),
            (row.anchor_place_id, row.observed_place_id),
        )
        mismatch = any(
            expected > 0 and observed > 0 and expected != observed
            for expected, observed in pairs
        )
        code_mismatch = bool(
            row.expected_private_code_hash
            and row.observed_private_code_hash
            and row.expected_private_code_hash != row.observed_private_code_hash
        )
        # Server-instance (jobId) change: the player moved to a DIFFERENT server
        # of the same game.  placeId/universeId stay identical, so only the jobId
        # exposes it.  Gated to private-server configs (or the no-target anchor
        # mode) to avoid flagging legitimate matchmaking server hops in public
        # games (user request p-1bc476d931).
        job_mismatch = bool(
            WRONG_SERVER_JOBID_ENABLED
            and row.anchor_job_id_hash
            and row.observed_job_id_hash
            and row.anchor_job_id_hash != row.observed_job_id_hash
            and (self._has_expected_private_server(row) or row.anchor_set
                 or not self._has_expected_target(row))
        )
        return bool(mismatch or code_mismatch or job_mismatch)

    def _maybe_flag_wrong_server(self, pkg: str, at: float) -> bool:
        """Transition to DISCONNECTED (reason ``wrong_server``) when the joined
        server identity provably differs from the configured target. Fail-safe:
        only fires when BOTH an expected and an observed id of the same kind are
        known and they differ. Never fires on missing/partial data."""
        row = self._states.get(pkg)
        if row is None:
            return False
        if not self._is_wrong_server_now(row):
            return False
        row.wrong_server_active = True
        # Debounce: do not re-flag the same wrong server repeatedly between rounds.
        if row.last_wrong_server_at and (at - row.last_wrong_server_at) < 30.0:
            return False
        row.last_wrong_server_at = at
        row.last_with_reason_at = at
        row.disconnect_prompt_text = "Wrong Server"
        self._transition(
            pkg,
            STATE_DISCONNECTED,
            "wrong_server",
            at=at,
            offline=True,
        )
        return True

    def _try_confirm_launch_online(self, pkg: str, now: float) -> bool:
        """Best-effort in-game proof during launch before watchdog marks join failed."""
        try:
            from .package_online_evidence import (
                collect_online_evidence,
                evaluate_online_confirmed,
            )

            scan = collect_online_evidence(pkg, root_info=self._root_info)
            decision = evaluate_online_confirmed(scan)
            if bool(getattr(decision, "is_disconnected", False)):
                return False
            if decision.is_online_confirmed:
                self._confirm_online_evidence(pkg, now, source="activity_in_game")
                return True
        except Exception as exc:  # noqa: BLE001
            self._detector_errors.append(f"launch_online_fallback:{exc}"[:120])
        return False

    def _logcat_line_epoch(self, line: str, now: float) -> float:
        """Device-local epoch for a threadtime logcat line. The agent and logcat
        share the device clock, so this is directly comparable to time.time()."""
        m = _LOGCAT_TS_PREFIX_RE.match(line)
        if not m:
            return now
        mo, da, hh, mm, ss, ms = (int(g) for g in m.groups())
        lt = time.localtime(now)
        year = lt.tm_year
        # Year boundary: a December line read in early January belongs to last year.
        if mo == 12 and lt.tm_mon == 1:
            year -= 1
        try:
            base = time.mktime((year, mo, da, hh, mm, ss, 0, 0, -1))
        except (ValueError, OverflowError):
            return now
        return base + (ms / 1000.0)

    def _dump_pkg_logcat(self, pids: list[str]) -> list[str]:
        """Wide, PID-scoped `logcat -d` dump (pre-attributed, cheap)."""
        lines: list[str] = []
        for pid in pids[:DUMP_MAX_PIDS]:
            pid_s = str(pid or "").strip()
            if not pid_s:
                continue
            try:
                res = android.run_command(
                    ["logcat", "-d", "--pid", pid_s, "-t", str(DUMP_TAIL_LINES)],
                    timeout=8,
                )
                if res.ok and res.stdout:
                    lines.extend(res.stdout.splitlines())
            except Exception as exc:  # noqa: BLE001
                self._detector_errors.append(f"dump_scan:{exc}"[:120])
                if len(self._detector_errors) > 16:
                    self._detector_errors = self._detector_errors[-16:]
        return lines

    def _scan_logcat_dump(self, pkg: str, now: float) -> None:
        """Authoritative full-dump disconnect + identity scan. Caller holds lock.

        Parses the most-recent disconnect-reason / idle / join-identity lines from
        a wide PID-scoped dump, ordered by device timestamp, and only acts on a
        disconnect that is newer than the last online proof (so a reconnect after
        the kick is respected). This runs even while the live stream looks fresh
        (the process keeps emitting heartbeats after an idle kick, which used to
        throttle the old fallback), so a stalled/lossy stream can no longer
        swallow a 278/disconnect."""
        if not DUMP_SCAN_ENABLED:
            return
        row = self._states.get(pkg)
        if row is None:
            return
        if (now - row.last_dump_scan_at) < DUMP_SCAN_MIN_INTERVAL_SECONDS:
            return
        row.last_dump_scan_at = now
        pids = [str(p).strip() for p in (row.pids or []) if str(p).strip()]
        if not pids:
            return
        lines = self._dump_pkg_logcat(pids)
        if not lines:
            return

        latest_disc_epoch = 0.0
        latest_disc_code = 0
        latest_disc_idle = False
        latest_disc_line = ""
        latest_online_epoch = 0.0
        latest_ident_epoch = 0.0
        latest_ident_line = ""
        latest_hb_epoch = 0.0
        latest_hb_line = ""

        for line in lines:
            if _INGAME_HB_RE.search(line):
                ep = self._logcat_line_epoch(line, now)
                if ep >= latest_hb_epoch:
                    latest_hb_epoch = ep
                    latest_hb_line = line
                continue
            if _GAME_JOIN_RE.search(line):
                ep = self._logcat_line_epoch(line, now)
                if ep >= latest_online_epoch:
                    latest_online_epoch = ep
                continue
            if (
                _WITH_REASON_RE.search(line)
                or _IDLE_DISCONNECT_RE.search(line)
                or _ERROR_CODE_LINE_RE.search(line)
            ):
                ep = self._logcat_line_epoch(line, now)
                if ep >= latest_disc_epoch:
                    latest_disc_epoch = ep
                    latest_disc_line = line
                    cm = _DISCONNECT_REASON_CODE_RE.search(line) or _ERROR_CODE_LINE_RE.search(line)
                    latest_disc_code = int(cm.group(1)) if cm else 0
                    latest_disc_idle = bool(
                        _IDLE_DISCONNECT_RE.search(line) or latest_disc_code == 278
                    )
                continue
            if _JOIN_IDENTITY_HINT_RE.search(line):
                ep = self._logcat_line_epoch(line, now)
                if ep >= latest_ident_epoch:
                    latest_ident_epoch = ep
                    latest_ident_line = line

        # In-game Lua detector heartbeat (clone-safe primary signal): the most
        # recent "DENGRJN_HB|" line carries the live server identity and proves
        # the client is in a real server right now.  Only act on a GENUINELY NEW
        # beat (newer than the last one we processed): the wide dump buffer keeps
        # printing stale heartbeat lines for minutes after the script stops, and
        # re-ingesting one would falsely re-confirm online and fight the
        # heartbeat-loss demotion.  A fresh beat confirms online, refreshes the
        # loss grace, and flags a wrong server (→ already DISCONNECTED, stop).
        if latest_hb_line and latest_hb_epoch > (row.last_ingame_hb_at + 0.5):
            verdict = self._ingest_logcat_heartbeat(pkg, latest_hb_line, latest_hb_epoch or now)
            if verdict == "wrong_server":
                return

        # Wrong-Server / deeplink: process the most recent join-identity line. If
        # it flags Wrong Server the package is already DISCONNECTED — stop here.
        if latest_ident_line:
            if self._capture_observed_identity(pkg, latest_ident_line, latest_ident_epoch or now):
                return

        online_anchor = max(row.last_positive_online_evidence_at, row.launch_started_at)
        # Reconnected after the kick: the latest join proof is newer than the
        # latest disconnect — re-confirm online (also self-heals a stalled stream).
        if (
            latest_online_epoch > 0
            and latest_online_epoch > latest_disc_epoch
            and latest_online_epoch >= online_anchor
        ):
            if row.internal_state != STATE_ONLINE_CONFIRMED:
                self._confirm_online_evidence(pkg, latest_online_epoch, source="gamejoinloadtime")
            return

        if latest_disc_epoch <= 0:
            return
        # Only a disconnect that happened after the last online proof / this
        # launch is actionable (ignore stale pre-launch kicks).
        if latest_disc_epoch < online_anchor:
            return
        # De-dupe: do not re-fire on the same already-handled disconnect line.
        if latest_disc_epoch <= row.last_dump_disconnect_epoch:
            return
        row.last_dump_disconnect_epoch = latest_disc_epoch
        row.last_disconnect_code = latest_disc_code or row.last_disconnect_code
        row.last_with_reason_at = max(row.last_with_reason_at, latest_disc_epoch)
        row.disconnect_prompt_text = _sanitize_line(latest_disc_line)
        reason = (
            internal_reason_for_disconnect_code(latest_disc_code)
            if latest_disc_code
            else (
                "idle_disconnect_278"
                if latest_disc_idle
                else "logcat_with_reason"
            )
        )
        if row.internal_state != STATE_DISCONNECTED or row.last_transition_reason != reason:
            self._transition(pkg, STATE_DISCONNECTED, reason, at=latest_disc_epoch, offline=True)

    def _detect_live_disconnect(self, package: str) -> tuple[str | None, str | None]:
        try:
            from .package_online_evidence import detect_live_disconnect

            reason, matched = detect_live_disconnect(
                package,
                root_info=getattr(self, "_root_info", None),
            )
            return reason, matched
        except Exception as exc:  # noqa: BLE001
            self._detector_errors.append(f"disconnect_scan:{exc}"[:120])
            return None, None

    def _apply_phrase(self, pkg: str, phrase: str, at: float, event: LogcatEvent) -> None:
        row = self._states[pkg]
        row.last_logcat_event_at = at
        if phrase == "gamejoinloadtime":
            self._confirm_online_evidence(pkg, at, source="gamejoinloadtime", event=event)
        elif phrase == "with reason":
            row.last_with_reason_at = at
            prompt = getattr(event, "raw_line_sanitized", "") if event is not None else ""
            if prompt:
                row.disconnect_prompt_text = str(prompt)[:240]
            # Parse the authoritative numeric disconnect code (e.g. 278 = idle) so
            # the user-facing reason shows the real "Error Code: N" text and a 278
            # idle kick is classified correctly.
            _raw = getattr(event, "raw_line_sanitized", "") or ""
            code_match = _DISCONNECT_REASON_CODE_RE.search(_raw) or _ERROR_CODE_LINE_RE.search(_raw)
            transition_reason = "logcat_with_reason"
            if code_match:
                try:
                    row.last_disconnect_code = int(code_match.group(1))
                except (TypeError, ValueError):
                    row.last_disconnect_code = 0
                from .roblox_disconnect_reasons import internal_reason_for_disconnect_code as _irc

                transition_reason = _irc(row.last_disconnect_code)
            self._transition(
                pkg,
                STATE_DISCONNECTED,
                transition_reason,
                at=at,
                offline=True,
            )
            event.action_taken = "DISCONNECTED"
        elif phrase == "idle_disconnect_278":
            row.last_with_reason_at = at
            row.last_disconnect_code = 278
            prompt = getattr(event, "raw_line_sanitized", "") if event is not None else ""
            if prompt:
                row.disconnect_prompt_text = str(prompt)[:240]
            self._transition(
                pkg,
                STATE_DISCONNECTED,
                internal_reason_for_disconnect_code(278),
                at=at,
                offline=True,
            )
            event.action_taken = "DISCONNECTED"
        elif phrase == "doTeleport":
            row.last_doteleport_at = at
            if row.internal_state == STATE_ONLINE_CONFIRMED:
                row.internal_state = STATE_TELEPORTING
                row.last_transition_at = at
                row.last_transition_reason = "doTeleport"
            event.action_taken = "TELEPORTING"

    def note_launch_watchdog(self, package: str, *, relaunch: bool = False) -> None:
        """Detection-only launch timer — does not launch/relaunch or change supervisor state."""
        pkg = str(package or "").strip()
        if not pkg:
            return
        now = time.time()
        with self._lock:
            row = self._states.setdefault(pkg, PackageRjnState(package=pkg))
            row.launch_started_at = now
            # Open/refresh the global launch-quiet window: this and every other
            # online clone gets its heartbeat throttled while this one loads, so
            # heartbeat-loss must stand down across the whole batch until things
            # settle (prevents the false-dead → recovery-storm → kill-all chain).
            self._last_any_launch_at = now
            row.watchdog_active = True
            row.launch_failed_reason = ""
            row.relaunching = bool(relaunch)
            # Fresh session: re-anchor Wrong-Server detection and let a new
            # post-launch disconnect be acted on again.
            row.observed_place_id = 0
            row.observed_root_place_id = 0
            row.observed_universe_id = 0
            row.observed_private_code_hash = ""
            row.observed_job_id_hash = ""
            row.anchor_job_id_hash = ""
            row.anchor_place_id = 0
            row.anchor_root_place_id = 0
            row.anchor_universe_id = 0
            row.anchor_set = False
            row.wrong_server_active = False
            row.last_wrong_server_at = 0.0
            row.last_dump_disconnect_epoch = 0.0
            # Fresh session: re-enroll heartbeat-loss only after the new session's
            # first in-game heartbeat (a stale pre-launch heartbeat must not
            # instantly demote the relaunched client).
            row.last_ingame_hb_at = 0.0
            row.ingame_hb_ever = False
            if relaunch:
                row.internal_state = STATE_RELAUNCHING
                row.last_transition_at = now
                row.last_transition_reason = "relaunch_watchdog_started"
            elif row.internal_state in {STATE_STOPPED, STATE_DEAD, STATE_FAILED}:
                row.internal_state = STATE_LAUNCHING
                row.last_transition_at = now
                row.last_transition_reason = "launch_watchdog_started"

    def begin_launch_watchdog(self, package: str, *, relaunch: bool = False) -> None:
        """Backward-compatible alias — detection only."""
        self.note_launch_watchdog(package, relaunch=relaunch)

    def _process_check(self, package: str) -> tuple[bool, list[str]]:
        """Roblox package PID check — pidof/is_process_running only (no pgrep -f)."""
        pkg = android.validate_package_name(package)
        pids: list[str] = []
        root_tool = getattr(self._root_info, "tool", None) if self._root_info else None
        try:
            if getattr(self._root_info, "available", False) and root_tool:
                res = android.run_root_command(["pidof", pkg], root_tool=root_tool, timeout=2)
                if res.ok and (res.stdout or "").strip():
                    pids = res.stdout.strip().split()
            elif android.is_process_running(pkg):
                res = android.run_command(["pidof", pkg], timeout=2)
                if res.ok and (res.stdout or "").strip():
                    pids = res.stdout.strip().split()
        except Exception:  # noqa: BLE001
            pass
        return bool(pids), pids

    def evaluate_package(
        self, package: str, *, fast_push: bool = False
    ) -> PackageEvaluateResult:
        """Evaluate one package's lifecycle state.

        ``fast_push`` is set by the supervisor when a *fresh* in-game Lua
        heartbeat already established the live-server truth (placeId/jobId).  In
        that case the expensive logcat dump + UI disconnect scan are skipped:
        the heartbeat is authoritative and a disconnect would show up as the
        heartbeat going silent (handled by the supervisor's loss watchdog), not
        as a log line.  This is what keeps a full round in the low single-digit
        seconds so every transition resolves under ~15s (p-5d0df79c33).
        """
        pkg = str(package or "").strip()
        now = time.time()
        failed: list[str] = []
        self._ensure_logcat_stream()
        if not fast_push:
            self._poll_recent_logcat()
        with self._lock:
            row = self._states.setdefault(pkg, PackageRjnState(package=pkg))
            effective_uid = row.uid or self._uid_map.get(pkg) or ""
            if not effective_uid and pkg in self._uid_resolutions:
                res = self._uid_resolutions[pkg]
                if not res.uid:
                    failed.append("uid_not_resolved")
            process_exists, pids = self._process_check(pkg)
            row.process_exists = process_exists
            row.pids = list(pids)
            row.last_process_check_at = now

            if row.watchdog_active and row.launch_started_at > 0:
                age = now - row.launch_started_at
                if (
                    age >= LAUNCH_ONLINE_FALLBACK_MIN_AGE_SECONDS
                    and row.last_positive_online_evidence_at < row.launch_started_at
                    and process_exists
                ):
                    self._try_confirm_launch_online(pkg, now)
                if age > self._launch_watchdog_seconds:
                    had_positive = row.last_positive_online_evidence_at >= row.launch_started_at
                    if not had_positive and row.last_gamejoinloadtime_at < row.launch_started_at:
                        row.watchdog_active = False
                        row.launch_failed_reason = (
                            "no_online_confirmation"
                            if row.last_positive_online_evidence_at <= 0
                            else "launch_watchdog_timeout"
                        )
                        row.error_count += 1
                        row.internal_state = STATE_FAILED
                        row.last_transition_at = now
                        row.last_transition_reason = row.launch_failed_reason
                        row.last_dead_detected_at = now
                        row.last_dead_reason = row.launch_failed_reason

            if not process_exists:
                row.process_missing_streak += 1
                if row.process_missing_streak >= PROCESS_MISSING_CONFIRM:
                    # Any package that ever reached a live server this session and
                    # whose process is now gone is a real force-close / crash →
                    # recover it within PROCESS_MISSING_CONFIRM checks.  This MUST
                    # include the logcat-heartbeat-only online path
                    # (``ingame_hb_ever`` / ``online_since``): the old code gated
                    # solely on ``_was_ever_online_confirmed`` (which the push
                    # heartbeat could leave unset) and otherwise required the state
                    # to be EXACTLY ONLINE_CONFIRMED — so a force-closed clone whose
                    # state was DISCONNECTED/TELEPORTING fell through EVERY branch
                    # and sat undetected for ~an hour (probe p-87b567bde8 #2).  The
                    # supervisor's loading grace / relaunch-verify window is what
                    # prevents a premature kill during the tool's own relaunch, so
                    # ``ever_in_game`` takes precedence here exactly as before.
                    ever_in_game = (
                        row.ingame_hb_ever
                        or row.last_positive_online_evidence_at > 0
                        or row.online_since > 0
                        or row.internal_state in {
                            STATE_ONLINE_CONFIRMED,
                            STATE_TELEPORTING,
                            STATE_DISCONNECTED,
                        }
                    )
                    if ever_in_game:
                        row.force_close_detected = True
                        self._transition(
                            pkg,
                            STATE_DEAD,
                            "process_missing",
                            at=now,
                            offline=True,
                        )
                    elif (
                        row.watchdog_active
                        or row.internal_state in {STATE_LAUNCHING, STATE_RELAUNCHING}
                    ):
                        # First launch still in flight (never reached a server yet)
                        # — tolerate the transient process gap.
                        row.process_missing_streak = 0
                    else:
                        # Never reached a live server and not launching — nothing to
                        # recover yet; don't let the streak grow unbounded (this is
                        # the old dead-end branch that left force-closes undetected).
                        row.process_missing_streak = 0
            elif fast_push:
                # Fresh in-game heartbeat already proved the live server this
                # round — skip the 8s/pid logcat dump and the dumpsys/uiautomator
                # disconnect scan entirely.  A real disconnect manifests as the
                # heartbeat going silent (supervisor loss watchdog), not a log line.
                row.process_missing_streak = 0
                row.force_close_detected = False
            else:
                row.process_missing_streak = 0
                row.force_close_detected = False
                # Authoritative full-dump scan: catches a disconnect/idle kick or a
                # move to a different game even when the live stream stalled or the
                # log spam buried the line. Runs every round (cheap, PID-scoped)
                # and is throttled internally.
                self._scan_logcat_dump(pkg, now)
                # In-game Lua detector heartbeat-loss: once detector.lua has
                # printed at least one "DENGRJN_HB|" line this session, a silence
                # past the grace while the process is alive means the client left
                # the live server (kick / error code / captcha / freeze) — the GL/
                # WebView state the UI scrapers cannot see on a background clone.
                # This is the universal "improve the other scenarios" path the
                # user asked for, matching the ~1s logcat online detection speed.
                #
                # It must NOT fire during normal loading, because the heartbeat
                # legitimately pauses then and demoting would false-kill a healthy
                # client (probe p-630c95f7cc #1).  We therefore stand down while:
                #   * this package is still launching/relaunching (watchdog_active),
                #   * ANY package launched recently (launch storm throttles every
                #     online clone's heartbeat — global launch-quiet window), or
                #   * this package just joined / teleported (in-game loading screen
                #     between places — also lets a wrong-server heartbeat arrive and
                #     be labelled "not in configured server" instead of generic loss).
                hb_quiet = self._last_any_launch_at > 0 and (
                    now - self._last_any_launch_at
                ) <= INGAME_HB_LOSS_LAUNCH_QUIET_SECONDS
                load_recent = (
                    now - max(row.last_gamejoinloadtime_at, row.last_doteleport_at)
                ) <= INGAME_HB_LOSS_SECONDS
                if (
                    INGAME_HB_LOSS_ENABLED
                    and row.ingame_hb_ever
                    and row.last_ingame_hb_at > 0
                    and row.internal_state in {STATE_ONLINE_CONFIRMED, STATE_TELEPORTING}
                    and not row.watchdog_active
                    and not hb_quiet
                    and not load_recent
                    and (now - row.last_ingame_hb_at) > INGAME_HB_LOSS_SECONDS
                ):
                    row.disconnect_prompt_text = (
                        "In-game detection heartbeat lost (client left live server)"
                    )
                    self._transition(
                        pkg,
                        STATE_DISCONNECTED,
                        "heartbeat_lost",
                        at=now,
                        offline=True,
                    )
                stream_fresh = self.stream_fresh_for(pkg, STREAM_FRESH_DISCONNECT_SKIP_SECONDS)
                heavy_due = (
                    now - row.last_disconnect_scan_at
                ) >= HEAVY_DISCONNECT_FALLBACK_INTERVAL_SECONDS
                if (
                    row.internal_state in {STATE_ONLINE_CONFIRMED, STATE_TELEPORTING}
                    and (not stream_fresh or heavy_due)
                    and now - row.last_disconnect_scan_at >= DISCONNECT_SCAN_INTERVAL_SECONDS
                ):
                    row.last_disconnect_scan_at = now
                    disconnect_reason, matched_text = self._detect_live_disconnect(pkg)
                    if disconnect_reason:
                        row.last_with_reason_at = now
                        if matched_text:
                            row.disconnect_prompt_text = str(matched_text)[:240]
                        self._transition(
                            pkg,
                            STATE_DISCONNECTED,
                            disconnect_reason,
                            at=now,
                            offline=True,
                        )

            internal = row.internal_state
            has_positive_evidence = (
                internal == STATE_ONLINE_CONFIRMED
                and row.last_positive_online_evidence_at > 0
            )
            is_online = has_positive_evidence and process_exists
            if process_exists and _package_force_stopped_quick(pkg):
                failed.append("force_stopped")
                is_online = False
            if internal == STATE_ONLINE_CONFIRMED and not process_exists:
                failed.append("process_missing")
                is_online = False
            if internal == STATE_ONLINE_CONFIRMED and row.last_with_reason_at > row.last_positive_online_evidence_at:
                failed.append("with_reason_after_join")
                is_online = False
            if not effective_uid:
                failed.append("uid_not_resolved")
                if row.online_evidence_source not in _UID_OPTIONAL_ONLINE_SOURCES:
                    is_online = False
            if internal != STATE_ONLINE_CONFIRMED:
                failed.append("no_positive_online_evidence")
                is_online = False
            elif not row.last_positive_online_evidence_at:
                failed.append("no_uid_matched_gamejoinloadtime")
                is_online = False

            public = self._map_public_status(
                internal,
                is_online,
                relaunching=bool(row.relaunching or internal == STATE_RELAUNCHING),
            )
            reason = self._decision_reason(row, is_online, failed)
            from .roblox_disconnect_reasons import format_lifecycle_dead_reason

            dead_reason_key = row.last_transition_reason or row.launch_failed_reason or row.last_dead_reason
            if is_online:
                reason_user_friendly = ""
            elif internal in {STATE_DISCONNECTED, STATE_DEAD, STATE_FAILED}:
                reason_user_friendly = format_lifecycle_dead_reason(
                    dead_reason_key,
                    row.disconnect_prompt_text or None,
                )
            else:
                reason_user_friendly = format_lifecycle_dead_reason(
                    row.launch_failed_reason or row.last_transition_reason or reason,
                    row.disconnect_prompt_text or None,
                )

            detail = {
                "internal_state": internal,
                "online_confirmed": str(is_online).lower(),
                "runtime_source": row.runtime_source or "none",
                "online_since": row.online_since or "",
                "online_evidence_source": row.online_evidence_source or "",
                "process_running": str(process_exists).lower(),
                "pids": ",".join(pids),
                "reason": reason,
                "dead_reason": row.last_transition_reason if not is_online else "",
                "launch_watchdog_active": str(row.watchdog_active).lower(),
                "launch_watchdog_age_seconds": round(
                    max(0.0, now - row.launch_started_at) if row.launch_started_at else 0.0,
                    1,
                ),
                "last_gamejoinloadtime_at": row.last_gamejoinloadtime_at or "",
                "last_positive_online_evidence_at": row.last_positive_online_evidence_at or "",
                "last_with_reason_at": row.last_with_reason_at or "",
                "disconnect_code": row.last_disconnect_code or "",
                "launch_failed_reason": row.launch_failed_reason or "",
                "reason_internal": row.last_transition_reason or row.launch_failed_reason or reason,
                "reason_user_friendly": reason_user_friendly,
                "disconnect_prompt_text": row.disconnect_prompt_text or "",
                "matched_disconnect_text": row.disconnect_prompt_text or "",
                "last_uid_line_at": row.last_uid_line_at or "",
                "logcat_stream_alive": str(bool(getattr(self, "_logcat_stream_alive", False))).lower(),
                "expected_place_id": row.expected_place_id or "",
                "observed_place_id": row.observed_place_id or "",
                "expected_root_place_id": row.expected_root_place_id or "",
                "observed_root_place_id": row.observed_root_place_id or "",
                "observed_universe_id": row.observed_universe_id or "",
                "anchor_place_id": row.anchor_place_id or "",
                "expected_private_code_set": str(bool(row.expected_private_code_hash)).lower(),
                "observed_private_code_set": str(bool(row.observed_private_code_hash)).lower(),
                "wrong_server": str(row.last_transition_reason == "wrong_server").lower(),
                "why_still_launching": (
                    reason
                    if internal in {STATE_LAUNCHING, STATE_RELAUNCHING} and not is_online
                    else ""
                ),
            }
            return PackageEvaluateResult(
                package=pkg,
                internal_state=internal,
                public_status=public,
                reason=reason,
                is_online_confirmed=is_online,
                failed_checks=list(failed),
                process_exists=process_exists,
                detail=detail,
            )

    def _map_public_status(self, internal: str, is_online: bool, *, relaunching: bool = False) -> str:
        if is_online:
            return "Online"
        if internal == STATE_DISCONNECTED:
            return "Disconnected"
        if internal == STATE_FAILED:
            return "Join Failed"
        if relaunching or internal == STATE_RELAUNCHING:
            return "Relaunching"
        if internal in {STATE_LAUNCHING, STATE_TELEPORTING, STATE_STOPPED}:
            return "Launching"
        if internal in {STATE_DEAD}:
            return "Dead"
        return "Dead"

    def _decision_reason(
        self,
        row: PackageRjnState,
        is_online: bool,
        failed: list[str],
    ) -> str:
        if is_online:
            src = row.online_evidence_source or row.runtime_source or "gamejoinloadtime"
            return f"online because UID-matched {src} and process exists"
        if row.force_close_detected or "process_missing" in failed:
            return "process_missing"
        if row.last_transition_reason == "wrong_server":
            return "wrong_server"
        if row.last_transition_reason and row.last_transition_reason.startswith("disconnect_code_"):
            return row.last_transition_reason
        if row.last_with_reason_at and row.last_with_reason_at >= row.last_positive_online_evidence_at:
            if row.last_transition_reason == "idle_disconnect_278":
                return "idle_disconnect_278"
            if row.last_transition_reason in {"ui_disconnect", "logcat_disconnect"}:
                return row.last_transition_reason
            return "UID-matched logcat line contained with reason"
        if row.launch_failed_reason in {"launch_watchdog_timeout", "no_online_confirmation"}:
            return row.launch_failed_reason
        if "uid_not_resolved" in failed:
            return row.uid_error or "uid_not_resolved"
        if row.internal_state in {STATE_LAUNCHING, STATE_RELAUNCHING}:
            if not self._logcat_stream_alive:
                return "logcat stream not alive"
            if row.uid_error:
                return row.uid_error
            return "no positive online evidence after launch"
        return "no UID-matched positive online evidence after launch"

    def probe_snapshot(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            uid_map_out: dict[str, Any] = {}
            for pkg, res in self._uid_resolutions.items():
                uid_map_out[pkg] = {
                    "uid": res.uid,
                    "resolved_at": res.resolved_at,
                    "resolve_error": res.error,
                    "sample": res.command_output_sample[:200] if res.command_output_sample else "",
                }
            packages_out: dict[str, Any] = {}
            for pkg, row in self._states.items():
                age = max(0.0, now - row.launch_started_at) if row.launch_started_at else 0.0
                ev = self.evaluate_package(pkg)
                packages_out[pkg] = {
                    "state": row.internal_state,
                    "online_since": row.online_since or None,
                    "runtime_source": row.runtime_source or "gamejoinloadtime",
                    "process_exists": row.process_exists,
                    "pids": list(row.pids),
                    "last_gamejoinloadtime_at": row.last_gamejoinloadtime_at or None,
                    "last_doteleport_at": row.last_doteleport_at or None,
                    "last_with_reason_at": row.last_with_reason_at or None,
                    "disconnect_code": row.last_disconnect_code or None,
                    "expected_place_id": row.expected_place_id or None,
                    "observed_place_id": row.observed_place_id or None,
                    "expected_root_place_id": row.expected_root_place_id or None,
                    "observed_root_place_id": row.observed_root_place_id or None,
                    "observed_universe_id": row.observed_universe_id or None,
                    "anchor_place_id": row.anchor_place_id or None,
                    "anchor_set": row.anchor_set,
                    "expected_private_code_set": bool(row.expected_private_code_hash),
                    "observed_private_code_set": bool(row.observed_private_code_hash),
                    "wrong_server_detected": row.last_transition_reason == "wrong_server",
                    "last_logcat_event_at": row.last_logcat_event_at or None,
                    "launch_started_at": row.launch_started_at or None,
                    "launch_watchdog_active": row.watchdog_active,
                    "launch_watchdog_age_seconds": round(age, 1),
                    "launch_watchdog_timeout_seconds": self._launch_watchdog_seconds,
                    "launch_failed_reason": row.launch_failed_reason or None,
                    "last_transition_reason": row.last_transition_reason or None,
                    "last_dead_reason": row.last_dead_reason or None,
                    "decision": ev.reason,
                    "reason_user_friendly": ev.detail.get("reason_user_friendly") or ev.reason,
                    "failed_checks": list(ev.failed_checks),
                    "is_online_confirmed": ev.is_online_confirmed,
                    "last_uid_line_at": row.last_uid_line_at or None,
                    "uid_line_silence_seconds": (
                        round(now - row.last_uid_line_at, 1) if row.last_uid_line_at else None
                    ),
                    "recent_uid_lines": list(row.recent_uid_lines[-14:]),
                }
            recent = [
                {
                    "package": e.package,
                    "uid": e.uid,
                    "phrase": e.phrase,
                    "seen_at": e.seen_at,
                    "raw_line_sanitized": e.raw_line_sanitized,
                    "action_taken": e.action_taken,
                }
                for e in self._recent_events[-32:]
            ]
            return {
                "enabled": self._session_started,
                "detection_only": True,
                "logcat_stream_alive": self._logcat_stream_alive,
                "logcat_pid": self._logcat_pid or None,
                "logcat_last_line_at": self._logcat_last_line_at or None,
                "logcat_last_uid_matched_line_at": self._logcat_last_uid_matched_at or None,
                "logcat_started_at": self._logcat_started_at or None,
                "logcat_cleared_at": self._logcat_cleared_at or None,
                "logcat_error": self._logcat_error or None,
                "detector_errors": list(self._detector_errors),
                "ignored_uid_lines": list(self._ignored_uid_lines[-16:]),
                "monitor_started_at": self._monitor_started_at or None,
                "watched_phrases": list(WATCHED_PHRASES),
                "uid_map": uid_map_out,
                "packages": packages_out,
                "recent_events": recent,
            }

    def write_probe_file(self) -> None:
        path = DATA_DIR / "rjn-style-detection.json"
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"rjn_style_detection": self.probe_snapshot()}, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:
            pass
