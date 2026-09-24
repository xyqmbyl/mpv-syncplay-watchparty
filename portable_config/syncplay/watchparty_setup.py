#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WatchParty 房主首次运行向导与网络诊断（纯标准库，无聊天）。

该模块只负责"开箱即用"的环境准备：随包 AList 的启动与防重复、/media
存储与管理员设置的自动配置、匿名+Range 共享探测、Tailscale 三态检测，
以及观看者侧的网络诊断。它不实现 Syncplay 协议，也不修改同步核心；
仅通过 syncplay_ui.conf 与 mpv 客户端、面板交换配置。

设计约束：
- 不启用 Tailscale Funnel，不把 AList 暴露到公网；媒体仅经 tailnet
  或局域网访问。
- 不在观看者机器上写入任何房主路径映射；观看者永远只消费 URL。
- 所有面向用户的输出为中文，并在失败时给出下一步操作。
"""

import argparse
import ipaddress
import json
import os
import posixpath
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# 允许直接运行本文件：把同目录模块加入导入路径。
SYNCPLAY_DIR = os.path.dirname(os.path.abspath(__file__))
if SYNCPLAY_DIR not in sys.path:
    sys.path.insert(0, SYNCPLAY_DIR)

import alist_diagnostics
import tailscale_integration
from tailscale_integration import TailscaleIntegrationError

PORTABLE_CONFIG_DIR = os.path.dirname(SYNCPLAY_DIR)
PROJECT_ROOT = os.path.dirname(PORTABLE_CONFIG_DIR)
WATCHPARTY_DIR = os.path.join(PROJECT_ROOT, "WatchParty")
ALIST_DIR = os.path.join(WATCHPARTY_DIR, "alist")
IS_WINDOWS = os.name == "nt"
ALIST_EXE = os.path.join(ALIST_DIR, "alist.exe" if IS_WINDOWS else "alist")
ALIST_CONFIG = os.path.join(ALIST_DIR, "data", "config.json")
ALIST_TEMPLATE = os.path.join(ALIST_DIR, "config.template.json")
MEDIA_DIR = os.path.join(WATCHPARTY_DIR, "media")
ADMIN_PASSWORD_FILE = os.path.join(WATCHPARTY_DIR, "ADMIN_PASSWORD.txt")
UI_CONFIG = os.path.join(PORTABLE_CONFIG_DIR, "script-opts", "syncplay_ui.conf")
PROBE_NAME = "_watchparty_probe.bin"
PROBE_SIZE = 1024 * 1024
ALIST_PORT = 5244
STARTUP_TIMEOUT = 45.0
TAILSCALE_IPV4_NETWORK = ipaddress.ip_network("100.64.0.0/10")
VIDEO_EXTENSIONS = {
    ".3gp", ".avi", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4",
    ".mpeg", ".mpg", ".mts", ".ogm", ".ogv", ".ts", ".vob", ".webm",
    ".wmv",
}
MAX_MEDIA_SCAN_DIRECTORIES = 32


class SetupError(RuntimeError):
    """带中文提示的向导错误。"""


# ----------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------
def log(message):
    print(message, flush=True)


def ui_path(text):
    """面向用户的路径文本：Windows 用反斜杠，macOS/Linux 用正斜杠。"""
    return text.replace("/", "\\") if IS_WINDOWS else text


# 房主首次设置入口：Windows 批处理，macOS 用 .command。
HOST_FIRST_RUN_NAME = "房主首次运行.bat" if IS_WINDOWS else "首次设置.command"


def _plain_url(url):
    """去掉 URL 末尾多余的斜杠。"""
    return (url or "").rstrip("/")


def http_get(url, timeout=6.0):
    """直接请求（忽略系统代理），返回 (status, body_text) 或 (None, 错误)。"""
    request = urllib.request.Request(url, headers={
        "User-Agent": "mpv-watchparty-setup/1.0",
        "Accept": "*/*",
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.getcode(), response.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(4096).decode("utf-8", "replace")
        except Exception:
            body = ""
        return exc.code, body
    except (OSError, ValueError) as exc:
        return None, str(getattr(exc, "reason", exc))


def http_post_json(url, payload, timeout=6.0):
    """匿名 POST JSON，返回 ``(HTTP 状态, 正文)``，且不使用系统代理。"""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "User-Agent": "mpv-watchparty-setup/1.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.getcode(), response.read(262144).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(262144).decode("utf-8", "replace")
        except Exception:
            body = ""
        return exc.code, body
    except (OSError, ValueError) as exc:
        return None, str(getattr(exc, "reason", exc))


def _ping_response_ok(status, body):
    if status != 200:
        return False
    if (body or "").strip().lower() == "pong":
        return True
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return False
    return (isinstance(payload, dict) and payload.get("code") == 200 and
            str(payload.get("message") or "").strip().lower() == "pong")


def _listen_addr(value):
    try:
        host, port = value.rsplit(":", 1)
        return host.strip("[]"), int(port)
    except (ValueError, AttributeError):
        return None, None


def probe_port(host, port, timeout=2.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def read_admin_password():
    """从随包 ADMIN_PASSWORD.txt 读取管理员密码（仅本机使用）。"""
    try:
        with open(ADMIN_PASSWORD_FILE, "r", encoding="utf-8-sig") as stream:
            for line in stream:
                text = line.strip()
                if text.startswith("密码") or "密码" in text:
                    password = text.split("：", 1)[-1].split(":", 1)[-1].strip()
                    if password:
                        return password
    except FileNotFoundError:
        return None
    return None


def write_admin_password(password):
    os.makedirs(WATCHPARTY_DIR, exist_ok=True)
    content = (
        "用户名：admin\n"
        "密码：%s\n\n"
        "此文件由 WatchParty 首次运行向导生成，仅用于本机登录 AList 管理后台\n"
        "(http://127.0.0.1:5244/@manage)。AList 只监听本机与 tailnet，\n"
        "请不要把本文件发给任何人。\n" % password
    )
    with open(ADMIN_PASSWORD_FILE, "w", encoding="utf-8") as stream:
        stream.write(content)


# ----------------------------------------------------------------------
# 第 1 步：随包 AList 的启动与防重复
# ----------------------------------------------------------------------
def render_alist_config():
    """生成监听 0.0.0.0 的 config.json（alist 默认是 127.0.0.1，必须先行覆盖）。"""
    template = None
    if os.path.isfile(ALIST_TEMPLATE):
        with open(ALIST_TEMPLATE, "r", encoding="utf-8-sig") as stream:
            template = json.load(stream)
    config = template or {
        "force": False,
        "site_url": "",
        "cdn": "",
        "jwt_secret": "",
        "token_expires_in": 48,
        "database": {"type": "sqlite3", "db_file": "data\\data.db"},
        "scheme": {"address": "0.0.0.0", "http_port": ALIST_PORT, "https_port": -1},
        "temp_dir": "data\\temp",
        "log": {"enable": True, "name": "data\\log\\log.log"},
    }
    scheme = config.setdefault("scheme", {})
    scheme["address"] = "0.0.0.0"
    scheme["http_port"] = ALIST_PORT
    scheme["https_port"] = scheme.get("https_port", -1) or -1
    if not config.get("jwt_secret"):
        config["jwt_secret"] = secrets.token_hex(16)
    database = config.setdefault("database", {})
    database.setdefault("type", "sqlite3")
    database.setdefault("db_file", "data\\data.db")
    if not IS_WINDOWS:
        # 模板按 Windows 习惯写成 data\\temp；POSIX 上反斜杠是文件名的一部分，
        # 必须统一成正斜杠。
        for key in ("temp_dir", "bleve_dir"):
            if isinstance(config.get(key), str):
                config[key] = config[key].replace("\\", "/")
        database["db_file"] = str(database.get("db_file", "data/data.db")).replace("\\", "/")
        log_config = config.setdefault("log", {})
        if isinstance(log_config.get("name"), str):
            log_config["name"] = log_config["name"].replace("\\", "/")
    os.makedirs(os.path.dirname(ALIST_CONFIG), exist_ok=True)
    with open(ALIST_CONFIG, "w", encoding="utf-8") as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
    return config


def alist_ping(base_url, timeout=4.0):
    """判断地址是否为随包 AList（/ping 返回 pong）。"""
    status, body = http_get(_plain_url(base_url) + "/ping", timeout)
    return _ping_response_ok(status, body)


def start_alist_process():
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [ALIST_EXE, "server", "--force-bin-dir"],
        cwd=ALIST_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )


def wait_for_alist(timeout=STARTUP_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if alist_ping("http://127.0.0.1:%d" % ALIST_PORT):
            return True
        time.sleep(0.5)
    return False


def stop_alist_process(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def run_alist_admin(arguments, timeout=30.0):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        [ALIST_EXE] + list(arguments),
        cwd=ALIST_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=flags,
        check=False,
    )


def ensure_alist():
    """保证随包 AList 正在运行；返回 (base_url, started_now)。"""
    if not os.path.isfile(ALIST_EXE):
        raise SetupError(
            "没有找到随包的 AList（%s）。\n"
            "你下载的可能是 GitHub 源码 ZIP；请下载 Release 中的 "
            "WatchParty-Host 完整房主包。" % ALIST_EXE)

    local = "http://127.0.0.1:%d" % ALIST_PORT
    if probe_port("127.0.0.1", ALIST_PORT):
        if alist_ping(local):
            log("AList 已在运行（复用现有进程，避免 5244 端口重复启动）。")
            return local, False
        raise SetupError(
            "端口 %d 已被其他程序占用（不是随包 AList）。\n"
            "请关闭占用该端口的程序，或修改 WatchParty%salist%sdata%sconfig.json "
            "中的 http_port 后重试。" % ((ALIST_PORT,) + (os.sep,) * 4))

    fresh_install = not os.path.isfile(ALIST_CONFIG)
    if fresh_install:
        render_alist_config()
    elif not alist_ping(local):
        # 已有配置时确保监听地址不是 127.0.0.1。
        try:
            with open(ALIST_CONFIG, "r", encoding="utf-8-sig") as stream:
                config = json.load(stream)
            address = config.get("scheme", {}).get("address", "")
            if address in ("127.0.0.1", "localhost"):
                log("检测到 AList 配置只监听 127.0.0.1，已自动改为 0.0.0.0（原文件已备份）。")
                backup = ALIST_CONFIG + ".before-public-listen.bak"
                if not os.path.isfile(backup):
                    with open(backup, "w", encoding="utf-8") as stream:
                        stream.write(json.dumps(config, ensure_ascii=False, indent=2))
                config.setdefault("scheme", {})["address"] = "0.0.0.0"
                with open(ALIST_CONFIG, "w", encoding="utf-8") as stream:
                    json.dump(config, stream, ensure_ascii=False, indent=2)
        except (OSError, ValueError) as exc:
            log("警告：无法检查 AList 配置文件（%s），继续按现状启动。" % exc)

    # 全新数据库必须在服务启动前设置管理员密码（避免 sqlite 写锁，
    # 也让首次启动后即可用已知密码登录管理接口）。
    if not os.path.isfile(os.path.join(ALIST_DIR, "data", "data.db")):
        ensure_admin_password()

    log("正在启动随包 AList ...")
    process = start_alist_process()
    if not wait_for_alist():
        stop_alist_process(process)
        raise SetupError(
            "AList 启动失败：%.0f 秒内未在 127.0.0.1:%d 就绪。\n"
            "请查看 %s 排查。" % (ui_path("WatchParty/alist/data/log/log.log"), STARTUP_TIMEOUT, ALIST_PORT))
    log("AList 已启动：http://127.0.0.1:%d" % ALIST_PORT)
    return local, True


# ----------------------------------------------------------------------
# 第 2 步：管理员初始化与 /media 存储自动配置
# ----------------------------------------------------------------------
class AlistAdmin:
    def __init__(self, base_url, password):
        self.base = _plain_url(base_url)
        self.token = self._login(password)

    def _post(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": self.token,
                     "User-Agent": "mpv-watchparty-setup/1.0"},
            method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            body = json.loads(response.read(65536).decode("utf-8", "replace"))
        if body.get("code") != 200:
            raise SetupError("AList 管理接口 %s 失败：%s" % (path, body.get("message")))
        return body.get("data")

    def _get(self, path):
        request = urllib.request.Request(
            self.base + path,
            headers={"Authorization": self.token,
                     "User-Agent": "mpv-watchparty-setup/1.0"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10) as response:
            body = json.loads(response.read(262144).decode("utf-8", "replace"))
        if body.get("code") != 200:
            raise SetupError("AList 管理接口 %s 失败：%s" % (path, body.get("message")))
        return body.get("data")

    def _login(self, password):
        data = json.dumps({"username": "admin", "password": password}).encode("utf-8")
        request = urllib.request.Request(
            self.base + "/api/auth/login", data=data,
            headers={"Content-Type": "application/json",
                     "User-Agent": "mpv-watchparty-setup/1.0"}, method="POST")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=10) as response:
                body = json.loads(response.read(8192).decode("utf-8", "replace"))
        except (OSError, ValueError) as exc:
            raise SetupError("无法连接本机 AList 管理接口：%s" % exc) from exc
        if body.get("code") != 200 or not (body.get("data") or {}).get("token"):
            raise SetupError(
                "AList 管理员登录失败（%s）。\n"
                "请核对 WatchParty%sADMIN_PASSWORD.txt 中的密码是否与当前 "
                "AList 数据目录匹配。" % ((body.get("message"),) + (os.sep,) * 2))
        return body["data"]["token"]

    # ---- 存储与设置 ----
    def list_storages(self):
        data = self._get("/api/admin/storage/list") or {}
        content = data.get("content") if isinstance(data, dict) else data
        return content or []

    def list_settings(self):
        items = []
        for group in range(6):
            try:
                data = self._get("/api/admin/setting/list?group=%d" % group)
            except SetupError:
                continue
            if isinstance(data, list):
                items.extend(data)
            elif isinstance(data, dict):
                items.extend(data.get("content") or [])
        return items

    def save_setting(self, key, value):
        self._post("/api/admin/setting/save",
                   [{"key": key, "value": str(value)}])

    def list_users(self):
        data = self._get("/api/admin/user/list") or {}
        content = data.get("content") if isinstance(data, dict) else data
        return content or []

    def enable_guest(self):
        for user in self.list_users():
            if user.get("username") != "guest":
                continue
            if not user.get("disabled"):
                return False
            payload = dict(user)
            payload["disabled"] = False
            payload.pop("password", None)
            self._post("/api/admin/user/update", payload)
            return True
        return False

    def ensure_media_storage(self, media_dir):
        """确保 /media 本地存储存在并指向本项目 media 目录。"""
        storages = self.list_storages()
        target = None
        for storage in storages:
            if storage.get("mount_path") == "/media":
                target = storage
                break
        addition = {
            "root_folder_path": media_dir,
            "thumbnail": False,
            "show_hidden": False,
            "mkdir_perm": "777",
        }
        if target is None:
            self._post("/api/admin/storage/create", {
                "mount_path": "/media",
                "driver": "Local",
                "cache_expiration": 0,
                "order": 0,
                "remark": "WatchParty only",
                "addition": json.dumps(addition, ensure_ascii=False),
                "enable_sign": False,
                "disabled": False,
            })
            return "created"
        addition_data = {}
        try:
            addition_data = json.loads(target.get("addition") or "{}")
        except ValueError:
            pass
        current_root = addition_data.get("root_folder_path", "")
        same = os.path.normcase(os.path.normpath(current_root)) == \
            os.path.normcase(os.path.normpath(media_dir))
        if same and not target.get("enable_sign") and not target.get("disabled"):
            return "ok"
        payload = dict(target)
        merged = dict(addition_data)
        merged["root_folder_path"] = media_dir
        payload["addition"] = json.dumps(merged, ensure_ascii=False)
        payload["enable_sign"] = False
        payload["disabled"] = False
        self._post("/api/admin/storage/update", payload)
        return "updated" if not same else "ok"


def ensure_admin_password():
    """保证存在已知的管理员密码；新数据库时初始化。"""
    password = read_admin_password()
    if password:
        return password
    password = "WP-%s-%s-%s" % (secrets.token_hex(2), secrets.token_hex(2),
                                secrets.token_hex(2))
    result = run_alist_admin(["admin", "set", password])
    if result.returncode != 0:
        raise SetupError(
            "无法初始化 AList 管理员密码：%s" % (result.stdout or "").strip()[:300])
    write_admin_password(password)
    log("已生成 AList 管理员密码并保存到 WatchParty%sADMIN_PASSWORD.txt（请勿外传）。" % os.sep)
    return password


def configure_alist():
    """确保 sign_all 关闭、guest 可匿名读、/media 存储正确指向本项目。"""
    password = ensure_admin_password()
    admin = AlistAdmin("http://127.0.0.1:%d" % ALIST_PORT, password)

    for item in admin.list_settings():
        if item.get("key") == "sign_all" and str(item.get("value")).lower() == "true":
            admin.save_setting("sign_all", "false")
            log("已关闭 AList 全局签名（sign_all），否则观看者无法直接播放。")
            break

    if admin.enable_guest():
        log("已启用 guest 匿名只读账号（观看者无需登录 AList）。")

    os.makedirs(MEDIA_DIR, exist_ok=True)
    outcome = admin.ensure_media_storage(MEDIA_DIR)
    if outcome == "created":
        log("已自动创建 /media 存储 → %s" % MEDIA_DIR)
    elif outcome == "updated":
        log("已把 /media 存储指向本项目的 %s（原配置包含其他机器的路径）。" % MEDIA_DIR)
    else:
        log("/media 存储已存在且指向正确。")
    return admin


# ----------------------------------------------------------------------
# 第 3 步：匿名 + Range 共享探测
# ----------------------------------------------------------------------
def verify_sharing():
    """写入探测文件，用与观看者完全相同的匿名方式验证，然后清理。"""
    probe_path = os.path.join(MEDIA_DIR, PROBE_NAME)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    with open(probe_path, "wb") as stream:
        stream.write(secrets.token_bytes(PROBE_SIZE))
    try:
        url = "http://127.0.0.1:%d/d/media/%s" % (ALIST_PORT, PROBE_NAME)
        result = alist_diagnostics.probe_media_url(url, timeout=8.0)
        report = result.format_report()
        log("")
        log(report)
        log("")
        if not result.ok:
            raise SetupError(
                "共享自检未通过。按上面的报告处理后，重新运行本向导。\n"
                "常见原因：存储或全局签名未关闭、guest 被停用、/media 设置了访问密码。")
        log("自检通过：观看者无需登录即可匿名播放，且支持拖动所需的 HTTP Range。")
        return True
    finally:
        try:
            os.remove(probe_path)
        except OSError:
            pass


# ----------------------------------------------------------------------
# 第 4 步：Tailscale 检测与 alist_server 自动设置
# ----------------------------------------------------------------------
def apply_tailscale(config_path=UI_CONFIG, install_if_missing=False):
    """检测 Tailscale 并把 alist_server 写为 http://<Tailscale IPv4>:5244。"""
    status = tailscale_integration.query_status()
    if not status.get("installed"):
        installer_hint = ("请运行 WatchParty\\Tailscale\\install-tailscale.bat 安装并登录，"
                          if IS_WINDOWS else
                          "请运行包内 WatchParty/Tailscale/Tailscale 安装包（双击 .pkg）并登录，")
        message = ("未检测到 Tailscale。观看者在外地时需要它才能访问你的 AList。\n"
                   "%s"
                   "然后重新运行本向导。" % installer_hint)
        if install_if_missing:
            if IS_WINDOWS:
                installer = os.path.join(WATCHPARTY_DIR, "Tailscale",
                                         "install-tailscale.bat")
                if os.path.isfile(installer):
                    log("正在打开 Tailscale 官方安装器（签名已校验）...")
                    subprocess.run(["cmd", "/c", installer], check=False)
                    status = tailscale_integration.query_status()
                    if not status.get("installed"):
                        log("安装尚未完成，请安装并登录后重新运行本向导。")
                        return status, message
                else:
                    log("找不到 WatchParty\\Tailscale\\install-tailscale.bat，请手动安装 Tailscale。")
                    return status, message
            else:
                installer = tailscale_integration.INSTALLER_PATH
                if os.path.isfile(installer):
                    log("正在打开 Tailscale 官方安装器（%s）..." % os.path.basename(installer))
                    subprocess.run(["open", installer], check=False)
                    log("请在安装器中完成安装后再运行本向导。")
                    return status, message
                else:
                    log("找不到包内 %s，请从 Tailscale 官网手动安装。" %
                        tailscale_integration.INSTALLER_NAME)
                    return status, message
        else:
            log(message)
            return status, message

    if not status.get("online"):
        log("Tailscale 已安装但尚未登录/连接，正在打开登录窗口 ...")
        try:
            tailscale_integration.open_tailscale(status.get("cli_path"))
        except TailscaleIntegrationError as exc:
            log("提示：%s" % exc)
        return status, ("请在 Tailscale 窗口完成登录，等图标显示已连接后，"
                        "重新运行本向导继续。")

    ipv4 = status.get("ipv4")
    if not ipv4:
        return status, "Tailscale 已连接但没有分配 100.x IPv4 地址，暂无法自动设置媒体地址。"

    origin = "http://%s:%d" % (ipv4, ALIST_PORT)
    # 不动 alist_map：房主可能手动添加过额外目录映射，向导只负责改写
    # 媒体服务器地址与主映射。观看者的 configure-viewer 才会清映射。
    tailscale_integration.update_syncplay_config(config_path, {
        "alist_enabled": "yes",
        "alist_server": origin,
        "alist_root": "~~/../WatchParty/media",
        "alist_virtual_root": "/media",
        "tailscale_mode": "host",
        "tailscale_host": ipv4,
    })
    log("已把媒体地址写入配置：alist_server=%s" % origin)
    log("")
    log("最后一步无法自动完成（需要你的 Tailscale 账号网页授权）：")
    log("  1. 打开 https://login.tailscale.com/admin/machines")
    log("  2. 找到本设备（%s / %s）" % (status.get("dns_name") or "本机", ipv4))
    log("  3. 点击 Share（共享），按提示生成共享链接并发给观看者")
    log("  4. 观看者登录 Tailscale 并接受共享后，即可观看")
    log("")
    log("提醒：观看者地址为 http://%s:%d（仅 tailnet 内可达，未开启公网 Funnel）。" % (ipv4, ALIST_PORT))
    return status, ""


