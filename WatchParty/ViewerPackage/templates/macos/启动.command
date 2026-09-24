#!/bin/bash
# WatchParty 房主日常启动（macOS）：确保随包 AList 正在运行，然后打开 mpv。
# 打开面板：在 mpv 中按 Ctrl+Shift+S。
set -u
cd "$(dirname "$0")"

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

"$PY" "$SETUP" ensure-alist
exec "$MPV" --config-dir="$PWD/portable_config" --idle=yes --force-window=yes
