[CmdletBinding()]
param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path

if (-not $Python) {
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
}
if (-not (Test-Path $Python)) {
    throw "Python interpreter not found: $Python"
}

$DistDir = Join-Path $Root "dist"
$WorkDir = Join-Path $Root "build\windows"
$AppDir = Join-Path $DistDir "Vocal More"
$Version = (& $Python (Join-Path $ScriptDir "read_version.py")).Trim()
if (-not $Version) {
    throw "Could not read project version"
}
$Archive = Join-Path $DistDir "Vocal-More-$Version-windows-x64.zip"

Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force $AppDir -ErrorAction SilentlyContinue
Remove-Item -Force $Archive -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $DistDir | Out-Null

Push-Location $Root
try {
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --workpath $WorkDir `
        --distpath $DistDir `
        (Join-Path $ScriptDir "vocal_more.spec")
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$Executable = Join-Path $AppDir "Vocal More.exe"
$License = Join-Path $AppDir "LICENSE.txt"
$WindowsReadme = Join-Path $AppDir "README-Windows.md"
if (-not (Test-Path $Executable)) {
    throw "Packaged executable is missing: $Executable"
}
Copy-Item (Join-Path $Root "LICENSE") $License -Force
Copy-Item (Join-Path $Root "docs\windows.md") $WindowsReadme -Force
if (-not (Test-Path $License)) {
    throw "Packaged GPL license is missing: $License"
}

Compress-Archive -Path $AppDir -DestinationPath $Archive -CompressionLevel Optimal
Write-Output $Archive
