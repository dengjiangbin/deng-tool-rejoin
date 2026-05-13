# Termux:Boot Public Setup

Termux:Boot lets Termux run scripts after Android boots.

## Enable DENG Boot Start

```sh
deng-rejoin enable-boot
```

This creates:

```sh
~/.termux/boot/deng-tool-rejoin.sh
```

The script waits briefly, enters `~/.deng-tool/rejoin`, and starts DENG safely through the existing duplicate-agent protection.

## Required User Steps

1. Install Termux:Boot.
2. Open Termux:Boot once.
3. Disable battery optimization for Termux and Termux:Boot if possible.
4. Reboot.
5. Check:

```sh
deng-rejoin-status
```

DENG does not fail if Termux:Boot is not detectable. It creates the script and prints instructions.
