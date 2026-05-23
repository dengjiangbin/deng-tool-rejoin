# Start Stability Guardrails

These rules protect the public `deng-rejoin` Start / launch-all flow from the
Termux native segfault class seen in probe `p-b66c244cad`.

## Hard Requirements

1. Inspect the probe first before changing Start, layout, supervisor, render, or Android command code.
2. Start must not call `os.system("clear")`; terminal redraw uses ANSI through `safe_io.safe_clear_screen()`.
3. Android/root subprocess work must go through the serialized runner in `agent.android`.
4. Termux curl HTTP work must share the same subprocess lock as Android/root commands.
5. Start owns exactly one `WatchdogSupervisor` loop and one render callback. Do not add render, watchdog, or status worker threads.
6. Background workers must not write directly to stdout.
7. Start must record phase context in `data/logs/crash_faulthandler.log`.
8. The public Start supervisor must not invoke Auto Execute, `/execute`, clipboard paste, input text, or client-side script execution.
9. Landscape and Portrait layout state must be isolated by mode; stale bounds from the previous mode must be cleared before computing new bounds.
10. Do not add a legacy layout fallback that reuses bounds from another screen mode.

## Required Regression Coverage

Any future layout, Start, supervisor, render, subprocess, or mode-switch change
must include:

1. Landscape Start regression test.
2. Portrait Start/layout regression test.
3. Start lifecycle test.
4. Start -> Ctrl+C -> Start test.
5. Launch-all no-segfault guard test.
6. Landscape slot order test.
7. Portrait changes do not affect landscape test.
8. Bounds readback failure does not crash Start test.
9. Render exception does not crash Start test.
10. Android/root/curl subprocess serialization test.

## Proof Before "Fixed"

A final fixed report must include:

1. Probe evidence and last log line before the crash.
2. Exact file/function/code path that caused the crash.
3. Why the regression returned.
4. What guard prevents it from returning.
5. Unit test commands and results.
6. Artifact SHA if the agent artifact changed.
7. Real Termux/probe proof: launch all packages, run 10 minutes, Ctrl+C, Start again, mode switch, killed package Dead -> relaunch.

If real Termux/cloud-phone verification was not run, the report must explicitly
say so and must not claim full live verification.
