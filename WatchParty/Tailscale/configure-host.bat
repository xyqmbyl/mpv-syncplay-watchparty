@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0..\.."
"%CD%\python.exe" "%CD%\portable_config\syncplay\tailscale_integration.py" configure-host
echo.
if errorlevel 1 (
    echo 请先完成 Tailscale 登录，再重试。
) else (
    echo 房主配置已保存。请重新启动 mpv 后打开 Syncplay 面板。
)
pause
