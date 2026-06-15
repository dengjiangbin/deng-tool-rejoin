#!/usr/bin/env pwsh
# Publish a signed DENG All In One release APK and refresh releases/android/latest.json
param(
  [string]$VersionName = "2.2.4",
  [int]$VersionCode = 21,
  [string]$BuildMarker = "APK_LOGIN_COOKIE_INTERSTITIAL_LIVE_TRACKER_DEFAULT_2026_06_15"
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
    "$BuildMarker - Fix APK login/auth loop: /auth/web-bridge now sets the deng_sid cookie on a 200 HTML interstitial (not a 302) so the WebView reliably stores the session before loading the tracker.",
    "Default landing after login changed to Live Tracker (first tab); Dashboard is now the second tab.",
    "Tracker account-status false-red fixed: account stays green for the full 10-minute grace after a valid contact; only a confirmed offline (or grace expiry) turns it red.",
    "Default site and API base URL: https://aio.deng.my.id."
  )
}

$manifestPath = Join-Path $Releases "latest.json"
$json = $manifest | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($manifestPath, $json + "`n", (New-Object System.Text.UTF8Encoding $false))

Write-Output "Published $TargetName"
Write-Output "SHA256: $hash"
Write-Output "Size: $size bytes"
Write-Output "Manifest: $manifestPath"
