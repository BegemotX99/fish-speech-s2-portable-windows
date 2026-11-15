@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Fish Speech S2-Pro - Diagnostics
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0portable\bootstrap.ps1" -Mode Doctor
set "result=%errorlevel%"
pause
exit /b %result%
