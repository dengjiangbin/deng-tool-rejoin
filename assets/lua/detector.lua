-- DENG Rejoin Detector v2
-- Session-aware, burst-heartbeat, gamejoin-gated Online proof.
-- Printed markers are parsed by the Python lifecycle monitor.
--
-- Marker formats (pipe-delimited, printed to logcat):
--   DENGRJN_START |session_id|userId|detector_started_at
--   DENGRJN_JOIN  |session_id|seq|userId|placeId|rootPlaceId|universeId|jobId
--   DENGRJN_HB    |placeId|rootPlaceId|universeId|jobId|alive|session_id|seq|userId
--   DENGRJN_FAIL  |session_id|reason
--   DENGRJN_EXIT  |session_id|uptime
--
-- The first 5 fields of DENGRJN_HB are kept identical to v1 for backward
-- compatibility with any monitor that has not yet been updated.

local _G_DENG = (getgenv and getgenv() or _G).DENG or {}
local PORT      = tonumber(_G_DENG.port)     or 52789
local TOKEN     = tostring(_G_DENG.token     or "")
local PKG       = tostring(_G_DENG.pkg       or "")
local INTERVAL  = tonumber(_G_DENG.interval) or 2

-- Burst schedule (seconds after gamejoin): 0, 1, 2, 5, 10, then INTERVAL
local BURST_SCHEDULE = {0, 1, 2, 5, 10}

-- ── session id ──────────────────────────────────────────────────────────────
-- Derived from game.JobId when available (stable for this server instance),
-- otherwise from a timestamp+random tuple.  Always a short opaque string.
local STARTED_AT = os.clock()  -- monotonic; tick() used for absolute times
local STARTED_TICK = tick()

local function _makeSessionId()
    local base = tostring(math.floor(STARTED_TICK * 1000) % 10000000)
    local r    = math.random(1000, 9999)
    return "s" .. base .. r
end

-- Will be refined once JobId is available (see below).
local SESSION_ID = _makeSessionId()
local _seq       = 0

local function _nextSeq()
    _seq = _seq + 1
    return _seq
end

-- ── emit ─────────────────────────────────────────────────────────────────────
local function emit(line)
    print(line)
end

-- ── helpers ──────────────────────────────────────────────────────────────────
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local function _localPlayer()
    return Players.LocalPlayer
end

local function _userId()
    local lp = _localPlayer()
    if lp then return tostring(lp.UserId) end
    return "0"
end

local function _placeId()
    local ok, v = pcall(function() return tostring(game.PlaceId or 0) end)
    return ok and v or "0"
end

local function _rootPlaceId()
    -- RootPlaceId is not a direct property everywhere; fall back to PlaceId.
    local ok, v = pcall(function()
        return tostring(game:GetService("RootInstance") and game.PlaceId or game.PlaceId)
    end)
    return ok and v or _placeId()
end

local function _universeId()
    local ok, v = pcall(function() return tostring(game.GameId or 0) end)
    return ok and v or "0"
end

local function _jobId()
    local ok, v = pcall(function() return tostring(game.JobId or "") end)
    return (ok and v) or ""
end

-- ── emit start marker immediately ────────────────────────────────────────────
-- This is the very first line the monitor uses to detect script execution.
-- It does NOT prove Online.
emit(string.format(
    "DENGRJN_START|%s|%s|%.3f",
    SESSION_ID, _userId(), STARTED_TICK
))

-- ── wait for real game context ────────────────────────────────────────────────
-- LocalPlayer must exist, game must be loaded, placeId must be non-zero.
-- An early script-start alone must NOT prove Online.
local function _gameContextValid()
    if not game:IsLoaded() then return false end
    local lp = _localPlayer()
    if not lp then return false end
    if (game.PlaceId or 0) == 0 then return false end
    -- jobId is populated once the client is fully in a live server
    if (_jobId() or "") == "" then return false end
    return true
end

local CONTEXT_DEADLINE = tick() + 120
repeat
    task.wait(0.1)
until _gameContextValid() or tick() > CONTEXT_DEADLINE

