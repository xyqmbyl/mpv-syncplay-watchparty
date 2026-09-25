#!/bin/bash
# 构建 macOS 房主/观看者安装包（Apple Silicon 或 Intel）。
#
# 用法：
#   ./build-macos-package.sh --role host   --arch arm64  [--output-dir DIR]
#   ./build-macos-package.sh --role viewer --arch intel \
#       [--tailscale-host 100.x.y.z] [--output-dir DIR]
#
# 产物：
#   WatchParty-Host-macOS-AppleSilicon.zip / WatchParty-Host-macOS-Intel.zip
#   WatchParty-Viewer-macOS-AppleSilicon.zip / WatchParty-Viewer-macOS-Intel.zip
#
# 依赖：macOS 自带 curl/ditto/shasum/codesign、Xcode Command Line Tools，
# 以及 python3（用于安装应用入口和审计，
# 用包外系统解释器或包内 python/bin/python3 均可）。所有第三方产物先核对
# SHA-256 再解包；哈希与官方发布值一一对应，任何不匹配立即失败。
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

ROLE=""
ARCH=""
TAILSCALE_HOST=""
OUTPUT_DIR=""

usage() {
    sed -n '2,12p' "$0"
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --role) ROLE="${2:-}"; shift 2 ;;
        --arch) ARCH="${2:-}"; shift 2 ;;
        --tailscale-host) TAILSCALE_HOST="${2:-}"; shift 2 ;;
        --output-dir) OUTPUT_DIR="${2:-}"; shift 2 ;;
        --help|-h) usage ;;
        *) echo "未知参数：$1" >&2; usage ;;
    esac
done

case "$ROLE" in
    host|viewer) ;;
    *) echo "--role 必须是 host 或 viewer" >&2; exit 2 ;;
esac
case "$ARCH" in
    arm64|intel) ;;
    *) echo "--arch 必须是 arm64 或 intel" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE_DIR="$SCRIPT_DIR/templates/macos"

# 固定版本的官方产物（URL + SHA-256 与官方发布值一致）。
MPV_VERSION="0.41.0"
MPV_ARM_NAME="mpv-v$MPV_VERSION-macos-14-arm.zip"
MPV_ARM_SHA256="5c96f9b21355fc0a11d2e2161ad65f33031070e9fb3f6bd9865fb459b94587e6"
MPV_INTEL_NAME="mpv-v$MPV_VERSION-macos-15-intel.zip"
MPV_INTEL_SHA256="41003617ab4f7784394b5ddea7ce51b3e0838e8cfc8166ad1a378b2eda3b583c"
PYTHON_PS_TAG="20260901"
PYTHON_VERSION="3.14.7"
PYTHON_ARM_TAR="cpython-3.14.7+20260901-aarch64-apple-darwin-install_only.tar.gz"
PYTHON_INTEL_TAR="cpython-3.14.7+20260901-x86_64-apple-darwin-install_only.tar.gz"
PYTHON_ARM_SHA256="30daa970c7d223530120f1693cd3c6fa4c0c0d31ef158710b0dd77f286a5b23e"
PYTHON_INTEL_SHA256="dd8841a2e8ef94bd1a02b52f92843120942140f112145d4e0199abab56f120b1"
ALIST_VERSION="3.64.0"
ALIST_ARM_SHA256="5f3cd409b1ba5c25d240ccb93bb77fa8a8e8cabbd21a467849ac90443e8f8140"
ALIST_INTEL_SHA256="877ded14aab8f754db197ef64fe9ee9191ed59ff60d70ad5e52d4d44e2075ead"
TAILSCALE_VERSION="1.102.4"
TAILSCALE_PKG_NAME="Tailscale-1.102.4-macos.pkg"
TAILSCALE_PKG_SHA256="b40b733af76233fd1e4af7acaeb325268e55e6818c15c6e9aa9e78f427245c5b"
UOSC_VERSION="5.12.0"
UOSC_ZIP_SHA256="ce5cf6bd1552de4b7cb166804f596cda678694f813041da89565171600385165"
MATERIAL_ICONS_SHA256="bad85e5454b6288104ce03806c37323bcd8f145e3094e727860173ac8c91062e"
LXGW_VERSION="1.521"
LXGW_FONT_SHA256="03d04443c99a261c5d1ac5cca1ef3e174194a5e1126d05f7a19c33617eb183f3"
UOSC_TEXTURES_SHA256="ccc0660f284dfceb5ab31eb363ccb2355df30fcdf628e781ee374b7d4172ada5"

