# Architecture

## Module Overview

- `deng_tool_rejoin.py`: CLI entrypoint
- `commands.py`: command parsing and user-facing workflows
- `menu.py`: public Termux menu that dispatches to command handlers
- `constants.py`: version, paths, limits, regexes
- `platform_detect.py`: Android release, SDK, Termux prefix, and Download path detection
- `launcher_file.py`: generated `/sdcard/Download/deng-rejoin.py` launcher support
- `banner.py`: pink ASCII Termux banner
- `logger.py`: local rotating logs with URL masking
- `db.py`: SQLite schema and storage helpers
- `config.py`: config defaults, validation, persistence
- `url_utils.py`: Roblox URL validation, normalization, masking
- `backoff.py`: capped exponential backoff
- `android.py`: Android shell/root command boundary
- `launcher.py`: one rejoin attempt
- `monitor.py`: network/package/process/foreground checks
- `lockfile.py`: PID and duplicate-agent safety
- `supervisor.py`: auto-rejoin state machine
- `doctor.py`: diagnostics

## Data Flow

Setup creates directories, config, and SQLite. Commands load config from JSON, validate it, mirror values into SQLite, and use Android helpers for device actions. Rejoin attempts write logs, `events`, and `rejoin_attempts`. The supervisor writes `heartbeats` each loop.

Public install creates global Termux wrappers in `$PREFIX/bin` and a market-style Python launcher in detected public Download folders where storage permission allows.

## Config Flow

Config lives at `~/.deng-tool/rejoin/config.json` and is mirrored into the SQLite `config` table. Every save validates package names, launch mode, URLs, booleans, numeric limits, and log level.

Config also stores detected `android_release`, `android_sdk`, and `download_dir` for status and diagnostics.

## Rejoin State Machine

States:

- `disabled`
- `checking_environment`
- `healthy`
- `network_down`
- `roblox_not_installed`
- `roblox_not_running`
- `launching`
- `waiting_after_launch`
- `backoff`
- `error`

The loop never runs tight. It sleeps according to `health_check_interval_seconds`, `foreground_grace_seconds`, `reconnect_delay_seconds`, and capped exponential backoff.

## Root vs Non-Root

Non-root mode can launch Roblox through `monkey` or `am start`. Root mode may force-stop the configured package first. Root is detected with a timeout and never required.

## V2 Web Panel Integration

V1 is local only. V2 can add a backend and dashboard by consuming the existing config, heartbeat, event, and rejoin-attempt data model. Remote commands must remain predefined and must never become arbitrary shell execution.
