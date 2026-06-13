#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');
const { DEFAULT_PRIVATE_RAW_PATH, resolveRawTrackerSourcePath } = require('./trackerRawSourcePath');

const REPO_ROOT = path.join(__dirname, '..');
const FALLBACK_SOURCE = path.join(REPO_ROOT, '_test_converted.lua');
const TARGET = process.argv[2] || DEFAULT_PRIVATE_RAW_PATH;

function isDistWrapper(src) {
  return src.includes('local __B=[[') && src.includes('local function __D(s)');
}

function readSource() {
  if (fs.existsSync(TARGET) && !isDistWrapper(fs.readFileSync(TARGET, 'utf8'))) {
    return { src: fs.readFileSync(TARGET, 'utf8'), from: TARGET };
  }
  if (!fs.existsSync(FALLBACK_SOURCE)) {
    throw new Error(`source missing: ${TARGET} and fallback ${FALLBACK_SOURCE}`);
  }
  return { src: fs.readFileSync(FALLBACK_SOURCE, 'utf8'), from: FALLBACK_SOURCE };
}

const HELPERS = `
function LiveSafe.isTotemName(name)
    local s = tostring(name or "")
    return s ~= "" and string.find(string.lower(s), "totem", 1, true) ~= nil
end

function LiveSafe.sumTotemQuantity(totemItems)
    local total = 0
    for i = 1, #(totemItems or {}) do
        local q = tonumber(totemItems[i].quantity) or 1
        if q < 1 then q = 1 end
        total = total + math.floor(q)
    end
    return total
end

function LiveSafe.formatTotemScanProof(totemItems)
    local names, seen = {}, {}
    for i = 1, #(totemItems or {}) do
        local n = tostring((totemItems[i] or {}).name or "")
        if n ~= "" and not seen[n] then
            seen[n] = true
            names[#names + 1] = n
        end
    end
    return #names, table.concat(names, ", ")
end

function LiveSafe.logTotemScanProof(totemItems)
    local count, joined = LiveSafe.formatTotemScanProof(totemItems)
    if count < 1 then return end
    print(LOG, ("TOTEM_SCAN_FOUND count=%d names=%s"):format(count, joined))
end

function LiveSafe.printGameItemDbUploadOk(ok200, statusCode, fishCount, stoneCount, totemItems)
    local totemCount = #(totemItems or {})
    local totemQty = LiveSafe.sumTotemQuantity(totemItems)
    print(LOG, ("PLAYERDATA_GAMEITEMDB_UPLOAD_OK %s status=%s fish=%d stones=%d totems=%d totemQty=%d"):format(
        tostring(ok200), tostring(statusCode or "?"), fishCount, stoneCount, totemCount, totemQty))
    LiveSafe.logTotemScanProof(totemItems)
end

function LiveSafe.classifyNonStoneInventoryItem(ItemUtility, item, itemId, qty, mutation, icon)
    local okData, itemData = pcall(ItemUtility.GetItemDataFromItemType, "Items", itemId)
    local data = itemData and (itemData.Data or itemData)
    if not okData or type(data) ~= "table" then
        return nil, { itemId = itemId, reason = "itemutility_unresolved" }
    end
    if data.Type == "Fish" and data.Name and tostring(data.Name) ~= "" then
        local tierNum = tonumber(data.Tier) or 1
        return "fish", {
            kind = "fish", itemId = itemId, name = data.Name, baseName = data.Name,
            quantity = qty, uuid = item.UUID, tier = tierNum, rarity = LiveSafe.TierNames[tierNum] or "Unknown",
            mutation = mutation, icon = icon, type = "Fish",
            imageSource = "gameitemdb_icon", source = "playerdata_gameitemdb", identityVerified = true,
        }
    end
    if LiveSafe.isTotemName(data.Name) then
        local tierNum = tonumber(data.Tier) or nil
        return "totem", {
            kind = "totem", itemId = itemId, name = tostring(data.Name),
            quantity = qty, uuid = item.UUID, tier = tierNum,
            rarity = tierNum and (LiveSafe.TierNames[tierNum] or "Unknown") or nil,
            mutation = mutation, icon = icon, type = "Totem", category = "totem",
            imageSource = "gameitemdb_icon", source = "playerdata_gameitemdb", identityVerified = true,
        }
    end
    return nil, { itemId = itemId, reason = "itemutility_unresolved" }
end
`;

