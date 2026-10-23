@echo off
rem 注意：本文件必须保存为 GBK/ANSI（zh-CN）编码，不要改存 UTF-8！
rem Win11 24H2+ 上 cmd 解析含多字节 UTF-8 的批处理会错位（表现为“语法错误”
rem 后窗口一闪而过），UTF-8+chcp 65001 方案已不可用，故本文件用 GBK 且不 chcp。
rem 控制台默认就是 cp936，中文正常显示；Python 输出走 WriteConsoleW 不受影响，
rem 管道里的 --json 输出仍由 PYTHONIOENCODING=utf-8 保证为 UTF-8。
setlocal DisableDelayedExpansion
set "PYTHONIOENCODING=utf-8"
title WatchParty 首次运行
cd /d "%~dp0.." || exit /b 1
set "WP_PYTHON=%CD%\python.exe"
set "WP_SCRIPT=%CD%\portable_config\syncplay\watchparty_setup.py"

rem 启动器日志：与 启动.bat 共用 logs\launcher.log，追加而不是覆盖，便于事后
rem 对照。目录不存在就创建；任何写入失败都不改变下面的交互、提示和退出码。
set "WP_LOGS=%CD%\logs"
if not exist "%WP_LOGS%" mkdir "%WP_LOGS%" >nul 2>nul
if exist "%WP_LOGS%\" >>"%WP_LOGS%\launcher.log" echo(===== %DATE% %TIME% 首次运行.bat =====
if exist "%WP_LOGS%\" >>"%WP_LOGS%\launcher.log" echo("工作目录 %CD%"

if not exist "%WP_PYTHON%" (
    if exist "%WP_LOGS%\" >>"%WP_LOGS%\launcher.log" echo(找不到内置 Python，安装包可能不完整。
    echo 未找到内置 Python，安装包可能不完整。
    pause
    exit /b 1
)
if not exist "%WP_SCRIPT%" (
    if exist "%WP_LOGS%\" >>"%WP_LOGS%\launcher.log" echo(找不到 watchparty_setup.py，安装包可能不完整。
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
    if exist "%WP_LOGS%\" >>"%WP_LOGS%\launcher.log" echo(bootstrap 未完成，退出码 %WP_RESULT%
    echo 首次运行检查未完成，请按上面的提示处理后重新双击本文件。
) else (
    if exist "%WP_LOGS%\" >>"%WP_LOGS%\launcher.log" echo(bootstrap 完成
    echo 提示：以后日常使用只需双击 WatchParty\启动.bat。
)
pause
exit /b %WP_RESULT%
