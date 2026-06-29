--[[
    DENG Tool: Rejoin — in-game detection heartbeat.

    Upload this file to:
        https://raw.githubusercontent.com/dengjiangbin/global/main/detector.lua

    The injected `deng.txt` bootstrap pins per-package config into
    getgenv().DENG and then loadstrings this script.  While the player is
    genuinely in a live server this loop emits a tiny heartbeat (placeId /
    rootPlaceId / universeId / jobId) every few seconds by TWO channels:

      1. A `print(...)` line tagged "DENGRJN_HB|" — this lands in Android
         logcat under the package's own PID, which the agent reads via the
         same PID-scoped `logcat` dump that already detects "online" in ~1s.
         This is the PRIMARY channel because it works even on cloud-phone
         clones where the loopback HTTP port is sandboxed/blocked.
      2. A best-effort HTTP POST to the agent's loopback detection worker
         (kept as a fallback for environments where loopback is allowed).

    When the player dies, disconnects, is kicked, teleports to a different
    game/server, or hits a mid-game captcha, the DataModel unloads and BOTH
    channels stop / change — which is exactly how the watchdog detects
    "dead", "wrong server", and "recovered" within ~10s (heartbeat-loss),
    catching GL/WebView error dialogs that dumpsys/uiautomator cannot read.

    Everything is pcall-wrapped: this script must never error or interrupt
    the user's own auto-exec scripts.
]]

local CFG = (getgenv and getgenv() or _G).DENG or {}
local PORT = tonumber(CFG.port) or 52789
local TOKEN = tostring(CFG.token or "")
local PKG = tostring(CFG.pkg or "")
local INTERVAL = tonumber(CFG.interval) or 5
if INTERVAL < 2 then INTERVAL = 2 end
local URL = "http://127.0.0.1:" .. tostring(PORT) .. "/h"

-- Only one detector loop per game instance.
if (getgenv and getgenv() or _G).__DENG_DETECTOR_RUNNING then return end
(getgenv and getgenv() or _G).__DENG_DETECTOR_RUNNING = true

local task = task or { wait = wait }

local function http_post(body)
    local req = (syn and syn.request)
        or (http and http.request)
        or http_request
        or (fluxus and fluxus.request)
        or request
    if req then
        return pcall(req, {
            Url = URL,
            Method = "POST",
            Headers = { ["Content-Type"] = "application/json" },
            Body = body,
        })
    end
    -- Last resort: HttpService (loopback may be blocked on some clients).
    local HttpService = game:GetService("HttpService")
    return pcall(function()
        return HttpService:PostAsync(URL, body, Enum.HttpContentType.ApplicationJson)
    end)
end

-- PRIMARY channel: a logcat-visible heartbeat line.  `print` from the
-- executor surfaces in Android logcat as "[FLog::Output] ..." under the
-- Roblox process PID, so the agent reads it with the same reliable
-- PID-scoped `logcat` dump it uses for online detection — no loopback port
-- needed (works on sandboxed cloud-phone clones).  Format is pipe-delimited
-- and easy to parse:  DENGRJN_HB|placeId|rootPlaceId|universeId|jobId|alive
local function hb_log(alive)
    local placeId, universeId, jobId = 0, 0, ""
    pcall(function() placeId = tonumber(game.PlaceId) or 0 end)
    pcall(function() universeId = tonumber(game.GameId) or 0 end)
    pcall(function() jobId = tostring(game.JobId or "") end)
    pcall(function()
        print(
            "DENGRJN_HB|" .. tostring(placeId)
                .. "|" .. tostring(placeId)
                .. "|" .. tostring(universeId)
                .. "|" .. tostring(jobId)
                .. "|" .. (alive and "1" or "0")
        )
    end)
end

local function send(alive)
    local payload = {
        k = TOKEN,
        pkg = PKG,
        alive = alive,
        placeId = game.PlaceId,
        universeId = game.GameId,
        jobId = game.JobId or "",
        user = "",
    }
    pcall(function()
        local Players = game:GetService("Players")
        local lp = Players and Players.LocalPlayer
        if lp then payload.user = lp.Name end
    end)
    local HttpService = game:GetService("HttpService")
    local ok, body = pcall(function() return HttpService:JSONEncode(payload) end)
    if ok and body then http_post(body) end
end

-- Wait until the place is really loaded before the first heartbeat.
pcall(function()
    if not game:IsLoaded() then
        game.Loaded:Wait()
    end
end)

while true do
    local alive = true
    pcall(function()
        alive = (tonumber(game.PlaceId) or 0) > 0
    end)
    pcall(hb_log, alive)
    pcall(send, alive)
    pcall(function() task.wait(INTERVAL) end)
end
