# Security Policy

DENG Tool: Rejoin is intentionally limited to local Android app/device management.

## No Credentials

The tool must never request, store, read, log, transmit, or use:

- Roblox passwords
- Roblox cookies
- `.ROBLOSECURITY`
- Browser cookies
- Session tokens
- Two-factor codes
- Account credentials
- Private authentication secrets

## No Gameplay Automation

The tool does not implement gameplay automation, auto farming, macro gameplay loops, anti-AFK bypass, captcha bypass, anti-cheat bypass, memory editing, packet manipulation, script injection, exploit execution, fake user activity, hidden remote shells, or arbitrary remote command execution.

## Root Usage

Root is optional. If available and enabled, DENG Tool: Rejoin may use `su` or `tsu` only for explicit Android app-management commands:

- Check harmless root identity with `id`
- Force-stop the configured Roblox package with `am force-stop <package>`

Root commands are centralized in `agent/android.py`, use short timeouts, validate package names, and are logged in readable local logs.

## Responsible Use

Use this tool only on devices/accounts you control. Keep Roblox login handled by the official Roblox app. Do not modify this project to bypass Roblox systems or automate gameplay.

## Public Trust Summary

DENG Tool: Rejoin only opens or reopens Roblox on your own Android device. It does not ask for Roblox login, does not use cookies, does not inject scripts, does not play the game for you, and does not send arbitrary commands from a website. Root is only used to force-stop the configured Roblox app when you enable root mode.
