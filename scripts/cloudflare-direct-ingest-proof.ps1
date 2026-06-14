# Validate Cloudflare direct-ingest routing and produce proof JSON.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root 'site\proofs\cloudflare_direct_ingest_proof.json'
$Probe = Join-Path $Root 'site\proofs\_cf_route_probe.json'
$Eco = Get-Content (Join-Path $Root 'ecosystem.site.json') -Raw | ConvertFrom-Json
$SiteEnv = $Eco.apps | Where-Object { $_.name -eq 'deng-tool-site' } | Select-Object -ExpandProperty env

function Fetch-Json($url, $timeoutSec = 8) {
  try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeoutSec
    $sw.Stop()
    return @{ ok = $true; status = [int]$r.StatusCode; ms = $sw.ElapsedMilliseconds; body = ($r.Content | ConvertFrom-Json) }
  } catch {
    $sw.Stop()
    $resp = $_.Exception.Response
    $status = if ($resp) { [int]$resp.StatusCode } else { 0 }
    return @{ ok = $false; status = $status; ms = $sw.ElapsedMilliseconds; error = $_.Exception.Message }
  }
}

function Curl-Upload($name, $url, $timeoutSec = 25) {
  $hdrFile = [System.IO.Path]::GetTempFileName()
  $bodyFile = [System.IO.Path]::GetTempFileName()
  try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    & curl.exe -sS -m $timeoutSec -D $hdrFile -o $bodyFile -X POST $url `
      -H 'Content-Type: application/json' --data-binary "@$Probe" 2>&1 | Out-Null
    $sw.Stop()
    $hdrText = Get-Content $hdrFile -Raw -ErrorAction SilentlyContinue
    $bodyText = Get-Content $bodyFile -Raw -ErrorAction SilentlyContinue
    $status = 0
    if ($hdrText -match 'HTTP/\S+\s+(\d+)') { $status = [int]$Matches[1] }
    $route = if ($hdrText -match '(?im)^x-deng-tracker-route:\s*(\S+)') { $Matches[1] } else { $null }
    $served = if ($hdrText -match '(?im)^x-deng-served-by:\s*(\S+)') { $Matches[1] } else { $null }
    $proxy = if ($hdrText -match '(?im)^x-deng-via-web-proxy:\s*(\S+)') { $Matches[1] } else { $null }
    return @{
      name = $name
      url = $url
      status = $status
      ms = $sw.ElapsedMilliseconds
      xDengTrackerRoute = $route
      xDengServedBy = $served
      xDengViaWebProxy = $proxy
      directIngest = ($route -eq 'direct-ingest' -and $served -eq 'deng-tracker-ingest')
      webProxyFallback = ($route -eq 'web-proxy-fallback')
      bodyPreview = if ($bodyText) { $bodyText.Substring(0, [Math]::Min(200, $bodyText.Length)) } else { $null }
      headersPreview = ($hdrText -split "`n" | Select-Object -First 15) -join '; '
    }
  } finally {
    Remove-Item $hdrFile, $bodyFile -ErrorAction SilentlyContinue
  }
}

function Burst-Ok($url, $count, $timeoutSec = 20) {
  $ok = 0
  for ($i = 0; $i -lt $count; $i++) {
    try {
      $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec $timeoutSec
      if ($r.StatusCode -eq 200) { $ok++ }
    } catch {}
  }
  return "$ok/$count"
}

$proof = @{
  timestamp = (Get-Date).ToUniversalTime().ToString('o')
  deployMarker = $SiteEnv.TOOL_SITE_ASSET_VERSION
  gitCommit = $SiteEnv.GIT_COMMIT
  cloudflaredIngressValidate = 'pending'
  metricsBefore = @{}
  metricsAfter = @{}
  uploads = @{}
  health = @{}
  bursts = @{}
  oauth = @{}
  disk = @{}
  sessions = @{}
  fallbackStillEnabled = $true
  conclusion = @{}
}

Write-Host 'Fetching metrics BEFORE...'
$proof.metricsBefore.web8791 = (Fetch-Json 'http://127.0.0.1:8791/api/internal/stability').body
$proof.metricsBefore.ingest8792 = (Fetch-Json 'http://127.0.0.1:8792/api/internal/stability' 5).body

Write-Host 'Sending tracker upload probes...'
$proof.uploads.aioPublic = Curl-Upload 'aio_public' 'https://aio.deng.my.id/api/fishit-tracker/update-backpack'
$proof.uploads.toolPublic = Curl-Upload 'tool_public' 'https://tool.deng.my.id/api/fishit-tracker/update-backpack'
$proof.uploads.local8792 = Curl-Upload 'local_8792' 'http://127.0.0.1:8792/api/fishit-tracker/update-backpack' 10
$proof.uploads.local8791Proxy = Curl-Upload 'local_8791_proxy' 'http://127.0.0.1:8791/api/fishit-tracker/update-backpack' 15

