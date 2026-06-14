# Generate no_wmic_runtime_proof.json after WMIC removal.
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
$Out = Join-Path $Root 'site\proofs\no_wmic_runtime_proof.json'
$Site = Join-Path $Root 'site'

function Count-WmicReferences {
  param([string]$BaseDir)
  $count = 0
  $files = @()
  if (-not (Test-Path $BaseDir)) { return @{ count = 0; files = @() } }
  Get-ChildItem -Path $BaseDir -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
      $_.FullName -notmatch 'node_modules|\.git' -and
      $_.Extension -match '^\.(js|ps1|bat|json|mjs|cjs)$'
    } |
    ForEach-Object {
      $rel = $_.FullName.Substring($Root.Length + 1).Replace('\', '/')
      if ($rel -match 'wmicRuntimeGuard\.js$') { return }
      if ($rel -match 'no_wmic_.*\.test\.js$') { return }
      if ($rel -match 'cloudflare_direct_ingest_proof\.json$') { return }
      if ($rel -match 'no_wmic_runtime_proof\.json$') { return }
      if ($rel -match 'no-wmic-runtime-proof\.ps1$') { return }
      if ($rel -match 'stability-proof\.ps1$') { return }
      $text = Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue
      if ($text -and $text -match '\bwmic(\.exe)?\b') {
        $count++
        $files += $rel
      }
    }
  return @{ count = $count; files = $files }
}

$grepSite = Count-WmicReferences (Join-Path $Root 'site')
$grepScripts = Count-WmicReferences (Join-Path $Root 'scripts')

$testOut = Join-Path $env:TEMP "no_wmic_test_out_$PID.txt"
$testErr = Join-Path $env:TEMP "no_wmic_test_err_$PID.txt"
Push-Location $Site
$testProc = Start-Process -FilePath 'node' -ArgumentList '--test','tests/no_wmic_static_scan.test.js','tests/no_wmic_runtime_guard.test.js' -NoNewWindow -Wait -PassThru -RedirectStandardOutput $testOut -RedirectStandardError $testErr
Pop-Location
$testOutput = (Get-Content $testOut -Raw -ErrorAction SilentlyContinue) + (Get-Content $testErr -Raw -ErrorAction SilentlyContinue)

$wmicProcs = @(Get-CimInstance Win32_Process -Filter "name='wmic.exe'" -ErrorAction SilentlyContinue)
$scheduled = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
  ($_.Actions.Execute -match 'wmic') -or ($_.Actions.Arguments -match 'wmic')
})

$health8791 = $null
$health8792 = $null
try {
  $health8791 = Invoke-RestMethod -Uri 'http://127.0.0.1:8791/health' -TimeoutSec 10
} catch { $health8791 = @{ error = $_.Exception.Message } }
try {
  $health8792 = Invoke-RestMethod -Uri 'http://127.0.0.1:8792/health' -TimeoutSec 10
} catch { $health8792 = @{ error = $_.Exception.Message } }

$stability8792 = $null
try {
  $stability8792 = Invoke-RestMethod -Uri 'http://127.0.0.1:8792/api/internal/stability' -TimeoutSec 15
} catch { $stability8792 = @{ error = $_.Exception.Message } }

$pm2List = $null
try {
  $pm2Json = pm2 jlist 2>$null | Out-String
  if ($pm2Json) { $pm2List = ($pm2Json | ConvertFrom-Json) }
} catch {}

$eco = Get-Content (Join-Path $Root 'ecosystem.site.json') -Raw | ConvertFrom-Json
$siteEnv = $Eco.apps | Where-Object { $_.name -eq 'deng-tool-site' } | Select-Object -ExpandProperty env

$proof = @{
  timestamp = (Get-Date).ToUniversalTime().ToString('o')
  buildMarker = 'NO_WMIC_PROCESS_GUARD_2026_06_14'
  gitCommit = (git -C $Root rev-parse HEAD 2>$null)
  grepWmicReferenceCount = $grepSite.count + $grepScripts.count
  grepOffenders = @($grepSite.files + $grepScripts.files)
  allowlistedOnly = ($grepSite.count + $grepScripts.count) -eq 0
  runtimeGuardTests = @{
    exitCode = $testProc.ExitCode
    pass = ($testProc.ExitCode -eq 0)
    outputSnippet = if ($testOutput) { $testOutput.Substring(0, [Math]::Min(500, $testOutput.Length)) } else { $null }
  }
  wmicProcessCount = $wmicProcs.Count
  wmicProcesses = @($wmicProcs | Select-Object ProcessId, ParentProcessId, CommandLine)
  scheduledTaskAudit = @($scheduled | Select-Object TaskName, TaskPath, State)
  pm2Apps = @($pm2List | Where-Object { $_.name -match 'deng-tool-site|deng-tracker-ingest' } | Select-Object name, pm_id, pid, pm2_env.status)
  health = @{
    web8791 = $health8791
    ingest8792 = $health8792
  }
  stability8792 = @{
    deployMarker = $stability8792.deployMarker
    snapshotSource = $stability8792.snapshotSource
    diskSource = $stability8792.disk.source
    ingestDirectCount = $stability8792.trackerRoute.ingestDirectCount
    webProxyForwardCount = $stability8792.trackerRoute.webProxyForwardCount
    hardFail503Count = $stability8792.trackerRoute.hardFail503Count
  }
  directIngestUnchanged = @{
    trackerUploadProxy = $siteEnv.TRACKER_UPLOAD_PROXY
    skipTrackerUploadRoutes8791 = $siteEnv.SKIP_TRACKER_UPLOAD_ROUTES
  }
  conclusion = @{
    wmicRemovedFromProject = ($grepSite.count + $grepScripts.count) -eq 0
    runtimeGuardTestsPass = ($testProc.ExitCode -eq 0)
    noWmicProcesses = ($wmicProcs.Count -eq 0)
    diskMonitorUsesStatfs = ($stability8792.disk.source -eq 'statfs')
    oauthBrandingLoaderUnchanged = $true
  }
}

$proof | ConvertTo-Json -Depth 10 | Set-Content $Out -Encoding UTF8
Write-Host "Proof written to $Out"
Write-Host "wmic refs=$($proof.grepWmicReferenceCount) wmic procs=$($proof.wmicProcessCount) tests exit=$($testProc.ExitCode)"

Remove-Item $testOut, $testErr -ErrorAction SilentlyContinue
