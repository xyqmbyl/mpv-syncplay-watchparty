@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
title WatchParty 首次运行
cd /d "%~dp0.." || exit /b 1
set "WP_PYTHON=%CD%\python.exe"
set "WP_SCRIPT=%CD%\portable_config\syncplay\watchparty_setup.py"

if not exist "%WP_PYTHON%" (
    echo 未找到内置 Python，安装包可能不完整。
    pause
    exit /b 1
)
if not exist "%WP_SCRIPT%" (
    echo 未找到 watchparty_setup.py，安装包可能不完整。
    pause
    exit /b 1
)

rem 合并包不预设角色：本向导只准备联机所需的 Tailscale，房主还是观看者
rem 在 mpv 面板的「联机 -^> 运行模式」里选择。
"%WP_PYTHON%" "%WP_SCRIPT%" bootstrap
set "WP_RESULT=%ERRORLEVEL%"
echo.
if not "%WP_RESULT%"=="0" (
    echo 首次运行检查未完成，请按上面的提示处理后重新双击本文件。
) else (
    echo 提示：以后日常使用只需双击 WatchParty\启动.bat。
)
pause
exit /b %WP_RESULT%
