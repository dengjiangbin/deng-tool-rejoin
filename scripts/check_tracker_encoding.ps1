param([string]$TrackerPath = "tracker.lua")
$errors = @()
if (-not (Test-Path $TrackerPath)) { Write-Error "File not found: $TrackerPath"; exit 1 }
$bytes   = [System.IO.File]::ReadAllBytes($TrackerPath)
$content = [System.IO.File]::ReadAllText($TrackerPath, [System.Text.Encoding]::UTF8)

# Strip Lua comment lines before code-pattern checks
$codeOnly = ($content -split "`n" | Where-Object { $_ -notmatch '^\s*--' }) -join "`n"

if ($bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
    $errors += "FAIL  BOM detected: first bytes are EF BB BF"
} else {
    Write-Host "PASS  No UTF-8 BOM. First bytes: 0x$([string]::Format('{0:X2}', $bytes[0])) 0x$([string]::Format('{0:X2}', $bytes[1])) 0x$([string]::Format('{0:X2}', $bytes[2]))"
}

if ($content -notmatch '^--') { $errors += "FAIL  Does not start with '--'" } else { Write-Host "PASS  Starts with '--' (Lua comment)" }
if ($content -match '\[DENG TRACKER\] tracker\.lua loaded') { Write-Host "PASS  Version marker found" } else { $errors += "FAIL  Version marker missing" }

$dc = ([regex]::Matches($codeOnly, '_G\.httpRequest\s*\(')).Count
if ($dc -gt 0) { $errors += "FAIL  $dc direct _G.httpRequest() call(s) in code (not comments)" } else { Write-Host "PASS  No direct _G.httpRequest() calls in code" }

if ($content -match 'performDashboardRequest') { Write-Host "PASS  performDashboardRequest found" } else { $errors += "FAIL  performDashboardRequest missing" }
if ($content -match 'xpcall' -and $content -match 'debug\.traceback') { Write-Host "PASS  xpcall + debug.traceback found" } else { $errors += "FAIL  xpcall/debug.traceback missing" }

$ra = ([regex]::Matches($codeOnly, 'RequestAsync')).Count
if ($ra -gt 0) { $errors += "FAIL  HttpService:RequestAsync found in code (must not be in LocalScript)" } else { Write-Host "PASS  No HttpService:RequestAsync in code" }

Write-Host ""
if ($errors.Count -eq 0) {
    Write-Host "ALL CHECKS PASSED" -ForegroundColor Green; exit 0
} else {
    Write-Host "VALIDATION FAILED:" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}