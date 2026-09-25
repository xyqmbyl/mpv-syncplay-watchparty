#!/bin/bash
# WatchParty 观看者日常启动（macOS）。
set -u
cd "$(dirname "$0")"

# 浏览器下载的 ZIP 解压后所有文件都带 macOS 隔离标记，未公证的随包二进制
# 会被系统直接拒绝运行。先递归清除本包的隔离标记。若双击本文件时被
# Gatekeeper 拦截，在"系统设置 → 隐私与安全性"里点"仍要打开"一次即可。
xattr -r -d com.apple.quarantine . >/dev/null 2>&1 || true

export PYTHONIOENCODING=utf-8
unset PYTHONPATH PYTHONHOME 2>/dev/null
export PYTHONNOUSERSITE=1

PY="./python/bin/python3"
TS="./portable_config/syncplay/tailscale_integration.py"
MPV="./mpv.app/Contents/MacOS/mpv"
if [ ! -x "$PY" ]; then
    echo "尚未完成首次设置，正在进入设置向导…"
    exec "./观看者首次设置.command"
fi
if [ ! -x "$MPV" ]; then
    echo "包不完整：缺少 mpv.app，请重新完整解压 ZIP。"
    exit 1
fi
if ! "$MPV" --version >/dev/null 2>&1; then
    echo "mpv 无法启动。请在终端执行以下命令后重试："
    echo "  xattr -cr \"\$PWD\""
    exit 1
fi

if ! "$PY" "$TS" open >/dev/null 2>&1; then
    echo "尚未完成首次设置，正在进入设置向导…"
    exec "./观看者首次设置.command"
fi
# 界面配置把缓存写进 ~~/_cache/（即 portable_config/_cache/）。发布包不允许携带
# 缓存目录，因此在这里按需创建，避免 mpv 报错或把缓存落到别处。
mkdir -p "$PWD/portable_config/_cache/icc" \
         "$PWD/portable_config/_cache/shader" \
         "$PWD/portable_config/_cache/watch_later" 2>/dev/null || true

# 界面图标字体是按 PostScript 名请求的。官方 macOS 版 mpv 的 libass 走 CoreText
# 字体提供者，不加载 portable_config/fonts，因此把随包字体装到 ~/Library/Fonts
# （已存在则跳过）。中文字体 LXGW 不安装：体积大且界面未启用该字体。
install_bundled_font() {
    local src="$1" name
    name="$(basename "$src")"
    if [ ! -f "$src" ] || [ -f "$HOME/Library/Fonts/$name" ]; then
        return 0
    fi
    mkdir -p "$HOME/Library/Fonts" 2>/dev/null || return 0
    if cp "$src" "$HOME/Library/Fonts/$name" 2>/dev/null; then
        echo "已安装界面字体：$name"
    else
        echo "提示：未能自动安装字体 $name，界面图标可能显示异常。"
    fi
}
install_bundled_font "$PWD/portable_config/fonts/MaterialIconsRound-Regular.otf"
install_bundled_font "$PWD/portable_config/fonts/uosc_textures.ttf"

exec "$MPV" --config-dir="$PWD/portable_config" --idle=yes --force-window=yes
