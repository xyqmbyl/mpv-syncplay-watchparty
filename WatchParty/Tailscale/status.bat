@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0..\.."
if exist "%CD%\tailscale.exe" (
    "%CD%\python.exe" "%CD%\portable_config\syncplay\tailscale_integration.py" --cli "%CD%\tailscale.exe" status
) else (
    "%CD%\python.exe" "%CD%\portable_config\syncplay\tailscale_integration.py" status
)
set "TS_RESULT=%ERRORLEVEL%"
pause
exit /b %TS_RESULT%
