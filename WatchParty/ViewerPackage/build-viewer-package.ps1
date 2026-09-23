[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [string]$PackageName = "MPV-Syncplay-Viewer",
    # 房主地址：Device Sharing 后可访问的 Tailscale IPv4。
    [Parameter(Mandatory = $true)]
    [string]$TailscaleHost
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$builderDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = [IO.Path]::GetFullPath((Join-Path $builderDirectory "..\.."))
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $builderDirectory "output"
}
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)

if ($PackageName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$') {
    throw "PackageName 只能包含英文字母、数字、点、下划线和连字符。"
}
$normalizedHost = $TailscaleHost.Trim()
$parsedIp = $null
$isIpv4 = [Net.IPAddress]::TryParse($normalizedHost, [ref]$parsedIp) -and
    $parsedIp.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork
$isTailnetIp = $false
if ($isIpv4) {
    $octets = $parsedIp.GetAddressBytes()
    $isTailnetIp = $octets[0] -eq 100 -and $octets[1] -ge 64 -and $octets[1] -le 127
}
if (-not $isTailnetIp) {
    throw "TailscaleHost 必须是房主的 Tailscale IPv4（100.64.0.0/10）。"
}
$normalizedHost = $parsedIp.ToString()
# Device Sharing 直连 AList；构建器不会配置 Tailscale Serve 或 Funnel。
$alistOrigin = "http://{0}:{1}" -f $normalizedHost, 5244

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

# Keep the package small enough for a normal GitHub release. Syncplay uses
# only Python's standard library, so CUDA/TensorRT, site-packages and the
# host's VapourSynth installation are deliberately not copied.
$rootFiles = @(
    'mpv.exe', 'mpv.com',
    'python.exe', 'pythonw.exe', 'python3.dll', 'python314.dll', 'python314.zip',
    'python314._pth', 'python.cat', 'libcrypto-3.dll', 'libssl-3.dll', 'sqlite3.dll',
    'concrt140.dll',
    'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
    'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vccorlib140.dll',
    'vcruntime140.dll', 'vcruntime140_1.dll', 'vcruntime140_threads.dll'
)
foreach ($name in $rootFiles) {
    Copy-PackageFile (Join-Path $projectRoot $name) $name
}
$pythonExtensions = @(
    '_hashlib.pyd', '_overlapped.pyd', '_queue.pyd', '_socket.pyd', '_ssl.pyd',
    '_sqlite3.pyd', 'select.pyd', 'unicodedata.pyd'
)
foreach ($name in $pythonExtensions) {
    Copy-PackageFile (Join-Path $projectRoot $name) $name
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
# 观看者包与房主包内容一致，只复制插件代码，不携带本机弹幕历史。
$danmakuSource = Join-Path $portableSource 'scripts\uosc_danmaku'
Get-ChildItem -LiteralPath $danmakuSource -File -Recurse -Force | ForEach-Object {
    $relative = Get-RelativePath $danmakuSource $_.FullName
    $topLevel = $relative.Split('\')[0]
    if (@('.github', '.gitignore', '.gitattributes') -contains $topLevel) {
        return
    }
    Copy-PackageFile $_.FullName (Join-Path 'portable_config\scripts\uosc_danmaku' $relative)
}

$tailscaleSource = Join-Path $projectRoot 'WatchParty\Tailscale'
foreach ($name in @(
    'tailscale-setup-1.102.3-amd64.msi', 'install-tailscale.bat',
    'configure-viewer.bat', 'status.bat', 'SOURCE.txt'
)) {
    Copy-PackageFile (Join-Path $tailscaleSource $name) (Join-Path 'WatchParty\Tailscale' $name)
}

$templateDirectory = Join-Path $builderDirectory 'templates'
$utf8 = New-Object Text.UTF8Encoding($false)
foreach ($templateName in @(
    'mpv.conf', 'syncplay_ui.conf', '观看者首次运行.bat', '启动观看.bat',
    '观看者使用说明.md', 'THIRD_PARTY_NOTICES.txt'
)) {
    $templatePath = Join-Path $templateDirectory $templateName
    if (-not (Test-Path -LiteralPath $templatePath -PathType Leaf)) {
        throw "缺少模板：$templatePath"
    }
    $content = [IO.File]::ReadAllText($templatePath, [Text.Encoding]::UTF8)
    $content = $content.Replace('__ALIST_ORIGIN__', $alistOrigin)
    $content = $content.Replace('__TAILSCALE_HOST__', $normalizedHost)
    $destinationName = $templateName
    if ($templateName -eq 'mpv.conf') {
        $destinationName = 'portable_config\mpv.conf'
    } elseif ($templateName -eq 'syncplay_ui.conf') {
        $destinationName = 'portable_config\script-opts\syncplay_ui.conf'
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
    'mpv.exe', 'python.exe', 'python314.zip', '_sqlite3.pyd', 'sqlite3.dll',
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
    'WatchParty\Tailscale\tailscale-setup-1.102.3-amd64.msi',
    '观看者首次运行.bat', '启动观看.bat', '观看者使用说明.md',
    'THIRD_PARTY_NOTICES.txt',
    'THIRD_PARTY_LICENSES\mpv-GPL-2.0.txt',
    'THIRD_PARTY_LICENSES\mpv-Copyright.txt',
    'THIRD_PARTY_LICENSES\Python-3.14.2.txt',
    'THIRD_PARTY_LICENSES\OpenSSL-3.0.18.txt',
    'THIRD_PARTY_LICENSES\uosc-LGPL-2.1.txt',
    'THIRD_PARTY_LICENSES\uosc_danmaku-MIT.txt',
    'THIRD_PARTY_LICENSES\Tailscale-BSD-3-Clause.txt',
    'THIRD_PARTY_LICENSES\Material-Icons-Apache-2.0.txt',
    'THIRD_PARTY_LICENSES\Ziggy-atotto-clipboard-BSD-3-Clause.txt',
    'THIRD_PARTY_LICENSES\Ziggy-pkg-browser-BSD-2-Clause.txt',
    'THIRD_PARTY_LICENSES\Ziggy-golang-x-sys-BSD-3-Clause.txt',
    'THIRD_PARTY_LICENSES\Ziggy-Go-BSD-3-Clause.txt'
)
foreach ($relative in $requiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $stagePath $relative) -PathType Leaf)) {
        throw "观看者包缺少必要文件：$relative"
    }
}

