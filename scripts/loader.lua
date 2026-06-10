-- ================================================================
--  DENG TRACKER — Safe Loader
--  Paste this into Roblox Studio LocalScript (or executor console).
--
--  This replaces the unsafe bare pattern:
--    loadstring(game:HttpGet("..."))()
--
--  It adds:
--    1. BOM stripping (handles UTF-8 BOM from editor saves)
--    2. Compile-check before calling (no nil-call crashes)
--    3. xpcall runtime traceback (real error, not "Line 1")
--    4. Cache-busting query string (avoids GitHub CDN stale cache)
-- ================================================================

local TRACKER_URL = "https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/dist/tracker.lua"

-- Cache-bust: append timestamp so GitHub CDN always serves the freshest version.
local url = TRACKER_URL .. "?v=" .. tostring(os.time())

print("[DENG LOADER] Fetching:", url)

-- 1. Fetch source safely
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

print("[DENG LOADER] Source length:", #source)
print("[DENG LOADER] Source preview:", string.sub(source, 1, 120))

-- 2. Strip UTF-8 BOM (EF BB BF) if present.
--    Without this, loadstring returns nil when the file was saved with BOM,
--    and loadstring(source)() crashes as "attempt to call a nil value".
source = source:gsub("^\239\187\191", "")

-- 3. Compile — check result BEFORE calling.
local fn, compileErr = loadstring(source)

if typeof(fn) ~= "function" then
    warn("[DENG LOADER] loadstring compile failed:", compileErr)
    return
end

print("[DENG LOADER] loadstring compiled OK")

-- 4. Run with full traceback so any runtime error shows the real location.
local okRun, runErr = xpcall(fn, debug.traceback)

if not okRun then
    warn("[DENG LOADER] tracker crashed:")
    warn(runErr)
else
    print("[DENG LOADER] tracker started OK")
end
