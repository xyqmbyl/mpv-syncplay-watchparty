[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [string]$PackageName = "",
    [ValidateSet('x64', 'x86')]
    [string]$Arch = 'x64'
)

# 构建房主完整包：mpv + 嵌入式 Python + 随包 AList + 首次运行向导。
# 房主地址不写死在包里：首次运行向导会检测本机 Tailscale IPv4 并自动写入。
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$expectedAlistVersion = "v3.64.0"
$expectedAlistCommit = "3e49fa46"
# AList 与 Tailscale 官方安装包都按架构区分；哈希与包内文件一一对应。
$expectedAlistSha256 = @{
    x64 = "2605BBE07D07F27C653C964EB8834B40AE4B4AFC5BDDF93287281801C95B3C2F"
    x86 = "F1A462EC2005CA2B704D7443FC43F411D739ADB1760C7A0A04A376E6449B1166"
}
$tailscaleInstallerName = @{
    x64 = "tailscale-setup-1.102.3-amd64.msi"
    x86 = "tailscale-setup-1.102.3-x86.msi"
}
$tailscaleInstallerSha256 = @{
    x64 = "03AC8183C6E3CE276E9B44281EBE7E4C02AEF28A971034CA170C4B665DF42DCE"
    x86 = "2A46E10F818991CA1476B2947BADB6EA5556541061B5B51C02A39682DE10DF53"
}
$alistSha256 = $expectedAlistSha256[$Arch]
$tailscaleInstaller = $tailscaleInstallerName[$Arch]
$tailscaleRequired = 'WatchParty\Tailscale\' + $tailscaleInstaller

$builderDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = [IO.Path]::GetFullPath((Join-Path $builderDirectory "..\.."))
if ([string]::IsNullOrWhiteSpace($PackageName)) {
    $PackageName = "WatchParty-Host-Windows-$Arch"
}
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $builderDirectory "output"
}
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)

# x64 包直接复用开发目录里的 64 位运行时；x86 包必须使用 fetch-artifacts.ps1
# 下载并解包的官方 32 位产物（artifacts\win-x86），绝不能与 x64 混用。
if ($Arch -eq 'x64') {
    $nativeRoot = $projectRoot
} else {
    $nativeRoot = [IO.Path]::GetFullPath((Join-Path $builderDirectory "artifacts\win-x86"))
    if (-not (Test-Path -LiteralPath (Join-Path $nativeRoot 'mpv.exe') -PathType Leaf)) {
        throw "x86 构建缺少预置产物，请先运行 fetch-artifacts.ps1 下载并解包 Windows x86 依赖。"
    }
}

if ($PackageName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') {
    throw "PackageName 只能包含英文字母、数字、点、下划线和连字符。"
}

$stagePath = [IO.Path]::GetFullPath((Join-Path $outputRoot $PackageName))
$zipPath = [IO.Path]::GetFullPath((Join-Path $outputRoot ($PackageName + ".zip")))
$outputPrefix = $outputRoot.TrimEnd('\') + '\'
if (-not $stagePath.StartsWith($outputPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "暂存目录必须位于输出目录内。"
}
if ($outputRoot.TrimEnd('\') -eq $projectRoot.TrimEnd('\')) {
    throw "输出目录不能是项目根目录。"
}

function Copy-PackageFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$RelativeDestination
    )
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "缺少构建所需文件：$Source"
    }
    $destination = Join-Path $stagePath $RelativeDestination
    $parent = Split-Path -Parent $destination
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $Source -Destination $destination -Force
}

