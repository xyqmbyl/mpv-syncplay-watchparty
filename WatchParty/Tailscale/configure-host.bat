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
set "TS_RESULT=1"
if exist "%PROJECT_ROOT%\tailscale.exe" (
    "%TS_PYTHON%" "%TS_SCRIPT%" --cli "%PROJECT_ROOT%\tailscale.exe" configure-host
) else (
    "%TS_PYTHON%" "%TS_SCRIPT%" configure-host
)
set "TS_RESULT=%ERRORLEVEL%"
echo.
if not "%TS_RESULT%"=="0" (
    echo 请先完成 Tailscale 登录，再重试。
) else (
    echo 房主配置已保存为 Tailscale IPv4 直连地址。
    echo.
    echo 还需要手动完成一次设备共享：
    echo Tailscale Admin Console ^> Machines ^> 房主设备 ^> Share
    echo 本工具不会启用 Tailscale Serve 或 Funnel。
    echo 完成后请重新启动 mpv 并打开 Syncplay 面板。
)
pause
exit /b %TS_RESULT%
