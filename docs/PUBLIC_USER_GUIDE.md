# Public User Guide

## Open The Menu

```sh
deng-rejoin
```

The pink DENG banner appears, then the local Termux menu.

## Configure Roblox

Choose **Setup or Change Settings**. Setup is a guided menu, not a code or JSON editor. You can set:

- Roblox package name, default `com.roblox.client`
- Launch mode: app, deep link, or Roblox web/private-server URL
- Reconnect delay
- Auto rejoin enabled/disabled
- Root mode enabled/disabled when root is available

## Roblox Package Setup

The package screen automatically scans Android for Roblox-related packages.

If packages are found, DENG shows them in a numbered list and marks `com.roblox.client` as recommended. Choose a number to use that package.

You can also choose **Enter package name manually**. Manual package names are validated and must look like a normal Android package, for example:

```text
com.roblox.client
```

If no package is detected, install Roblox, reopen DENG, and rescan. Some cloud-phone builds use regional package names, so manual entry remains available.

## Private Server URL

Choose web/private-server URL mode and paste your Roblox URL. DENG masks private query values in logs and status, for example:

```text
privateServerLinkCode=***MASKED***
```

## One-Time Rejoin Test

Choose **One-Time Rejoin Test**. DENG records the attempt in SQLite and writes a readable log entry.

## Auto Rejoin

Choose **Start Auto Rejoin**. The supervisor checks network, package state, and foreground/process state where Android allows it. It uses safe delays and exponential backoff.

Choose **Stop Auto Rejoin** to stop only the recorded DENG process.

## Logs

Choose **Logs** or run:

```sh
deng-rejoin-logs
```

Logs include event type, package, root usage, success/failure, and masked URLs.

## Status

Status shows config, running state, latest heartbeat, latest rejoin attempt, latest error, Android release/SDK, root availability, Roblox package, and detected Download path.
