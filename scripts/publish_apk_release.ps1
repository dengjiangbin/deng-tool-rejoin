#!/usr/bin/env pwsh
# Publish a signed DENG All In One release APK and refresh releases/android/latest.json
param(
  [string]$VersionName = "2.2.0",
  [int]$VersionCode = 17,
  [string]$BuildMarker = "APK_SYSTEM_BROWSER_DISCORD_AUTH_AIO_2026_06_14"
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
    "$BuildMarker - Discord OAuth opens system browser / Custom Tabs, not WebView.",
    "Default site and API base URL migrated to https://aio.deng.my.id.",
    "OAuth deep-link handoff (deng-aio://auth/callback) plus web-bootstrap session bridge.",
    "Branding updated to DENG All In One across APK UI and download page."
  )
}

$manifestPath = Join-Path $Releases "latest.json"
$json = $manifest | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($manifestPath, $json + "`n", (New-Object System.Text.UTF8Encoding $false))

Write-Output "Published $TargetName"
Write-Output "SHA256: $hash"
Write-Output "Size: $size bytes"
Write-Output "Manifest: $manifestPath"
