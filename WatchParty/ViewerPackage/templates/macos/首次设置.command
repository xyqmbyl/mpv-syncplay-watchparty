#!/bin/bash
# WatchParty 房主首次设置（macOS）。
# 作用与 Windows 包的 房主首次运行.bat 相同：启动随包 AList、自动配置
# 匿名共享并检测 Tailscale，最后把媒体地址写入观看者要使用的配置。
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
if [ ! -x "$PY" ] || [ ! -f "$SETUP" ]; then
    echo "包不完整：缺少内置 Python 或设置向导，请重新完整解压 ZIP。"
    echo "（若曾复制过目录，请确认 python/ 与 portable_config/ 都在本目录内。）"
    read -r -p "按回车退出…" _
    exit 1
fi
if ! "$PY" -c "pass" >/dev/null 2>&1; then
    echo "内置 Python 无法运行。请在终端执行以下命令后重试："
    echo "  xattr -cr \"\$PWD\""
    read -r -p "按回车退出…" _
    exit 1
fi

echo "============================================================"
echo "  WatchParty 房主首次设置（macOS）"
echo "============================================================"
echo

"$PY" "$SETUP" full --install-tailscale
RESULT=$?
echo
read -r -p "按回车退出…" _
exit $RESULT