MPV_BASE_URL="https://github.com/mpv-player/mpv/releases/download/v$MPV_VERSION"
PYTHON_BASE_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PYTHON_PS_TAG"
ALIST_BASE_URL="https://github.com/AlistGo/alist/releases/download/v$ALIST_VERSION"
TAILSCALE_PKG_URL="https://pkgs.tailscale.com/stable/$TAILSCALE_PKG_NAME"
UOSC_URL="https://github.com/tomasklaen/uosc/releases/download/$UOSC_VERSION/uosc.zip"
MATERIAL_ICONS_URL="https://raw.githubusercontent.com/google/material-design-icons/27e9ef1dbeedc13d682fece4a58e1eda4cb0961a/font/MaterialIconsRound-Regular.otf"
LXGW_FONT_URL="https://github.com/lxgw/LxgwWenKai-Lite/releases/download/v$LXGW_VERSION/LXGWWenKaiMonoLite-Regular.ttf"
UOSC_TEXTURES_URL="https://raw.githubusercontent.com/tomasklaen/uosc/$UOSC_VERSION/src/fonts/uosc_textures.ttf"

if [ "$ARCH" = "arm64" ]; then
    MPV_NAME="$MPV_ARM_NAME"
    MPV_SHA256="$MPV_ARM_SHA256"
    PYTHON_TAR="$PYTHON_ARM_TAR"
    PYTHON_SHA256="$PYTHON_ARM_SHA256"
    ALIST_TAR="alist-darwin-arm64.tar.gz"
    ALIST_SHA256="$ALIST_ARM_SHA256"
    ARCH_LABEL="AppleSilicon"
else
    MPV_NAME="$MPV_INTEL_NAME"
    MPV_SHA256="$MPV_INTEL_SHA256"
    PYTHON_TAR="$PYTHON_INTEL_TAR"
    PYTHON_SHA256="$PYTHON_INTEL_SHA256"
    ALIST_TAR="alist-darwin-amd64.tar.gz"
    ALIST_SHA256="$ALIST_INTEL_SHA256"
    ARCH_LABEL="Intel"
fi
PACKAGE_NAME="WatchParty-$ROLE-macOS-$ARCH_LABEL"
if [ "$ROLE" = "host" ]; then
    PACKAGE_NAME="WatchParty-Host-macOS-$ARCH_LABEL"
else
    PACKAGE_NAME="WatchParty-Viewer-macOS-$ARCH_LABEL"
fi
# tarball 文件名里的 + 需要转义成 %2B。
PYTHON_URL="$PYTHON_BASE_URL/$(printf '%s' "$PYTHON_TAR" | sed 's/+/%2B/g')"
MPV_URL="$MPV_BASE_URL/$MPV_NAME"
ALIST_URL="$ALIST_BASE_URL/$ALIST_TAR"

if [ -z "$OUTPUT_DIR" ]; then
    OUTPUT_DIR="$SCRIPT_DIR/output"
fi
# 观看者包可带 --tailscale-host 构建预配置包（私发用）；留空则构建"通用包"
# （Release 发布用），由观看者首次设置时输入房主地址。
if [ -n "$TAILSCALE_HOST" ]; then
    TAILSCALE_HOST="$(python3 -c 'import ipaddress,sys; print(ipaddress.ip_address(sys.argv[1]))' "$TAILSCALE_HOST")"
    case "$TAILSCALE_HOST" in
        100.*) ;;
        *) echo "TailscaleHost 必须是 100.64.0.0/10 内的地址。" >&2; exit 2 ;;
    esac
fi
if [ -n "$TAILSCALE_HOST" ]; then
    ALIST_ORIGIN="http://$TAILSCALE_HOST:5244"
