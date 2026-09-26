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
# 房主放行防火墙时使用的规则名与脚本（与房主模式一键配置保持一致）。
FIREWALL_RULE_NAME = "MPV Syncplay WatchParty AList"
HOST_FIREWALL_SCRIPT = os.path.join(
    WATCHPARTY_DIR, "Tailscale", "configure-host-firewall.bat")
# 用户取消了 UAC 授权；与普通失败区分，便于给出"重新点击并选择是"的提示。
ELEVATION_CANCELLED = -2


class SetupError(RuntimeError):
    """带中文提示的向导错误。"""


# ----------------------------------------------------------------------
# 通用工具
# ----------------------------------------------------------------------
_LOG_TO_STDERR = False


def log(message):
    """人类可读的进度输出。

    ``--json`` 模式下全部改走 stderr，保证 stdout 恰好只有最后一行 JSON，
    这样 mpv 面板才能直接解析（子进程的 stdout 也会被并入 stderr）。
    """
    print(message, file=sys.stderr if _LOG_TO_STDERR else sys.stdout, flush=True)


def ui_path(text):
    """面向用户的路径文本：Windows 用反斜杠，macOS/Linux 用正斜杠。"""
    return text.replace("/", "\\") if IS_WINDOWS else text


# 房主首次设置入口：Windows 批处理，macOS 用 .command。
HOST_FIRST_RUN_NAME = "首次运行.bat" if IS_WINDOWS else "首次设置.command"


# ----------------------------------------------------------------------
# 第 0 步：Windows 防火墙（仅放行 Tailscale 网段，需要管理员权限）
# ----------------------------------------------------------------------
def is_admin():
    """当前进程是否已具备管理员权限（非 Windows 恒为 False）。"""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - 仅在异常 shell 上触发
        return False


def elevate_and_wait(target, parameters=None, timeout=900.0):
    """以管理员身份运行 target 并等待结束，返回退出码。

    用户拒绝 UAC 时返回 ``ELEVATION_CANCELLED``。只用于必须提权的单步操作
    （netsh 防火墙规则）；命令窗口以隐藏方式启动，不干扰正在播放的 mpv。
    """
    import ctypes
    from ctypes import wintypes

    see_mask_noclose_process = 0x00000040
    see_mask_noasync = 0x00000100
    sw_hide = 0
    error_cancelled = 1223
    wait_timeout = 0x00000102

    class Shellexecuteinfow(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    # use_last_error=True：ctypes 内部调用可能覆盖线程错误码，
    # 必须用 ctypes.get_last_error() 读取 ShellExecuteExW 保存的错误。
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    info = Shellexecuteinfow()
    info.cbSize = ctypes.sizeof(Shellexecuteinfow)
    info.fMask = see_mask_noclose_process | see_mask_noasync
    info.lpVerb = "runas"
    info.lpFile = target
    info.lpParameters = parameters
    info.lpDirectory = os.path.dirname(target) or None
    info.nShow = sw_hide
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == error_cancelled:
            return ELEVATION_CANCELLED
        raise SetupError("无法启动管理员进程（Windows 错误码 %s）。" % error)
    if not info.hProcess:
        return 0
    try:
        if kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000)) == wait_timeout:
            raise SetupError("管理员进程超过 %.0f 秒仍未结束，已放弃等待。" % timeout)
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise SetupError("无法读取管理员进程的退出码。")
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


def _run_hidden(arguments, timeout=900.0):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        arguments,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        creationflags=flags,
        check=False,
    )
    return completed.returncode


