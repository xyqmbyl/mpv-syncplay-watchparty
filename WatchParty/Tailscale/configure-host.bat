@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0..\.."
set "TS_RESULT=1"
if exist "%CD%\tailscale.exe" (
    "%CD%\python.exe" "%CD%\portable_config\syncplay\tailscale_integration.py" --cli "%CD%\tailscale.exe" configure-host
) else (
    "%CD%\python.exe" "%CD%\portable_config\syncplay\tailscale_integration.py" configure-host
)
set "TS_RESULT=%ERRORLEVEL%"
echo.
if not "%TS_RESULT%"=="0" (
    echo 请先完成 Tailscale 登录，再重试。
) else (
    echo 房主配置已保存。请重新启动 mpv 后打开 Syncplay 面板。
)
pause
exit /b %TS_RESULT%
