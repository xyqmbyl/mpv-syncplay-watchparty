@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0..\.."
set "VIEWER_HOST=%~1"
if defined VIEWER_HOST (
    if exist "%CD%\tailscale.exe" (
        "%CD%\python.exe" "%CD%\portable_config\syncplay\tailscale_integration.py" --cli "%CD%\tailscale.exe" configure-viewer "%VIEWER_HOST%"
    ) else (
        "%CD%\python.exe" "%CD%\portable_config\syncplay\tailscale_integration.py" configure-viewer "%VIEWER_HOST%"
    )
) else (
    if exist "%CD%\tailscale.exe" (
        "%CD%\python.exe" "%CD%\portable_config\syncplay\tailscale_integration.py" --cli "%CD%\tailscale.exe" configure-viewer
    ) else (
        "%CD%\python.exe" "%CD%\portable_config\syncplay\tailscale_integration.py" configure-viewer
    )
)
set "VIEWER_RESULT=%ERRORLEVEL%"
echo.
if not "%VIEWER_RESULT%"=="0" (
    echo 请确认已登录 Tailscale，并填写房主提供的完整地址。
) else (
    echo 观看者配置已保存。请重新启动 mpv 后进入相同 Syncplay 房间。
)
if /I not "%~2"=="--no-pause" pause
exit /b %VIEWER_RESULT%