$forbiddenPatterns = @(
    '(^|/)(alist|media)(/|$)',
    '(^|/)ADMIN_PASSWORD\.txt$',
    '(^|/)connection\.json$',
    '(^|/)configure-host\.bat$',
    '(^|/)(tailscale|tailscaled|tailscale-ipn)\.exe$',
    '(^|/)wintun\.dll$',
    '(^|/)(_cache|__pycache__)(/|$)',
    '(^|/)(backup|backups|[^/]*备份[^/]*)(/|$)',
    'syncplay_(status|command)\.json$',
    '(saved-props|danmaku-history)\.json$',
    '(^|/)test_[^/]*\.py$',
    '(?i)(screenshot|panel[-_].*check)',
    '(?i)\.(pyc|log|bak|tmp)$'
)
$packagedFiles = Get-ChildItem -LiteralPath $stagePath -File -Recurse -Force
foreach ($file in $packagedFiles) {
    $relative = (Get-RelativePath $stagePath $file.FullName).Replace('\', '/')
    foreach ($pattern in $forbiddenPatterns) {
        if ($relative -match $pattern) {
            throw "审计失败，观看者包含有禁止项：$relative"
        }
    }
}

$viewerConfig = Get-Content -LiteralPath (Join-Path $stagePath 'portable_config\script-opts\syncplay_ui.conf')
$expectedConfig = @{
    'alist_enabled' = 'yes'
    'alist_server' = $alistOrigin
    'alist_root' = ''
    'alist_map' = ''
    'tailscale_mode' = 'viewer'
    'tailscale_host' = $normalizedHost
}
$actualConfig = @{}
foreach ($line in $viewerConfig) {
    if ($line -match '^\s*([A-Za-z][A-Za-z0-9_]*)\s*=(.*)$') {
        $actualConfig[$matches[1]] = $matches[2].Trim()
    }
}
foreach ($key in $expectedConfig.Keys) {
    if (-not $actualConfig.ContainsKey($key) -or $actualConfig[$key] -ne $expectedConfig[$key]) {
        throw "审计失败，观看者配置 $key 不符合预期。"
    }
}
$mpvConfigText = [IO.File]::ReadAllText(
    (Join-Path $stagePath 'portable_config\mpv.conf'),
    [Text.Encoding]::UTF8
)
if ($mpvConfigText -match '(?m)^\s*vf-pre\s*=' -or
        $mpvConfigText -match '(?m)^\s*glsl-shaders' -or
        $mpvConfigText -notmatch '(?m)^speed=1\.0\s*$') {
    throw "审计失败，观看者包仍启用了房主 VapourSynth/TensorRT 滤镜。"
}

& (Join-Path $stagePath 'python.exe') -c "import argparse, hashlib, json, socket, sqlite3, ssl, threading, urllib.request; print('Python runtime OK')"
if ($LASTEXITCODE -ne 0) { throw "观看者包内 Python 运行时验证失败。" }
& (Join-Path $stagePath 'python.exe') (Join-Path $stagePath 'portable_config\syncplay\tailscale_integration.py') --json verify-installer | Out-Null
if ($LASTEXITCODE -ne 0) { throw "观看者包内 Tailscale 安装包验证失败。" }
& (Join-Path $stagePath 'mpv.com') --no-config --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "观看者包内 mpv 启动验证失败。" }

$manifestPath = Join-Path $stagePath 'PACKAGE-CONTENTS.sha256'
$manifestLines = Get-ChildItem -LiteralPath $stagePath -File -Recurse -Force |
    Sort-Object FullName |
    ForEach-Object {
        $relative = (Get-RelativePath $stagePath $_.FullName).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash *$relative"
    }
[IO.File]::WriteAllLines($manifestPath, [string[]]$manifestLines, $utf8)

# Normalize archive timestamps so identical source files and parameters produce
# an identical ZIP rather than changing on every build.
$archiveTimestamp = [DateTime]::SpecifyKind(
    [DateTime]::ParseExact('2026-09-08 00:00:00', 'yyyy-MM-dd HH:mm:ss', $null),
    [DateTimeKind]::Utc
)
Get-ChildItem -LiteralPath $stagePath -Force -Recurse | ForEach-Object {
    $_.LastWriteTimeUtc = $archiveTimestamp
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
Write-Host "观看者包构建并审计完成："
Write-Host "  目录：$stagePath"
Write-Host "  ZIP ：$zipPath"
Write-Host "  文件：$count"
Write-Host "  大小：$size bytes"
Write-Host "  SHA-256：$zipHash"
