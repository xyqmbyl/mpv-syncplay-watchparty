@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title WatchParty 防火墙规则（仅 Tailscale 网段）

rem 只放行 Tailscale IPv4 网段访问随包 AList 的 5244 端口。
rem
rem 本文件会被 mpv 面板和首次运行向导以管理员身份【静默】调用，
rem 因此绝对不要在这里加 pause —— 隐藏窗口里的 pause 会让调用方一直等待。
rem 需要人工排查时，请右键“以管理员身份运行”，错误信息会打印在窗口里。
rem
rem 退出码：0 成功；1 netsh 失败；2 找不到随包 AList。

set "WP_ALIST="
for %%I in ("%~dp0..\alist\alist.exe") do set "WP_ALIST=%%~fI"
set "WP_RULE=MPV Syncplay WatchParty AList"

if not exist "%WP_ALIST%" (
    echo [错误] 找不到随包的 AList：%WP_ALIST%
    exit /b 2
)

rem 先删除同名规则，保证重复运行不会累积重复条目。
netsh advfirewall firewall delete rule name="%WP_RULE%" >nul 2>&1

netsh advfirewall firewall add rule name="%WP_RULE%" dir=in action=allow enable=yes profile=any protocol=TCP localport=5244 remoteip=100.64.0.0/10 program="%WP_ALIST%" >nul
if errorlevel 1 (
    echo [错误] 无法写入防火墙规则，可能被组策略或安全软件拦截。
    echo         请右键“以管理员身份运行”本文件查看详细报错。
    echo         不要改用系统弹窗放行：那会同时向局域网和公网开放 5244。
    exit /b 1
)

echo [完成] 已放行 5244：仅 Tailscale 网段 100.64.0.0/10 可以访问。
echo        规则名：%WP_RULE%
echo        程序：  %WP_ALIST%
exit /b 0
