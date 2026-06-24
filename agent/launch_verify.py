"""Root-first Android launch verification with honest evidence."""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass, field
from typing import Any

from . import android, root_access
from .config import validate_package_name


@dataclass
class LaunchVerificationResult:
    package: str
    success: bool
    launch_command: str = ""
    launch_method: str = ""
    launch_returncode: int = -1
    launch_stdout: str = ""
    launch_stderr: str = ""
    resolved_activity: str = ""
    launchable: bool = True
    not_launchable_reason: str = ""
    process_evidence: dict[str, Any] = field(default_factory=dict)
    foreground_package: str | None = None
    resumed_activity_line: str = ""
    window_focus_line: str = ""
    logcat_lines: list[str] = field(default_factory=list)
    poll_seconds: float = 0.0
    failure_reason: str = ""
    possible_causes: list[str] = field(default_factory=list)
    root_used: bool = True
    game_joined: bool | None = None
    game_join_reason: str = ""

    def summary_lines(self) -> list[str]:
        lines = [
            f"package: {self.package}",
            f"launchable: {'yes' if self.launchable else 'no'}",
            f"root_used: {'yes' if self.root_used else 'no'}",
        ]
        if self.not_launchable_reason:
            lines.append(f"not_launchable: {self.not_launchable_reason}")
        if self.resolved_activity:
            lines.append(f"resolved_activity: {self.resolved_activity}")
        lines.append(f"launch_method: {self.launch_method or 'none'}")
        if self.launch_command:
            lines.append(f"launch_command: {self.launch_command[:500]}")
        lines.append(f"launch_rc: {self.launch_returncode}")
        if self.launch_stdout.strip():
            lines.append(f"launch_stdout: {self.launch_stdout.strip()[:400]}")
        if self.launch_stderr.strip():
            lines.append(f"launch_stderr: {self.launch_stderr.strip()[:400]}")
        pe = self.process_evidence or {}
        lines.append(
            "process_checks: "
            f"root_pidof={pe.get('root_pidof', '')!s} "
            f"root_ps={pe.get('root_ps', '')!s} "
            f"root_running={pe.get('root_running', False)!s} "
            f"foreground={pe.get('foreground', False)!s}"
        )
        if self.foreground_package:
            lines.append(f"foreground_package: {self.foreground_package}")
        if self.resumed_activity_line:
            lines.append(f"resumed_activity: {self.resumed_activity_line[:300]}")
        if self.window_focus_line:
            lines.append(f"window_focus: {self.window_focus_line[:300]}")
        if self.logcat_lines:
            lines.append("logcat:")
            lines.extend(f"  {ln[:300]}" for ln in self.logcat_lines[:6])
        lines.append(f"poll_seconds: {self.poll_seconds:.1f}")
        lines.append(f"verified_success: {'yes' if self.success else 'no'}")
        if self.game_joined is not None:
            lines.append(f"game_joined: {'yes' if self.game_joined else 'no'}")
        if self.game_join_reason:
            lines.append(f"game_join_note: {self.game_join_reason}")
        if self.failure_reason:
            lines.append(f"failure: {self.failure_reason}")
        for cause in self.possible_causes[:4]:
            lines.append(f"possible_cause: {cause}")
        return lines

    def failure_message(self) -> str:
        if self.success:
            return ""
        base = self.failure_reason or "launch verification failed"
        detail = self.summary_lines()
        return base + "\n" + "\n".join(detail[:16])


def root_preflight_error() -> str:
    report = root_access.root_required_preflight()
    if report.ok:
        return ""
    return report.public_error()


def _root_shell(cmd: str, *, timeout: int = 8) -> root_access.RootResult:
    return root_access.run_root(cmd, timeout=timeout)


def resolve_launcher_activity(package: str) -> tuple[str, bool, str]:
    package = validate_package_name(package)
    list_res = _root_shell(f"cmd package list packages --user current | grep -F {package} | head -1")
    if list_res.ok and package not in (list_res.stdout or ""):
        return "", False, f"package not installed for current user: {package}"
    if not android.package_installed(package):
        return "", False, f"package not installed: {package}"

    resolve = _root_shell(
        f"cmd package resolve-activity --brief --user current -a android.intent.action.MAIN "
        f"-c android.intent.category.LAUNCHER {package} 2>/dev/null | tail -1"
    )
    component = (resolve.stdout or "").strip().splitlines()[-1].strip() if resolve.stdout else ""
    if component and "/" in component and package in component:
        return component, True, ""
    dump = _root_shell(f"dumpsys package {package} 2>/dev/null | grep -E 'android.intent.action.MAIN' | head -3")
    if dump.stdout and "MAIN" in dump.stdout:
        return "", True, ""
    return "", False, "no_launcher_activity"