else
    ALIST_ORIGIN=""
fi

STAGE="$OUTPUT_DIR/$PACKAGE_NAME"
ZIP_PATH="$OUTPUT_DIR/$PACKAGE_NAME.zip"
CACHE_DIR="$SCRIPT_DIR/artifacts/macos"

mkdir -p "$OUTPUT_DIR" "$CACHE_DIR"
rm -rf "$STAGE" "$ZIP_PATH"
mkdir -p "$STAGE"

fetch() {
    # fetch <url> <dest> <expected-sha256>
    url="$1"; dest="$2"; expected="$3"
    if [ ! -f "$dest" ]; then
        echo "下载 $(basename "$dest")"
        curl -sL --retry 5 --retry-delay 2 -o "$dest.part" "$url"
        mv "$dest.part" "$dest"
    fi
    echo "$expected  $dest" | shasum -a 256 -c - >/dev/null || {
        echo "SHA-256 校验失败：$dest" >&2
        exit 1
    }
}

# 1. 下载并核对全部官方产物。
fetch "$MPV_URL" "$CACHE_DIR/$MPV_NAME" "$MPV_SHA256"
fetch "$PYTHON_URL" "$CACHE_DIR/$PYTHON_TAR" "$PYTHON_SHA256"
fetch "$ALIST_URL" "$CACHE_DIR/$ALIST_TAR" "$ALIST_SHA256"
fetch "$TAILSCALE_PKG_URL" "$CACHE_DIR/$TAILSCALE_PKG_NAME" "$TAILSCALE_PKG_SHA256"
fetch "$UOSC_URL" "$CACHE_DIR/uosc-$UOSC_VERSION.zip" "$UOSC_ZIP_SHA256"
fetch "$MATERIAL_ICONS_URL" "$CACHE_DIR/MaterialIconsRound-Regular.otf" "$MATERIAL_ICONS_SHA256"
fetch "$LXGW_FONT_URL" "$CACHE_DIR/LXGWWenKaiMonoLite-Regular.ttf" "$LXGW_FONT_SHA256"
fetch "$UOSC_TEXTURES_URL" "$CACHE_DIR/uosc_textures.ttf" "$UOSC_TEXTURES_SHA256"

# 2. 解包官方产物。
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/mpv" "$WORK/py" "$WORK/alist"
ditto -x -k "$CACHE_DIR/$MPV_NAME" "$WORK/mpv"
# 官方 macOS zip 的唯一顶层条目是 mpv.tar.gz，应用包还需再解一层。
MPV_TAR="$(find "$WORK/mpv" -name mpv.tar.gz -type f -print -quit)"
if [ -n "$MPV_TAR" ]; then
    tar -xzf "$MPV_TAR" -C "$WORK/mpv"
fi
MPV_APP="$(find "$WORK/mpv" -name mpv.app -type d -print -quit)"
if [ -z "$MPV_APP" ]; then
    echo "mpv 压缩包里找不到 mpv.app" >&2
    find "$WORK/mpv" -maxdepth 5 -print | head -80 >&2
    exit 1
fi
tar -xzf "$CACHE_DIR/$PYTHON_TAR" -C "$WORK/py"
tar -xzf "$CACHE_DIR/$ALIST_TAR" -C "$WORK/alist"
ALIST_BIN="$(find "$WORK/alist" -type f -name alist -print -quit)"
if [ -z "$ALIST_BIN" ]; then
    echo "AList 压缩包里找不到 alist" >&2
    exit 1
fi
unzip -q "$CACHE_DIR/uosc-$UOSC_VERSION.zip" -d "$WORK/uosc"

# 3. 组装包目录。
mkdir -p "$STAGE/WatchParty/Tailscale" "$STAGE/portable_config/syncplay" \
    "$STAGE/portable_config/scripts" "$STAGE/portable_config/script-opts" \
    "$STAGE/portable_config/fonts"
