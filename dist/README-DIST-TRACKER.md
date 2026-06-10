# Protected tracker release (`dist/tracker.lua`)

Public users load **only** the obfuscated dist artifact from GitHub:

```lua
loadstring(game:HttpGet("https://raw.githubusercontent.com/dengjiangbin/deng-tool-rejoin/main/dist/tracker.lua"))()
```

## Dev workflow

1. Edit raw dev source: `tracker.lua` (repo root, dev-only).
2. Validate raw compile:

   ```powershell
   cd "C:\Users\Administrator\Desktop\DENG Tool Rejoin"
   node scripts/validate_tracker_compile.js
   ```

3. Obfuscate `tracker.lua` with your obfuscator (Luraph or equivalent).
4. Save output as:

   ```text
   C:\Users\Administrator\Desktop\DENG Tool Rejoin\dist\tracker.lua
   ```

5. Validate dist + secrets:

   ```powershell
   node scripts/validate_luraph_dist.js
   node scripts/audit_tracker_secrets.js
   ```

6. Commit and push `dist/tracker.lua`.

## Notes

- Do **not** publish root `tracker.lua` in the website copy box.
- Do **not** use `dist/tracker.luraph.lua` — the public path is `dist/tracker.lua`.
- Backend upload validation remains required; obfuscation does not replace server checks.
