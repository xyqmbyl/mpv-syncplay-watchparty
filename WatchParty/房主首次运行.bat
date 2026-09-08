@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
title WatchParty 房主首次运行
cd /d "%~dp0.." || (
    echo 无法定位项目目录，请从完整包目录运行此文件。
    pause
    exit /b 1
)
set "WP_PYTHON=%CD%\python.exe"
set "WP_SCRIPT=%CD%\portable_config\syncplay\watchparty_setup.py"
set "WP_ALIST=%CD%\WatchParty\alist\alist.exe"
set "WP_FIREWALL_RULE=MPV Syncplay WatchParty AList"

if not exist "%WP_PYTHON%" (
    echo 未找到项目内 Python。请下载 Release 中的 MPV-Syncplay-Host.zip
    echo 完整房主包，而不是 GitHub 源码 ZIP。
    pause
    exit /b 1
)
if not exist "%WP_SCRIPT%" (
    echo 未找到 WatchParty 向导模块，请重新解压完整房主包。
    pause
    exit /b 1
)
if not exist "%WP_ALIST%" (
    echo 未找到随包 AList，请重新解压完整房主包。
    pause
    exit /b 1
)

rem AList 的入站规则与绝对路径绑定。首次运行先取得 UAC 授权，再为当前
rem 解压位置建立仅允许 Tailscale IPv4 网段访问 5244 的规则。
powershell.exe -NoProfile -Command "if(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){exit 0}else{exit 1}" >nul 2>&1
if errorlevel 1 (
    echo 首次设置需要管理员权限，以配置仅限 Tailscale 的 5244 防火墙规则。
    set "WP_ELEVATE_SCRIPT=%~f0"
    powershell.exe -NoProfile -Command "$p=Start-Process -FilePath $env:WP_ELEVATE_SCRIPT -Verb RunAs -PassThru -Wait; exit $p.ExitCode"
    if errorlevel 1 (
        echo 未获得管理员权限，无法配置安全的媒体入站规则。
        exit /b 1
    )
    exit /b 0
)

netsh advfirewall firewall delete rule name="%WP_FIREWALL_RULE%" >nul 2>&1
netsh advfirewall firewall add rule name="%WP_FIREWALL_RULE%" dir=in action=allow enable=yes profile=any protocol=TCP localport=5244 remoteip=100.64.0.0/10 program="%WP_ALIST%" >nul
if errorlevel 1 (
    echo 无法配置 Windows 防火墙。请不要手动把 5244 暴露到公网。
    pause
    exit /b 1
)
echo 已配置 Windows 防火墙：仅允许 Tailscale 100.64.0.0/10 访问 AList 5244。
echo.

"%WP_PYTHON%" "%WP_SCRIPT%" full --install-tailscale
set "WP_RESULT=%ERRORLEVEL%"
echo.
if not "%WP_RESULT%"=="0" (
    echo 向导未全部完成。按上面提示处理后，重新运行本文件即可继续。
) else (
    echo 房主环境已就绪。日常启动请运行 WatchParty\启动.bat，
    echo 或直接启动 mpv 后按 Ctrl+Shift+S 打开面板创建房间。
)
pause
exit /b %WP_RESULT%