def run_apply_tailscale(config_path=UI_CONFIG, install_if_missing=False):
    """运行独立配置步骤，并用退出码表示 Tailscale 是否真正就绪。"""
    status, message = apply_tailscale(config_path, install_if_missing)
    if message:
        log(message)
    ready = bool(
        status.get("installed") and status.get("online") and status.get("ipv4"))
    return 0 if ready else 1


# ----------------------------------------------------------------------
# 观看者/房主：网络诊断
# ----------------------------------------------------------------------
def _viewer_origin(host, config_path):
    """读取并严格规范为当前架构的 ``http://100.x:5244`` 地址。"""
    address = str(host or "").strip()
    if not address:
        address = tailscale_integration.read_syncplay_config(config_path).get(
            "alist_server", "").strip()
    if not address:
        raise SetupError(
            "房主 alist_server 尚未配置。请运行观看者首次设置，并输入房主提供的 "
            "100.x.x.x 地址。")
    if "://" not in address:
        address = "http://%s:%d" % (address, ALIST_PORT)
    try:
        parsed = urllib.parse.urlsplit(address)
        port = parsed.port
    except ValueError as exc:
        raise SetupError("房主 alist_server 格式无效：%s" % exc) from exc
    if parsed.scheme.lower() != "http":
        raise SetupError(
            "房主 alist_server 必须使用 http://100.x.x.x:5244。当前地址仍像旧的 "
            "Tailscale Serve/HTTPS 配置，请让房主重新运行首次向导。")
    if (not parsed.hostname or parsed.username is not None or
            parsed.password is not None or parsed.query or parsed.fragment or
            parsed.path not in ("", "/")):
        raise SetupError("房主 alist_server 只能填写 http://100.x.x.x:5244，不要附加路径或凭据。")
    try:
        address_ip = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise SetupError("房主地址不是 Tailscale 100.x IPv4，请使用房主提供的 100.x.x.x。") from exc
    if address_ip.version != 4 or address_ip not in TAILSCALE_IPV4_NETWORK:
        raise SetupError("房主地址不是有效的 Tailscale 100.x IPv4（100.64.0.0/10）。")
    if port not in (None, ALIST_PORT):
        raise SetupError("房主 AList 端口必须是 5244，当前端口是 %s。" % port)
    return "http://%s:%d" % (address_ip.compressed, ALIST_PORT), address_ip.compressed


