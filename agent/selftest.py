"""One-command live proof: root scan + launch + probe upload."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

from . import android, build_info, launch_verify, package_username, root_access
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


def run_selftest(
    *,
    package: str = "",
    first: bool = False,
    upload: bool = False,
    summary_probe: bool = True,
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

    build = build_info.collect_version_info()
    pre = root_access.root_required_preflight()
    if not pre.ok:
        blocking.append(pre.public_error())
        return SelftestResult(
            False,
            selected,
            blocking_errors=blocking,
            summary={
                "product_version": build.get("product_version"),
                "artifact_sha256": build.get("artifact_sha256_short"),
                "root_available": False,
            },
        )

    username_report = package_username.scan_package_username_root(selected)
    if not username_report.username and username_report.reason:
        blocking.append(username_report.reason)

    doctor_lines = launch_verify.doctor_package_report(selected)
    launch_result, launch_method = launch_verify.launch_package_root(selected)
    verification = launch_verify.verify_launch(
        selected,
        launch_result=launch_result,
        launch_method=launch_method,
        wait_seconds=20.0,
    )
    if not verification.success:
        blocking.append(verification.failure_reason or "launch failed")

    launch_block = {
        "attempted": True,
        "state": "success" if verification.success else "failed",
        "reason": verification.failure_reason or "",
        "method": launch_method,
        "strong_success_evidence": verification.success,
        "foreground_package": verification.foreground_package,
        "process_evidence": verification.process_evidence,
        "game_joined": verification.game_joined,
        "game_join_reason": verification.game_join_reason,
    }
    username_block = {
        "package": selected,
        "username": username_report.username,
        "source": username_report.source,
        "root_used": username_report.root_used,
        "confidence": username_report.confidence,
        "reason": username_report.reason,
        "root_read_status": username_report.root_read_status,
    }
    summary = {
        "probe_id": "",
        "product_version": build.get("product_version"),
        "artifact_sha256": build.get("artifact_sha256_short"),
        "git_commit": build.get("git_commit_short"),
        "root_available": True,
        "root_required_mode": True,
        "root_uid": pre.uid,
        "packages_found": 1,
        "usernames_found": 1 if username_report.username else 0,
        "selected_package": selected,
        "last_command": f"selftest --package {selected}",
        "launch_attempted": True,
        "launch_state": launch_block["state"],
        "launch_reason": launch_block["reason"],
        "strong_success_evidence": verification.success,
        "blocking_errors": blocking,
        "doctor_lines": doctor_lines[:20],
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
                "username_scan": username_block,
                "launch": launch_block,
                "doctor_lines": doctor_lines,
            },
            last_command=f"selftest --package {selected}",
        )
        probe_doc["summary"] = summary
        ok, info = _probe.upload_probe(probe_doc)
        if ok:
            probe_id = info
            probe_url = f"https://rejoin.deng.my.id/api/dev-probe/{info}"
            summary["probe_id"] = probe_id
        else:
            blocking.append(f"probe upload failed: {info}")

    ok = verification.success and pre.ok and (not blocking or all(
        "game join not externally verifiable" in b for b in blocking
    ))
    return SelftestResult(
        ok=verification.success and pre.ok and not any(
            x for x in blocking if "upload failed" in x or "root is required" in x
        ),
        package=selected,
        probe_id=probe_id,
        probe_url=probe_url,
        blocking_errors=blocking,
        summary=summary,
        launch=launch_block,
        username_scan=username_block,
    )


def print_selftest_report(result: SelftestResult) -> None:
    sys.stdout.write(f"selftest package: {result.package}\n")
    for key, value in result.summary.items():
        if key == "doctor_lines":
            continue
        sys.stdout.write(f"{key}: {value}\n")
    sys.stdout.write(f"username: {result.username_scan.get('username') or 'unknown'}\n")
    sys.stdout.write(f"username_source: {result.username_scan.get('source')}\n")
    sys.stdout.write(f"launch_state: {result.launch.get('state')}\n")
    if result.launch.get("reason"):
        sys.stdout.write(f"launch_reason: {result.launch.get('reason')}\n")
    if result.probe_id:
        sys.stdout.write(f"probe_id: {result.probe_id}\n")
        sys.stdout.write(f"probe_url: {result.probe_url}\n")
    if result.blocking_errors:
        sys.stdout.write("blocking_errors:\n")
        for err in result.blocking_errors:
            sys.stdout.write(f"  - {err}\n")
