# Protected tracker release (`dist/tracker.lua`)

Public users load **only** the obfuscated dist artifact from GitHub:

```lua
loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/dist/tracker.lua"))()
```

Root `tracker.lua` is **not** part of the public repo. Raw development source lives in private/local storage only (see `.gitignore`).

## Release workflow

1. Edit private raw dev source (example local path):

   ```text
   C:\Users\Administrator\Desktop\DENG PRIVATE SOURCE\fishtracker\tracker.lua
   ```

   Or set `TRACKER_RAW_SOURCE_PATH` / `PRIVATE_TRACKER_SOURCE_PATH` to your private copy.

2. Validate raw compile (skipped automatically if private source is absent):

   ```powershell
   cd "C:\Users\Administrator\Desktop\DENG Tool Rejoin"
   $env:TRACKER_RAW_SOURCE_PATH = "C:\Users\Administrator\Desktop\DENG PRIVATE SOURCE\fishtracker\tracker.lua"
   node scripts/validate_tracker_compile.js
   ```

3. Obfuscate the private raw source with your obfuscator (Luraph or equivalent).

4. Save output as:

   ```text
   C:\Users\Administrator\Desktop\DENG Tool Rejoin\dist\tracker.lua
   ```

5. Validate dist + secrets:

   ```powershell
   node scripts/validate_luraph_dist.js
   node scripts/audit_tracker_secrets.js
   node scripts/verify_tracker_github_raw.js
   ```

6. Commit and push **`dist/tracker.lua` only** — never commit root `tracker.lua`.

## Notes

- Public website copy box and loader use **`dist/tracker.lua` only**.
- Do **not** publish root `tracker.lua` on the public GitHub branch.
- Do **not** use `dist/tracker.luraph.lua` — the public path is `dist/tracker.lua`.
- Older GitHub commits may still contain historical raw source unless history is rewritten.
- Backend upload validation remains required; obfuscation does not replace server checks.
