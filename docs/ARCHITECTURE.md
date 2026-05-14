# Architecture

## Module Overview

- `deng_tool_rejoin.py`: CLI entrypoint
- `commands.py`: command parsing and user-facing workflows
- `menu.py`: public Termux menu that dispatches to command handlers
- `constants.py`: version, paths, limits, regexes
- `platform_detect.py`: Android release, SDK, Termux prefix, and Download path detection
- `launcher_file.py`: generated `/sdcard/Download/deng-rejoin.py` launcher support
- `webhook.py`: safe Discord webhook status updates with URL masking
- `snapshot.py`: optional Android screencap snapshots for webhook use
- `window_layout.py`: display-aware grid layout and safe App Cloner XML updates
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

Config also stores detected `android_release`, `android_sdk`, and `download_dir` for status and diagnostics. New configs use `roblox_packages` as objects with `package`, `account_username`, `enabled`, and `username_source`; old `label`, old `roblox_package` strings, and old `roblox_packages` string lists migrate into that object list. Account names are display-only and may come from manual entry, a safe Android app label, or an allowlisted display-name preference key. Package auto-detection uses configurable `package_detection_hints` such as `roblox`, `rblx`, `blox`, and `moons` so App Cloner or cloud-phone packages like `com.moons.*` can be found without relaxing Android package validation.

## Private Test Update Flow

During v1.0.0 private testing, `deng-rejoin-update` updates from GitHub `main`.

For public release-stage updates, the planned architecture is a channel-aware updater:

- stable tags such as `v1.0.0`, `v1.0.1`, `v1.1.0`
- beta tags such as `v1.0.1-beta.1`
- channels: `stable`, `beta`, `dev/main`
- user choices: latest stable, specific stable, beta, dev/main, or stay current

Public users should default to stable tags. Testers can use dev/main. Do not create public release tags until release is explicitly requested.

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
