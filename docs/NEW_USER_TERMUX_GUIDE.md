# DENG Tool: Rejoin — New User Termux Setup Guide

Copy and paste into Termux (long-press the terminal, tap **Paste**, then press **Enter**).

If you are lost inside the tool, open **New User Help** (main menu option **5**).

Public installs should use **DENG Tool: Rejoin Panel** in Discord → **Select Version** → copy **Mobile Copy** (not the `main` branch).

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

Optional (helpful before pasting a long install line):

```sh
pkg install -y curl git python sqlite
```

---

## 4. Open DENG Tool: Rejoin Panel in Discord

In your Discord server, open the **DENG Tool: Rejoin Panel** message (license keys + install).

---

## 5. Click Select Version

Press **Select Version** on the panel.

---

## 6. Choose recommended Stable version

Pick the version marked **Stable** (recommended when shown).

---

## 7. Copy Mobile Copy command

Copy the **Mobile Copy** block (same text as **Desktop Copy** if you prefer).

---

## 8. Paste into Termux

Paste the command at the `$` prompt and press **Enter**.

---

## 9. Run deng-rejoin

```sh
deng-rejoin
```

If the command is not found: close Termux fully, open it again, or run `source ~/.bashrc`.

---

## 10. Enter license key

In the menu, choose **1 — Enter / Update License Key**.

Paste your key from the **DENG Tool: Rejoin Panel** (Discord).

---

## 11. Choose First Time Setup Config

Choose **2 — First Time Setup Config** and follow the prompts on screen.

---

## 12. Choose Start

Choose **4 — Start**.

---

## More reading

- [Public install guide](PUBLIC_INSTALL.md) (developers / advanced paths only in later sections)
- [Public user guide](PUBLIC_USER_GUIDE.md)

Repository: [https://github.com/dengjiangbin/deng-tool-rejoin](https://github.com/dengjiangbin/deng-tool-rejoin)
