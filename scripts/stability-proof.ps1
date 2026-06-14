# Production stability proof script (Tasks A-G)
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root 'site\proofs\stability_followup_proof.json'
$results = @{
  timestamp = (Get-Date).ToUniversalTime().ToString('o')
  commit = (git -C $Root rev-parse --short HEAD 2>$null)
  deployMarker = 'STABILITY_FOLLOWUP_2026_06_13'
  disk = @{}
  curls = @{}
  bursts = @{}
  sessions = @{}
  branding = @{}
}

# Disk (Get-CimInstance — no wmic.exe)
$diskRows = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" -ErrorAction SilentlyContinue |
  Select-Object Caption, FreeSpace, Size
$results.disk.raw = $diskRows
$results.disk.source = 'cim'
if ($diskRows) {
  $diskRows | Format-Table -AutoSize | Out-String | Write-Host
}

function Test-Url($name, $url, $method = 'GET', $body = $null) {
  try {
    $params = @{
      Uri = $url
      Method = $method
      UseBasicParsing = $true
      TimeoutSec = 30
    }
    if ($body) {
      $params.Body = $body
      $params.ContentType = 'application/json'
    }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $r = Invoke-WebRequest @params
    $sw.Stop()
    $h = @{}
    $r.Headers.GetEnumerator() | ForEach-Object { $h[$_.Key] = $_.Value }
    return @{
      name = $name
      url = $url
      status = [int]$r.StatusCode
      ms = $sw.ElapsedMilliseconds
      headers = $h
    }
  } catch {
    $resp = $_.Exception.Response
    $status = if ($resp) { [int]$resp.StatusCode } else { 0 }
    return @{ name = $name; url = $url; status = $status; error = $_.Exception.Message }
  }
}

$urls = @(
  @('health_public', 'https://aio.deng.my.id/health'),
  @('login_public', 'https://aio.deng.my.id/login'),
  @('oauth_public', 'https://aio.deng.my.id/auth/discord'),
  @('tracker_aio', 'https://aio.deng.my.id/api/fishit-tracker/update-backpack', 'POST', '{"username":"proof_user","userId":999999001}'),
  @('tracker_tool', 'https://tool.deng.my.id/api/fishit-tracker/update-backpack', 'POST', '{"username":"proof_user","userId":999999001}'),
  @('health_web_local', 'http://127.0.0.1:8791/health'),
  @('health_ingest_local', 'http://127.0.0.1:8792/health'),
  @('stability_web', 'http://127.0.0.1:8791/api/internal/stability'),
  @('stability_ingest', 'http://127.0.0.1:8792/api/internal/stability')
)
foreach ($u in $urls) {
  $r = if ($u.Length -ge 4) { Test-Url $u[0] $u[1] $u[2] $u[3] } else { Test-Url $u[0] $u[1] }
  $results.curls[$u[0]] = $r
  Write-Host "$($u[0]): status=$($r.status) ms=$($r.ms) route=$($r.headers.'X-DENG-Tracker-Route') served=$($r.headers.'X-DENG-Served-By')"
}

# Burst homepage
$homeOk = 0
for ($i = 0; $i -lt 10; $i++) {
  $r = Test-Url "home_$i" 'https://aio.deng.my.id/'
  if ($r.status -eq 200) { $homeOk++ }
}
$results.bursts.homepage = "$homeOk/10"

# Burst login
$loginOk = 0
for ($i = 0; $i -lt 20; $i++) {
  $r = Test-Url "login_$i" 'https://aio.deng.my.id/login'
  if ($r.status -eq 200) { $loginOk++ }
}
$results.bursts.login = "$loginOk/20"

# Session count
$sessDir = Join-Path $Root 'data\site-sessions'
$jsonCount = (Get-ChildItem $sessDir -Filter '*.json' -ErrorAction SilentlyContinue | Measure-Object).Count
$tmpCount = (Get-ChildItem $sessDir -Filter '*.tmp' -ErrorAction SilentlyContinue | Measure-Object).Count
$results.sessions = @{ jsonCount = $jsonCount; tmpCount = $tmpCount; dir = $sessDir }

# Branding check
try {
  $home = (Invoke-WebRequest -Uri 'https://aio.deng.my.id/' -UseBasicParsing -TimeoutSec 30).Content
  $results.branding.hasAllInOne = $home -match 'DENG All In One'
  $results.branding.noDengToolRegression = -not ($home -match 'DENG Tool Rejoin')
} catch {}

$results | ConvertTo-Json -Depth 8 | Set-Content $Out -Encoding UTF8
Write-Host "Proof written to $Out"
Write-Host "Homepage burst: $($results.bursts.homepage)"
Write-Host "Login burst: $($results.bursts.login)"
Write-Host "Sessions: json=$jsonCount tmp=$tmpCount"
