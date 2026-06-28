"""Extra Roblox connectivity / disconnect signals (best-effort, no secret scraping)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import android
from .config import validate_package_name

# (regex, reason) — only use when regex matches; keep patterns conservative.
_LOGCAT_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(connection lost|disconnected from|lost connection|network error)\b", re.I), "disconnected"),
    (re.compile(r"\b(disconnected for being idle|Error Code:\s*278|idle\s+\d+\s+minutes)\b", re.I), "idle_disconnect"),
    (re.compile(r"\b(server shut|shutting down|server closed|you were kicked)\b", re.I), "server_shutdown"),
    (
        re.compile(r"\b(private server link (code )?refresh|private server expired|private server access)\b", re.I),
        "private_server_refresh",
    ),
]

# dumpsys / activity text (no secrets expected in these fragments)
_DUMPSYS_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(ErrorActivity|Disconnected|ConnectionError|Reconnect)\b"), "disconnected"),
    (re.compile(r"\b(disconnected for being idle|Error Code:\s*278|You were disconnected)\b", re.I), "idle_disconnect"),
    (re.compile(r"\b(maintenance|shut\s*down)\b", re.I), "server_shutdown"),
]

_REASON_FALLBACK = "unknown_unhealthy"


@dataclass(frozen=True)
class UnhealthyEvidence:
    category: str
    source: str
    snippet: str


def _pid_for_package(package: str) -> str | None:
    package = validate_package_name(package)
    res = android.run_command(["pidof", package], timeout=android.PROCESS_TIMEOUT_SECONDS)
    if res.ok and res.stdout.strip():
        return res.stdout.strip().split()[0]
    return None


# A GL-rendered disconnect (e.g. Error 278) emits at most one log line and the
# process keeps running, so the line scrolls out of a tiny window quickly. We read
# a much wider tail so a disconnect that happened seconds-to-minutes ago is still
# visible to the 5s periodic scan. (Patterns are unchanged and unambiguous, so a
# wider window only improves recall — it cannot cause a false disconnect.)
_LOGCAT_PID_TAIL = 1000
_LOGCAT_PKG_TAIL = 1500


def _brief_logcat_for_pid(pid: str) -> str:
    res = android.run_command(
        ["logcat", "-d", "-t", str(_LOGCAT_PID_TAIL), "--pid", pid],
        timeout=6,
    )
    if res.ok:
        return res.stdout[-24000:]
    res2 = android.run_command(["logcat", "-d", "-t", str(_LOGCAT_PID_TAIL)], timeout=6)
    return res2.stdout[-24000:] if res2.ok else ""


def _brief_logcat_for_package(package: str) -> str:
    package = validate_package_name(package)
    res = android.run_command(["logcat", "-d", "-t", str(_LOGCAT_PKG_TAIL)], timeout=7)
    if not res.ok:
        return ""
    lines: list[str] = []
    for line in res.stdout.splitlines():
        lower = line.lower()
        if package in line or "with reason" in lower or "278" in line or "idle" in lower:
            lines.append(line)
    return "\n".join(lines[-160:])


def _match_rules(text: str, rules: list[tuple[re.Pattern[str], str]]) -> tuple[str | None, str]:
    if not text:
        return None, ""
    for rx, cat in rules:
        m = rx.search(text)
        if m:
            return cat, m.group(0)[:80]
    return None, ""


def analyze_disconnect_signals(package: str) -> UnhealthyEvidence | None:
    """When process is alive but unhealthy, look for non-secret UI/log hints."""
    package = validate_package_name(package)
    pid = _pid_for_package(package)
    if pid:
        log_blob = _brief_logcat_for_pid(pid)
        cat, snip = _match_rules(log_blob, _LOGCAT_RULES)
        if cat:
            return UnhealthyEvidence(category=cat, source="logcat", snippet=snip)
    hint_blob = _brief_logcat_for_package(package)
    cat, snip = _match_rules(hint_blob, _LOGCAT_RULES)
    if cat:
        return UnhealthyEvidence(category=cat, source="logcat_hint", snippet=snip)
    # Activity / window (global dumpsys — filter lines mentioning package)
    act = android.run_command(["dumpsys", "activity", "activities"], timeout=6)
    if act.ok:
        lines = [ln for ln in act.stdout.splitlines() if package in ln][:40]
        blob = "\n".join(lines)
        cat, snip = _match_rules(blob, _DUMPSYS_RULES)
        if cat:
            return UnhealthyEvidence(category=cat, source="dumpsys_activity", snippet=snip)
    win = android.run_command(["dumpsys", "window", "windows"], timeout=6)
    if win.ok:
        lines = [ln for ln in win.stdout.splitlines() if package in ln][:30]
        blob = "\n".join(lines)
        cat, snip = _match_rules(blob, _DUMPSYS_RULES)
        if cat:
            return UnhealthyEvidence(category=cat, source="dumpsys_window", snippet=snip)
    cmd_top = android.run_command(
        ["dumpsys", "activity", "top"], timeout=5
    )
    if cmd_top.ok:
        lines = [ln for ln in cmd_top.stdout.splitlines() if package in ln][:24]
        blob = "\n".join(lines)
        cat, snip = _match_rules(blob, _DUMPSYS_RULES)
        if cat:
            return UnhealthyEvidence(category=cat, source="activity_top", snippet=snip)


def categorize_unhealthy(default_reason: str | None, package: str) -> str:
    """Map signals to reason category; never invent specific category without evidence."""
    ev = analyze_disconnect_signals(package)
    if ev:
        return ev.category
    if default_reason in {
        "disconnected",
        "process_missing",
        "server_shutdown",
        "private_server_refresh",
        "heartbeat_timeout",
    }:
        return default_reason
    return _REASON_FALLBACK
