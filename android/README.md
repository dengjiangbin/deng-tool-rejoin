# DENG Tool: Rejoin APK — Android Project

Kotlin + Jetpack Compose source for the **DENG Tool: Rejoin APK** — the
Android monitoring companion for DENG Tool: Rejoin.

* **App name (full):** DENG Tool: Rejoin APK
* **App name (launcher):** Rejoin APK
* **Package id:** `my.id.deng.monitor` *(kept stable so existing installs
  upgrade in-place)*
* **Min SDK:** 26 (Android 8.0+)
* **Target SDK:** 34
* **Backend:** `https://tool.deng.my.id` (override at build time, see below)

> **Note on versioning.** This APK has its own Android release version
> (`versionName` in `app/build.gradle.kts`). That version is **independent**
> of the Rejoin package version selected via the website or Discord
> `Select Version` command. Do not surface the APK version anywhere that
> would imply it equals a Rejoin package version.

## Prerequisites

* JDK 17
* Android SDK with platform 34 + build-tools 34.x
* Android Gradle Plugin 8.5+ (downloaded automatically by the wrapper)

If you don't yet have the Gradle wrapper in this directory, generate it once
from any machine that has Gradle installed:

```bash
cd android
gradle wrapper --gradle-version 8.7
```

The wrapper files (`gradlew`, `gradlew.bat`, `gradle/wrapper/*`) should then
be committed.

## Build

```bash
# debug APK (faster, debuggable)
./gradlew assembleDebug

# release APK (signed with debug key by default — see "Signing" below)
./gradlew assembleRelease

# unit tests
./gradlew test
```

Outputs:

* Debug:   `app/build/outputs/apk/debug/app-debug.apk`
* Release: `app/build/outputs/apk/release/app-release.apk`

## Backend URL override

```bash
./gradlew assembleRelease -PbridgeUrl=https://staging.example.com
```

`BuildConfig.BRIDGE_URL` is consumed by `data.MonitorApi`.

## Signing (production)

The default `release` build type uses the debug keystore so it builds
out-of-the-box. **For public distribution**, configure a real signing config:

1. Generate a keystore (one-time):
   ```bash
   keytool -genkeypair -v -keystore deng-tool-rejoin-apk.jks \
       -keyalg RSA -keysize 4096 -validity 10000 -alias deng-tool-rejoin-apk
   ```
2. Put credentials in `~/.gradle/gradle.properties` (NOT committed):
   ```
   DENG_KEYSTORE_FILE=/abs/path/deng-tool-rejoin-apk.jks
   DENG_KEYSTORE_PASSWORD=...
   DENG_KEY_ALIAS=deng-tool-rejoin-apk
   DENG_KEY_PASSWORD=...
   ```
3. Edit `app/build.gradle.kts` to add a real `signingConfigs { release { ... } }`
   block and reference it from `buildTypes.release.signingConfig`.

## Project layout

```
android/
├── app/
│   ├── src/main/
│   │   ├── AndroidManifest.xml
│   │   ├── kotlin/my/id/deng/monitor/
│   │   │   ├── MainActivity.kt
│   │   │   ├── MonitorApp.kt
│   │   │   ├── data/        # API, models, session store
│   │   │   ├── ui/          # AppRoot, screens, components
│   │   │   │   └── theme/   # Compose theme + tokens
│   │   │   └── util/        # Format
│   │   └── res/
│   └── src/test/kotlin/.../FormatTest.kt
├── build.gradle.kts         # root
├── settings.gradle.kts
└── gradle/libs.versions.toml
```

## Theme

`ui/theme/Color.kt` mirrors the DENG Tool website CSS variables so the app
looks visually identical:

| Variable | Hex |
| --- | --- |
| `--bg-a`   | `#050816` |
| `--bg-b`   | `#111827` |
| `--bg-c`   | `#250a26` |
| `--cyan`   | `#00cfff` |
| `--pink`   | `#ff2fb3` |
| `--purple` (mid-gradient) | `#7b5cff` |

The button gradient (`Cyan → Purple → Pink`) matches the website
`--button-gradient`.

## Publishing the APK

After a successful `./gradlew assembleRelease`:

1. Rename to canonical name:
   `cp app-release.apk ../../releases/android/deng-tool-rejoin-apk-v1.0.0.apk`
2. Compute SHA-256 and update `releases/android/latest.json`
3. Commit and deploy the site — the `/download` page reads `latest.json`
   automatically.

Legacy filenames like `deng-monitor-v*.apk` are still accepted by the
website route for backward compatibility, but new releases should use the
`deng-tool-rejoin-apk-v*.apk` form.