Start-Sleep -Seconds 3
Write-Host 'Fetching metrics AFTER...'
$proof.metricsAfter.web8791 = (Fetch-Json 'http://127.0.0.1:8791/api/internal/stability').body
$proof.metricsAfter.ingest8792 = (Fetch-Json 'http://127.0.0.1:8792/api/internal/stability' 5).body

$proof.health.web8791 = Fetch-Json 'http://127.0.0.1:8791/health' 10
$proof.health.ingest8792 = Fetch-Json 'http://127.0.0.1:8792/health' 10
$proof.health.aioPublic = Fetch-Json 'https://aio.deng.my.id/health' 15

$proof.bursts.homepage = Burst-Ok 'https://aio.deng.my.id/' 10
$proof.bursts.login = Burst-Ok 'https://aio.deng.my.id/login' 10

try {
  $oauthStart = Invoke-WebRequest -Uri 'https://aio.deng.my.id/auth/discord' -MaximumRedirection 0 -UseBasicParsing -TimeoutSec 20 -ErrorAction SilentlyContinue
  $loc = $oauthStart.Headers.Location
  $proof.oauth = @{
    startStatus = [int]$oauthStart.StatusCode
    usesAioCallback = ($loc -match 'aio\.deng\.my\.id')
    usesToolCallback = ($loc -match 'tool\.deng\.my\.id')
  }
} catch {
  $resp = $_.Exception.Response
  if ($resp) {
    $loc = $resp.Headers['Location']
    $proof.oauth = @{
      startStatus = [int]$resp.StatusCode
      usesAioCallback = ($loc -match 'aio\.deng\.my\.id')
      usesToolCallback = ($loc -match 'tool\.deng\.my\.id')
    }
  }
}

$beforeWebProxy = $proof.metricsBefore.web8791.trackerRoute.webProxyForwardCount
$afterWebProxy = $proof.metricsAfter.web8791.trackerRoute.webProxyForwardCount
$beforeDirect = $proof.metricsBefore.ingest8792.trackerRoute.ingestDirectCount
$afterDirect = $proof.metricsAfter.ingest8792.trackerRoute.ingestDirectCount
$beforeViaProxy = $proof.metricsBefore.ingest8792.trackerRoute.ingestViaProxyCount
$afterViaProxy = $proof.metricsAfter.ingest8792.trackerRoute.ingestViaProxyCount

$proof.routeMetricDelta = @{
  webProxyForwardDelta = $afterWebProxy - $beforeWebProxy
  ingestDirectDelta = $afterDirect - $beforeDirect
  ingestViaProxyDelta = $afterViaProxy - $beforeViaProxy
}

if ($proof.metricsAfter.web8791.disk) { $proof.disk = $proof.metricsAfter.web8791.disk }
if ($proof.metricsAfter.web8791.sessions) { $proof.sessions = $proof.metricsAfter.web8791.sessions.browser }

$directHeaderOk = ($proof.uploads.aioPublic.directIngest -or $proof.uploads.toolPublic.directIngest)
$proxyNotUsedForPublic = -not ($proof.uploads.aioPublic.webProxyFallback -or $proof.uploads.toolPublic.webProxyFallback)
$metricsShowDirect = ($proof.routeMetricDelta.ingestDirectDelta -gt 0) -and ($proof.routeMetricDelta.webProxyForwardDelta -eq 0)

$proof.conclusion = @{
  publicHeadersShowDirectIngest = $directHeaderOk
  publicHeadersAvoidWebProxyFallback = $proxyNotUsedForPublic
  ingestDirectCountIncreased = ($proof.routeMetricDelta.ingestDirectDelta -gt 0)
  webProxyForwardCountUnchangedDuringProbe = ($proof.routeMetricDelta.webProxyForwardDelta -eq 0)
  homepageBurst = $proof.bursts.homepage
  loginBurst = $proof.bursts.login
  oauthUsesAioNotTool = ($proof.oauth.usesAioCallback -and -not $proof.oauth.usesToolCallback)
  directRoutingLikelyActive = ($directHeaderOk -or $metricsShowDirect)
  note = if ($proof.uploads.aioPublic.status -eq 504) { '504 may indicate ingest saturation under live flood, not wrong routing — verify headers/metrics when response arrives.' } else { $null }
}

$proof | ConvertTo-Json -Depth 10 | Set-Content $Out -Encoding UTF8
Write-Host "Proof written to $Out"
Write-Host "Direct headers aio=$($proof.uploads.aioPublic.xDengTrackerRoute) tool=$($proof.uploads.toolPublic.xDengTrackerRoute)"
Write-Host "Metric delta: webProxy+$($proof.routeMetricDelta.webProxyForwardDelta) directIngest+$($proof.routeMetricDelta.ingestDirectDelta)"
Write-Host "Bursts: home=$($proof.bursts.homepage) login=$($proof.bursts.login)"
