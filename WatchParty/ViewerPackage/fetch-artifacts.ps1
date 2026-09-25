[CmdletBinding()]
param(
    [string]$ArtifactRoot = "",
    [string]$ProjectRoot = ""
)

# 预置构建 Windows x86 安装包所需的官方产物，并把固定的 v0.3.0 uosc
# 界面、各平台 Ziggy 辅助程序和字体合并进 portable_config。
#   mpv v0.41.0 官方 i686 构建（内层 zip 含 mpv.exe 与全部运行时 DLL）、
#   python.org 3.14.2 embed win32、AList v3.64.0 windows-386、
#   Tailscale 1.102.3 x86 MSI、uosc 5.12.0 官方 zip、
#   LXGW 文楷 Mono Lite 1.521 / Material Icons Round / uosc_textures 字体。
# 每个下载都会先核对 SHA-256，任何不匹配立即失败；不要绕过校验。
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$builderDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $ArtifactRoot = Join-Path $builderDirectory "artifacts"
}
$artifactRoot = [IO.Path]::GetFullPath($ArtifactRoot)
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    # 仓库根目录（build-*.ps1 使用的 $projectRoot）。
    $ProjectRoot = (Get-Item (Join-Path $builderDirectory "..\..")).FullName
}
$projectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$downloadRoot = Join-Path $artifactRoot "downloads"
$targetRoot = Join-Path $artifactRoot "win-x86"
New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null

$artifacts = @(
    @{
        Name = "mpv-v0.41.0-i686-w64-mingw32.zip"
        Url = "https://github.com/mpv-player/mpv/releases/download/v0.41.0/mpv-v0.41.0-i686-w64-mingw32.zip"
        Sha256 = "7E88E5B53B322CC2FA8C161494C75B11C02A64252FC8C6F34A80E01D6ACE4A73"
    },
    @{
        Name = "python-3.14.2-embed-win32.zip"
        Url = "https://www.python.org/ftp/python/3.14.2/python-3.14.2-embed-win32.zip"
        Sha256 = "A31DEDDF36D97EF9D6142FC919DD3E3884912563419629E58DACBC5A086F4E76"
    },
    @{
        Name = "alist-windows-386.zip"
        Url = "https://github.com/AlistGo/alist/releases/download/v3.64.0/alist-windows-386.zip"
        Sha256 = "31D6119E4811202438463423F3B5DBEBBEE15F3C885B19FE8D7D7106F290E198"
    },
    @{
        Name = "tailscale-setup-1.102.3-x86.msi"
        Url = "https://pkgs.tailscale.com/stable/tailscale-setup-1.102.3-x86.msi"
        Sha256 = "2A46E10F818991CA1476B2947BADB6EA5556541061B5B51C02A39682DE10DF53"
    },
    @{
        Name = "uosc-5.12.0.zip"
        Url = "https://github.com/tomasklaen/uosc/releases/download/5.12.0/uosc.zip"
        Sha256 = "CE5CF6BD1552DE4B7CB166804F596CDA678694F813041DA89565171600385165"
    },
    @{
        Name = "LXGWWenKaiMonoLite-Regular.ttf"
        Url = "https://github.com/lxgw/LxgwWenKai-Lite/releases/download/v1.521/LXGWWenKaiMonoLite-Regular.ttf"
        Sha256 = "03D04443C99A261C5D1AC5CCA1EF3E174194A5E1126D05F7A19C33617EB183F3"
    },
    @{
        Name = "MaterialIconsRound-Regular.otf"
        Url = "https://raw.githubusercontent.com/google/material-design-icons/27e9ef1dbeedc13d682fece4a58e1eda4cb0961a/font/MaterialIconsRound-Regular.otf"
        Sha256 = "BAD85E5454B6288104CE03806C37323BCD8F145E3094E727860173AC8C91062E"
    },
    @{
        Name = "uosc_textures.ttf"
        Url = "https://raw.githubusercontent.com/tomasklaen/uosc/5.12.0/src/fonts/uosc_textures.ttf"
        Sha256 = "CCC0660F284DFCEB5AB31EB363CCB2355DF30FCDF628E781EE374B7D4172ADA5"
    }
)

