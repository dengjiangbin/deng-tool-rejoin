"""Multi-step Android launch verification with honest evidence.

``am start`` returning exit code 0 is never treated as proof of launch.
Success requires process and/or foreground/resumed-activity evidence after
polling.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from . import android
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

    def summary_lines(self) -> list[str]:
        lines = [
            f"package: {self.package}",
            f"launchable: {'yes' if self.launchable else 'no'}",
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
            f"pidof={pe.get('pidof', '')!s} "
            f"pgrep={pe.get('pgrep', '')!s} "
            f"proc_scan={pe.get('proc_scan', '')!s} "
            f"root_running={pe.get('root_running', False)!s} "
            f"running={pe.get('running', False)!s}"
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
        return base + "\n" + "\n".join(detail[:14])


def resolve_launcher_activity(package: str) -> tuple[str, bool, str]:
    """Return (component_or_empty, launchable, reason_if_not)."""
    package = validate_package_name(package)
    if not android.package_installed(package):
        return "", False, f"package not installed: {package}"
    if not android.is_launchable_package(package):
        return "", False, "not launchable: no launcher activity found"
    cmd_bin = android._find_command("cmd", "/system/bin/cmd")  # noqa: SLF001
    if not cmd_bin:
        return "", True, ""
    res = android.run_command(
        [cmd_bin, "package", "resolve-activity", "--brief", package],
        timeout=android.PROCESS_TIMEOUT_SECONDS,
    )
    if not res.ok:
        return "", True, ""
    component = android._parse_activity_component(res.stdout, package)  # noqa: SLF001
    return component or "", True, ""


def collect_process_evidence(package: str) -> dict[str, Any]:
    """Run pidof/ps/proc/root checks; never raises."""
    package = validate_package_name(package)
    out: dict[str, Any] = {
        "pidof": "",
        "pgrep": "",
        "proc_scan": "",
        "ps_filtered": "",
        "running": False,
        "root_running": False,
    }
    try:
        pidof = android.run_command(["pidof", package], timeout=4)
        out["pidof"] = (pidof.stdout or "").strip() if pidof.ok else f"rc={pidof.returncode}"
    except Exception:  # noqa: BLE001
        pass
    try:
        pgrep = android.run_command(["pgrep", "-f", package], timeout=4)
        out["pgrep"] = (pgrep.stdout or "").strip() if pgrep.ok else f"rc={pgrep.returncode}"
    except Exception:  # noqa: BLE001
        pass
    try:
        scan = android.run_command(android.process_cmdline_scan_args(package), timeout=5)
        out["proc_scan"] = (scan.stdout or "").strip() if scan.ok else f"rc={scan.returncode}"
    except Exception:  # noqa: BLE001
        pass
    try:
        ps = android.run_command(["ps", "-A"], timeout=5)
        if ps.ok:
            hits = [ln for ln in ps.stdout.splitlines() if package in ln]
            out["ps_filtered"] = hits[0][:200] if hits else ""
    except Exception:  # noqa: BLE001
        pass
    try:
        alive = android.get_package_alive_evidence(package)
        out["running"] = bool(alive.get("running"))
        out["root_running"] = bool(alive.get("root_running"))
        out["foreground"] = bool(alive.get("foreground"))
        out["window"] = bool(alive.get("window"))
        out["alive"] = bool(alive.get("alive"))
    except Exception:  # noqa: BLE001
        pass
    return out


def _foreground_lines(package: str) -> tuple[str | None, str, str]:
    fg_pkg = None
    resumed = ""
    focus = ""
    try:
        fg_pkg = android.current_foreground_package()
    except Exception:  # noqa: BLE001
        pass
    for args, markers in (
        (("dumpsys", "activity", "activities"), ("mResumedActivity", "topResumedActivity", "ResumedActivity")),
        (("dumpsys", "window", "windows"), ("mCurrentFocus", "mFocusedApp")),
    ):
        try:
            res = android.run_command(list(args), timeout=4)
            if not res.ok:
                continue
            for line in res.stdout.splitlines():
                if package not in line:
                    continue
                for marker in markers:
                    if marker in line:
                        if marker in ("mResumedActivity", "topResumedActivity", "ResumedActivity"):
                            resumed = line.strip()[:400]
                        else:
                            focus = line.strip()[:400]
        except Exception:  # noqa: BLE001
            continue
    return fg_pkg, resumed, focus


def _recent_logcat_for_package(package: str, *, limit: int = 8) -> list[str]:
    lines: list[str] = []
    try:
        res = android.run_command(
            ["logcat", "-d", "-t", "120", "ActivityTaskManager:I", "ActivityManager:I", "*:S"],
            timeout=6,
        )
        if not res.ok:
            res = android.run_command(["logcat", "-d", "-t", "80"], timeout=6)
        if not res.ok:
            return lines
        for line in reversed(res.stdout.splitlines()):
            low = line.lower()
            if package not in line and "start" not in low and "crash" not in low:
                continue
            if package in line or "ActivityManager" in line or "ActivityTaskManager" in line:
                lines.append(line.strip()[:400])
            if len(lines) >= limit:
                break
    except Exception:  # noqa: BLE001
        pass
    return list(reversed(lines))


def _has_launch_proof(
    package: str,
    process_evidence: dict[str, Any],
    fg_pkg: str | None,
    resumed: str,
    focus: str,
) -> bool:
    if process_evidence.get("running") or process_evidence.get("root_running"):
        return True
    if process_evidence.get("proc_scan") and not str(process_evidence.get("proc_scan", "")).startswith("rc="):
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
    wait_seconds: float = 15.0,
    poll_interval: float = 0.5,
    require_launch_command_ok: bool = True,
) -> LaunchVerificationResult:
    """Poll after launch and return structured verification evidence."""
    package = validate_package_name(package)
    component, launchable, not_launch_reason = resolve_launcher_activity(package)
    result = LaunchVerificationResult(
        package=package,
        success=False,
        resolved_activity=component,
        launchable=launchable,
        not_launchable_reason=not_launch_reason,
        launch_method=launch_method,
    )
    if not launchable:
        result.failure_reason = not_launch_reason or "not launchable"
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

    deadline = time.monotonic() + max(3.0, min(float(wait_seconds), 25.0))
    last_pe: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last_pe = collect_process_evidence(package)
        fg_pkg, resumed, focus = _foreground_lines(package)
        result.process_evidence = last_pe
        result.foreground_package = fg_pkg
        result.resumed_activity_line = resumed
        result.window_focus_line = focus
        if _has_launch_proof(package, last_pe, fg_pkg, resumed, focus):
            result.success = True
            result.poll_seconds = max(0.0, max(3.0, min(float(wait_seconds), 25.0)) - (deadline - time.monotonic()))
            result.logcat_lines = _recent_logcat_for_package(package)
            return result
        time.sleep(max(0.25, float(poll_interval)))

    result.poll_seconds = max(3.0, min(float(wait_seconds), 25.0))
    result.logcat_lines = _recent_logcat_for_package(package)
    log_text = "\n".join(result.logcat_lines).lower()
    if re.search(r"(fatal|crash|died|force finishing).*?" + re.escape(package), log_text):
        result.failure_reason = "launched then exited/crashed"
        result.possible_causes.append("logcat shows crash/force-finish for target package")
    elif launch_result is not None and launch_result.ok:
        result.failure_reason = (
            "Android launch returned success but package process was not detected"
        )
        result.possible_causes.extend([
            "process may be hidden from Termux without root — root scan also found nothing",
            "app may have exited immediately after am start",
            "clone package may use a process name suffix; check dumpsys activity/window above",
        ])
    else:
        result.failure_reason = "launch verification failed: no process or foreground evidence"
    return result


def doctor_package_report(package: str) -> list[str]:
    """Human-readable diagnostics for ``deng-rejoin doctor --package``."""
    package = validate_package_name(package)
    lines: list[str] = [f"Doctor: {package}", ""]
    component, launchable, nl_reason = resolve_launcher_activity(package)
    lines.append(f"  installed: {'yes' if android.package_installed(package) else 'no'}")
    lines.append(f"  launchable: {'yes' if launchable else 'no'}")
    if nl_reason:
        lines.append(f"  launchability: {nl_reason}")
    if component:
        lines.append(f"  resolved_activity: {component}")
    pe = collect_process_evidence(package)
    lines.append(
        f"  process: running={pe.get('running')} root_running={pe.get('root_running')} "
        f"pidof={pe.get('pidof') or '-'} proc_scan={pe.get('proc_scan') or '-'}"
    )
    fg, resumed, focus = _foreground_lines(package)
    lines.append(f"  foreground: {fg or '-'}")
    if resumed:
        lines.append(f"  resumed: {resumed[:200]}")
    if focus:
        lines.append(f"  window_focus: {focus[:200]}")
    root = android.detect_root()
    lines.append(f"  root: {'yes (' + str(root.tool) + ')' if root.available else 'no'}")
    try:
        from . import package_username as _pu

        scan = _pu.scan_package_username(package)
        lines.append(f"  username_supported: {'yes' if scan.supported else 'no'}")
        if scan.username:
            lines.append(f"  username: {scan.username} ({scan.source})")
        elif scan.reason:
            lines.append(f"  username: unavailable — {scan.reason}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"  username_scan_error: {exc}")
    return lines