def _run_tailscale_command(cli_path, arguments, timeout=10.0):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            [cli_path] + list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=flags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)


def probe_tailscale_peer(tailscale_status, host, timeout=7.0):
    """返回 ``(True/False/None, 详情)``；None 表示 CLI 无法单独确认。"""
    cli_path = tailscale_status.get("cli_path") or tailscale_integration.locate_tailscale()
    if not cli_path:
        return None, "找不到 tailscale 命令，无法执行 peer ping"
    result = _run_tailscale_command(
        cli_path, ["ping", "--timeout=5s", "--c=1", host], timeout=timeout)
    if isinstance(result, tuple):
        return None, result[1]
    detail = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, detail[:1000]


def _alist_payload(body):
    try:
        payload = json.loads((body or "").lstrip("\ufeff\r\n\t "))
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _anonymous_error(http_status, body, payload=None):
    payload = payload if isinstance(payload, dict) else _alist_payload(body)
    alist_code = payload.get("code") if payload else None
    message = str(payload.get("message") or "") if payload else ""
    combined = (message + "\n" + (body or "")).lower()
    if "guest user is disabled" in combined or ("guest" in combined and "disabled" in combined):
        return "guest", message or "Guest user is disabled"
    if "expire missing" in combined:
        return "signature", message or "expire missing"
    if http_status == 401 or str(alist_code) == "401":
        return "unauthorized", message or "HTTP 401"
    return None, message


