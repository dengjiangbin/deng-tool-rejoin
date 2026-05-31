-- ================================================================
--  Fish It — Real-Time Catch Hook  (Event-Driven)
--
--  This script intercepts the server's "fish caught" RemoteEvent
--  using OnClientEvent and forwards every catch to the dashboard
--  in real-time — no polling, fires only when a fish is actually caught.
--
--  Usage:
--    loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/fishit_hook.lua"))()
--
--  Kill switch (stop listening at any time):
--    _G.StopAutoFish = true
--
--  Requirements: an executor with HTTP request support
-- ================================================================

-- ── Global Kill Switch ────────────────────────────────────────────
-- Set _G.StopAutoFish = true in the executor console to stop the hook
-- without re-executing the script. Defaults to false (active).
_G.StopAutoFish = _G.StopAutoFish or false

-- ── Executor HTTP wrapper (auto-detects common executors) ─────────
_G.httpRequest = (syn and syn.request)
    or (http and http.request)
    or http_request
    or (fluxus and fluxus.request)
    or request

-- ── Configuration ─────────────────────────────────────────────────
-- The endpoint that receives catch data.
-- Replace with YOUR_WEBHOOK_URL_HERE for a generic webhook target.
local HOOK_URL      = "https://tool.deng.my.id/api/tracker/update-backpack"
local LOG_PREFIX    = "[FishHook]"

-- ── Roblox service references ─────────────────────────────────────
local Players          = game:GetService("Players")
local HttpService      = game:GetService("HttpService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local LocalPlayer = Players.LocalPlayer

-- ── Session catch log ─────────────────────────────────────────────
-- Accumulated fish caught THIS session, grouped by name.
-- Dictionary keyed by fish name → item entry.
-- caughtOrder preserves the first-seen sequence for a stable JSON array.
local caughtFish  = {}
local caughtOrder = {}

-- Last JSON string sent — delta-check avoids duplicate HTTP calls.
local lastSentStr = ""

-- ── Send the current catch log to the dashboard ───────────────────
-- Builds the items array from caughtFish, JSON-encodes it, and fires
-- an async HTTP POST. Wrapped in pcall so a slow or downed server
-- never yields the fishing loop.
local function sendCatchLog()
    -- Build items array in stable first-seen order
    local items = {}
    for _, name in ipairs(caughtOrder) do
        items[#items + 1] = caughtFish[name]
    end

    local payload = {
        username  = LocalPlayer.Name,
        userId    = LocalPlayer.UserId,
        items     = items,
        timestamp = os.time(),
    }

    -- Delta-check: don't fire if nothing changed since last send
    local encoded = HttpService:JSONEncode(payload)
    if encoded == lastSentStr then return end
    lastSentStr = encoded

    -- Async fire — never blocks the calling thread
    task.spawn(function()
        local ok, err = pcall(function()
            _G.httpRequest({
                Url     = HOOK_URL,
                Method  = "POST",
                Headers = { ["Content-Type"] = "application/json" },
                Body    = encoded,
            })
        end)
        if not ok then
            warn(LOG_PREFIX, "HTTP send error:", err)
        end
    end)
end

-- ── Core hook: connect ONE RemoteEvent safely ─────────────────────
-- Only RemoteEvents are connected — RemoteFunctions are deliberately
-- skipped. Connecting OnClientEvent to a RemoteFunction triggers a
-- fatal OnClientInvoke error that crashes the script.
local function hookRemoteEvent(obj)
    -- STRICT type guard — must be a RemoteEvent, nothing else
    if not obj:IsA("RemoteEvent") then return end

    -- pcall shields the connection so that if the game developer
    -- changes the network structure, we fail silently instead of
    -- tripping the game's anti-cheat monitoring.
    pcall(function()
        obj.OnClientEvent:Connect(function(arg1, arg2, arg3, arg4)

            -- ① Kill switch — stop processing immediately if requested
            if _G.StopAutoFish then return end

            -- ② Signature filter — only process fish-catch packets:
            --    arg3 must be a string (the fish name)
            --    arg4 must be a table containing a Weight key
            --    All other network traffic is silently discarded.
            if type(arg3) ~= "string"
            or type(arg4) ~= "table"
            or arg4.Weight == nil then
                return
            end

            local fishName = arg3
            local weight   = tonumber(arg4.Weight) or 0

            -- ③ Accumulate into the session catch log
            if caughtFish[fishName] then
                -- Known fish — increment stack and add weight
                caughtFish[fishName].weight = caughtFish[fishName].weight + weight
                caughtFish[fishName].amount = caughtFish[fishName].amount + 1
            else
                -- First time catching this species this session
                caughtFish[fishName] = {
                    name   = fishName,
                    weight = weight,
                    amount = 1,
                    tab    = "CatchLog",  -- distinguishes from inventory scanner
                }
                caughtOrder[#caughtOrder + 1] = fishName
            end

            -- ④ Console feedback for the executor window
            print(("%s Caught: %s (%.2f kg) | Session total: x%d"):format(
                LOG_PREFIX,
                fishName,
                weight,
                caughtFish[fishName].amount
            ))

            -- ⑤ Push the updated catch log to the dashboard
            sendCatchLog()
        end)
    end)
end

-- ── Scan ReplicatedStorage recursively ───────────────────────────
-- GetDescendants covers nested Folders/Models where games often
-- hide their RemoteEvents under random hash names.
-- Each object is passed through hookRemoteEvent which silently
-- ignores everything that isn't a RemoteEvent.
for _, obj in pairs(ReplicatedStorage:GetDescendants()) do
    hookRemoteEvent(obj)
end

-- ── Watch for late-loaded RemoteEvents ───────────────────────────
-- Some games lazy-load their network objects after the initial load.
-- DescendantAdded fires whenever a new child is added anywhere
-- under ReplicatedStorage, so we never miss a late-arriving event.
ReplicatedStorage.DescendantAdded:Connect(function(obj)
    hookRemoteEvent(obj)
end)

-- ── Ready ─────────────────────────────────────────────────────────
print(LOG_PREFIX, ("Hook active for %s (ID: %d)"):format(
    LocalPlayer.Name, LocalPlayer.UserId))
print(LOG_PREFIX, "Listening for fish-catch RemoteEvents...")
print(LOG_PREFIX, "Dashboard: https://tool.deng.my.id/tracker")
print(LOG_PREFIX, "To stop: _G.StopAutoFish = true")
