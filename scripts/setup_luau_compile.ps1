# Downloads Luau compiler binaries into _luau/ for tracker compile validation.
param(
    [string]$Version = "0.723",
    [string]$Dest = (Join-Path $PSScriptRoot ".." "_luau")
)
$ErrorActionPreference = "Stop"
$zipUrl = "https://github.com/luau-lang/luau/releases/download/$Version/luau-windows.zip"
$zipPath = Join-Path $env:TEMP "luau-windows-$Version.zip"
if (Test-Path (Join-Path $Dest "luau-compile.exe")) {
    Write-Host "Luau compiler already present at $Dest"
    exit 0
}
Write-Host "Downloading Luau $Version ..."
Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Expand-Archive -Path $zipPath -DestinationPath $Dest -Force
Write-Host "Installed luau-compile.exe to $Dest"