def _media_url(origin, media_path):
    text = str(media_path or "").strip().replace("\\", "/")
    if not text:
        raise SetupError("媒体路径为空。")
    if "://" in text:
        parsed = urllib.parse.urlsplit(text)
        expected = urllib.parse.urlsplit(origin)
        if (parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment):
            raise SetupError("--media URL 不能包含凭据、查询参数或片段。")
        if (parsed.scheme.lower(), parsed.hostname, parsed.port or 80) != (
                expected.scheme.lower(), expected.hostname, expected.port or 80):
            raise SetupError("--media URL 必须属于当前房主 alist_server。")
        text = urllib.parse.unquote(parsed.path)
    if text.startswith("/d/"):
        text = text[2:]
    if not text.startswith("/"):
        text = "/" + text
    if not (text == "/media" or text.startswith("/media/")):
        text = "/media" + text
    if any(part == ".." for part in text.split("/")):
        raise SetupError("媒体路径不能包含 '..'。")
    text = posixpath.normpath(text)
    if text == "/media":
        raise SetupError("请提供 /media 下的具体视频文件，而不是目录。")
    return origin + "/d" + urllib.parse.quote(text, safe="/")


def find_anonymous_video(origin, timeout=8.0):
    """通过匿名 AList list API 在 /media 中寻找一个视频用于 Range 探测。"""
    pending = ["/media"]
    visited = set()
    scanned = 0
    while pending and scanned < MAX_MEDIA_SCAN_DIRECTORIES:
        public_path = pending.pop(0)
        if public_path in visited:
            continue
        visited.add(public_path)
        scanned += 1
        status, body = http_post_json(origin + "/api/fs/list", {
            "path": public_path,
            "password": "",
            "page": 1,
            "per_page": 0,
            "refresh": False,
        }, timeout=timeout)
        if status is None:
            return None, "network", body
        payload = _alist_payload(body)
        kind, detail = _anonymous_error(status, body, payload)
        if kind:
            return None, kind, detail
        if status != 200 or not payload or payload.get("code") != 200:
            message = (payload or {}).get("message") if payload else "返回内容不是 AList JSON"
            return None, "list", "HTTP %s / %s" % (status, message)
        data = payload.get("data") or {}
        entries = data.get("content") or [] if isinstance(data, dict) else []
        if not isinstance(entries, list):
            return None, "list", "AList 目录响应缺少 content"
        files = []
        directories = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "")
            if not name or "/" in name or "\\" in name:
                continue
            child = posixpath.join(public_path, name)
            if entry.get("is_dir"):
                directories.append(child)
            elif posixpath.splitext(name)[1].lower() in VIDEO_EXTENSIONS:
                files.append(child)
        if files:
            return _media_url(origin, sorted(files, key=str.lower)[0]), None, ""
        pending.extend(sorted(directories, key=str.lower))
    if pending:
        return None, "scan_limit", "目录过多，自动扫描只检查了前 %d 个目录" % scanned
    return None, "empty", "/media 中没有可用于检测的常见视频文件"


