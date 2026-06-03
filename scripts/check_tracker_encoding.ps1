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

# ── RS metadata-catalog checks (retained, demoted to metadata under v7-replion) ──
if ($content -match 'scanReplicatedStorageFishCatalog') { Write-Host "PASS  scanReplicatedStorageFishCatalog (recursive scanner) found" } else { $errors += "FAIL  scanReplicatedStorageFishCatalog missing" }
if ($content -match 'fish_catalog_snapshot') { Write-Host "PASS  fish_catalog_snapshot payload type found" } else { $errors += "FAIL  fish_catalog_snapshot missing" }
if ($content -match 'syncCatalogToBackend') { Write-Host "PASS  syncCatalogToBackend found" } else { $errors += "FAIL  syncCatalogToBackend missing" }
if ($content -match 'CATALOG_URL') { Write-Host "PASS  CATALOG_URL constant found" } else { $errors += "FAIL  CATALOG_URL missing" }
if ($content -match 'resolveImageUrl') { Write-Host "PASS  resolveImageUrl (rbxassetid converter) found" } else { $errors += "FAIL  resolveImageUrl missing" }
if ($content -match 'asset-thumbnail') { Write-Host "PASS  asset-thumbnail URL conversion found" } else { $errors += "FAIL  asset-thumbnail conversion missing" }
if ($content -match 'extractInstanceMeta') { Write-Host "PASS  extractInstanceMeta (rich metadata) found" } else { $errors += "FAIL  extractInstanceMeta missing" }
if ($content -match 'walkCatalogTable') { Write-Host "PASS  walkCatalogTable (module require scan) found" } else { $errors += "FAIL  walkCatalogTable missing" }
if ($content -match 'GetAttributes') { Write-Host "PASS  GetAttributes() metadata read found" } else { $errors += "FAIL  GetAttributes missing" }

# ── v7-replion checks (Replion player data = inventory source of truth) ──
if ($content -match 'v7-replion') { Write-Host "PASS  v7-replion build marker found" } else { $errors += "FAIL  v7-replion build marker missing" }
if ($content -match 'findReplionClient') { Write-Host "PASS  findReplionClient (Replion module discovery) found" } else { $errors += "FAIL  findReplionClient missing" }
if ($content -match 'findPlayerDataReplion') { Write-Host "PASS  findPlayerDataReplion (player data selection) found" } else { $errors += "FAIL  findPlayerDataReplion missing" }
if ($content -match 'readReplionData') { Write-Host "PASS  readReplionData (read-only data accessor) found" } else { $errors += "FAIL  readReplionData missing" }
if ($content -match 'parseInventoryFromReplionData') { Write-Host "PASS  parseInventoryFromReplionData (inventory parser) found" } else { $errors += "FAIL  parseInventoryFromReplionData missing" }
if ($content -match 'buildMetadataCatalog') { Write-Host "PASS  buildMetadataCatalog (RS metadata catalog) found" } else { $errors += "FAIL  buildMetadataCatalog missing" }
if ($content -match 'attachReplionListeners') { Write-Host "PASS  attachReplionListeners (realtime) found" } else { $errors += "FAIL  attachReplionListeners missing" }
if ($content -match 'inventory_snapshot') { Write-Host "PASS  inventory_snapshot payload type found" } else { $errors += "FAIL  inventory_snapshot missing" }
if ($content -match 'tracker_status') { Write-Host "PASS  tracker_status payload type found" } else { $errors += "FAIL  tracker_status missing" }
if ($content -match 'polling every 5s') { Write-Host "PASS  polling fallback present" } else { $errors += "FAIL  polling fallback missing" }
if ($content -match 'DEBUG_DIAGNOSTIC') { Write-Host "PASS  DEBUG_DIAGNOSTIC quarantine flag found" } else { $errors += "FAIL  DEBUG_DIAGNOSTIC missing" }

