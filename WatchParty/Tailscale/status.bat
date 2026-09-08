@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."
"%CD%\python.exe" "%CD%\portable_config\syncplay\tailscale_integration.py" status
pause