def _log_anonymous_failure(result):
    message = str(getattr(result, "alist_message", "") or "")
    result_text = str(getattr(result, "result", "") or "")
    combined = (message + "\n" + result_text).lower()
    http_status = getattr(result, "http_status", None)
    alist_code = getattr(result, "alist_code", None)
    if "guest user is disabled" in combined or ("guest" in combined and "disabled" in combined):
        log("AList 游客访问未开启（Guest user is disabled）。")
        log("下一步：请房主重新运行“%s”，启用 guest 匿名只读访问。" % HOST_FIRST_RUN_NAME)
    elif getattr(result, "signature_required", False) or "expire missing" in combined:
        log("AList 仍启用了签名（expire missing），匿名视频 URL 无法使用。")
        log("下一步：请房主重新运行“%s”，关闭全局和 /media 存储签名。" % HOST_FIRST_RUN_NAME)
    elif http_status == 401 or alist_code == 401:
        log("AList 视频 URL 匿名访问返回 401，游客读取权限尚未正确开放。")
        log("下一步：请房主检查 guest 是否启用，以及 /media 是否设置了密码。")
    elif http_status != 206 or not getattr(result, "range_supported", False):
        log("视频服务器不支持 HTTP Range：Range 请求未返回 206 Partial Content。")
        log("实际结果：%s" % (result_text or getattr(result, "status_text", "未知")))
    else:
        log("AList 视频 URL 无法匿名访问：%s" % (result_text or "未知错误"))
        log("下一步：请房主重新运行“%s”完成 AList 自检。" % HOST_FIRST_RUN_NAME)