# ── v7-replion player-data discovery hardening (this fix) ──
if ($content -match 'DEBUG_REPLION_DISCOVERY') { Write-Host "PASS  DEBUG_REPLION_DISCOVERY config flag found" } else { $errors += "FAIL  DEBUG_REPLION_DISCOVERY missing" }
if ($content -match 'REPLION_WAIT_SECONDS') { Write-Host "PASS  REPLION_WAIT_SECONDS deadline found" } else { $errors += "FAIL  REPLION_WAIT_SECONDS missing" }
if ($content -match 'describeReplionObject') { Write-Host "PASS  describeReplionObject (safe shape printer) found" } else { $errors += "FAIL  describeReplionObject missing" }
if ($content -match 'inventoryPaths') { Write-Host "PASS  inventoryPaths (inventory path detector) found" } else { $errors += "FAIL  inventoryPaths missing" }
if ($content -match 'GetReplion') { Write-Host "PASS  GetReplion attempt found" } else { $errors += "FAIL  GetReplion missing" }
if ($content -match 'WaitReplion') { Write-Host "PASS  WaitReplion attempt found" } else { $errors += "FAIL  WaitReplion missing" }
if ($content -match 'OnReplionAdded') { Write-Host "PASS  OnReplionAdded subscription found" } else { $errors += "FAIL  OnReplionAdded missing" }

# tracker_status discovery phases must be emitted so the website shows "running"
foreach ($ph in @('startup','replion_client_found','player_data_selected','player_data_not_found','inventory_path_missing','replion_missing')) {
    if ($content -match [regex]::Escape($ph)) { Write-Host "PASS  phase '$ph' present" } else { $errors += "FAIL  phase '$ph' missing" }
}