const CLASSIFY_LOOP = `            else
                local kind, row = LiveSafe.classifyNonStoneInventoryItem(ItemUtility, item, itemId, qty, mutation, icon)
                if kind == "fish" then
                    stats.resolvedFish = stats.resolvedFish + 1
                    if icon and icon ~= "rbxassetid://0" then stats.fishIconResolved = stats.fishIconResolved + 1
                    else stats.fishIconMissing = stats.fishIconMissing + 1 end
                    fishItems[#fishItems + 1] = row
                elseif kind == "totem" then
                    stats.resolvedTotem = stats.resolvedTotem + 1
                    if icon and icon ~= "rbxassetid://0" then stats.totemIconResolved = stats.totemIconResolved + 1 end
                    totemItems[#totemItems + 1] = row
                else
                    stats.unresolved = stats.unresolved + 1
                    unresolvedItems[#unresolvedItems + 1] = row or { itemId = itemId, reason = "itemutility_unresolved" }
                end`;

function patch(src) {
  src = src.replace(/\r\n/g, '\n');
  src = src.replace(
    /local TRACKER_BUILD = "[^"]+"/,
    'local TRACKER_BUILD = "LOADER_REGISTER_LIMIT_FIX_2026_06_11"',
  );

  if (!src.includes('function LiveSafe.isTotemName')) {
    const anchor = 'function LiveSafe.scanPlayerDataGameItemDbInventory()';
    if (!src.includes(anchor)) throw new Error('scan anchor missing');
    src = src.replace(anchor, `${HELPERS}\n${anchor}`);
  }

  const oldBranch = /else\n                local okData, itemData = pcall\(ItemUtility\.GetItemDataFromItemType, "Items", itemId\)[\s\S]*?unresolvedItems\[#unresolvedItems \+ 1\] = \{ itemId = itemId, reason = "itemutility_unresolved" \}\n                end\n            end/;
  if (!oldBranch.test(src)) {
    if (!src.includes('LiveSafe.classifyNonStoneInventoryItem')) {
      throw new Error('classify loop anchor missing');
    }
  } else {
    src = src.replace(oldBranch, `${CLASSIFY_LOOP}\n            end`);
  }

  src = src.replace(
    /print\(LOG, \("PLAYERDATA_GAMEITEMDB_UPLOAD_OK %s status=%s fish=%d stones=%d"\):format\(\n        tostring\(ok200\), pcallOk and type\(result\) == "table" and tostring\(result\.StatusCode or "\?"\) or "\?",\n        uploadFishCount, uploadStoneCount\)\)/,
    'LiveSafe.printGameItemDbUploadOk(ok200, pcallOk and type(result) == "table" and tostring(result.StatusCode or "?") or "?", uploadFishCount, uploadStoneCount, gameItemScan.totemItems)',
  );

  src = src.replace(
    /print\(LOG, \("PLAYERDATA_GAMEITEMDB_UPLOAD_OK %s status=%s fish=%d stones=%d"\):format\(\n                    tostring\(ok200\), tostring\(code\), uploadFishCount, uploadStoneCount\)\)/,
    'LiveSafe.printGameItemDbUploadOk(ok200, code, uploadFishCount, uploadStoneCount, payload.totemItems)',
  );

  if (!src.includes('local uploadTotemCount =')) {
    src = src.replace(
      '    local uploadStoneCount = #(gameItemScan.stoneItems or {})\n    local inventoryCount = gameItemScan.inventoryCount or 0',
      '    local uploadStoneCount = #(gameItemScan.stoneItems or {})\n    local uploadTotemCount = #(gameItemScan.totemItems or {})\n    local inventoryCount = gameItemScan.inventoryCount or 0',
    );
  }

  src = src.replace(
    'if inventoryCount > 0 and uploadFishCount == 0 and uploadStoneCount == 0 then',
    'if inventoryCount > 0 and uploadFishCount == 0 and uploadStoneCount == 0 and uploadTotemCount == 0 then',
  );

  if (!src.includes('LiveSafe.logTotemScanProof(gameItemScan.totemItems)')) {
    src = src.replace(
      /print\(LOG, \("PLAYERDATA_GAMEITEMDB_UPLOAD fish=%d stones=%d totems=%d unresolved=%d"\):format\(\n        #\(gameItemScan\.fishItems or \{\}\),\n        #\(gameItemScan\.stoneItems or \{\}\),\n        #\(gameItemScan\.totemItems or \{\}\),\n        #\(gameItemScan\.unresolvedItems or \{\}\)\)\)/,
      `print(LOG, ("PLAYERDATA_GAMEITEMDB_UPLOAD fish=%d stones=%d totems=%d unresolved=%d"):format(
        #(gameItemScan.fishItems or {}),
        #(gameItemScan.stoneItems or {}),
        #(gameItemScan.totemItems or {}),
        #(gameItemScan.unresolvedItems or {})))
    LiveSafe.logTotemScanProof(gameItemScan.totemItems)`,
    );
  }

  if (!src.includes('LOADER_FIX_REGISTER_LIMIT_2026_06_11: isolate locals in IIFE')) {
    src = src.replace(
      'print("[FishTracker] TRACKER_BOOT_BEGIN BLOCKER10Z7_METADATA_SPECIES_EXTRACTION_2026_06_08 BLOCKER10ZT3_SYNC_STATUS_COIN_MOBILE_TABLE_2026_06_10")\n\n-- Kill switch',
      'print("[FishTracker] TRACKER_BOOT_BEGIN BLOCKER10Z7_METADATA_SPECIES_EXTRACTION_2026_06_08 BLOCKER10ZT3_SYNC_STATUS_COIN_MOBILE_TABLE_2026_06_10")\n\n-- LOADER_FIX_REGISTER_LIMIT_2026_06_11: isolate locals in IIFE\n;(function()\n\n-- Kill switch',
    );
    if (!src.trimEnd().endsWith('end)()')) {
      src = `${src.trimEnd()}\nend)()\n`;
    }
  }

  // Luau requires a statement separator before a leading-paren IIFE after print().
  src = src.replace(
    /-- LOADER_FIX_REGISTER_LIMIT_2026_06_11: isolate locals in IIFE\n\(function\(\)/,
    '-- LOADER_FIX_REGISTER_LIMIT_2026_06_11: isolate locals in IIFE\n;(function()',
  );
  src = src.replace(
    /print\("\[FishTracker\] TRACKER_BOOT_BEGIN[^\n]*"\)\n\n\(function\(\)/,
    (m) => m.replace('\n(function()', '\n;(function()'),
  );

  if (src.includes('LiveSafe.uploadSeq = 0\nLiveSafe.firstFullSnapshotAccepted = false')) {
    src = src.replace(
      `LiveSafe.uploadSeq = 0
LiveSafe.firstFullSnapshotAccepted = false

function attachSnapshotExecutionProof(payload, scanMeta)
    if type(payload) ~= "table" then return payload end
    LiveSafe.uploadSeq = (LiveSafe.uploadSeq or 0) + 1
    payload.runId = thisRunId
    payload.executionSessionId = thisRunId
    payload.uploadSeq = LiveSafe.uploadSeq
    payload.firstExecution = LiveSafe.firstFullSnapshotAccepted ~= true`,
      `function ensureUploadRuntimeState()
    local rt = _G.__DENG_TRACKER_UPLOAD_RUNTIME
    if type(rt) ~= "table" then
        rt = { uploadSeq = 0, firstFullSnapshotAccepted = false }
        _G.__DENG_TRACKER_UPLOAD_RUNTIME = rt
    end
    rt.uploadSeq = tonumber(rt.uploadSeq) or 0
    rt.firstFullSnapshotAccepted = rt.firstFullSnapshotAccepted == true
    if type(LiveSafe) == "table" then
        LiveSafe.uploadSeq = tonumber(LiveSafe.uploadSeq) or rt.uploadSeq
        if LiveSafe.firstFullSnapshotAccepted == true then
            rt.firstFullSnapshotAccepted = true
        end
    end
    return rt
end

function attachSnapshotExecutionProof(payload, scanMeta)
    if type(payload) ~= "table" then return payload end
    local rt = ensureUploadRuntimeState()
    local nextSeq = rt.uploadSeq + 1
    rt.uploadSeq = nextSeq
    if type(LiveSafe) == "table" then
        LiveSafe.uploadSeq = nextSeq
    end
    payload.runId = thisRunId
    payload.executionSessionId = thisRunId
    payload.uploadSeq = nextSeq
    local accepted = rt.firstFullSnapshotAccepted
    if type(LiveSafe) == "table" and LiveSafe.firstFullSnapshotAccepted == true then
        accepted = true
    end
    payload.firstExecution = accepted ~= true`,
    );
  }

  if (!src.includes('uploadSeq = 0,')) {
    src = src.replace(
      '    lightSyncLoopStarted = false,\n    syncBeat = 0,',
      '    lightSyncLoopStarted = false,\n    uploadSeq = 0,\n    firstFullSnapshotAccepted = false,\n    syncBeat = 0,',
    );
  }

  if (!src.includes('UPLOAD_RUNTIME_ERROR stage=inventory_snapshot')) {
    src = src.replace(
      `function syncToDashboard()
    if LiveSafe.playerDataDirectMode then
        return LiveSafe.syncPlayerDataDashboard()
    end`,
      `function syncToDashboard()
    if LiveSafe.playerDataDirectMode then
        local ok, result = xpcall(LiveSafe.syncPlayerDataDashboard, debug.traceback)
        if not ok then
            warn(LOG, ("UPLOAD_RUNTIME_ERROR stage=inventory_snapshot err=%s"):format(tostring(result):sub(1, 500)))
            return false
        end
        return result
    end`,
    );
  }

  if (!src.includes('_G.__DENG_TRACKER_UPLOAD_RUNTIME')) {
    src = src.replace(
      '        LiveSafe.firstFullSnapshotAccepted = true\n    end',
      `        LiveSafe.firstFullSnapshotAccepted = true
        local rt = _G.__DENG_TRACKER_UPLOAD_RUNTIME
        if type(rt) == "table" then rt.firstFullSnapshotAccepted = true end
    end`,
    );
  }

  src = src.replace(/^local function /gm, 'function ');
  return src;
}

const { src: initial, from } = readSource();
const patched = patch(initial);
fs.mkdirSync(path.dirname(TARGET), { recursive: true });
fs.writeFileSync(TARGET, patched, 'utf8');

const topLocal = (patched.match(/^local /gm) || []).length;
console.log('PATCH_TOTEM_REGISTER_PROOF OK');
console.log('  source:', from);
console.log('  target:', TARGET);
console.log('  top-level local count:', topLocal);
console.log('  iife:', patched.includes(';(function()') && patched.includes('end)()'));
console.log('  upload ok proof:', patched.includes('totems=%d totemQty=%d'));
console.log('  classify helper:', patched.includes('LiveSafe.classifyNonStoneInventoryItem'));
console.log('  upload runtime:', patched.includes('ensureUploadRuntimeState') && !patched.includes('LiveSafe.uploadSeq = 0\nLiveSafe.firstFullSnapshotAccepted'));
