#!/bin/bash
# WatchParty 房主日常启动（macOS）：确保随包 AList 正在运行，然后打开 mpv。
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
SETUP="./portable_config/syncplay/watchparty_setup.py"
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

"$PY" "$SETUP" ensure-alist
exec "$MPV" --config-dir="$PWD/portable_config" --idle=yes --force-window=yes
