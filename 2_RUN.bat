@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title Fish Speech S2-Pro Portable
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0portable\bootstrap.ps1" -Mode Run
set "result=%errorlevel%"
if not "%result%"=="0" echo ERROR. Run 1_INSTALL_AND_RUN.bat first, or check logs.
pause
exit /b %result%
