#!/bin/bash
# WatchParty 日常启动（macOS）：打开 mpv。
# 房主/观看者共用；角色在面板的「联机 → 运行模式」里选择，随包 AList 只在
# 房主模式下由面板自动启动，观看者不会占用本机 5244 端口。
# 打开面板：在 mpv 中按 Ctrl+Shift+S。
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
MPV="./mpv.app/Contents/MacOS/mpv"
if [ ! -x "$PY" ]; then
    echo "尚未完成首次设置，正在进入设置向导…"
    exec "./首次设置.command"
fi
if [ ! -x "$MPV" ]; then
    echo "包不完整：缺少 mpv.app，请重新完整解压 ZIP。"
    exit 1
fi
if ! "$PY" -c "pass" >/dev/null 2>&1; then
    echo "内置 Python 无法运行。请在终端执行以下命令后重试："
    echo "  xattr -cr \"\$PWD\""
    read -r -p "按回车退出…" _
    exit 1
fi
if ! "$MPV" --version >/dev/null 2>&1; then
    echo "mpv 无法启动。请在终端执行以下命令后重试："
    echo "  xattr -cr \"\$PWD\""
    exit 1
fi

# 界面配置把缓存写进 ~~/_cache/（即 portable_config/_cache/）。发布包不允许携带
# 缓存目录，因此在这里按需创建，避免 mpv 报错或把缓存落到别处。
mkdir -p "$PWD/portable_config/_cache/icc" \
         "$PWD/portable_config/_cache/shader" \
         "$PWD/portable_config/_cache/watch_later" 2>/dev/null || true

# 界面图标字体是按 PostScript 名（MaterialIconsRound-Regular / uosc_textures）请求的。
# mpv 会把 portable_config/fonts（--sub-fonts-dir 默认的 ~~/fonts）通过 ass_add_font
# 交给 libass，libass 用自带的 FreeType 内嵌字体提供者解析，与系统字体提供者是哪一套
# （macOS 上是 CoreText）无关，因此包内字体在 macOS 上本来就生效。
# 这里再往 ~/Library/Fonts 装一份只是兜底：万一某个第三方 mpv 构建的内嵌字体通道不可用，
# 系统字体库仍能按 PostScript 名匹配到这两个图标字体。已存在同名字体则不覆盖。
# 中文字体 LXGW 不安装：已随包提供，且体积较大。
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

# 角色（房主/观看者）由面板的「联机 → 运行模式」决定，这里不做任何联机前置检查：
# 没选角色时就是纯本地播放器，选好角色后面板会自行启动 AList / Tailscale 会话。
exec "$MPV" --config-dir="$PWD/portable_config" --idle=yes --force-window=yes