function Get-ExistingSha256 {
    param([string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

foreach ($artifact in $artifacts) {
    $destination = Join-Path $downloadRoot $artifact.Name
    if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
        Write-Host "下载 $($artifact.Name)"
        Invoke-WebRequest -Uri $artifact.Url -OutFile $destination
    }
    $actual = Get-ExistingSha256 $destination
    if ($actual -ne $artifact.Sha256) {
        throw ("SHA-256 校验失败：{0}`n  期望 {1}`n  实际 {2}" -f $artifact.Name, $artifact.Sha256, $actual)
    }
}
Write-Host "全部下载校验通过。"

# mpv 官方 i686 zip：外层只包了一个内层 zip，里面是 mpv.exe / mpv.com 与
# 全部运行时 DLL（i686 构建不是自包含的）。全部解到目标根目录。
$mpvExtract = Join-Path $downloadRoot "mpv-i686-extract"
if (Test-Path -LiteralPath $mpvExtract) {
    Remove-Item -LiteralPath $mpvExtract -Recurse -Force
}
Expand-Archive -LiteralPath (Join-Path $downloadRoot 'mpv-v0.41.0-i686-w64-mingw32.zip') -DestinationPath $mpvExtract
$innerZip = Get-ChildItem -LiteralPath $mpvExtract -Filter '*.zip' -File -Recurse | Select-Object -First 1
if ($null -eq $innerZip) {
    throw "mpv 压缩包里没有内层 zip"
}
$mpvInnerExtract = Join-Path $mpvExtract "inner"
Expand-Archive -LiteralPath $innerZip.FullName -DestinationPath $mpvInnerExtract
Copy-Item -Path (Join-Path $mpvInnerExtract '*') -Destination $targetRoot -Recurse -Force

# python.org embed win32：zip 根目录就是与 x64 开发目录一致的嵌入布局。
Expand-Archive -LiteralPath (Join-Path $downloadRoot 'python-3.14.2-embed-win32.zip') `
    -DestinationPath $targetRoot -Force

# AList windows-386：解出 alist.exe 放到 WatchParty\alist 布局。
$alistExtract = Join-Path $downloadRoot "alist-386-extract"
if (Test-Path -LiteralPath $alistExtract) {
    Remove-Item -LiteralPath $alistExtract -Recurse -Force
}
Expand-Archive -LiteralPath (Join-Path $downloadRoot 'alist-windows-386.zip') -DestinationPath $alistExtract
$alistExe = Get-ChildItem -LiteralPath $alistExtract -Filter 'alist.exe' -File -Recurse | Select-Object -First 1
if ($null -eq $alistExe) {
    throw "AList 压缩包中找不到 alist.exe"
}
New-Item -ItemType Directory -Path (Join-Path $targetRoot 'alist') -Force | Out-Null
Copy-Item -LiteralPath $alistExe.FullName -Destination (Join-Path $targetRoot 'alist\alist.exe') -Force

# Tailscale x86 MSI。
New-Item -ItemType Directory -Path (Join-Path $targetRoot 'Tailscale') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $downloadRoot 'tailscale-setup-1.102.3-x86.msi') `
    -Destination (Join-Path $targetRoot 'Tailscale\tailscale-setup-1.102.3-x86.msi') -Force

# 保留 v0.3.0 的定制 uosc 界面；上游压缩包只提供各平台 Ziggy 辅助程序。
$uoscExtract = Join-Path $downloadRoot "uosc-extract"
if (Test-Path -LiteralPath $uoscExtract) {
    Remove-Item -LiteralPath $uoscExtract -Recurse -Force
}
Expand-Archive -LiteralPath (Join-Path $downloadRoot 'uosc-5.12.0.zip') -DestinationPath $uoscExtract
$uoscSource = Get-ChildItem -LiteralPath $uoscExtract -Filter 'uosc' -Directory -Recurse | Select-Object -First 1
if ($null -eq $uoscSource) {
    throw "uosc 压缩包中找不到 uosc 目录"
}
$uoscBaseline = Join-Path $builderDirectory 'ui-baseline\uosc'
foreach ($required in @('main.lua', 'elements\Logo.lua', 'lib\lang.lua')) {
    if (-not (Test-Path -LiteralPath (Join-Path $uoscBaseline $required) -PathType Leaf)) {
        throw "v0.3.0 uosc 界面基线缺少文件：$required"
    }
}
$baselineMainHash = (Get-FileHash -LiteralPath (Join-Path $uoscBaseline 'main.lua') -Algorithm SHA256).Hash
if ($baselineMainHash -ne '20482D3906000C52DE11D4FFD73D19C530B447AE28A2857BBA6DD072DDEEA319') {
    throw 'v0.3.0 uosc 界面基线已改变，请核对官方发布包后再继续。'
}
$uoscHelpers = Join-Path $uoscSource.FullName 'bin'
if (-not (Test-Path -LiteralPath (Join-Path $uoscHelpers 'ziggy-windows.exe') -PathType Leaf) -or
    -not (Test-Path -LiteralPath (Join-Path $uoscHelpers 'ziggy-darwin') -PathType Leaf)) {
    throw 'uosc 官方压缩包缺少 Windows 或 macOS Ziggy 辅助程序。'
}
$uoscTarget = Join-Path $projectRoot "portable_config\scripts\uosc"
$projectFull = [IO.Path]::GetFullPath($projectRoot).TrimEnd('\') + '\'
$uoscFull = [IO.Path]::GetFullPath($uoscTarget)
if (-not $uoscFull.StartsWith($projectFull, [StringComparison]::OrdinalIgnoreCase)) {
    throw "拒绝清理工作区外的 uosc 目录：$uoscFull"
}
if (Test-Path -LiteralPath $uoscTarget) {
    if ((Get-Item -LiteralPath $uoscTarget -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "拒绝清理链接指向的 uosc 目录：$uoscTarget"
    }
    Remove-Item -LiteralPath $uoscTarget -Recurse -Force
}
Copy-Item -LiteralPath $uoscBaseline -Destination $uoscTarget -Recurse -Force
Copy-Item -LiteralPath $uoscHelpers -Destination (Join-Path $uoscTarget 'bin') -Recurse -Force

# 字体：uosc 与 OSD/Danmaku 都依赖 portable_config\fonts 下的三份字体。
$fontsTarget = Join-Path $projectRoot "portable_config\fonts"
New-Item -ItemType Directory -Path $fontsTarget -Force | Out-Null
foreach ($fontName in @('LXGWWenKaiMonoLite-Regular.ttf', 'MaterialIconsRound-Regular.otf', 'uosc_textures.ttf')) {
    Copy-Item -LiteralPath (Join-Path $downloadRoot $fontName) `
        -Destination (Join-Path $fontsTarget $fontName) -Force
}

Write-Host ""
Write-Host "x86 产物已就绪：$targetRoot"
Get-ChildItem -LiteralPath $targetRoot | ForEach-Object { Write-Host ("  " + $_.Name) }
Write-Host "v0.3.0 定制 uosc、平台辅助程序与字体已合并到：$projectRoot\portable_config"
