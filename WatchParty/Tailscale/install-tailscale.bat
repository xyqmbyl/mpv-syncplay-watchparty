@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul

rem x64 包与 x86 包的 MSI 文件名不同；每个包内只有一个官方 MSI，按通配符定位。
set "TAILSCALE_MSI="
for %%f in ("%~dp0tailscale-setup-*.msi") do set "TAILSCALE_MSI=%%~ff"
set "PROJECT_ROOT=%~dp0..\.."

if not defined TAILSCALE_MSI (
    echo [Tailscale] 找不到官方安装包 tailscale-setup-*.msi
    pause
    exit /b 1
)

echo [Tailscale] 正在验证官方安装包...
if exist "%PROJECT_ROOT%\python.exe" (
    "%PROJECT_ROOT%\python.exe" "%PROJECT_ROOT%\portable_config\syncplay\tailscale_integration.py" verify-installer
    if errorlevel 1 (
        echo [Tailscale] 安装包验证失败，已停止。
        pause
        exit /b 1
    )
) else (
    powershell.exe -NoProfile -Command "$s=Get-AuthenticodeSignature -LiteralPath $env:TAILSCALE_MSI; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Tailscale Inc\.'){Write-Error '数字签名无效'; exit 1}"
    if errorlevel 1 (
        echo [Tailscale] 安装包验证失败，已停止。
        pause
        exit /b 1
    )
)

echo [Tailscale] 验证通过，即将打开官方安装界面。
start /wait "Tailscale Installer" msiexec.exe /i "%TAILSCALE_MSI%"
if errorlevel 1 (
    echo [Tailscale] 安装未完成。
    pause
    exit /b 1
)

if exist "%PROJECT_ROOT%\python.exe" (
    "%PROJECT_ROOT%\python.exe" "%PROJECT_ROOT%\portable_config\syncplay\tailscale_integration.py" open >nul 2>&1
)
echo [Tailscale] 安装完成。请在托盘图标中登录，然后运行 configure-host.bat 或 configure-viewer.bat。
pause
