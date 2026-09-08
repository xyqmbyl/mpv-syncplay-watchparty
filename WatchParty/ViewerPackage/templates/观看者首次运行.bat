@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title MPV Syncplay 观看者首次设置

echo ============================================================
echo   MPV Syncplay 观看者首次设置
echo ============================================================
echo.
echo 房主地址已预设为：__ALIST_ORIGIN__
echo 此地址不是密码；只有获得房主 Tailscale 共享授权的设备才能访问。
echo.

set "TS_PYTHON=%~dp0python.exe"
set "TS_SCRIPT=%~dp0portable_config\syncplay\tailscale_integration.py"
set "WP_SCRIPT=%~dp0portable_config\syncplay\watchparty_setup.py"
if not exist "%TS_PYTHON%" (
    echo 观看者包缺少内置 Python，请重新完整解压 ZIP。
    pause
    exit /b 1
)
if not exist "%WP_SCRIPT%" (
    echo 观看者包缺少诊断模块，请重新完整解压 ZIP。
    pause
    exit /b 1
)

echo [1/4] 正在检查 Tailscale。
"%TS_PYTHON%" "%TS_SCRIPT%" open >nul 2>&1
if errorlevel 1 (
    echo 此电脑尚未安装 Tailscale，即将打开官方安装界面。
    call "%~dp0WatchParty\Tailscale\install-tailscale.bat"
    "%TS_PYTHON%" "%TS_SCRIPT%" open >nul 2>&1
)

if errorlevel 1 (
    echo.
    echo Tailscale 尚未安装完成。请完成安装后重新运行本文件。
    pause
    exit /b 1
)

echo [2/4] 正在打开 Tailscale。
echo.
echo 请使用你自己的账号登录 Tailscale，并接受房主发来的设备共享邀请。
echo 等到 Tailscale 显示“已连接”后，再回到此窗口继续。
pause

echo [3/4] 确认房主地址。
echo 直接按回车使用预设地址，或输入房主提供的地址后回车。
set "HOST_ADDR=__ALIST_ORIGIN__"
set /p "HOST_INPUT=房主地址 [%HOST_ADDR%]: "
if not "%HOST_INPUT%"=="" set "HOST_ADDR=%HOST_INPUT%"

:configure
echo.
echo [4/4] 正在写入观看者配置并清除所有本地媒体映射。
call "%~dp0WatchParty\Tailscale\configure-viewer.bat" "%HOST_ADDR%" --no-pause
if errorlevel 1 (
    echo.
    choice /C RT /N /M "Tailscale 尚未就绪。按 R 重试，按 T 退出："
    if errorlevel 2 exit /b 1
    goto configure
)

:diagnose
echo.
echo 正在按观看者视角诊断房主 AList（可达性）...
"%TS_PYTHON%" "%WP_SCRIPT%" doctor "%HOST_ADDR%" --role viewer
if errorlevel 1 (
    echo.
    choice /C RCT /N /M "按 R 重新诊断，按 C 仍要继续启动（仅播放同步，无视频），按 T 退出："
    if errorlevel 3 exit /b 1
    if errorlevel 2 goto start_mpv
    goto diagnose
)

:start_mpv
echo.
echo 设置完成，正在启动 mpv。
echo 打开面板后设置房间和昵称，再选择“加入 / 连接房间”。
start "mpv" "%~dp0mpv.exe" --idle=yes --force-window=yes
exit /b 0
