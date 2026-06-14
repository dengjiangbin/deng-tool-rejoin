#!/usr/bin/env pwsh
# Backup live session storage, clear stale tmp/queue artifacts, clean-restart PM2 ingest+site.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Data = Join-Path $Root "data"
$Sharded = Join-Path $Data "fishit_live_sessions"
$Legacy = Join-Path $Data "fishit_live_sessions.json"
$Ts = Get-Date -Format "yyyyMMdd-HHmmss"
$Backup = Join-Path $Data "backups\fresh-start-$Ts"

Write-Output "=== FRESH START: backup to $Backup ==="
New-Item -ItemType Directory -Force -Path $Backup | Out-Null
if (Test-Path $Legacy) { Copy-Item -Force $Legacy (Join-Path $Backup "fishit_live_sessions.json") }
Get-ChildItem -Path $Data -Filter "fishit_live_sessions.json.*" -ErrorAction SilentlyContinue | ForEach-Object {
  Copy-Item -Force $_.FullName (Join-Path $Backup $_.Name)
}
if (Test-Path $Sharded) {
  Copy-Item -Recurse -Force $Sharded (Join-Path $Backup "fishit_live_sessions")
}

Write-Output "=== Flush + stop PM2 services ==="
pm2 stop deng-tool-site deng-tracker-ingest 2>&1 | Out-String | Write-Output
Start-Sleep -Seconds 8

function Get-PortOwner([int]$Port) {
  $lines = netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING"
  if (-not $lines) { return $null }
  $parts = ($lines[0].Line -split '\s+') | Where-Object { $_ -ne '' }
  return [int]$parts[-1]
}

foreach ($port in @(8791, 8792)) {
  $owner = Get-PortOwner $port
  if ($owner) {
    $pm2Names = @('deng-tool-site', 'deng-tracker-ingest')
    $pm2Ok = $false
    foreach ($n in $pm2Names) {
      $desc = pm2 describe $n 2>$null
      if ($desc -match "pid.*$owner") { $pm2Ok = $true; break }
    }
    if (-not $pm2Ok) {
      Write-Output "Killing orphan PID $owner on port $port"
      Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
    }
  }
}

Start-Sleep -Seconds 2
foreach ($port in @(8791, 8792)) {
  $owner = Get-PortOwner $port
  if ($owner) { throw "Port $port still owned by PID $owner after cleanup" }
  Write-Output "Port $port is free"
}

Write-Output "=== Remove stale .tmp files ==="
$tmpCount = 0
Get-ChildItem -Path $Data -Recurse -Filter "*.tmp" -ErrorAction SilentlyContinue | ForEach-Object {
  Remove-Item -Force $_.FullName
  $tmpCount += 1
}
Write-Output "Removed $tmpCount .tmp files"

Write-Output "=== Archive legacy monolith (keep backup only; sharded is source of truth) ==="
if (Test-Path $Legacy) {
  $archived = Join-Path $Data "fishit_live_sessions.json.archived-$Ts"
  Move-Item -Force $Legacy $archived
  Write-Output "Moved legacy monolith to $archived"
}

Write-Output "=== Start PM2 fresh ==="
Push-Location (Split-Path -Parent $Root)
try {
  pm2 start ecosystem.site.json --only deng-tool-site,deng-tracker-ingest --update-env 2>&1 | Out-String | Write-Output
} finally {
  Pop-Location
}

Start-Sleep -Seconds 6
$webPid = (pm2 pid deng-tool-site 2>$null)
$ingestPid = (pm2 pid deng-tracker-ingest 2>$null)
$p8791 = Get-PortOwner 8791
$p8792 = Get-PortOwner 8792
Write-Output "PM2 deng-tool-site PID=$webPid owns 8791=$($webPid -eq $p8791) (port owner $p8791)"
Write-Output "PM2 deng-tracker-ingest PID=$ingestPid owns 8792=$($ingestPid -eq $p8792) (port owner $p8792)"

node (Join-Path $Root "scripts\collect_fresh_start_proof.js")
Write-Output "Fresh start complete. Backup: $Backup"
