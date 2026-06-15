#!/usr/bin/env pwsh
# Publish a signed DENG All In One release APK and refresh releases/android/latest.json
param(
  [string]$VersionName = "2.2.7",
  [int]$VersionCode = 24,
  [string]$BuildMarker = "APK_SHELL_AUTH_IMAGES_2026_06_15"
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
    "$BuildMarker (v2.2.7) - Discord login no longer shows a false 'login failed' after success: once /api/aio/auth/me == 200 the attempt is locked (APK_AUTH_SUCCESS_LOCKED) and every later timeout/poll/failure handler is cancelled/suppressed (APK_AUTH_FAILURE_SUPPRESSED_AFTER_SUCCESS).",
    "App now pulls itself to the foreground over the Chrome Custom Tab after the deep link / status poll completes, so users no longer have to close the browser manually; faster status polling makes sign-in feel quicker.",
    "App shell respects Android system bars: WebView content is inset below the status bar and the bottom navigation is always visible above the gesture bar (no more raw mobile-browser look).",
    "Bottom navigation is exactly four tabs - Live Tracker (default), Rejoin, Packages, Settings. Dashboard and Inventory tabs are removed from the APK; duplicate in-page website mobile tabs are hidden in APK mode.",
    "Manual image overrides for Runic Stone, Love Totem and Shiny Totem (override wins over broken catalog/gameDB art); IMAGE_OVERRIDE_MATCH is logged server-side and via the WebView console (logcat tag DengTrackerImages).",
    "Base URL: https://aio.deng.my.id. No username/password login and no long-lived token stored in the app."
  )
}

$manifestPath = Join-Path $Releases "latest.json"
$json = $manifest | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($manifestPath, $json + "`n", (New-Object System.Text.UTF8Encoding $false))

Write-Output "Published $TargetName"
Write-Output "SHA256: $hash"
Write-Output "Size: $size bytes"
Write-Output "Manifest: $manifestPath"
