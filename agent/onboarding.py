"""First-run onboarding text and setup status for the main Termux menu."""

from __future__ import annotations

from typing import Any

from .config import (
    DEFAULT_ROBLOX_PACKAGE_HINTS,
    effective_private_server_url,
    enabled_package_entries,
    validate_package_detection_hints,
    validate_package_entries,
)
from .constants import PRODUCT_NAME, VERSION

NEW_USER_HELP_TEXT = """
New User Help
=============

What this tool does
-------------------
DENG Tool: Rejoin helps open, reopen, and reconnect the Roblox Android app
(or a compatible clone) on your phone. It runs in Termux on your device.

What you need before Start
--------------------------
- A valid license key (from the DENG Tool: Rejoin Panel in Discord).
- At least one Roblox package chosen in setup (often auto-detected).
- Optional: a private server or game link if you want direct join.

What to do first
----------------
1) Enter your license key (main menu, option 1).
2) Run First Time Setup Config (option 2).
3) Pick your Roblox package from the detection table, or enter it manually.
4) Add a private server URL only if you need it (optional).
5) Choose Start (option 4).

Main menu options
-----------------
1 — Enter / Update License Key
2 — First Time Setup Config
3 — Setup / Edit Config
4 — Start
5 — New User Help
0 — Exit

How to enter a license key
--------------------------
From the main menu choose "Enter / Update License Key", paste your key,
and wait for "License OK". If you change phones, ask support about Reset HWID.

Package auto-detection
----------------------
The tool scans installed Android packages using safe name hints (like roblox,
rblx, blox) plus hints you add for your clone. Pick a row from the table
(#, Package, App Name, Launchable). If nothing appears, install Roblox,
open it once, then try again—or use manual package entry.

Username (account name)
-----------------------
This is only a label in the Start table so you can tell accounts apart.
You can leave it blank: the table will show "Unknown" and Start still works.

Private Server URL
--------------------
Optional. If you set a full private server https link or a Roblox web URL,
the tool can use it when launching or reconnecting. If you skip it, the app
opens normally. Full URLs are never printed on screen after saving.

How to Start
------------
Choose Start from the main menu. Keep Termux open while monitoring runs.

Start table (normal view)
-------------------------
Only these columns appear:

  # | Package | Username | State

If State becomes Online, Roblox is running and being watched.

Common State values
-------------------
- Preparing / Optimizing — getting ready.
- Launching — opening the app.
- Online — looks healthy.
- Background — app may be in the background while the tool decides next step.
- Reconnecting — trying to reopen or reconnect.
- Warning — check messages or run status.
- Failed / Offline — something went wrong; try opening Roblox manually once.
- Unknown — not enough information yet.

Extra detail (cache, graphics, launch trace) appears only with --verbose,
--debug, or DEBUG log level—not in the normal table.

What to send support
--------------------
- Screenshot of the Start table.
- Output of: version and status (see docs).
- Selected package name (e.g. com.roblox.client).
- Whether Roblox opens when you tap the app icon.
Do not post your full license key or private server link in public chat.

Full install guide: docs/NEW_USER_TERMUX_GUIDE.md
""".strip()


def print_beginner_license_gate_help(*, show_hwid_footer: bool = True) -> None:
    """Steps after a Start-time license failure (no shame, no URL dump)."""
    print()
    print("You need a DENG Tool: Rejoin license key before using Start.")
    print()
    print("What to do:")
    print("  1. Get your key from the DENG Tool: Rejoin Panel.")
    print('  2. Choose Enter / Update License Key (main menu option 1).')
    print("  3. Paste your key.")
    print("  4. Run First Time Setup Config (option 2).")
    if show_hwid_footer:
        print()
        print("If your key says wrong device, ask support to Reset HWID.")


def print_beginner_menu_license_prompt() -> None:
    """Shown when the license wizard asks for a key (user chose menu option 1)."""
    print("No License Key Found")
    print()
    print("You need a DENG Tool: Rejoin license key before using Start.")
    print()
    print("What to do:")
    print("  1. Get your key from the DENG Tool: Rejoin Panel.")
    print('  2. Choose Enter / Update License Key (you are here).')
    print("  3. Paste your key below when prompted.")
    print("  4. Run First Time Setup Config (main menu option 2).")
    print()
    print("If your key says wrong device, ask support to Reset HWID.")
    print()


def _license_ui_line(cfg: dict[str, Any], *, dev_mode: bool) -> str:
    if dev_mode:
        return "License: Not required (development mode)"
    lic = cfg.get("license") if isinstance(cfg.get("license"), dict) else {}
    if lic.get("disabled_by_user") or not lic.get("enabled", True):
        return "License: Not required (your config)"

    key = (str(lic.get("key") or "").strip() or str(cfg.get("license_key") or "").strip())
    if not key:
        return "License: Missing"

    st = str(lic.get("last_status") or "").strip().lower()
    if st == "active":
        return "License: Verified"
    if st == "wrong_device":
        return "License: Wrong device (you may need Reset HWID)"
    if not st or st in ("missing_key", "not_configured"):
        return "License: Not verified — Start will check, or use option 1 to update your key"
    return "License: Not verified — use option 1 if you need to change your key"


def _config_ui_line(cfg: dict[str, Any] | None) -> str:
    if cfg is None:
        return "Config: Could not load config (check install paths)"
    if not cfg.get("first_setup_completed"):
        return "Config: Not created"
    return "Config: Saved on this device"


