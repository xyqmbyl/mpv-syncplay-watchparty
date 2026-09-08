@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
title WatchParty 启动
cd /d "%~dp0.." || exit /b 1
set "WP_PYTHON=%CD%\python.exe"
set "WP_SCRIPT=%CD%\portable_config\syncplay\watchparty_setup.py"

if not exist "%WP_PYTHON%" (
    echo 未找到项目内 Python，请先运行 WatchParty\房主首次运行.bat。
    pause
    exit /b 1
)

rem 日常启动只保证 AList 在运行（已在运行则直接复用，不会重复启动），
rem 不重复完整向导；首次使用请先运行 房主首次运行.bat。
"%WP_PYTHON%" "%WP_SCRIPT%" ensure-alist
if errorlevel 1 (
    echo AList 未能启动，请先运行 WatchParty\房主首次运行.bat。
    pause
    exit /b 1
)

start "mpv" "%CD%\mpv.exe" --idle=yes --force-window=yes
exit /b 0
