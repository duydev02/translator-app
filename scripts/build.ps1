#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Build Translator.exe from the current source.

.DESCRIPTION
    Wraps `pyinstaller Translator.spec` with a clean-first option and a bit
    of nicer output. Run from any directory — the script locates the project
    root relative to itself.

.PARAMETER Clean
    Remove build/ and dist/ before rebuilding.

.EXAMPLE
    .\scripts\build.ps1
    .\scripts\build.ps1 -Clean
#>

[CmdletBinding()]
param(
    [switch] $Clean
)

$ErrorActionPreference = 'Stop'

# Locate project root (parent of this script's directory)
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

Write-Host "Building from: $projectRoot" -ForegroundColor Cyan

if ($Clean) {
    Write-Host "Cleaning build/ dist/ ..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
}

# Sanity-check PyInstaller
try {
    $v = & pyinstaller --version 2>$null
    Write-Host "PyInstaller $v" -ForegroundColor DarkGray
} catch {
    Write-Host "ERROR: pyinstaller is not installed. Run: pip install pyinstaller" -ForegroundColor Red
    exit 1
}

& pyinstaller Translator.spec
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

$exe = Join-Path $projectRoot "dist\Translator.exe"
if (Test-Path $exe) {
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "✔  Built: $exe  ($size MB)" -ForegroundColor Green
} else {
    Write-Host "Build completed but Translator.exe not found." -ForegroundColor Yellow
}
