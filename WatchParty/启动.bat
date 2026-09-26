@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title WatchParty 启动
cd /d "%~dp0.." || exit /b 1

if not exist "%CD%\mpv.exe" (
    echo 未找到 mpv.exe，安装包可能不完整。
    pause
    exit /b 1
)

rem 日常启动只打开 mpv：角色与随包 AList 都由面板管理。房主模式下 mpv
rem 会自动拉起 AList；观看者不会在本机运行 AList，也不会开放任何端口。
start "mpv" "%CD%\mpv.exe" --idle=yes --force-window=yes
exit /b 0
