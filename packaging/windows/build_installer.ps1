[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$Iscc = "",
    [switch]$SkipPortableBuild
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

if (-not $SkipPortableBuild) {
    & (Join-Path $ScriptDir "build.ps1") -Python $Python
    if ($LASTEXITCODE -ne 0) {
        throw "Portable Windows build failed"
    }
}

$Version = (& $Python (Join-Path $ScriptDir "read_version.py")).Trim()
$DistDir = Join-Path $Root "dist"
$AppDir = Join-Path $DistDir "Vocal More"
$SetupPath = Join-Path $DistDir "Vocal-More-$Version-windows-x64-setup.exe"
$Icon = Join-Path $ScriptDir "VocalMore.ico"
$License = Join-Path $Root "LICENSE"
$Script = Join-Path $ScriptDir "vocal_more.iss"

if (-not (Test-Path (Join-Path $AppDir "Vocal More.exe"))) {
    throw "Packaged application folder is missing: $AppDir"
}
if (-not (Test-Path $Icon)) {
    & $Python (Join-Path $ScriptDir "make_icon.py") $Icon
}

if (-not $Iscc) {
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($command) {
        $Iscc = $command.Source
    }
}
if (-not $Iscc) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    )
    $Iscc = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
if (-not $Iscc -or -not (Test-Path $Iscc)) {
    throw "Inno Setup 6 compiler not found. Install it or pass -Iscc <path-to-ISCC.exe>."
}

Remove-Item -Force $SetupPath -ErrorAction SilentlyContinue
& $Iscc `
    "/DMyAppVersion=$Version" `
    "/DSourceDir=$AppDir" `
    "/DOutputDir=$DistDir" `
    "/DSourceLicense=$License" `
    $Script
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path $SetupPath)) {
    throw "Installer output is missing: $SetupPath"
}

Write-Output $SetupPath
