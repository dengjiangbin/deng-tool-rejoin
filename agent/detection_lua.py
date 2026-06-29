"""Generate the on-device ``deng.txt`` detection bootstrap (obscured).

``deng.txt`` is dropped into the Delta executor's ``Autoexecute`` folder next
to the user's own scripts.  It is intentionally tiny and obfuscated: it only
pins per-package config into ``getgenv().DENG`` and then loadstrings the real
detector from the canonical raw URL:

    loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/global/main/detector.lua"))()

The heavy logic lives in the remote ``detector.lua`` (see
``assets/lua/detector.lua`` in this repo, which the operator uploads to that
GitHub path).  The remote script POSTs in-game heartbeats (placeId / jobId /
universeId) to the loopback detection worker.

This module never executes Lua; it only builds text.
"""

from __future__ import annotations

DETECTOR_URL = "https://raw.githubusercontent.com/dengjiangbin/global/main/detector.lua"
DETECTION_FILENAME = "deng.txt"
DEFAULT_HEARTBEAT_INTERVAL = 5


def _lua_quote(value: str) -> str:
    """Safely embed an arbitrary string into a Lua double-quoted literal."""
    out = []
    for ch in str(value):
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32:
            out.append(f"\\{ord(ch)}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def build_bootstrap_lua(
    *,
    port: int,
    token: str,
    package: str,
    interval: int = DEFAULT_HEARTBEAT_INTERVAL,
    url: str = DETECTOR_URL,
) -> str:
    """Readable bootstrap: pin config + loadstring the remote detector."""
    return (
        "local D={}\n"
        f"D.port={int(port)}\n"
        f"D.token={_lua_quote(token)}\n"
        f"D.pkg={_lua_quote(package)}\n"
        f"D.interval={int(interval)}\n"
        "local G=(getgenv and getgenv()) or _G\n"
        "G.DENG=D\n"
        f"local ok,src=pcall(function() return game:HttpGet({_lua_quote(url)}) end)\n"
        "if ok and src then\n"
        "  local f=(loadstring or load)(src)\n"
        "  if f then pcall(f) end\n"
        "end\n"
    )


def obscure_lua(src: str) -> str:
    """Wrap ``src`` as a byte-array that is decoded and run at runtime.

    Hides the URL / token / package from a casual read of ``deng.txt`` while
    staying a single self-contained chunk any executor can auto-run.
    """
    data = src.encode("utf-8")
    nums = ",".join(str(b) for b in data)
    return (
        "-- deng\n"
        "do\n"
        f"local b={{{nums}}}\n"
        "local t={}\n"
        "for i=1,#b do t[i]=string.char(b[i]) end\n"
        "local s=table.concat(t)\n"
        "local f=(loadstring or load)(s)\n"
        "if f then pcall(f) end\n"
        "end\n"
    )


def build_deng_txt(
    package: str,
    *,
    port: int | None = None,
    token: str | None = None,
    interval: int = DEFAULT_HEARTBEAT_INTERVAL,
    url: str = DETECTOR_URL,
) -> str:
    """Full obscured ``deng.txt`` content for one package."""
    if port is None:
        try:
            from .detection_worker import detection_worker_port

            port = detection_worker_port()
        except Exception:  # noqa: BLE001
            port = 52789
    if token is None:
        try:
            from .detection_worker import current_token

            token = current_token()
        except Exception:  # noqa: BLE001
            token = ""
    bootstrap = build_bootstrap_lua(
        port=int(port),
        token=str(token or ""),
        package=str(package or ""),
        interval=int(interval),
        url=url,
    )
    return obscure_lua(bootstrap)