if not _gameContextValid() then
    emit(string.format("DENGRJN_FAIL|%s|no_game_context_timeout", SESSION_ID))
    return
end

-- Refine session id using JobId now that it is available (deterministic).
local jid = _jobId()
if jid and #jid > 4 then
    -- Take last 8 hex chars of UUID-style JobId for a short stable id.
    SESSION_ID = "j" .. jid:sub(-8):gsub("-", "")
end

local PLACE_ID      = _placeId()
local ROOT_PLACE_ID = _rootPlaceId()
local UNIVERSE_ID   = _universeId()
local JOB_ID        = jid

-- ── emit gamejoin marker ──────────────────────────────────────────────────────
-- ONLY emitted once real game context is confirmed.  This is the primary
-- proof-of-Online signal for the lifecycle monitor.
emit(string.format(
    "DENGRJN_JOIN|%s|%d|%s|%s|%s|%s|%s",
    SESSION_ID, _nextSeq(),
    _userId(), PLACE_ID, ROOT_PLACE_ID, UNIVERSE_ID, JOB_ID
))

-- ── heartbeat ─────────────────────────────────────────────────────────────────
-- v1-compatible logcat format with v2 extension fields at the end.
local function _emitHB(alive)
    local aliveFlag = alive and "1" or "0"
    -- v1 compat: fields 1-5 unchanged (placeId|rootPlaceId|universeId|jobId|alive)
    -- v2 extension: |session_id|seq|userId appended
    emit(string.format(
        "DENGRJN_HB|%s|%s|%s|%s|%s|%s|%d|%s",
        PLACE_ID, ROOT_PLACE_ID, UNIVERSE_ID, JOB_ID, aliveFlag,
        SESSION_ID, _nextSeq(), _userId()
    ))
end

-- Optional HTTP push (best-effort; logcat is authoritative).
local function _pushHB(alive)
    local ok, _ = pcall(function()
        local url = string.format("http://127.0.0.1:%d/hb", PORT)
        local body = string.format(
            '{"token":"%s","pkg":"%s","alive":%s,"placeId":%s,'
            .. '"rootPlaceId":%s,"universeId":%s,"jobId":"%s",'
            .. '"sessionId":"%s","seq":%d,"userId":%s}',
            TOKEN, PKG,
            alive and "true" or "false",
            PLACE_ID, ROOT_PLACE_ID, UNIVERSE_ID, JOB_ID,
            SESSION_ID, _seq, _userId()
        )
        -- Use syn.request if available, otherwise game:HttpGet on the GET endpoint.
        if syn and syn.request then
            syn.request({Url=url, Method="POST", Body=body,
                Headers={["Content-Type"]="application/json"}})
        elseif http and http.request then
            http.request({Url=url, Method="POST", Body=body,
                Headers={["Content-Type"]="application/json"}})
        end
    end)
    return ok
end

local function _sendHB(alive)
    _emitHB(alive)
    _pushHB(alive)
end

-- ── burst + loop ──────────────────────────────────────────────────────────────
-- First burst at t=0 (emitted immediately after gamejoin), then at 1, 2, 5, 10s,
-- then normal interval.
_sendHB(true)  -- 0s burst

local _burstIdx  = 2  -- already sent index 1 (0s); next is index 2 (1s)
local _lastHbAt  = tick()

local function _nextWait()
    if _burstIdx <= #BURST_SCHEDULE then
        local delta = BURST_SCHEDULE[_burstIdx] - BURST_SCHEDULE[_burstIdx - 1]
        _burstIdx = _burstIdx + 1
        return math.max(0.05, delta)
    end
    return INTERVAL
end

local function _runLoop()
    while true do
        task.wait(_nextWait())
        if not _gameContextValid() then
            _sendHB(false)
            break
        end
        _sendHB(true)
        _lastHbAt = tick()
    end
end

local ok_loop, err_loop = pcall(_runLoop)
if not ok_loop then
    emit(string.format("DENGRJN_ERROR|%s|%s", SESSION_ID,
        tostring(err_loop or "unknown"):sub(1, 120)))
end

emit(string.format("DENGRJN_EXIT|%s|%.1f", SESSION_ID, tick() - STARTED_TICK))
