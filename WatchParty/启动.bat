@echo off
rem 注意：本文件必须保存为 GBK/ANSI（zh-CN）编码，不要改存 UTF-8！
rem Win11 24H2+ 上 cmd 解析含多字节 UTF-8 的批处理会错位（详见 首次运行.bat）。
setlocal DisableDelayedExpansion
title WatchParty 启动
cd /d "%~dp0.." || exit /b 1

rem 启动器日志：每次双击都往 logs\launcher.log 追加一段带时间戳的记录，方便排查
rem 「双击没反应」。目录不存在就创建；任何写入失败都不影响下面的启动流程和退出码。
set "WP_LOGS=%CD%\logs"
if not exist "%WP_LOGS%" mkdir "%WP_LOGS%" >nul 2>nul
if exist "%WP_LOGS%\" >>"%WP_LOGS%\launcher.log" echo(===== %DATE% %TIME% 启动.bat =====
if exist "%WP_LOGS%\" >>"%WP_LOGS%\launcher.log" echo("工作目录 %CD%"

if not exist "%CD%\mpv.exe" (
    if exist "%WP_LOGS%\" >>"%WP_LOGS%\launcher.log" echo(找不到 mpv.exe，安装包可能不完整。
    echo 未找到 mpv.exe，安装包可能不完整。
    pause
    exit /b 1
)

rem 日常启动只打开 mpv：角色与随包 AList 都由面板管理。房主模式下 mpv
rem 会自动拉起 AList；观看者不会在本机运行 AList，也不会开放任何端口。
if exist "%WP_LOGS%\" >>"%WP_LOGS%\launcher.log" echo(启动 mpv.exe
start "mpv" "%CD%\mpv.exe" --idle=yes --force-window=yes
exit /b 0
