# Web Panel V2 Plan

V1.0.0 intentionally does not include a web dashboard. It prepares the local data model and command boundaries for a future panel.

## Planned Features

- User login
- Device registration
- Per-device token
- Remote config
- Heartbeat dashboard
- Manual rejoin button
- Logs viewer
- Multi-device management

## Future API Endpoints

- `POST /api/device/register`
- `POST /api/device/heartbeat`
- `GET /api/device/config`
- `POST /api/device/events`
- `GET /api/device/commands`
- `POST /api/device/commands/:id/ack`

## Allowed Remote Commands

Remote commands must be predefined only:

- `manual_rejoin`
- `reload_config`
- `disable_auto_rejoin`
- `enable_auto_rejoin`
- `request_status`
- `update_agent`

The website must never send arbitrary shell commands, Python code, Lua scripts, Android commands, or user-provided command strings to devices.

V2 must preserve the V1 safety model: predefined command names only, no remote shell, no gameplay automation, no credential/cookie handling, and no Roblox script injection.

## Security Model

Each device receives a revocable token during registration. Heartbeats and event uploads authenticate with that token. Remote commands are pulled by the device, validated against an allow-list, acknowledged after execution, and logged locally.
