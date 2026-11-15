@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Fish Speech S2-Pro Portable - Setup and Run
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0portable\bootstrap.ps1" -Mode InstallAndRun
set "result=%errorlevel%"
if not "%result%"=="0" echo ERROR. See the logs folder and MANUAL_RU.html.
pause
exit /b %result%
