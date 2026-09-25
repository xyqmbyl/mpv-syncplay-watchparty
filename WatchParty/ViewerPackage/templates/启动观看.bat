@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"

set "TS_PYTHON=%~dp0python.exe"
set "TS_SCRIPT=%~dp0portable_config\syncplay\tailscale_integration.py"
if not exist "%TS_PYTHON%" (
    echo 尚未完成首次设置，即将进入设置向导。
    call "%~dp0观看者首次运行.bat"
    exit /b %ERRORLEVEL%
)

"%TS_PYTHON%" "%TS_SCRIPT%" open >nul 2>&1
if errorlevel 1 (
    echo 尚未完成首次设置，即将进入设置向导。
    call "%~dp0观看者首次运行.bat"
    exit /b %ERRORLEVEL%
)
rem 界面配置把缓存写进 portable_config\_cache\；发布包不带缓存目录，这里按需创建。
if not exist "%~dp0portable_config\_cache\icc" mkdir "%~dp0portable_config\_cache\icc" >nul 2>&1
if not exist "%~dp0portable_config\_cache\shader" mkdir "%~dp0portable_config\_cache\shader" >nul 2>&1
if not exist "%~dp0portable_config\_cache\watch_later" mkdir "%~dp0portable_config\_cache\watch_later" >nul 2>&1

start "mpv" "%~dp0mpv.exe" --idle=yes --force-window=yes
exit /b 0
