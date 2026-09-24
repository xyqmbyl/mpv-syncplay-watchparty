#!/bin/bash
# WatchParty 观看者日常启动（macOS）。
set -u
cd "$(dirname "$0")"

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

if ! "$PY" "$TS" open >/dev/null 2>&1; then
    echo "尚未完成首次设置，正在进入设置向导…"
    exec "./观看者首次设置.command"
fi
exec "$MPV" --config-dir="$PWD/portable_config" --idle=yes --force-window=yes