def collect_process_evidence(package: str) -> dict[str, Any]:
    package = validate_package_name(package)
    out: dict[str, Any] = {
        "root_pidof": "",
        "root_ps": "",
        "root_proc_exact": "",
        "root_running": False,
        "foreground": False,
        "resumed_line": "",
        "window_line": "",
    }
    pidof = _root_shell(f"pidof {package} 2>/dev/null || true")
    out["root_pidof"] = (pidof.stdout or "").strip()[:120]
    ps = _root_shell(shlex.join(android.process_ps_scan_args(package)))
    out["root_ps"] = (ps.stdout or "").strip()[:200]
    proc = _root_shell(shlex.join(android.process_cmdline_scan_args(package)), timeout=10)
    out["root_proc_exact"] = (proc.stdout or "").strip()[:200]
    out["root_running"] = bool(
        out["root_pidof"]
        or android.ps_output_has_live_package(out["root_ps"], package)
        or out["root_proc_exact"]
    )

    activity = _root_shell(
        "dumpsys activity activities 2>/dev/null | "
        "grep -E 'mResumedActivity|topResumedActivity|ResumedActivity' | head -5"
    )
    for line in (activity.stdout or "").splitlines():
        if package in line:
            out["resumed_line"] = line.strip()[:300]
            out["foreground"] = True
            break
    window = _root_shell(
        "dumpsys window 2>/dev/null | grep -E 'mCurrentFocus|mFocusedApp' | head -5"
    )
    for line in (window.stdout or "").splitlines():
        if package in line:
            out["window_line"] = line.strip()[:300]
            out["foreground"] = True
            break
    processes = _root_shell(
        f"dumpsys activity processes 2>/dev/null | grep -F {package} | head -5 || true"
    )
    if processes.stdout.strip():
        out["root_running"] = True
    return out


def _foreground_lines(package: str) -> tuple[str | None, str, str]:
    fg_pkg = None
    resumed = ""
    focus = ""
    pe = collect_process_evidence(package)
    resumed = str(pe.get("resumed_line") or "")
    focus = str(pe.get("window_line") or "")
    if pe.get("foreground"):
        fg_pkg = package
    if not fg_pkg:
        try:
            fg_pkg = android.current_foreground_package()
        except Exception:  # noqa: BLE001
            pass
    return fg_pkg, resumed, focus