function Get-RelativePath {
    param([string]$Base, [string]$Path)
    $prefix = [IO.Path]::GetFullPath($Base).TrimEnd('\') + '\'
    $fullPath = [IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "路径不在预期目录内：$Path"
    }
    return $fullPath.Substring($prefix.Length)
}

New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
if (Test-Path -LiteralPath $stagePath) {
    Remove-Item -LiteralPath $stagePath -Recurse -Force
}
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
New-Item -ItemType Directory -Path $stagePath -Force | Out-Null

# mpv + 嵌入式 Python 运行时（Syncplay 客户端只用标准库，不需要 CUDA/站点包）。
$rootFiles = @(
    'mpv.exe', 'mpv.com',
    'python.exe', 'pythonw.exe', 'python3.dll', 'python314.dll', 'python314.zip',
    'python314._pth', 'python.cat', 'libcrypto-3.dll', 'libssl-3.dll',
    'sqlite3.dll',
    'concrt140.dll',
    'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vccorlib140.dll',
    'vcruntime140.dll', 'vcruntime140_1.dll', 'vcruntime140_threads.dll'
)
if ($Arch -eq 'x86') {
    # 官方 i686 mpv 与嵌入式 x86 Python：与 x64 开发目录不同，mpv 需要随包
    # 携带 FFmpeg/libass 等运行库，Python 嵌入式包只带 vcruntime140.dll。
    $rootFiles = @(
        'mpv.exe', 'mpv.com',
        'avcodec-62.dll', 'avdevice-62.dll', 'avfilter-11.dll',
        'avformat-62.dll', 'avutil-60.dll',
        'libass-9.dll', 'libdav1d.dll', 'libfreetype-6.dll', 'libfribidi-0.dll',
        'libgcc_s_dw2-1.dll', 'libharfbuzz-0.dll', 'libiconv-2.dll',
        'liblcms2.dll', 'libplacebo-358.dll', 'libshaderc_shared.dll',
        'libspirv-cross-c-shared.dll', 'libssp-0.dll', 'libstdc++-6.dll',
        'libwinpthread-1.dll', 'swresample-6.dll', 'swscale-9.dll', 'zlib1.dll',
        'python.exe', 'pythonw.exe', 'python3.dll', 'python314.dll', 'python314.zip',
        'python314._pth', 'python.cat', 'libcrypto-3.dll', 'libssl-3.dll',
        'sqlite3.dll', 'vcruntime140.dll'
    )
}
foreach ($name in $rootFiles) {
    Copy-PackageFile (Join-Path $nativeRoot $name) $name
}
$pythonExtensions = @(
    '_hashlib.pyd', '_overlapped.pyd', '_queue.pyd', '_socket.pyd', '_ssl.pyd',
    '_sqlite3.pyd', 'select.pyd', 'unicodedata.pyd'
)
foreach ($name in $pythonExtensions) {
    Copy-PackageFile (Join-Path $nativeRoot $name) $name
}

$portableSource = Join-Path $projectRoot 'portable_config'
foreach ($relative in @(
    'scripts\syncplay_ui.lua',
    'syncplay\mpv_syncplay.py',
    'syncplay\media_provider.py',
    'syncplay\alist_diagnostics.py',
    'syncplay\tailscale_integration.py',
    'syncplay\watchparty_setup.py',
    'fonts\MaterialIconsRound-Regular.otf',
    'fonts\uosc_textures.ttf'
)) {
    Copy-PackageFile (Join-Path $portableSource $relative) (Join-Path 'portable_config' $relative)
}
$uoscSource = Join-Path $portableSource 'scripts\uosc'
Get-ChildItem -LiteralPath $uoscSource -File -Recurse -Force | ForEach-Object {
    $relative = Get-RelativePath $uoscSource $_.FullName
    Copy-PackageFile $_.FullName (Join-Path 'portable_config\scripts\uosc' $relative)
}

# 弹幕插件 uosc_danmaku（定制版）：纯本地 OSD 覆盖层，只读取本机 time-pos，
# 不参与 Syncplay 的播放/暂停/跳转同步，房主与观看者可各自独立开关。
# 只复制插件代码，不携带 danmaku-history.json 等本机运行状态。
$danmakuSource = Join-Path $portableSource 'scripts\uosc_danmaku'
Get-ChildItem -LiteralPath $danmakuSource -File -Recurse -Force | ForEach-Object {
    $relative = Get-RelativePath $danmakuSource $_.FullName
    $topLevel = $relative.Split('\')[0]
    if (@('.github', '.gitignore', '.gitattributes') -contains $topLevel) {
        return
    }
    Copy-PackageFile $_.FullName (Join-Path 'portable_config\scripts\uosc_danmaku' $relative)
}

# 随包 AList：只带程序本体和配置模板，绝不携带本机运行状态（data/、密码、日志）。
$alistBinary = if ($Arch -eq 'x64') {
    Join-Path $projectRoot 'WatchParty\alist\alist.exe'
} else {
    Join-Path $nativeRoot 'alist\alist.exe'
}
Copy-PackageFile $alistBinary 'WatchParty\alist\alist.exe'
Copy-PackageFile (Join-Path $projectRoot 'WatchParty\alist\config.template.json') 'WatchParty\alist\config.template.json'
Copy-PackageFile (Join-Path $projectRoot 'WatchParty\media\README.txt') 'WatchParty\media\README.txt'

# Tailscale 官方安装包与房主/观看者辅助脚本。
$tailscaleSource = if ($Arch -eq 'x64') {
    Join-Path $projectRoot 'WatchParty\Tailscale'
} else {
    Join-Path $nativeRoot 'Tailscale'
}
# Tailscale 官方安装包与房主/观看者辅助脚本。脚本始终取自仓库；MSI 按架构
# 取自对应产物目录；SOURCE.txt 按架构生成，记录本包携带的安装包与哈希。
foreach ($name in @(
    'install-tailscale.bat', 'configure-host.bat', 'configure-viewer.bat',
    'status.bat', 'README.md'
)) {
    Copy-PackageFile (Join-Path $projectRoot (Join-Path 'WatchParty\Tailscale' $name)) `
        (Join-Path 'WatchParty\Tailscale' $name)
}
$msiSource = Join-Path $tailscaleSource $tailscaleInstaller
Copy-PackageFile $msiSource (Join-Path 'WatchParty\Tailscale' $tailscaleInstaller)
$msiItem = Get-Item -LiteralPath (Join-Path $stagePath (Join-Path 'WatchParty\Tailscale' $tailscaleInstaller))
$utf8 = New-Object Text.UTF8Encoding($false)
$tailscaleProvenance = @"
Tailscale for Windows ($Arch)
Version: 1.102.3
Official URL: https://pkgs.tailscale.com/stable/$tailscaleInstaller
Downloaded: 2026-09-08
Size: $($msiItem.Length) bytes
SHA-256: $($tailscaleInstallerSha256[$Arch])

The installer is kept unchanged.  install-tailscale.bat checks the recorded
SHA-256 hash and the Windows Authenticode signer before launching it.
"@
[IO.File]::WriteAllText(
    (Join-Path $stagePath 'WatchParty\Tailscale\SOURCE.txt'),
    $tailscaleProvenance, $utf8)

# 房主入口脚本（首次运行向导 + 日常启动）。
foreach ($name in @('房主首次运行.bat', '启动.bat')) {
    Copy-PackageFile (Join-Path $projectRoot (Join-Path 'WatchParty' $name)) (Join-Path 'WatchParty' $name)
}

# 配置模板。
$templateDirectory = Join-Path $builderDirectory 'templates'
$utf8 = New-Object Text.UTF8Encoding($false)
foreach ($templateName in @(
    'host-mpv.conf', 'host-syncplay_ui.conf', 'host-THIRD_PARTY_NOTICES.txt'
)) {
    $templatePath = Join-Path $templateDirectory $templateName
    if (-not (Test-Path -LiteralPath $templatePath -PathType Leaf)) {
        throw "缺少模板：$templatePath"
    }
    $content = [IO.File]::ReadAllText($templatePath, [Text.Encoding]::UTF8)
    $destinationName = $templateName
    if ($templateName -eq 'host-mpv.conf') {
        $destinationName = 'portable_config\mpv.conf'
    } elseif ($templateName -eq 'host-syncplay_ui.conf') {
        $destinationName = 'portable_config\script-opts\syncplay_ui.conf'
    } elseif ($templateName -eq 'host-THIRD_PARTY_NOTICES.txt') {
        $destinationName = 'THIRD_PARTY_NOTICES.txt'
    }
    $destination = Join-Path $stagePath $destinationName
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    [IO.File]::WriteAllText($destination, $content, $utf8)
}

$licenseSource = Join-Path $templateDirectory 'licenses'
Get-ChildItem -LiteralPath $licenseSource -File -Force | ForEach-Object {
    Copy-PackageFile $_.FullName (Join-Path 'THIRD_PARTY_LICENSES' $_.Name)
}

# cmd.exe 对混合换行尤其敏感。无论源码编辑器使用何种换行，Release 中
# 所有批处理都固定为 UTF-8 无 BOM + CRLF。
$batchFiles = Get-ChildItem -LiteralPath $stagePath -Filter '*.bat' -File -Recurse -Force
foreach ($file in $batchFiles) {
    $content = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8)
    $normalized = $content.Replace("`r`n", "`n").Replace("`r", "`n").Replace("`n", "`r`n")
    [IO.File]::WriteAllText($file.FullName, $normalized, $utf8)
}
foreach ($file in $batchFiles) {
    $bytes = [IO.File]::ReadAllBytes($file.FullName)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and
            $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        throw "审计失败，批处理包含 UTF-8 BOM：$($file.FullName)"
    }
    $content = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8)
    if ($content -match '(?<!\r)\n|\r(?!\n)') {
        throw "审计失败，批处理没有统一使用 CRLF：$($file.FullName)"
    }
}

$requiredFiles = @(
    'mpv.exe', 'mpv.com',
    'python.exe', 'pythonw.exe', 'python3.dll', 'python314.dll', 'python314.zip',
    'python314._pth', '_sqlite3.pyd', 'sqlite3.dll',
    'WatchParty\alist\alist.exe',
    'WatchParty\alist\config.template.json',
    'WatchParty\media\README.txt',
    $tailscaleRequired,
    'WatchParty\房主首次运行.bat',
    'WatchParty\启动.bat',
    'portable_config\scripts\syncplay_ui.lua',
    'portable_config\syncplay\mpv_syncplay.py',
    'portable_config\syncplay\media_provider.py',
    'portable_config\syncplay\alist_diagnostics.py',
    'portable_config\syncplay\tailscale_integration.py',
    'portable_config\syncplay\watchparty_setup.py',
    'portable_config\script-opts\syncplay_ui.conf',
    'portable_config\scripts\uosc_danmaku\main.lua',
    'portable_config\scripts\uosc_danmaku\apis\dandanplay.lua',
    'portable_config\scripts\uosc_danmaku\modules\options.lua',
    'portable_config\scripts\uosc_danmaku\modules\render.lua',
    'portable_config\scripts\uosc_danmaku\modules\style.lua',
    'THIRD_PARTY_NOTICES.txt',
    'THIRD_PARTY_LICENSES\mpv-GPL-2.0.txt',
    'THIRD_PARTY_LICENSES\mpv-Copyright.txt',
    'THIRD_PARTY_LICENSES\Python-3.14.2.txt',
    'THIRD_PARTY_LICENSES\OpenSSL-3.0.18.txt',
    'THIRD_PARTY_LICENSES\uosc-LGPL-2.1.txt',
    'THIRD_PARTY_LICENSES\uosc_danmaku-MIT.txt',
    'THIRD_PARTY_LICENSES\Tailscale-BSD-3-Clause.txt',
    'THIRD_PARTY_LICENSES\Material-Icons-Apache-2.0.txt',
    'THIRD_PARTY_LICENSES\AList-AGPL-3.0.txt'
)
foreach ($relative in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $stagePath $relative) -PathType Leaf)) {
        throw "房主包缺少必要文件：$relative"
    }
}

# 审计：房主包绝不能携带本机私密状态或运行产物。
$forbiddenPatterns = @(
    '(^|/)data(/|$)',
    '(^|/)ADMIN_PASSWORD\.txt$',
    '(^|/)connection\.json$',
    '(?i)^watchparty/media/.+\.(mp4|mkv|avi|flv|ts|webm|mov|m2ts|wmv)$',
    '(^|/)(tailscale|tailscaled|tailscale-ipn)\.exe$',
    '(^|/)wintun\.dll$',
    '(^|/)(_cache|__pycache__)(/|$)',
    '(^|/)(backup|backups|[^/]*备份[^/]*)(/|$)',
    'syncplay_(status|command)\.json$',
    '(saved-props|danmaku-history)\.json$',
    '(^|/)test_[^/]*\.py$',
    '(?i)\.(pyc|pyo|log|bak|tmp|db|db-shm|db-wal|engine)$'
)
$packagedFiles = Get-ChildItem -LiteralPath $stagePath -File -Recurse -Force
foreach ($file in $packagedFiles) {
    $relative = (Get-RelativePath $stagePath $file.FullName).Replace('\', '/')
    foreach ($pattern in $forbiddenPatterns) {
        if ($relative -match $pattern) {
            throw "审计失败，房主包含有禁止项：$relative"
        }
    }
}

# 审计：房主配置必须是"等待向导改写"的安全初始值。
$hostConfig = Get-Content -LiteralPath (Join-Path $stagePath 'portable_config\script-opts\syncplay_ui.conf')
$expectedConfig = @{
    'server' = 'syncplay.pl:8995'
    'name' = ''
    'alist_enabled' = 'yes'
    'alist_server' = 'http://127.0.0.1:5244'
    'alist_root' = '~~/../WatchParty/media'
    'alist_virtual_root' = '/media'
    'alist_map' = ''
    'tailscale_mode' = 'host'
    'tailscale_host' = ''
}
$actualConfig = @{}
foreach ($line in $hostConfig) {
    if ($line -match '^\s*([A-Za-z][A-Za-z0-9_]*)\s*=(.*)$') {
        $actualConfig[$matches[1]] = $matches[2].Trim()
    }
}
foreach ($key in $expectedConfig.Keys) {
    if (-not $actualConfig.ContainsKey($key) -or $actualConfig[$key] -ne $expectedConfig[$key]) {
        throw "审计失败，房主配置 $key 不符合预期。"
    }
}
$mpvConfigText = [IO.File]::ReadAllText(
    (Join-Path $stagePath 'portable_config\mpv.conf'),
    [Text.Encoding]::UTF8
)
if ($mpvConfigText -match '(?m)^\s*vf-pre\s*=' -or
        $mpvConfigText -match '(?m)^\s*glsl-shaders' -or
        $mpvConfigText -notmatch '(?m)^speed=1\.0\s*$') {
    throw "审计失败，房主包 mpv 配置不应携带原机器的滤镜/速度设置。"
}

# AList 初始模板只允许携带公开的服务参数，密钥和数据库登录信息必须为空。
$alistTemplatePath = Join-Path $stagePath 'WatchParty\alist\config.template.json'
try {
    $alistTemplate = Get-Content -LiteralPath $alistTemplatePath -Raw |
        ConvertFrom-Json -ErrorAction Stop
} catch {
    throw "审计失败，AList 配置模板不是有效 JSON：$($_.Exception.Message)"
}
if (-not [string]::IsNullOrEmpty([string]$alistTemplate.jwt_secret) -or
        -not [string]::IsNullOrEmpty([string]$alistTemplate.meilisearch.api_key)) {
    throw "审计失败，AList 配置模板包含预置密钥。"
}
$databaseProperty = $alistTemplate.PSObject.Properties['database']
if ($null -ne $databaseProperty -and $null -ne $databaseProperty.Value) {
    $databaseConfig = $databaseProperty.Value
    foreach ($key in @('host', 'user', 'password', 'name', 'dsn')) {
        $property = $databaseConfig.PSObject.Properties[$key]
        if ($null -ne $property -and
                -not [string]::IsNullOrEmpty([string]$property.Value)) {
            throw "审计失败，AList 配置模板包含数据库机器配置：$key"
        }
    }
}
if ([string]$alistTemplate.scheme.address -ne '0.0.0.0' -or
        [int]$alistTemplate.scheme.http_port -ne 5244) {
    throw "审计失败，AList 模板必须监听 0.0.0.0:5244 才能通过 Tailscale IP 访问。"
}

$alistPath = Join-Path $stagePath 'WatchParty\alist\alist.exe'
$alistHash = (Get-FileHash -LiteralPath $alistPath -Algorithm SHA256).Hash
if ($alistHash -ne $alistSha256) {
    throw "审计失败，AList 二进制 SHA-256 与固定版本（$Arch）不符。"
}

# PE 机器类型审计：防止 x64/x86 产物混装进同一个包。
$peMachine = @{ x64 = 0x8664; x86 = 0x014C }
function Get-PEMachineType {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    try {
        $reader = New-Object IO.BinaryReader($stream)
        $stream.Position = 0x3C
        $peOffset = $reader.ReadInt32()
        $stream.Position = $peOffset
        if ($reader.ReadUInt32() -ne 0x00004550) {
            throw "审计失败，不是有效的 PE 文件：$Path"
        }
        return $reader.ReadUInt16()
    } finally {
        $stream.Dispose()
    }
}
foreach ($relative in @('mpv.exe', 'python.exe', 'WatchParty\alist\alist.exe')) {
    $actualMachine = Get-PEMachineType (Join-Path $stagePath $relative)
    if ($actualMachine -ne $peMachine[$Arch]) {
        throw ("审计失败，{0} 的 PE 架构与 -Arch {1} 不符。" -f $relative, $Arch)
    }
}

# 运行时自检：Python、AList、mpv、Tailscale 安装包签名。
$packagedPython = Join-Path $stagePath 'python.exe'
$runtimeCheck = @'
import argparse, hashlib, json, os, socket, sqlite3, ssl, sys, threading, urllib.request
root = os.path.normcase(os.path.realpath(os.path.dirname(sys.executable)))
assert sys.flags.isolated and sys.flags.ignore_environment
for entry in sys.path:
    resolved = os.path.normcase(os.path.realpath(entry))
    assert os.path.commonpath((root, resolved)) == root, (entry, root)
connection = sqlite3.connect(':memory:')
connection.execute('select 1').fetchone()
connection.close()
print('Embedded Python runtime OK')
'@
& $packagedPython -I -B -c $runtimeCheck
if ($LASTEXITCODE -ne 0) { throw "房主包内 Python 运行时验证失败。" }
& $packagedPython -I -B (Join-Path $stagePath 'portable_config\syncplay\watchparty_setup.py') --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "房主首次运行模块无法由包内 Python 加载。" }
& $packagedPython -I -B (Join-Path $stagePath 'portable_config\syncplay\mpv_syncplay.py') --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Syncplay 客户端无法由包内 Python 加载。" }
& $packagedPython -I -B (Join-Path $stagePath 'portable_config\syncplay\tailscale_integration.py') --json verify-installer | Out-Null
if ($LASTEXITCODE -ne 0) { throw "房主包内 Tailscale 安装包验证失败。" }
$alistVersionText = (& $alistPath version 2>&1) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "房主包内 AList 启动验证失败。" }
if ($alistVersionText -notmatch "(?m)^Version:\s+$([regex]::Escape($expectedAlistVersion))\s*$" -or
        $alistVersionText -notmatch "(?m)^Commit ID:\s+$([regex]::Escape($expectedAlistCommit))") {
    throw "审计失败，AList 二进制版本或提交与固定版本不符。"
}
& (Join-Path $stagePath 'mpv.com') --no-config --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "房主包内 mpv 启动验证失败。" }

# 所有可执行自检之后再审计一次，防止导入模块等操作把缓存或状态写进 ZIP。
$postCheckFiles = Get-ChildItem -LiteralPath $stagePath -File -Recurse -Force
foreach ($file in $postCheckFiles) {
    $relative = (Get-RelativePath $stagePath $file.FullName).Replace('\', '/')
    foreach ($pattern in $forbiddenPatterns) {
        if ($relative -match $pattern) {
            throw "自检后审计失败，房主包含有禁止项：$relative"
        }
    }
}

# 将当前机器已有的关键秘密/地址与产物文本做反向比对。只报告字段名，
# 不把管理员密码或 JWT 写到构建日志中。
$sensitiveValues = New-Object Collections.Generic.List[object]
function Add-SensitiveValue {
    param([string]$Name, [object]$Value)
    $text = [string]$Value
    if (-not [string]::IsNullOrWhiteSpace($text) -and $text.Length -ge 8 -and
            $text.IndexOfAny([char[]]"`r`n") -lt 0) {
        $sensitiveValues.Add([pscustomobject]@{ Name = $Name; Value = $text })
    }
}

$sourceAdminPassword = Join-Path $projectRoot 'WatchParty\ADMIN_PASSWORD.txt'
if (Test-Path -LiteralPath $sourceAdminPassword -PathType Leaf) {
    $adminText = [IO.File]::ReadAllText($sourceAdminPassword, [Text.Encoding]::UTF8)
    if ($adminText -match '(?m)^密码[：:]\s*(\S+)\s*$') {
        Add-SensitiveValue 'AList 管理员密码' $matches[1]
    }
}
$sourceAlistConfig = Join-Path $projectRoot 'WatchParty\alist\data\config.json'
if (Test-Path -LiteralPath $sourceAlistConfig -PathType Leaf) {
    try {
        $machineAlist = Get-Content -LiteralPath $sourceAlistConfig -Raw |
            ConvertFrom-Json -ErrorAction Stop
        Add-SensitiveValue 'AList JWT' $machineAlist.jwt_secret
        $machineDatabase = $machineAlist.PSObject.Properties['database']
        if ($null -ne $machineDatabase -and $null -ne $machineDatabase.Value) {
            foreach ($key in @('host', 'user', 'password', 'name', 'dsn')) {
                $property = $machineDatabase.Value.PSObject.Properties[$key]
                if ($null -ne $property) {
                    Add-SensitiveValue ("AList database." + $key) $property.Value
                }
            }
        }
    } catch {
        throw "无法审计当前 AList 机器配置：$($_.Exception.Message)"
    }
}
$sourceUiConfig = Join-Path $projectRoot 'portable_config\script-opts\syncplay_ui.conf'
if (Test-Path -LiteralPath $sourceUiConfig -PathType Leaf) {
    foreach ($line in Get-Content -LiteralPath $sourceUiConfig) {
        if ($line -match '^\s*(alist_server|alist_map|tailscale_host)\s*=\s*(.+?)\s*$') {
            Add-SensitiveValue ("Syncplay " + $matches[1]) $matches[2]
        }
    }
}
$sourceConnection = Join-Path $projectRoot 'WatchParty\Tailscale\connection.json'
if (Test-Path -LiteralPath $sourceConnection -PathType Leaf) {
    try {
        $machineConnection = Get-Content -LiteralPath $sourceConnection -Raw |
            ConvertFrom-Json -ErrorAction Stop
        Add-SensitiveValue 'Tailscale 主机名' $machineConnection.tailscale_host
        Add-SensitiveValue 'Tailscale AList 地址' $machineConnection.alist_server
    } catch {
        throw "无法审计当前 Tailscale 机器配置：$($_.Exception.Message)"
    }
}

$textExtensions = @('.txt', '.md', '.json', '.conf', '.py', '.lua', '.bat', '.ps1', '._pth')
$packagedTextFiles = $postCheckFiles | Where-Object {
    $textExtensions -contains $_.Extension.ToLowerInvariant()
}
foreach ($file in $packagedTextFiles) {
    $content = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8)
    foreach ($sensitive in $sensitiveValues) {
        if ($content.IndexOf($sensitive.Value, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $relative = (Get-RelativePath $stagePath $file.FullName).Replace('\', '/')
            throw "机器配置泄漏审计失败：$($sensitive.Name) 出现在 $relative"
        }
    }
}

$manifestPath = Join-Path $stagePath 'PACKAGE-CONTENTS.sha256'
$manifestLines = Get-ChildItem -LiteralPath $stagePath -File -Recurse -Force |
    Sort-Object FullName |
    ForEach-Object {
        $relative = (Get-RelativePath $stagePath $_.FullName).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash *$relative"
    }
[IO.File]::WriteAllLines($manifestPath, [string[]]$manifestLines, $utf8)

# 与观看者包相同的固化时间戳，保证可复现构建。冒烟测试刚运行过的
# alist/mpv 可能尚未完全释放句柄，个别文件允许短暂重试。
$archiveTimestamp = [DateTime]::SpecifyKind(
    [DateTime]::ParseExact('2026-09-08 00:00:00', 'yyyy-MM-dd HH:mm:ss', $null),
    [DateTimeKind]::Utc
)
Get-ChildItem -LiteralPath $stagePath -Force -Recurse | ForEach-Object {
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            $_.LastWriteTimeUtc = $archiveTimestamp
            break
        } catch [System.IO.IOException] {
            if ($attempt -eq 5) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
}
(Get-Item -LiteralPath $stagePath).LastWriteTimeUtc = $archiveTimestamp

Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory(
    $stagePath,
    $zipPath,
    [IO.Compression.CompressionLevel]::Optimal,
    $true
)
$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText($zipPath + '.sha256', "$zipHash *$([IO.Path]::GetFileName($zipPath))`r`n", $utf8)

$size = (Get-Item -LiteralPath $zipPath).Length
$count = (Get-ChildItem -LiteralPath $stagePath -File -Recurse -Force).Count
Write-Host ""
Write-Host "房主包构建并审计完成："
Write-Host "  目录：$stagePath"
Write-Host "  ZIP ：$zipPath"
Write-Host "  文件：$count"
Write-Host "  大小：$size bytes"
Write-Host "  SHA-256：$zipHash"
