'use strict';

const CLEAN_TRACKER_LOADSTRING = 'loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/tracker.lua"))()';

const DEBUG_TRACKER_LOADSTRING = 'loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/tracker.lua?t=" .. os.time()))()';

module.exports = {
  CLEAN_TRACKER_LOADSTRING,
  DEBUG_TRACKER_LOADSTRING,
};
