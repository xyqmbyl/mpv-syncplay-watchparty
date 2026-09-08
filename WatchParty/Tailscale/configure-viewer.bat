@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0..\.." || (
    echo 无法定位 mpv 项目目录，请从完整的便携版目录运行此脚本。
    pause
    exit /b 1
)
set "PROJECT_ROOT=%CD%"
set "TS_PYTHON=%PROJECT_ROOT%\python.exe"
set "TS_SCRIPT=%PROJECT_ROOT%\portable_config\syncplay\tailscale_integration.py"
if not exist "%TS_PYTHON%" (
    echo 未找到项目内 Python，请使用完整的 mpv 便携版目录，而不是源码仓库。
    pause
    exit /b 1
)
if not exist "%TS_SCRIPT%" (
    echo 未找到 Syncplay Tailscale 模块，请重新解压完整项目。
    pause
    exit /b 1
)
set "VIEWER_HOST=%~1"
if defined VIEWER_HOST (
    if exist "%PROJECT_ROOT%\tailscale.exe" (
        "%TS_PYTHON%" "%TS_SCRIPT%" --cli "%PROJECT_ROOT%\tailscale.exe" configure-viewer "%VIEWER_HOST%"
    ) else (
        "%TS_PYTHON%" "%TS_SCRIPT%" configure-viewer "%VIEWER_HOST%"
    )
) else (
    if exist "%PROJECT_ROOT%\tailscale.exe" (
        "%TS_PYTHON%" "%TS_SCRIPT%" --cli "%PROJECT_ROOT%\tailscale.exe" configure-viewer
    ) else (
        "%TS_PYTHON%" "%TS_SCRIPT%" configure-viewer
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
