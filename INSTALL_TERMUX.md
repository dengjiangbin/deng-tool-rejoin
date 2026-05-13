# Termux Install Guide

Install Termux from a current trusted source such as F-Droid or official GitHub releases. Old Play Store builds are often outdated and can fail on modern Android.

## Public Install

Open Termux and run:

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

Wget alternative:

```sh
wget -O install.sh https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh && bash install.sh
```

The installer installs Python, SQLite, curl, git, android-tools when available, and optional `tsu` when available.

## After Install

Start the menu:

```sh
deng-rejoin
```

Run setup:

```sh
deng-rejoin-setup
```

Market-style launcher:

```sh
python /sdcard/Download/deng-rejoin.py
```

Android 10 fallback:

```sh
python /sdcard/download/deng-rejoin.py
```

## Storage Permission

The installer runs `termux-setup-storage` when available. If public storage was denied, run:

```sh
termux-setup-storage
```

Then rerun:

```sh
deng-rejoin setup
```

## Root Is Optional

Without root, DENG can launch Roblox and deep links. With root enabled and granted, DENG can force-stop the configured Roblox package before launching.

## Termux:Boot Is Optional

Enable:

```sh
deng-rejoin enable-boot
```

Then install/open Termux:Boot once, disable battery optimization if possible, reboot, and check:

```sh
deng-rejoin-status
```

## Android 12+ Notes

Android 12+ and many cloud-phone images aggressively restrict background apps. Keep Termux allowed in the background when possible and use Termux:Boot for reboot recovery.

## Signal 4 / Illegal Instruction

If `pkg`, Python, SQLite, or Android shell tools fail with `signal 4` or `Illegal instruction`, your Termux build or Android image may not match the CPU/ABI. Reinstall Termux from a current trusted build or use a compatible Android/cloud-phone image.
