# DENG Tool Monitor — iOS

Native SwiftUI companion for [tool.deng.my.id](https://tool.deng.my.id). Same backend and pairing flow as the Android APK.

## Requirements

- macOS with Xcode 15+
- iOS 17+ deployment target
- Apple Developer account (for device/TestFlight/App Store builds)

## Open the project

1. Open `ios/DENGMonitor/DENGMonitor.xcodeproj` in Xcode.
2. Select the **DENGMonitor** scheme and an iPhone simulator or device.
3. Build & Run (⌘R).

## Backend

Production API base URL is fixed in `Core/AppConfig.swift`:

`https://tool.deng.my.id`

No localhost or staging URLs are permitted in release builds.

## Auth

1. Sign in on the website with Discord.
2. Open **Download** → **Generate Pair Code**.
3. Enter the code in the iOS app (Pair screen).

The app stores `app_session_token` in the **Keychain** and sends `Authorization: Bearer …` on private routes.

## Distribution

| Mode | Website | Notes |
|------|---------|-------|
| `coming_soon` | “iOS coming soon” | Default until TestFlight/IPA is configured |
| `testflight` | “Join iOS TestFlight” | Set `IOS_TESTFLIGHT_URL` or `releases/ios/latest.json` → `testflight_url` |
| `ipa` | “Download iOS Test Build” | Ad-hoc signed IPA in `releases/ios/` |

iOS cannot be sideloaded like Android APK without signing. See `releases/ios/latest.json`.

## Tests

In Xcode: **Product → Test** (⌘U), or:

```bash
xcodebuild test -project ios/DENGMonitor/DENGMonitor.xcodeproj -scheme DENGMonitor -destination 'platform=iOS Simulator,name=iPhone 16'
```

Unit tests cover base URL, exact number formatting, username masking, and JSON model decoding.
