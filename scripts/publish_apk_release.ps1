#!/usr/bin/env pwsh
# Publish a signed DENG All In One release APK and refresh releases/android/latest.json
param(
  [string]$VersionName = "2.2.6",
  [int]$VersionCode = 23,
  [string]$BuildMarker = "APK_MOBILE_AUTH_WEBVIEW_BOOTSTRAP_2026_06_15"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Android = Join-Path $Root "android"
$Releases = Join-Path $Root "releases\android"
$OutApk = Join-Path $Android "app\build\outputs\apk\release\app-release.apk"
$TargetName = "deng-all-in-one-apk-v$VersionName.apk"
$TargetPath = Join-Path $Releases $TargetName

Push-Location $Android
try {
  & .\gradlew.bat clean assembleRelease
  if ($LASTEXITCODE -ne 0) { throw "assembleRelease failed with exit $LASTEXITCODE" }
} finally {
  Pop-Location
}

if (-not (Test-Path $OutApk)) { throw "Missing release APK at $OutApk" }

Copy-Item -Force $OutApk $TargetPath
$hash = (Get-FileHash -Path $TargetPath -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $TargetPath).Length
$releasedAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")

$manifest = [ordered]@{
  app_name = "DENG All In One"
  version_name = $VersionName
  version_code = $VersionCode
  file_name = $TargetName
  sha256 = $hash
  size_bytes = $size
  released_at = $releasedAt
  build_marker = $BuildMarker
  min_sdk = 26
  purpose = "Monitoring companion for DENG All In One (aio.deng.my.id)"
  changelog = @(
    "$BuildMarker (v2.2.6) - Hardened the APK Discord login handoff and added full runtime auth diagnostics.",
    "After Discord OAuth the app loads https://aio.deng.my.id/mobile-auth/consume inside the persistent app WebView; the server sets the real deng_sid session cookie on its own response.",
    "Consume now returns a 'Signing you in...' bridge page that verifies /api/aio/auth/me == 200 in the same WebView BEFORE opening /tracker, eliminating the 303 race that bounced users back to login.",
    "Added APK_AUTH_* logcat markers for every step (start, custom tab, deep link, consume URL, page started/finished, cookie state, auth/me, final tracker URL, fail reason) plus non-secret server debug headers.",
    "Login uses a short-lived single-use mobile auth code bound to a transaction/state; deep-link return plus status polling fallback. No Discord/long-lived token is stored in the app.",
    "Default landing after login is Live Tracker (first tab); Dashboard is the second tab. Base URL: https://aio.deng.my.id."
  )
}

$manifestPath = Join-Path $Releases "latest.json"
$json = $manifest | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($manifestPath, $json + "`n", (New-Object System.Text.UTF8Encoding $false))

Write-Output "Published $TargetName"
Write-Output "SHA256: $hash"
Write-Output "Size: $size bytes"
Write-Output "Manifest: $manifestPath"