def diagnose_host(host=None, media_path=None, config_path=UI_CONFIG):
    """观看者端：逐层诊断 Tailscale、5244、AList 匿名访问和 Range。"""
    log("[WatchParty Doctor - 观看者]")
    log("")
    tailscale_status = tailscale_integration.query_status()
    if not tailscale_status.get("installed"):
        log("Tailscale 程序        FAIL")
        log("诊断结论：Tailscale 未安装。")
        if IS_WINDOWS:
            log("下一步：运行 WatchParty\\Tailscale\\install-tailscale.bat，安装后使用自己的账号登录。")
        else:
            log("下一步：双击包内 WatchParty/Tailscale/%s 完成安装，再用自己的账号登录。"
                % tailscale_integration.INSTALLER_NAME)
        return 1
    log("Tailscale 程序        OK")
    backend_state = str(tailscale_status.get("backend_state") or "Unknown")
    if backend_state.lower() != "running" or not tailscale_status.get("online"):
        log("Tailscale 状态        FAIL (%s)" % backend_state)
        log("诊断结论：Tailscale 未登录或未连接。")
        log("下一步：打开 Tailscale，完成登录并等到状态显示 Running/已连接。")
        return 1
    log("Tailscale 状态        Running")

    try:
        origin, peer_ip = _viewer_origin(host, config_path)
    except SetupError as exc:
        log("alist_server          FAIL")
        log("诊断结论：%s" % exc)
        return 1
    log("alist_server          %s" % origin)

    reachable, reach_detail = probe_tailscale_peer(tailscale_status, peer_ip)
    if reachable is False:
        log("房主 Tailscale ping   未确认（继续检测 5244 TCP）")
    if reachable is True:
        log("房主 Tailscale 地址   OK (%s)" % peer_ip)
    elif reachable is None:
        log("房主 Tailscale 地址   待由 5244 连接确认")

    if not probe_port(peer_ip, ALIST_PORT, timeout=3.0):
        log("AList 端口            FAIL (%d)" % ALIST_PORT)
        log("诊断结论：无法访问房主 AList 5244 端口。")
        if reachable is True:
            log("Tailscale 网络已能到达房主；请让房主启动 AList，并检查系统防火墙。")
        elif reachable is False:
            log("Tailscale ping 和 5244 TCP 均失败。最常见原因是尚未接受房主设备共享；")
            log("也可能是房主离线、房主尚未启动 AList，或房主地址已经变化。")
            log("下一步：接受房主 Device Share 邀请，并确认房主电脑在线且已运行“启动.bat”。")
            if reach_detail:
                log("Tailscale 详情：%s" % reach_detail)
        else:
            log("请确认已接受房主设备共享、房主在线并已运行“启动.bat”。")
        return 1
    log("AList 端口            OK (5244)")
    if reachable is False:
        log("房主 Tailscale 地址   OK（5244 TCP 已连通；忽略 ping 的假阴性）")
    elif reachable is None:
        log("房主 Tailscale 地址   OK（已由 5244 TCP 连接确认）")

    ping_status, ping_body = http_get(origin + "/ping", timeout=8.0)
    if not _ping_response_ok(ping_status, ping_body):
        log("AList /ping           FAIL")
        if ping_status is None:
            log("诊断结论：5244 端口可连接，但 /ping 请求失败：%s" % ping_body)
        else:
            log("诊断结论：AList /ping 未返回 pong（HTTP %s）；该端口可能不是 AList。" % ping_status)
        return 1
    log("AList /ping           pong")

    if media_path:
        try:
            media_url = _media_url(origin, media_path)
        except SetupError as exc:
            log("AList 视频 URL        FAIL")
            log("诊断结论：%s" % exc)
            return 1
    else:
        media_url, discovery_error, detail = find_anonymous_video(origin)
        if not media_url:
            log("AList 匿名目录        FAIL")
            if discovery_error == "guest":
                log("诊断结论：AList 游客访问未开启（Guest user is disabled）。")
            elif discovery_error == "signature":
                log("诊断结论：AList 仍启用了签名（expire missing）。")
            elif discovery_error == "unauthorized":
                log("诊断结论：AList 匿名访问返回 401，游客读取权限未开启。")
            elif discovery_error == "empty":
                log("诊断结论：%s，无法验证匿名视频 URL 和 HTTP Range。" % detail)
                log("下一步：请房主把至少一个视频放入 %s 后重试。" % ui_path("WatchParty/media"))
            else:
                log("诊断结论：无法匿名读取房主 /media：%s" % detail)
            return 1
        log("AList 匿名目录        OK")

    log("AList 视频 URL        %s" % media_url)
    result = alist_diagnostics.probe_media_url(media_url, timeout=10.0)
    if (not result.ok or result.http_status != 206 or
            not result.range_supported):
        log("匿名视频访问          FAIL")
        _log_anonymous_failure(result)
        return 1
    log("匿名视频访问          OK")
    log("HTTP Range           206 Partial Content")
    log("")
    log("Result:")
    log("观看者网络环境已就绪，可以直接由 mpv 加载房主视频。")
    return 0


def inspect_host_alist():
    """读取 AList 管理状态，不修改管理员、存储或全局设置。"""
    password = read_admin_password()
    if not password:
        raise SetupError("缺少 %s，无法核对 AList 管理设置。" % ui_path("WatchParty/ADMIN_PASSWORD.txt"))
    admin = AlistAdmin("http://127.0.0.1:%d" % ALIST_PORT, password)
    settings = {item.get("key"): str(item.get("value") or "")
                for item in admin.list_settings() if isinstance(item, dict)}
    guest = next((item for item in admin.list_users()
                  if item.get("username") == "guest"), None)
    storage = next((item for item in admin.list_storages()
                    if item.get("mount_path") == "/media"), None)
    storage_root = ""
    if storage:
        try:
            storage_root = json.loads(storage.get("addition") or "{}").get(
                "root_folder_path", "")
        except ValueError:
            storage_root = ""
    same_root = bool(storage_root) and (
        os.path.normcase(os.path.normpath(storage_root)) ==
        os.path.normcase(os.path.normpath(MEDIA_DIR)))
    sign_all = settings.get("sign_all", "false").strip().lower() == "true"
    return {
        "media_ok": bool(storage and not storage.get("disabled") and same_root),
        "media_root": storage_root,
        "guest_ok": bool(guest and not guest.get("disabled")),
        "sign_off": bool(storage and not storage.get("enable_sign") and not sign_all),
    }


