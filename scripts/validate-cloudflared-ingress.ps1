# Validate Cloudflare tunnel ingress reference and optionally copy to user profile.
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Ref = Join-Path $Root 'config\cloudflared-ingress.reference.yml'
$CF = 'C:\Users\Administrator\Desktop\DENG Control Panel\tools\cloudflared.exe'
$UserCfgDir = Join-Path $env:USERPROFILE '.cloudflared'
$UserCfg = Join-Path $UserCfgDir 'config.yml'

Write-Host "Reference config: $Ref"
& $CF --config $Ref tunnel ingress validate
Write-Host '--- rule: aio tracker upload ---'
& $CF --config $Ref tunnel ingress rule 'https://aio.deng.my.id/api/fishit-tracker/update-backpack'
Write-Host '--- rule: aio tracker read (web) ---'
& $CF --config $Ref tunnel ingress rule 'https://aio.deng.my.id/api/tracker/get-backpack/denghub2'
Write-Host '--- rule: aio fishit read (web, not ingest) ---'
& $CF --config $Ref tunnel ingress rule 'https://aio.deng.my.id/api/fishit-tracker/get-backpack/denghub2'
Write-Host '--- rule: aio login ---'
& $CF --config $Ref tunnel ingress rule 'https://aio.deng.my.id/login'
Write-Host '--- rule: tool tracker upload ---'
& $CF --config $Ref tunnel ingress rule 'https://tool.deng.my.id/api/fishit-tracker/update-backpack'

if (-not (Test-Path $UserCfg)) {
  New-Item -ItemType Directory -Force -Path $UserCfgDir | Out-Null
  Copy-Item $Ref $UserCfg
  Write-Host "Copied reference config to $UserCfg (token tunnel still uses remote dashboard rules until migrated)."
} else {
  Write-Host "Existing $UserCfg left unchanged. Apply path rules in Cloudflare Zero Trust dashboard for token tunnels."
}
