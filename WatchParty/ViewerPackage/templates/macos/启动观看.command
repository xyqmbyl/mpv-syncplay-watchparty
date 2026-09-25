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
exec "$MPV" --config-dir="$PWD/portable_config" --idle=yes --force-window=yes
