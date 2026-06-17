$ErrorActionPreference = 'Stop'
$root = 'C:\Users\Administrator\Desktop\DENG Tool Rejoin'
$ts = Get-Date -Format 'yyyyMMdd-HHmm'
$dest = "C:\backups\deng-tracker-before-scale-$ts"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Write-Output "BACKUP_DEST=$dest"

# Live session shards + manifests + image-cache indexes (writable production state)
robocopy "$root\site\data" "$dest\site_data" /E /R:1 /W:1 /NFL /NDL /NP | Out-Null

# PM2 + ecosystem configs
Copy-Item "$root\ecosystem.site.json" "$dest\" -Force
Copy-Item "$root\ecosystem.config.js" "$dest\" -Force
if (Test-Path "$root\pm2_jlist_before_scale_rebuild.json") { Copy-Item "$root\pm2_jlist_before_scale_rebuild.json" "$dest\" -Force }

# Writable global catalog DB (WAL) if present
if (Test-Path "$root\site\data\fishit_global.db") { Copy-Item "$root\site\data\fishit_global.db*" "$dest\" -Force }

$size = (Get-ChildItem $dest -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Output ("BACKUP_BYTES=" + $size)
Write-Output "BACKUP_DONE"
