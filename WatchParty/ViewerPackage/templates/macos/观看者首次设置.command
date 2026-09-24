#!/bin/bash
# WatchParty 观看者首次设置（macOS）。
# 作用与 Windows 包的 观看者首次运行.bat 相同：安装/打开 Tailscale、
# 确认房主地址、写入观看者配置并从观看者视角诊断。
set -u
cd "$(dirname "$0")"

export PYTHONIOENCODING=utf-8
unset PYTHONPATH PYTHONHOME 2>/dev/null
export PYTHONNOUSERSITE=1

PY="./python/bin/python3"
TS="./portable_config/syncplay/tailscale_integration.py"
WP="./portable_config/syncplay/watchparty_setup.py"
MPV="./mpv.app/Contents/MacOS/mpv"
PKG="./WatchParty/Tailscale/Tailscale-1.102.4-macos.pkg"
if [ ! -x "$PY" ] || [ ! -f "$TS" ] || [ ! -f "$WP" ]; then
    echo "观看者包不完整，请重新完整解压 ZIP。"
    read -r -p "按回车退出…" _
    exit 1
fi

echo "============================================================"
echo "  WatchParty 观看者首次设置（macOS）"
echo "============================================================"
echo
HOST_ADDR="__ALIST_ORIGIN__"
if [ -z "$HOST_ADDR" ]; then
    echo "本包未预设房主地址，首次设置时需要输入房主提供的地址。"
else
    echo "房主地址已预设为：__ALIST_ORIGIN__"
fi
echo "此地址不是密码；只有获得房主 Tailscale 共享授权的设备才能访问。"
echo

echo "[1/4] 正在检查 Tailscale。"
"$PY" "$TS" open >/dev/null 2>&1
if [ $? -ne 0 ]; then
    if [ -f "$PKG" ]; then
        echo "此电脑尚未安装 Tailscale，正在打开官方安装包。"
        open "$PKG"
        echo "完成安装（把 Tailscale 拖入“应用程序”）并登录后，重新运行本文件。"
    else
        echo "包内未找到 Tailscale 安装包，请从 https://tailscale.com 下载安装。"
    fi
    read -r -p "按回车退出…" _
    exit 1
fi

echo "[2/4] 请在 Tailscale 中使用你自己的账号登录，并接受房主发来的设备共享邀请。"
echo "等 Tailscale 菜单栏图标显示“已连接”后，回到此窗口按回车继续。"
read -r -p "按回车继续…" _

while true; do
    echo "[3/4] 确认房主地址。"
    if [ -n "$HOST_ADDR" ]; then
        echo "直接按回车使用预设地址，或输入房主提供的地址后回车。"
    else
        echo "本包未预设地址，请输入房主提供的地址后回车。"
    fi
    read -r -p "房主地址 [${HOST_ADDR}]: " HOST_INPUT
    if [ -n "$HOST_INPUT" ]; then
        HOST_ADDR="$HOST_INPUT"
    fi
    if [ -n "$HOST_ADDR" ]; then
        break
    fi
    echo "必须提供房主地址才能继续，请向房主索取后重新输入。"
done

while true; do
    echo
    echo "[4/4] 正在写入观看者配置并清除所有本地媒体映射。"
    if "$PY" "$TS" configure-viewer "$HOST_ADDR"; then
        break
    fi
    read -r -p "Tailscale 尚未就绪。按 R 重试，按其他键退出：" CHOICE
    case "$CHOICE" in
        r|R) ;;
        *) exit 1 ;;
    esac
done

echo
echo "正在按观看者视角诊断房主 AList（可达性）…"
if ! "$PY" "$WP" doctor "$HOST_ADDR" --role viewer; then
    read -r -p "诊断未通过。按回车仍要继续启动（仅播放同步，无视频），按 Ctrl+C 退出：" _
fi

echo
echo "设置完成，正在启动 mpv。"
echo "打开面板后设置房间和昵称，再选择“加入 / 连接房间”。"
exec "$MPV" --config-dir="$PWD/portable_config" --idle=yes --force-window=yes
