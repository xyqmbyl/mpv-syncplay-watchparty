@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title MPV Syncplay 观看者首次设置

echo ============================================================
echo   MPV Syncplay 观看者首次设置
echo ============================================================
echo.
echo 房主地址已预设为：__TAILSCALE_HOST__
echo 此地址不是密码；只有获得房主 Tailscale 共享授权的设备才能访问。
echo.

set "TAILSCALE_UI=%ProgramFiles%\Tailscale\tailscale-ipn.exe"
if not exist "%TAILSCALE_UI%" (
    echo [1/3] 此电脑尚未安装 Tailscale，即将打开官方安装界面。
    call "%~dp0WatchParty\Tailscale\install-tailscale.bat"
)

if not exist "%TAILSCALE_UI%" (
    echo.
    echo Tailscale 尚未安装完成。请完成安装后重新运行本文件。
    pause
    exit /b 1
)

echo [2/3] 正在打开 Tailscale。
start "Tailscale" "%TAILSCALE_UI%"
echo.
echo 请使用你自己的账号登录 Tailscale，并接受房主发来的设备共享邀请。
echo 等到 Tailscale 显示“已连接”后，再回到此窗口继续。
pause

:configure
echo.
echo [3/3] 正在写入观看者配置并清除所有本地媒体映射。
call "%~dp0WatchParty\Tailscale\configure-viewer.bat" "__TAILSCALE_HOST__" --no-pause
if errorlevel 1 (
    echo.
    choice /C RT /N /M "Tailscale 尚未就绪。按 R 重试，按 T 退出："
    if errorlevel 2 exit /b 1
    goto configure
)

echo.
echo 设置完成，正在启动 mpv。
echo 打开面板后设置房间和昵称，再选择“加入 / 连接房间”。
start "mpv" "%~dp0mpv.exe" --idle=yes --force-window=yes
exit /b 0
