"""Auto-detect resize orchestrator — mode, grid, safe XML writes, trace."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import android
from .package_identity import get_package_identity
from .resize_grid import calculate_resize_grid, validate_grid_bounds
from .resize_mode import detect_effective_resize_mode
from .resize_packages import get_trusted_resize_packages
from .resize_trace import append_resize_event
from .resize_xml import safe_write_resize_bounds
from .window_layout import WindowRect

_RATE_LIMIT_SECONDS = 45


@dataclass
class ResizePipelineResult:
    ok: bool
    skipped: bool = False
    skipped_reason: str = ""
    mode: str = ""
    confidence: str = ""
    basis: str = ""
    signals: dict[str, Any] = field(default_factory=dict)
    layout: dict[str, Any] = field(default_factory=dict)
    rects: list[WindowRect] = field(default_factory=list)
    packages: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
    trigger: str = "unknown"


def _left_offset_pixels(cfg: dict[str, Any], screen_width: int) -> int:
    pct = cfg.get("resize_left_offset_percent")
    if pct is None:
        frac = cfg.get("termux_dock_fraction")
        if frac is not None:
            try:
                pct = float(frac) * 100.0
            except (TypeError, ValueError):
                pct = 0.0
        else:
            pct = 0.0
    try:
        pct_f = max(0.0, min(90.0, float(pct)))
    except (TypeError, ValueError):
        pct_f = 0.0
    return int(round(screen_width * pct_f / 100.0))


def _rate_limited(cfg: dict[str, Any], trigger: str) -> bool:
    if trigger in {"manual", "startup"}:
        return False
    try:
        last = float(cfg.get("last_resize_at") or 0)
    except (TypeError, ValueError):
        last = 0.0
    return last > 0 and (time.time() - last) < _RATE_LIMIT_SECONDS


def run_resize_pipeline(
    cfg: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    trigger: str = "auto",
    force: bool = False,
    relaunch: bool = False,
) -> ResizePipelineResult:
    """Detect mode, compute grid, write bounds for trusted packages only."""
    if _rate_limited(cfg, trigger) and not force:
        return ResizePipelineResult(
            ok=True,
            skipped=True,
            skipped_reason="rate_limited",
            trigger=trigger,
        )

    trusted, _skipped_excluded = get_trusted_resize_packages(cfg, entries)
    if not trusted:
        event = {
            "last_resize_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "trigger": trigger,
            "package_source": "own_system",
            "package_count": 0,
            "skipped_reason": "no trusted packages",
            "summary": {"resized": 0, "already_correct": 0, "skipped": 0, "failed": 0},
        }
        append_resize_event(event)
        return ResizePipelineResult(
            ok=True,
            skipped=True,
            skipped_reason="no trusted packages",
            trigger=trigger,
        )

    mode_info = detect_effective_resize_mode(previous_mode=cfg.get("last_resize_mode"))
    mode = str(mode_info.get("mode") or "LANDSCAPE")
    norm = mode_info.get("signals", {}).get("physical_size_normalized", {})
    major = int(norm.get("major") or 0)
    minor = int(norm.get("minor") or 0)
    if major <= 0 or minor <= 0:
        disp = mode_info.get("signals", {}).get("logical_size", "1080x1920")
        try:
            w_s, h_s = str(disp).lower().split("x", 1)
            major = max(int(w_s), int(h_s))
            minor = min(int(w_s), int(h_s))
        except Exception:  # noqa: BLE001
            major, minor = 1920, 1080

    screen_width, screen_height = (
        (major, minor) if mode == "LANDSCAPE" else (minor, major)
    )
    left_offset = _left_offset_pixels(cfg, screen_width)

    rects, layout = calculate_resize_grid(
        trusted,
        mode=mode,
        major=major,
        minor=minor,
        left_offset=left_offset,
    )
    grid_errors = validate_grid_bounds(rects, layout["screen_width"], layout["screen_height"])
    if grid_errors:
        event = {
            "last_resize_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "trigger": trigger,
            "package_source": "own_system",
            "package_count": len(trusted),
            "mode": mode,
            "confidence": mode_info.get("confidence"),
            "basis": mode_info.get("basis"),
            "signals": mode_info.get("signals"),
            "layout": layout,
            "skipped_reason": "; ".join(grid_errors[:3]),
            "summary": {"resized": 0, "already_correct": 0, "skipped": len(trusted), "failed": 0},
        }
        append_resize_event(event)
        return ResizePipelineResult(
            ok=False,
            skipped=True,
            skipped_reason="invalid_grid: " + "; ".join(grid_errors[:3]),
            mode=mode,
            confidence=str(mode_info.get("confidence") or ""),
            basis=str(mode_info.get("basis") or ""),
            signals=dict(mode_info.get("signals") or {}),
            layout=layout,
            trigger=trigger,
        )

    root_info = android.detect_root()
    root_tool = root_info.tool if root_info.available else None
    pkg_results: list[dict[str, Any]] = []
    summary = {"resized": 0, "already_correct": 0, "skipped": 0, "failed": 0}

    rect_by_pkg = {r.package: r for r in rects}
    for pkg in trusted:
        rect = rect_by_pkg.get(pkg)
        if not rect:
            row = {"package": pkg, "status": "skipped", "reason": "no_rect"}
            summary["skipped"] += 1
            pkg_results.append(row)
            continue
        identity = get_package_identity(pkg) or {}
        account = str(identity.get("username") or "")
        row = safe_write_resize_bounds(
            pkg,
            rect,
            screen_mode=mode.lower(),
            root_tool=root_tool,
        )
        row["account"] = account
        status = str(row.get("status") or "failed")
        if status == "resized":
            summary["resized"] += 1
        elif status == "already_correct":
            summary["already_correct"] += 1
        elif status == "skipped":
            summary["skipped"] += 1
        else:
            summary["failed"] += 1
        pkg_results.append(row)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg["last_resize_at"] = time.time()
    cfg["last_resize_mode"] = mode
    cfg["last_layout_mode"] = mode.lower()
    cfg["last_layout_preview"] = [r.as_dict() for r in rects]
    cfg["_layout_rects"] = [r.as_dict() for r in rects]
    if layout.get("screen_width"):
        cfg["termux_dock_fraction"] = left_offset / max(1, int(layout["screen_width"]))

    event = {
        "last_resize_at": now_iso,
        "trigger": trigger,
        "package_source": "own_system",
        "package_count": len(trusted),
        "mode": mode,
        "confidence": mode_info.get("confidence"),
        "basis": mode_info.get("basis"),
        "signals": mode_info.get("signals"),
        "layout": layout,
        "packages": pkg_results,
        "summary": summary,
    }
    append_resize_event(event)

    return ResizePipelineResult(
        ok=summary["failed"] == 0,
        mode=mode,
        confidence=str(mode_info.get("confidence") or ""),
        basis=str(mode_info.get("basis") or ""),
        signals=dict(mode_info.get("signals") or {}),
        layout=layout,
        rects=rects,
        packages=pkg_results,
        summary=summary,
        trigger=trigger,
    )
