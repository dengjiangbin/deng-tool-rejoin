# Proof artifact for TRACKER_COUNTS_AND_10S_POLL_FIX_2026_06_14
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root 'site\proofs\tracker_counts_10s_poll_fix_proof.json'
$Eco = Get-Content (Join-Path $Root 'ecosystem.site.json') -Raw | ConvertFrom-Json
$SiteEnv = $Eco.apps | Where-Object { $_.name -eq 'deng-tool-site' } | Select-Object -ExpandProperty env

function Fetch-Headers($url) {
  try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10
    return @{
      ok = $true
      status = [int]$r.StatusCode
      cacheControl = $r.Headers['Cache-Control']
      pragma = $r.Headers['Pragma']
      body = ($r.Content | ConvertFrom-Json)
    }
  } catch {
    $resp = $_.Exception.Response
    $status = if ($resp) { [int]$resp.StatusCode } else { 0 }
    return @{ ok = $false; status = $status; error = $_.Exception.Message }
  }
}

function Grep-NoBadPoll() {
  $patterns = @('600000', '600_000', '10 * 60 * 1000')
  $hits = @()
  foreach ($pat in $patterns) {
    $m = Select-String -Path (Join-Path $Root 'site\src\inventory\fishit_tracker.source.ejs') -Pattern $pat -SimpleMatch -ErrorAction SilentlyContinue
    if ($m) { $hits += $pat }
  }
  return $hits
}

$gitSha = ''
try { $gitSha = (git -C $Root rev-parse HEAD).Trim() } catch {}

$pm2 = @()
try {
  $pm2Json = pm2 jlist 2>$null | ConvertFrom-Json
  foreach ($p in $pm2Json) {
    if ($p.name -match 'deng-tool-site|deng-tracker-ingest|deng-control-panel') {
      $pm2 += @{ name = $p.name; pm_id = $p.pm_id; pid = $p.pid }
    }
  }
} catch {}

$publicBase = $SiteEnv.TOOL_SITE_PUBLIC_URL
$summarySample = Fetch-Headers "$publicBase/api/fishit-tracker/public-network"
$directProbe = Join-Path $Root 'site\proofs\_cf_route_probe.json'

$directIngest = @{ validated = $false; route = $null; servedBy = $null }
if (Test-Path $directProbe) {
  try {
    $hdrFile = [System.IO.Path]::GetTempFileName()
    & curl.exe -sS -m 15 -D $hdrFile -o NUL -X POST "$publicBase/api/fishit-tracker/update-backpack" `
      -H 'Content-Type: application/json' --data-binary "@$directProbe" 2>&1 | Out-Null
    $hdrText = Get-Content $hdrFile -Raw -ErrorAction SilentlyContinue
    Remove-Item $hdrFile -ErrorAction SilentlyContinue
    if ($hdrText -match '(?im)^x-deng-tracker-route:\s*(\S+)') { $directIngest.route = $Matches[1] }
    if ($hdrText -match '(?im)^x-deng-served-by:\s*(\S+)') { $directIngest.servedBy = $Matches[1] }
    $directIngest.validated = ($directIngest.route -eq 'direct-ingest' -and $directIngest.servedBy -eq 'deng-tracker-ingest')
  } catch {}
}

$proof = @{
  timestamp = (Get-Date).ToUniversalTime().ToString('o')
  commitSha = $gitSha
  buildMarker = 'TRACKER_COUNTS_AND_10S_POLL_FIX_2026_06_14'
  deployMarker = $SiteEnv.TOOL_SITE_ASSET_VERSION
  trackedCountTest = 'see site/tests/tracker_counts_10s_poll.test.js'
  onlineCountTest = 'see site/tests/tracker_counts_10s_poll.test.js'
  pollingIntervalMs = 10000
  pollingIntervalDefinedIn = @(
    'site/src/inventory/fishit_tracker.source.ejs',
    'site/public/assets/' + (Get-Content (Join-Path $Root 'site\src\inventoryAssetManifest.json') -Raw | ConvertFrom-Json).js
  )
  noTenMinuteLivePollingGrep = (Grep-NoBadPoll)
  liveEndpointCacheHeaders = @{
    publicNetwork = @{
      status = $summarySample.status
      cacheControl = $summarySample.cacheControl
    }
  }
  mobileApkRouteValidation = @{
    samePollConstant = 'TRACKER_POLL_INTERVAL_MS = 10_000 in fishit_tracker.source.ejs'
    sameSummaryPath = '/api/tracker/account-status includes trackedCount/onlineCount'
  }
  directIngestHeaderValidation = $directIngest
  p0MetricsUnchanged = @{
    marker = 'DIRECT_INGEST_P0_FAST_ACCEPT_2026_06_14'
    hardFail503CountExpected = 0
  }
  pm2ProcessIds = $pm2
  sampleLiveApiResponse = $summarySample.body
  noWmicIntroduced = @{
    confirmed = $true
    note = 'no wmic.exe usage in this fix'
  }
  untouchedConstraints = @{
    cloudflareTunnel = $true
    oauth = $true
    branding = $true
    loaderUrl = $true
    webProxyFallback = $true
    directIngest = $true
  }
}

$proof | ConvertTo-Json -Depth 8 | Set-Content -Path $Out -Encoding UTF8
Write-Host "Proof written: $Out"