ditto "$MPV_APP" "$STAGE/mpv.app"
cp -R "$WORK/py/python" "$STAGE/python"
# python-build-standalone 附带缓存、测试源码和 Windows 辅助批处理；
# 它们都不是 macOS 运行时所需文件，不纳入发布包。
find "$STAGE/python" -type f \( -name '*.pyc' -o -name '*.pyo' -o -name '*.bat' -o -name 'test_*.py' \) -delete
find "$STAGE/python" -type d -name __pycache__ -empty -delete
if [ "$ROLE" = "host" ]; then
    mkdir -p "$STAGE/WatchParty/alist" "$STAGE/WatchParty/media"
    cp "$ALIST_BIN" "$STAGE/WatchParty/alist/alist"
    cp "$REPO_ROOT/WatchParty/alist/config.template.json" \
        "$STAGE/WatchParty/alist/config.template.json"
    cp "$REPO_ROOT/WatchParty/media/README.txt" "$STAGE/WatchParty/media/README.txt"
fi
cp "$CACHE_DIR/$TAILSCALE_PKG_NAME" \
    "$STAGE/WatchParty/Tailscale/$TAILSCALE_PKG_NAME"
cat > "$STAGE/WatchParty/Tailscale/SOURCE.txt" <<EOF
Tailscale for macOS
Version: 1.102.4
Official URL: https://pkgs.tailscale.com/stable/$TAILSCALE_PKG_NAME
SHA-256: $TAILSCALE_PKG_SHA256
EOF

# 界面始终使用 v0.3.0 发布版的定制 uosc；官方 zip 仅提供 darwin 版 ziggy。
UOSC_SRC="$WORK/uosc/scripts/uosc"
if [ ! -d "$UOSC_SRC" ]; then
    echo "uosc.zip 里找不到 uosc 目录" >&2
    exit 1
fi
UOSC_BASELINE="$SCRIPT_DIR/ui-baseline/uosc"
if [ ! -f "$UOSC_BASELINE/main.lua" ] || [ ! -f "$UOSC_BASELINE/elements/Logo.lua" ]; then
    echo "缺少 v0.3.0 定制 uosc UI 基线，不能构建 macOS 包" >&2
    exit 1
fi
mkdir -p "$STAGE/portable_config/scripts/uosc"
cp -R "$UOSC_BASELINE/." "$STAGE/portable_config/scripts/uosc/"
UOSC_BIN="$STAGE/portable_config/scripts/uosc/bin"
mkdir -p "$UOSC_BIN"
if [ -f "$UOSC_SRC/bin/ziggy-darwin" ]; then
    cp "$UOSC_SRC/bin/ziggy-darwin" "$UOSC_BIN/ziggy-darwin"
elif [ -f "$UOSC_SRC/bin/ziggy-darwin-arm64" ] && [ "$ARCH" = "arm64" ]; then
    cp "$UOSC_SRC/bin/ziggy-darwin-arm64" "$UOSC_BIN/ziggy-darwin"
elif [ -f "$UOSC_SRC/bin/ziggy-darwin-x64" ] && [ "$ARCH" = "intel" ]; then
    cp "$UOSC_SRC/bin/ziggy-darwin-x64" "$UOSC_BIN/ziggy-darwin"
else
    echo "uosc.zip 里找不到 darwin 版 ziggy" >&2
    exit 1
fi
ZIGGY_SRC="$UOSC_BIN/ziggy-darwin"

# 4. 项目文件。
for f in mpv_syncplay.py media_provider.py alist_diagnostics.py \
         tailscale_integration.py watchparty_setup.py; do
    cp "$REPO_ROOT/portable_config/syncplay/$f" "$STAGE/portable_config/syncplay/$f"
done
cp "$REPO_ROOT/portable_config/scripts/syncplay_ui.lua" \
    "$STAGE/portable_config/scripts/syncplay_ui.lua"
DANMAKU_SRC="$REPO_ROOT/portable_config/scripts/uosc_danmaku"
( cd "$DANMAKU_SRC" && find . -type f \
    ! -path './.github/*' ! -name '.gitignore' ! -name '.gitattributes' \
    ! -name 'danmaku-history.json' ) | while IFS= read -r rel; do
    mkdir -p "$STAGE/portable_config/scripts/uosc_danmaku/$(dirname "$rel")"
    cp "$DANMAKU_SRC/$rel" "$STAGE/portable_config/scripts/uosc_danmaku/$rel"
