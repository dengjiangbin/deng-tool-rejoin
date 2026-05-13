# Public User Guide

## Open The Menu

```sh
deng-rejoin
```

The pink DENG banner appears, then the local Termux menu.

## Configure Roblox

Choose **Setup / Edit Config**. You can set:

- Roblox package name, default `com.roblox.client`
- Launch mode: app, deep link, or Roblox web/private-server URL
- Reconnect delay
- Auto rejoin enabled/disabled
- Root mode enabled/disabled when root is available

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
