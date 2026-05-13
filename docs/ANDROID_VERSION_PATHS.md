# Android Version And Download Paths

DENG detects Android release with:

```sh
getprop ro.build.version.release
```

DENG detects Android SDK with:

```sh
getprop ro.build.version.sdk
```

These values appear in doctor and status output.

## Download Path Detection

DENG checks these public Download paths:

- `/sdcard/Download`
- `/sdcard/download`
- `/storage/emulated/0/Download`
- `/storage/emulated/0/download`

It prefers `/sdcard/Download` when it exists.

## Why Uppercase And Lowercase Exist

Android versions, ROMs, and cloud-phone images differ. Android 10 images sometimes expose lowercase `/sdcard/download`; Android 12+ commonly exposes uppercase `/sdcard/Download`.

## Fallback

If no public Download folder is accessible, DENG creates:

```sh
~/.deng-tool/rejoin/launcher/deng-rejoin.py
```

Recommended command remains:

```sh
deng-rejoin
```