done
cp "$TEMPLATE_DIR/mpv.conf" "$STAGE/portable_config/mpv.conf"
cp "$TEMPLATE_DIR/uosc_danmaku.conf" "$STAGE/portable_config/script-opts/uosc_danmaku.conf"
if [ "$ROLE" = "host" ]; then
    cp "$TEMPLATE_DIR/syncplay_ui.conf" \
        "$STAGE/portable_config/script-opts/syncplay_ui.conf"
else
    sed -e "s|__ALIST_ORIGIN__|$ALIST_ORIGIN|g" \
        -e "s|__TAILSCALE_HOST__|$TAILSCALE_HOST|g" \
        "$TEMPLATE_DIR/syncplay_ui-viewer.conf" \
        > "$STAGE/portable_config/script-opts/syncplay_ui.conf"
fi
cp "$CACHE_DIR/MaterialIconsRound-Regular.otf" \
    "$STAGE/portable_config/fonts/MaterialIconsRound-Regular.otf"
cp "$CACHE_DIR/LXGWWenKaiMonoLite-Regular.ttf" \
    "$STAGE/portable_config/fonts/LXGWWenKaiMonoLite-Regular.ttf"
cp "$CACHE_DIR/uosc_textures.ttf" "$STAGE/portable_config/fonts/uosc_textures.ttf"
mkdir -p "$STAGE/THIRD_PARTY_LICENSES"
cp "$SCRIPT_DIR/templates/licenses/"*.txt "$STAGE/THIRD_PARTY_LICENSES/"

# 说明文档与入口脚本（.command 需要可执行位）。
if [ "$ROLE" = "host" ]; then
    for name in 首次设置.command 启动.command 房主使用说明.md; do
        cp "$TEMPLATE_DIR/$name" "$STAGE/$name"
    done
else
    for name in 观看者首次设置.command 启动观看.command 观看者使用说明.md; do
        cp "$TEMPLATE_DIR/$name" "$STAGE/$name"
    done
    sed -e "s|__ALIST_ORIGIN__|$ALIST_ORIGIN|g" \
        -e "s|__TAILSCALE_HOST__|$TAILSCALE_HOST|g" \
        -i "" "$STAGE/观看者首次设置.command" "$STAGE/启动观看.command"
fi