def probe_local_range():
    """用短暂探测文件从匿名访问路径核对本机 AList 的 206。"""
    os.makedirs(MEDIA_DIR, exist_ok=True)
    while True:
        probe_name = "_watchparty_doctor_%s.bin" % secrets.token_hex(8)
        probe_path = os.path.join(MEDIA_DIR, probe_name)
        try:
            with open(probe_path, "xb") as stream:
                stream.write(secrets.token_bytes(PROBE_SIZE))
            break
        except FileExistsError:
            continue
    try:
        return alist_diagnostics.probe_media_url(
            "http://127.0.0.1:%d/d/media/%s" % (ALIST_PORT, probe_name),
            timeout=8.0)
    finally:
        try:
            os.remove(probe_path)
        except OSError:
            pass


def _truthy_funnel(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return any(_truthy_funnel(item) for item in value.values())
    if isinstance(value, list):
        return any(_truthy_funnel(item) for item in value)
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def query_funnel_enabled(tailscale_status):
    """返回 ``(True/False/None, 详情)``，绝不把无法判断伪装成 OFF。"""
    cli_path = tailscale_status.get("cli_path") or tailscale_integration.locate_tailscale()
    if not cli_path:
        return None, "找不到 tailscale.exe"
    result = _run_tailscale_command(cli_path, ["funnel", "status", "--json"], timeout=10.0)
    if isinstance(result, tuple):
        return None, result[1]
    output = (result.stdout or result.stderr or "").strip()
    lowered = output.lower()
    if result.returncode != 0:
        if "no serve config" in lowered or "no funnel config" in lowered:
            return False, output
        return None, output or "tailscale funnel status 执行失败"
    try:
        payload = json.loads(output or "{}")
    except ValueError:
        if "no serve config" in lowered or "no funnel config" in lowered:
            return False, output
        if "available on the internet" in lowered or "funnel on" in lowered:
            return True, output
        return None, output or "Funnel 状态输出为空"
    if not isinstance(payload, dict):
        return None, "Funnel 状态 JSON 格式未知"
    allow_funnel = payload.get("AllowFunnel")
    return _truthy_funnel(allow_funnel), output


def diagnose_local_host(config_path=UI_CONFIG):
    """房主端最终 doctor；只读配置，Range 探测文件会立即删除。"""
    log("[WatchParty Doctor - 房主]")
    log("")
    failures = []
    alist_program = os.path.isfile(ALIST_EXE)
    log("AList 程序           %s" % ("OK" if alist_program else "FAIL"))
    if not alist_program:
        failures.append("缺少随包 AList（%s）" % ALIST_EXE)

    ping_status, ping_body = http_get(
        "http://127.0.0.1:%d/ping" % ALIST_PORT, timeout=5.0)
    alist_service = _ping_response_ok(ping_status, ping_body)
    log("AList 服务           %s" % ("OK" if alist_service else "FAIL"))
    if not alist_service:
        failures.append("AList 服务未运行或 /ping 未返回 pong")

    listen_text = "未知"
    listen_ok = False
    try:
        with open(ALIST_CONFIG, "r", encoding="utf-8-sig") as stream:
            alist_config = json.load(stream)
        scheme = alist_config.get("scheme") or {}
        listen_text = "%s:%s" % (scheme.get("address"), scheme.get("http_port"))
        listen_ok = (scheme.get("address") == "0.0.0.0" and
                     int(scheme.get("http_port")) == ALIST_PORT)
    except (OSError, TypeError, ValueError):
        pass
    log("监听地址             %s%s" % (listen_text, "" if listen_ok else " (FAIL)"))
    if not listen_ok:
        failures.append("AList 未监听 0.0.0.0:5244")

    alist_state = None
    if alist_service:
        try:
            alist_state = inspect_host_alist()
        except (OSError, ValueError, SetupError, urllib.error.URLError) as exc:
            failures.append("无法读取 AList 管理设置：%s" % exc)
    media_ok = bool(alist_state and alist_state.get("media_ok"))
    guest_ok = bool(alist_state and alist_state.get("guest_ok"))
    sign_off = bool(alist_state and alist_state.get("sign_off"))
    log("/media               %s" % ("OK" if media_ok else "FAIL"))
    log("Guest                %s" % ("OK" if guest_ok else "FAIL"))
    log("Sign                 %s" % ("OFF" if sign_off else "FAIL"))
    if not media_ok:
        failures.append("/media 未指向当前包的 %s" % ui_path("WatchParty/media"))
    if not guest_ok:
        failures.append("AList 游客访问未开启")
    if not sign_off:
        failures.append("AList 仍启用了签名")

    tailscale_status = tailscale_integration.query_status()
    tailscale_ok = (tailscale_status.get("installed") and
                    tailscale_status.get("online") and
                    str(tailscale_status.get("backend_state") or "").lower() == "running")
    log("Tailscale            %s" % ("Running" if tailscale_ok else "FAIL"))
    ipv4 = tailscale_status.get("ipv4") or ""
    log("Tailscale IPv4       %s" % (ipv4 or "FAIL"))
    if not tailscale_ok:
        failures.append("Tailscale 未安装、未登录或未连接")
    if not ipv4:
        failures.append("Tailscale 没有可用的 100.x IPv4")

    syncplay_config = tailscale_integration.read_syncplay_config(config_path)
    configured_origin = syncplay_config.get("alist_server", "")
    expected_origin = "http://%s:%d" % (ipv4, ALIST_PORT) if ipv4 else ""
    origin_ok = bool(expected_origin and configured_origin.rstrip("/") == expected_origin)
    log("alist_server         %s%s" % (
        configured_origin or "FAIL", "" if origin_ok else " (FAIL)"))
    if not origin_ok:
        failures.append("alist_server 未写成当前 Tailscale IPv4 的 http://100.x.x.x:5244")

    range_ok = False
    if alist_service and media_ok and guest_ok and sign_off:
        try:
            range_result = probe_local_range()
            range_ok = bool(range_result.ok and range_result.range_supported and
                            range_result.http_status == 206)
        except OSError as exc:
            failures.append("HTTP Range 探测失败：%s" % exc)
    log("HTTP Range           %s" % ("206 OK" if range_ok else "FAIL"))
    if not range_ok:
        failures.append("视频服务器不支持 HTTP Range，或 Range 请求未返回 206")

    funnel_enabled, funnel_detail = query_funnel_enabled(tailscale_status)
    if funnel_enabled is False:
        log("Funnel               OFF")
    elif funnel_enabled is True:
        log("Funnel               ON (FAIL)")
        failures.append("Tailscale Funnel 已开启，必须关闭")
    else:
        log("Funnel               无法确认 (FAIL)")
        failures.append("无法可靠确认 Funnel 已关闭：%s" % funnel_detail)

    log("")
    log("Device Share:")
    log("需要在 Tailscale Admin Console → Machines → 房主设备 → Share 手动确认")
    log("")
    log("Result:")
    if failures:
        log("房主共享环境尚未就绪：")
        for failure in failures:
            log("- %s" % failure)
        return 1
    log("房主共享环境已就绪")
    return 0


def run_doctor(host=None, media_path=None, config_path=UI_CONFIG, role="auto"):
    role = str(role or "auto").lower()
    if role == "auto":
        configured_role = tailscale_integration.read_syncplay_config(
            config_path).get("tailscale_mode", "").strip().lower()
        role = "viewer" if host or configured_role == "viewer" else "host"
    if role == "host":
        return diagnose_local_host(config_path)
    return diagnose_host(host, media_path, config_path)


# ----------------------------------------------------------------------
# 房主完整向导
# ----------------------------------------------------------------------
def run_host_wizard(config_path=UI_CONFIG, install_if_missing=False):
    log("=" * 56)
    log("  WatchParty 房主首次运行向导")
    log("=" * 56)
    log("")
    log("[1/4] 检查并启动随包 AList")
    ensure_alist()
    log("")
    log("[2/4] 自动配置 AList（签名 / 匿名 guest / /media 存储）")
    configure_alist()
    log("")
    log("[3/4] 共享自检（匿名访问 + HTTP Range，与观看者视角一致）")
    verify_sharing()
    log("")
    log("[4/4] 检测 Tailscale 并写入观看者要使用的地址")
    status, message = apply_tailscale(config_path, install_if_missing)
    if message:
        log(message)
    tailscale_ready = bool(
        status.get("installed") and status.get("online") and status.get("ipv4"))
    if not tailscale_ready:
        log("")
        log("=" * 56)
        log("  房主首次设置尚未完成：Tailscale 还未就绪。")
        log("  已完成的 AList、/media、Guest、Sign 和 Range 配置均已保留，")
        log("  不会回滚或重置。请完成上面的 Tailscale 操作后重新运行本向导。")
        log("=" * 56)
        return 1
    log("")
    log("[最终检查] 确认房主共享环境与 Funnel 状态")
    if diagnose_local_host(config_path) != 0:
        log("")
        log("=" * 56)
        log("  房主首次设置尚未完成：最终 doctor 未通过。")
        log("  已完成的配置均已保留，不会自动修改 Funnel/Serve。")
        log("  请按 doctor 报告处理后重新运行本向导。")
        log("=" * 56)
        return 1
    log("")
    log("=" * 56)
    log("  向导完成。日常使用：")
    log("    1. 把视频放进 %s（默认只自动共享此目录；" % ui_path("WatchParty/media"))
    log("       外部路径必须已经配置 alist_map 才能供观看者播放）")
    log("    2. 启动 mpv，按 Ctrl+Shift+S 打开面板，创建房间并播放")
    log("    3. 观看者加入同一 Syncplay 服务器与房间即可自动加载视频")
    log("=" * 56)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="WatchParty 房主向导与网络诊断")
    parser.add_argument("--json", action="store_true", help="输出 JSON（供面板读取）")
    parser.add_argument("--config", default=UI_CONFIG, help="syncplay_ui.conf 路径")
    subparsers = parser.add_subparsers(dest="command")

    full = subparsers.add_parser("full", help="房主首次运行完整向导")
    full.add_argument("--install-tailscale", action="store_true",
                      help="未安装 Tailscale 时自动打开官方安装器")

    subparsers.add_parser("ensure-alist", help="仅检查/启动 AList")
    subparsers.add_parser("configure-alist", help="仅配置签名/guest//media 存储")
    subparsers.add_parser("verify-sharing", help="仅执行匿名+Range 共享自检")
    tailscale = subparsers.add_parser("apply-tailscale", help="仅检测 Tailscale 并写 alist_server")
    tailscale.add_argument("--install-if-missing", action="store_true")

    doctor = subparsers.add_parser("doctor", help="诊断房主共享环境或观看者到房主的网络")
    doctor.add_argument("host", nargs="?", help="房主地址（100.x.x.x 或完整 URL）")
    doctor.add_argument("--media", default=None, help="可选：要检测的 /media 下文件路径")
    doctor.add_argument("--role", choices=("auto", "host", "viewer"), default="auto",
                        help="诊断角色；默认按 tailscale_mode 自动判断")

    args = parser.parse_args(argv)
    if not args.command:
        args.command = "full"
        args.install_tailscale = False

    actions = {
        "full": lambda: run_host_wizard(args.config, args.install_tailscale),
        "ensure-alist": lambda: (ensure_alist(), 0)[1],
        "configure-alist": lambda: (configure_alist(), 0)[1],
        "verify-sharing": lambda: (verify_sharing(), 0)[1],
        "apply-tailscale": lambda: run_apply_tailscale(
            args.config, args.install_if_missing),
        "doctor": lambda: run_doctor(args.host, args.media, args.config, args.role),
    }
    try:
        code = actions[args.command]() or 0
        if args.json:
            print(json.dumps({"ok": code == 0}, ensure_ascii=True))
        return code
    except SetupError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        else:
            log("[WatchParty 设置] 出现问题：")
            log(str(exc))
        return 1
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        else:
            log("[WatchParty 设置] 出现问题：%s" % exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