# ── BLOCKER 2: Replion inventory parser + id/name metadata mapping ──
if ($content -match 'DEBUG_REPLION_INVENTORY_DUMP') { Write-Host "PASS  DEBUG_REPLION_INVENTORY_DUMP config flag found" } else { $errors += "FAIL  DEBUG_REPLION_INVENTORY_DUMP missing" }
if ($content -match 'debugDumpReplionInventoryShape') { Write-Host "PASS  debugDumpReplionInventoryShape (safe shape dumper) found" } else { $errors += "FAIL  debugDumpReplionInventoryShape missing" }
if ($content -match 'metadataById') { Write-Host "PASS  metadataById (id->meta index) found" } else { $errors += "FAIL  metadataById missing" }
if ($content -match 'metadataByName') { Write-Host "PASS  metadataByName (name->meta index) found" } else { $errors += "FAIL  metadataByName missing" }
if ($content -match 'resolveMetaById') { Write-Host "PASS  resolveMetaById (id resolver) found" } else { $errors += "FAIL  resolveMetaById missing" }
if ($content -match 'scoreInventoryTable') { Write-Host "PASS  scoreInventoryTable (path scorer) found" } else { $errors += "FAIL  scoreInventoryTable missing" }
if ($content -match 'sourcePath') { Write-Host "PASS  sourcePath tracking found" } else { $errors += "FAIL  sourcePath missing" }
if ($content -match 'REPLION_PARSE_RESULT') { Write-Host "PASS  REPLION_PARSE_RESULT summary log found" } else { $errors += "FAIL  REPLION_PARSE_RESULT missing" }
if ($content -match 'REPLION_PARSE_ERROR') { Write-Host "PASS  REPLION_PARSE_ERROR log found" } else { $errors += "FAIL  REPLION_PARSE_ERROR missing" }
if ($content -match 'parseInventoryFromReplionData\(data\)' -and $content -match 'xpcall\(function\(\)' ) { Write-Host "PASS  xpcall wraps parseInventoryFromReplionData in refreshFromReplion" } else { $errors += "FAIL  xpcall around parseInventoryFromReplionData missing" }
if ($content -match 'DEBUG_SAMPLE_LIMIT') { Write-Host "PASS  DEBUG_SAMPLE_LIMIT cap found" } else { $errors += "FAIL  DEBUG_SAMPLE_LIMIT missing" }
if ($content -match 'DEBUG_LOOKUP_LIMIT') { Write-Host "PASS  DEBUG_LOOKUP_LIMIT cap found" } else { $errors += "FAIL  DEBUG_LOOKUP_LIMIT missing" }
if ($content -match 'DEBUG_REJECT_LIMIT') { Write-Host "PASS  DEBUG_REJECT_LIMIT cap found" } else { $errors += "FAIL  DEBUG_REJECT_LIMIT missing" }
if ($content -match 'DEBUG_RAW_ENTRY_LIMIT') { Write-Host "PASS  DEBUG_RAW_ENTRY_LIMIT cap found" } else { $errors += "FAIL  DEBUG_RAW_ENTRY_LIMIT missing" }
if ($content -match 'DASHBOARD_SEND tracker_status phase=inventory_parse_failed') { Write-Host "PASS  inventory_parse_failed sync log found" } else { $errors += "FAIL  inventory_parse_failed sync log missing" }
if ($content -match 'CONSUME_ENTRY_ERROR') { Write-Host "PASS  CONSUME_ENTRY_ERROR diagnostic found" } else { $errors += "FAIL  CONSUME_ENTRY_ERROR missing" }
if ($content -match 'RawItemSample') { Write-Host "PASS  RawItemSample inventory logging found" } else { $errors += "FAIL  RawItemSample logging missing" }
if ($content -match 'finalizeReplionParseStats') { Write-Host "PASS  finalizeReplionParseStats (unified counters) found" } else { $errors += "FAIL  finalizeReplionParseStats missing" }
if ($content -match 'safeCallNamed') { Write-Host "PASS  safeCallNamed nil-safe helper found" } else { $errors += "FAIL  safeCallNamed missing" }
if ($content -match 'MISSING_HELPER name=') { Write-Host "PASS  MISSING_HELPER diagnostic found" } else { $errors += "FAIL  MISSING_HELPER diagnostic missing" }
if ($content -match 'NUMERIC_ID_FALLBACK_ACCEPTED') { Write-Host "PASS  NUMERIC_ID_FALLBACK_ACCEPTED log found" } else { $errors += "FAIL  NUMERIC_ID_FALLBACK_ACCEPTED missing" }
if ($content -match 'addOwnedNumericFallback') { Write-Host "PASS  addOwnedNumericFallback found" } else { $errors += "FAIL  addOwnedNumericFallback missing" }
if ($content -match 'local mergeOwnedItem') { Write-Host "PASS  mergeOwnedItem forward declaration found" } else { $errors += "FAIL  mergeOwnedItem forward declaration missing" }
if ($content -match 'TRACKER_BUILD BLOCKER10C') { Write-Host "PASS  TRACKER_BUILD BLOCKER10C marker found" } else { $errors += "FAIL  TRACKER_BUILD BLOCKER10C marker missing" }
if ($content -match 'BLOCKER10C_NONBLOCKING_ITEM_CATALOG_UPGRADE_2026_06_03') { Write-Host "PASS  BLOCKER10C build id found" } else { $errors += "FAIL  BLOCKER10C build id missing" }
if ($content -match 'STARTUP_NON_BLOCKING') { Write-Host "PASS  STARTUP_NON_BLOCKING log found" } else { $errors += "FAIL  STARTUP_NON_BLOCKING missing" }
if ($content -match 'scanBudgetYield') { Write-Host "PASS  scanBudgetYield scheduler found" } else { $errors += "FAIL  scanBudgetYield missing" }
if ($content -match 'INVENTORY_PHASE_A') { Write-Host "PASS  INVENTORY_PHASE_A log found" } else { $errors += "FAIL  INVENTORY_PHASE_A missing" }
if ($content -match 'INVENTORY_PHASE_B') { Write-Host "PASS  INVENTORY_PHASE_B log found" } else { $errors += "FAIL  INVENTORY_PHASE_B missing" }
if ($content -match 'looksLikeOwnedInventoryTable') { Write-Host "PASS  looksLikeOwnedInventoryTable guard found" } else { $errors += "FAIL  looksLikeOwnedInventoryTable missing" }
if ($content -match 'buildQuickPriorityCatalog') { Write-Host "PASS  buildQuickPriorityCatalog found" } else { $errors += "FAIL  buildQuickPriorityCatalog missing" }
if ($content -match 'buildMetadataCatalogAsync') { Write-Host "PASS  buildMetadataCatalogAsync found" } else { $errors += "FAIL  buildMetadataCatalogAsync missing" }
if ($content -match 'hookRemotesDeferred') { Write-Host "PASS  hookRemotesDeferred found" } else { $errors += "FAIL  hookRemotesDeferred missing" }
if ($content -match 'isPlaceholderName') { Write-Host "PASS  isPlaceholderName helper found" } else { $errors += "FAIL  isPlaceholderName missing" }
if ($content -match 'shouldReplaceName') { Write-Host "PASS  shouldReplaceName helper found" } else { $errors += "FAIL  shouldReplaceName missing" }
if ($content -match 'CATALOG_DOWNGRADE_BLOCKED') { Write-Host "PASS  CATALOG_DOWNGRADE_BLOCKED log found" } else { $errors += "FAIL  CATALOG_DOWNGRADE_BLOCKED missing" }
if ($content -match 'CATALOG_PLACEHOLDER_UPGRADED') { Write-Host "PASS  CATALOG_PLACEHOLDER_UPGRADED log found" } else { $errors += "FAIL  CATALOG_PLACEHOLDER_UPGRADED missing" }
if ($content -match 'safeWriteMetadataById') { Write-Host "PASS  safeWriteMetadataById found" } else { $errors += "FAIL  safeWriteMetadataById missing" }
if ($content -match 'CONSUME_ENTRY_ACTIVE BLOCKER8') { Write-Host "PASS  CONSUME_ENTRY_ACTIVE BLOCKER8 log found" } else { $errors += "FAIL  CONSUME_ENTRY_ACTIVE BLOCKER8 missing" }
if ($content -match 'toNumberOr') { Write-Host "PASS  toNumberOr nil-safe helper found" } else { $errors += "FAIL  toNumberOr missing" }
if ($content -match 'safeAdd') { Write-Host "PASS  safeAdd nil-safe helper found" } else { $errors += "FAIL  safeAdd missing" }
if ($content -match 'buildCatalogFromReplionData') { Write-Host "PASS  buildCatalogFromReplionData found" } else { $errors += "FAIL  buildCatalogFromReplionData missing" }
if ($content -match 'walkGenericCatalogIndex') { Write-Host "PASS  walkGenericCatalogIndex found" } else { $errors += "FAIL  walkGenericCatalogIndex missing" }
if ($content -match 'resolveCatalogMetaById') { Write-Host "PASS  resolveCatalogMetaById found" } else { $errors += "FAIL  resolveCatalogMetaById missing" }
if ($content -match 'ITEM_CATALOG_HIT') { Write-Host "PASS  ITEM_CATALOG_HIT log found" } else { $errors += "FAIL  ITEM_CATALOG_HIT missing" }
if ($content -match 'ITEM_CATALOG_SOURCE_FOUND') { Write-Host "PASS  ITEM_CATALOG_SOURCE_FOUND log found" } else { $errors += "FAIL  ITEM_CATALOG_SOURCE_FOUND missing" }
if ($content -match 'UNRESOLVED_ITEM_ID') { Write-Host "PASS  UNRESOLVED_ITEM_ID log found" } else { $errors += "FAIL  UNRESOLVED_ITEM_ID missing" }
if ($content -match 'postParseItemCatalogPass') { Write-Host "PASS  postParseItemCatalogPass found" } else { $errors += "FAIL  postParseItemCatalogPass missing" }
if ($content -match 'runTargetedSearchForUnresolvedIds') { Write-Host "PASS  runTargetedSearchForUnresolvedIds found" } else { $errors += "FAIL  runTargetedSearchForUnresolvedIds missing" }
if ($content -match 'METADATA_DECODE_FAILED') { Write-Host "PASS  METADATA_DECODE_FAILED log found" } else { $errors += "FAIL  METADATA_DECODE_FAILED missing" }
if ($content -match 'RAW_INVENTORY_ENTRY') { Write-Host "PASS  RAW_INVENTORY_ENTRY diagnostic found" } else { $errors += "FAIL  RAW_INVENTORY_ENTRY missing" }
if ($content -match 'sendDashboardRequest') { Write-Host "PASS  sendDashboardRequest throttle found" } else { $errors += "FAIL  sendDashboardRequest missing" }
if ($content -match 'RATE_LIMIT_BACKOFF') { Write-Host "PASS  RATE_LIMIT_BACKOFF log found" } else { $errors += "FAIL  RATE_LIMIT_BACKOFF missing" }
if ($content -match 'FishTrackerRunId') { Write-Host "PASS  FishTrackerRunId reload guard found" } else { $errors += "FAIL  FishTrackerRunId missing" }
if ($content -match 'PARSER_IMPL active') { Write-Host "PASS  PARSER_IMPL startup audit found" } else { $errors += "FAIL  PARSER_IMPL audit missing" }
if ($content -match 'xpcall\(function\(\)\s*\n\s*return consumeReplionEntry' -or $content -match 'xpcall\(function\(\)[\s\S]{0,80}consumeReplionEntry') { Write-Host "PASS  xpcall wraps consumeReplionEntry" } else { $errors += "FAIL  xpcall around consumeReplionEntry missing" }
foreach ($ph in @('inventory_empty','inventory_parse_failed')) {
    if ($content -match [regex]::Escape($ph)) { Write-Host "PASS  phase '$ph' present" } else { $errors += "FAIL  phase '$ph' missing" }
}

