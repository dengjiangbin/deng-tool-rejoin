-- ================================================================
--  DENG TRACKER — Safe Loader (BLOCKER10ZT3)
--  Paste into Roblox Studio LocalScript or executor console.
--
--  Canonical dist URL (deng-fishtracker-dist):
--    https://raw.githubusercontent.com/dengjiangbin/deng-fishtracker-dist/main/dist/tracker.lua
-- ================================================================

local LOADER_BUILD = "BLOCKER10ZT5_RUNTIME_LINE_FIX_2026_06_10"
local TRACKER_URL = "https://raw.githubusercontent.com/dengjiangbin/deng-fishtracker-dist/main/dist/tracker.lua"
local url = TRACKER_URL .. "?v=" .. LOADER_BUILD

print("LOADER_BUILD=" .. LOADER_BUILD)
print("FETCH_URL=" .. url)

local okFetch, source = pcall(function()
    return game:HttpGet(url)
end)

if not okFetch then
    warn("[DENG LOADER] HttpGet failed:", source)
    return
end

if typeof(source) ~= "string" then
    warn("[DENG LOADER] HttpGet returned non-string:", typeof(source))
    return
end

source = source:gsub("^\239\187\191", "")

local fetchedBuild = source:match("DENG protected tracker dist | ([^\n%]]+)") or "unknown"
print("FETCHED_TRACKER_BUILD=" .. tostring(fetchedBuild))

if not tostring(fetchedBuild):find("BLOCKER10ZT5", 1, true)
    and not tostring(fetchedBuild):find("BLOCKER10ZT4", 1, true)
    and not tostring(fetchedBuild):find("BLOCKER10ZT3", 1, true) then
    warn("[DENG LOADER] stale dist fetched — expected BLOCKER10ZT5, got:", fetchedBuild)
end

local fn, compileErr = loadstring(source)
if typeof(fn) ~= "function" then
    warn("[DENG LOADER] loadstring compile failed:", compileErr)
    return
end

local okRun, runErr = xpcall(fn, debug.traceback)
if not okRun then
    warn("[DENG LOADER] tracker crashed:")
    warn(runErr)
else
    print("[DENG LOADER] tracker started OK")
end