def _recent_logcat_for_package(package: str, *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    res = _root_shell(
        "logcat -d -t 300 2>/dev/null | "
        "grep -E 'ActivityTaskManager|ActivityManager|AndroidRuntime|FATAL|ANR' | tail -80"
    )
    if not res.ok and not res.stdout:
        return lines
    for line in reversed((res.stdout or "").splitlines()):
        low = line.lower()
        if package not in line and "fatal" not in low and "crash" not in low:
            continue
        lines.append(line.strip()[:400])
        if len(lines) >= limit:
            break
    return list(reversed(lines))


def launch_package_root(
    package: str,
    *,
    activity: str = "",
    force_stop: bool = False,
) -> tuple[android.CommandResult, str]:
    package = validate_package_name(package)
    pre = root_access.root_required_preflight()
    if not pre.ok:
        return android.CommandResult(
            ("su", "-c", "launch"),
            126,
            "",
            pre.public_error(),
        ), "root_preflight_failed"

    component = activity or resolve_launcher_activity(package)[0]
    if force_stop:
        _root_shell(f"am force-stop {package}")

    if component and "/" in component:
        cmd = f"am start -W --user current -n {component}"
        res = _root_shell(cmd, timeout=20)
        if res.ok:
            return android.CommandResult((pre.tool or "su", "-c", cmd), 0, res.stdout, res.stderr), "root_am_start_n"
    cmd = (
        "am start -W --user current "
        "-a android.intent.action.MAIN "
        "-c android.intent.category.LAUNCHER "
        f"-p {package}"
    )
    res = _root_shell(cmd, timeout=20)
    return android.CommandResult(
        (pre.tool or "su", "-c", cmd),
        res.returncode,
        res.stdout,
        res.stderr or res.error,
    ), "root_am_start_main"
    return android.CommandResult(
        ("su", "-c", "launch"),
        1,
        "",
        "no_launcher_activity",
    ), "no_launcher_activity"


def _has_launch_proof(
    package: str,
    process_evidence: dict[str, Any],
    fg_pkg: str | None,
    resumed: str,
    focus: str,
) -> bool:
    if process_evidence.get("root_running"):
        return True
    if fg_pkg and (fg_pkg == package or package in fg_pkg):
        return True
    if resumed and package in resumed:
        return True
    if focus and package in focus:
        return True
    return False


def verify_launch(
    package: str,
    *,
    launch_result: android.CommandResult | None = None,
    launch_method: str = "",
    wait_seconds: float = 20.0,
    poll_interval: float = 0.75,
    require_launch_command_ok: bool = True,
    private_url: str | None = None,
) -> LaunchVerificationResult:
    pre_err = root_preflight_error()
    package = validate_package_name(package)
    component, launchable, not_launch_reason = resolve_launcher_activity(package)
    result = LaunchVerificationResult(
        package=package,
        success=False,
        resolved_activity=component,
        launchable=launchable,
        not_launchable_reason=not_launch_reason,
        launch_method=launch_method,
        root_used=True,
    )
    if pre_err:
        result.failure_reason = pre_err
        result.launchable = False
        return result
    if not launchable:
        result.failure_reason = not_launch_reason or "no_launcher_activity"
        return result

    if launch_result is not None:
        result.launch_returncode = launch_result.returncode
        result.launch_stdout = launch_result.stdout or ""
        result.launch_stderr = launch_result.stderr or ""
        result.launch_command = " ".join(str(a) for a in launch_result.args)
        if require_launch_command_ok and not launch_result.ok:
            result.failure_reason = launch_result.summary or "launch command failed"
            result.process_evidence = collect_process_evidence(package)
            return result

    deadline = time.monotonic() + max(5.0, min(float(wait_seconds), 30.0))
    started = time.monotonic()
    last_pe: dict[str, Any] = {}
    max_poll_iterations = 15
    for _poll_idx in range(max_poll_iterations):
        if time.monotonic() >= deadline:
            break
        last_pe = collect_process_evidence(package)
        fg_pkg, resumed, focus = _foreground_lines(package)
        result.process_evidence = last_pe
        result.foreground_package = fg_pkg
        result.resumed_activity_line = resumed
        result.window_focus_line = focus
        if _has_launch_proof(package, last_pe, fg_pkg, resumed, focus):
            result.success = True
            result.poll_seconds = time.monotonic() - started
            result.logcat_lines = _recent_logcat_for_package(package)
            if private_url:
                result.game_joined = None
                result.game_join_reason = (
                    "launch success; game join not externally verifiable without place/server proof"
                )
            else:
                result.game_joined = None
                result.game_join_reason = "package launch only (no deep link configured)"
            return result
        time.sleep(max(0.25, min(float(poll_interval), 2.0)))

    result.poll_seconds = time.monotonic() - started
    result.logcat_lines = _recent_logcat_for_package(package)
    log_text = "\n".join(result.logcat_lines).lower()
    fg_now = result.foreground_package or ""
    if re.search(r"(fatal exception|has died|force finishing).*?" + re.escape(package), log_text):
        result.failure_reason = "launched_then_crashed"
        result.possible_causes.append("logcat shows crash/force-finish for target package")
    elif fg_now and package not in fg_now:
        result.failure_reason = "foreground_not_target"
        result.possible_causes.append(f"foreground is {fg_now}, not {package}")
    elif launch_result is not None and launch_result.ok:
        result.failure_reason = "launch_accepted_but_not_alive"
        result.possible_causes.extend([
            "root process checks found no pid/ps match",
            "resumed/window dumpsys did not show target package",
            "app may have exited immediately after launch",
        ])
    else:
        result.failure_reason = "launch verification failed: no root process or foreground evidence"
    return result


def doctor_package_report(package: str) -> list[str]:
    package = validate_package_name(package)
    lines: list[str] = [f"Doctor: {package}", ""]
    pre = root_access.root_required_preflight()
    lines.append(f"  root_available: {'yes' if pre.ok else 'no'}")
    lines.append(f"  root_uid: {pre.uid or '-'}")
    if not pre.ok:
        lines.append(f"  root_error: {pre.public_error()}")
        return lines
    component, launchable, nl_reason = resolve_launcher_activity(package)
    lines.append(f"  installed: {'yes' if android.package_installed(package) else 'no'}")
    lines.append(f"  launchable: {'yes' if launchable else 'no'}")
    if nl_reason:
        lines.append(f"  launchability: {nl_reason}")
    if component:
        lines.append(f"  launcher_activity: {component}")
    from . import package_username as _pu

    scan = _pu.scan_package_username_root(package)
    lines.append(f"  username: {scan.username or 'unknown'}")
    lines.append(f"  username_source: {scan.source}")
    lines.append(f"  username_confidence: {scan.confidence or '-'}")
    if scan.reason:
        lines.append(f"  username_reason: {scan.reason}")
    lines.append(f"  root_read_status: {scan.root_read_status or '-'}")
    pe = collect_process_evidence(package)
    lines.append(
        f"  process_before_launch: root_running={pe.get('root_running')} "
        f"root_pidof={pe.get('root_pidof') or '-'} foreground={pe.get('foreground')}"
    )
    fg, resumed, focus = _foreground_lines(package)
    lines.append(f"  foreground_before_launch: {fg or '-'}")
    if resumed:
        lines.append(f"  resumed_before_launch: {resumed[:180]}")
    launch_cmd = (
        "am start -W --user current -a android.intent.action.MAIN "
        f"-c android.intent.category.LAUNCHER -p {package}"
    )
    if component:
        launch_cmd = f"am start -W --user current -n {component}"
    lines.append(f"  launch_command: su -c '{launch_cmd}'")
    if not launchable:
        lines.append("  status: NOT OK — not launchable")
    else:
        lines.append("  status: root preflight OK; ready to launch")
    return lines
