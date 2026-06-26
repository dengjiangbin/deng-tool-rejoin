"""Android memory metrics using PSS / private RAM — not inflated RSS or VSS.

Primary sources:
  - ``dumpsys meminfo <package|pid>``
  - ``/proc/meminfo``
  - ``/proc/<pid>/smaps_rollup`` when accessible

RSS is collected only as a debug/reference field. Per-package display and
capacity math use PSS (proportional shared RAM) and private dirty / USS when
available.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .constants import DATA_DIR
from .config import validate_package_name

_INCREMENTAL_PATH = DATA_DIR / "ram_incremental_samples.json"
_MAX_INCREMENTAL_SAMPLES_PER_PKG = 32

_UNAVAILABLE = "unavailable"


def _kb_to_mb_str(kb: int | None) -> str:
    if kb is None or kb < 0:
        return _UNAVAILABLE
    if kb >= 1024 * 1024:
        return f"{kb / (1024 * 1024):.1f} GB"
    return f"{round(kb / 1024)} MB"


def _avg_int(values: list[int]) -> int | None:
    if not values:
        return None
    return int(round(sum(values) / len(values)))


def parse_proc_meminfo(text: str) -> dict[str, Any]:
    """Parse ``/proc/meminfo`` into MB-scale device summary fields."""
    fields_kb: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0].rstrip(":")
        try:
            val = int(parts[1])
        except (TypeError, ValueError):
            continue
        fields_kb[key] = val

    total_kb = fields_kb.get("MemTotal", 0)
    if total_kb <= 0:
        return {"parse_ok": False}

    avail_kb = fields_kb.get("MemAvailable", -1)
    if avail_kb < 0:
        avail_kb = fields_kb.get("MemFree", 0)

    free_kb = fields_kb.get("MemFree", 0)
    cached_kb = fields_kb.get("Cached", 0)
    buffers_kb = fields_kb.get("Buffers", 0)
    swap_total_kb = fields_kb.get("SwapTotal", 0)
    swap_free_kb = fields_kb.get("SwapFree", 0)
    zswap_kb = fields_kb.get("Zswap", 0)

    used_estimate_kb = max(0, total_kb - avail_kb)
    swap_used_kb = max(0, swap_total_kb - swap_free_kb)

    system_reserved_kb: int | None = None
    # Heuristic: memory not accounted as available + user-visible free cache.
    try:
        system_reserved_kb = max(
            0,
            total_kb - avail_kb - cached_kb - buffers_kb,
        )
    except TypeError:
        system_reserved_kb = None

    return {
        "parse_ok": True,
        "total_kb": total_kb,
        "mem_available_kb": max(0, avail_kb),
        "mem_free_kb": max(0, free_kb),
        "used_estimate_kb": used_estimate_kb,
        "cached_kb": max(0, cached_kb),
        "buffers_kb": max(0, buffers_kb),
        "swap_total_kb": max(0, swap_total_kb),
        "swap_used_kb": swap_used_kb,
        "swap_free_kb": max(0, swap_free_kb),
        "zswap_kb": max(0, zswap_kb),
        "system_reserved_estimate_kb": system_reserved_kb,
        "total_mb": total_kb // 1024,
        "mem_available_mb": max(0, avail_kb) // 1024,
        "used_estimate_mb": used_estimate_kb // 1024,
        "swap_total_mb": swap_total_kb // 1024,
        "swap_used_mb": swap_used_kb // 1024,
        "zswap_mb": zswap_kb // 1024,
        "system_reserved_estimate_mb": (
            system_reserved_kb // 1024 if system_reserved_kb is not None else None
        ),
    }


def parse_smaps_rollup(text: str) -> dict[str, Any]:
    """Parse ``/proc/<pid>/smaps_rollup`` when readable."""
    out: dict[str, Any] = {
        "pss_kb": None,
        "rss_kb": None,
        "private_dirty_kb": None,
        "private_clean_kb": None,
        "swap_pss_kb": None,
        "uss_kb": None,
        "source": "smaps_rollup",
    }
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        key = key.strip()
        token = rest.strip().split()
        if not token or not token[0].isdigit():
            continue
        val = int(token[0])
        if key == "Rss":
            out["rss_kb"] = val
        elif key == "Pss":
            out["pss_kb"] = val
        elif key in {"Private_Clean", "Private Clean"}:
            out["private_clean_kb"] = val
        elif key in {"Private_Dirty", "Private Dirty"}:
            out["private_dirty_kb"] = val
        elif key == "SwapPss":
            out["swap_pss_kb"] = val

    if out["private_dirty_kb"] is not None or out["private_clean_kb"] is not None:
        dirty = out["private_dirty_kb"] or 0
        clean = out["private_clean_kb"] or 0
        out["uss_kb"] = dirty + clean

    if out["pss_kb"] is None and out["rss_kb"] is None:
        out["source"] = _UNAVAILABLE
    return out


def _parse_dumpsys_total_row(line: str) -> dict[str, int]:
    """Parse a ``TOTAL`` table row from ``dumpsys meminfo`` (pid view)."""
    parts = line.strip().split()
    if len(parts) < 2 or parts[0].upper() != "TOTAL":
        return {}
    if parts[1].upper() == "PSS":
        return {}
    nums = [int(p) for p in parts[1:] if p.isdigit()]
    out: dict[str, int] = {}
    if len(nums) >= 1:
        out["pss_kb"] = nums[0]
    if len(nums) >= 2:
        out["private_dirty_kb"] = nums[1]
    if len(nums) >= 3:
        out["private_clean_kb"] = nums[2]
    if len(nums) >= 4:
        out["swap_pss_kb"] = nums[3]
    if len(nums) >= 2:
        out["uss_kb"] = nums[1] + (nums[2] if len(nums) >= 3 else 0)
    return out


def parse_dumpsys_meminfo(text: str) -> dict[str, Any]:
    """Parse ``dumpsys meminfo`` output for one pid or package."""
    out: dict[str, Any] = {
        "pss_kb": None,
        "rss_kb": None,
        "private_dirty_kb": None,
        "private_clean_kb": None,
        "swap_pss_kb": None,
        "uss_kb": None,
        "source": _UNAVAILABLE,
        "notes": [],
    }
    if not text or not str(text).strip():
        return out

    for line in text.splitlines():
        m = re.search(r"TOTAL\s+PSS:\s*(\d+)", line, re.IGNORECASE)
        if m:
            out["pss_kb"] = int(m.group(1))
            out["source"] = "dumpsys_total_pss_line"
            break

    if out["pss_kb"] is None:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("TOTAL") and "PSS" not in stripped.upper():
                parsed = _parse_dumpsys_total_row(stripped)
                if parsed.get("pss_kb") is not None:
                    out.update(parsed)
                    out["source"] = "dumpsys_total_row"
                    break

    # App Summary single TOTAL (package-level, no private columns).
    if out["pss_kb"] is None:
        for line in text.splitlines():
            m = re.match(r"^\s*TOTAL:\s*(\d+)\s*$", line)
            if m:
                out["pss_kb"] = int(m.group(1))
                out["source"] = "dumpsys_app_summary_total"
                break

    for line in text.splitlines():
        m = re.search(r"Native Heap:\s*(\d+)", line, re.IGNORECASE)
        if m and out.get("rss_kb") is None:
            # Not RSS — do not store as rss_kb; note only.
            pass
        m_rss = re.search(r"(?:\bRSS|TOTAL RSS)\s*:?\s*(\d+)", line, re.IGNORECASE)
        if m_rss:
            out["rss_kb"] = int(m_rss.group(1))
            out["notes"].append("rss_from_dumpsys_debug_only")

    if out["pss_kb"] is None:
        out["notes"].append("pss_not_found_in_dumpsys_output")
    return out


def parse_dumpsys_device_summary(text: str) -> dict[str, Any]:
    """Parse device-level lines from ``dumpsys meminfo`` (no args)."""
    out: dict[str, Any] = {}
    for line in text.splitlines():
        m = re.search(r"Total RAM:\s*(\d+)\s*([kKmMgG]?B)?", line, re.I)
        if m:
            val = int(m.group(1))
            unit = (m.group(2) or "KB").upper()
            if unit.startswith("M"):
                out["total_mb"] = val
            elif unit.startswith("G"):
                out["total_mb"] = val * 1024
            else:
                out["total_mb"] = val // 1024
        m = re.search(r"Free RAM:\s*(\d+)\s*([kKmMgG]?B)?", line, re.I)
        if m:
            val = int(m.group(1))
            unit = (m.group(2) or "KB").upper()
            if unit.startswith("M"):
                out["mem_available_mb"] = val
            elif unit.startswith("G"):
                out["mem_available_mb"] = val * 1024
            else:
                out["mem_available_mb"] = val // 1024
        m = re.search(r"Used RAM:\s*(\d+)\s*([kKmMgG]?B)?", line, re.I)
        if m:
            val = int(m.group(1))
            unit = (m.group(2) or "KB").upper()
            if unit.startswith("M"):
                out["used_estimate_mb"] = val
            elif unit.startswith("G"):
                out["used_estimate_mb"] = val * 1024
            else:
                out["used_estimate_mb"] = val // 1024
        m = re.search(r"ZRAM:\s*(\d+)/(\d+)\s*MB", line, re.I)
        if m:
            out["zram_used_mb"] = int(m.group(1))
            out["zram_total_mb"] = int(m.group(2))
    return out


def snapshot_mem_available_kb() -> int | None:
    """Return MemAvailable from ``/proc/meminfo`` in kilobytes."""
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parsed = parse_proc_meminfo(text)
    if not parsed.get("parse_ok"):
        return None
    return int(parsed.get("mem_available_kb") or 0)


def _load_incremental_db() -> dict[str, Any]:
    try:
        if not _INCREMENTAL_PATH.is_file():
            return {"packages": {}}
        raw = json.loads(_INCREMENTAL_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"packages": {}}
        pkgs = raw.get("packages")
        if not isinstance(pkgs, dict):
            raw["packages"] = {}
        return raw
    except Exception:  # noqa: BLE001
        return {"packages": {}}


def _save_incremental_db(data: dict[str, Any]) -> None:
    try:
        _INCREMENTAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _INCREMENTAL_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(_INCREMENTAL_PATH)
    except OSError:
        pass


def record_launch_baseline(package: str, mem_available_kb: int) -> None:
    """Store MemAvailable captured immediately before a package launch."""
    package = validate_package_name(package)
    db = _load_incremental_db()
    pkgs = db.setdefault("packages", {})
    row = pkgs.setdefault(package, {})
    row["pending_baseline_kb"] = int(mem_available_kb)
    row["baseline_at"] = time.time()
    _save_incremental_db(db)


def record_incremental_sample(package: str, mem_available_before_kb: int, mem_available_after_kb: int) -> None:
    """Persist one incremental sample (drop in MemAvailable when package became stable)."""
    package = validate_package_name(package)
    delta_kb = max(0, int(mem_available_before_kb) - int(mem_available_after_kb))
    db = _load_incremental_db()
    pkgs = db.setdefault("packages", {})
    row = pkgs.setdefault(package, {})
    samples = row.setdefault("incremental_kb", [])
    if not isinstance(samples, list):
        samples = []
        row["incremental_kb"] = samples
    samples.append(delta_kb)
    if len(samples) > _MAX_INCREMENTAL_SAMPLES_PER_PKG:
        samples[:] = samples[-_MAX_INCREMENTAL_SAMPLES_PER_PKG:]
    row.pop("pending_baseline_kb", None)
    row["last_sample_at"] = time.time()
    _save_incremental_db(db)


def finalize_launch_incremental_sample(package: str) -> None:
    """Complete a pending launch baseline with the current MemAvailable."""
    package = validate_package_name(package)
    db = _load_incremental_db()
    row = (db.get("packages") or {}).get(package) or {}
    baseline = row.get("pending_baseline_kb")
    if baseline is None:
        return
    after = snapshot_mem_available_kb()
    if after is None:
        return
    record_incremental_sample(package, int(baseline), after)


def get_incremental_samples(package: str) -> list[int]:
    package = validate_package_name(package)
    db = _load_incremental_db()
    row = (db.get("packages") or {}).get(package) or {}
    samples = row.get("incremental_kb") or []
    if not isinstance(samples, list):
        return []
    out: list[int] = []
    for item in samples:
        try:
            out.append(max(0, int(item)))
        except (TypeError, ValueError):
            continue
    return out


def get_package_pids(package: str, root_info: Any) -> list[str]:
    """Return all running PIDs for a package (clone-safe)."""
    from .android import detect_root, run_root_command, validate_package_name as _vp

    package = _vp(package)
    info = root_info or detect_root()
    if not info.available or not info.tool:
        return []
    res = run_root_command(["pidof", package], root_tool=info.tool, timeout=5)
    pids: list[str] = []
    if res.ok:
        for token in (res.stdout or "").split():
            if token.isdigit() and int(token) > 0:
                pids.append(token)
    if pids:
        return pids

    from .android import process_ps_first_pid, process_ps_scan_args

    res2 = run_root_command(process_ps_scan_args(package), root_tool=info.tool, timeout=5)
    if res2.ok:
        pid = process_ps_first_pid(res2.stdout or "", package)
        if pid:
            return [pid]
    return []


def _detect_process_status(package: str, pids: list[str], root_info: Any) -> str:
    from .android import current_foreground_package, detect_root

    fg = ""
    try:
        fg = str(current_foreground_package() or "").strip()
    except Exception:  # noqa: BLE001
        fg = ""
    if fg == package:
        return "foreground"

    frozen = False
    stopped = False
    for pid in pids:
        try:
            text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("State:"):
                state_char = line.split()[1] if len(line.split()) > 1 else ""
                if state_char == "T":
                    stopped = True
                break
    if stopped:
        return "frozen"
    if pids:
        return "background"
    return _UNAVAILABLE


def _read_smaps_rollup(pid: str) -> dict[str, Any]:
    try:
        text = Path(f"/proc/{pid}/smaps_rollup").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"source": _UNAVAILABLE, "error": str(exc)[:120]}
    parsed = parse_smaps_rollup(text)
    if parsed.get("source") == _UNAVAILABLE:
        parsed["error"] = "smaps_rollup unreadable or empty"
    return parsed


def _read_dumpsys_meminfo(target: str) -> dict[str, Any]:
    from .android import run_android_command

    result = run_android_command(["dumpsys", "meminfo", target], timeout=10, prefer_root=False)
    if not result.ok:
        return {
            "source": _UNAVAILABLE,
            "error": (result.stderr or result.stdout or "dumpsys failed")[:120],
        }
    parsed = parse_dumpsys_meminfo(result.stdout or "")
    if parsed.get("pss_kb") is None:
        parsed["error"] = parsed.get("error") or "PSS not found in dumpsys output"
    return parsed


def _merge_metrics(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in extra.items():
        if key in {"notes", "error"}:
            if val:
                notes = out.setdefault("notes", [])
                if isinstance(val, list):
                    notes.extend(val)
                else:
                    notes.append(str(val))
            continue
        if val is None:
            continue
        if key.endswith("_kb") and out.get(key) is None:
            out[key] = val
        elif key == "source" and out.get("source") == _UNAVAILABLE and val != _UNAVAILABLE:
            out["source"] = val
    return out


def collect_pid_memory(pid: str, root_info: Any = None) -> dict[str, Any]:
    """Collect memory metrics for one process PID."""
    pid = str(pid or "").strip()
    if not pid.isdigit():
        return {"pid": pid, "source": _UNAVAILABLE, "error": "invalid pid"}

    metrics: dict[str, Any] = {
        "pid": pid,
        "pss_kb": None,
        "rss_kb": None,
        "private_dirty_kb": None,
        "uss_kb": None,
        "swap_pss_kb": None,
        "source": _UNAVAILABLE,
        "notes": [],
    }

    smaps = _read_smaps_rollup(pid)
    if smaps.get("pss_kb") is not None:
        metrics = _merge_metrics(metrics, smaps)
        metrics["method"] = "proc_smaps_rollup"
    else:
        metrics["notes"].append(
            "smaps_rollup unavailable — using dumpsys meminfo fallback"
        )
        dumpsys = _read_dumpsys_meminfo(pid)
        metrics = _merge_metrics(metrics, dumpsys)
        metrics["method"] = "dumpsys_meminfo_pid"

    if metrics.get("rss_kb") is None:
        try:
            text = Path(f"/proc/{pid}/status").read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        metrics["rss_kb"] = int(parts[1])
                        metrics["notes"].append("rss_from_proc_status_debug_only")
                        break
        except OSError:
            pass

    return metrics


def _sum_optional(values: list[int | None]) -> int | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums)


def collect_package_memory(package: str, root_info: Any = None) -> dict[str, Any]:
    """Aggregate per-package metrics across all running PIDs."""
    from .android import detect_root

    package = validate_package_name(package)
    pids = get_package_pids(package, root_info or detect_root())
    status = _detect_process_status(package, pids, root_info)

    if not pids:
        return {
            "package": package,
            "pids": [],
            "status": status,
            "pss_kb": None,
            "rss_kb": None,
            "private_dirty_kb": None,
            "uss_kb": None,
            "swap_pss_kb": None,
            "usage_mb": "N/A",
            "method": "unknown",
            "success": False,
            "error": "no running pid",
            "notes": [],
        }

    per_pid = [collect_pid_memory(pid, root_info) for pid in pids]
    pss_kb = _sum_optional([m.get("pss_kb") for m in per_pid])
    rss_kb = _sum_optional([m.get("rss_kb") for m in per_pid])
    private_dirty_kb = _sum_optional([m.get("private_dirty_kb") for m in per_pid])
    uss_kb = _sum_optional([m.get("uss_kb") for m in per_pid])
    swap_pss_kb = _sum_optional([m.get("swap_pss_kb") for m in per_pid])

    methods = {str(m.get("method") or "") for m in per_pid}
    method = "+".join(sorted(m for m in methods if m)) or "unknown"

    success = pss_kb is not None
    usage_mb = _kb_to_mb_str(pss_kb) if success else "N/A"

    notes: list[str] = []
    if rss_kb is not None and pss_kb is not None and rss_kb > pss_kb + 50 * 1024:
        notes.append(
            f"{_kb_to_mb_str(rss_kb)} is inflated/shared RSS, not real private RAM."
        )

    return {
        "package": package,
        "pids": pids,
        "status": status,
        "pss_kb": pss_kb,
        "rss_kb": rss_kb or 0,
        "private_dirty_kb": private_dirty_kb,
        "uss_kb": uss_kb,
        "swap_pss_kb": swap_pss_kb,
        "usage_mb": usage_mb,
        "method": method,
        "success": success,
        "error": "" if success else "PSS unavailable",
        "notes": notes,
        "per_pid": per_pid,
    }


def collect_device_memory(root_info: Any = None) -> dict[str, Any]:
    """Device-level RAM, swap/zRAM, and optional dumpsys summary."""
    from .android import run_android_command

    out: dict[str, Any] = {"source": "proc_meminfo", "parse_ok": False}
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace")
        parsed = parse_proc_meminfo(text)
        if parsed.get("parse_ok"):
            out.update(parsed)
            out["parse_ok"] = True
    except OSError as exc:
        out["error"] = f"/proc/meminfo unreadable: {exc}"[:120]

    dumpsys = run_android_command(["dumpsys", "meminfo"], timeout=12, prefer_root=False)
    if dumpsys.ok:
        ds = parse_dumpsys_device_summary(dumpsys.stdout or "")
        out["dumpsys_summary"] = ds
        for key in ("total_mb", "mem_available_mb", "used_estimate_mb", "zram_used_mb", "zram_total_mb"):
            if ds.get(key) is not None and out.get(key.replace("_mb", "_kb")) is None:
                if key.endswith("_mb"):
                    out[key] = ds[key]
    else:
        out["dumpsys_error"] = (dumpsys.stderr or "dumpsys meminfo failed")[:120]

    return out


def build_ram_report_text(
    packages: list[str],
    root_info: Any = None,
) -> str:
    """Human-readable RAM report for Termux / doctor output."""
    from .android import detect_root

    root_info = root_info or detect_root()
    device = collect_device_memory(root_info)

    lines: list[str] = [
        "DENG Tool: Rejoin — Android RAM report",
        "Metrics use PSS (proportional shared RAM) and private dirty/USS.",
        "RSS is shown only as debug/reference — never as real per-package RAM.",
        "",
        "── Device summary ──",
    ]

    if device.get("parse_ok"):
        lines.append(f"  Total physical RAM:     {device.get('total_mb', _UNAVAILABLE)} MB")
        lines.append(f"  MemAvailable:           {device.get('mem_available_mb', _UNAVAILABLE)} MB")
        lines.append(f"  Used RAM estimate:      {device.get('used_estimate_mb', _UNAVAILABLE)} MB")
        zram_total = device.get("zram_total_mb")
        zram_used = device.get("zram_used_mb")
        if zram_total is not None:
            lines.append(f"  zRAM total / used:      {zram_total} MB / {zram_used or 0} MB")
        else:
            swap_total = device.get("swap_total_mb", 0)
            swap_used = device.get("swap_used_mb", 0)
            if swap_total:
                lines.append(f"  Swap total / used:      {swap_total} MB / {swap_used} MB")
            else:
                lines.append("  zRAM/swap:              unavailable (not reported by kernel)")
        reserved = device.get("system_reserved_estimate_mb")
        if reserved is not None:
            lines.append(f"  System reserved est.:   {reserved} MB (heuristic)")
    else:
        lines.append(f"  Device RAM:             {_UNAVAILABLE} ({device.get('error', 'parse failed')})")

    pkg_metrics: list[dict[str, Any]] = []
    for pkg in packages:
        pkg_metrics.append(collect_package_memory(pkg, root_info))

    active = [m for m in pkg_metrics if m.get("success")]
    lines.extend([
        "",
        f"  Active packages:        {len(active)} / {len(packages)} configured",
    ])

    if active:
        avg_pss = _avg_int([int(m["pss_kb"]) for m in active if m.get("pss_kb")])
        avg_private = _avg_int(
            [int(m["private_dirty_kb"]) for m in active if m.get("private_dirty_kb") is not None]
        )
        lines.append(f"  Average PSS / package:  {_kb_to_mb_str(avg_pss)}")
        if avg_private is not None:
            lines.append(f"  Average Private Dirty:  {_kb_to_mb_str(avg_private)}")
        else:
            lines.append("  Average Private Dirty:  unavailable")

        incremental_vals: list[int] = []
        for m in active:
            samples = get_incremental_samples(str(m.get("package") or ""))
            if samples:
                incremental_vals.append(int(round(sum(samples) / len(samples))))
        avg_incremental = _avg_int(incremental_vals)
        if avg_incremental is not None:
            lines.append(
                f"  Avg incremental cost:   {_kb_to_mb_str(avg_incremental)} "
                "(MemAvailable drop per launch, averaged)"
            )
        else:
            lines.append(
                "  Avg incremental cost:   unavailable "
                "(needs launch baselines from Start — run packages online once)"
            )
    else:
        lines.append("  Average PSS / package:  unavailable (no running packages)")

    lines.extend(["", "── Per package ──"])
    for m in pkg_metrics:
        pkg = m.get("package") or "?"
        lines.append(f"  Package: {pkg}")
        pids = m.get("pids") or []
        lines.append(f"    PID(s):           {', '.join(pids) if pids else _UNAVAILABLE}")
        lines.append(f"    Status:           {m.get('status', _UNAVAILABLE)}")
        lines.append(f"    PSS:              {_kb_to_mb_str(m.get('pss_kb'))}")
        pd = m.get("private_dirty_kb")
        uss = m.get("uss_kb")
        if pd is not None:
            lines.append(f"    Private Dirty:    {_kb_to_mb_str(pd)}")
        elif uss is not None:
            lines.append(f"    USS (private):    {_kb_to_mb_str(uss)}")
        else:
            lines.append("    Private Dirty:    unavailable")
        swap = m.get("swap_pss_kb")
        lines.append(
            f"    Swap PSS:         {_kb_to_mb_str(swap) if swap is not None else _UNAVAILABLE}"
        )
        rss = m.get("rss_kb")
        if rss:
            lines.append(f"    RSS (debug only): {_kb_to_mb_str(int(rss))}")
        else:
            lines.append(f"    RSS (debug only): {_UNAVAILABLE}")
        for note in m.get("notes") or []:
            lines.append(f"    Note: {note}")
        if not m.get("success"):
            err = m.get("error") or "measurement failed"
            lines.append(f"    Error: {err}")

    if active and device.get("total_mb"):
        total_mb = int(device["total_mb"])
        avg_pss_mb = (_avg_int([int(m["pss_kb"]) for m in active if m.get("pss_kb")]) or 0) // 1024
        count = len(active)
        naive_rss_sum = sum(int(m.get("rss_kb") or 0) for m in active) // 1024
        lines.extend([
            "",
            "── Why multiple packages can run ──",
            f"  This device reports {total_mb} MB total RAM with {count} active package(s).",
            f"  Average real pressure per package (PSS) is about {avg_pss_mb} MB, not naive RSS sums.",
        ])
        if naive_rss_sum > total_mb:
            lines.append(
                f"  Summing RSS ({naive_rss_sum} MB) looks impossible on {total_mb} MB — "
                "that is shared/inflated RSS, not private RAM."
            )
        if device.get("swap_used_mb") or device.get("zram_used_mb"):
            zused = device.get("zram_used_mb") or device.get("swap_used_mb")
            lines.append(
                f"  Compressed memory (zRAM/swap) is in use ({zused} MB used) — "
                "background apps can stay cached cheaply."
            )
        lines.append(
            "  Shared libraries/assets are counted once in RAM; PSS splits that cost fairly."
        )
        lines.append(
            "  Background/frozen processes keep state with lower CPU and often lower PSS."
        )
        if avg_pss_mb and count * avg_pss_mb > total_mb:
            lines.append(
                "  Warning: PSS averages still suggest tight RAM — expect swapping or kills under load."
            )
        else:
            lines.append(
                "  Capacity check: PSS/private metrics do NOT prove this device is impossible."
            )

    lines.append("")
    return "\n".join(lines)
