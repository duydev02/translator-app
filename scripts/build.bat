@echo off
REM Build Translator.exe using PyInstaller.
REM Run from any directory — the script cd's to the project root.

setlocal

pushd "%~dp0.."

where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo ERROR: pyinstaller is not installed.
    echo    Install with:  pip install pyinstaller
    popd & exit /b 1
)

if /I "%~1"=="--clean" (
    echo Cleaning build and dist folders...
    if exist build rmdir /s /q build
    if exist dist  rmdir /s /q dist
)

pyinstaller Translator.spec
if errorlevel 1 (
    echo Build failed.
    popd & exit /b 1
)

if exist dist\Translator.exe (
    echo.
    echo OK - Built: %CD%\dist\Translator.exe
)

popd
endlocal
