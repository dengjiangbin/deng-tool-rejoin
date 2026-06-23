"""Inject in-game Lua auto-exec heartbeat trackers into clone data dirs."""

from __future__ import annotations

import logging
import os
import shlex
from typing import Any

from .config import validate_package_name
from .lua_heartbeat_server import DEFAULT_HOST, DEFAULT_PORT

_log = logging.getLogger("deng.rejoin.autoexec_injection")

TRACKER_FILENAME = "deng_rejoin_heartbeat.lua"
PRIMARY_TRACKER_URL = "https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua"

# Executor-specific auto-exec folders on rooted Android clones (Delta/Codex/Vega X/etc.).
AUTOEXEC_PATH_TEMPLATES: tuple[str, ...] = (
    "/sdcard/Android/data/{package}/files/Delta/autoexec/",
    "/sdcard/Android/data/{package}/files/Codex/autoexec/",
    "/sdcard/Android/data/{package}/files/VegaX/autoexec/",
    "/sdcard/Android/media/{package}/spdm_scripts/autoexec/",
    "/sdcard/Android/data/{package}/files/autoexec/",
    "/data/data/{package}/files/autoexec/",
)


def resolve_autoexec_paths(package: str) -> list[str]:
    """Return absolute auto-exec directory paths for a clone package."""
    pkg = validate_package_name(str(package or "").strip())
    return [tmpl.format(package=pkg) for tmpl in AUTOEXEC_PATH_TEMPLATES]


def _package_files_dir(package: str) -> str:
    pkg = validate_package_name(str(package or "").strip())
    return f"/data/data/{pkg}/files/"


def _is_internal_data_path(path: str, package: str) -> bool:
    prefix = f"/data/data/{validate_package_name(package)}/"
    return str(path or "").startswith(prefix)


def lookup_package_uid_gid(
    package: str,
    *,
    root_tool: str,
    stat_path: str | None = None,
) -> tuple[str, str] | None:
    """Read ``uid:gid`` from the clone's sandbox files directory via root stat."""
    from . import android

    pkg = validate_package_name(str(package or "").strip())
    target = str(stat_path or _package_files_dir(pkg)).rstrip("/") + "/"
    res = android.run_mount_master_root_command(
        ["stat", "-c", "%u:%g", target],
        root_tool=root_tool,
        timeout=8,
    )
    if not res.ok:
        return None
    raw = str(res.stdout or "").strip()
    if ":" not in raw:
        return None
    uid, gid = raw.split(":", 1)
    uid = uid.strip()
    gid = gid.strip()
    if not uid.isdigit() or not gid.isdigit():
        return None
    return uid, gid


def build_injection_shell_script(
    package: str,
    paths: list[str],
    payload: str,
    *,
    uid_gid: tuple[str, str] | None = None,
) -> str:
    """Build one mount-master bash block that writes the tracker to every autoexec path."""
    lines: list[str] = []
    chown_dirs: list[str] = []
    for directory in paths:
        dest_dir = str(directory or "").rstrip("/")
        if not dest_dir:
            continue
        dest = f"{dest_dir}/{TRACKER_FILENAME}"
        lines.append(f"mkdir -p {shlex.quote(dest_dir)} 2>/dev/null")
        lines.append(f"printf %s {shlex.quote(payload)} > {shlex.quote(dest)}")
        lines.append(f"chmod 777 {shlex.quote(dest)} 2>/dev/null")
        if _is_internal_data_path(dest_dir + "/", package):
            chown_dirs.append(dest_dir)
    if uid_gid:
        uid, gid = uid_gid
        for dest_dir in chown_dirs:
            lines.append(
                f"chown -R {uid}:{gid} {shlex.quote(dest_dir)} 2>/dev/null"
            )
    return "\n".join(lines)


def build_heartbeat_tracker_lua(
    package: str,
    *,
    heartbeat_host: str = DEFAULT_HOST,
    heartbeat_port: int = DEFAULT_PORT,
    primary_script_url: str = PRIMARY_TRACKER_URL,
) -> str:
    """Build the auto-exec Lua payload for one clone package."""
    pkg = validate_package_name(str(package or "").strip())
    heartbeat_url = (
        f"http://{heartbeat_host}:{int(heartbeat_port)}"
        f"/heartbeat?package={pkg}"
    )
    return (
        "-- Dynamic Auto-Generated Rejoin Heartbeat Tracker\n"
        "task.spawn(function()\n"
        "    while task.wait(10) do\n"
        "        pcall(function()\n"
        f'            game:HttpGet("{heartbeat_url}")\n'
        "        end)\n"
        "    end\n"
        "end)\n"
        "-- Chain-load the primary script\n"
        "pcall(function()\n"
        f'    loadstring(game:HttpGet("{primary_script_url}"))()\n'
        "end)\n"
    )


def inject_autoexec_tracker(
    package: str,
    *,
    root_tool: str | None = None,
    heartbeat_host: str = DEFAULT_HOST,
    heartbeat_port: int = DEFAULT_PORT,
    primary_script_url: str = PRIMARY_TRACKER_URL,
) -> dict[str, Any]:
    """Write ``deng_rejoin_heartbeat.lua`` into known auto-exec folders for one clone."""
    from . import android

    result: dict[str, Any] = {
        "success": False,
        "package": "",
        "paths_attempted": [],
        "paths_written": [],
        "uid_gid": None,
        "mount_master": True,
        "errors": [],
    }
    try:
        pkg = validate_package_name(str(package or "").strip())
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
        return result

    result["package"] = pkg
    tool = root_tool
    if not tool:
        root_info = android.detect_root()
        if not root_info.available:
            result["errors"].append("root unavailable")
            return result
        tool = root_info.tool

    paths = resolve_autoexec_paths(pkg)
    payload = build_heartbeat_tracker_lua(
        pkg,
        heartbeat_host=heartbeat_host,
        heartbeat_port=heartbeat_port,
        primary_script_url=primary_script_url,
    )
    uid_gid = lookup_package_uid_gid(pkg, root_tool=str(tool))
    if uid_gid:
        result["uid_gid"] = f"{uid_gid[0]}:{uid_gid[1]}"

    for directory in paths:
        result["paths_attempted"].append(
            os.path.join(directory, TRACKER_FILENAME),
        )

    script = build_injection_shell_script(pkg, paths, payload, uid_gid=uid_gid)
    inject_res = android.run_mount_master_root_command(
        ["sh", "-c", script],
        root_tool=str(tool),
        timeout=30,
    )
    if inject_res.ok:
        result["paths_written"] = list(result["paths_attempted"])
        result["success"] = True
        _log.info(
            "[DENG_REJOIN_AUTOEXEC_WRITE] package=%s paths=%s uid_gid=%s mount_master=true success=true",
            pkg,
            len(result["paths_written"]),
            result.get("uid_gid") or "unknown",
        )
    else:
        err = (inject_res.stderr or inject_res.stdout or "inject failed")[:160]
        result["errors"].append(err)
        _log.debug(
            "[DENG_REJOIN_AUTOEXEC_WRITE] package=%s mount_master=true success=false error=%s",
            pkg,
            err,
        )

    return result