def firewall_rule_present():
    """只读检查房主放行规则是否已存在且内容正确（不需要管理员权限）。

    ``netsh`` 的字段名会跟随系统语言本地化，所以这里只比对与语言无关的
    端口、来源网段（GBK/UTF-8 都与 ASCII 兼容，中文输出不会影响匹配）。
    规则已存在时无需再次弹出 UAC——否则用户每次点「切换为房主模式」都要
    授权一次，漏点或点"否"就表现为"点了没反应"。
    """
    if not IS_WINDOWS:
        return False
    try:
        completed = subprocess.run(
            [
                "netsh", "advfirewall", "firewall", "show", "rule",
                "name=%s" % FIREWALL_RULE_NAME, "verbose",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if completed.returncode != 0:
        return False
    output = completed.stdout or ""
    return "5244" in output and "100.64.0.0/10" in output


def configure_host_firewall():
    """放行 100.64.0.0/10 访问随包 AList 的 5244 端口。

    只对这一步按需提权（netsh），且规则严格限制来源网段，不使用
    "程序首次监听时由系统弹窗放行" 的做法——那会同时向局域网/公网开放。
    非 Windows 平台无需该规则，直接返回 unsupported。
    """
    if not IS_WINDOWS:
        return {"configured": False, "reason": "unsupported"}
    if not os.path.isfile(ALIST_EXE):
        raise SetupError(
            "没有找到随包的 AList（%s），无法配置防火墙规则。" % ALIST_EXE)
    if not os.path.isfile(HOST_FIREWALL_SCRIPT):
        raise SetupError(
            "找不到 %s，无法配置防火墙规则。" % ui_path("WatchParty/Tailscale/configure-host-firewall.bat"))

    if firewall_rule_present():
        log("防火墙规则已存在且匹配（仅 Tailscale 网段可访问 5244），跳过授权。")
        return {
            "configured": True,
            "rule": FIREWALL_RULE_NAME,
            "reused": True,
            "elevated": False,
        }

    if is_admin():
        log("已经具备管理员权限，直接写入防火墙规则。")
        code = _run_hidden(["cmd.exe", "/c", HOST_FIREWALL_SCRIPT])
    else:
        log("修改 Windows 防火墙需要管理员权限，正在弹出授权窗口 ...")
        code = elevate_and_wait(HOST_FIREWALL_SCRIPT)

    if code == 0:
        log("防火墙已放行：仅 Tailscale 网段（100.64.0.0/10）可访问 AList 的 5244 端口。")
        return {"configured": True, "rule": FIREWALL_RULE_NAME}
    if code == ELEVATION_CANCELLED:
        raise SetupError(
            "已取消管理员授权，防火墙规则没有写入。\n"
            "房主模式必须放行 Tailscale 网段，请重新选择房主模式并在弹窗中点击“是”。")
    raise SetupError(
        "防火墙规则写入失败（退出码 %s）。\n"
        "可能是安全软件或组策略限制；请右键以管理员身份运行 "
        "WatchParty\\Tailscale\\configure-host-firewall.bat 后重试。" % code)


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


def _pid_listening_on(port):
    """返回独占监听本机 TCP 端口的进程号；拿不到时返回 None。

    只按"本地地址以 :端口 结尾"且"外部地址以 :0 结尾"匹配，不依赖状态列
    文案（中文/英文系统的 netstat 表头与状态列都可能被本地化）。
    """
    if not IS_WINDOWS:
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace",
            timeout=20, creationflags=flags, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    suffix = ":%d" % port
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if not parts[1].endswith(suffix) or not parts[2].endswith(":0"):
            continue
        if parts[-1].isdigit():
            return int(parts[-1])
    return None


def _process_image_path(pid):
    """返回进程的可执行文件全路径；进程已退出或权限不足时为 None。"""
    if not IS_WINDOWS or not pid:
        return None
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    except (AttributeError, OSError):
        return None
    try:
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel.QueryFullProcessImageNameW.restype = ctypes.c_int
        kernel.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint32)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            size = ctypes.c_uint32(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel.QueryFullProcessImageNameW(
                    handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value or None
        finally:
            kernel.CloseHandle(handle)
    except (AttributeError, OSError):
        return None


def _is_watchparty_alist_path(path):
    """路径是否形如 <任意目录>\\WatchParty\\alist\\alist.exe。"""
    if not path:
        return False
    if os.path.basename(path).lower() not in ("alist.exe", "alist"):
        return False
    parent = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    return os.path.basename(parent).lower() == "watchparty"


def _same_executable(path):
    """路径是否就是本目录的 AList（兼容 8.3 短名、subst、目录联接）。"""
    try:
        return os.path.samefile(ALIST_EXE, path)
    except OSError:
        pass
    try:
        return os.path.normcase(os.path.realpath(path)) == \
            os.path.normcase(os.path.realpath(ALIST_EXE))
    except OSError:
        return False


def alist_listener(port=None):
    """5244 的监听者若是 AList 进程，返回 (pid, 可执行文件全路径)。

    返回 None 表示端口没有被 AList 占用（可能是别的程序，也可能查不到）。
    """
    pid = _pid_listening_on(ALIST_PORT if port is None else port)
    if pid is None:
        return None
    path = _process_image_path(pid)
    if not path:
        return None
    if os.path.basename(path).lower() not in ("alist.exe", "alist"):
        return None
    return pid, path


def stop_alist_pid(pid):
    """强制停止指定进程并等它放开 5244；成功返回 True。"""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/F"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=30, creationflags=flags, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log("警告：无法停止进程 %s（%s）。" % (pid, exc))
    deadline = time.time() + 10
    while time.time() < deadline:
        if not probe_port("127.0.0.1", ALIST_PORT):
            return True
        time.sleep(0.3)
    return not probe_port("127.0.0.1", ALIST_PORT)


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
    # stdout/stderr 落到 data/log/server-stdout.log：wait_for_alist 失败时只
    # 知道"没就绪"，把 AList 的真实退出原因（端口、配置、被系统拦截等）留在
    # 文件里才能继续排查。
    log_dir = os.path.join(ALIST_DIR, "data", "log")
    os.makedirs(log_dir, exist_ok=True)
    server_log = open(os.path.join(log_dir, "server-stdout.log"), "ab")
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(
            [ALIST_EXE, "server", "--force-bin-dir"],
            cwd=ALIST_DIR,
            stdout=server_log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            close_fds=True,
        )
    finally:
        server_log.close()


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


def _read_server_output_tail(limit=400):
    """读取随包 AList 最近一次启动输出的结尾，拼进错误提示帮助定位。"""
    path = os.path.join(ALIST_DIR, "data", "log", "server-stdout.log")
    try:
        with open(path, "rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - 4096))
            text = stream.read().decode("utf-8", "replace")
    except OSError:
        return ""
    text = " ".join(text.split())[-limit:]
    if not text:
        return ""
    return "最近输出：%s" % text


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


def clear_macos_quarantine():
    """清掉整个安装包的 macOS 隔离标记（best-effort）。

    浏览器下载的 ZIP 解压后所有文件都带 com.apple.quarantine，未公证的
    alist、ziggy（uosc 菜单/输入依赖的辅助程序）等都会被 Gatekeeper 直接
    杀掉，表现为"AList 启动失败"或"面板/输入打不开"。首次设置.command /
    启动.command 的入口已递归清除过；这里兜底覆盖用户直接双击 mpv.app、
    没经过 .command 的场景，所以按整个包根目录递归清理。
    """
    if IS_WINDOWS or not os.path.isfile(ALIST_EXE):
        return
    xattr = "/usr/bin/xattr"
    if not os.path.isfile(xattr):
        return
    try:
        subprocess.run(
            [xattr, "-r", "-d", "com.apple.quarantine", PROJECT_ROOT],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        log("提示：清除隔离标记失败（%s），若面板或 AList 异常请手动执行 "
            "xattr -cr <包目录>。" % exc)


def ensure_alist():
    """保证随包 AList 正在运行；返回 (base_url, started_now)。"""
    if not os.path.isfile(ALIST_EXE):
        raise SetupError(
            "没有找到随包的 AList（%s）。\n"
            "你下载的可能是 GitHub 源码 ZIP；请下载 Release 中的 "
            "WatchParty 完整安装包。" % ALIST_EXE)

    local = "http://127.0.0.1:%d" % ALIST_PORT
    if probe_port("127.0.0.1", ALIST_PORT):
        if alist_ping(local):
            listener = alist_listener()
            if listener is None or _same_executable(listener[1]):
                log("AList 已在运行（复用现有进程，避免 5244 端口重复启动）。")
                return local, False
            if not _is_watchparty_alist_path(listener[1]):
                # 能 ping 通说明是 AList，但不在 WatchParty 目录里：交给用户处理。
                raise SetupError(
                    "端口 %d 已被其他 AList 占用（不是本包的 AList）：%s\n"
                    "请关闭占用该端口的程序，或修改 WatchParty%salist%sdata%s"
                    "config.json 中的 http_port 后重试。" % (
                        (ALIST_PORT, listener[1]) + (os.sep,) * 3))
            # 另一个 WatchParty 副本（例如"下载后解压了两份"）的 AList 先占住了
            # 5244：它的密码和 media 目录都属于那个副本，复用它会直接表现为
            # "AList 管理员登录失败"，即使侥幸登录成功也共享不到本目录的视频。
            log("检测到 %d 端口由另一个 WatchParty 副本的 AList 占用：%s"
                % (ALIST_PORT, listener[1]))
            log("已停止它（pid %s），改用本目录的 AList 与密码。" % listener[0])
            if not stop_alist_pid(listener[0]):
                raise SetupError(
                    "无法停止另一个 WatchParty 副本的 AList（pid %s，%s），"
                    "5244 端口仍被占用。\n"
                    "请手动结束该进程（任务管理器 → 详细信息 → alist.exe）后"
                    "重新点击房主模式。" % (listener[0], listener[1]))
        else:
            raise SetupError(
                "端口 %d 已被其他程序占用（不是随包 AList）。\n"
                "请关闭占用该端口的程序，或修改 WatchParty%salist%sdata%sconfig.json "
                "中的 http_port 后重试。" % ((ALIST_PORT,) + (os.sep,) * 3))

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
    clear_macos_quarantine()
    process = start_alist_process()
    if not wait_for_alist():
        stop_alist_process(process)
        exit_code = process.poll()
        exit_note = "" if exit_code is None else "（进程已退出，退出码 %s）" % exit_code
        raise SetupError(
            "AList 启动失败：%.0f 秒内未在 127.0.0.1:%d 就绪。%s%s\n"
            "请查看 %s 排查。" % (
                STARTUP_TIMEOUT, ALIST_PORT, exit_note, _read_server_output_tail(),
                ui_path("WatchParty/alist/data/log/")))
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
                "常见原因：5244 端口上运行的是**另一个** WatchParty 副本的 AList"
                "（它的密码与本目录的 %s 不一致），或本目录的密码文件已过期。\n"
                "请先结束多余的 alist.exe（任务管理器 → 详细信息）后重新点击房主"
                "模式；若仍失败，可删除 %s 让向导重建 AList 数据与密码。" % (
                    body.get("message"), ui_path("WatchParty/ADMIN_PASSWORD.txt"),
                    ui_path("WatchParty/alist/data/")))
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


def _reset_admin_password_and_login(base_url, password):
    """密码对不上时用包内 alist 重新设置，再登录一次（自愈）。"""
    result = run_alist_admin(["admin", "set", password])
    if result.returncode != 0:
        raise SetupError(
            "无法重新设置 AList 管理员密码：%s\n"
            "请关闭多余的 alist.exe 后重新点击房主模式；必要时删除 %s 让向导"
            "重建 AList 数据。" % ((result.stdout or "").strip()[:300],
                                  ui_path("WatchParty/alist/data/")))
    try:
        return AlistAdmin(base_url, password)
    except SetupError as exc:
        raise SetupError(
            "%s\n"
            "重设本目录密码后仍无法登录，说明 5244 端口很可能由另一个 WatchParty "
            "副本的 AList 提供服务。请先结束多余的 alist.exe（任务管理器 → "
            "详细信息），再重新点击房主模式。" % exc)


def configure_alist():
    """确保 sign_all 关闭、guest 可匿名读、/media 存储正确指向本项目。"""
    password = ensure_admin_password()
    base_url = "http://127.0.0.1:%d" % ALIST_PORT
    try:
        admin = AlistAdmin(base_url, password)
    except SetupError:
        # ADMIN_PASSWORD.txt 与当前数据目录里的账号对不上（数据目录被复制、
        # 被别的副本接管、或残留了旧密码文件）。用包内 alist 重新对齐密码后
        # 再试一次，避免用户只能看到无法自行处理的"登录失败"。
        log("检测到 AList 管理员密码与当前数据目录不一致，正在重新设置 ...")
        admin = _reset_admin_password_and_login(base_url, password)
        log("已按 %s 重新设置管理员密码。" % ui_path("WatchParty/ADMIN_PASSWORD.txt"))

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
            "房主 alist_server 尚未配置。请在联机面板选择「运行模式 → 房主模式」"
            "完成自动配置，或输入房主提供的 100.x.x.x 地址。")
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
        log("下一步：请房主在面板「联机 → 运行模式」重新选择房主模式（或重新运行“%s”），"
            "启用 guest 匿名只读访问。" % HOST_FIRST_RUN_NAME)
    elif getattr(result, "signature_required", False) or "expire missing" in combined:
        log("AList 仍启用了签名（expire missing），匿名视频 URL 无法使用。")
        log("下一步：请房主在面板「联机 → 运行模式」重新选择房主模式（或重新运行“%s”），"
            "关闭全局和 /media 存储签名。" % HOST_FIRST_RUN_NAME)
    elif http_status == 401 or alist_code == 401:
        log("AList 视频 URL 匿名访问返回 401，游客读取权限尚未正确开放。")
        log("下一步：请房主检查 guest 是否启用，以及 /media 是否设置了密码。")
    elif http_status != 206 or not getattr(result, "range_supported", False):
        log("视频服务器不支持 HTTP Range：Range 请求未返回 206 Partial Content。")
        log("实际结果：%s" % (result_text or getattr(result, "status_text", "未知")))
    else:
        log("AList 视频 URL 无法匿名访问：%s" % (result_text or "未知错误"))
        log("下一步：请房主在面板「联机 → 运行模式」重新选择房主模式（或重新运行“%s”）"
            "完成 AList 自检。" % HOST_FIRST_RUN_NAME)


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
    log("  WatchParty 房主模式向导")
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


def _safe_tailscale_status():
    """查询 Tailscale 状态；任何异常都降级为空状态，不打断模式切换。"""
    try:
        return tailscale_integration.query_status()
    except Exception as exc:  # noqa: BLE001 - 面板需要稳定的 JSON，而不是堆栈
        return {"error": str(exc)}


def _mode_host_failure_message(status):
    if not status.get("installed"):
        return ("AList、/media 与匿名播放都已配置完成，但还没有安装 Tailscale。\n"
                "请先安装并登录 Tailscale，然后再次选择“房主模式”。")
    if not status.get("online"):
        return ("AList、/media 与匿名播放都已配置完成，但 Tailscale 还没有登录/连接。\n"
                "请在 Tailscale 图标显示已连接后，再次选择“房主模式”。")
    if not status.get("ipv4"):
        return ("Tailscale 已连接但没有分配 100.x IPv4 地址，"
                "请检查 Tailscale 状态后再次选择“房主模式”。")
    return "房主环境最终自检未通过，请查看面板里的“完整诊断”输出后重试。"


# 面板原地更新配置时需要回传的键（与 syncplay_ui.lua 的 apply_tailscale_payload 一致）。
MODE_HOST_KEYS = ("alist_enabled", "alist_server", "alist_root",
                  "alist_virtual_root", "tailscale_mode", "tailscale_host")


def run_mode_host(config_path=UI_CONFIG, install_if_missing=False):
    """从面板一键进入房主模式：防火墙 → AList → 自检 → Tailscale 地址。

    等价于旧版独立的房主首次运行向导，但全程非交互、且不启动第二个 mpv：返回的
    字典由面板在内存里直接应用，用户不必重启。返回 ``ok=False`` 时 AList
    侧已完成的配置仍然保留，可直接重试。
    """
    result = {"ok": False, "tailscale_mode": "host"}
    log("=" * 56)
    log("  WatchParty 切换到房主模式")
    log("=" * 56)
    log("")
    if IS_WINDOWS:
        log("[防火墙] 仅放行 Tailscale 网段访问随包 AList 的 5244 端口")
        result["firewall"] = ("configured" if configure_host_firewall().get("configured")
                              else "unsupported")
        log("")

    code = run_host_wizard(config_path, install_if_missing)

    status = _safe_tailscale_status()
    result["tailscale_ready"] = bool(
        status.get("installed") and status.get("online") and status.get("ipv4"))
    values = tailscale_integration.read_syncplay_config(config_path)
    for key in MODE_HOST_KEYS:
        if values.get(key) is not None:
            result[key] = values[key]
    result["device_share"] = "manual"

    if code != 0:
        result["stage"] = "verify" if result["tailscale_ready"] else "tailscale"
        result["error"] = _mode_host_failure_message(status)
        return result

    result["ok"] = True
    result["message"] = ("房主模式已启用。最后一步需要你在浏览器里完成：打开 "
                         "https://login.tailscale.com/admin/machines ，"
                         "对本设备点击 Share，把共享链接发给观看者。")
    return result


def run_bootstrap(config_path=UI_CONFIG, install_if_missing=True):
    """合并包首次运行：准备共同前置条件，角色留给面板选择。

    与 ``full`` / ``mode-host`` 不同，这里不预设房主或观看者——两种模式都
    只需要 Tailscale 可用，角色本身在 mpv 面板的「运行模式」里选择。因此本
    向导只负责：检测 Tailscale，缺失时打开官方安装器，然后给出下一步指引。
    """
    log("=" * 56)
    log("  WatchParty 首次运行")
    log("=" * 56)
    log("")
    status = _safe_tailscale_status()
    if not status.get("installed") and install_if_missing:
        if IS_WINDOWS:
            installer = os.path.join(WATCHPARTY_DIR, "Tailscale",
                                     "install-tailscale.bat")
            if os.path.isfile(installer):
                log("[1/2] 未检测到 Tailscale，正在打开官方安装器（签名已校验）...")
                subprocess.run(["cmd", "/c", installer], check=False)
            else:
                log("[1/2] 未找到 WatchParty\\Tailscale\\install-tailscale.bat，"
                    "请从 Tailscale 官网手动安装。")
        else:
            installer = tailscale_integration.INSTALLER_PATH
            if os.path.isfile(installer):
                log("[1/2] 未检测到 Tailscale，正在打开官方安装包 ...")
                subprocess.run(["open", installer], check=False)
            else:
                log("[1/2] 未找到包内 Tailscale 安装包，请从 Tailscale 官网手动安装。")
        status = _safe_tailscale_status()

    if status.get("installed"):
        if status.get("online") and status.get("ipv4"):
            log("[1/2] Tailscale 已安装并已连接。")
        else:
            log("[1/2] Tailscale 已安装，但还没有登录/连接。")
            log("      请在托盘图标里登录；房主和观看者都需要它在线。")
    else:
        log("[1/2] 尚未安装 Tailscale：联机观看需要它；只看本机影片可以先跳过。")

    log("")
    log("[2/2] 下一步：")
    log("    1. 双击 WatchParty\\启动.bat 打开 mpv" if IS_WINDOWS
        else "    1. 双击包内的 启动.command 打开 mpv")
    log("    2. 按 Ctrl+Shift+S 打开面板，进入「联机」→「运行模式」")
    log("    3. 选择“房主模式”（共享本机影片）或“观看者模式”（连房主的房间）")
    log("")
    log("房主模式会请求一次管理员权限，用于只放行 Tailscale 网段访问本机 5244 端口；")
    log("观看者模式不开放本机任何端口。")
    return 0


def main(argv=None):
    global _LOG_TO_STDERR
    parser = argparse.ArgumentParser(description="WatchParty 向导与网络诊断")
    parser.add_argument("--json", action="store_true", help="输出 JSON（供面板读取）")
    parser.add_argument("--config", default=UI_CONFIG, help="syncplay_ui.conf 路径")
    subparsers = parser.add_subparsers(dest="command")

    full = subparsers.add_parser("full", help="房主模式完整向导")
    full.add_argument("--install-tailscale", action="store_true",
                      help="未安装 Tailscale 时自动打开官方安装器")

    mode_host = subparsers.add_parser(
        "mode-host", help="从面板一键切换为房主模式（含防火墙规则）")
    mode_host.add_argument("--install-tailscale", action="store_true",
                           help="未安装 Tailscale 时自动打开官方安装器")

    subparsers.add_parser("ensure-alist", help="仅检查/启动 AList")
    subparsers.add_parser(
        "bootstrap", help="合并包首次运行：准备 Tailscale 并给出选择运行模式的指引")
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
        "mode-host": lambda: run_mode_host(args.config, args.install_tailscale),
        "ensure-alist": lambda: (ensure_alist(), 0)[1],
        "bootstrap": lambda: run_bootstrap(args.config),
        "configure-alist": lambda: (configure_alist(), 0)[1],
        "verify-sharing": lambda: (verify_sharing(), 0)[1],
        "apply-tailscale": lambda: run_apply_tailscale(
            args.config, args.install_if_missing),
        "doctor": lambda: run_doctor(args.host, args.media, args.config, args.role),
    }

    # --json 时把 fd 1 整体并到 stderr，这样连子进程的输出也不会污染
    # stdout；只有最后一行 JSON 走保留下来的原始 fd。
    saved_stdout_fd = None
    if args.json:
        _LOG_TO_STDERR = True
        try:
            sys.stdout.flush()
            saved_stdout_fd = os.dup(1)
            os.dup2(2, 1)
        except OSError:
            saved_stdout_fd = None

    def emit(payload):
        text = json.dumps(payload, ensure_ascii=True) + "\n"
        if saved_stdout_fd is not None:
            os.write(saved_stdout_fd, text.encode("ascii", "replace"))
        else:
            print(text, end="", flush=True)

    try:
        outcome = actions[args.command]()
        if isinstance(outcome, dict):
            payload = outcome
            code = 0 if payload.get("ok") else 1
        else:
            code = outcome or 0
            payload = {"ok": code == 0}
        if args.json:
            emit(payload)
        elif payload.get("error"):
            log("[WatchParty 设置] %s" % payload["error"])
        return code
    except SetupError as exc:
        if args.json:
            emit({"ok": False, "error": str(exc)})
        else:
            log("[WatchParty 设置] 出现问题：")
            log(str(exc))
        return 1
    except (OSError, ValueError) as exc:
        if args.json:
            emit({"ok": False, "error": str(exc)})
        else:
            log("[WatchParty 设置] 出现问题：%s" % exc)
        return 1
    except Exception as exc:  # noqa: BLE001 - 面板需要稳定的 JSON，而不是堆栈
        if args.json:
            emit({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
        else:
            log("[WatchParty 设置] 出现问题：%s: %s" % (type(exc).__name__, exc))
        return 1
    finally:
        if saved_stdout_fd is not None:
            try:
                os.close(saved_stdout_fd)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
