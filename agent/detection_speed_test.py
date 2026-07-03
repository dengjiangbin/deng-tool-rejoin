"""Live Lime-style detection speed test runner + dev-probe proof builder."""

from __future__ import annotations

import time
from typing import Any, Callable

from .lime_detection_speed import (
    get_active_lime_tracker,
    probe_lime_detection_speed_snapshot,
)


def _fmt_ts(at: float | None) -> str:
    if at is None:
        return "-"
    try:
        return f"{float(at):.3f}"
    except (TypeError, ValueError):
        return "—"


def _latency_ok(ms: float | None, target_ms: float = 1000.0) -> str:
    if ms is None:
        return "pending"
    return "PASS" if ms <= target_ms else "FAIL"


def format_speed_test_report(snap: dict[str, Any]) -> list[str]:
    lines = [
        "DENG Tool: Rejoin — LIME DETECTION SPEED TEST",
        f"  Tracker enabled:        {'yes' if snap.get('enabled') else 'no'}",
        f"  Cookie auto-extract:    {'disabled' if not snap.get('cookie_auto_extract') else 'ENABLED'}",
        f"  Launch requires cookie: {'no' if not snap.get('launch_requires_cookie') else 'yes'}",
        f"  Process poll interval:  {snap.get('process_poll_interval_ms', '—')} ms",
    ]
    pkgs = snap.get("packages") or {}
    if not isinstance(pkgs, dict) or not pkgs:
        lines.append("  Packages:               (no live session — start deng-rejoin first)")
        reason = snap.get("reason") or ""
        if reason:
            lines.append(f"  Note:                   {reason}")
        return lines
    for pkg, row in pkgs.items():
        if not isinstance(row, dict):
            continue
        lines.append(f"  Package:                {pkg}")
        lines.append(f"    process_dead_detected_at:     {_fmt_ts(row.get('process_dead_detected_at'))}")
        lines.append(f"    logcat_dead_detected_at:      {_fmt_ts(row.get('logcat_dead_detected_at'))}")
        lines.append(f"    ocr_dead_detected_at:         {_fmt_ts(row.get('ocr_dead_detected_at'))}")
        lines.append(f"    online_evidence_at:           {_fmt_ts(row.get('online_evidence_at'))}")
        lines.append(
            f"    checking_committed_state_at:  {_fmt_ts(row.get('checking_committed_state_at'))}"
        )
        lines.append(f"    recovery_requested_at:        {_fmt_ts(row.get('recovery_requested_at'))}")
        lines.append(f"    detection_latency_ms:         {row.get('detection_latency_ms', '—')}")
        lines.append(
            f"    dead latency target (≤1s):    "
            f"{_latency_ok(row.get('detection_latency_ms'))}"
        )
        lines.append(
            f"    online latency target (≤1s):  "
            f"{_latency_ok(row.get('online_latency_ms'))}"
        )
        ocr = row.get("ocr") or {}
        if isinstance(ocr, dict):
            lines.append(
                f"    OCR backend:                  {ocr.get('ocr_backend') or '—'} "
                f"(available={ocr.get('ocr_available')})"
            )
    return lines


def build_probe_proof_section(snap: dict[str, Any]) -> dict[str, Any]:
    pkgs = snap.get("packages") or {}
    primary: dict[str, Any] = {}
    if isinstance(pkgs, dict) and pkgs:
        primary = next(iter(pkgs.values()), {}) if isinstance(next(iter(pkgs.values())), dict) else {}
    build_meta: dict[str, Any] = {}
    keyless_start_ok = False
    try:
        from .build_info import collect_version_info
        from .license import is_test_license_bypass_active

        build_meta = collect_version_info() or {}
        ch = str(build_meta.get("channel") or "").strip().lower()
        keyless_start_ok = ch in {"test-latest2", "test_latest2", "main-dev"} and (
            is_test_license_bypass_active() or ch == "test-latest2"
        )
    except Exception:  # noqa: BLE001
        pass
    return {
        "scenario": "lime_detection_speed_live",
        "captured_at": time.time(),
        "channel": build_meta.get("channel") or snap.get("channel"),
        "artifact_sha256": build_meta.get("artifact_sha256") or snap.get("artifact_sha256"),
        "source_version": build_meta.get("source_version") or snap.get("source_version") or "v1.3.0",
        "keyless_start_ok": keyless_start_ok,
        "enabled": bool(snap.get("enabled")),
        "cookie_auto_extract": bool(snap.get("cookie_auto_extract")),
        "process_dead_detected_at": primary.get("process_dead_detected_at"),
        "logcat_dead_detected_at": primary.get("logcat_dead_detected_at"),
        "ocr_dead_detected_at": primary.get("ocr_dead_detected_at"),
        "online_evidence_at": primary.get("online_evidence_at"),
        "checking_committed_state_at": primary.get("checking_committed_state_at"),
        "recovery_requested_at": primary.get("recovery_requested_at"),
        "detection_latency_ms": primary.get("detection_latency_ms"),
        "online_latency_ms": primary.get("online_latency_ms"),
        "recovery_latency_ms": primary.get("recovery_latency_ms"),
        "targets_ms": {
            "process_dead": 1000,
            "logcat_dead": 1000,
            "online": 1000,
            "recovery_after_checking": 1000,
        },
        "packages": pkgs,
    }


def run_speed_test(
    *,
    package: str = "",
    scenario: str = "",
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """Collect current Lime speed snapshot; optionally arm a scenario baseline."""
    snap = probe_lime_detection_speed_snapshot(clock=clock)
    pkg = str(package or "").strip()
    if pkg and scenario:
        live = get_active_lime_tracker()
        if live is not None:
            live.set_evidence_baseline(pkg, at=(clock or time.time)())
    snap["armed_scenario"] = scenario or None
    snap["armed_package"] = pkg or None
    return snap


def run_speed_test_cli(
    *,
    package: str = "",
    scenario: str = "",
    upload_probe: bool = False,
) -> int:
    snap = run_speed_test(package=package, scenario=scenario)
    for line in format_speed_test_report(snap):
        print(line)
    if not upload_probe:
        print("")
        print("  Tip: run with --upload-probe to POST dev-probe proof timestamps.")
        return 0 if snap.get("enabled") else 1
    from . import probe as probe_mod

    proof = build_probe_proof_section(snap)
    try:
        doc = probe_mod.collect_probe()
    except Exception as exc:  # noqa: BLE001
        doc = {"probe_version": 1, "errors": [{"step": "collect_probe", "error": str(exc)[:120]}]}
    doc["lime_detection_speed"] = snap
    doc["lime_detection_speed_live_test"] = proof
    doc["lime_detection_speed_proof"] = {
        k: proof[k]
        for k in (
            "channel",
            "artifact_sha256",
            "source_version",
            "keyless_start_ok",
            "process_dead_detected_at",
            "logcat_dead_detected_at",
            "ocr_dead_detected_at",
            "online_evidence_at",
            "checking_committed_state_at",
            "recovery_requested_at",
            "detection_latency_ms",
        )
    }
    try:
        ok, detail = probe_mod.upload_probe(doc)
    except Exception as exc:  # noqa: BLE001
        print(f"  Probe upload:           ERROR ({exc.__class__.__name__})")
        return 1
    if ok:
        probe_id = detail if isinstance(detail, str) and detail.startswith("p-") else detail
        print("")
        print(f"  probe_id:               {probe_id}")
        print("  Share this id for dev-probe proof review.")
        return 0
    print(f"  Probe upload:           FAILED ({detail})")
    return 1
