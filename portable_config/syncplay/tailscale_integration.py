#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tailscale setup helpers for the bundled AList watch-party workflow.

This module deliberately stays outside SyncClient and the Syncplay protocol.
It only reads the local Tailscale daemon and updates the mpv panel's local
options for direct Device Sharing access.  It never enables Serve or Funnel,
and never creates, reads, or stores a Tailscale auth key.
"""

import argparse
import datetime
import hashlib
import ipaddress
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit


SYNCPLAY_DIR = os.path.dirname(os.path.abspath(__file__))
PORTABLE_CONFIG_DIR = os.path.dirname(SYNCPLAY_DIR)
PROJECT_ROOT = os.path.dirname(PORTABLE_CONFIG_DIR)
DEFAULT_CONFIG = os.path.join(
    PORTABLE_CONFIG_DIR, "script-opts", "syncplay_ui.conf")
TAILSCALE_DIR = os.path.join(PROJECT_ROOT, "WatchParty", "Tailscale")
CONNECTION_FILE = os.path.join(TAILSCALE_DIR, "connection.json")
TAILSCALE_IPV4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_IPV6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
DEFAULT_ALIST_PORT = 5244
# 房主发布随包 media 目录时使用的固定映射。观看者的 configure-viewer 会清空
# 这两项，所以切回房主模式时必须重新写回来，否则房主的 AList 什么都不发布。
HOST_ALIST_ROOT = "~~/../WatchParty/media"
HOST_ALIST_VIRTUAL_ROOT = "/media"
# 每个平台/架构只携带并校验自己的官方安装包；哈希与包内文件一一对应。
# Windows 包内 Python 的位数与包架构一致，因此用 Python 自身位数选 MSI。
def _python_bitness():
    return "x86" if struct.calcsize("P") == 4 else "x64"


INSTALLERS = {
    ("nt", "x64"): (
        "tailscale-setup-1.102.3-amd64.msi",
        "03AC8183C6E3CE276E9B44281EBE7E4C02AEF28A971034CA170C4B665DF42DCE",
    ),
    ("nt", "x86"): (
        "tailscale-setup-1.102.3-x86.msi",
        "2A46E10F818991CA1476B2947BADB6EA5556541061B5B51C02A39682DE10DF53",
    ),
    ("darwin", None): (
        "Tailscale-1.102.4-macos.pkg",
        "B40B733AF76233FD1E4AF7ACAEB325268E55E6818C15C6E9AA9E78F427245C5B",
    ),
}
INSTALLER_NAME, INSTALLER_SHA256 = INSTALLERS[
    ("nt", _python_bitness()) if os.name == "nt" else ("darwin", None)
]
INSTALLER_PATH = os.path.join(TAILSCALE_DIR, INSTALLER_NAME)
# The current official Windows MSI installs under ``Tailscale IPN``.  Keep
# the older ``Tailscale`` directory as a compatibility fallback for existing
# installations and portable builds.
TAILSCALE_INSTALL_DIRS = ("Tailscale IPN", "Tailscale")
# macOS 官方 Standalone 安装包装到 /Applications/Tailscale.app。
TAILSCALE_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


class TailscaleIntegrationError(RuntimeError):
    pass


def _text(value):
    return str(value or "").strip()


def _is_tailscale_ipv4(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.version == 4 and address in TAILSCALE_IPV4


def _valid_tsnet_name(host):
    host = _text(host).rstrip(".").lower()
    if not host.endswith(".ts.net") or len(host) > 253:
        return False
    labels = host.split(".")
    label_re = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    return all(label_re.match(label) for label in labels)


def _format_host(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.lower()
    return "[%s]" % address.compressed if address.version == 6 else address.compressed


def normalize_host(value):
    """Return the direct AList origin for a shared Tailscale IPv4 address."""
    value = _text(value)
    if not value or any(character.isspace() for character in value):
        raise ValueError("请输入房主的 Tailscale IPv4 地址（100.x.x.x）")

    if "://" not in value:
        candidate = value.strip()
        if _is_tailscale_ipv4(candidate):
            return "http://%s:%d" % (_format_host(candidate), DEFAULT_ALIST_PORT)
        raise ValueError(
            "地址必须是房主的 Tailscale IPv4（100.64.0.0/10）")

    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError("无效的 Tailscale 地址") from exc
    scheme = parsed.scheme.lower()
    if scheme != "http" or not host:
        raise ValueError("Device Sharing 地址必须使用 HTTP")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Tailscale 地址不能包含账号或密码")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("请只填写房主地址，不要附加路径、查询参数或片段")
    host = host.rstrip(".").lower()
    if not _is_tailscale_ipv4(host):
        raise ValueError("只接受房主的 Tailscale IPv4（100.64.0.0/10）")
    if port not in (None, DEFAULT_ALIST_PORT):
        raise ValueError("AList Device Sharing 地址必须使用 5244 端口")
    netloc = "%s:%d" % (_format_host(host), DEFAULT_ALIST_PORT)
    return urlunsplit(("http", netloc, "", "", ""))


def locate_tailscale(explicit=None):
    candidates = []
    if explicit:
        candidates.append(os.path.abspath(os.path.expandvars(explicit)))
    if os.name == "nt":
        # The full host bundle keeps the matching CLI beside mpv.  Do not rely
        # on the caller's inherited PATH: a double-clicked .bat or an already
        # running mpv process can have an older environment snapshot.
        candidates.append(os.path.join(PROJECT_ROOT, "tailscale.exe"))
        for variable in (
                "ProgramFiles", "ProgramW6432", "ProgramFiles(x86)",
                "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                for directory_name in TAILSCALE_INSTALL_DIRS:
                    candidates.append(os.path.join(
                        base, directory_name, "tailscale.exe"))
        # PATH is a final fallback.  A long-running mpv process can inherit an
        # older PATH entry, so installed locations above take precedence.
        found = shutil.which("tailscale.exe") or shutil.which("tailscale")
        if found:
            candidates.append(found)
    else:
        # macOS：官方 Standalone 包装到 /Applications/Tailscale.app。
        candidates.append(TAILSCALE_APP_CLI)
        found = shutil.which("tailscale")
        if found:
            candidates.append(found)
    seen = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.normpath(candidate))
        if key not in seen and os.path.isfile(candidate):
            return os.path.abspath(candidate)
        seen.add(key)
    return None


def _run_cli(cli_path, arguments, timeout=15.0):
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
        raise TailscaleIntegrationError("无法运行 Tailscale：%s" % exc) from exc


def extract_status(data, cli_path=None):
    if not isinstance(data, dict):
        raise ValueError("Tailscale 状态必须是 JSON 对象")
    own = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    raw_ips = data.get("TailscaleIPs")
    if not isinstance(raw_ips, list):
        raw_ips = own.get("TailscaleIPs")
    if not isinstance(raw_ips, list):
        raw_ips = []
    ipv4 = None
    ipv6 = None
    for raw in raw_ips:
        try:
            address = ipaddress.ip_address(_text(raw))
        except ValueError:
            continue
        if address.version == 4 and address in TAILSCALE_IPV4 and ipv4 is None:
            ipv4 = address.compressed
        elif address.version == 6 and address in TAILSCALE_IPV6 and ipv6 is None:
            ipv6 = address.compressed
    dns_name = _text(own.get("DNSName")).rstrip(".").lower()
    if dns_name and not _valid_tsnet_name(dns_name):
        dns_name = ""
    backend_state = _text(data.get("BackendState")) or "Unknown"
    return {
        "installed": True,
        "cli_path": cli_path or "",
        "backend_state": backend_state,
        "online": backend_state.lower() == "running" and bool(ipv4 or ipv6),
        "ipv4": ipv4 or "",
        "ipv6": ipv6 or "",
        "dns_name": dns_name,
        "error": "",
    }


def query_status(cli_path=None):
    cli_path = locate_tailscale(cli_path)
    if cli_path is None:
        return {
            "installed": False,
            "cli_path": "",
            "backend_state": "NotInstalled",
            "online": False,
            "ipv4": "",
            "ipv6": "",
            "dns_name": "",
            "error": "未安装 Tailscale",
        }
    result = _run_cli(cli_path, ["status", "--json"])
    try:
        data = json.loads(result.stdout or "{}")
        status = extract_status(data, cli_path)
    except (TypeError, ValueError, json.JSONDecodeError):
        detail = _text(result.stderr or result.stdout) or "无法读取服务状态"
        return {
            "installed": True,
            "cli_path": cli_path,
            "backend_state": "Unknown",
            "online": False,
            "ipv4": "",
            "ipv6": "",
            "dns_name": "",
            "error": detail[:500],
        }
    if result.returncode != 0:
        status["error"] = _text(result.stderr)[:500]
    return status


def build_host_configuration(status):
    if not isinstance(status, dict) or not status.get("installed"):
        raise ValueError("尚未安装 Tailscale")
    if _text(status.get("backend_state")).lower() != "running":
        raise ValueError("Tailscale 尚未登录或未连接")
    ipv4 = _text(status.get("ipv4"))
    if not _is_tailscale_ipv4(ipv4):
        raise ValueError("Tailscale 尚未提供可用的 100.x IPv4 地址")
    return {
        "alist_enabled": "yes",
        "alist_server": "http://%s:%d" % (ipv4, DEFAULT_ALIST_PORT),
        # 从观看者模式切回房主时必须恢复发布目录：观看者的
        # configure-viewer 会把 alist_root / alist_map 清空。
        "alist_root": HOST_ALIST_ROOT,
        "alist_virtual_root": HOST_ALIST_VIRTUAL_ROOT,
        "tailscale_mode": "host",
        "tailscale_host": ipv4,
    }


def build_viewer_configuration(host):
    origin = normalize_host(host)
    parsed = urlsplit(origin)
    return {
        "alist_enabled": "yes",
        "alist_server": origin,
        # A viewer consumes the host's URL and must never publish files from
        # mappings left behind by a copied host configuration.
        "alist_root": "",
        "alist_map": "",
        "tailscale_mode": "viewer",
        "tailscale_host": parsed.hostname or "",
    }


def build_local_configuration():
    """关闭共享：保留已保存的地址，只停止对外发布/取流。

    合并安装包首次运行时 ``tailscale_mode=off``，用户可能只想本机观看；
    面板的「改回仅本机观看」用它把角色退回未选择状态。
    """
    return {
        "alist_enabled": "no",
        "tailscale_mode": "off",
    }


def read_syncplay_config(path=DEFAULT_CONFIG):
    values = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as stream:
            lines = stream.readlines()
    except FileNotFoundError:
        return values
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def update_syncplay_config(path, updates):
    if not isinstance(updates, dict) or not updates:
        return
    clean = {}
    for key, value in updates.items():
        key = _text(key)
        value = str(value)
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", key):
            raise ValueError("无效的配置键：%s" % key)
        if any(character in value for character in ("\0", "\r", "\n")):
            raise ValueError("配置值不能包含换行或空字符")
        clean[key] = value

    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as stream:
            original = stream.read()
    except FileNotFoundError:
        original = ""
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines(keepends=True)
    remaining = dict(clean)
    output = []
    for line in lines:
        ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
        body = line[:-len(ending)] if ending else line
        match = re.match(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=", body)
        key = match.group(1) if match else None
        if key in remaining:
            output.append("%s=%s%s" % (key, remaining.pop(key), ending or newline))
        else:
            output.append(line)
    if output and not output[-1].endswith(("\n", "\r")):
        output[-1] += newline
    for key, value in remaining.items():
        output.append("%s=%s%s" % (key, value, newline))

    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(prefix=".syncplay-ui-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write("".join(output))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def _write_connection_file(configuration, path=CONNECTION_FILE):
    payload = {
        "schema_version": 1,
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "alist_server": configuration["alist_server"],
        "tailscale_host": configuration["tailscale_host"],
        "note": "This file contains no Tailscale login token or AList credential.",
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    descriptor, temp_path = tempfile.mkstemp(prefix=".connection-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def configure_host(config_path=DEFAULT_CONFIG, cli_path=None):
    status = query_status(cli_path)
    configuration = build_host_configuration(status)
    update_syncplay_config(config_path, configuration)
    _write_connection_file(configuration)
    status.update(configuration)
    status["device_share"] = "manual"
    return status


def configure_viewer(host, config_path=DEFAULT_CONFIG, cli_path=None):
    status = query_status(cli_path)
    if not status.get("installed"):
        raise TailscaleIntegrationError("请先安装 Tailscale")
    if _text(status.get("backend_state")).lower() != "running":
        raise TailscaleIntegrationError("请先登录并连接 Tailscale")
    configuration = build_viewer_configuration(host)
    update_syncplay_config(config_path, configuration)
    status.update(configuration)
    return status


def configure_local(config_path=DEFAULT_CONFIG):
    """退回"仅本机观看"：关闭 AList 共享，不影响 Tailscale 本身。"""
    configuration = build_local_configuration()
    update_syncplay_config(config_path, configuration)
    return configuration


def open_tailscale(cli_path=None):
    cli_path = locate_tailscale(cli_path)
    if cli_path is None:
        raise TailscaleIntegrationError("尚未安装 Tailscale")
    if os.name == "nt":
        ui_path = os.path.join(os.path.dirname(cli_path), "tailscale-ipn.exe")
        if not os.path.isfile(ui_path):
            raise TailscaleIntegrationError("找不到 Tailscale 登录界面")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(
            [ui_path],
            close_fds=True,
            creationflags=flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"opened": True, "ui_path": ui_path}
    # macOS：打开 Tailscale.app 的主界面（未登录时即登录窗口）。
    subprocess.run(["open", "-a", "Tailscale"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"opened": True, "ui_path": "/Applications/Tailscale.app"}


def verify_installer(path=INSTALLER_PATH):
    try:
        with open(path, "rb") as stream:
            digest = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise TailscaleIntegrationError("找不到 Tailscale 安装包：%s" % path) from exc
    actual = digest.hexdigest().upper()
    if actual != INSTALLER_SHA256:
        raise TailscaleIntegrationError("Tailscale 安装包校验失败，已拒绝运行")
    return {"verified": True, "installer_path": path, "sha256": actual}


def _human_status(result):
    lines = ["[Tailscale WatchParty]"]
    lines.append("安装：%s" % ("是" if result.get("installed") else "否"))
    lines.append("状态：%s" % (result.get("backend_state") or "Unknown"))
    if result.get("ipv4"):
        lines.append("本机地址：%s" % result["ipv4"])
    if result.get("dns_name"):
        lines.append("完整名称：%s" % result["dns_name"])
    if result.get("alist_server"):
        lines.append("媒体地址：%s" % result["alist_server"])
    if result.get("error"):
        lines.append("错误：%s" % result["error"])
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Tailscale Device Sharing + AList watch-party setup")
    parser.add_argument("--json", action="store_true", help="输出供 mpv 面板读取的 JSON")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="syncplay_ui.conf 路径")
    parser.add_argument("--cli", default=None, help="tailscale 命令行程序路径")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="检查 Tailscale 状态")
    subparsers.add_parser("configure-host", help="配置房主的 Tailscale IPv4 直连地址")
    viewer = subparsers.add_parser("configure-viewer", help="配置观看者使用房主地址")
    viewer.add_argument("host", nargs="?", help="房主 Tailscale IPv4（100.x.x.x）")
    subparsers.add_parser("configure-local", help="关闭共享，退回仅本机观看")
    subparsers.add_parser("open", help="打开 Tailscale 登录界面")
    subparsers.add_parser("verify-installer", help="校验项目内的官方安装包")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        if args.command == "status":
            result = query_status(args.cli)
            result.update({
                key: value for key, value in read_syncplay_config(args.config).items()
                if key in ("alist_enabled", "alist_server", "alist_root",
                           "alist_virtual_root", "tailscale_mode", "tailscale_host")
            })
        elif args.command == "configure-host":
            result = configure_host(args.config, args.cli)
        elif args.command == "configure-local":
            result = configure_local(args.config)
        elif args.command == "configure-viewer":
            host = args.host
            if not host:
                if not sys.stdin.isatty():
                    raise TailscaleIntegrationError("缺少房主 Tailscale 地址")
                host = input("房主的 Tailscale IPv4 地址（100.x.x.x）：").strip()
            result = configure_viewer(host, args.config, args.cli)
        elif args.command == "open":
            result = open_tailscale(args.cli)
        elif args.command == "verify-installer":
            result = verify_installer()
        else:
            raise TailscaleIntegrationError("未知命令")
        if args.json:
            # mpv captures raw subprocess bytes.  ASCII-only JSON remains
            # parseable even when the embedded Python console uses GBK.
            print(json.dumps({"ok": True, **result}, ensure_ascii=True))
        else:
            print(_human_status(result))
        return 0
    except (OSError, ValueError, TailscaleIntegrationError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=True))
        else:
            print("[Tailscale WatchParty]\n错误：%s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
