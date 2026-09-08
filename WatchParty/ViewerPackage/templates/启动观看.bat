@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist "%ProgramFiles%\Tailscale\tailscale.exe" (
    echo 尚未完成首次设置，即将进入设置向导。
    call "%~dp0观看者首次运行.bat"
    exit /b %ERRORLEVEL%
)

start "Tailscale" "%ProgramFiles%\Tailscale\tailscale-ipn.exe"
start "mpv" "%~dp0mpv.exe" --idle=yes --force-window=yes
exit /b 0
