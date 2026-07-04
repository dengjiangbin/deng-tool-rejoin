"""Auto-detect resize orchestrator — mode, grid, safe XML writes, trace."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import android
from .package_identity import get_package_identity
from .resize_grid import calculate_resize_grid, validate_grid_bounds
from .resize_mode import detect_effective_resize_mode, resolve_runtime_screen_mode
from .resize_pb99 import calculate_pb99_grid, read_display_rotation, write_pb99_bounds_root
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
            pct = 40.0
    try:
        pct_f = max(0.0, min(90.0, float(pct)))
    except (TypeError, ValueError):
        pct_f = 0.0
    return int(round(screen_width * pct_f / 100.0))


def layout_package_set(entries: list[dict[str, Any]]) -> set[str]:
    """Return non-excluded package names from enabled config entries."""
    selected: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("enabled", True) is False:
            continue
        pkg = str(entry.get("package") or "").strip()
        if not pkg:
            continue
        from .window_layout import layout_exclusion_reason

        if layout_exclusion_reason(pkg):
            continue
        selected.add(pkg)
    return selected


def rects_cover_packages(rects: list[WindowRect], packages: set[str]) -> bool:
    if not packages:
        return True
    return {r.package for r in rects} == packages


def rects_from_cfg(
    cfg: dict[str, Any],
    packages: set[str],
) -> list[WindowRect]:
    """Load stored layout rects for ``packages`` when mode matches."""
    screen_mode = str(cfg.get("screen_mode") or "auto").lower()
    if screen_mode not in ("landscape", "portrait"):
        from .resize_mode import resolve_runtime_screen_mode

        screen_mode, _ = resolve_runtime_screen_mode(
            configured=screen_mode,
            previous_mode=cfg.get("last_resize_mode"),
        )
    last_mode = str(cfg.get("last_layout_mode") or "").lower()
    if last_mode and last_mode != screen_mode:
        return []
    stored = cfg.get("_layout_rects") or cfg.get("last_layout_preview")
    if not isinstance(stored, list):
        return []
    rects: list[WindowRect] = []
    for item in stored:
        if not isinstance(item, dict):
            continue
        pkg = str(item.get("package") or "").strip()
        if pkg not in packages:
            continue
        try:
            rects.append(
                WindowRect(
                    package=pkg,
                    left=int(item["left"]),
                    top=int(item["top"]),
                    right=int(item["right"]),
                    bottom=int(item["bottom"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rects


def compute_layout_rects(
    cfg: dict[str, Any],
    entries: list[dict[str, Any]] | None = None,
) -> tuple[list[WindowRect], dict[str, Any], str]:
    """Compute grid rects for trusted packages without writing XML."""
    trusted, _skipped = get_trusted_resize_packages(cfg, entries)
    runtime_mode, mode_info = resolve_runtime_screen_mode(
        configured=str(cfg.get("screen_mode") or "auto"),
        previous_mode=cfg.get("last_resize_mode"),
    )
    mode = "PORTRAIT" if runtime_mode == "portrait" else "LANDSCAPE"
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

    wm = android.get_wm_size()
    wm_w = int(wm.get("width") or screen_width or 0)
    wm_h = int(wm.get("height") or screen_height or 0)
    if wm_w <= 0 or wm_h <= 0:
        wm_w, wm_h = screen_width, screen_height

    rotation = read_display_rotation()
    try:
        rotation = int(mode_info.get("signals", {}).get("rotation") or rotation)
    except (TypeError, ValueError):
        pass
    rects, layout = calculate_pb99_grid(
        trusted,
        wm_width=wm_w,
        wm_height=wm_h,
        rotation=rotation,
        layout_mode=mode,
    )
    return rects, layout, mode.lower()


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

    runtime_mode, mode_info = resolve_runtime_screen_mode(
        configured=str(cfg.get("screen_mode") or "auto"),
        previous_mode=cfg.get("last_resize_mode"),
    )
    mode = "PORTRAIT" if runtime_mode == "portrait" else "LANDSCAPE"
    rects, layout, _runtime_mode = compute_layout_rects(cfg, entries)
    left_offset = int(layout.get("left_offset") or 0)
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
        active_root = root_tool
        if not active_root:
            retry = android.detect_root()
            active_root = retry.tool if retry.available else None
        row = safe_write_resize_bounds(
            pkg,
            rect,
            screen_mode=mode.lower(),
            root_tool=active_root,
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
    top_offset = int(layout.get("top_offset") or 0)
    screen_w = int(layout.get("screen_width") or 0)
    screen_h = int(layout.get("screen_height") or 0)
    if top_offset > 0 and screen_h > 0:
        cfg["termux_dock_fraction"] = top_offset / max(1, screen_h)
    elif left_offset > 0 and screen_w > 0:
        cfg["termux_dock_fraction"] = left_offset / max(1, screen_w)

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
