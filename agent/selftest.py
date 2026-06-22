"""One-command live proof: root scan + state scan + launch + probe upload."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from . import android, build_info, launch_verify, package_state, package_username, root_access
from .config import load_config, validate_package_name


@dataclass
class SelftestResult:
    ok: bool
    package: str
    probe_id: str = ""
    probe_url: str = ""
    blocking_errors: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    launch: dict[str, Any] = field(default_factory=dict)
    username_scan: dict[str, Any] = field(default_factory=dict)
    kill_relaunch: dict[str, Any] = field(default_factory=dict)


def _pick_first_package_with_username(cfg: dict[str, Any] | None) -> str:
    from .config import DEFAULT_ROBLOX_PACKAGE_HINTS

    hints = list(DEFAULT_ROBLOX_PACKAGE_HINTS)
    if cfg and isinstance(cfg.get("package_detection"), dict):
        raw = cfg["package_detection"].get("hints")
        if isinstance(raw, list) and raw:
            hints = [str(x) for x in raw if str(x).strip()]
    candidates = android.discover_roblox_package_candidates(hints, include_launchable_only=True)
    if not candidates:
        for pkg in android.find_roblox_packages(hints):
            candidates.append(android.RobloxPackageCandidate(package=pkg, app_name=pkg, launchable=True))
    for candidate in candidates:
        scan = package_username.scan_package_username_root(candidate.package)
        if scan.username:
            return candidate.package
    if candidates:
        return candidates[0].package
    return ""


def _discover_packages(cfg: dict[str, Any] | None) -> list[str]:
    from .config import DEFAULT_ROBLOX_PACKAGE_HINTS, enabled_package_names

    if cfg:
        names = enabled_package_names(cfg)
        if names:
            return names
    hints = list(DEFAULT_ROBLOX_PACKAGE_HINTS)
    if cfg and isinstance(cfg.get("package_detection"), dict):
        raw = cfg["package_detection"].get("hints")
        if isinstance(raw, list) and raw:
            hints = [str(x) for x in raw if str(x).strip()]
    candidates = android.discover_roblox_package_candidates(hints, include_launchable_only=True)
    if candidates:
        return [c.package for c in candidates]
    return android.find_roblox_packages(hints)


def _username_probe_rows(packages: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pkg in packages:
        row = package_username.username_display_for_package(pkg)
        rows.append(
            {
                "package": pkg,
                "username_display": row.username_display,
                "account_status": row.account_status,
                "username_source": row.username_source,
                "reason": row.reason,
            }
        )
    return rows


def _state_probe_rows(states: dict[str, package_state.PackageStateRow]) -> list[dict[str, Any]]:
    return [package_state.row_to_probe_dict(states[pkg]) for pkg in sorted(states.keys())]


def _poll_online(
    package: str,
    *,
    timeout_seconds: float = 30.0,
    poll_interval: float = 1.0,
) -> package_state.PackageStateRow:
    deadline = time.time() + timeout_seconds
    last = package_state.scan_package_state_root(package)
    while time.time() < deadline:
        last = package_state.scan_package_state_root(package)
        if last.state == package_state.STATE_ONLINE:
            return last
        if last.state in {package_state.STATE_CRASHED, package_state.STATE_BLOCKED, package_state.STATE_NOT_LAUNCHABLE}:
            return last
        time.sleep(poll_interval)
    return last


def run_selftest(
    *,
    package: str = "",
    first: bool = False,
    upload: bool = False,
    summary_probe: bool = True,
    kill_relaunch: bool = False,
) -> SelftestResult:
    blocking: list[str] = []
    selected = (package or "").strip()
    cfg = None
    try:
        cfg = load_config()
    except Exception:  # noqa: BLE001
        cfg = None

    if first and not selected:
        selected = _pick_first_package_with_username(cfg)
    if not selected:
        return SelftestResult(False, "", blocking_errors=["no package selected or detected"])

    try:
        selected = validate_package_name(selected)
    except Exception as exc:  # noqa: BLE001
        return SelftestResult(False, selected, blocking_errors=[str(exc)])

    packages = _discover_packages(cfg)
    if selected not in packages:
        packages = [selected] + [p for p in packages if p != selected]

    build = build_info.collect_version_info()
    pre = root_access.root_required_preflight()
    if not pre.ok:
        blocking.append(pre.public_error())
        return SelftestResult(
            False,
            selected,
            blocking_errors=blocking,
            summary={
                "version": build.get("product_version"),
                "product_version": build.get("product_version"),
                "artifact_sha256": build.get("artifact_sha256_short"),
                "build_commit": build.get("git_commit_short"),
                "root_available": False,
            },
        )

    username_rows = _username_probe_rows(packages)
    for row in username_rows:
        if row["username_display"] == "Unknown":
            blocking.append(f"{row['package']}: forbidden Unknown username")

    states_before = package_state.scan_all_package_states_root(packages)
    doctor_lines = launch_verify.doctor_package_report(selected)

    launch_result, launch_method = launch_verify.launch_package_root(selected)
    package_state.record_launch_attempt(
        selected,
        command=str(getattr(launch_result, "args", ())),
        rc=int(getattr(launch_result, "returncode", -1)),
        ok=bool(getattr(launch_result, "ok", False)),
        failure_reason="" if getattr(launch_result, "ok", False) else "launch command failed",
    )
    verification = launch_verify.verify_launch(
        selected,
        launch_result=launch_result,
        launch_method=launch_method,
        wait_seconds=20.0,
    )
    after_launch_row = _poll_online(selected, timeout_seconds=30.0)
    states_after = package_state.scan_all_package_states_root(packages)

    if not verification.success and after_launch_row.state != package_state.STATE_ONLINE:
        blocking.append(verification.failure_reason or "launch failed")
    if after_launch_row.state != package_state.STATE_ONLINE:
        blocking.append(
            f"selected package not online after launch: {after_launch_row.state} ({after_launch_row.reason})"
        )

    launch_block = {
        "package": selected,
        "attempted": True,
        "command": verification.launch_command or str(getattr(launch_result, "args", "")),
        "rc": verification.launch_returncode,
        "state_after": after_launch_row.state,
        "success_evidence": after_launch_row.state == package_state.STATE_ONLINE,
        "failure_reason": verification.failure_reason or "",
        "method": launch_method,
        "strong_success_evidence": after_launch_row.state == package_state.STATE_ONLINE,
        "foreground_package": verification.foreground_package,
        "process_evidence": verification.process_evidence,
    }

    kill_block: dict[str, Any] = {}
    if kill_relaunch:
        try:
            android.force_stop_package(selected)
        except Exception:  # noqa: BLE001
            pass
        try:
            root_access.run_root(f"am force-stop {selected}", timeout=6)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5)
        after_kill = package_state.scan_package_state_root(selected)
        if after_kill.state == package_state.STATE_LAUNCHING:
            blocking.append("after kill state still launching")
        if after_kill.state == package_state.STATE_ONLINE:
            blocking.append("after kill state still online")

        relaunch_result, relaunch_method = launch_verify.launch_package_root(selected)
        package_state.record_launch_attempt(
            selected,
            command=str(getattr(relaunch_result, "args", ())),
            rc=int(getattr(relaunch_result, "returncode", -1)),
            ok=bool(getattr(relaunch_result, "ok", False)),
        )
        relaunch_verify = launch_verify.verify_launch(
            selected,
            launch_result=relaunch_result,
            launch_method=relaunch_method,
            wait_seconds=20.0,
        )
        after_relaunch = _poll_online(selected, timeout_seconds=30.0)
        states_after = package_state.scan_all_package_states_root(packages)
        kill_block = {
            "killed": selected,
            "state_after_kill": after_kill.state,
            "relaunch_attempted": True,
            "state_after_relaunch": after_relaunch.state,
            "relaunch_success_evidence": after_relaunch.state == package_state.STATE_ONLINE,
            "relaunch_failure_reason": relaunch_verify.failure_reason or "",
        }
        if after_relaunch.state != package_state.STATE_ONLINE:
            blocking.append(
                f"after relaunch state not online: {after_relaunch.state} ({after_relaunch.reason})"
            )

    selected_username = next((r for r in username_rows if r["package"] == selected), {})
    summary = {
        "probe_id": "",
        "version": build.get("product_version"),
        "product_version": build.get("product_version"),
        "artifact_sha256": build.get("artifact_sha256_short"),
        "build_commit": build.get("git_commit_short"),
        "root_available": True,
        "root_required_mode": True,
        "root_uid": pre.uid,
        "packages_total": len(packages),
        "packages_found": len(packages),
        "selected_package": selected,
        "command": f"selftest --package {selected}" + (" --kill-relaunch" if kill_relaunch else ""),
        "usernames": username_rows,
        "states_before": _state_probe_rows(states_before),
        "launch_attempt": launch_block,
        "states_after": _state_probe_rows(states_after),
        "kill_relaunch": kill_block or None,
        "launch_attempted": True,
        "launch_state": launch_block["state_after"],
        "strong_success_evidence": launch_block["success_evidence"],
        "blocking_errors": blocking,
        "doctor_lines": doctor_lines[:12],
    }

    probe_id = ""
    probe_url = ""
    if upload and os.name != "nt":
        from . import probe as _probe

        probe_doc = _probe.collect_probe(
            include_heavy=False,
            mode="summary" if summary_probe else "full",
            selftest={
                "package": selected,
                "username_scan": selected_username,
                "usernames": username_rows,
                "states_before": summary["states_before"],
                "states_after": summary["states_after"],
                "launch": launch_block,
                "launch_attempt": launch_block,
                "kill_relaunch": kill_block or None,
                "doctor_lines": doctor_lines[:12],
            },
            last_command=summary["command"],
        )
        probe_doc["summary"] = summary
        ok, info = _probe.upload_probe(probe_doc)
        if ok:
            probe_id = info
            probe_url = f"https://rejoin.deng.my.id/api/dev-probe/{info}"
            summary["probe_id"] = probe_id
        else:
            blocking.append(f"probe upload failed: {info}")

    ok = (
        pre.ok
        and launch_block["success_evidence"]
        and not any("Unknown" in b for b in blocking)
        and not any("upload failed" in b for b in blocking)
        and (not kill_relaunch or bool(kill_block.get("relaunch_success_evidence")))
    )
    return SelftestResult(
        ok=ok,
        package=selected,
        probe_id=probe_id,
        probe_url=probe_url,
        blocking_errors=blocking,
        summary=summary,
        launch=launch_block,
        username_scan=selected_username,
        kill_relaunch=kill_block,
    )


def print_selftest_report(result: SelftestResult) -> None:
    sys.stdout.write(f"selftest package: {result.package}\n")
    for key, value in result.summary.items():
        if key in {"doctor_lines", "usernames", "states_before", "states_after", "launch_attempt"}:
            continue
        sys.stdout.write(f"{key}: {value}\n")
    user = result.username_scan or {}
    sys.stdout.write(f"username_display: {user.get('username_display')}\n")
    sys.stdout.write(f"username_source: {user.get('username_source')}\n")
    sys.stdout.write(f"launch_state: {result.launch.get('state_after')}\n")
    if result.launch.get("failure_reason"):
        sys.stdout.write(f"launch_reason: {result.launch.get('failure_reason')}\n")
    if result.kill_relaunch:
        sys.stdout.write(f"kill_relaunch: {result.kill_relaunch}\n")
    if result.probe_id:
        sys.stdout.write(f"probe_id: {result.probe_id}\n")
        sys.stdout.write(f"probe_url: {result.probe_url}\n")
    if result.blocking_errors:
        sys.stdout.write("blocking_errors:\n")
        for err in result.blocking_errors:
            sys.stdout.write(f"  - {err}\n")
