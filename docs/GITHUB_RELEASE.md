# GitHub Release Notes

## Repository

https://github.com/dengjiangbin/deng-tool-rejoin

## Remote

```sh
https://github.com/dengjiangbin/deng-tool-rejoin.git
```

## Raw Installer

```sh
https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh
```

## Public Install Command

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

## Release Checklist

- Version is `1.0.0`
- README starts with public install instructions
- `install.sh` works from raw GitHub and from a cloned repo
- Global commands are created
- `/sdcard/Download/deng-rejoin.py` is created where storage allows
- Tests pass
- Doctor reports Android release, SDK, Download path, root, Roblox package, DB, logs, and lock state
- Security boundaries remain intact

## Update Flow

Users run:

```sh
deng-rejoin-update
```

The update command backs up config/database/logs, pulls or clones from GitHub, refreshes launchers, preserves user data, and runs doctor.
