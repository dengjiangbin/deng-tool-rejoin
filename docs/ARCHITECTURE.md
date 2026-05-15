# Architecture

## Module Overview

- `deng_tool_rejoin.py`: CLI entrypoint
- `commands.py`: command parsing and user-facing workflows
- `menu.py`: public Termux menu that dispatches to command handlers
- `constants.py`: version, paths, limits, regexes
- `platform_detect.py`: Android release, SDK, Termux prefix, and Download path detection
- `launcher_file.py`: generated `/sdcard/Download/deng-rejoin.py` launcher support
- `webhook.py`: safe Discord webhook status updates with URL masking; full 7-category status overview
- `snapshot.py`: optional Android screencap snapshots for webhook use
- `window_layout.py`: display-aware grid layout and safe App Cloner XML updates
- `banner.py`: pink ASCII Termux banner
- `logger.py`: local rotating logs with URL masking
- `db.py`: SQLite schema and storage helpers
- `config.py`: config defaults, validation, persistence; nested `license` section
- `url_utils.py`: Roblox URL validation, normalization, masking
- `backoff.py`: capped exponential backoff
- `android.py`: Android shell/root command boundary
- `launcher.py`: one rejoin attempt
- `monitor.py`: network/package/process/foreground checks
- `lockfile.py`: PID and duplicate-agent safety
- `supervisor.py`: auto-rejoin state machine; `MultiPackageSupervisor.get_status_snapshot()`
- `doctor.py`: diagnostics
- `license.py`: license key utilities (DENG-XXXX-XXXX-XXXX-XXXX format, install_id, device summary)
- `license_store.py`: `BaseLicenseStore` interface + `LocalJsonLicenseStore` implementation
- `license_panel.py`: Discord license panel embed/button builders (no runtime bot dependency)
- `keystore.py`: local JSON keystore (legacy flat key format; DENG_DEV=1 bypass)

## Data Flow

Setup creates directories, config, and SQLite. Commands load config from JSON, validate it, mirror values into SQLite, and use Android helpers for device actions. Rejoin attempts write logs, `events`, and `rejoin_attempts`. The supervisor writes `heartbeats` each loop.

Public install creates global Termux wrappers in `$PREFIX/bin` and a market-style Python launcher in detected public Download folders where storage permission allows.

## Config Flow

Config lives at `~/.deng-tool/rejoin/config.json` and is mirrored into the SQLite `config` table. Every save validates package names, launch mode, URLs, booleans, numeric limits, and log level.

Each `roblox_packages` entry supports `package`, `app_name`, `account_username`, optional per-package `private_server_url`, and toggles for low graphics / auto reopen / auto reconnect. Global `private_server_url` applies when a package omits its own. URLs are masked in logs and normal CLI output.

Nested `package_detection` drives root-aware discovery (`pm` / `cmd package` listing, launchability via `resolve-activity`, `dumpsys` labels, and configurable hint fragments). Hints such as `roblox`, `rblx`, `blox`, `moon`, `moons`, `lite`, and `clone` are **aids only** — they are not treated as a fixed allow-list. Manual package entry remains available when nothing matches. Legacy flat `package_detection_hints` is kept in sync with `package_detection.hints`.

On every **Start**, DENG runs **mandatory safe cache cleanup** (e.g. `cache`, `code_cache`, `files/tmp`) via root `find -delete` — never `pm clear` and never full app data, logins, cookies, or shared_prefs. Optional **low graphics** merges known keys into discovered Roblox client JSON under the package `files/` tree when a safe, understood file is found (backup + verify), and skips otherwise.

Nested `supervisor` configures launch grace, health interval, restart backoff, hourly restart caps, and global auto-reopen / auto-reconnect behavior coordinated with per-package flags. The **public Start summary** is a **single table**: **#**, **Package**, **Username**, and **State**. Cache/graphics/heartbeat/reconnect detail stay internal, in logs, or under `--verbose` / `--debug` / DEBUG log level — not as extra columns in the default table.

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
