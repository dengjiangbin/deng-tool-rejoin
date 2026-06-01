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

if ($content -match 'buildCatalogFromRS') { Write-Host "PASS  buildCatalogFromRS found" } else { $errors += "FAIL  buildCatalogFromRS missing" }
if ($content -match 'STAT_LABEL_DENYLIST') { Write-Host "PASS  STAT_LABEL_DENYLIST found" } else { $errors += "FAIL  STAT_LABEL_DENYLIST missing" }
if ($content -match 'normalizeName')       { Write-Host "PASS  normalizeName found" }       else { $errors += "FAIL  normalizeName missing" }
if ($content -match 'resolveFishMeta')     { Write-Host "PASS  resolveFishMeta found" }     else { $errors += "FAIL  resolveFishMeta missing" }
if ($content -match 'rejectInventoryLabel') { Write-Host "PASS  rejectInventoryLabel found" } else { $errors += "FAIL  rejectInventoryLabel missing" }
if ($content -match 'scanOwnedInventory') { Write-Host "PASS  scanOwnedInventory found" } else { $errors += "FAIL  scanOwnedInventory missing" }
if ($content -match 'mergeItem') { Write-Host "PASS  mergeItem found" } else { $errors += "FAIL  mergeItem missing" }
if ($content -match 'walkInventoryTable') { Write-Host "PASS  walkInventoryTable found" } else { $errors += "FAIL  walkInventoryTable missing" }
if ($content -match 'DEBUG_VERBOSE_INVENTORY') { Write-Host "PASS  DEBUG_VERBOSE_INVENTORY config found" } else { $errors += "FAIL  DEBUG_VERBOSE_INVENTORY missing" }

# ── v7-rs-catalog checks ──────────────────────────────────────────
if ($content -match 'v7-rs-catalog') { Write-Host "PASS  v7-rs-catalog build marker found" } else { $errors += "FAIL  v7-rs-catalog build marker missing" }
if ($content -match 'scanReplicatedStorageFishCatalog') { Write-Host "PASS  scanReplicatedStorageFishCatalog (recursive scanner) found" } else { $errors += "FAIL  scanReplicatedStorageFishCatalog missing" }
if ($content -match 'fish_catalog_snapshot') { Write-Host "PASS  fish_catalog_snapshot payload type found" } else { $errors += "FAIL  fish_catalog_snapshot missing" }
if ($content -match 'syncCatalogToBackend') { Write-Host "PASS  syncCatalogToBackend found" } else { $errors += "FAIL  syncCatalogToBackend missing" }
if ($content -match 'CATALOG_URL') { Write-Host "PASS  CATALOG_URL constant found" } else { $errors += "FAIL  CATALOG_URL missing" }
if ($content -match 'resolveImageUrl') { Write-Host "PASS  resolveImageUrl (rbxassetid converter) found" } else { $errors += "FAIL  resolveImageUrl missing" }
if ($content -match 'asset-thumbnail') { Write-Host "PASS  asset-thumbnail URL conversion found" } else { $errors += "FAIL  asset-thumbnail conversion missing" }
if ($content -match 'extractInstanceMeta') { Write-Host "PASS  extractInstanceMeta (rich metadata) found" } else { $errors += "FAIL  extractInstanceMeta missing" }
if ($content -match 'walkCatalogTable') { Write-Host "PASS  walkCatalogTable (module require scan) found" } else { $errors += "FAIL  walkCatalogTable missing" }
if ($content -match 'GetAttributes') { Write-Host "PASS  GetAttributes() metadata read found" } else { $errors += "FAIL  GetAttributes missing" }

Write-Host ""
if ($errors.Count -eq 0) {
    Write-Host "ALL CHECKS PASSED" -ForegroundColor Green; exit 0
} else {
    Write-Host "VALIDATION FAILED:" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}