-- ================================================================
--  Fish It Unified Tracker  (Replion Player-Data Inventory)
--  Build: v7-replion
--
--  Inventory source of truth is Replion replicated player data.
--  Backpack/PlayerGui are diagnostic only and must not feed public inventory.
--
--  The script:
--    1. Discovers the Replion client module in ReplicatedStorage.
--    2. Finds the player-data Replion and reads a read-only data snapshot.
--    3. Parses real owned inventory (fish / rods / items) from that data.
--    4. Builds a metadata catalog (names / tiers / images) from
--       ReplicatedStorage definitions and merges it onto the inventory.
--    5. Syncs an inventory_snapshot to the dashboard and listens for
--       Replion data changes in real time (with a polling fallback).
--
--  Read-only: no gameplay automation, no remotes fired, no data mutated.
--
--  Usage:
--    loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/tracker.lua"))()
--
--  Kill switch:  _G.StopAutoFish = true
-- ================================================================

-- BLOCKER10J: first executable line — confirms loadstring compiled and ran.
print("[FishTracker] TRACKER_BOOT_BEGIN BLOCKER10J")

-- Kill switch
_G.StopAutoFish = _G.StopAutoFish or false

-- Executor compatibility shims (must not call nil).
if typeof(task) ~= "table" then
    task = {
        wait = function(delayTime) wait(delayTime or 0) end,
        spawn = function(fn) coroutine.wrap(fn)() end,
        defer = function(fn)
            coroutine.wrap(function()
                wait()
                fn()
            end)()
        end,
    }
end

-- Version marker — confirms this build is running after boot shim.
print("[DENG TRACKER] tracker.lua loaded — build v7-replion")

-- Set true to print every item found during parsing (verbose mode).
local DEBUG_VERBOSE_INVENTORY = false

-- Caps for diagnostic printing — prevents flooding Roblox output with 3000+
-- lines that slow parse time and make it hard to read the final result.
local DEBUG_SAMPLE_LIMIT    = 20  -- max parse-sample lines per refresh (per-item)
local DEBUG_LOOKUP_LIMIT    = 20  -- max catalog lookup diagnostic lines
local DEBUG_REJECT_LIMIT    = 20  -- max rejected-entry diagnostic lines
local DEBUG_RAW_ENTRY_LIMIT = 20  -- max raw entry prints in pre-parse diagnostic

-- Inventory source of truth is Replion replicated player data.
-- Backpack/PlayerGui are diagnostic only and must not feed public inventory.
-- Set true ONLY to print Backpack/PlayerGui evidence; never feeds the payload.
local DEBUG_DIAGNOSTIC = false

-- Verbose Replion discovery diagnostics: candidate modules, every
-- GetReplion/WaitReplion attempt, OnReplionAdded events, candidate keys,
-- inventory-like paths and the final selection / failure reason.
local DEBUG_REPLION_DISCOVERY = true

-- How long (seconds) to keep listening for the player-data Replion to
-- replicate via OnReplionAdded before declaring it not-found.
local REPLION_WAIT_SECONDS = 20

-- After the player-data Replion is selected, print a safe SHAPE summary of
-- the real inventory paths (exists / typeof / key counts / small samples).
-- Read-only and output-capped. Used to discover the true item shape.
local DEBUG_REPLION_INVENTORY_DUMP = false

-- ----------------------------------------------------------------
-- Constants
-- ----------------------------------------------------------------
local TRACKER_URL = "https://tool.deng.my.id/api/tracker/update-backpack"
local CATALOG_URL = "https://tool.deng.my.id/api/tracker/update-catalog"
local LOG         = "[FishTracker]"
local TRACKER_BUILD = "BLOCKER10J_SAFE_LIGHT_SYNC_10S_2026_06_04"

local function fishLog(msg, ...)
    if select("#", ...) > 0 then
        print(LOG, string.format(tostring(msg), ...))
    else
        print(LOG, tostring(msg))
    end
end

-- Cancel stale loops when the loader is re-run.
_G.FishTrackerRunId = tostring(os.clock()) .. "-" .. tostring(math.random(100000, 999999))
local thisRunId = _G.FishTrackerRunId

local function isCurrentRun()
    return _G.FishTrackerRunId == thisRunId
end

