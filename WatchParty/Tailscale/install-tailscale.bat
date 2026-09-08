@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul

set "TAILSCALE_MSI=%~dp0tailscale-setup-1.102.3-amd64.msi"
set "TAILSCALE_SHA256=03AC8183C6E3CE276E9B44281EBE7E4C02AEF28A971034CA170C4B665DF42DCE"

if not exist "%TAILSCALE_MSI%" (
    echo [Tailscale] 找不到安装包：%TAILSCALE_MSI%
    pause
    exit /b 1
)

echo [Tailscale] 正在验证官方安装包...
powershell.exe -NoProfile -Command "$h=(Get-FileHash -Algorithm SHA256 -LiteralPath $env:TAILSCALE_MSI).Hash; $s=Get-AuthenticodeSignature -LiteralPath $env:TAILSCALE_MSI; if($h -ne $env:TAILSCALE_SHA256){Write-Error 'SHA-256 不匹配'; exit 1}; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Tailscale Inc\.'){Write-Error '数字签名无效'; exit 1}"
if errorlevel 1 (
    echo [Tailscale] 安装包验证失败，已停止。
    pause
    exit /b 1
)

echo [Tailscale] 验证通过，即将打开官方安装界面。
start /wait "Tailscale Installer" msiexec.exe /i "%TAILSCALE_MSI%"
if errorlevel 1 (
    echo [Tailscale] 安装未完成。
    pause
    exit /b 1
)

set "PROJECT_ROOT=%~dp0..\.."
if exist "%PROJECT_ROOT%\python.exe" (
    "%PROJECT_ROOT%\python.exe" "%PROJECT_ROOT%\portable_config\syncplay\tailscale_integration.py" open >nul 2>&1
)
echo [Tailscale] 安装完成。请在托盘图标中登录，然后运行 configure-host.bat 或 configure-viewer.bat。
pause