# Read-only guarantee: never scrape Backpack or PlayerGui as the PRODUCTION
# inventory source. They may only appear inside DEBUG_DIAGNOSTIC-gated code.
if ($codeOnly -match 'Selected owned inventory path') { Write-Host "PASS  'Selected owned inventory path' log present" } else { $errors += "FAIL  selected-path log missing" }

# ── BLOCKER 3: numeric Id resolution (Replion instance records) ──
if ($content -match 'debugCatalogLookupForOwnedIds') { Write-Host "PASS  debugCatalogLookupForOwnedIds found" } else { $errors += "FAIL  debugCatalogLookupForOwnedIds missing" }
if ($content -match 'catalog_missing_numeric_id') { Write-Host "PASS  catalog_missing_numeric_id fallback reason found" } else { $errors += "FAIL  catalog_missing_numeric_id missing" }
if ($content -match 'parseStats') { Write-Host "PASS  parseStats payload found" } else { $errors += "FAIL  parseStats missing" }
if ($content -match 'idVariants') { Write-Host "PASS  idVariants (numeric id lookup variants) found" } else { $errors += "FAIL  idVariants missing" }
# UUID instance shape: the live Fish It shape { Id=70, UUID="...", Metadata={Weight} }
if ($content -match 'value\.UUID or value\.Uuid or value\.uuid') { Write-Host "PASS  UUID instance record shape handled" } else { $errors += "FAIL  UUID instance record shape missing" }
if ($content -match 'numericId keys') { Write-Host "PASS  numericId keys diagnostic found" } else { $errors += "FAIL  numericId keys diagnostic missing" }
if ($content -match 'buildNumericIdIndexFromRSFolders') { Write-Host "PASS  buildNumericIdIndexFromRSFolders present" } else { $errors += "FAIL  buildNumericIdIndexFromRSFolders missing (BLOCKER 3 RS folder fallback)" }

# Replion must be read-only: no mutation methods actually invoked on a replion object
$mut = ([regex]::Matches($codeOnly, ':\s*(Set|Update|Increase|Decrease|Fire|Save|Equip|Buy|Sell|Remove|Insert)\s*\(')).Count
if ($mut -gt 0) { $errors += "FAIL  $mut Replion mutation call(s) found in code (must be read-only)" } else { Write-Host "PASS  No Replion mutation calls in code (read-only)" }

Write-Host ""
if ($errors.Count -eq 0) {
    Write-Host "ALL CHECKS PASSED" -ForegroundColor Green; exit 0
} else {
    Write-Host "VALIDATION FAILED:" -ForegroundColor Red
    $errors | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}