-- ----------------------------------------------------------------
-- Roblox services
-- ----------------------------------------------------------------
local Players           = game:GetService("Players")
local HttpService       = game:GetService("HttpService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService        = game:GetService("RunService")
local LocalPlayer       = Players.LocalPlayer

-- ----------------------------------------------------------------
-- HTTP: safe executor-request finder
--
-- HttpService:RequestAsync is SERVER-ONLY and cannot make external
-- HTTP calls from a LocalScript — it is intentionally NOT used here.
-- This script relies on executor-provided HTTP globals only.
-- If none are available, dashboard sync is gracefully skipped.
-- ----------------------------------------------------------------
local HttpDash = {
    lastSendAt = {},
    backoffUntil = {},
    throttleSec = {
        tracker_status     = 15,
        inventory_snapshot = 30,
        catalog            = 60,
        default            = 15,
    },
    backoff429Sec = 60,
    cachedProvider = nil,
    cachedLabel = nil,
}

local function findExecutorRequest()
    local function tryFn(label, fn)
        if typeof(fn) == "function" then
            return fn, label
        end
        return nil
    end

    return tryFn("rawget(_G,'httpRequest')", rawget(_G, "httpRequest"))
        or tryFn("syn.request",              syn    and syn.request)
        or tryFn("http.request",             http   and http.request)
        or tryFn("http_request",             http_request)
        or tryFn("fluxus.request",           fluxus and fluxus.request)
        or tryFn("request",                  request)
end

local function getExecutorRequest()
    if HttpDash.cachedProvider ~= nil then return HttpDash.cachedProvider end
    local fn, label = findExecutorRequest()
    HttpDash.cachedProvider = fn or false
    HttpDash.cachedLabel = label
    if fn then
        fishLogDebug("HTTP provider found: %s", label)
    end
    return fn
end

-- Single safe call site — replaces every direct _G.httpRequest() call.
local function performDashboardRequest(opts)
    local fn = getExecutorRequest()

    if typeof(fn) == "function" then
        return fn(opts)
    end

    warn(LOG, "No executor HTTP function available (syn/http/request/fluxus all nil).",
        "Dashboard sync skipped. Fish tracking continues normally.")
    return { Success = false, StatusCode = 0, Body = "no-http-runtime" }
end

local function sendDashboardRequest(endpoint, opts)
    if not isCurrentRun() then
        return { Success = false, StatusCode = 0, Body = "stale-run" }
    end
    endpoint = endpoint or "default"
    local now = os.clock()
    if now < (HttpDash.backoffUntil[endpoint] or 0) then
        return { Success = false, StatusCode = 0, Body = "client-backoff" }
    end
    local minGap = HttpDash.throttleSec[endpoint] or HttpDash.throttleSec.default
    if endpoint == "inventory_snapshot" and LiveSafe.lightSyncEnabled then
        minGap = math.max(1, (LiveSafe.lightSyncIntervalSeconds or 10) - 2)
    end
    local lastAt = HttpDash.lastSendAt[endpoint] or -1e9
    if now - lastAt < minGap then
        fishLog("HTTP_THROTTLE skip endpoint=%s minGap=%ds", endpoint, minGap)
        return { Success = false, StatusCode = 0, Body = "throttled-local" }
    end
    HttpDash.lastSendAt[endpoint] = now
    local result = performDashboardRequest(opts)
    local code = (type(result) == "table" and tonumber(result.StatusCode)) or 0
    if code == 429 then
        HttpDash.backoffUntil[endpoint] = now + HttpDash.backoff429Sec
        fishLog("RATE_LIMIT_BACKOFF endpoint=%s seconds=%d", endpoint, HttpDash.backoff429Sec)
    end
    return result
end

print("[DENG TRACKER] httpRequest bootstrap reached")

-- ----------------------------------------------------------------
-- Runtime self-test — prints type of every HTTP candidate so you
-- can see exactly which globals are available in this environment.
-- ----------------------------------------------------------------
local function runtimeSelfTest()
    print("[DENG TRACKER] Runtime self-test:")
    print("  loadstring               :", typeof(loadstring))
    print("  game.HttpGet             :", typeof(game.HttpGet))
    print("  rawget(_G,'httpRequest') :", typeof(rawget(_G, "httpRequest")))
    print("  syn                      :", typeof(syn))
    print("  http                     :", typeof(http))
    print("  http.request             :", (typeof(http) == "table") and typeof(http.request) or "n/a (http not table)")
    print("  http_request             :", typeof(http_request))
    print("  fluxus                   :", typeof(fluxus))
    print("  fluxus.request           :", (typeof(fluxus) == "table") and typeof(fluxus.request) or "n/a (fluxus not table)")
    print("  request                  :", typeof(request))
    print("  performDashboardRequest  :", typeof(performDashboardRequest))
end

-- ================================================================
-- FISH CATALOG SYSTEM (built from ReplicatedStorage at startup)
-- ================================================================

-- Known stat/UI labels — never treat as fish/item names.
local STAT_LABEL_DENYLIST = {}
for _, v in ipairs({
    "caught","rarest fish","total","total fish","fish","weight",
    "search","inventory","owned","best catch","rarity","tier",
    "amount","count","value","oldest","newest","all","sort",
    "filter","equip","equipped","use","sell","buy","lock","unlock",
    "backpack","collection","bag","myfish","myitems","none",
    "common","uncommon","rare","epic","legendary","legend",
    "secret","forgotten","shiny","weight (kg)","max weight",
    "total weight","close","back","next","prev","previous",
    "page","tab","menu","stats","info","profile","shop","store",
    "trade","donate","rank","level","exp","coins","cash","gold",
    "gems","ok","yes","no","cancel","confirm","submit","reset",
    "settings","options","help","credits","about","exit","quit",
    "leave","loading","please wait","equipped rod","current rod",
    "rod","best","item","items","bait","baits",
}) do STAT_LABEL_DENYLIST[v] = true end

-- Fish/rod/item catalogs: normalizedKey -> { name, key, tier, imageUrl, category, source }
local fishCatalog  = {}
local rodCatalog   = {}
local itemCatalog  = {}
local rejectedLabels = {}   -- list of { rawName, count, reason, source }

-- Cross-index for metadata resolution (BLOCKER 2). Replion inventory entries
-- are frequently keyed/identified by an item id ("yellow_damselfish") rather
-- than a display name, so we index every catalog entry by BOTH its
-- normalized display name and any id-like key we can derive.
local metadataByName = {}   -- normalizedName     -> entry
local metadataById   = {}   -- normalizedId/slug  -> entry

local function trim(value)
    return tostring(value or ""):match("^%s*(.-)%s*$")
end

local function normalizeName(raw)
    return trim(raw):lower():gsub("%s+", " ")
end

local function isStatLabel(normalized)
    if STAT_LABEL_DENYLIST[normalized] then return true end
    -- Pure-numeric strings are potential item IDs (e.g. "70"), not stat labels.
    if normalized:match("^%d+$") then return false end
    if #normalized <= 2 then return true end
    return false
end

-- Normalize an ID/slug for cross-indexing: lower-case, strip non-alphanumerics
-- so "Yellow Damselfish", "yellow_damselfish", "fish_yellow_damselfish" and
-- "YellowDamselfish" all collapse toward a comparable key.
local function normalizeId(raw)
    local s = tostring(raw or ""):lower()
    s = s:gsub("[^%a%d]", "")
    return s
end

-- Common id prefixes seen in Fish It item ids; stripped to improve matching.
local ID_PREFIXES = { "fish", "rod", "item", "bait", "rods", "items", "fishes" }
local function idVariants(raw)
    local base = normalizeId(tostring(raw))
    local out = { base }
    -- Strip known prefix to get the base stem (e.g. "fish70" -> "70").
    for _, p in ipairs(ID_PREFIXES) do
        if base:sub(1, #p) == p and #base > #p then
            out[#out + 1] = base:sub(#p + 1)
        end
    end
    -- BLOCKER 3: Replion instance records use pure-numeric Id (e.g. Id=70).
    -- Generate prefix+number variants so catalog entries indexed as "fish70"
    -- are also reachable when we look up the raw number "70".
    if base:match("^%d+$") then
        out[#out + 1] = "id" .. base
        for _, p in ipairs(ID_PREFIXES) do
            out[#out + 1] = p .. base
        end
    end
    return out
end

-- Resolve catalog metadata by display name first, then by any id-like key.
local function resolveFishMeta(normalizedKey)
    local byName = metadataByName[normalizedKey]
        or fishCatalog[normalizedKey]
        or rodCatalog[normalizedKey]
        or itemCatalog[normalizedKey]
    if byName then return byName end
    -- Fall back to id index (handles "yellow_damselfish", "fish_yellow_...").
    for _, v in ipairs(idVariants(normalizedKey)) do
        local hit = metadataById[v]
        if hit then return hit end
    end
    return nil
end

-- Explicit id-only lookup used by the inventory parser.
local function resolveMetaById(rawId)
    if rawId == nil then return nil end
    for _, v in ipairs(idVariants(tostring(rawId))) do
        local hit = metadataById[v]
        if hit then return hit end
    end
    return nil
end

-- BLOCKER9: nil-safe numeric helpers (prevents "attempt to perform arithmetic on nil").
local function toNumberOr(value, fallback)
    local n = tonumber(value)
    if n == nil then return fallback end
    return n
end

local function safeAdd(a, b)
    return toNumberOr(a, 0) + toNumberOr(b, 0)
end

local function isPlaceholderName(name, id)
    if type(name) ~= "string" then return true end
    if name == "" then return true end
    if id ~= nil and tostring(name) == ("Item #" .. tostring(id)) then return true end
    if name:match("^Item #%d+$") then return true end
    return false
end

local function shouldReplaceName(existingName, newName, id)
    if isPlaceholderName(newName, id) then return false end
    if isPlaceholderName(existingName, id) then return true end
    return false
end

local function catalogEntryStrength(entry)
    if not entry or type(entry.name) ~= "string" or entry.name == "" then return 0 end
    if isPlaceholderName(entry.name, entry.itemId) then return 1 end
    if entry.category == "fish" then return 4 end
    if entry.category == "rod" or entry.category == "bait" then return 3 end
    return 2
end

local function safeWriteMetadataById(idStr, newEntry)
    idStr = tostring(idStr or "")
    local existing = metadataById[idStr]
    if not existing then
        metadataById[idStr] = newEntry
        return true
    end
    if shouldReplaceName(existing.name, newEntry.name, idStr) then
        metadataById[idStr] = newEntry
        return true
    end
    if catalogEntryStrength(newEntry) > catalogEntryStrength(existing) then
        metadataById[idStr] = newEntry
        return true
    end
    if isPlaceholderName(newEntry.name, idStr) or catalogEntryStrength(newEntry) < catalogEntryStrength(existing) then
        fishLog("CATALOG_DOWNGRADE_BLOCKED id=%s existing=%s attempted=%s source=%s",
            idStr, tostring(existing.name), tostring(newEntry.name), tostring(newEntry.source or "?"))
    end
    return false
end

local function safeUpgradeOwnedEntry(entry, meta)
    if not entry or not meta or not meta.name then return false end
    local idStr = entry.itemId and tostring(entry.itemId) or nil
    if not shouldReplaceName(entry.name, meta.name, idStr) then
        if isPlaceholderName(meta.name, idStr) and not isPlaceholderName(entry.name, idStr) then
            fishLog("CATALOG_DOWNGRADE_BLOCKED id=%s existing=%s attempted=%s source=%s",
                tostring(idStr or "?"), tostring(entry.name), tostring(meta.name), tostring(meta.source or "?"))
        end
        return false
    end
    local oldName = entry.name
    entry.name = meta.name
    entry.resolved = true
    entry.catalogReason = "catalog_hit"
    entry.catalogSource = meta.source
    if meta.category == "fish" then
        entry.category = "fish"
    elseif meta.category and (not entry.category or entry.category == "items") then
        entry.category = meta.category
    end
    if meta.tier and not entry.tier then entry.tier = meta.tier end
    fishLog("CATALOG_PLACEHOLDER_UPGRADED id=%s old=%s new=%s source=%s",
        tostring(idStr or "?"), tostring(oldName), meta.name, tostring(meta.source or "?"):sub(1, 60))
    return true
end

-- BLOCKER10J: light 10s sync — server resolves all names; no client catalog.
local LiveSafe = {
    safeMinimalMode = true,
    oneShot = false,
    repeatUpload = true,
    lightSyncEnabled = true,
    lightSyncIntervalSeconds = 10,
    lightSyncBackoffSeconds = 30,
    lightSyncFailThreshold = 3,
    lightSyncLoopBudgetMs = 500,
    lightSyncLoopStarted = false,
    currentSyncReason = "",
    playerDataOnly = true,
    clientCatalogResolution = false,
    quickFishCatalog = false,
    verbose = false,
    debug = false,
    enableFreezeMonitor = false,
    enableHeavyCatalog = false,
    enablePhaseBItemUpgrade = false,
    enableTargetedItemDiagnostics = false,
    debugRemoteHooks = false,
    enableModuleRequire = false,
    cachedInventoryPath = "Inventory.Items",
    steps = {},
    targetItemIds = {
        "10", "990", "388", "196", "74", "70", "112", "234", "115", "67", "65", "232", "237",
    },
    shallowRsRoots = { "Shared", "Modules", "Packages", "Resources", "Assets", "Configs" },
    nonBlocking = true,
    budgetMs = 2,
    scanOpsPerYield = 4,
    stallThresholdSec = 0.25,
    catalogBackgroundComplete = false,
    catalogAborted = false,
    catalogPausedUntil = 0,
    scanBudgetFrameStart = os.clock(),
    scanOpsSinceYield = 0,
    freeze = {
        maxDt = 0, stalls = 0, worstSection = "none", lastSection = "boot",
        lastPath = "", lastId = "", lastHeartbeatAt = os.clock(),
    },
    phaseB = { pass = 0, upgradedTotal = 0, finalizeInProgress = false },
    moduleRequire = { attempted = 0, succeeded = 0, skipped = 0, failed = 0 },
    unresolvedDiagnostics = {},
}
local lastReplionDataCache = nil
local tryFinalizeCatalogAndUpgrade

local function fishLogDebug(msg, ...)
    if not LiveSafe.debug and not LiveSafe.verbose then return end
    fishLog(msg, ...)
end

local function stepBegin(name)
    LiveSafe.steps[name] = os.clock()
    fishLog("STEP_BEGIN %s", name)
end

local function stepEnd(name)
    local t0 = LiveSafe.steps[name] or os.clock()
    local ms = math.floor((os.clock() - t0) * 1000)
    fishLog("STEP_END %s ms=%d", name, ms)
    if ms >= 100 then fishLog("SLOW_STEP section=%s ms=%d", name, ms) end
    return ms
end

local function setActiveSection(section, path, id)
    if section then LiveSafe.freeze.lastSection = section end
    if path then LiveSafe.freeze.lastPath = tostring(path):sub(1, 120) end
    if id then LiveSafe.freeze.lastId = tostring(id) end
end

local function startFreezeMonitor()
    if not LiveSafe.enableFreezeMonitor then return end
    pcall(function()
        RunService.Heartbeat:Connect(function()
            local now = os.clock()
            local dt = now - LiveSafe.freeze.lastHeartbeatAt
            LiveSafe.freeze.lastHeartbeatAt = now
            if dt > LiveSafe.freeze.maxDt then LiveSafe.freeze.maxDt = dt end
            if dt >= LiveSafe.stallThresholdSec then
                LiveSafe.freeze.stalls = LiveSafe.freeze.stalls + 1
                LiveSafe.freeze.worstSection = LiveSafe.freeze.lastSection or "unknown"
                fishLog("FREEZE_SUSPECT section=%s elapsedMs=%.0f lastPath=%s lastId=%s",
                    LiveSafe.freeze.lastSection, dt * 1000,
                    LiveSafe.freeze.lastPath:sub(1, 60), LiveSafe.freeze.lastId)
                LiveSafe.budgetMs = math.max(0.5, LiveSafe.budgetMs * 0.5)
                LiveSafe.scanOpsPerYield = math.max(2, math.floor(LiveSafe.scanOpsPerYield * 0.5))
                LiveSafe.catalogPausedUntil = os.clock() + 10
                fishLog("CATALOG_THROTTLE reason=frame_stall newBudgetMs=%.1f", LiveSafe.budgetMs)
            elseif dt >= 0.1 then
                fishLogDebug("FRAME_STALL_WARN dt=%.3f section=%s", dt, LiveSafe.freeze.lastSection)
            end
        end)
    end)
end

local function printFreezeMonitorSummary()
    fishLog("Freeze monitor summary: maxDt=%.3f stalls=%d worstSection=%s",
        LiveSafe.freeze.maxDt, LiveSafe.freeze.stalls, LiveSafe.freeze.worstSection or "none")
end

local function abortHeavyCatalog(reason)
    if LiveSafe.catalogAborted then return end
    LiveSafe.catalogAborted = true
    fishLog("CATALOG_ABORTED reason=%s", tostring(reason))
end

local function resetScanBudget()
    LiveSafe.scanBudgetFrameStart = os.clock()
    LiveSafe.scanOpsSinceYield = 0
end

local function scanBudgetYield(section)
    if not LiveSafe.nonBlocking then return true end
    if section then setActiveSection(section) end
    if LiveSafe.catalogAborted then return false end
    if os.clock() < LiveSafe.catalogPausedUntil then
        task.wait()
        resetScanBudget()
        return true
    end
    LiveSafe.scanOpsSinceYield = LiveSafe.scanOpsSinceYield + 1
    local elapsedMs = (os.clock() - LiveSafe.scanBudgetFrameStart) * 1000
    if elapsedMs >= LiveSafe.budgetMs or LiveSafe.scanOpsSinceYield >= LiveSafe.scanOpsPerYield then
        task.wait()
        resetScanBudget()
    end
    return true
end

local function perfEndSection(t0, section, warnMs)
    local elapsedMs = (os.clock() - t0) * 1000
    if elapsedMs >= (warnMs or 50) then
        fishLog("PERF_WARN section=%s elapsedMs=%.1f", section, elapsedMs)
    else
        fishLog("PERF section=%s elapsedMs=%.1f", section, elapsedMs)
    end
    return elapsedMs
end

local function countPlaceholderItems()
    local n = 0
    for _, key in ipairs(ownedOrder) do
        local e = ownedInventory[key]
        if e and isPlaceholderName(e.name, e.itemId) then n = n + 1 end
    end
    return n
end

local function printMetadataCatalogSummary(unresolvedCount)
    local fishN, rodsN, baitsN, cratesN, materialsN, itemsN = 0, 0, 0, 0, 0, 0
    for _, e in pairs(metadataById) do
        if type(e) == "table" then
            local c = (e.category or ""):lower()
            if c == "fish" then fishN = fishN + 1
            elseif c == "rod" then rodsN = rodsN + 1
            elseif c == "bait" then baitsN = baitsN + 1
            elseif c:find("crate") then cratesN = cratesN + 1
            elseif c:find("material") then materialsN = materialsN + 1
            else itemsN = itemsN + 1 end
        end
    end
    fishLog("Metadata catalog summary: fish=%d rods=%d baits=%d crates=%d materials=%d items=%d unresolved=%d",
        fishN, rodsN, baitsN, cratesN, materialsN, itemsN, unresolvedCount or countPlaceholderItems())
end

local CATALOG_LOG_LIMIT = 30
local catalogLookupLogCount = 0
local catalogSourceLogCount = 0
local unresolvedItemLogCount = 0
local metadataDecodeFailedIds = {}
local catalogSearchRoots = {}   -- { t=table, path=string } for targeted id search
local itemCatalogSourcesScanned = 0

local DEF_ID_FIELDS = {
    "Id","ID","id","ItemId","ItemID","itemId","NumericId","numericId","DataId","dataId",
    "FishId","FishID","RodId","RodID",
}
local META_NAME_FIELDS = {
    "Name","DisplayName","ItemName","Title","Label",
    "name","displayName","itemName","title","label",
    "FishName","fishName",
}
local META_TYPE_FIELDS = {
    "Type","Category","ItemType","Class","Kind","Rarity",
    "type","category","itemType","class","kind",
}
-- Preserved for BLOCKER11 image work — capture raw icon fields, do not render yet.
local DEF_ICON_FIELDS = {
    "Image","ImageId","Icon","IconId","Thumbnail","Texture","AssetId",
    "image","imageId","icon","iconId","thumbnail","texture","assetId",
}

-- Safely decode Metadata table or JSON-like string; never throws.
local function decodeMetadata(raw, idStr)
    if raw == nil then return nil end
    if type(raw) == "table" then return raw end
    if type(raw) == "string" then
        local trimmed = trim(raw)
        if #trimmed > 1 and trimmed:sub(1, 1) == "{" then
            if type(HttpService) == "Instance" and type(HttpService.JSONDecode) == "function" then
                local ok, decoded = pcall(HttpService.JSONDecode, HttpService, trimmed)
                if ok and type(decoded) == "table" then return decoded end
            end
            if idStr and not metadataDecodeFailedIds[idStr] then
                metadataDecodeFailedIds[idStr] = true
                fishLog("METADATA_DECODE_FAILED id=%s reason=json_decode", tostring(idStr))
            end
        end
    end
    return nil
end

local function readMetaField(record, fields)
    if type(record) ~= "table" then return nil end
    for _, f in ipairs(fields) do
        local v = record[f]
        if v ~= nil then return v end
    end
    return nil
end

local function resolveCategoryFromMeta(meta, metaBlock, sourcePath)
    if meta and meta.category then return meta.category end
    if type(metaBlock) == "table" then
        local tv = readMetaField(metaBlock, META_TYPE_FIELDS)
        if type(tv) == "string" then
            local t = tv:lower()
            if t:find("rod") then return "rod" end
            if t:find("bait") then return "bait" end
            if t:find("fish") then return "fish" end
            if t:find("item") or t:find("crate") or t:find("box") then return "items" end
        end
    end
    local p = (sourcePath or ""):lower()
    if p:find("rod") then return "rod" end
    if p:find("fish") then return "fish" end
    return "items"
end

local function resolveItemDisplayName(idStr, entry, meta, metaBlock)
    if type(entry) == "table" then
        local direct = readMetaField(entry, META_NAME_FIELDS)
        if type(direct) == "string" and #trim(direct) > 1 and not direct:match("^%d+$") then
            return trim(direct)
        end
    end
    if meta and meta.name then return meta.name end
    if type(metaBlock) == "table" then
        local mname = readMetaField(metaBlock, META_NAME_FIELDS)
        if type(mname) == "string" and #trim(mname) > 1 and not mname:match("^%d+$") then
            return trim(mname)
        end
    end
    return "Item #" .. tostring(idStr)
end

-- Tries metadataById index built from RS + Replion catalog scans.
local function resolveCatalogMetaById(idStr)
    idStr = tostring(idStr or "")
    local meta = resolveMetaById(idStr)
    if meta then
        if catalogLookupLogCount < CATALOG_LOG_LIMIT then
            catalogLookupLogCount = catalogLookupLogCount + 1
            fishLog("ITEM_CATALOG_HIT id=%s name=%s category=%s source=%s",
                idStr, meta.name or "?", meta.category or "?", meta.source or "?")
        end
        return meta, meta.source or "metadataById"
    end
    if catalogLookupLogCount < CATALOG_LOG_LIMIT then
        catalogLookupLogCount = catalogLookupLogCount + 1
        fishLog("ITEM_CATALOG_MISS id=%s fallback=Item #%s searchedSources=%d",
            idStr, idStr, itemCatalogSourcesScanned)
    end
    return nil, nil
end

-- PART 1 (BLOCKER 3): Catalog diagnostic — print whether specific numeric ids
-- resolve in metadataById and what keys were tried. Called after first parse.
local function debugCatalogLookupForOwnedIds(ids)
    local byIdCount, numericCount, stringCount = 0, 0, 0
    for k in pairs(metadataById) do
        byIdCount = byIdCount + 1
        if k:match("^%d+$") then numericCount = numericCount + 1
        else stringCount = stringCount + 1 end
    end
    print(LOG, ("Catalog ID index summary: metadataById keys=%d numericId keys=%d stringId keys=%d"):format(
        byIdCount, numericCount, stringCount))
    local lookupPrinted = 0
    for _, rawId in ipairs(ids) do
        lookupPrinted = lookupPrinted + 1
        if lookupPrinted > DEBUG_LOOKUP_LIMIT then
            print(LOG, ("Catalog lookup logging capped at %d of %d ids"):format(
                DEBUG_LOOKUP_LIMIT, #ids))
            break
        end
        local idStr = tostring(rawId)
        local meta = resolveMetaById(idStr)
        if meta then
            print(LOG, ("  Catalog lookup id=%s -> found name=%s category=%s tier=%s image=%s"):format(
                idStr, meta.name or "?", meta.category or "?", meta.tier or "?",
                meta.imageUrl and "yes" or "no"))
        else
            local tried = {}
            for _, v in ipairs(idVariants(idStr)) do tried[#tried+1] = '"'..v..'"' end
            print(LOG, ("  Catalog lookup id=%s -> not found tried:[%s]"):format(
                idStr, table.concat(tried, ",")))
        end
    end
end

local function rejectInventoryLabel(rawName, count, reason, source)
    rejectedLabels[#rejectedLabels + 1] = {
        rawName = rawName, count = count,
        reason = reason, source = source or "unknown",
    }
    if DEBUG_VERBOSE_INVENTORY then
        print(LOG, ("  REJECT '%s' (x%d) — %s [%s]"):format(
            rawName, count, reason, source or "?"))
    end
end

local TIER_MAP = {
    common="common", uncommon="uncommon", rare="rare",
    epic="epic", legendary="legend", legend="legend",
    secret="secret", forgotten="forgotten",
    mythic="epic", mythical="epic", special="rare", ultra="epic",
    legendaries="legend", uncommons="uncommon", rares="rare",
}

-- ----------------------------------------------------------------
-- Image source resolution: convert any Roblox asset reference to a
-- browser-renderable HTTPS thumbnail URL. Never invents an image.
-- ----------------------------------------------------------------
local function resolveImageUrl(raw)
    if raw == nil then return nil end
    local s = trim(tostring(raw))
    if s == "" then return nil end
    -- rbxassetid://12345  → thumbnail URL
    local id = s:match("rbxassetid://(%d+)")
             or s:match("rbxthumb://[^%d]*(%d+)")
             or s:match("rbxgameasset://.*/(%d+)")
    if id then
        return ("https://www.roblox.com/asset-thumbnail/image?assetId=%s&width=150&height=150&format=png"):format(id)
    end
    -- Bare numeric asset id
    if s:match("^%d+$") then
        return ("https://www.roblox.com/asset-thumbnail/image?assetId=%s&width=150&height=150&format=png"):format(s)
    end
    -- Already an http(s) image URL → keep
    if s:match("^https?://") then return s end
    -- Placeholder / unknown → no image
    if s:lower():find("placeholder") then return nil end
    return nil
end

-- Field-name lists used during metadata extraction.
local NAME_FIELDS  = {"Name","DisplayName","ItemName","FishName","Title","Label","Id","Identifier"}
local TIER_FIELDS  = {"Rarity","Tier","Quality","Grade","Rank","Type"}
local IMAGE_FIELDS = {"Image","ImageId","ImageID","Icon","IconId","IconID",
                      "Thumbnail","ThumbnailId","Texture","TextureId",
                      "AssetId","Decal","Sprite","Picture"}

-- Extract { name, tier, imageUrl } from a single Instance (attributes,
-- child Value objects, and child ImageLabel/Decal/Texture).
local function extractInstanceMeta(inst)
    local tier, imageUrl, displayName

    -- Attributes
    pcall(function()
        local attrs = inst:GetAttributes()
        if type(attrs) == "table" then
            for _, f in ipairs(TIER_FIELDS) do
                if attrs[f] ~= nil and tier == nil then
                    tier = TIER_MAP[tostring(attrs[f]):lower()] or nil
                end
            end
            for _, f in ipairs(IMAGE_FIELDS) do
                if attrs[f] ~= nil and imageUrl == nil then
                    imageUrl = resolveImageUrl(attrs[f])
                end
            end
            for _, f in ipairs(NAME_FIELDS) do
                if type(attrs[f]) == "string" and displayName == nil and #trim(attrs[f]) > 1 then
                    if f ~= "Name" then displayName = trim(attrs[f]) end
                end
            end
        end
    end)

    -- Child Value objects (StringValue / IntValue / NumberValue)
    pcall(function()
        for _, ch in ipairs(inst:GetChildren()) do
            local cn = ch.Name
            if (ch:IsA("StringValue") or ch:IsA("IntValue") or ch:IsA("NumberValue")) then
                if tier == nil then
                    for _, f in ipairs(TIER_FIELDS) do
                        if cn == f then tier = TIER_MAP[tostring(ch.Value):lower()] or tier end
                    end
                end
                if imageUrl == nil then
                    for _, f in ipairs(IMAGE_FIELDS) do
                        if cn == f then imageUrl = resolveImageUrl(ch.Value) end
                    end
                end
                if displayName == nil then
                    for _, f in ipairs(NAME_FIELDS) do
                        if cn == f and f ~= "Name" and type(ch.Value) == "string"
                           and #trim(ch.Value) > 1 then displayName = trim(ch.Value) end
                    end
                end
            end
        end
    end)

    -- Child image-bearing GUI / Decal / Texture
    if imageUrl == nil then
        pcall(function()
            local img = inst:FindFirstChildWhichIsA("ImageLabel")
                     or inst:FindFirstChildWhichIsA("ImageButton")
            if img then imageUrl = resolveImageUrl(img.Image) end
        end)
    end
    if imageUrl == nil then
        pcall(function()
            local dec = inst:FindFirstChildWhichIsA("Decal")
                     or inst:FindFirstChildWhichIsA("Texture")
            if dec then imageUrl = resolveImageUrl(dec.Texture) end
        end)
    end

    return displayName, tier, imageUrl
end

-- Decide whether an Instance/name looks like a catalog candidate (fish/rod/item)
-- and which category it belongs to. Returns category string or nil.
local function classifyCatalogCandidate(name, sourcePath)
    local n = name:lower()
    local path = (sourcePath or ""):lower()
    if isStatLabel(normalizeName(name)) then return nil end
    if n:find("rod") or path:find("rod") then return "rod" end
    if n:find("bait") or path:find("bait") then return "bait" end
    if path:find("crate") or path:find("material") or path:find("consumable")
        or path:find("resource") then return "items" end
    if path:find("fish") or path:find("school") then return "fish" end
    if path:find("item") then return "items" end
    if n:find("rod") or n:find("crate") or n:find("bait") then
        if n:find("rod") then return "rod" end
        if n:find("bait") then return "bait" end
        return "items"
    end
    -- default: treat as fish (Fish It's primary content)
    return "fish"
end

local catalogStats = { fish=0, rods=0, items=0, bait=0, images=0, tiers=0 }

-- Index an entry by every id-like variant we can derive (id field + slug of
-- the display name). Lets the parser resolve id-keyed Replion records.
local function indexEntryIds(entry, extraId)
    metadataByName[entry.key] = entry
    local ids = {}
    -- Slug of the display name (e.g. "Yellow Damselfish" -> "yellowdamselfish").
    ids[#ids + 1] = normalizeId(entry.name)
    -- extraId may be a number (Replion numeric Id) or string (slug/key).
    if extraId ~= nil then
        local extraStr = tostring(extraId)
        if #extraStr > 0 then
            for _, v in ipairs(idVariants(extraStr)) do ids[#ids + 1] = v end
        end
    end
    for _, id in ipairs(ids) do
        -- Allow pure-numeric ids of any length (e.g. "70" = 2 chars is valid).
        -- Require >= 3 chars only for alpha-containing strings.
        local isNumeric = id:match("^%d+$") ~= nil
        if ((isNumeric and #id >= 1) or (#id >= 3)) then
            if not metadataById[id] then
                metadataById[id] = entry
            end
        end
    end
end

local function addCatalogEntry(name, tier, imageUrl, category, sourcePath, idHint)
    name = trim(name)
    if #name < 2 then return false end
    local key = normalizeName(name)
    if isStatLabel(key) then return false end

    local catalog
    if category == "rod" or category == "bait" then catalog = rodCatalog
    elseif category == "items" then catalog = itemCatalog
    else catalog = fishCatalog end

    if catalog[key] then
        -- Enrich existing entry if new data is better
        local e = catalog[key]
        if (not e.tier or e.tier == "unknown") and tier then e.tier = tier; catalogStats.tiers = catalogStats.tiers + 1 end
        if not e.imageUrl and imageUrl then e.imageUrl = imageUrl; catalogStats.images = catalogStats.images + 1 end
        indexEntryIds(e, idHint)
        return false
    end

    catalog[key] = {
        name = name, key = key, tier = tier or "unknown",
        imageUrl = imageUrl, category = category, source = sourcePath or "ReplicatedStorage",
    }
    indexEntryIds(catalog[key], idHint)
    if category == "rod" then catalogStats.rods = catalogStats.rods + 1
    elseif category == "bait" then catalogStats.bait = catalogStats.bait + 1
    elseif category == "items" then catalogStats.items = catalogStats.items + 1
    else catalogStats.fish = catalogStats.fish + 1 end
    if imageUrl then catalogStats.images = catalogStats.images + 1 end
    if tier and tier ~= "unknown" then catalogStats.tiers = catalogStats.tiers + 1 end

    if DEBUG_VERBOSE_INVENTORY then
        print(LOG, ("  Catalog %s: %s tier=%s image=%s source=%s"):format(
            category, name, tier or "unknown", imageUrl and "yes" or "no", sourcePath or "?"))
    end
    return true
end

-- Recursively walk a plain Lua table (from a required ModuleScript) looking
-- for fish/item definition records. Read-only; never calls table functions.
local function walkCatalogTable(root, sourceName)
    if type(root) ~= "table" then return end
    -- Inline register — registerCatalogSearchRoot is defined later in this file.
    catalogSearchRoots[#catalogSearchRoots + 1] = { t = root, path = sourceName }
    itemCatalogSourcesScanned = itemCatalogSourcesScanned + 1
    local visited, nodeCount = {}, {n=0}

    local function readField(t, fields)
        for _, f in ipairs(fields) do
            local v = t[f]
            if v ~= nil then return v end
        end
        return nil
    end

    local function walk(t, depth)
        if depth > 6 or type(t) ~= "table" then return end
        local addr = tostring(t)
        if visited[addr] then return end
        visited[addr] = true
        nodeCount.n = nodeCount.n + 1
        if nodeCount.n > 4000 then return end
        scanBudgetYield("walkCatalogTable")

        -- Treat this node as a possible record
        local nameVal = readField(t, {"Name","name","DisplayName","displayName","ItemName","itemName","FishName","fishName","Title","title"})
        if type(nameVal) == "string" and #trim(nameVal) > 1 then
            local tierVal = readField(t, {"Rarity","rarity","Tier","tier","Quality","quality","Grade","grade","Rank","rank","Class","class"})
            local tier = tierVal and (TIER_MAP[tostring(tierVal):lower()] or nil) or nil
            local imgVal = readField(t, {"Image","image","ImageId","imageId","Icon","icon","Thumbnail","thumbnail","Texture","texture","AssetId","assetId"})
            local imageUrl = resolveImageUrl(imgVal)
            local idVal = readField(t, {"Id","id","ItemId","itemId","Identifier","identifier","FishId","fishId","RodId","rodId","Key","key"})
            local cat = classifyCatalogCandidate(nameVal, sourceName)
            -- Pass numeric Id (e.g. 70) as tostring so it gets indexed in metadataById.
            if cat then addCatalogEntry(nameVal, tier, imageUrl, cat, sourceName, idVal ~= nil and tostring(idVal) or nil) end
        end

        -- Key-as-name pattern: { ["Yellow Damselfish"] = { rarity=..., image=... } }
        for k, v in pairs(t) do
            if type(k) == "string" and #trim(k) > 1 and type(v) == "table" then
                local tierVal = readField(v, {"Rarity","rarity","Tier","tier","Quality","quality","Grade","grade","Rank","rank","Class","class"})
                local imgVal  = readField(v, {"Image","image","ImageId","imageId","Icon","icon","Thumbnail","thumbnail","Texture","texture"})
                -- The key may be an ID with the display name inside the record
                -- (Fish It pattern: ["yellow_damselfish"] = { Name="Yellow Damselfish", ... }).
                local innerName = readField(v, {"Name","name","DisplayName","displayName","ItemName","itemName","FishName","fishName"})
                if tierVal or imgVal or (type(innerName) == "string" and #trim(innerName) > 1) then
                    local tier = tierVal and (TIER_MAP[tostring(tierVal):lower()] or nil) or nil
                    local displayName = (type(innerName) == "string" and #trim(innerName) > 1) and innerName or k
                    local cat = classifyCatalogCandidate(displayName, sourceName)
                    -- Key is the id hint; if the key itself is the display name
                    -- the slug index still covers it.
                    if cat then addCatalogEntry(displayName, tier, resolveImageUrl(imgVal), cat, sourceName, k) end
                end
            end
            -- BLOCKER 3: Numeric-key-as-id pattern: {[70] = {Name="...", Image="..."}}
            -- The numeric key IS the item ID; pass it as idHint so metadataById["70"] is set.
            -- This covers cases where modules use e.g. { [70] = { Name="Yello Damselfish", ... } }
            -- instead of { YelloDamselfish = { Name="...", Id=70, ... } }.
            if type(k) == "number" and type(v) == "table" then
                local innerName = readField(v, {"Name","name","DisplayName","displayName","ItemName","itemName","FishName","fishName","Title","title"})
                if type(innerName) == "string" and #trim(innerName) > 1 then
                    local tierVal2 = readField(v, {"Rarity","rarity","Tier","tier","Quality","quality","Grade","grade","Rank","rank","Class","class"})
                    local imgVal2  = readField(v, {"Image","image","ImageId","imageId","Icon","icon","Thumbnail","thumbnail","Texture","texture","AssetId","assetId"})
                    local tier2 = tierVal2 and (TIER_MAP[tostring(tierVal2):lower()] or nil) or nil
                    local cat2  = classifyCatalogCandidate(innerName, sourceName)
                    if cat2 then addCatalogEntry(innerName, tier2, resolveImageUrl(imgVal2), cat2, sourceName, tostring(k)) end
                end
            end
            if type(v) == "table" then walk(v, depth + 1) end
        end
    end
    walk(root, 0)
end

-- ----------------------------------------------------------------
-- METADATA CATALOG: recursive ReplicatedStorage scanner.
-- This is METADATA ONLY (names / tiers / images for resolution).
-- It is NOT owned inventory — owned inventory comes from Replion.
-- Read-only. Depth/node-capped. pcall-guarded require() of modules.
-- ----------------------------------------------------------------
local function scanReplicatedStorageFishCatalog(opts)
    opts = opts or {}
    local maxInst = opts.maxInst or (LiveSafe.safeMinimalMode and 200 or 4000)
    local folderList = opts.folderList
    local allowRequire = opts.requireModules == true and LiveSafe.enableModuleRequire and not LiveSafe.catalogAborted
    setActiveSection("rs_scan")
    print(LOG, "Metadata catalog build starting...")
    local TOP_FOLDERS = folderList or {
        "Items","Item","FishSchoolAssets","Fish","Fishes","FishData","ItemData",
        "ItemDatas","ItemDefinitions","ItemInfo","InventoryItem",
        "FishCatalog","ItemCatalog","Catalog","Assets","Shared","Modules",
        "Controllers","Rods","RodData","Rod","Packages",
        "Baits","Bait","Crates","Crate","Materials","Material","Resources","Consumables",
        "GameData","Config","GameConfig","Fishing","FishTypes","FishTemplate",
        "Templates","Data","Registry","Game","Scripts",
        "Equipment","Tools","Skins","Boats","Packs","Shop","Rewards",
    }
    local visited = {}
    local instCount = {n=0}

    -- Recursively descend Instances (folders/models), extracting metadata.
    local function descend(inst, path, depth)
        if depth > 6 then return end
        instCount.n = instCount.n + 1
        if instCount.n > maxInst then return end
        scanBudgetYield("rs_descend")
        local ok, children = pcall(function() return inst:GetChildren() end)
        if not ok or type(children) ~= "table" then return end

        for _, child in ipairs(children) do
            local childPath = path .. "." .. child.Name
            scanBudgetYield("rs_child")

            if child:IsA("ModuleScript") then
                if allowRequire then
                    local rln = child.Name:lower()
                    local skipName = rln:find("controller") or rln:find("client") or rln:find("ui")
                        or rln:find("effect") or rln:find("anim") or rln:find("network")
                        or rln:find("remote") or rln:find("service")
                    if not skipName and (rln:find("fish") or rln:find("item") or rln:find("catalog")
                       or rln:find("data") or rln:find("rod") or rln:find("bait")
                       or rln:find("crate") or rln:find("material") or rln:find("def")) then
                        if not scanBudgetYield("rs_require") then return end
                        LiveSafe.moduleRequire.attempted = LiveSafe.moduleRequire.attempted + 1
                        task.wait()
                        local okR, result = pcall(require, child)
                        task.wait()
                        if okR and type(result) == "table" then
                            LiveSafe.moduleRequire.succeeded = LiveSafe.moduleRequire.succeeded + 1
                            walkCatalogTable(result, childPath)
                        else
                            LiveSafe.moduleRequire.failed = LiveSafe.moduleRequire.failed + 1
                        end
                    end
                end
            elseif child:IsA("Folder") or child:IsA("Model") or child:IsA("Configuration") then
                -- Folder/model: it may itself be a fish definition, then descend.
                local name, tier, imageUrl = extractInstanceMeta(child)
                local cat = classifyCatalogCandidate(child.Name, childPath)
                -- BLOCKER 3: pass numeric folder name as id hint (e.g. folder named "70" → idHint="70").
                local fldIdHint = child.Name:match("^%d+$") and child.Name or nil
                -- Allow entry even without tier/image when a real display name was extracted.
                if cat and (tier or imageUrl or (name and name ~= child.Name)) then
                    addCatalogEntry(name or child.Name, tier, imageUrl, cat, childPath, fldIdHint)
                end
                -- Instance attributes as definition hints (Id/Name/DisplayName/Type).
                pcall(function()
                    local attrId = child:GetAttribute("Id") or child:GetAttribute("ItemId")
                    local attrName = child:GetAttribute("Name") or child:GetAttribute("DisplayName")
                    if attrId and attrName and type(attrName) == "string" and #trim(attrName) > 1 then
                        local acat = classifyCatalogCandidate(attrName, childPath) or "items"
                        addCatalogEntry(attrName, nil, nil, acat, childPath .. "@attr", tostring(attrId))
                    end
                end)
                descend(child, childPath, depth + 1)
            else
                -- Leaf definition instance (Tool, Part, anything with meta)
                local name, tier, imageUrl = extractInstanceMeta(child)
                if tier or imageUrl then
                    local cat = classifyCatalogCandidate(child.Name, childPath)
                    if cat then addCatalogEntry(name or child.Name, tier, imageUrl, cat, childPath) end
                end
            end
        end
    end

    for _, fname in ipairs(TOP_FOLDERS) do
        local folder = ReplicatedStorage:FindFirstChild(fname)
        if folder and not visited[fname] then
            visited[fname] = true
            pcall(descend, folder, "ReplicatedStorage." .. fname, 0)
        end
    end

    if not folderList then
        -- BLOCKER10: nested Shared/Modules definition paths (items/rods/baits/crates).
        local RS_NESTED_PATHS = {
            "Shared.Items", "Shared.ItemData", "Shared.ItemDatas", "Shared.ItemDefinitions",
            "Shared.ItemConfig", "Shared.Constants", "Shared.Rods", "Shared.Baits",
            "Shared.Crates", "Shared.Materials", "Shared.Equipment", "Shared.Shop",
            "Modules.Items", "Modules.ItemData", "Modules.ItemDefinitions",
            "Modules.Rods", "Modules.Baits", "Modules.Crates", "Modules.Equipment",
        }
        for _, dotted in ipairs(RS_NESTED_PATHS) do
            local node = ReplicatedStorage
            for segment in dotted:gmatch("[^%.]+") do
                if not node then break end
                node = node:FindFirstChild(segment)
            end
            if node and not visited[dotted] then
                visited[dotted] = true
                pcall(descend, node, "ReplicatedStorage." .. dotted, 0)
            end
        end
    end

    printMetadataCatalogSummary(countPlaceholderItems())
    return catalogStats.fish + catalogStats.rods + catalogStats.items + catalogStats.bait
end

-- BLOCKER 3: Post-scan pass — look for RS folders whose direct children are
-- named with numeric ids (e.g. ReplicatedStorage.Fish["70"]).  This handles
-- games that store each item as a named Instance rather than a module table.
local function buildNumericIdIndexFromRSFolders()
    local NUMERIC_SCAN_ROOTS = {
        "Fish","FishData","Items","Item","ItemData","Rods","RodData",
        "Baits","Bait","Crates","Crate","Materials",
    }
    local added = 0
    for _, folderName in ipairs(NUMERIC_SCAN_ROOTS) do
        local root = ReplicatedStorage:FindFirstChild(folderName)
        if root then
            pcall(function()
                for _, child in ipairs(root:GetChildren()) do
                    scanBudgetYield("numeric_rs_folder")
                    if child.Name:match("^%d+$") then
                        local name, tier, imageUrl = extractInstanceMeta(child)
                        local cat = classifyCatalogCandidate(
                            name or child.Name,
                            "ReplicatedStorage." .. folderName .. "." .. child.Name)
                        if cat and name and #trim(name) > 1 then
                            if addCatalogEntry(name, tier, imageUrl, cat,
                                "ReplicatedStorage." .. folderName .. "." .. child.Name,
                                child.Name) then
                                added = added + 1
                            end
                        end
                    end
                end
            end)
        end
    end
    if added > 0 then
        print(LOG, ("Numeric-id RS folder scan: +%d catalog entries"):format(added))
    end
end

-- BLOCKER10E: targeted item catalog roots (no full-world scan).
local TARGET_ITEM_CATALOG_ROOTS = {
    "Shared", "Modules", "Packages", "Resources", "Assets", "Configs",
    "Items", "ItemData", "ItemDatas", "ItemDefinitions", "ItemInfo",
    "Rods", "RodData", "Baits", "Bait", "Crates", "Crate",
    "Materials", "Material", "Equipment", "Tools", "Consumables",
    "Shop", "Rewards", "Products", "Catalog", "Directory", "Index",
}

local MODULE_SKIP_PATH_PATTERNS = {
    "controller", "client", "ui", "effects", "animation", "network",
    "remote", "service", "inventory", "playerdata", "runtime", "state", "backpack",
}

local DEF_MODULE_EXACT_NAMES = {
    Items=true, Item=true, ItemData=true, ItemDatas=true, ItemDefinitions=true,
    ItemDefs=true, ItemDatabase=true, ItemDB=true, AllItems=true, ItemInfo=true,
    InventoryItem=true, Rods=true, Rod=true, RodData=true, RodDefinitions=true,
    Baits=true, Bait=true, BaitData=true, Crates=true, Crate=true,
    Materials=true, Material=true, Equipment=true, Tools=true, Resources=true,
    Consumables=true, Shop=true, Rewards=true, Products=true, GameData=true,
    Content=true, Catalog=true, Definitions=true, Registry=true, Directory=true,
    Index=true,
}

local function shouldSkipModulePath(path)
    local p = (path or ""):lower()
    for _, pat in ipairs(MODULE_SKIP_PATH_PATTERNS) do
        if p:find(pat) then return true end
    end
    return false
end

local function scanTargetedItemCatalogRoots()
    if LiveSafe.catalogAborted then return end
    setActiveSection("rs_scan")
    local t0 = os.clock()
    scanReplicatedStorageFishCatalog({
        maxInst = 600,
        folderList = TARGET_ITEM_CATALOG_ROOTS,
        requireModules = false,
    })
    perfEndSection(t0, "targeted_item_roots", 80)
end

local function scanTargetedDefinitionModules()
    if LiveSafe.catalogAborted or not LiveSafe.enableModuleRequire then return end
    local t0 = os.clock()
    setActiveSection("module_require")
    local hits = 0
    local queue = {}
    for _, name in ipairs({ "Shared", "Modules", "Packages", "Resources", "Assets", "Configs" }) do
        local root = ReplicatedStorage:FindFirstChild(name)
        if root then queue[#queue + 1] = { inst = root, path = "ReplicatedStorage." .. name, depth = 0 } end
    end
    local visited = 0
    while #queue > 0 do
        if LiveSafe.catalogAborted then break end
        if not scanBudgetYield("module_require") then break end
        local node = table.remove(queue, 1)
        if node.depth <= 7 then
            visited = visited + 1
            if visited > 1200 then break end
            local inst, path, depth = node.inst, node.path, node.depth
            if inst:IsA("ModuleScript") and DEF_MODULE_EXACT_NAMES[inst.Name] then
                if shouldSkipModulePath(path) then
                    LiveSafe.moduleRequire.skipped = LiveSafe.moduleRequire.skipped + 1
                else
                    LiveSafe.moduleRequire.attempted = LiveSafe.moduleRequire.attempted + 1
                    setActiveSection("module_require", path, inst.Name)
                    task.wait()
                    local okR, result = pcall(require, inst)
                    task.wait()
                    if okR and type(result) == "table" then
                        LiveSafe.moduleRequire.succeeded = LiveSafe.moduleRequire.succeeded + 1
                        local before = itemCatalogSourcesScanned
                        walkIdKeyedCatalogTable(result, path, 0)
                        if itemCatalogSourcesScanned > before then hits = hits + 1 end
                    else
                        LiveSafe.moduleRequire.failed = LiveSafe.moduleRequire.failed + 1
                    end
                end
            end
            if inst:IsA("Folder") or inst:IsA("Configuration") then
                local okC, children = pcall(function() return inst:GetChildren() end)
                if okC and type(children) == "table" then
                    for _, child in ipairs(children) do
                        if child:IsA("Folder") or child:IsA("Configuration") or child:IsA("ModuleScript") then
                            queue[#queue + 1] = {
                                inst = child,
                                path = path .. "." .. child.Name,
                                depth = depth + 1,
                            }
                        end
                    end
                end
            end
        end
    end
    perfEndSection(t0, "targeted_def_modules", 80)
    fishLog("MODULE_REQUIRE_SUMMARY attempted=%d succeeded=%d skipped=%d failed=%d hits=%d",
        LiveSafe.moduleRequire.attempted, LiveSafe.moduleRequire.succeeded,
        LiveSafe.moduleRequire.skipped, LiveSafe.moduleRequire.failed, hits)
end

local activeReplionClient = nil

local function ingestCatalogFromReplionClient(client)
    if type(client) ~= "table" then return 0 end
    local paths = {
        "Items", "ItemData", "ItemDatas", "ItemDefinitions", "AllItems",
        "Rods", "RodData", "Baits", "Crates", "Materials", "Catalog", "Definitions",
        "GameData", "Content", "Registry",
    }
    local added = 0
    for _, key in ipairs(paths) do
        local val = client[key]
        if type(val) == "table" then
            added = added + walkIdKeyedCatalogTable(val, "ReplionClient." .. key, 0)
        end
        scanBudgetYield("replion_client_catalog")
    end
    if added > 0 then
        fishLog("Replion client catalog ingest: +%d id-indexed entries", added)
    end
    return added
end

-- Public name for the metadata catalog builder (Part 6).
local function buildMetadataCatalog()
    catalogSearchRoots = {}
    itemCatalogSourcesScanned = 0
    unresolvedItemLogCount = 0
    local t0 = os.clock()
    resetScanBudget()
    local n = scanReplicatedStorageFishCatalog()
    pcall(buildNumericIdIndexFromRSFolders)
    perfEndSection(t0, "catalog_scan", 100)
    return n
end

-- BLOCKER10C: quick fish-first catalog before Phase A inventory upload.
local QUICK_FISH_FOLDERS = {
    "Fish", "Fishes", "FishData", "FishSchoolAssets", "FishCatalog", "FishTypes", "Fishing",
}

local function buildQuickPriorityCatalog()
    if LiveSafe.playerDataOnly or not LiveSafe.clientCatalogResolution then return end
    if LiveSafe.catalogAborted or not LiveSafe.safeMinimalMode then return end
    resetScanBudget()
    setActiveSection("quick_fish_catalog")
    local t0 = os.clock()
    scanReplicatedStorageFishCatalog({
        maxInst = 120,
        folderList = QUICK_FISH_FOLDERS,
        requireModules = false,
    })
    perfEndSection(t0, "quick_fish_catalog", 30)
end

local function buildMetadataCatalogAsync()
    if not LiveSafe.enableHeavyCatalog then
        fishLog("HEAVY_CATALOG disabled=true")
        return
    end
    if not isCurrentRun() then return end
    resetScanBudget()
    local t0 = os.clock()
    itemCatalogSourcesScanned = 0
    unresolvedItemLogCount = 0
    genericCatalogNodeCount = 0
    pcall(scanTargetedItemCatalogRoots)
    if not LiveSafe.catalogAborted then pcall(buildNumericIdIndexFromRSFolders) end
    if not LiveSafe.catalogAborted then pcall(scanTargetedDefinitionModules) end
    if not LiveSafe.catalogAborted and activeReplionClient then
        pcall(ingestCatalogFromReplionClient, activeReplionClient)
    end
    if LiveSafe.freeze.stalls >= 3 then
        abortHeavyCatalog("freeze_protection")
    end
    perfEndSection(t0, "catalog_scan_full", 100)
    LiveSafe.catalogBackgroundComplete = true
    printMetadataCatalogSummary(countPlaceholderItems())
    printFreezeMonitorSummary()
    fishLog("MODULE_REQUIRE_SUMMARY attempted=%d succeeded=%d skipped=%d failed=%d",
        LiveSafe.moduleRequire.attempted, LiveSafe.moduleRequire.succeeded,
        LiveSafe.moduleRequire.skipped, LiveSafe.moduleRequire.failed)
    tryFinalizeCatalogAndUpgrade("background_catalog")
end

-- Build a SMALL catalog_summary payload (counts + up to 3 name-only samples).
-- The full catalog is NOT sent — each inventory item already carries its own
-- name/imageUrl/tier after Replion resolution, so the backend needs only stats.
-- Keeping the payload under 32 KB prevents HTTP 413 from the backend.
local function buildCatalogSummary()
    -- Collect up to 3 sample fish entries (name + tier only, no imageUrl).
    local sample = {}
    for _, e in pairs(fishCatalog) do
        if #sample >= 3 then break end
        sample[#sample + 1] = { key = e.key, name = e.name, tier = e.tier }
    end
    -- Count metadataById key classes for diagnostics.
    local byIdCount, numericCount, stringCount = 0, 0, 0
    for k in pairs(metadataById) do
        byIdCount = byIdCount + 1
        if k:match("^%d+$") then numericCount = numericCount + 1
        else stringCount = stringCount + 1 end
    end
    return {
        type       = "catalog_summary",
        playerName = LocalPlayer.Name,
        userId     = LocalPlayer.UserId,
        scannedAt  = os.time(),
        catalogStats = {
            fish             = catalogStats.fish,
            rods             = catalogStats.rods,
            items            = catalogStats.items,
            bait             = catalogStats.bait,
            images           = catalogStats.images,
            tiers            = catalogStats.tiers,
            metadataByIdKeys = byIdCount,
            numericIdKeys    = numericCount,
            stringIdKeys     = stringCount,
        },
        sampleEntries = sample,
    }
end

-- Send the catalog summary to the backend (diagnostic counts only).
-- Replaces the old fish_catalog_snapshot which could exceed 200 KB and was
-- silently rejected by the backend with HTTP 413.
local function syncCatalogToBackend()
    local total = catalogStats.fish + catalogStats.rods + catalogStats.items + catalogStats.bait
    if total == 0 then
        print(LOG, "Catalog empty — nothing to sync.")
        return
    end
    local ok, encoded = pcall(function()
        return HttpService:JSONEncode(buildCatalogSummary())
    end)
    if not ok then warn(LOG, "Catalog encode error:", encoded); return end

    local byteCount = #encoded
    print(LOG, ("DASHBOARD_SEND catalog_summary user=%s fish=%d rods=%d items=%d images=%d bytes=%d"):format(
        LocalPlayer.Name, catalogStats.fish, catalogStats.rods, catalogStats.items, catalogStats.images, byteCount))

    -- Safety guard: abort if summary is still unexpectedly large.
    if byteCount > 32000 then
        warn(LOG, ("catalog_summary skipped_full_payload reason=too_large bytes=%d"):format(byteCount))
        return
    end

    task.spawn(function()
        if not isCurrentRun() then return end
        local sent, resultC = pcall(sendDashboardRequest, "catalog", {
            Url     = CATALOG_URL,
            Method  = "POST",
            Headers = { ["Content-Type"] = "application/json" },
            Body    = encoded,
        })
        local code  = (sent and type(resultC) == "table" and resultC.StatusCode) or "?"
        local body2 = (sent and type(resultC) == "table" and type(resultC.Body) == "string")
                      and resultC.Body:sub(1, 120) or ""
        local ok200 = (tostring(code) == "200")
        print(LOG, ("DASHBOARD_RESPONSE catalog_summary success=%s status=%s bodyPreview=%s"):format(
            tostring(ok200), tostring(code), body2))
        if not sent then
            warn(LOG, "Catalog sync error:", tostring(resultC))
        elseif not ok200 then
            warn(LOG, "Catalog HTTP error — backend returned:", tostring(code), body2)
        end
    end)
end

-- BLOCKER11: upload compact id-keyed catalog for owned ids so backend can enrich placeholders.
local function syncCompactIdCatalogToBackend()
    if #ownedOrder == 0 then return end
    local fishL, rodsL, itemsL = {}, {}, {}
    local count = 0
    for _, key in ipairs(ownedOrder) do
        local e = ownedInventory[key]
        if e and e.itemId and tostring(e.itemId):match("^%d+$") then
            local idStr = tostring(e.itemId)
            local meta = metadataById[idStr]
            if meta and meta.name and not isPlaceholderName(meta.name, idStr) and count < 200 then
                count = count + 1
                local entry = {
                    name = meta.name, key = normalizeName(meta.name), itemId = idStr,
                    tier = meta.tier or "unknown",
                    category = meta.category or e.category or "items",
                    source = meta.source,
                }
                local c = (entry.category or ""):lower()
                if c == "rod" or c == "bait" then rodsL[#rodsL + 1] = entry
                elseif c == "fish" then fishL[#fishL + 1] = entry
                else itemsL[#itemsL + 1] = entry end
            end
        end
    end
    if count == 0 then return end
    local payload = {
        type = "fish_catalog_snapshot",
        playerName = LocalPlayer.Name,
        userId = LocalPlayer.UserId,
        catalog = { fish = fishL, rods = rodsL, items = itemsL },
    }
    local ok, encoded = pcall(function() return HttpService:JSONEncode(payload) end)
    if not ok or #encoded > 480000 then return end
    fishLog("DASHBOARD_SEND id_catalog_snapshot entries=%d bytes=%d", count, #encoded)
    task.spawn(function()
        if not isCurrentRun() then return end
        pcall(sendDashboardRequest, "catalog", {
            Url = CATALOG_URL, Method = "POST",
            Headers = { ["Content-Type"] = "application/json" },
            Body = encoded,
        })
    end)
end

-- Backward-compat alias retained for validation/legacy references.
local function buildCatalogFromRS()
    return scanReplicatedStorageFishCatalog()
end

-- ================================================================
-- REPLION SUBSYSTEM — inventory source of truth.
--
-- Replion is a replicated-state library used by many Roblox games.
-- Its client API differs between versions, so every access is probed
-- and pcall-guarded. We NEVER call mutation-style methods.
-- ================================================================

-- Method/property names that would MUTATE state — never invoked.
local REPLION_MUTATION_NAMES = {
    Set=true, Update=true, SetData=true, Increase=true, Insert=true,
    Remove=true, Delete=true, Clear=true, Fire=true, FireServer=true,
    Invoke=true, InvokeServer=true, Save=true, Equip=true, Buy=true,
    Sell=true, Claim=true, Give=true, Destroy=true,
}

-- Names that suggest a Replion client module.
local REPLION_MODULE_NAMES = {
    replion=true, replionclient=true, clientreplion=true, client=true,
}

-- Methods/fields that identify a Replion CLIENT object.
local REPLION_CLIENT_METHODS = {
    "GetReplion","WaitReplion","Get","OnReplionAdded","ReplionAdded",
    "GetReplions","GetAll","OnDataChange","Changed","ListenToChange",
    "Listen","GetData",
}

-- Candidate player-data Replion names (tried in order).
local REPLION_DATA_NAMES = {
    "PlayerData","Data","Profile","ProfileData","Player","PlayerProfile",
    "Inventory","FishData","UserData","Session",
}

-- Inventory-like keys that mark a strong player-data replion.
local INVENTORY_KEYS = {
    "Inventory","Items","Fish","Fishes","Rods","Equipment","Backpack",
    "Owned","Collection","FishIndex","Caught","Stats","Data",
}

-- Keys used to score / locate inventory-like structures (PART 3).
local INVENTORY_SCORE_KEYS = {
    "Inventory","Items","Fish","Fishes","Rods","Equipment","Backpack",
    "Collection","Owned","Stats","Caught","Index","FishIndex",
    "UserId","Level","Coins","Cash",
}

-- ----------------------------------------------------------------
-- Replion discovery state (shared) + early status reporting.
-- Declared up here so main() can report a phase BEFORE any inventory
-- exists, which is what tells the website "script is running".
-- ----------------------------------------------------------------
local replionFound      = false
local selectedReplion   = nil
local inventorySource   = "none"   -- "replion" | "replion_missing"
local trackerPhase      = "startup"
local replionCandidatesSeen = {}   -- names/identifiers observed via discovery
local replionNamesTried     = {}   -- GetReplion/WaitReplion keys attempted
local replionClientMethods  = {}   -- methods exposed by the found client

-- syncStatus is defined later (needs TRACKER_URL/performDashboardRequest);
-- forward-declare so discovery code can report phases as they happen.
local syncStatus  -- function(online, phase, extra)

local function dprint(...)
    if DEBUG_REPLION_DISCOVERY then print(LOG, ...) end
end

local function shapeHasMethods(obj)
    local found = {}
    if type(obj) ~= "table" then return found end
    for _, m in ipairs(REPLION_CLIENT_METHODS) do
        local ok, val = pcall(function() return obj[m] end)
        if ok and (type(val) == "function") then
            found[#found + 1] = m
        end
    end
    return found
end

-- ----------------------------------------------------------------
-- PART 2: Safely describe a Replion candidate object's shape.
-- Read-only: never invokes mutators, never prints huge data values.
-- ----------------------------------------------------------------
local REPLION_SHAPE_PROBE = {
    "Data","_data","data","GetData","Get","Read","GetRawData",
    "ListenToChange","OnChange","OnDataChange","Listen","Changed","DataChanged",
}
local REPLION_ID_FIELDS_PROBE = { "Name","Identifier","Id","Channel","Tags" }

local function describeReplionObject(replion)
    if not DEBUG_REPLION_DISCOVERY then return end
    print(LOG, "Replion object shape:")
    local tn = typeof and typeof(replion) or type(replion)
    print(LOG, "  typeof:", tostring(tn))
    local okS, s = pcall(function() return tostring(replion) end)
    print(LOG, "  tostring:", okS and s or "?")
    if type(replion) ~= "table" then return end
    for _, field in ipairs(REPLION_SHAPE_PROBE) do
        local ok, v = pcall(function() return replion[field] end)
        if ok and v ~= nil then
            print(LOG, "  " .. field .. ":", type(v))
        end
    end
    for _, field in ipairs(REPLION_ID_FIELDS_PROBE) do
        local ok, v = pcall(function() return replion[field] end)
        if ok and v ~= nil and type(v) ~= "table" then
            print(LOG, "  " .. field .. " =", tostring(v))
        end
    end
    -- Metatable __index method table (read-only inspection).
    pcall(function()
        local mt = getmetatable(replion)
        if type(mt) == "table" and type(mt.__index) == "table" then
            local names = {}
            for k, v in pairs(mt.__index) do
                if type(v) == "function" then names[#names + 1] = tostring(k) end
            end
            if #names > 0 then
                print(LOG, "  __index methods:", table.concat(names, ", "))
            end
        end
    end)
end

-- ----------------------------------------------------------------
-- PART 2: Discover the Replion client module safely.
-- Returns (clientObject, modulePath, methodList) or nil.
-- ----------------------------------------------------------------
local function findReplionClient()
    print(LOG, "Replion discovery starting...")
    local visited, scanned = {}, {n = 0}
    local best, bestPath, bestMethods = nil, nil, {}

    local function consider(module)
        if scanned.n > 4000 then return end
        local nm = module.Name:lower()
        if not REPLION_MODULE_NAMES[nm] and not nm:find("replion") then return end
        if DEBUG_VERBOSE_INVENTORY then
            print(LOG, "Candidate module:", module:GetFullName())
        else
            print(LOG, "Candidate module:", nm)
        end
        local okR, result = pcall(require, module)
        if not okR or type(result) ~= "table" then return end

        local methods = shapeHasMethods(result)
        -- A Replion client must expose at least one accessor method.
        if #methods >= 1 and #methods > #bestMethods then
            best, bestPath, bestMethods = result, module:GetFullName(), methods
        end
    end

    local roots = {
        ReplicatedStorage:FindFirstChild("Packages"),
        ReplicatedStorage:FindFirstChild("Replion"),
        ReplicatedStorage:FindFirstChild("Shared"),
        ReplicatedStorage:FindFirstChild("Modules"),
        ReplicatedStorage:FindFirstChild("Controllers"),
        ReplicatedStorage,
    }

    local function descend(inst, depth)
        if depth > 8 or not inst then return end
        local addr = tostring(inst)
        if visited[addr] then return end
        visited[addr] = true
        local ok, children = pcall(function() return inst:GetChildren() end)
        if not ok or type(children) ~= "table" then return end
        for _, child in ipairs(children) do
            scanned.n = scanned.n + 1
            if scanned.n > 4000 then return end
            if child:IsA("ModuleScript") then pcall(consider, child) end
            descend(child, depth + 1)
        end
    end

    for _, root in ipairs(roots) do
        if root then pcall(descend, root, 0) end
    end

    if best then
        replionClientMethods = bestMethods
        print(LOG, "Replion client found:", bestPath)
        print(LOG, "Available methods:", table.concat(bestMethods, ", "))
        return best, bestPath, bestMethods
    end
    warn(LOG, "Replion client not found.")
    return nil
end

-- ----------------------------------------------------------------
-- Safe data reader: returns a data table for a replion or nil.
-- Tries the many read APIs Replion versions expose; never mutates.
-- ----------------------------------------------------------------
local function readReplionData(replion)
    if replion == nil then return nil end

    -- Direct data fields.
    local direct = nil
    pcall(function()
        if type(replion) == "table" then
            direct = rawget(replion, "Data") or rawget(replion, "_data")
                  or rawget(replion, "data")
        end
    end)
    if type(direct) == "table" then return direct end

    -- Method-based readers (read-only names only). Ordered widest-first.
    local readers = {
        function() return replion:GetData() end,
        function() return replion:Get() end,
        function() return replion:Get({}) end,
        function() return replion:Get(nil) end,
        function() return replion:Read() end,
        function() return replion:GetRawData() end,
        -- Path-only Get() variants (ytrev_replion exposes Get(path)).
        function() return replion:Get("Data") end,
        function() return replion:Get({"Data"}) end,
        function() return replion:Get("Inventory") end,
        function() return replion:Get({"Inventory"}) end,
    }
    for _, fn in ipairs(readers) do
        local ok, result = pcall(fn)
        if ok and type(result) == "table" then
            return result
        end
    end
    return nil
end

-- Score a data table by how many inventory-like keys it contains.
local function inventoryScore(data)
    if type(data) ~= "table" then return 0 end
    local score = 0
    for _, key in ipairs(INVENTORY_SCORE_KEYS) do
        if data[key] ~= nil then score = score + 1 end
    end
    -- Nested data.Data / data.Inventory.* bonus.
    if type(data.Inventory) == "table" then score = score + 2 end
    if type(data.Data) == "table" then score = score + 1 end
    return score
end

-- Collect inventory-like paths inside a data table (1-2 levels deep).
local function inventoryPaths(data)
    local paths = {}
    if type(data) ~= "table" then return paths end
    local function isInvKey(k)
        local s = tostring(k):lower()
        return s == "inventory" or s == "items" or s == "fish" or s == "fishes"
            or s == "rods" or s == "equipment" or s == "backpack"
            or s == "collection" or s == "owned"
    end
    for k, v in pairs(data) do
        if isInvKey(k) then paths[#paths + 1] = tostring(k) end
        if type(v) == "table" then
            for k2, _ in pairs(v) do
                if isInvKey(k2) then paths[#paths + 1] = tostring(k) .. "." .. tostring(k2) end
            end
        end
    end
    return paths
end

-- ----------------------------------------------------------------
-- PART 1 + 3: Find the player-data Replion using the client.
--
-- ytrev_replion@2.x exposes GetReplion / WaitReplion / OnReplionAdded.
-- A player-data Replion is frequently NOT replicated the instant our
-- LocalScript starts, so a single synchronous GetReplion() returns nil.
-- We therefore: (1) try GetReplion for every known name, (2) subscribe
-- to OnReplionAdded to capture replions as they replicate, (3) spin a
-- short WaitReplion loop, and (4) keep listening for up to
-- REPLION_WAIT_SECONDS before giving up. Read-only throughout.
--
-- Returns (replion, name, candidatesSeen, namesTried) — replion is nil
-- if none was found within the wait window.
-- ----------------------------------------------------------------
local function findPlayerDataReplion(client)
    if not client then return nil end
    print(LOG, "Locating player data replion...")

    local candidates = {}     -- list of { replion, name, score, data }
    local seenSet     = {}    -- de-dupe by tostring(replion)
    local namesTried  = {}

    local function consider(rep, name)
        if type(rep) ~= "table" then return end
        local addr = tostring(rep)
        if seenSet[addr] then return end
        seenSet[addr] = true
        local data  = readReplionData(rep)
        local score = inventoryScore(data)
        replionCandidatesSeen[#replionCandidatesSeen + 1] = name
        if DEBUG_REPLION_DISCOVERY then
            describeReplionObject(rep)
            print(LOG, ("Inspecting added replion: %s score=%d"):format(tostring(name), score))
            local paths = inventoryPaths(data)
            for _, p in ipairs(paths) do
                print(LOG, "Inventory-like path found:", p)
            end
        end
        candidates[#candidates + 1] = { replion = rep, name = name, score = score, data = data }
    end

    -- Build the ordered key list (known names + user-specific keys).
    local keys = {}
    for _, n in ipairs(REPLION_DATA_NAMES) do keys[#keys + 1] = n end
    keys[#keys + 1] = "ProfileData"
    keys[#keys + 1] = "PlayerProfile"
    keys[#keys + 1] = "Replica"
    keys[#keys + 1] = "LocalPlayer"
    keys[#keys + 1] = tostring(LocalPlayer.UserId)
    keys[#keys + 1] = LocalPlayer.Name
    keys[#keys + 1] = "Player_"     .. LocalPlayer.UserId
    keys[#keys + 1] = "PlayerData_" .. LocalPlayer.UserId
    keys[#keys + 1] = "Profile_"    .. LocalPlayer.UserId
    keys[#keys + 1] = "Data_"       .. LocalPlayer.UserId

    -- 1) Synchronous GetReplion / Get for each known name.
    for _, key in ipairs(keys) do
        namesTried[#namesTried + 1] = key
        local outcome = "nil"
        local ok, rep = pcall(function()
            local fn = client.GetReplion or client.Get
            if type(fn) ~= "function" then return nil end
            return fn(client, key)
        end)
        if not ok then
            outcome = "error"
        elseif type(rep) == "table" then
            outcome = "success"
            consider(rep, key)
        end
        dprint(('Trying GetReplion("%s") -> %s'):format(key, outcome))
    end

    -- 2) Subscribe to OnReplionAdded to capture late replications.
    pcall(function()
        local fn = client.OnReplionAdded or client.ReplionAdded
        if type(fn) == "function" then
            fn(client, function(rep)
                local nm = "added"
                pcall(function()
                    nm = tostring(rep and (rep.Name or rep.Identifier or rep.Channel) or "added")
                end)
                dprint("OnReplionAdded:", nm)
                consider(rep, nm)
            end)
        end
    end)

    local function bestSoFar()
        if #candidates == 0 then return nil end
        table.sort(candidates, function(a, b) return a.score > b.score end)
        return candidates[1]
    end

    -- If we already have a strong candidate, return immediately.
    local chosen = bestSoFar()
    if chosen and chosen.score > 0 then
        return chosen.replion, chosen.name, replionCandidatesSeen, namesTried
    end

    -- 3) WaitReplion loop + OnReplionAdded polling for up to N seconds.
    local deadline = os.clock() + REPLION_WAIT_SECONDS
    while os.clock() < deadline and not _G.StopAutoFish do
        for _, key in ipairs(keys) do
            local ok, rep = pcall(function()
                local fn = client.WaitReplion
                if type(fn) ~= "function" then return nil end
                -- ytrev WaitReplion(name) yields until added; guard with the
                -- loop deadline rather than an internal timeout.
                return fn(client, key)
            end)
            if ok and type(rep) == "table" then
                dprint(('Trying WaitReplion("%s") -> success'):format(key))
                consider(rep, key)
            else
                dprint(('Trying WaitReplion("%s") -> %s'):format(key, ok and "timeout" or "error"))
            end
            if _G.StopAutoFish then break end
            chosen = bestSoFar()
            if chosen and chosen.score > 0 then break end
        end
        chosen = bestSoFar()
        if chosen and chosen.score > 0 then break end
        -- Enumerate (some Replion builds expose this) and re-check.
        for _, method in ipairs({ "GetReplions", "GetAll" }) do
            pcall(function()
                local fn = client[method]
                if type(fn) ~= "function" then return end
                local all = fn(client)
                if type(all) == "table" then
                    for k, rep in pairs(all) do consider(rep, tostring(k)) end
                end
            end)
        end
        chosen = bestSoFar()
        if chosen and chosen.score > 0 then break end
        task.wait(1)
    end

    chosen = bestSoFar()
    if not chosen or chosen.score == 0 then
        for _, c in ipairs(candidates) do
            print(LOG, ("Rejected replion candidate: %s (score %d)"):format(c.name, c.score))
        end
        warn(LOG, "No player-data replion with inventory structure found.")
        return nil, nil, replionCandidatesSeen, namesTried
    end

    -- Log selection + rejected.
    if type(chosen.data) == "table" then
        local keys2 = {}
        for k in pairs(chosen.data) do keys2[#keys2 + 1] = tostring(k) end
        print(LOG, "Replion data top-level keys:", table.concat(keys2, ", "))
        for _, p in ipairs(inventoryPaths(chosen.data)) do
            print(LOG, "Inventory-like path found:", p)
        end
    end
    for i = 2, #candidates do
        local c = candidates[i]
        print(LOG, ("Rejected replion candidate: %s (weaker score %d)"):format(c.name, c.score))
    end
    print(LOG, "Selected player data replion:", chosen.name)
    return chosen.replion, chosen.name, replionCandidatesSeen, namesTried
end

-- ================================================================
-- OWNED INVENTORY (source of truth = Replion).
-- ownedInventory[normalizedKey] = { name, count, weight, tier,
--   imageUrl, category, itemId, source }
-- This map is REPLACED on every Replion snapshot (never appended), so
-- counts always reflect current player data and never double-count.
-- ================================================================
local ownedInventory = {}   -- normalizedKey -> entry
local ownedOrder      = {}  -- ordered keys for stable output
local replionRejected = {}  -- { rawKey, sourcePath, reason }
local lastSentStr     = ""  -- delta-check cache
local rejectLogCount  = 0   -- capped reject diagnostic lines per parse

-- First N rejected entries for parseStats.firstRejected (backend diagnostic).
local function buildFirstRejectedSample(maxN)
    local out, limit = {}, maxN or 10
    for i = 1, math.min(#replionRejected, limit) do
        local r = replionRejected[i]
        out[#out + 1] = {
            rawKey     = r.rawKey,
            sourcePath = r.sourcePath,
            reason     = r.reason,
        }
    end
    return out
end

-- Result of the most recent Replion inventory parse (BLOCKER 2). Drives the
-- REPLION_PARSE_RESULT log line and the inventory_empty/parse_failed phases.
local replionParseResult = {
    selected = "?", path = "none",
    raw = 0, accepted = 0, acceptedInstances = 0, rejected = 0,
    fish = 0, rods = 0, items = 0,
    images = 0, tiers = 0,
    pathExists = false,
}

-- Field-name candidates for Replion item records.
local R_NAME_FIELDS   = {"Name","name","ItemName","itemName","FishName","fishName","DisplayName","displayName","Id","id","ItemId","itemId","Type","type"}
local R_COUNT_FIELDS  = {"Amount","amount","Count","count","Quantity","quantity","Qty","qty","Stack","stack","Owned","owned"}
local R_WEIGHT_FIELDS = {"Weight","weight","MaxWeight","maxWeight","TotalWeight","totalWeight"}
local R_TIER_FIELDS   = {"Rarity","rarity","Tier","tier","Quality","quality","Grade","grade","Rank","rank"}
local R_IMAGE_FIELDS  = {"Image","image","ImageId","imageId","Icon","icon","IconId","iconId","Thumbnail","thumbnail","Texture","texture","AssetId","assetId"}
local R_ID_FIELDS     = {"ItemId","itemId","Id","id","FishId","fishId","Identifier","identifier","Key","key"}

-- Forward declarations for Replion parser helpers.
-- Use `function name()` assignment form below — NOT `local function name()`,
-- which creates a separate binding and leaves earlier references nil.
local parseWeight
local readAnyField
local safeCallNamed
local recordReplionReject
local extractEntryNumericId
local makeOwnedKey
local classifyOwned
local mergeOwnedItem
local addOwnedNumericFallback
local consumeReplionEntry
local finalizeReplionParseStats

function readAnyField(record, fields)
    if type(record) ~= "table" then return nil end
    for _, f in ipairs(fields) do
        local v = record[f]
        if v ~= nil then return v end
    end
    return nil
end

function parseWeight(raw)
    if type(raw) == "number" then return raw end
    if type(raw) == "string" then return tonumber(raw:match("[%d%.]+")) or 0 end
    if type(raw) == "table" then
        local direct = raw.Value or raw.value or raw.Weight or raw.weight
        if direct ~= nil then return parseWeight(direct) end
        for _, v in pairs(raw) do
            local n = parseWeight(v)
            if n and n > 0 then return n end
        end
    end
    return 0
end

function safeCallNamed(name, fn, ...)
    if type(fn) ~= "function" then
        print(LOG, ("MISSING_HELPER name=%s type=%s"):format(tostring(name), type(fn)))
        return nil, "missing_helper"
    end
    local ok, result = pcall(fn, ...)
    if not ok then
        print(LOG, ("HELPER_ERROR name=%s error=%s"):format(tostring(name), tostring(result):sub(1, 200)))
        return nil, result
    end
    return result, nil
end

function recordReplionReject(rawKey, sourcePath, reason)
    replionRejected[#replionRejected + 1] = {
        rawKey     = tostring(rawKey or "?"),
        sourcePath = sourcePath or "?",
        reason     = reason or "unknown",
    }
end

function extractEntryNumericId(value)
    if type(value) ~= "table" then return nil end
    local numId = value.Id or value.ID or value.id or value.ItemId or value.ItemID
        or value.itemId or value.FishId or value.FishID or value.RodId or value.RodID
    if numId == nil then return nil end
    return tostring(numId)
end

function makeOwnedKey(displayName, itemId)
    if type(itemId) == "string" and itemId:match("^%d+$") then
        return "item_id_" .. itemId
    end
    local norm = select(1, safeCallNamed("normalizeName", normalizeName, displayName))
    if type(norm) == "string" and #norm > 0 then return norm end
    return tostring(displayName or "?")
end

function classifyOwned(name, sourcePath)
    local n = (name or ""):lower()
    local p = (sourcePath or ""):lower()
    if n:find("rod") or p:find("rod") then return "rod" end
    if n:find("bait") or p:find("bait") then return "bait" end
    if p:find("fish") then return "fish" end
    if p:find("rod") then return "rod" end
    if p:find("item") then return "items" end
    return "fish"
end

-- Register one owned entry (replacing/stacking within the CURRENT snapshot).
function mergeOwnedItem(rawKey, count, weight, tier, imageUrl, category, itemId, sourcePath, opts)
    opts = type(opts) == "table" and opts or {}
    if type(rawKey) == "number" then rawKey = tostring(rawKey) end
    if type(itemId) == "number" then itemId = tostring(itemId) end

    local lookupName = nil
    local trimmedKey = select(1, safeCallNamed("trim", trim, rawKey)) or ""
    if #trimmedKey > 0 then lookupName = trimmedKey end
    local trimmedId = select(1, safeCallNamed("trim", trim, itemId)) or ""
    if #trimmedId > 0 and not lookupName then lookupName = trimmedId end
    if not lookupName then
        recordReplionReject(rawKey, sourcePath, "missing_name")
        return false
    end

    local normalized = select(1, safeCallNamed("normalizeName", normalizeName, lookupName)) or ""
    if normalized == "" then
        normalized = makeOwnedKey(lookupName, itemId)
    end
    if normalized == "" or normalized == "?" then
        recordReplionReject(rawKey, sourcePath, "empty_name")
        return false
    end

    if select(1, safeCallNamed("isStatLabel", isStatLabel, normalized)) then
        recordReplionReject(rawKey, sourcePath, "stat_label")
        return false
    end

    local meta = select(1, safeCallNamed("resolveFishMeta", resolveFishMeta, normalized))
    if not meta and type(itemId) == "string" and #trimmedId > 0 then
        meta = select(1, safeCallNamed("resolveMetaById", resolveMetaById, itemId))
    end

    local resolvedName = (meta and meta.name) or lookupName
    local hasNumericId = type(itemId) == "string" and itemId:match("^%d+$") ~= nil
    if not meta and not (tier or imageUrl) and not lookupName:match("%a") and not hasNumericId then
        recordReplionReject(rawKey, sourcePath, "unresolved_replion_item")
        return false
    end

    count  = math.max(1, math.floor(toNumberOr(count, 1)))
    weight = toNumberOr(weight, 0)
    local tierKey = tier and tostring(tier):lower() or nil
    local resolvedTier  = (meta and meta.tier) or (tierKey and TIER_MAP[tierKey]) or tier or nil
    local resolvedImage = (meta and meta.imageUrl)
        or select(1, safeCallNamed("resolveImageUrl", resolveImageUrl, imageUrl)) or nil
    local resolvedCat   = category or (meta and meta.category)
        or select(1, safeCallNamed("classifyOwned", classifyOwned, resolvedName, sourcePath)) or "items"

    local catalogSource = opts.catalogSource or (meta and meta.source) or nil
    local catalogReason = opts.catalogReason or (meta and "catalog_hit") or nil
    local isResolved    = opts.resolved == true or meta ~= nil

    local existing = ownedInventory[normalized]
    if not existing then
        ownedInventory[normalized] = {
            name          = resolvedName,
            count         = count,
            weight        = weight,
            tier          = resolvedTier,
            imageUrl      = resolvedImage,
            category      = resolvedCat,
            itemId        = (type(itemId) == "string") and itemId or nil,
            source        = sourcePath or "Replion",
            resolved      = isResolved,
            catalogSource = catalogSource,
            catalogReason = catalogReason,
        }
        ownedOrder[#ownedOrder + 1] = normalized
    else
        existing.count  = safeAdd(existing.count, count)
        existing.weight = math.max(toNumberOr(existing.weight, 0), weight)
        if resolvedTier and not existing.tier then existing.tier = resolvedTier end
        if resolvedImage and not existing.imageUrl then existing.imageUrl = resolvedImage end
        if isResolved then
            existing.resolved = true
            if catalogSource then existing.catalogSource = catalogSource end
            if catalogReason then existing.catalogReason = catalogReason end
            if shouldReplaceName(existing.name, resolvedName, itemId) then
                existing.name = resolvedName
            elseif isPlaceholderName(resolvedName, itemId) and not isPlaceholderName(existing.name, itemId) then
                fishLog("CATALOG_DOWNGRADE_BLOCKED id=%s existing=%s attempted=%s source=%s",
                    tostring(itemId or "?"), tostring(existing.name), tostring(resolvedName),
                    tostring(sourcePath or "?"))
            end
            if resolvedCat == "fish" then
                existing.category = "fish"
            elseif meta and meta.category == "fish" then
                existing.category = "fish"
            elseif not existing.category or existing.category == "items" then
                existing.category = resolvedCat
            end
        end
    end

    if DEBUG_VERBOSE_INVENTORY then
        print(LOG, ("  Replion owned: %s x%d tier=%s image=%s src=%s"):format(
            resolvedName, count, resolvedTier or "?", resolvedImage and "yes" or "no", sourcePath or "?"))
    end
    return true
end

-- Accept numeric Id (+ optional UUID); resolve catalog/name or placeholder fallback.
function addOwnedNumericFallback(idStr, uuid, weight, sourcePath, sampleSlot, entry)
    idStr = tostring(idStr or "?")
    weight = toNumberOr(weight, 0)
    entry = type(entry) == "table" and entry or nil
    local uuidStr = uuid and tostring(uuid):sub(1, 36) or nil

    if LiveSafe.playerDataOnly and not LiveSafe.clientCatalogResolution then
        local placeholderName = "Item #" .. idStr
        local normalized = makeOwnedKey(placeholderName, idStr)
        local existing = ownedInventory[normalized]
        if not existing then
            ownedInventory[normalized] = {
                name = placeholderName, count = 1, weight = weight,
                tier = nil, imageUrl = nil, category = "items", itemId = idStr,
                uuid = uuidStr, resolved = false,
                catalogReason = "server_enrichment_pending", source = sourcePath or "Replion",
            }
            ownedOrder[#ownedOrder + 1] = normalized
        else
            existing.count = safeAdd(existing.count, 1)
            existing.weight = math.max(toNumberOr(existing.weight, 0), weight)
        end
        return true
    end

    local metaBlock = entry and decodeMetadata(entry.Metadata or entry.metadata, idStr) or nil
    if type(metaBlock) == "table" and weight == 0 then
        weight = toNumberOr(parseWeight(
            metaBlock.Weight or metaBlock.weight or metaBlock.MaxWeight), 0)
    end

    local meta, catSource = resolveCatalogMetaById(idStr)
    local displayName = resolveItemDisplayName(idStr, entry, meta, metaBlock)
    local placeholderName = "Item #" .. idStr

    if meta and meta.name then
        local cat = resolveCategoryFromMeta(meta, metaBlock, sourcePath)
        if not sampleSlot or sampleSlot <= DEBUG_SAMPLE_LIMIT then
            print(LOG, ("NUMERIC_ID_CATALOG_HIT id=%s name=%s source=%s"):format(
                idStr, meta.name, tostring(catSource or "?"):sub(1, 40)))
        end
        return mergeOwnedItem(meta.name, 1, weight, meta.tier, meta.imageUrl, cat, idStr, sourcePath, {
            resolved = true, catalogSource = catSource, catalogReason = "catalog_hit",
        })
    end

    if displayName ~= placeholderName then
        local cat = resolveCategoryFromMeta(meta, metaBlock, sourcePath)
        return mergeOwnedItem(displayName, 1, weight, meta and meta.tier, meta and meta.imageUrl,
            cat, idStr, sourcePath, {
                resolved = true, catalogSource = catSource or "entry_name", catalogReason = "entry_name",
            })
    end

    local normalized = makeOwnedKey(placeholderName, idStr)
    local existing = ownedInventory[normalized]
    if not existing then
        ownedInventory[normalized] = {
            name          = placeholderName,
            count         = 1,
            weight        = weight,
            tier          = nil,
            imageUrl      = nil,
            category      = "items",
            itemId        = idStr,
            uuid          = uuidStr,
            resolved      = false,
            catalogReason = "catalog_missing_numeric_id",
            source        = sourcePath or "Replion",
        }
        ownedOrder[#ownedOrder + 1] = normalized
    else
        existing.count  = safeAdd(existing.count, 1)
        existing.weight = math.max(toNumberOr(existing.weight, 0), weight)
    end

    if not sampleSlot or sampleSlot <= DEBUG_SAMPLE_LIMIT then
        print(LOG, ("NUMERIC_ID_FALLBACK_ACCEPTED id=%s uuid=%s name=%s"):format(
            idStr, tostring(uuidStr or "none"):sub(1, 12), placeholderName))
    end
    return true
end

function consumeReplionEntry(key, value, fullPath, sampleSlot)
    if not LiveSafe.verbose then sampleSlot = nil end
    if LiveSafe.verbose then
        consumeEntryActiveLogCount = consumeEntryActiveLogCount + 1
        if consumeEntryActiveLogCount <= 3 then
            fishLogDebug("CONSUME_ENTRY_ACTIVE raw=%s key=%s",
                tostring(sampleSlot or consumeEntryActiveLogCount), tostring(key))
        end
    end

    if type(value) == "number" then
        return mergeOwnedItem(key, value, 0, nil, nil, nil,
            (type(key) == "string") and key or nil, fullPath)
    end

    if type(value) ~= "table" then
        recordReplionReject(key, fullPath, "unsupported_value_type")
        return false
    end

    local uuid  = value.UUID or value.Uuid or value.uuid
    local numId = value.Id or value.ID or value.id or value.ItemId or value.ItemID
                  or value.itemId or value.FishId or value.FishID or value.RodId or value.RodID

    -- Numeric Id (+ optional UUID): catalog resolve or placeholder fallback.
    if numId ~= nil then
        local metaBlock = value.Metadata or value.metadata
        local w = 0
        if type(metaBlock) == "table" then
            w = toNumberOr(select(1, safeCallNamed("parseWeight", parseWeight,
                metaBlock.Weight or metaBlock.weight or metaBlock.MaxWeight)), 0)
        else
            w = toNumberOr(select(1, safeCallNamed("parseWeight", parseWeight, metaBlock)), 0)
        end
        local idStr = tostring(numId)
        local accepted = addOwnedNumericFallback(idStr, uuid, w, fullPath, sampleSlot, value) == true
        if sampleSlot then
            local meta = select(1, safeCallNamed("resolveMetaById", resolveMetaById, idStr))
            print(LOG, ("  Parse sample [%d]: Id=%s UUID=%s resolved=%s name=%s %s"):format(
                sampleSlot, idStr, tostring(uuid or "none"):sub(1, 12),
                meta and "yes" or "no",
                meta and meta.name or ("Item #" .. idStr),
                accepted and "accepted" or "rejected"
            ))
        end
        return accepted
    end

    local name   = select(1, safeCallNamed("readAnyField", readAnyField, value, R_NAME_FIELDS))
    local count  = tonumber(select(1, safeCallNamed("readAnyField", readAnyField, value, R_COUNT_FIELDS))) or 1
    local weight = select(1, safeCallNamed("parseWeight", parseWeight,
        select(1, safeCallNamed("readAnyField", readAnyField, value, R_WEIGHT_FIELDS)))) or 0
    local tier   = select(1, safeCallNamed("readAnyField", readAnyField, value, R_TIER_FIELDS))
    local image  = select(1, safeCallNamed("readAnyField", readAnyField, value, R_IMAGE_FIELDS))
    local itemId = select(1, safeCallNamed("readAnyField", readAnyField, value, R_ID_FIELDS))

    local metaBlock = value.Metadata or value.metadata or value.Meta or value.meta
    if type(metaBlock) == "table" then
        name   = name or select(1, safeCallNamed("readAnyField", readAnyField, metaBlock, R_NAME_FIELDS))
        if weight == 0 then
            weight = select(1, safeCallNamed("parseWeight", parseWeight,
                select(1, safeCallNamed("readAnyField", readAnyField, metaBlock, R_WEIGHT_FIELDS)))) or 0
        end
        tier   = tier  or select(1, safeCallNamed("readAnyField", readAnyField, metaBlock, R_TIER_FIELDS))
        image  = image or select(1, safeCallNamed("readAnyField", readAnyField, metaBlock, R_IMAGE_FIELDS))
        itemId = itemId or select(1, safeCallNamed("readAnyField", readAnyField, metaBlock, R_ID_FIELDS))
    end

    if type(itemId) == "number" then itemId = tostring(itemId) end
    if type(name)   == "number" then name   = tostring(name) end

    local resolvedItemId = (type(itemId) == "string" and itemId)
        or (type(key) == "string" and key) or nil
    local preMeta = (resolvedItemId and resolvedItemId:match("^%d+$"))
        and select(1, safeCallNamed("resolveMetaById", resolveMetaById, resolvedItemId)) or nil
    if preMeta and preMeta.name and not (type(name) == "string" and #(select(1, safeCallNamed("trim", trim, name)) or "") > 0) then
        return mergeOwnedItem(preMeta.name, count, weight,
            preMeta.tier, preMeta.imageUrl, preMeta.category, resolvedItemId, fullPath)
    end

    if resolvedItemId and resolvedItemId:match("^%d+$") then
        return addOwnedNumericFallback(resolvedItemId, uuid, weight, fullPath, sampleSlot, value)
    end

    local displayKey = (type(name) == "string" and #(select(1, safeCallNamed("trim", trim, name)) or "") > 0 and name)
        or (type(key) == "string" and key)
        or (type(itemId) == "string" and itemId)

    if not displayKey then
        recordReplionReject(key, fullPath, "parse_error")
        return false
    end

    return mergeOwnedItem(displayKey, count, weight, tier, image, nil,
        (type(itemId) == "string") and itemId or (type(key) == "string" and key) or nil, fullPath)
end

function finalizeReplionParseStats(rawCount, acceptedInstances)
    local fish, rods, items, images, tiers = 0, 0, 0, 0, 0
    for _, key in ipairs(ownedOrder) do
        local e = ownedInventory[key]
        if e then
            if e.category == "rod" or e.category == "bait" then rods = rods + 1
            elseif e.category == "items" then items = items + 1
            else fish = fish + 1 end
            if e.imageUrl then images = images + 1 end
            if e.tier and e.tier ~= "unknown" then tiers = tiers + 1 end
        end
    end
    local uniqueAccepted = #ownedOrder
    local rejectedCount  = math.max(0, (rawCount or 0) - (acceptedInstances or 0))

    replionParseResult.selected          = selectedReplion or "Data"
    replionParseResult.raw               = rawCount or 0
    replionParseResult.accepted          = uniqueAccepted
    replionParseResult.acceptedInstances = acceptedInstances or 0
    replionParseResult.rejected          = rejectedCount
    replionParseResult.fish              = fish
    replionParseResult.rods              = rods
    replionParseResult.items             = items
    replionParseResult.images            = images
    replionParseResult.tiers             = tiers

    return replionParseResult
end

-- Legacy alias used elsewhere in this file.
local addOwned = mergeOwnedItem

local function safeCall(fn, ...)
    return select(1, safeCallNamed("safeCall", fn, ...))
end

-- Safe JSON preview for diagnostic logging (never throws).
local function safeJsonPreview(val, maxLen)
    maxLen = maxLen or 200
    if type(val) == "table" then
        local slim, n = {}, 0
        for k, v in pairs(val) do
            n = n + 1
            if n > 12 then slim["..."] = "truncated"; break end
            local ks = tostring(k)
            if type(v) == "table" then
                slim[ks] = "{table}"
            elseif type(v) == "function" or type(v) == "userdata" or type(v) == "thread" then
                slim[ks] = type(v)
            else
                slim[ks] = v
            end
        end
        if type(HttpService) == "Instance" and type(HttpService.JSONEncode) == "function" then
            local ok, s = pcall(HttpService.JSONEncode, HttpService, slim)
            if ok and type(s) == "string" then return s:sub(1, maxLen) end
        end
        return "{table}"
    end
    return tostring(val):sub(1, maxLen)
end

local RAW_ITEM_SAMPLE_LIMIT = 10
local consumeEntryActiveLogCount = 0

-- Log the first N Inventory.Items entries with key/type/childKeys/id/uuid.
local function logRawInventoryItemSamples(invTable, pathLabel)
    if type(invTable) ~= "table" then return end
    local printed = 0
    for rawKey, rawValue in pairs(invTable) do
        printed = printed + 1
        local valType = type(rawValue)
        local childKeys, kn = {}, 0
        if valType == "table" then
            for ck in pairs(rawValue) do
                kn = kn + 1
                childKeys[#childKeys + 1] = tostring(ck)
                if kn >= 20 then break end
            end
        end
        fishLog("RAW_INVENTORY_ENTRY index=%d rawKey=%s valueType=%s keys=[%s]",
            printed, tostring(rawKey), valType, table.concat(childKeys, ","))
        if valType == "table" then
            local idStr = extractEntryNumericId(rawValue)
            local uuidVal = rawValue.UUID or rawValue.Uuid or rawValue.uuid
            fishLog("RAW_INVENTORY_ENTRY_ID index=%d id=%s uuid=%s",
                printed, tostring(idStr or "?"), tostring(uuidVal or "none"):sub(1, 12))
        end
        fishLog("  jsonPreview=%s", safeJsonPreview(rawValue))
        if printed >= RAW_ITEM_SAMPLE_LIMIT then
            fishLog("RawItemSample logging capped at %d", RAW_ITEM_SAMPLE_LIMIT)
            break
        end
    end
end

-- Print the traceback line that caused a consumeReplionEntry failure.
local function logConsumeEntryError(rawIndex, rawKey, errTrace)
    local trace = tostring(errTrace or "unknown error")
    print(LOG, ("CONSUME_ENTRY_ERROR raw=%d key=%s err=%s"):format(
        rawIndex, tostring(rawKey), trace:match("[^\n]+") or trace))
    for line in trace:gmatch("[^\r\n]+") do
        if line:find("attempt to call a nil value", 1, true)
            or line:find("tracker%.lua", 1, true) then
            print(LOG, ("CONSUME_ENTRY_AT %s"):format(line:match("^%s*(.-)%s*$")))
        end
    end
end

-- ----------------------------------------------------------------
-- PART 1 (BLOCKER 2): Safe Replion inventory SHAPE dump.
-- Prints whether each candidate path exists, its type, key count, a few
-- keys, and a small sample of the first entries (scalar value or first
-- child keys + a handful of safe scalar fields). Output is capped — it
-- never dumps whole tables. Read-only.
-- ----------------------------------------------------------------
local DUMP_SCALAR_FIELDS = {
    "Name","DisplayName","ItemName","FishName","Id","ID","ItemId","ItemID",
    "UUID","Count","Amount","Quantity","Stack","Weight","Rarity","Tier",
    "Type","Category",
}

-- Resolve a dotted path ("Inventory.Items") inside a data table. Returns the
-- value (or nil) and the resolved value type.
local function resolvePath(data, dotted)
    local node = data
    for segment in tostring(dotted):gmatch("[^%.]+") do
        if type(node) ~= "table" then return nil end
        node = node[segment]
        if node == nil then return nil end
    end
    return node
end

local function countKeys(t)
    local n = 0
    for _ in pairs(t) do n = n + 1 end
    return n
end

-- BLOCKER10: generic numeric-id definition indexer + targeted inventory search.
local REPLION_CATALOG_CANDIDATES = {
    "Items", "Item", "ItemData", "ItemDatas", "ItemDefinitions", "ItemDefs", "AllItems",
    "ItemInfo", "InventoryItem",
    "Fish", "FishData", "FishDefinitions", "FishDefs", "Fishes", "AllFish",
    "Rods", "RodData", "RodDefinitions", "RodDefs", "Rod",
    "Baits", "Bait", "BaitData", "Crates", "Crate", "Materials", "Material", "Resources", "Consumables",
    "Equipment", "Tools", "Skins", "Boats", "Packs", "Shop", "Rewards",
    "Catalog", "Catalogue", "Definitions", "Registry", "GameData", "Content",
    "Data.Items", "Data.ItemData", "Data.ItemDefinitions", "Data.Fish", "Data.Rods",
    "Data.Baits", "Data.Crates", "Data.Materials", "Data.Catalog", "Data.Definitions",
    "Shared.Items", "Shared.ItemData", "Shared.Fish", "Shared.Rods", "Shared.Baits",
    "Shared.Crates", "Shared.Materials", "Shared.Equipment", "Shared.Shop",
}

local GENERIC_CATALOG_MAX_DEPTH = 8
local GENERIC_CATALOG_MAX_NODES = 5000
local genericCatalogNodeCount = 0

local function keysPreview(record, limit)
    local keys, n = {}, 0
    if type(record) ~= "table" then return "" end
    for fk in pairs(record) do
        n = n + 1
        keys[#keys + 1] = tostring(fk)
        if n >= (limit or 8) then break end
    end
    return table.concat(keys, ",")
end

local function registerCatalogSearchRoot(tbl, sourcePath)
    if type(tbl) ~= "table" then return end
    catalogSearchRoots[#catalogSearchRoots + 1] = { t = tbl, path = sourcePath }
    itemCatalogSourcesScanned = itemCatalogSourcesScanned + 1
end

local function extractIconFields(record)
    if type(record) ~= "table" then return nil, nil, nil, nil end
    local raw = readMetaField(record, DEF_ICON_FIELDS)
    if raw == nil then return nil, nil, nil, nil end
    local s = tostring(raw)
    return s, s, s, s
end

local function isInventorySnapshotPath(sourcePath)
    local p = (sourcePath or ""):lower()
    return p:find("inventory%.items") ~= nil
        or p:find("inventory%.fish") ~= nil
        or p:find("inventory%.fishes") ~= nil
        or p:find("inventory%.rods") ~= nil
        or p:find("data%.inventory") ~= nil
        or p:find("backpack") ~= nil
        or p:find("owneditems") ~= nil
        or (p:find("playerdata") ~= nil and p:find("inventory") ~= nil)
        or (p:find("profile") ~= nil and p:find("inventory") ~= nil)
        or p:find("inventorynotifications") ~= nil
        or p:find("abilities%.inventory") ~= nil
end

local function looksLikeOwnedInventoryTable(tbl)
    if type(tbl) ~= "table" then return false end
    local total, uuidRows, countRows = 0, 0, 0
    for _, v in pairs(tbl) do
        total = total + 1
        if total > 120 then break end
        if type(v) == "table" then
            if v.UUID or v.Uuid or v.uuid then uuidRows = uuidRows + 1 end
            if v.Count or v.count or v.Amount or v.amount then countRows = countRows + 1 end
            if (v.Id or v.ID or v.ItemId) and (v.Weight or v.weight or v.UUID or v.Uuid) then
                uuidRows = uuidRows + 1
            end
        end
    end
    if total >= 15 and uuidRows >= math.max(3, math.floor(total * 0.35)) then return true end
    if total >= 20 and countRows >= math.max(5, math.floor(total * 0.5)) then return true end
    return false
end

local function indexDefinitionById(idStr, name, category, sourcePath, record)
    idStr = tostring(idStr or "")
    if not idStr:match("^%d+$") then return false end
    name = trim(name or "")
    if #name < 2 or name:match("^%d+$") then return false end
    if isStatLabel(normalizeName(name)) then return false end

    local tierVal = record and readMetaField(record, META_TYPE_FIELDS) or nil
    local tier = tierVal and (TIER_MAP[tostring(tierVal):lower()] or nil) or nil
    local imgVal = record and readMetaField(record, DEF_ICON_FIELDS) or nil
    local imageUrl = resolveImageUrl(imgVal)
    local cat = category or "items"

    addCatalogEntry(name, tier, imageUrl, cat, sourcePath, idStr)

    safeWriteMetadataById(idStr, {
        name = name, key = normalizeName(name), tier = tier, imageUrl = imageUrl,
        category = cat, source = sourcePath, rawKeys = record and keysPreview(record) or nil,
        itemId = idStr,
    })
    if catalogSourceLogCount < CATALOG_LOG_LIMIT then
        catalogSourceLogCount = catalogSourceLogCount + 1
        fishLog("ITEM_CATALOG_HIT id=%s name=%s category=%s source=%s",
            idStr, name, cat, sourcePath)
    end
    return true
end

local function tryIndexRecord(record, sourcePath, idHint)
    if type(record) ~= "table" then return false end
    if isInventorySnapshotPath(sourcePath) then return false end
    local nameVal = readMetaField(record, META_NAME_FIELDS)
    if type(nameVal) ~= "string" or #trim(nameVal) < 2 or nameVal:match("^%d+$") then
        return false
    end
    local idVal = idHint or readMetaField(record, DEF_ID_FIELDS)
    if idVal == nil then return false end
    local idStr = tostring(idVal)
    if not idStr:match("^%d+$") then return false end
    local cat = classifyCatalogCandidate(nameVal, sourcePath) or "items"
    if cat == "items" and (sourcePath:lower():find("fish") or nameVal:lower():find("fish")) then
        cat = "fish"
    end
    local ok = indexDefinitionById(idStr, nameVal, cat, sourcePath, record)
    if ok and catalogSourceLogCount < CATALOG_LOG_LIMIT then
        catalogSourceLogCount = catalogSourceLogCount + 1
        fishLog("ITEM_CATALOG_SAMPLE source=%s id=%s name=%s keys=[%s]",
            sourcePath, idStr, trim(nameVal), keysPreview(record))
    end
    return ok
end

local function walkGenericCatalogIndex(tbl, sourcePath, depth, visited)
    if depth > GENERIC_CATALOG_MAX_DEPTH or type(tbl) ~= "table" then return 0 end
    if isInventorySnapshotPath(sourcePath) then return 0 end
    if depth == 0 and looksLikeOwnedInventoryTable(tbl) then return 0 end
    genericCatalogNodeCount = genericCatalogNodeCount + 1
    if genericCatalogNodeCount > GENERIC_CATALOG_MAX_NODES then return 0 end
    scanBudgetYield("walkGenericCatalogIndex")
    local addr = tostring(tbl)
    if visited[addr] then return 0 end
    visited[addr] = true

    if depth == 0 then registerCatalogSearchRoot(tbl, sourcePath) end

    local added = 0
    -- Record shaped as { Id=10, Name="..." } without numeric key.
    if tryIndexRecord(tbl, sourcePath, nil) then added = added + 1 end

    local preferNested = { Items=true, ItemData=true, ItemDatas=true, ItemDefinitions=true,
        ItemInfo=true, InventoryItem=true, Data=true, Definitions=true,
        Rods=true, Rod=true, Baits=true, Bait=true, Crates=true, Crate=true,
        Materials=true, Material=true, Equipment=true, Tools=true, Resources=true,
        Consumables=true, Packs=true, Shop=true, Rewards=true, Skins=true, Boats=true }

    -- Array-shaped definitions: { { Id=990, Name="..." }, ... }
    for i, v in ipairs(tbl) do
        if type(v) == "table" then
            local innerId = readMetaField(v, DEF_ID_FIELDS)
            if innerId ~= nil then
                if tryIndexRecord(v, sourcePath .. "[" .. tostring(i) .. "]", tostring(innerId)) then
                    added = added + 1
                end
            end
        end
        scanBudgetYield("walkGenericCatalogIndex_array")
    end

    for k, v in pairs(tbl) do
        scanBudgetYield("walkGenericCatalogIndex")
        if type(v) == "table" then
            local idHint = nil
            if type(k) == "number" or (type(k) == "string" and k:match("^%d+$")) then
                idHint = tostring(k)
                if tryIndexRecord(v, sourcePath .. "[" .. idHint .. "]", idHint) then
                    added = added + 1
                end
            else
                local innerId = readMetaField(v, DEF_ID_FIELDS)
                if innerId ~= nil then
                    if tryIndexRecord(v, sourcePath .. "." .. tostring(k), tostring(innerId)) then
                        added = added + 1
                    end
                end
            end
            local kn = tostring(k)
            local nextDepth = depth + 1
            if preferNested[kn] or preferNested[k] then nextDepth = depth end
            if nextDepth <= GENERIC_CATALOG_MAX_DEPTH then
                added = added + walkGenericCatalogIndex(v, sourcePath .. "." .. kn, nextDepth, visited)
            end
        end
    end
    return added
end

local function logItemCatalogSourceFound(sourcePath, cnt)
    if catalogSourceLogCount >= CATALOG_LOG_LIMIT then return end
    catalogSourceLogCount = catalogSourceLogCount + 1
    fishLog("ITEM_CATALOG_SOURCE_FOUND source=%s count=%d", sourcePath, cnt)
end

local function walkIdKeyedCatalogTable(tbl, sourcePath, depth)
    registerCatalogSearchRoot(tbl, sourcePath)
    return walkGenericCatalogIndex(tbl, sourcePath, depth or 0, {})
end

local function buildCatalogFromReplionData(data)
    if not LiveSafe.enableHeavyCatalog and not LiveSafe.enablePhaseBItemUpgrade then return 0 end
    if type(data) ~= "table" or LiveSafe.catalogAborted then return 0 end
    setActiveSection("replion_scan")
    -- Drop stale Replion roots from prior refresh; keep RS/module roots.
    local kept = {}
    for _, r in ipairs(catalogSearchRoots) do
        if r.path:sub(1, 8) ~= "Replion." then kept[#kept + 1] = r end
    end
    catalogSearchRoots = kept
    genericCatalogNodeCount = 0
    local t0 = os.clock()
    local totalAdded = 0
    for _, path in ipairs(REPLION_CATALOG_CANDIDATES) do
        if not isInventorySnapshotPath("Replion." .. path) then
            local val = resolvePath(data, path)
            if type(val) == "table" and not looksLikeOwnedInventoryTable(val) then
                local cnt = countKeys(val)
                if cnt > 0 then
                    logItemCatalogSourceFound("Replion." .. path, cnt)
                    totalAdded = totalAdded + walkIdKeyedCatalogTable(val, "Replion." .. path, 0)
                end
            end
        end
        scanBudgetYield("replion_catalog")
    end
    -- Full Replion data as last-resort search root for targeted id lookup (not walked as catalog).
    registerCatalogSearchRoot(data, "Replion.Data")
    if totalAdded > 0 then
        fishLog("Replion item catalog ingest: +%d id-indexed entries", totalAdded)
    end
    perfEndSection(t0, "replion_catalog_ingest", 50)
    return totalAdded
end

-- Deep search a single id inside any scanned catalog table (inventory-driven).
local function deepSearchCatalogForId(targetId, tbl, sourcePath, depth, visited)
    if depth > GENERIC_CATALOG_MAX_DEPTH or type(tbl) ~= "table" then return false end
    if isInventorySnapshotPath(sourcePath) then return false end
    local addr = tostring(tbl)
    if visited[addr] then return false end
    visited[addr] = true

    targetId = tostring(targetId)
    for k, v in pairs(tbl) do
        if not scanBudgetYield("deepSearchCatalogForId") then return false end
        if type(v) == "table" then
            local keyId = (type(k) == "number" or (type(k) == "string" and k:match("^%d+$")))
                and tostring(k) or nil
            if keyId == targetId and tryIndexRecord(v, sourcePath .. "[" .. keyId .. "]", keyId) then
                return true
            end
            local fieldId = readMetaField(v, DEF_ID_FIELDS)
            if fieldId ~= nil and tostring(fieldId) == targetId then
                if tryIndexRecord(v, sourcePath .. "." .. tostring(k), targetId) then
                    return true
                end
            end
            if deepSearchCatalogForId(targetId, v, sourcePath .. "." .. tostring(k), depth + 1, visited) then
                return true
            end
        end
    end
    return false
end

local function runTargetedSearchForUnresolvedIds(ids)
    if type(ids) ~= "table" or #ids == 0 then return 0 end
    local found = 0
    for _, idStr in ipairs(ids) do
        if not resolveMetaById(idStr) then
            for _, root in ipairs(catalogSearchRoots) do
                if deepSearchCatalogForId(idStr, root.t, root.path, 0, {}) then
                    found = found + 1
                    break
                end
            end
        end
    end
    return found
end

local function collectUnresolvedItemIds()
    local ids, seen = {}, {}
    for _, key in ipairs(ownedOrder) do
        local e = ownedInventory[key]
        if e and e.itemId and tostring(e.itemId):match("^%d+$") then
            local idStr = tostring(e.itemId)
            local placeholder = e.name and e.name:match("^Item #%d+$")
            if (e.resolved == false or placeholder) and not seen[idStr] then
                seen[idStr] = true
                ids[#ids + 1] = idStr
            end
        end
    end
    return ids
end

local function upgradeUnresolvedOwnedNames()
    local upgraded = 0
    for _, key in ipairs(ownedOrder) do
        local e = ownedInventory[key]
        if e and e.itemId then
            local meta = resolveMetaById(tostring(e.itemId))
            if safeUpgradeOwnedEntry(e, meta) then
                upgraded = upgraded + 1
            end
        end
    end
    return upgraded
end

local function logUnresolvedInventoryIds(ids)
    for _, idStr in ipairs(ids) do
        if unresolvedItemLogCount >= CATALOG_LOG_LIMIT then break end
        local meta = resolveMetaById(idStr)
        if not meta then
            unresolvedItemLogCount = unresolvedItemLogCount + 1
            fishLog("UNRESOLVED_ITEM_ID id=%s searchedSources=%d reason=no_catalog_hit",
                idStr, itemCatalogSourcesScanned)
        end
    end
end

local function postParseItemCatalogPass()
    local unresolved = collectUnresolvedItemIds()
    if #unresolved == 0 then return 0 end
    runTargetedSearchForUnresolvedIds(unresolved)
    local upgraded = upgradeUnresolvedOwnedNames()
    logUnresolvedInventoryIds(unresolved)
    if upgraded > 0 then
        fishLog("Item name upgrade pass: resolved %d placeholder entries", upgraded)
    end
    return upgraded
end

local function traceUnresolvedId(idStr)
    local diag = {
        id = idStr,
        count = 0,
        category = "items",
        sourcePath = nil,
        checkedDefinitionPaths = {},
        foundCandidate = false,
        candidatePath = nil,
        candidateKeys = {},
        inventoryShape = nil,
    }
    for _, key in ipairs(ownedOrder) do
        local e = ownedInventory[key]
        if e and tostring(e.itemId) == idStr then
            diag.count = diag.count + (e.count or 1)
            diag.category = e.category or "items"
            diag.sourcePath = e.source
        end
    end
    local meta = resolveMetaById(idStr)
    if meta and meta.name and not isPlaceholderName(meta.name, idStr) then
        diag.foundCandidate = true
        diag.candidatePath = meta.source
        diag.candidateKeys = { meta.name }
        return diag
    end
    local paths = {}
    for _, root in ipairs(catalogSearchRoots) do
        if #paths < 10 then paths[#paths + 1] = root.path end
        if deepSearchCatalogForId(idStr, root.t, root.path, 0, {}) then
            diag.foundCandidate = true
            diag.candidatePath = root.path
            local hit = resolveMetaById(idStr)
            if hit and hit.name then diag.candidateKeys = { hit.name } end
            break
        end
        scanBudgetYield("unresolved_trace")
    end
    diag.checkedDefinitionPaths = paths
    local nameFields = {}
    for _, key in ipairs(ownedOrder) do
        local e = ownedInventory[key]
        if e and tostring(e.itemId) == idStr and lastReplionDataCache then break end
    end
    fishLog("UNRESOLVED_ID_TRACE id=%s checkedPaths=%d found=%s category=%s",
        idStr, #paths, tostring(diag.foundCandidate), tostring(diag.category))
    return diag
end

local function traceTargetUnresolvedIds()
    if not LiveSafe.enablePhaseBItemUpgrade then return end
    LiveSafe.unresolvedDiagnostics = {}
    for _, idStr in ipairs(LiveSafe.targetItemIds) do
        local owned = false
        for _, key in ipairs(ownedOrder) do
            local e = ownedInventory[key]
            if e and tostring(e.itemId) == idStr and isPlaceholderName(e.name, idStr) then
                owned = true
                break
            end
        end
        if owned then
            LiveSafe.unresolvedDiagnostics[#LiveSafe.unresolvedDiagnostics + 1] = traceUnresolvedId(idStr)
        end
    end
end

-- BLOCKER11: Phase B runs only after catalog + inventory are both ready.
local function runInventoryPhaseB(reason)
    if not LiveSafe.enablePhaseBItemUpgrade then return end
    if not isCurrentRun() then return end
    if LiveSafe.catalogAborted and reason ~= "post_initial_parse" then return end
    setActiveSection("phase_b_upgrade")
    LiveSafe.phaseB.upgradedTotal = 0
    local stillUnresolved = countPlaceholderItems()
    while stillUnresolved > 0 and LiveSafe.phaseB.pass < 25 do
        if os.clock() < LiveSafe.catalogPausedUntil then
            fishLog("INVENTORY_PHASE_B paused reason=frame_stall")
            task.wait(10)
        end
        if not scanBudgetYield("phase_b_upgrade") then break end
        LiveSafe.phaseB.pass = LiveSafe.phaseB.pass + 1
        local unresolved = collectUnresolvedItemIds()
        if #unresolved == 0 then break end
        local batch = {}
        for i = 1, math.min(4, #unresolved) do batch[i] = unresolved[i] end
        runTargetedSearchForUnresolvedIds(batch)
        pcall(traceTargetUnresolvedIds)
        local upgraded = 0
        pcall(function() upgraded = upgradeUnresolvedOwnedNames() or 0 end)
        LiveSafe.phaseB.upgradedTotal = LiveSafe.phaseB.upgradedTotal + upgraded
        stillUnresolved = countPlaceholderItems()
        fishLog("INVENTORY_PHASE_B pass=%d checked=%d upgraded=%d stillUnresolved=%d reason=%s",
            LiveSafe.phaseB.pass, #batch, upgraded, stillUnresolved, tostring(reason or "background"))
        if upgraded == 0 and LiveSafe.phaseB.pass >= 3 then break end
        task.wait(0.35)
    end
    fishLog("INVENTORY_PHASE_B complete upgradedTotal=%d stillUnresolved=%d",
        LiveSafe.phaseB.upgradedTotal, stillUnresolved)
    printMetadataCatalogSummary(stillUnresolved)
    if LiveSafe.phaseB.upgradedTotal > 0 then
        lastReplionStr = ""
        task.wait()
        setActiveSection("upload")
        pcall(syncCompactIdCatalogToBackend)
        pcall(syncToDashboard)
    end
end

tryFinalizeCatalogAndUpgrade = function(reason)
    if not LiveSafe.enablePhaseBItemUpgrade then return end
    if not LiveSafe.enableHeavyCatalog then return end
    if not isCurrentRun() or LiveSafe.phaseB.finalizeInProgress then return end
    if not LiveSafe.catalogBackgroundComplete then return end
    if replionFound and activeReplion and #ownedOrder == 0 then return end
    LiveSafe.phaseB.finalizeInProgress = true
    task.spawn(function()
        if not isCurrentRun() then LiveSafe.phaseB.finalizeInProgress = false return end
        if type(lastReplionDataCache) == "table" then
            pcall(buildCatalogFromReplionData, lastReplionDataCache)
        end
        if activeReplionClient then
            pcall(ingestCatalogFromReplionClient, activeReplionClient)
        end
        runInventoryPhaseB(reason or "finalize")
        LiveSafe.phaseB.finalizeInProgress = false
    end)
end

local function debugDumpReplionInventoryShape(data)
    if not DEBUG_REPLION_INVENTORY_DUMP then return end
    if type(data) ~= "table" then return end
    local paths = {
        "Inventory", "Inventory.Items", "Inventory.Fish", "Inventory.Fishes",
        "Inventory.Rods", "Inventory.Equipment",
        "InventoryNotifications", "InventoryNotifications.Items",
        "InventoryNotifications.Fish", "Abilities.Inventory",
        "Data.Inventory", "Data.Inventory.Items",
    }
    for _, path in ipairs(paths) do
        local val = resolvePath(data, path)
        if val == nil then
            print(LOG, ("Replion path %s : absent"):format(path))
        elseif type(val) ~= "table" then
            print(LOG, ("Replion path %s exists %s value=%s"):format(
                path, type(val), tostring(val):sub(1, 60)))
        else
            local keys = countKeys(val)
            -- First 10 keys.
            local firstKeys, kn = {}, 0
            for k in pairs(val) do
                kn = kn + 1
                firstKeys[#firstKeys + 1] = tostring(k)
                if kn >= 10 then break end
            end
            print(LOG, ("Replion path %s exists table keys=%d first=[%s]"):format(
                path, keys, table.concat(firstKeys, ",")))
            -- Sample first 3 entries.
            local sampled = 0
            for k, v in pairs(val) do
                sampled = sampled + 1
                if type(v) ~= "table" then
                    print(LOG, ("  Sample %s[%s]: valueType=%s value=%s"):format(
                        path, tostring(k), type(v), tostring(v):sub(1, 40)))
                else
                    local childKeys, cn = {}, 0
                    for ck in pairs(v) do
                        cn = cn + 1
                        childKeys[#childKeys + 1] = tostring(ck)
                        if cn >= 20 then break end
                    end
                    print(LOG, ("  Sample %s[%s]: valueType=table childKeys=[%s]"):format(
                        path, tostring(k), table.concat(childKeys, ",")))
                    -- Safe scalar fields (incl. one nested Metadata level).
                    local parts = {}
                    for _, f in ipairs(DUMP_SCALAR_FIELDS) do
                        local fv = v[f]
                        if fv ~= nil and type(fv) ~= "table" then
                            parts[#parts + 1] = ("%s=%s"):format(f, tostring(fv):sub(1, 30))
                        end
                    end
                    local meta = v.Metadata or v.metadata or v.Meta or v.meta
                    if type(meta) == "table" then
                        for _, f in ipairs(DUMP_SCALAR_FIELDS) do
                            local fv = meta[f]
                            if fv ~= nil and type(fv) ~= "table" then
                                parts[#parts + 1] = ("Metadata.%s=%s"):format(f, tostring(fv):sub(1, 30))
                            end
                        end
                    end
                    if #parts > 0 then
                        print(LOG, "    fields: " .. table.concat(parts, " "))
                    end
                end
                if sampled >= 3 then break end
            end
        end
    end
end

-- ----------------------------------------------------------------
-- PART 2 (BLOCKER 2): Score a candidate inventory table by how "owned
-- inventory"-like its entries are. Higher = more likely the real owned
-- list. Notification/ability tables score low; count/id records score high.
-- ----------------------------------------------------------------
local function scoreInventoryTable(t, pathName)
    if type(t) ~= "table" then return -1, "not a table" end
    local entries, withCount, withId, withName, numberVals = 0, 0, 0, 0, 0
    for _, v in pairs(t) do
        entries = entries + 1
        if entries > 200 then break end
        if type(v) == "number" then
            numberVals = numberVals + 1
        elseif type(v) == "table" then
            if readAnyField(v, R_COUNT_FIELDS) ~= nil then withCount = withCount + 1 end
            if readAnyField(v, R_ID_FIELDS)    ~= nil then withId    = withId + 1 end
            if readAnyField(v, R_NAME_FIELDS)  ~= nil then withName  = withName + 1 end
        end
    end
    if entries == 0 then return 0, "empty" end
    local score, reasons = 0, {}
    local pl = pathName:lower()
    if pl:find("inventory") then score = score + 3; reasons[#reasons+1] = "path~inventory" end
    if pl:find("notification") then score = score - 4; reasons[#reasons+1] = "path~notification" end
    if pl:find("abilit") then score = score - 3; reasons[#reasons+1] = "path~abilities" end
    if withCount > 0 then score = score + 4; reasons[#reasons+1] = "count records" end
    if withId    > 0 then score = score + 2; reasons[#reasons+1] = "id records" end
    if withName  > 0 then score = score + 1; reasons[#reasons+1] = "name records" end
    if numberVals > 0 then score = score + 3; reasons[#reasons+1] = "count map" end
    score = score + math.min(entries, 10) * 0.1
    return score, table.concat(reasons, "+")
end

-- consumeReplionEntry is forward-declared and defined above (forward-declaration block).

-- ----------------------------------------------------------------
-- PART 3/5: Parse owned inventory out of the Replion data table.
-- Picks the best-scoring inventory path, then parses every supported shape.
-- Returns the accepted item count; records details in replionParseResult.
-- ----------------------------------------------------------------
local function parseInventoryFromReplionData(data)
    -- Reset the source-of-truth maps; Replion snapshot fully replaces them.
    ownedInventory, ownedOrder, replionRejected = {}, {}, {}
    rejectLogCount = 0
    consumeEntryActiveLogCount = 0
    catalogLookupLogCount = 0
    metadataDecodeFailedIds = {}
    replionParseResult = {
        selected = "?", path = "none",
        raw = 0, accepted = 0, acceptedInstances = 0, rejected = 0,
        fish = 0, rods = 0, items = 0,
        images = 0, tiers = 0, pathExists = false,
    }
    if type(data) ~= "table" then return 0 end

    -- BLOCKER10J: cached-path read only — no path scoring, no dumps.
    if LiveSafe.playerDataOnly and (LiveSafe.oneShot or LiveSafe.lightSyncEnabled) then
        local path = LiveSafe.cachedInventoryPath or "Inventory.Items"
        local itemsTable = resolvePath(data, path)
        if type(itemsTable) ~= "table" and type(data.Inventory) == "table" then
            itemsTable = data.Inventory.Items
            path = "Inventory.Items"
        end
        if type(itemsTable) ~= "table" then
            replionParseResult.phase = "inventory_path_missing"
            return 0
        end
        replionParseResult.pathExists = true
        replionParseResult.path = path
        local raw, acceptedInstances = 0, 0
        for k, v in pairs(itemsTable) do
            raw = raw + 1
            if type(v) == "table" then
                local numId = v.Id or v.ID or v.ItemId or v.ItemID or v.itemId
                if numId ~= nil then
                    local w = 0
                    local mb = v.Metadata or v.metadata
                    if type(mb) == "table" then
                        w = toNumberOr(parseWeight(mb.Weight or mb.weight or mb.MaxWeight), 0)
                    end
                    if addOwnedNumericFallback(tostring(numId), v.UUID or v.Uuid or v.uuid,
                        w, path .. "." .. tostring(k), nil, v) then
                        acceptedInstances = acceptedInstances + 1
                    end
                end
            end
        end
        finalizeReplionParseStats(raw, acceptedInstances)
        if LiveSafe.verbose then
            fishLogDebug("Selected owned inventory path: %s (cached)", path)
        end
        return replionParseResult.accepted
    end

    -- PART 2: candidate paths in PREFERENCE order. Each is scored; the best
    -- non-notification owned-inventory table wins.
    local candidatePaths = {
        "Inventory.Items", "Inventory.Fish", "Inventory.Fishes",
        "Inventory.Rods", "Inventory.Equipment", "Inventory",
        "Data.Inventory.Items", "Data.Inventory",
        "Fish", "Fishes", "Items", "Rods", "Backpack", "Collection", "Owned",
        "InventoryNotifications.Items", "InventoryNotifications.Fish",
        "Abilities.Inventory",
    }

    -- Collect every existing path with its score.
    local scored = {}
    for _, p in ipairs(candidatePaths) do
        local val = resolvePath(data, p)
        if type(val) == "table" then
            local s, reason = scoreInventoryTable(val, p)
            scored[#scored + 1] = { path = p, t = val, score = s, reason = reason }
        end
    end

    if #scored == 0 then
        replionParseResult.phase = "inventory_path_missing"
        print(LOG, "No inventory path present in Replion data.")
        print(LOG, ("REPLION_PARSE_RESULT selected=%s path=none raw=0 accepted=0 fish=0 rods=0 items=0 rejected=0 images=0 tiers=0 phase=inventory_path_missing"):format(
            selectedReplion or "?"))
        return 0
    end

    table.sort(scored, function(a, b) return a.score > b.score end)
    replionParseResult.pathExists = true

    -- Parse roots: the best path, plus any sibling category tables under
    -- Inventory (Fish/Rods/Items) so multi-category inventories are complete.
    local chosen = scored[1]
    print(LOG, ("Selected owned inventory path: %s reason=%s score=%.1f"):format(
        chosen.path, chosen.reason, chosen.score))
    replionParseResult.path = chosen.path

    -- Build the set of roots to parse: the winner + Inventory category siblings.
    local roots, seenT = {}, {}
    local function pushRoot(t, path)
        if type(t) == "table" and not seenT[tostring(t)] then
            seenT[tostring(t)] = true
            roots[#roots + 1] = { t = t, path = path }
        end
    end
    pushRoot(chosen.t, chosen.path)
    if type(data.Inventory) == "table" then
        pushRoot(data.Inventory.Items,     "Inventory.Items")
        pushRoot(data.Inventory.Fish,      "Inventory.Fish")
        pushRoot(data.Inventory.Fishes,    "Inventory.Fishes")
        pushRoot(data.Inventory.Rods,      "Inventory.Rods")
        pushRoot(data.Inventory.Equipment, "Inventory.Equipment")
    end

    local itemsTable = resolvePath(data, "Inventory.Items")
    if type(itemsTable) == "table" then
        logRawInventoryItemSamples(itemsTable, "Inventory.Items")
    end

    -- Show up to DEBUG_RAW_ENTRY_LIMIT raw entries from the chosen path as
    -- proof before the full parse starts. No longer hunts for id=70 across
    -- all 3116 entries (that iteration was the cause of silent aborts before
    -- the REPLION_PARSE_RESULT print when the loop took too long or threw).
    local rawEntryPrinted = 0
    for k, v in pairs(chosen.t) do
        if type(v) == "table" then
            rawEntryPrinted = rawEntryPrinted + 1
            local numId = v.Id or v.ID or v.ItemId or v.ItemID or v.FishId
            local fields = {}
            for fk, fv in pairs(v) do
                if type(fv) ~= "table" then
                    fields[#fields + 1] = ("%s=%s"):format(tostring(fk), tostring(fv):sub(1, 30))
                end
                if #fields >= 8 then break end
            end
            print(LOG, ("Raw owned entry [%s] Id=%s: %s"):format(
                tostring(k), tostring(numId), table.concat(fields, " ")))
            if rawEntryPrinted >= DEBUG_RAW_ENTRY_LIMIT then
                print(LOG, ("Raw entry logging capped at %d of %d"):format(
                    DEBUG_RAW_ENTRY_LIMIT, countKeys(chosen.t)))
                break
            end
        end
    end

    -- PART 1 (BLOCKER 3): Catalog diagnostic — always include ids 70 and 119 plus
    -- the first few numeric ids actually found in the chosen table.
    local diagSeenId = {["70"]=true, ["119"]=true}
    local diagSampleIds = {"70", "119"}
    for _, v in pairs(chosen.t) do
        if type(v) == "table" then
            local numId = v.Id or v.ID or v.ItemId or v.ItemID or v.FishId
            if numId ~= nil then
                local s = tostring(numId)
                if not diagSeenId[s] then diagSeenId[s] = true; diagSampleIds[#diagSampleIds + 1] = s end
            end
        end
        if #diagSampleIds >= DEBUG_LOOKUP_LIMIT then break end
    end
    pcall(debugCatalogLookupForOwnedIds, diagSampleIds)

    local raw = 0
    local acceptedInstances = 0
    local rawErrCount = 0
    local tracebackFn = (type(debug) == "table" and type(debug.traceback) == "function")
        and debug.traceback or function(err) return tostring(err) end
    for _, root in ipairs(roots) do
        for k, v in pairs(root.t) do
            if type(k) == "string" and safeCall(isStatLabel, safeCall(normalizeName, k) or "") and type(v) ~= "table" then
                -- ignore stat scalar
            else
                raw = raw + 1
                local slot = raw <= DEBUG_SAMPLE_LIMIT and raw or nil
                local entryPath = "Replion." .. root.path .. "." .. tostring(k)
                local okEntry, entryResult = xpcall(function()
                    return consumeReplionEntry(k, v, entryPath, slot)
                end, tracebackFn)
                if okEntry then
                    if entryResult == true then
                        acceptedInstances = acceptedInstances + 1
                    end
                else
                    rawErrCount = rawErrCount + 1
                    logConsumeEntryError(raw, k, entryResult)
                    local idStr = extractEntryNumericId(v)
                    if idStr then
                        local uuidVal = type(v) == "table" and (v.UUID or v.Uuid or v.uuid) or nil
                        if addOwnedNumericFallback(idStr, uuidVal, 0, entryPath, nil, v) then
                            acceptedInstances = acceptedInstances + 1
                        else
                            recordReplionReject(idStr, "Replion." .. root.path, "parse_error")
                        end
                    else
                        recordReplionReject(k, "Replion." .. root.path, "parse_error")
                    end
                end
            end
        end
    end
    if rawErrCount > 0 then
        warn(LOG, ("Total consumeReplionEntry errors this parse: %d"):format(rawErrCount))
    end

    -- BLOCKER10C: defer targeted catalog upgrade to Phase B (non-blocking startup).
    local ps = finalizeReplionParseStats(raw, acceptedInstances)

    local parsePhase = (ps.acceptedInstances > 0) and "live"
        or (ps.pathExists and ps.raw > 0) and "inventory_parse_failed"
        or (ps.pathExists) and "inventory_empty"
        or "no_path"
    ps.phase = parsePhase
    print(LOG, ("REPLION_PARSE_RESULT selected=%s path=%s raw=%d accepted=%d acceptedInstances=%d fish=%d rods=%d items=%d rejected=%d images=%d tiers=%d phase=%s"):format(
        ps.selected, ps.path, ps.raw, ps.accepted, ps.acceptedInstances,
        ps.fish, ps.rods, ps.items, ps.rejected, ps.images, ps.tiers, parsePhase))

    return ps.accepted
end

local lastReplionStr = ""  -- delta-check cache for Replion snapshots

-- ================================================================
-- SESSION INVENTORY (event-based, SECONDARY to Replion).
-- Kept only as a secondary catch-event log. Never the public source.
-- ================================================================
local sessionInventory = {}  -- [normalizedKey] = { name, amount, weight, category, tier, imageUrl, source }
local caughtOrder      = {}  -- ordered list of normalized keys

local function classifyItem(itemName)
    local n = string.lower(itemName)
    if string.find(n, "rod")     then return "rod"
    elseif string.find(n, "bait")  then return "bait"
    elseif string.find(n, "stone")
        or string.find(n, "enchant")
        or string.find(n, "crate") then return "items"
    else return "fish"
    end
end

-- Unified merge with catalog validation and stat-label rejection.
-- explicitImageUrl (optional) — a fish image already resolved by the caller
-- (e.g. PlayerGui card). Catalog image still takes priority when present.
local function mergeItem(rawName, amount, weight, explicitImageUrl, source, category)
    rawName = trim(rawName)
    if rawName == "" then return end
    amount  = math.max(1, math.floor(toNumberOr(amount, 1)))
    weight  = toNumberOr(weight, 0)

    local normalized = normalizeName(rawName)

    if isStatLabel(normalized) then
        rejectInventoryLabel(rawName, amount, "stat_label_denylist", source)
        return
    end

    local meta        = resolveFishMeta(normalized)
    local resolvedCat = category or (meta and meta.category) or classifyItem(rawName)
    local tier        = meta and meta.tier     or nil
    local imageUrl    = (meta and meta.imageUrl) or explicitImageUrl or nil
    local displayName = (meta and meta.name)   or rawName

    if not sessionInventory[normalized] then
        sessionInventory[normalized] = {
            name     = displayName,
            amount   = 0,
            weight   = 0,
            category = resolvedCat,
            tier     = tier,
            imageUrl = imageUrl,
            source   = source or "unknown",
        }
        caughtOrder[#caughtOrder + 1] = normalized
    end

    local e = sessionInventory[normalized]
    e.amount = safeAdd(e.amount, amount)
    e.weight = safeAdd(e.weight, weight)
    if tier     and not e.tier     then e.tier     = tier     end
    if imageUrl and not e.imageUrl then e.imageUrl = imageUrl end

    if DEBUG_VERBOSE_INVENTORY then
        print(LOG, ("  Accept '%s' x%d tier=%s src=%s"):format(
            displayName, amount, tier or "?", source or "?"))
    end
end

-- ----------------------------------------------------------------
-- Helpers
-- ----------------------------------------------------------------
local function packetBelongsToLocalPlayer(playerArg)
    local localName = string.lower(LocalPlayer.Name)
    if type(playerArg) == "string" then
        return string.lower(trim(playerArg)) == localName
    end
    if typeof(playerArg) == "Instance" and playerArg:IsA("Player") then
        return playerArg == LocalPlayer or string.lower(playerArg.Name) == localName
    end
    return false
end

-- ----------------------------------------------------------------
-- PART 9: Dashboard sync.
-- The public inventory comes from ownedInventory (Replion source of
-- truth) and REPLACES the previous snapshot on the backend — counts
-- are never appended, so refreshing never double-counts.
-- (replionFound / selectedReplion / inventorySource / trackerPhase are
--  declared near the Replion subsystem so discovery can report phases.)
-- ----------------------------------------------------------------
local function buildOwnedGroups()
    local fish, rods, items = {}, {}, {}
    local flat = {}  -- single combined list for the legacy `items` field
    for _, key in ipairs(ownedOrder) do
        local d = ownedInventory[key]
        local entry = {
            name     = d.name,
            count    = d.count,
            amount   = d.count,
            weight   = d.weight,
            maxWeight= d.weight,
            tier     = d.tier,
            rarity   = d.tier,
            imageUrl = d.imageUrl,
            category = d.category,
            itemId   = d.itemId,
            source   = d.source,
            resolved = d.resolved,
            catalogSource = d.catalogSource,
            catalogReason = d.catalogReason,
            icon      = d.icon,
            assetId   = d.assetId,
            thumbnail = d.thumbnail,
        }
        flat[#flat + 1] = entry
        if d.category == "rod" or d.category == "bait" then
            rods[#rods + 1] = entry
        elseif d.category == "items" then
            items[#items + 1] = entry
        else
            fish[#fish + 1] = entry
        end
    end
    return { fish = fish, rods = rods, items = items }, flat
end

-- Trim optional fields when the inventory_snapshot JSON exceeds 128 KB.
local PAYLOAD_SOFT_LIMIT = 128 * 1024

local function compactSnapshotItems(flat)
    local out = {}
    for _, d in ipairs(flat) do
        out[#out + 1] = {
            name      = d.name,
            count     = d.count,
            amount    = d.amount,
            weight    = d.weight,
            maxWeight = d.maxWeight,
            tier      = d.tier,
            rarity    = d.rarity,
            imageUrl  = d.imageUrl,
            category  = d.category,
            itemId    = d.itemId,
            resolved  = d.resolved,
            catalogSource = d.catalogSource,
            catalogReason = d.catalogReason,
            icon      = d.icon,
            assetId   = d.assetId,
            thumbnail = d.thumbnail,
        }
    end
    return out
end

-- Read bounded player stats (coins / caught / rarest fish / ruin / artifact).
-- Does NOT collect quest progress or leaderboard UI scrape data.
local function shallowPickStatValue(data, keys)
    if type(data) ~= "table" then return nil end
    for _, k in ipairs(keys) do
        local v = data[k]
        if v ~= nil and (type(v) == "number" or type(v) == "string") then
            return v
        end
    end
    for _, sub in pairs(data) do
        if type(sub) == "table" then
            for _, k in ipairs(keys) do
                local v = sub[k]
                if v ~= nil and (type(v) == "number" or type(v) == "string") then
                    return v
                end
            end
        end
    end
    return nil
end

local function normaliseProgressPair(raw)
    if raw == nil then return nil end
    if type(raw) == "string" then
        local cur, max = raw:match("^(%d+)%s*/%s*(%d+)$")
        if cur and max then return { current = tonumber(cur), max = tonumber(max) } end
        return nil
    end
    if type(raw) ~= "table" then return nil end
    local current = tonumber(raw.current or raw.progress or raw.done or raw.value)
    local maxv = tonumber(raw.max or raw.total or raw.goal or raw.target)
    if not current or not maxv or maxv <= 0 then return nil end
    return { current = math.floor(current), max = math.floor(maxv) }
end

local function readLeaderstatValue(names)
    local folder = LocalPlayer:FindFirstChild("leaderstats")
    if not folder then return nil end
    for _, name in ipairs(names) do
        local child = folder:FindFirstChild(name)
        if child and child:IsA("ValueBase") then
            return child.Value
        end
    end
    return nil
end

local function extractPlayerStats()
    local stats = {}
    local data = lastReplionDataCache

    local coins = shallowPickStatValue(data, {"Coins","Cash","coins","cash","Gold","Money"})
    if coins == nil then coins = readLeaderstatValue({"Coins","Cash","Gold","Money"}) end
    if coins ~= nil then
        local n = tonumber(coins)
        stats.coins = n or coins
    end

    local caught = shallowPickStatValue(data, {
        "TotalCaught","TotalCaughtFish","Caught","FishCaught","totalCaught","caught","TotalFish",
    })
    if caught == nil then caught = readLeaderstatValue({"Caught","Total Caught","Fish Caught","TotalCaught"}) end
    if caught ~= nil then
        local n = tonumber(caught)
        stats.totalCaught = n or caught
    end

    local rarest = shallowPickStatValue(data, {
        "RarestFishChance","RarestFish","rarestFishChance","rarestFish","BestCatch","LuckiestCatch",
    })
    if rarest == nil then rarest = readLeaderstatValue({"Rarest Fish","RarestFish","Luck"}) end
    if rarest ~= nil then stats.rarestFishChance = tostring(rarest) end

    local ruin = shallowPickStatValue(data, {"Ruin","RuinProgress","ruin"})
    local ruinPair = normaliseProgressPair(ruin)
    if ruinPair then stats.ruin = ruinPair end

    local artifact = shallowPickStatValue(data, {"Artifact","ArtifactProgress","artifact"})
    local artifactPair = normaliseProgressPair(artifact)
    if artifactPair then stats.artifact = artifactPair end

    if next(stats) then
        stats.statsAt = os.date("!%Y-%m-%dT%H:%M:%SZ")
        return stats
    end
    return nil
end

-- Send the Replion inventory_snapshot (and a compatible update-backpack body).
local function syncToDashboard()
    if LiveSafe.oneShot then stepBegin("payload_build") end
    local owned, flat = buildOwnedGroups()
    flat = compactSnapshotItems(flat)

    local parseStatsBlock = {
        raw               = replionParseResult.raw,
        accepted          = replionParseResult.accepted,
        acceptedInstances = replionParseResult.acceptedInstances,
        rejected          = replionParseResult.rejected,
        images            = replionParseResult.images,
        tiers             = replionParseResult.tiers,
        selectedPath      = replionParseResult.path,
        fish              = replionParseResult.fish,
        rods              = replionParseResult.rods,
        items             = replionParseResult.items,
        firstRejected     = buildFirstRejectedSample(10),
    }

    if LiveSafe.verbose then
        print(LOG, ("DASHBOARD_SEND inventory_snapshot user=%s flatItems=%d raw=%d accepted=%d selectedPath=%s"):format(
            LocalPlayer.Name, #flat, replionParseResult.raw, replionParseResult.accepted,
            replionParseResult.path or "?"))
    end

    local payload = {
        type      = "inventory_snapshot",
        username  = LocalPlayer.Name,
        userId    = LocalPlayer.UserId,
        source    = inventorySource,
        isOnline  = true,
        phase     = "live",
        trackerBuild = TRACKER_BUILD,
        scannedAt = os.time(),
        timestamp = os.time(),
        items     = flat,
        parseStats= parseStatsBlock,
    }
    local playerStats = extractPlayerStats()
    if playerStats then payload.playerStats = playerStats end
    if #LiveSafe.unresolvedDiagnostics > 0 then
        payload.unresolvedDiagnostics = LiveSafe.unresolvedDiagnostics
    end

    local encoded = HttpService:JSONEncode(payload)
    if #encoded > PAYLOAD_SOFT_LIMIT then
        for _, it in ipairs(flat) do
            if it.imageUrl and #tostring(it.imageUrl) > 80 then
                it.imageUrl = nil
            end
        end
        payload.parseStats.firstRejected = nil
        encoded = HttpService:JSONEncode(payload)
    end
    if #encoded > PAYLOAD_SOFT_LIMIT and #flat > 200 then
        local trimmed = {}
        for i = 1, math.min(#flat, 200) do trimmed[i] = flat[i] end
        payload.items = trimmed
        encoded = HttpService:JSONEncode(payload)
    end

    if encoded == lastSentStr and LiveSafe.currentSyncReason ~= "light_sync" then
        if LiveSafe.oneShot then stepEnd("payload_build") end
        return true
    end
    lastSentStr = encoded
    if LiveSafe.oneShot then stepEnd("payload_build") end
    if LiveSafe.verbose then
        fishLogDebug("DASHBOARD_SEND inventory_snapshot bytes=%d raw=%d accepted=%d",
            #encoded, replionParseResult.raw, replionParseResult.accepted)
    end

    local function uploadOkFromResult(ok, result)
        if not ok then return false end
        if type(result) ~= "table" then return false end
        local code = result.StatusCode
        return tostring(code) == "200"
    end

    local function doUpload()
        if LiveSafe.oneShot then stepBegin("upload") end
        setActiveSection("upload")
        local ok, result = pcall(sendDashboardRequest, "inventory_snapshot", {
            Url     = TRACKER_URL,
            Method  = "POST",
            Headers = { ["Content-Type"] = "application/json" },
            Body    = encoded,
        })
        if LiveSafe.oneShot then stepEnd("upload") end
        return uploadOkFromResult(ok, result)
    end

    local syncReason = LiveSafe.currentSyncReason or ""
    if LiveSafe.oneShot or syncReason == "light_sync" or syncReason == "initial" then
        return doUpload()
    end

    task.spawn(function()
        if not isCurrentRun() then return end
        setActiveSection("upload")
        task.wait()
        local ok, result = pcall(sendDashboardRequest, "inventory_snapshot", {
            Url     = TRACKER_URL,
            Method  = "POST",
            Headers = { ["Content-Type"] = "application/json" },
            Body    = encoded,
        })
        task.wait()
        -- PART 1: DASHBOARD_RESPONSE — always show HTTP status so failures are
        -- not silent. Previously this only printed on pcall error (Lua crash),
        -- not on HTTP 4xx/5xx — meaning a backend 413/400 was invisible.
        if ok then
            local code   = type(result) == "table" and result.StatusCode or "?"
            local body   = (type(result) == "table" and type(result.Body) == "string")
                           and result.Body:sub(1, 200) or ""
            local ok200  = (tostring(code) == "200")
            print(LOG, ("DASHBOARD_RESPONSE inventory_snapshot success=%s status=%s bodyPreview=%s"):format(
                tostring(ok200), tostring(code), body))
            if not ok200 then
                warn(LOG, "Inventory snapshot HTTP error — backend returned:", tostring(code), body)
            end
        else
            warn(LOG, "DASHBOARD_RESPONSE inventory_snapshot HTTP send error:", tostring(result))
        end
    end)
    return true
end

-- Send a lightweight tracker_status payload (Replion found/missing, online,
-- and the current discovery phase). Assigns to the forward-declared upvalue
-- so discovery code higher in the file can call it.
-- Phases: startup | replion_client_found | player_data_selected |
--         player_data_not_found | inventory_path_missing | replion_missing
function syncStatus(online, phase, extra)
    if LiveSafe.oneShot then return end
    if phase then trackerPhase = phase end
    local payload = {
        type            = "tracker_status",
        username        = LocalPlayer.Name,
        userId          = LocalPlayer.UserId,
        source          = inventorySource,
        replionFound    = replionFound,
        selectedReplion = selectedReplion,
        phase           = trackerPhase,
        trackerBuild    = TRACKER_BUILD,
        online          = online ~= false,
        isOnline        = online ~= false,
        updatedAt       = os.time(),
    }
    if type(extra) == "table" then
        for k, v in pairs(extra) do payload[k] = v end
    end
    local playerStats = extractPlayerStats()
    if playerStats then payload.playerStats = playerStats end
    if type(payload.parseStats) == "table" and (payload.parseStats.acceptedInstances or 0) > 0 then
        payload.phase = "live"
        trackerPhase = "live"
    end
    local ok, encoded = pcall(function() return HttpService:JSONEncode(payload) end)
    if not ok then return end
    print(LOG, ("DASHBOARD_SEND tracker_status user=%s userId=%d phase=%s online=%s build=%s"):format(
        LocalPlayer.Name, LocalPlayer.UserId, tostring(trackerPhase), tostring(payload.isOnline), TRACKER_BUILD))
    task.spawn(function()
        if not isCurrentRun() then return end
        local okR, resultR = pcall(sendDashboardRequest, "tracker_status", {
            Url     = TRACKER_URL,
            Method  = "POST",
            Headers = { ["Content-Type"] = "application/json" },
            Body    = encoded,
        })
        local codeR = (okR and type(resultR) == "table" and resultR.StatusCode) or "?"
        local bodyR = (okR and type(resultR) == "table" and type(resultR.Body) == "string")
                      and resultR.Body:sub(1, 80) or ""
        local ok200R = (tostring(codeR) == "200")
        print(LOG, ("DASHBOARD_RESPONSE tracker_status success=%s status=%s phase=%s"):format(
            tostring(okR and ok200R), tostring(codeR), tostring(trackerPhase)))
        if okR and not ok200R then
            warn(LOG, "tracker_status HTTP error:", tostring(codeR), bodyR)
        end
    end)
end

-- BLOCKER10G: shallow targeted diagnostics after inventory upload (nested for register limit).
local function runTargetedItemDiagnosticsAsync()
    if not LiveSafe.enableTargetedItemDiagnostics then return end
    if LiveSafe.catalogAborted then return end

    local function shallowLookupIdInReplion(idStr, data, checkedPaths)
        if type(data) ~= "table" then return nil end
        idStr = tostring(idStr)
        for _, path in ipairs(REPLION_CATALOG_CANDIDATES) do
            if #checkedPaths >= 24 then break end
            if not scanBudgetYield("target_item_replion") then return nil end
            if LiveSafe.catalogAborted or os.clock() < LiveSafe.catalogPausedUntil then return nil end
            local val = resolvePath(data, path)
            if type(val) == "table" and not isInventorySnapshotPath(path)
                and not looksLikeOwnedInventoryTable(val) then
                local fullPath = "Replion." .. path
                checkedPaths[#checkedPaths + 1] = fullPath
                local rec = val[idStr] or val[tonumber(idStr)]
                if type(rec) == "table" then
                    local name = readMetaField(rec, DEF_NAME_FIELDS)
                    if name and not isPlaceholderName(name, idStr) then
                        return {
                            name = name,
                            tier = readMetaField(rec, DEF_TIER_FIELDS),
                            category = classifyCatalogCandidate(name, fullPath) or "items",
                            source = fullPath, itemId = idStr, confidence = "exact",
                        }
                    end
                end
            end
        end
        return nil
    end

    local function shallowLookupIdInRs(idStr, checkedPaths)
        idStr = tostring(idStr)
        setActiveSection("target_item_rs", nil, idStr)
        for _, rootName in ipairs(LiveSafe.shallowRsRoots) do
            if not scanBudgetYield("target_item_rs") then return nil end
            if LiveSafe.catalogAborted or os.clock() < LiveSafe.catalogPausedUntil then return nil end
            local root = ReplicatedStorage:FindFirstChild(rootName)
            local basePath = "ReplicatedStorage." .. rootName
            checkedPaths[#checkedPaths + 1] = basePath
            if not root then continue end
            local okC, children = pcall(function() return root:GetChildren() end)
            if not okC or type(children) ~= "table" then continue end
            for _, child in ipairs(children) do
                if not scanBudgetYield("target_item_rs") then return nil end
                if child.Name == idStr then
                    local name, tier = extractInstanceMeta(child)
                    if name and not isPlaceholderName(name, idStr) then
                        return {
                            name = name, tier = tier,
                            category = classifyCatalogCandidate(name, basePath .. "." .. idStr) or "items",
                            source = basePath .. "." .. idStr, itemId = idStr, confidence = "exact",
                        }
                    end
                end
                if DEF_MODULE_EXACT_NAMES[child.Name]
                    and (child:IsA("Folder") or child:IsA("Configuration")) then
                    local subPath = basePath .. "." .. child.Name
                    checkedPaths[#checkedPaths + 1] = subPath
                    local okS, subChildren = pcall(function() return child:GetChildren() end)
                    if okS and type(subChildren) == "table" then
                        for _, sub in ipairs(subChildren) do
                            if not scanBudgetYield("target_item_rs") then return nil end
                            if sub.Name == idStr then
                                local name, tier = extractInstanceMeta(sub)
                                if name and not isPlaceholderName(name, idStr) then
                                    return {
                                        name = name, tier = tier,
                                        category = classifyCatalogCandidate(name, subPath .. "." .. idStr) or "items",
                                        source = subPath .. "." .. idStr, itemId = idStr, confidence = "exact",
                                    }
                                end
                            end
                        end
                    end
                end
            end
        end
        return nil
    end

    local function traceTargetItemId(idStr)
        local t0 = os.clock()
        idStr = tostring(idStr)
        local checkedPaths = {}
        local diag = {
            id = tonumber(idStr) or idStr, count = 0, currentName = "Item #" .. idStr,
            category = "items", checkedPaths = checkedPaths, found = false,
            candidatePath = nil, candidateKeys = {}, elapsedMs = 0,
        }
        for _, key in ipairs(ownedOrder) do
            local e = ownedInventory[key]
            if e and tostring(e.itemId) == idStr then
                diag.count = diag.count + (e.count or 1)
                if e.name then diag.currentName = e.name end
                diag.category = e.category or "items"
            end
        end
        local meta = resolveMetaById(idStr)
        if meta and meta.name and not isPlaceholderName(meta.name, idStr) then
            diag.found = true
            diag.candidatePath = meta.source
            diag.candidateKeys = { meta.name }
            diag.elapsedMs = math.floor((os.clock() - t0) * 1000)
            fishLog("TARGET_ITEM_HIT id=%s name=%s category=%s source=%s confidence=exact",
                idStr, meta.name, tostring(meta.category or diag.category),
                tostring(meta.source or "?"):sub(1, 60))
            return diag
        end
        local hit = shallowLookupIdInReplion(idStr, lastReplionDataCache, checkedPaths)
        if not hit then hit = shallowLookupIdInRs(idStr, checkedPaths) end
        if hit and hit.name and safeWriteMetadataById(idStr, hit) then
            diag.found = true
            diag.candidatePath = hit.source
            diag.candidateKeys = { hit.name }
            for _, key in ipairs(ownedOrder) do
                local e = ownedInventory[key]
                if e and tostring(e.itemId) == idStr then safeUpgradeOwnedEntry(e, hit) end
            end
            fishLog("TARGET_ITEM_HIT id=%s name=%s category=%s source=%s confidence=exact",
                idStr, hit.name, tostring(hit.category or diag.category),
                tostring(hit.source or "?"):sub(1, 60))
        else
            fishLog("TARGET_ITEM_TRACE id=%s checked=%d found=false candidatePath=none elapsedMs=%d",
                idStr, #checkedPaths, math.floor((os.clock() - t0) * 1000))
        end
        diag.elapsedMs = math.floor((os.clock() - t0) * 1000)
        return diag
    end

    setActiveSection("target_item_diag")
    LiveSafe.unresolvedDiagnostics = {}
    local checked, upgraded = 0, 0
    for _, idStr in ipairs(LiveSafe.targetItemIds) do
        if LiveSafe.catalogAborted then
            fishLog("TARGET_ITEM_DIAG aborted reason=catalog_aborted")
            break
        end
        if os.clock() < LiveSafe.catalogPausedUntil then
            fishLog("TARGET_ITEM_DIAG paused reason=frame_stall")
            break
        end
        if not scanBudgetYield("target_item_diag") then break end
        local ownedPlaceholder = false
        for _, key in ipairs(ownedOrder) do
            local e = ownedInventory[key]
            if e and tostring(e.itemId) == idStr and isPlaceholderName(e.name, idStr) then
                ownedPlaceholder = true
                break
            end
        end
        if ownedPlaceholder then
            checked = checked + 1
            local beforeFound = resolveMetaById(idStr) ~= nil
            LiveSafe.unresolvedDiagnostics[#LiveSafe.unresolvedDiagnostics + 1] = traceTargetItemId(idStr)
            if not beforeFound and resolveMetaById(idStr) then upgraded = upgraded + 1 end
            task.wait()
        end
    end
    fishLog("TARGET_ITEM_DIAG complete checked=%d upgraded=%d stillUnresolved=%d",
        checked, upgraded, countPlaceholderItems())
    if #LiveSafe.unresolvedDiagnostics > 0 then
        local discovered = {}
        for _, d in ipairs(LiveSafe.unresolvedDiagnostics) do
            if d.found and d.candidateKeys and d.candidateKeys[1] then
                discovered[#discovered + 1] = {
                    itemId = tostring(d.id), name = d.candidateKeys[1],
                    category = d.category or "items", source = d.candidatePath or "targeted_diag",
                }
            end
        end
        pcall(function()
            syncStatus(true, "live", {
                phase = "targeted_diagnostics",
                unresolvedDiagnostics = LiveSafe.unresolvedDiagnostics,
                discoveredCatalog = discovered,
            })
        end)
    end
    if upgraded > 0 then
        lastReplionStr = ""
        task.wait()
        pcall(syncCompactIdCatalogToBackend)
        pcall(syncToDashboard)
    end
end

-- Re-read Replion → parse → merge metadata → sync (used by listeners/polling).
local refreshFromReplion  -- forward declaration; defined after main wiring


-- ----------------------------------------------------------------
-- Catch handler (SECONDARY event log).
-- Replion remains the source of truth for inventory. A catch event is
-- a hint to refresh the Replion snapshot; the session log is kept only
-- as a secondary record and is NOT the public inventory.
-- ----------------------------------------------------------------
local function handleCatch(arg1, arg2, itemName, data)
    if _G.StopAutoFish then return end
    if not packetBelongsToLocalPlayer(arg1) then return end
    if type(itemName) ~= "string"
    or type(data)     ~= "table"
    or data.Weight    == nil then return end

    local name = trim(itemName)
    if name == "" then return end

    local weight = parseWeight(data.Weight)
    mergeItem(name, 1, weight, nil, "CatchEvent")  -- secondary log only
    print(("%s fish_caught event: %s | %.2f kg"):format(LOG, name, weight))

    -- Replion is source of truth — refresh only when repeat uploads enabled.
    if replionFound and refreshFromReplion and LiveSafe.repeatUpload and not LiveSafe.oneShot then
        pcall(refreshFromReplion, "catch_event")
    end
end

-- ----------------------------------------------------------------
-- Remote event hooking
-- ----------------------------------------------------------------
local function hookEvent(obj)
    if not obj:IsA("RemoteEvent") then return end
    local lname = string.lower(obj.Name)
    if lname:find("analytic") or lname:find("telemetry") then return end
    pcall(function() obj.OnClientEvent:Connect(handleCatch) end)
end

-- ================================================================
-- DIAGNOSTIC-ONLY LEGACY SCAN  (Backpack / PlayerGui / modules).
--
-- Inventory source of truth is Replion replicated player data.
-- Backpack/PlayerGui are diagnostic only and must not feed public inventory.
--
-- The functions below remain for evidence printing when
-- DEBUG_DIAGNOSTIC = true. They feed only the SECONDARY sessionInventory
-- log and are NEVER used to build the public Replion inventory_snapshot.
-- scanOwnedInventory() is only invoked from main() when DEBUG_DIAGNOSTIC.
-- ================================================================

-- Scan an Instance's direct children as owned items.
local function scanInstanceAsInventory(folder, sourceName)
    if not folder then return 0 end
    local count = 0
    pcall(function()
        for _, child in pairs(folder:GetChildren()) do
            local name = trim(child.Name)
            if #name < 2 then continue end
            local amount, weight = 1, 0
            pcall(function()
                amount = child:GetAttribute("Count")
                       or child:GetAttribute("Amount")
                       or child:GetAttribute("Quantity")
                       or (child:IsA("IntValue")    and math.floor(child.Value))
                       or (child:IsA("NumberValue") and math.floor(child.Value))
                       or 1
                amount = math.max(1, math.floor(tonumber(amount) or 1))
                weight = child:GetAttribute("Weight")
                       or child:GetAttribute("TotalWeight")
                       or 0
                weight = tonumber(weight) or 0
            end)
            mergeItem(name, amount, weight, nil, sourceName)
            count = count + 1
        end
    end)
    return count
end

-- Recursively walk a plain Lua table for item records.
local function walkInventoryTable(rootTable, sourceName)
    if type(rootTable) ~= "table" then return end
    local visited   = {}
    local nodeCount = {n = 0}
    local NAME_KEYS   = {"name","Name","itemName","fishName","rodName","displayName"}
    local COUNT_KEYS  = {"count","Count","amount","Amount","quantity","Quantity"}
    local WEIGHT_KEYS = {"weight","Weight","totalWeight","TotalWeight"}
    local SKIP_KEYS   = {config=true,settings=true,meta=true,_meta=true}

    local function walk(t, depth)
        if depth > 8 or type(t) ~= "table" then return end
        local addr = tostring(t)
        if visited[addr] then return end
        visited[addr] = true
        nodeCount.n = nodeCount.n + 1
        if nodeCount.n > 5000 then return end

        local nameVal
        for _, k in ipairs(NAME_KEYS) do
            if type(t[k]) == "string" and #trim(t[k]) > 1 then
                nameVal = trim(t[k]); break
            end
        end
        if nameVal then
            local countVal, weightVal = 1, 0
            for _, k in ipairs(COUNT_KEYS) do
                local v = tonumber(t[k])
                if v and v > 0 then countVal = math.floor(v); break end
            end
            for _, k in ipairs(WEIGHT_KEYS) do
                local v = tonumber(t[k])
                if v then weightVal = v; break end
            end
            mergeItem(nameVal, countVal, weightVal, nil, sourceName)
        end

        for k, v in pairs(t) do
            if type(v) == "table" then
                local lk = type(k) == "string" and k:lower() or ""
                if not SKIP_KEYS[lk] then walk(v, depth + 1) end
            end
        end
    end
    walk(rootTable, 0)
end

-- Scan all client-accessible sources for player-owned items.
local function scanOwnedInventory()
    print(LOG, "Owned inventory scan starting...")
    local foundSource = false
    local snapshotBefore = #caughtOrder

    -- SOURCE 1: Backpack
    pcall(function()
        local backpack = LocalPlayer:FindFirstChildOfClass("Backpack")
                      or LocalPlayer:FindFirstChild("Backpack")
        if backpack then
            local c = scanInstanceAsInventory(backpack, "Backpack")
            if c > 0 then foundSource = true
                print(LOG, ("  Backpack: %d tool(s)"):format(c)) end
        end
    end)

    -- SOURCE 2: Character equipped tool
    pcall(function()
        local char = LocalPlayer.Character
        if char then
            for _, child in pairs(char:GetChildren()) do
                if child:IsA("Tool") then
                    mergeItem(trim(child.Name), 1, 0, nil, "Character")
                    foundSource = true
                end
            end
        end
    end)

    -- SOURCE 3: LocalPlayer data folders
    pcall(function()
        local DATA_NAMES = {"Data","PlayerData","Inventory","leaderstats"}
        for _, dname in ipairs(DATA_NAMES) do
            local folder = LocalPlayer:FindFirstChild(dname)
            if not folder then continue end
            local c = scanInstanceAsInventory(folder, "Player."..dname)
            if c > 0 then foundSource = true
                print(LOG, ("  Player.%s: %d item(s)"):format(dname, c)) end
            for _, sub in pairs(folder:GetChildren()) do
                if sub:IsA("Folder") then
                    local c2 = scanInstanceAsInventory(sub, "Player."..dname.."."..sub.Name)
                    if c2 > 0 then foundSource = true
                        print(LOG, ("  Player.%s.%s: %d item(s)"):format(dname, sub.Name, c2)) end
                end
            end
        end
    end)

    -- SOURCE 4: ReplicatedStorage player-specific data
    pcall(function()
        local RS_FOLDERS = {"PlayerData","Profiles","Inventories","Data","PlayerInventory","UserData"}
        for _, fname in ipairs(RS_FOLDERS) do
            local folder = ReplicatedStorage:FindFirstChild(fname)
            if not folder then continue end
            local playerFolder = folder:FindFirstChild(LocalPlayer.Name)
                              or folder:FindFirstChild(tostring(LocalPlayer.UserId))
            if not playerFolder then continue end
            print(LOG, ("  RS.%s: player folder found for %s"):format(fname, LocalPlayer.Name))
            local c = scanInstanceAsInventory(playerFolder, "RS."..fname)
            if c > 0 then foundSource = true end
            for _, sub in pairs(playerFolder:GetChildren()) do
                if sub:IsA("Folder") then
                    local c2 = scanInstanceAsInventory(sub, "RS."..fname.."."..sub.Name)
                    if c2 > 0 then foundSource = true
                        print(LOG, ("  RS.%s.%s: %d item(s)"):format(fname, sub.Name, c2)) end
                end
            end
        end
    end)

    -- SOURCE 5: PlayerGui inventory UI (card-aware, descendant-walking parse)
    -- Walks ALL nested cards (not just direct children) so multi-level layouts
    -- (ScrollingFrame > Container > Card) are captured. A card is accepted when
    -- it has a readable name that is NOT a stat label AND either matches the
    -- catalog OR carries a fish image. mergeItem still applies the denylist.
    pcall(function()
        local gui = LocalPlayer:FindFirstChildOfClass("PlayerGui")
        if not gui then return end
        local INV_PATTERNS = {
            "inventory","backpack","fishlist","itemlist",
            "collection","fishbook","myfish","myitems","bag",
        }
        local function isInvFrame(name)
            local ln = name:lower()
            for _, p in ipairs(INV_PATTERNS) do
                if ln:find(p, 1, true) then return true end
            end
            return false
        end

        -- Pull the best fish image URL out of a card (direct or nested).
        local function cardImage(card)
            local url
            pcall(function()
                local img = card:FindFirstChildWhichIsA("ImageLabel", true)
                         or card:FindFirstChildWhichIsA("ImageButton", true)
                if img and img.Image and img.Image ~= ""
                   and not img.Image:find("GuiImagePlaceholder") then
                    url = resolveImageUrl(img.Image)
                end
            end)
            return url
        end

        -- Pull the best name TextLabel from a card (named fields preferred).
        local function cardName(card)
            local lbl = card:FindFirstChild("ItemName")
                     or card:FindFirstChild("FishName")
                     or card:FindFirstChild("RodName")
                     or card:FindFirstChild("Title")
                     or card:FindFirstChild("Name")
            if lbl and lbl:IsA("TextLabel") then return trim(lbl.Text) end
            -- Fallback: first non-numeric TextLabel descendant
            local best
            pcall(function()
                for _, t in ipairs(card:GetDescendants()) do
                    if t:IsA("TextLabel") then
                        local txt = trim(t.Text)
                        if #txt > 2 and not txt:match("^[%dxX%s%.,kgKG]+$") then
                            best = txt
                            break
                        end
                    end
                end
            end)
            return best or ""
        end

        local function cardCount(card)
            local c = card:FindFirstChild("Count")
                   or card:FindFirstChild("Amount")
                   or card:FindFirstChild("Quantity")
            if c and c:IsA("TextLabel") then
                return tonumber(trim(c.Text):match("%d+")) or 1
            end
            -- Look for "x12" style labels among descendants
            local n = 1
            pcall(function()
                for _, t in ipairs(card:GetDescendants()) do
                    if t:IsA("TextLabel") then
                        local m = trim(t.Text):match("[xX]%s*(%d+)")
                        if m then n = tonumber(m) or n; break end
                    end
                end
            end)
            return n
        end

        local scanned, accepted = 0, 0
        for _, desc in pairs(gui:GetDescendants()) do
            scanned = scanned + 1
            if scanned > 6000 then break end
            if not (desc:IsA("Frame") or desc:IsA("ScrollingFrame")) then continue end
            if not isInvFrame(desc.Name) then continue end
            if DEBUG_VERBOSE_INVENTORY then
                print(LOG, "  Scanning PlayerGui frame:", desc:GetFullName())
            end
            -- Walk ALL descendant cards, not just direct children.
            for _, card in pairs(desc:GetDescendants()) do
                if not (card:IsA("Frame") or card:IsA("ImageButton")) then continue end

                local itemName = cardName(card)
                if #itemName <= 2 then continue end
                local key = normalizeName(itemName)
                if isStatLabel(key) then continue end

                local imageUrl = cardImage(card)
                local inCatalog = resolveFishMeta(key) ~= nil

                -- Accept when catalog-known OR an actual fish image present.
                if inCatalog or imageUrl then
                    local itemCount = cardCount(card)
                    mergeItem(itemName, itemCount, 0, imageUrl, "PlayerGui")
                    accepted = accepted + 1
                    foundSource = true
                end
            end
        end
        if accepted > 0 then
            print(LOG, ("  PlayerGui: accepted %d card(s)"):format(accepted))
        end
    end)

    -- SOURCE 6: Client ModuleScript caches
    pcall(function()
        local MOD_PARENTS = {
            ReplicatedStorage:FindFirstChild("Modules"),
            ReplicatedStorage:FindFirstChild("ClientModules"),
            ReplicatedStorage:FindFirstChild("Shared"),
        }
        for _, parent in ipairs(MOD_PARENTS) do
            if not parent then continue end
            for _, mod in pairs(parent:GetChildren()) do
                if not mod:IsA("ModuleScript") then continue end
                local ln = mod.Name:lower()
                if ln:find("inventory") or ln:find("backpack")
                   or ln:find("fishcache") or ln:find("itemcache") then
                    local ok, result = pcall(require, mod)
                    if ok and type(result) == "table" then
                        print(LOG, "  Module cache:", mod.Name)
                        walkInventoryTable(result, "Module:"..mod.Name)
                        foundSource = true
                    end
                end
            end
        end
    end)

    -- Summary
    local added = #caughtOrder - snapshotBefore
    if not foundSource then
        warn(LOG, "Owned inventory source not found — only future catches will appear.")
    else
        local totals = {fish=0,rod=0,items=0,bait=0,unknown=0}
        for _, key in ipairs(caughtOrder) do
            local cat = sessionInventory[key].category
            totals[cat] = (totals[cat] or 0) + 1
        end
        print(LOG, ("Owned inventory: %d unique — fish=%d  rods=%d  items=%d  bait=%d"):format(
            added, totals.fish, totals.rod, totals.items, totals.bait))
        if #rejectedLabels > 0 then
            print(LOG, ("Rejected labels: %d (stat labels / denylist)"):format(#rejectedLabels))
        end
        if DEBUG_VERBOSE_INVENTORY then
            for _, key in ipairs(caughtOrder) do
                local d = sessionInventory[key]
                print(LOG, ("  [%s] %s  x%d  %.2f kg  tier=%s  src:%s"):format(
                    d.category, d.name, d.amount, d.weight, d.tier or "?", d.source))
            end
        end
    end
    return foundSource
end

-- ================================================================
-- REPLION RUNTIME — snapshot refresh, realtime listeners, polling.
-- ================================================================
local activeReplion = nil   -- the selected player-data replion

-- Re-read Replion → parse inventory → merge metadata → sync snapshot.
-- Assigns to the earlier forward-declared `refreshFromReplion`.
function refreshFromReplion(reason)
    if not activeReplion then return 0 end
    LiveSafe.currentSyncReason = reason or ""
    setActiveSection("phase_a_parse")
    local data = readReplionData(activeReplion)
    if type(data) ~= "table" then
        warn(LOG, "Replion re-read returned no data (", tostring(reason), ")")
        return 0
    end
    lastReplionDataCache = data
    -- PART 1: on the first parse (or when nothing is accepted) dump the exact
    -- Replion inventory shape so we can see real keys/records safely.
    if DEBUG_REPLION_INVENTORY_DUMP and reason == "initial" then
        pcall(debugDumpReplionInventoryShape, data)
    end

    -- BLOCKER10C: Phase A skips heavy Replion catalog ingest (background task handles it).
    local isPhaseB = (reason == "phase_b_resync")
    if isPhaseB and LiveSafe.enablePhaseBItemUpgrade then
        pcall(buildCatalogFromReplionData, data)
        pcall(postParseItemCatalogPass)
    end

    -- Wrap the entire parse in xpcall so any error inside
    -- parseInventoryFromReplionData is caught HERE and still produces a
    -- REPLION_PARSE_RESULT log + inventory_parse_failed status instead of
    -- silently propagating to the outer pcall(refreshFromReplion, ...) and
    -- leaving the backend permanently stuck at player_data_selected.
    if LiveSafe.oneShot then stepBegin("inventory_read") end
    local parseOk, parseResult = xpcall(function()
        return parseInventoryFromReplionData(data)
    end, debug.traceback)
    if LiveSafe.oneShot then stepEnd("inventory_read") end

    local n = 0
    if not parseOk then
        local shortErr = tostring(parseResult):sub(1, 500)
        print(LOG, ("REPLION_PARSE_ERROR %s"):format(shortErr))
        print(LOG, ("REPLION_PARSE_RESULT selected=%s path=%s raw=%d accepted=0 fish=0 rods=0 items=0 rejected=%d images=0 tiers=0 phase=inventory_parse_failed errorShort=%s"):format(
            selectedReplion or "?", replionParseResult.path or "none",
            replionParseResult.raw or 0, replionParseResult.rejected or 0,
            shortErr:sub(1, 120)))
        print(LOG, ("DASHBOARD_SEND tracker_status phase=inventory_parse_failed raw=%d accepted=0"):format(
            replionParseResult.raw or 0))
        syncStatus(true, "inventory_parse_failed", {
            parseStats = {
                raw          = replionParseResult.raw or 0,
                accepted     = 0,
                rejected     = replionParseResult.rejected or 0,
                images       = 0,
                tiers        = 0,
                selectedPath = replionParseResult.path or "none",
                error        = shortErr,
                firstRejected= buildFirstRejectedSample(10),
            },
        })
        return 0
    end
    n = parseResult or 0
    inventorySource = "replion"
    local instances = replionParseResult.acceptedInstances or 0
    local uniqueAccepted = replionParseResult.accepted or n or 0

    -- Success path: any accepted instance rows → inventory_snapshot (phase=live).
    if instances > 0 then
        local _, flat = buildOwnedGroups()
        local sigOk, sig = pcall(function() return HttpService:JSONEncode(flat) end)
        if not sigOk then
            local shortErr = tostring(sig):sub(1, 500)
            fishLog("REPLION_PARSE_ERROR sync encode: %s", shortErr)
            syncStatus(true, "inventory_parse_failed", {
                parseStats = {
                    raw = replionParseResult.raw, accepted = uniqueAccepted,
                    acceptedInstances = instances, rejected = replionParseResult.rejected,
                    selectedPath = replionParseResult.path, error = shortErr,
                },
            })
            return 0
        end
        if sig == lastReplionStr and reason ~= "initial" and reason ~= "light_sync" then
            return uniqueAccepted
        end
        lastReplionStr = sig

        if LiveSafe.verbose then
            fishLogDebug("Replion inventory summary: accepted=%d acceptedInstances=%d rejected=%d raw=%d (%s)",
                uniqueAccepted, instances, replionParseResult.rejected, replionParseResult.raw, tostring(reason))
        end
        local syncOk, syncResult = pcall(syncToDashboard)
        local uploadOk = syncOk and syncResult == true
        if not syncOk then
            local shortErr = tostring(syncResult):sub(1, 500)
            fishLog("REPLION_PARSE_ERROR sync send: %s", shortErr)
            if reason ~= "light_sync" then
                syncStatus(true, "inventory_parse_failed", {
                    parseStats = {
                        raw = replionParseResult.raw, accepted = uniqueAccepted,
                        acceptedInstances = instances, rejected = replionParseResult.rejected,
                        selectedPath = replionParseResult.path, error = shortErr,
                    },
                })
            end
        elseif reason == "initial" or reason == "startup" then
            if LiveSafe.oneShot or LiveSafe.lightSyncEnabled then
                fishLog("INVENTORY_READ ok=true accepted=%d", uniqueAccepted)
                fishLog("INVENTORY_UPLOAD ok=true accepted=%d", uniqueAccepted)
            else
                fishLog("INVENTORY_UPLOAD ok=true raw=%d accepted=%d placeholders=%d",
                    replionParseResult.raw or 0, uniqueAccepted, countPlaceholderItems())
            end
            if LiveSafe.enableTargetedItemDiagnostics and not LiveSafe.oneShot then
                task.spawn(function()
                    task.wait(0.5)
                    if isCurrentRun() then pcall(runTargetedItemDiagnosticsAsync) end
                end)
            end
        elseif reason == "light_sync" then
            if uploadOk then
                fishLog("SYNC_UPLOAD ok=true accepted=%d", uniqueAccepted)
            else
                warn(LOG, ("SYNC_UPLOAD warn accepted=%d"):format(uniqueAccepted))
                return -1
            end
        end
        return uniqueAccepted
    end

    -- Zero accepted instances: report precise failure/empty phase.
    if replionParseResult.pathExists then
        if replionParseResult.raw > 0 then
            if DEBUG_REPLION_INVENTORY_DUMP then
                pcall(debugDumpReplionInventoryShape, data)
            end
            print(LOG, ("DASHBOARD_SEND tracker_status phase=inventory_parse_failed raw=%d accepted=0"):format(
                replionParseResult.raw))
            syncStatus(true, "inventory_parse_failed", {
                path = replionParseResult.path,
                raw = replionParseResult.raw,
                rejected = replionParseResult.rejected,
                parseStats = {
                    raw = replionParseResult.raw, accepted = 0, acceptedInstances = 0,
                    rejected = replionParseResult.rejected,
                    images = replionParseResult.images, tiers = replionParseResult.tiers,
                    selectedPath = replionParseResult.path,
                    firstRejected = buildFirstRejectedSample(10),
                },
            })
        else
            print(LOG, "DASHBOARD_SEND tracker_status phase=inventory_empty raw=0 accepted=0")
            syncStatus(true, "inventory_empty", {
                path = replionParseResult.path,
                parseStats = {
                    raw = 0, accepted = 0, acceptedInstances = 0, rejected = 0,
                    selectedPath = replionParseResult.path,
                },
            })
        end
        return 0
    end

    return uniqueAccepted
end

-- ----------------------------------------------------------------
-- PART 8: Attach realtime listeners, or fall back to polling.
-- Returns true if a listener was attached, false if polling is used.
-- ----------------------------------------------------------------
local function attachReplionListeners(client)
    local attached = false

    local function onChange(label)
        if DEBUG_VERBOSE_INVENTORY then
            print(LOG, "Replion update detected:", label)
        end
        if _G.StopAutoFish then return end
        pcall(refreshFromReplion, label)
    end

    -- Replion-object listener methods (only if we already have a replion).
    local listenerMethods = { "OnChange", "OnDataChange", "ListenToChange", "Listen" }
    for _, m in ipairs(listenerMethods) do
        if not activeReplion then break end
        local ok = pcall(function()
            local fn = activeReplion[m]
            if type(fn) == "function" then
                -- Most signatures: replion:OnChange("Inventory", callback)
                local okInner = pcall(function()
                    fn(activeReplion, "Inventory", function() onChange("Inventory."..m) end)
                end)
                if not okInner then
                    -- Some take just a callback: replion:OnChange(callback)
                    fn(activeReplion, function() onChange(m) end)
                end
                attached = true
            end
        end)
        if ok and attached then break end
    end

    -- Signal-style fields: replion.Changed / replion.DataChanged
    if not attached and activeReplion then
        for _, sig in ipairs({ "Changed", "DataChanged" }) do
            pcall(function()
                local s = activeReplion[sig]
                if s and typeof(s) == "RBXScriptSignal" then
                    s:Connect(function() onChange(sig) end)
                    attached = true
                elseif type(s) == "table" and type(s.Connect) == "function" then
                    s:Connect(function() onChange(sig) end)
                    attached = true
                end
            end)
            if attached then break end
        end
    end

    -- Client-level replion-added (in case data replion swaps).
    if client then
        for _, m in ipairs({ "OnReplionAdded", "ReplionAdded" }) do
            pcall(function()
                local fn = client[m]
                if type(fn) == "function" then
                    fn(client, function() onChange("client."..m) end)
                end
            end)
        end
    end

    if attached then
        print(LOG, "Replion realtime listener active")
    else
        print(LOG, "Replion listener unavailable; polling every 5s")
    end

    -- Polling loop — disabled in one-shot mode (BLOCKER10I freeze fix).
    if LiveSafe.oneShot or not LiveSafe.repeatUpload then
        if attached and LiveSafe.verbose then
            fishLogDebug("Replion listener attached (one-shot: no polling)")
        end
        return attached
    end

    task.spawn(function()
        while not _G.StopAutoFish and isCurrentRun() do
            task.wait(5)
            if not isCurrentRun() then break end
            if activeReplion then
                if not attached then pcall(refreshFromReplion, "poll") end
            elseif client then
                local okP, rep, name = pcall(findPlayerDataReplion, client)
                if okP and rep then
                    activeReplion   = rep
                    selectedReplion = name
                    inventorySource = "replion"
                    print(LOG, "Player data replion appeared late:", tostring(name))
                    syncStatus(true, "player_data_selected")
                    pcall(refreshFromReplion, "initial")
                    attachReplionListeners(client)  -- attach real listeners now
                    return
                end
            end
        end
    end)
    return attached
end

local function hookRemotesDeferred()
    if not LiveSafe.debugRemoteHooks then
        fishLog("REMOTE_HOOKS disabled_by_default=true")
        return
    end
    if not isCurrentRun() then return end
    resetScanBudget()
    local t0 = os.clock()
    local batch = 0
    for _, obj in pairs(ReplicatedStorage:GetDescendants()) do
        hookEvent(obj)
        batch = batch + 1
        if batch >= 40 then
            scanBudgetYield("remote_hook")
            batch = 0
        end
    end
    ReplicatedStorage.DescendantAdded:Connect(function(obj) hookEvent(obj) end)
    perfEndSection(t0, "remote_hook", 50)
end

local function runReplionStartupPhase()
    if not isCurrentRun() then return end
    stepBegin("replion_find")
    local client = nil
    local okR, resClient = pcall(findReplionClient)
    if okR then client = resClient end
    stepEnd("replion_find")

    if client then
        replionFound = true
        activeReplionClient = client
        fishLogDebug("Replion client found")

        local rep, name, seen, tried = nil, nil, nil, nil
        local okP, r1, r2, r3, r4 = pcall(findPlayerDataReplion, client)
        if okP then rep, name, seen, tried = r1, r2, r3, r4 end

        if rep then
            selectedReplion = name
            activeReplion   = rep
            inventorySource = "replion"

            local data = readReplionData(rep)
            if type(data) == "table" then
                lastReplionDataCache = data
                local n = 0
                local okRef, res = pcall(refreshFromReplion, "initial")
                if okRef then n = res or 0 end

                if LiveSafe.oneShot then
                    fishLog("TRACKER_DONE one_shot=true")
                    return
                end

                if LiveSafe.lightSyncEnabled and LiveSafe.repeatUpload then
                    if not LiveSafe.lightSyncLoopStarted then
                        LiveSafe.lightSyncLoopStarted = true
                        local baseInterval = LiveSafe.lightSyncIntervalSeconds or 10
                        fishLog("SYNC_LOOP_STARTED interval=%d", baseInterval)
                        task.spawn(function()
                            local interval = baseInterval
                            local backoff = LiveSafe.lightSyncBackoffSeconds or 30
                            local failThreshold = LiveSafe.lightSyncFailThreshold or 3
                            local failStreak = 0
                            while not _G.StopAutoFish and isCurrentRun() do
                                task.wait(interval)
                                if not isCurrentRun() or not activeReplion then continue end
                                local cycleOk, cycleResult = xpcall(function()
                                    local t0 = os.clock()
                                    local budget = (LiveSafe.lightSyncLoopBudgetMs or 500) / 1000
                                    local snap = readReplionData(activeReplion)
                                    if type(snap) ~= "table" then return false end
                                    if (os.clock() - t0) >= budget then return false end
                                    lastReplionDataCache = snap
                                    local n = refreshFromReplion("light_sync")
                                    if (os.clock() - t0) >= budget then return false end
                                    return type(n) == "number" and n ~= -1
                                end, debug.traceback)
                                if cycleOk and cycleResult then
                                    failStreak = 0
                                    interval = baseInterval
                                else
                                    failStreak = failStreak + 1
                                    warn(LOG, ("SYNC_UPLOAD warn streak=%d"):format(failStreak))
                                    if failStreak >= failThreshold then interval = backoff end
                                end
                            end
                        end)
                    end
                end

                if LiveSafe.debug then
                    local paths = inventoryPaths(data)
                    if #paths == 0 and n == 0 then
                        local topKeys = {}
                        for k in pairs(data) do topKeys[#topKeys + 1] = tostring(k) end
                        syncStatus(true, "inventory_path_missing", { topLevelKeys = topKeys })
                    end
                end
            else
                warn(LOG, "Replion data snapshot read: failed (no table)")
                if not LiveSafe.oneShot then syncStatus(true, "inventory_path_missing") end
                if LiveSafe.oneShot then fishLog("TRACKER_DONE one_shot=true") end
            end

            if not LiveSafe.oneShot and not LiveSafe.lightSyncEnabled then
                attachReplionListeners(client)
            end
        else
            inventorySource = "replion"
            warn(LOG, "Replion client found but player data not found within wait window.")
            if not LiveSafe.oneShot and not LiveSafe.lightSyncEnabled then
                syncStatus(true, "player_data_not_found", {
                    candidateNamesTried = tried or replionNamesTried,
                    candidatesSeen      = seen or replionCandidatesSeen,
                    availableMethods    = replionClientMethods,
                })
                attachReplionListeners(client)
            elseif LiveSafe.oneShot then
                fishLog("TRACKER_DONE one_shot=true")
            end
        end
    else
        inventorySource = "replion_missing"
        warn(LOG, "Replion client not found. Inventory snapshot unavailable.")
        if not LiveSafe.oneShot then syncStatus(true, "replion_missing") end
        if LiveSafe.oneShot then fishLog("TRACKER_DONE one_shot=true") end
    end
end

-- ================================================================
-- MAIN STARTUP
-- ================================================================
local function main()
    fishLog("TRACKER_BUILD %s", TRACKER_BUILD)
    fishLog("LIGHT_SYNC enabled=%s interval=%d", tostring(LiveSafe.lightSyncEnabled),
        LiveSafe.lightSyncIntervalSeconds or 10)
    inventorySource = "replion"
    if not LiveSafe.oneShot then syncStatus(true, "startup") end
    task.spawn(runReplionStartupPhase)
end

xpcall(main, function(err)
    warn("[DENG TRACKER] FATAL ERROR — real traceback:")
    warn(debug.traceback(err))
end)