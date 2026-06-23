"""Inject in-game Lua auto-exec heartbeat trackers into clone data dirs."""

from __future__ import annotations

import logging
import os
import shlex
import tempfile
from typing import Any

from .config import validate_package_name
from .lua_heartbeat_server import DEFAULT_HOST, DEFAULT_PORT

_log = logging.getLogger("deng.rejoin.autoexec_injection")

TRACKER_FILENAME = "deng_heartbeat.lua"
PRIMARY_TRACKER_URL = "https://raw.githubusercontent.com/dengjiangbin/fish-it/main/tracker.lua"

# Known executor auto-exec search paths on rooted Android clones.
AUTOEXEC_PATH_TEMPLATES: tuple[str, ...] = (
    "/data/data/{package}/files/autoexec/",
    "/data/data/{package}/files/workspace/autoexec/",
    "/sdcard/Android/media/{package}/autoexec/",
    "/sdcard/Documents/{package}/autoexec/",
)


def resolve_autoexec_paths(package: str) -> list[str]:
    """Return absolute auto-exec directory paths for a clone package."""
    pkg = validate_package_name(str(package or "").strip())
    return [tmpl.format(package=pkg) for tmpl in AUTOEXEC_PATH_TEMPLATES]


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


def _write_file_via_root(dest_path: str, content: str, *, root_tool: str) -> tuple[bool, str]:
    from . import android

    parent = os.path.dirname(dest_path)
    mkdir_res = android.run_root_command(
        ["sh", "-c", f"mkdir -p {shlex.quote(parent)}"],
        root_tool=root_tool,
        timeout=10,
    )
    if not mkdir_res.ok:
        return False, f"mkdir failed: {(mkdir_res.stderr or '')[:120]}"

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            suffix=".lua",
            prefix="deng_autoexec_",
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        write_res = android.run_root_command(
            [
                "sh",
                "-c",
                f"cat {shlex.quote(tmp_path)} > {shlex.quote(dest_path)}",
            ],
            root_tool=root_tool,
            timeout=15,
        )
        if not write_res.ok:
            return False, f"write failed: {(write_res.stderr or '')[:120]}"

        chmod_res = android.run_root_command(
            ["chmod", "777", dest_path],
            root_tool=root_tool,
            timeout=8,
        )
        if not chmod_res.ok:
            return False, f"chmod failed: {(chmod_res.stderr or '')[:120]}"
        return True, ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def inject_autoexec_tracker(
    package: str,
    *,
    root_tool: str | None = None,
    heartbeat_host: str = DEFAULT_HOST,
    heartbeat_port: int = DEFAULT_PORT,
    primary_script_url: str = PRIMARY_TRACKER_URL,
) -> dict[str, Any]:
    """Write ``deng_heartbeat.lua`` into known auto-exec folders for one clone."""
    from . import android

    result: dict[str, Any] = {
        "success": False,
        "package": "",
        "paths_attempted": [],
        "paths_written": [],
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

    payload = build_heartbeat_tracker_lua(
        pkg,
        heartbeat_host=heartbeat_host,
        heartbeat_port=heartbeat_port,
        primary_script_url=primary_script_url,
    )
    for directory in resolve_autoexec_paths(pkg):
        dest = os.path.join(directory, TRACKER_FILENAME)
        result["paths_attempted"].append(dest)
        ok, err = _write_file_via_root(dest, payload, root_tool=str(tool))
        if ok:
            result["paths_written"].append(dest)
            _log.info(
                "[DENG_REJOIN_AUTOEXEC_WRITE] package=%s path=%s success=true",
                pkg,
                dest,
            )
        elif err:
            result["errors"].append(f"{dest}: {err}")
            _log.debug(
                "[DENG_REJOIN_AUTOEXEC_WRITE] package=%s path=%s success=false error=%s",
                pkg,
                dest,
                err,
            )

    result["success"] = bool(result["paths_written"])
    return result
