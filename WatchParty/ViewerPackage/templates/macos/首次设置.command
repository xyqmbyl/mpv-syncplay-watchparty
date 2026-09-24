#!/bin/bash
# WatchParty 房主首次设置（macOS）。
# 作用与 Windows 包的 房主首次运行.bat 相同：启动随包 AList、自动配置
# 匿名共享并检测 Tailscale，最后把媒体地址写入观看者要使用的配置。
set -u
cd "$(dirname "$0")"

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

echo "============================================================"
echo "  WatchParty 房主首次设置（macOS）"
echo "============================================================"
echo

"$PY" "$SETUP" full --install-tailscale
RESULT=$?
echo
read -r -p "按回车退出…" _
exit $RESULT