chmod +x "$STAGE"/*.command "$ZIGGY_SRC"
if [ "$ROLE" = "host" ]; then
    chmod +x "$STAGE/WatchParty/alist/alist"
fi

# 可执行文件签名：保持官方原签名；缺失或失效时补 ad-hoc 瘦签名，避免
# Gatekeeper 把解压出来的二进制直接当作"已损坏"拒绝运行。
for bin in "$ZIGGY_SRC"; do
    if ! codesign --verify "$bin" >/dev/null 2>&1; then
        codesign --force --sign - "$bin" >/dev/null 2>&1 || true
    fi
done
if [ "$ROLE" = "host" ] && ! codesign --verify "$STAGE/WatchParty/alist/alist" >/dev/null 2>&1; then
    codesign --force --sign - "$STAGE/WatchParty/alist/alist" >/dev/null 2>&1 || true
fi

# Finder 双击也要加载应用旁的 portable_config；保留原 mpv 路径供 .command 使用。
python3 "$SCRIPT_DIR/install_macos_launcher.py" "$STAGE" --arch "$ARCH"

# 签名可能改变二进制内容，必须在签名完成后记录第三方哈希。
ZIGGY_SHA256="$(shasum -a 256 "$ZIGGY_SRC" | awk '{print toupper($1)}')"
MPV_BIN_SHA256="$(shasum -a 256 "$STAGE/mpv.app/Contents/MacOS/mpv" | awk '{print toupper($1)}')"
sed -e "s/__ZIGGY_SHA256__/$ZIGGY_SHA256/" \
    -e "s/__MPV_SHA256__/$MPV_BIN_SHA256/" \
    "$TEMPLATE_DIR/THIRD_PARTY_NOTICES.txt" > "$STAGE/THIRD_PARTY_NOTICES.txt"

# 5. 审计：禁止项（机器状态、凭据、媒体、缓存、测试文件、Windows 专用文件）。
ROLE="$ROLE" "$STAGE/python/bin/python3" - "$STAGE" <<'PY'
import os
import re
import sys

stage = sys.argv[1]
role = os.environ["ROLE"]
forbidden = [
    r"(^|/)data(/|$)",
    r"(^|/)ADMIN_PASSWORD\.txt$",
    r"(^|/)connection\.json$",
    r"(?i)^watchparty/media/.+\.(mp4|mkv|avi|flv|ts|webm|mov|m2ts|wmv)$",
    r"(^|/)(tailscale|tailscaled|tailscale-ipn)$",
    r"(^|/)(_cache|__pycache__)(/|$)",
    r"(^|/)(backup|backups|[^/]*备份[^/]*)(/|$)",
    r"syncplay_(status|command)\.json$",
    r"(saved-props|danmaku-history)\.json$",
    r"(^|/)test_[^/]*\.py$",
    r"(?i)\.(pyc|pyo|log|bak|tmp|db|db-shm|db-wal|engine)$",
    r"\.bat$",
]
if role == "viewer":
    forbidden.append(r"(^|/)(alist|media)(/|$)")
count = 0
for root, dirs, files in os.walk(stage):
    dirs.sort()
    for name in sorted(files):
        rel = os.path.relpath(os.path.join(root, name), stage).replace(os.sep, "/")
        count += 1
        for pattern in forbidden:
            if re.search(pattern, rel):
                raise SystemExit("审计失败，包内含禁止项：%s" % rel)
print("内容审计通过：%d 个文件" % count)
PY

# 6. 配置审计：host 是"等待向导改写"的安全初始值；viewer 已写入房主地址。
"$STAGE/python/bin/python3" - "$STAGE" "$ROLE" "$ALIST_ORIGIN" "$TAILSCALE_HOST" <<'PY'
import sys

stage, role, alist_origin, tailscale_host = sys.argv[1:5]
conf = {}
path = stage + "/portable_config/script-opts/syncplay_ui.conf"
with open(path, encoding="utf-8") as handle:
    for line in handle:
        if "=" in line and line.split("=")[0].strip().isidentifier():
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()

if role == "host":
    expected = {
        "server": "syncplay.pl:8995",
        "alist_enabled": "yes",
        "alist_server": "http://127.0.0.1:5244",
        "alist_root": "~~/../WatchParty/media",
        "alist_virtual_root": "/media",
        "alist_map": "",
        "tailscale_mode": "host",
        "tailscale_host": "",
    }
else:
    expected_prefix = alist_origin
    checks = {
        "alist_enabled": "yes",
        "alist_server": expected_prefix,
        "tailscale_mode": "viewer",
        "tailscale_host": tailscale_host,
    }
    if conf.get("alist_root"):
        raise SystemExit("审计失败，观看者配置不应携带 alist_root。")
    if conf.get("alist_map"):
        raise SystemExit("审计失败，观看者配置不应携带 alist_map。")
    for key, value in checks.items():
        if conf.get(key) != value:
            raise SystemExit("审计失败，观看者配置 %s 不符合预期。" % key)
    print("观看者配置审计通过")
    raise SystemExit(0)
for key, value in expected.items():
    if conf.get(key) != value:
        raise SystemExit("审计失败，房主配置 %s 不符合预期。" % key)
print("房主配置审计通过")
PY

# AList 配置模板审计：房主包不得携带密钥或数据库配置。
if [ "$ROLE" = "host" ]; then
python3 - "$STAGE/WatchParty/alist/config.template.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
assert not (config.get("jwt_secret") or "").strip(), "模板包含预置 jwt_secret"
api_key = (config.get("meilisearch") or {}).get("api_key")
assert not (api_key or "").strip(), "模板包含 meilisearch api_key"
database = config.get("database") or {}
for key in ("host", "user", "password", "name", "dsn"):
    value = database.get(key)
    assert not (value or "").strip(), "模板包含数据库配置：" + key
scheme = config["scheme"]
assert scheme["address"] == "0.0.0.0" and int(scheme["http_port"]) == 5244
print("AList 模板审计通过")
PY
fi

# 7. 运行时自检：包内 python、AList、mpv 都要能启动；Tailscale 安装包哈希核对。
cd "$STAGE"
"$PWD/python/bin/python3" -B -c "import sqlite3, ssl, socket, urllib.request; sqlite3.connect(':memory:').execute('select 1'); print('Python runtime OK')"
"$STAGE/python/bin/python3" -B portable_config/syncplay/watchparty_setup.py --help >/dev/null
"$STAGE/python/bin/python3" -B portable_config/syncplay/mpv_syncplay.py --help >/dev/null
"$STAGE/python/bin/python3" -B portable_config/syncplay/tailscale_integration.py verify-installer >/dev/null
if [ "$ROLE" = "host" ]; then
    "$STAGE/WatchParty/alist/alist" version | grep -F "v$ALIST_VERSION"
fi
"./mpv.app/Contents/MacOS/mpv" --no-config --version >/dev/null
"./mpv.app/Contents/MacOS/watchparty-launcher" --no-config --version >/dev/null
cd - >/dev/null

# 自检后再次审计，防止运行时缓存混入 ZIP。
"$STAGE/python/bin/python3" - "$STAGE" "$ROLE" <<'PY'
import os
import re
import sys

stage = sys.argv[1]
role = sys.argv[2]
forbidden = [
    r"(^|/)data(/|$)",
    r"(^|/)ADMIN_PASSWORD\.txt$",
    r"(^|/)connection\.json$",
    r"(?i)^watchparty/media/.+\.(mp4|mkv|avi|flv|ts|webm|mov|m2ts|wmv)$",
    r"(^|/)(tailscale|tailscaled|tailscale-ipn)$",
    r"(^|/)(_cache|__pycache__)(/|$)",
    r"(^|/)(backup|backups|[^/]*备份[^/]*)(/|$)",
    r"syncplay_(status|command)\.json$",
    r"(saved-props|danmaku-history)\.json$",
    r"(^|/)test_[^/]*\.py$",
    r"(?i)\.(pyc|pyo|log|bak|tmp|db|db-shm|db-wal|engine)$",
    r"\.bat$",
]
if role == "viewer":
    forbidden.append(r"(^|/)(alist|media)(/|$)")
for root, dirs, files in os.walk(stage):
    for name in files:
        rel = os.path.relpath(os.path.join(root, name), stage).replace(os.sep, "/")
        for pattern in forbidden:
            if re.search(pattern, rel):
                raise SystemExit("自检后审计失败：%s" % rel)
print("自检后审计通过")
PY

# 8. 固定时间戳 + 清单 + 打包。
find "$STAGE" -exec touch -t 202609080000 {} +
MANIFEST_TMP="$OUTPUT_DIR/.manifest.tmp"
( cd "$STAGE" && find . -type f | sed 's|^\./||' | LC_ALL=C sort ) |
while IFS= read -r rel; do
    hash="$(shasum -a 256 "$STAGE/$rel" | awk '{print $1}')"
    printf '%s *%s\n' "$hash" "$rel"
done > "$MANIFEST_TMP"
mv "$MANIFEST_TMP" "$STAGE/PACKAGE-CONTENTS.sha256"

ditto -c -k --keepParent "$STAGE" "$ZIP_PATH"
ZIP_SHA256="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"
printf '%s *%s\n' "$ZIP_SHA256" "$PACKAGE_NAME.zip" > "$ZIP_PATH.sha256"

echo ""
echo "$ROLE macOS ($ARCH_LABEL) 包构建并审计完成："
echo "  目录：$STAGE"
echo "  ZIP ：$ZIP_PATH"
echo "  文件：$(find "$STAGE" -type f | wc -l | tr -d ' ')"
echo "  大小：$(stat -f%z "$ZIP_PATH") bytes"
echo "  SHA-256：$ZIP_SHA256"
