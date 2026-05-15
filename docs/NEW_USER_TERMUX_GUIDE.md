# DENG Tool: Rejoin — New User Termux Setup Guide

Copy and paste into Termux (long-press the terminal, tap **Paste**, then press **Enter**).

If you are lost inside the tool, open **New User Help** (main menu option **5**).

---

## 1. Download Termux

Install **Termux** from [F-Droid](https://f-droid.org/) or another trusted source.

Open Termux and wait until you see a command prompt (usually `$`).

---

## 2. Configure Root & Termux

Enable **Magisk**, **Kitsune**, **KernelSU**, **LSPosed**, or **Root Permission** if your device or cloud phone uses one.

Open:

**Magisk** / **Kitsune** / **KernelSU** / **LSPosed** / **Root Permission**

Then go to:

**Superuser**

Make sure **Termux** is granted root access.

**Note:** If you cannot find Termux in Magisk, Kitsune, KernelSU, LSPosed, or Root Permission, skip this step and continue.

---

## 3. Prepare Termux

Update packages:

```sh
pkg update -y && pkg upgrade -y
```

Optional (the installer can install these too):

```sh
pkg install -y curl git python sqlite
```

---

## 4. Install DENG Tool: Rejoin

```sh
curl -fsSL https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh -o install.sh && bash install.sh
```

Wget alternative:

```sh
wget -O install.sh https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/install.sh && bash install.sh
```

---

## 5. Open DENG Tool: Rejoin

```sh
deng-rejoin
```

If the command is not found: close Termux fully, open it again, or run `source ~/.bashrc`.

---

## 6. Enter License Key

In the menu, choose **1 — Enter / Update License Key**.

Paste your key from the **DENG Tool: Rejoin Key Panel** (Discord).

---

## 7. First Time Setup

Choose **2 — First Time Setup Config** and follow the prompts on screen.

---

## 8. Start

Choose **4 — Start**.

---

## More reading

- [Public install guide](PUBLIC_INSTALL.md)
- [Public user guide](PUBLIC_USER_GUIDE.md)

Repository: [https://github.com/dengjiangbin/deng-tool-rejoin](https://github.com/dengjiangbin/deng-tool-rejoin)