def _packages_ui_line(cfg: dict[str, Any]) -> str:
    try:
        entries = enabled_package_entries(cfg)
    except Exception:
        return "Packages: None selected (run First Time Setup or Setup / Edit Config)"
    if not entries:
        return "Packages: None selected"
    n = len(entries)
    return f"Packages: {n} selected ({', '.join(e['package'] for e in entries[:3])}{'…' if n > 3 else ''})"


def _detection_options_from_cfg(cfg: dict[str, Any]) -> tuple[list[str], bool, bool]:
    pd = cfg.get("package_detection")
    if not isinstance(pd, dict):
        pd = {}
    hints_src = pd.get("hints")
    if hints_src in (None, "", []):
        hints_src = cfg.get("package_detection_hints")
    try:
        hints = validate_package_detection_hints(hints_src)
    except Exception:
        hints = list(DEFAULT_ROBLOX_PACKAGE_HINTS)
    return hints, bool(pd.get("include_launchable_only", True)), bool(pd.get("enabled", True))


def _package_scan_line(cfg: dict[str, Any]) -> str:
    try:
        from . import android

        hints, inc_launch, det_en = _detection_options_from_cfg(cfg)
        n = len(
            android.discover_roblox_package_candidates(
                hints,
                include_launchable_only=inc_launch,
                detection_enabled=det_en,
            )
        )
        if n == 0:
            return "Package scan: No Roblox-like packages detected — install/open Roblox, then rerun setup"
        return f"Package scan: {n} candidate(s) detected on this device"
    except Exception:
        return "Package scan: Unavailable (run setup to pick a package manually)"


def _private_url_line(cfg: dict[str, Any]) -> str:
    try:
        entries = validate_package_entries(cfg.get("roblox_packages") or [])
    except Exception:
        entries = []
    any_url = False
    for e in entries:
        if effective_private_server_url(e, cfg):
            any_url = True
            break
    if not any_url and not (str(cfg.get("private_server_url") or "").strip()):
        if str(cfg.get("launch_url") or "").strip() and str(cfg.get("launch_mode") or "") in ("web_url", "deeplink"):
            any_url = True
    if any_url:
        return "Private URL: Set (optional — full link is never shown here)"
    return "Private URL: Optional, not set"


def _ready_line(cfg: dict[str, Any], *, dev_mode: bool) -> tuple[bool, list[str]]:
    """Return (is_ready, extra lines to print after Next Step)."""
    lic = cfg.get("license") if isinstance(cfg.get("license"), dict) else {}
    license_required = bool(not dev_mode and lic.get("enabled", True) and not lic.get("disabled_by_user"))

    key_ok = True
    if license_required:
        key = (str(lic.get("key") or "").strip() or str(cfg.get("license_key") or "").strip())
        st = str(lic.get("last_status") or "").strip().lower()
        key_ok = bool(key) and st == "active"

    try:
        entries = enabled_package_entries(cfg)
    except Exception:
        entries = []

    setup_ok = bool(cfg.get("first_setup_completed"))
    pkg_ok = len(entries) > 0

    ready = key_ok and setup_ok and pkg_ok
    extra: list[str] = []
    if ready:
        extra.extend(
            [
                "",
                "Ready To Start",
                "----------",
                "Your setup is ready.",
                "",
                "Choose Start (option 4) to launch and monitor Roblox.",
                "",
                "The public Start table shows only:",
                "  # | Package | Username | State",
                "",
                "Cache cleanup, graphics tuning, and reconnect details appear only in",
                "verbose or debug mode—not in the normal table.",
            ]
        )
    return ready, extra


def _next_step_line(cfg: dict[str, Any], *, dev_mode: bool) -> str:
    if cfg is None:
        return "Fix your config path or reinstall, then try again."

    lic = cfg.get("license") if isinstance(cfg.get("license"), dict) else {}
    license_required = bool(not dev_mode and lic.get("enabled", True) and not lic.get("disabled_by_user"))

    if license_required:
        key = (str(lic.get("key") or "").strip() or str(cfg.get("license_key") or "").strip())
        st = str(lic.get("last_status") or "").strip().lower()
        if not key:
            return "Choose option 1 to enter your license key, then option 2 for First Time Setup Config."
        if st != "active":
            return "Choose option 1 to verify or update your license key, then try again."

    if not cfg.get("first_setup_completed"):
        return "Choose option 2 — First Time Setup Config — to pick your Roblox package and options."

    try:
        entries = enabled_package_entries(cfg)
    except Exception:
        entries = []
    if not entries:
        return "Choose option 3 — Setup / Edit Config — then Package, and enable at least one package."

    return "Choose option 4 — Start — when you are ready to launch."


def build_onboarding_lines(
    cfg: dict[str, Any] | None,
    *,
    dev_mode: bool = False,
    version: str = VERSION,
    product: str = PRODUCT_NAME,
) -> list[str]:
    """Human-readable lines shown above the main menu (no URLs, no shame)."""
    lines = [
        f"{product} v{version}",
        "",
        "Setup Status",
        "------------",
    ]
    if cfg is None:
        lines.extend(
            [
                "Config: Not loaded",
                "",
                "Next Step:",
                "Install or repair DENG, then run deng-rejoin again.",
            ]
        )
        return lines

    lines.append(_license_ui_line(cfg, dev_mode=dev_mode))
    lines.append(_config_ui_line(cfg))
    lines.append(_packages_ui_line(cfg))
    lines.append(_private_url_line(cfg))
    lines.append("")
    lines.append(_package_scan_line(cfg))
    lines.append("")
    lines.append("Next Step:")
    lines.append(_next_step_line(cfg, dev_mode=dev_mode))

    ready, ready_extra = _ready_line(cfg, dev_mode=dev_mode)
    if ready:
        lines.extend(ready_extra)

    return lines
