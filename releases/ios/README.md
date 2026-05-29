# DENG Tool Monitor — iOS releases

iOS builds are **not** distributed like Android APK files. Choose one path:

| Distribution | Website button | Requirement |
|--------------|----------------|---------------|
| `coming_soon` | iOS coming soon | Default until TestFlight/IPA is ready |
| `testflight` | Join iOS TestFlight | `testflight_url` in `latest.json` or `IOS_TESTFLIGHT_URL` |
| `ipa` | Download iOS Test Build | Signed `.ipa` in this folder + registered UDIDs |

Update `latest.json` when shipping a new iOS build. Source project: `ios/DENGMonitor/`.
