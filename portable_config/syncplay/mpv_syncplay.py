#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mpv_syncplay.py —— 把 Syncplay 的同步观看功能集成进 mpv（精简版，无聊天）

原理：以 mpv 自带的嵌入式 Python 运行（纯标准库，零第三方依赖），
      通过 mpv 的 JSON IPC 管道控制播放，并实现 Syncplay 客户端协议
      （与官方 syncplay.pl 公共服务器及官方客户端互通）。

用法：
  python mpv_syncplay.py --room 房间名 --name 昵称
  python mpv_syncplay.py --server syncplay.pl:8996 --room 房间名
  python mpv_syncplay.py --host 8995                  # 本机开精简中继服务器
  python mpv_syncplay.py --virtual                    # 虚拟播放器（测试/调试）

同步行为与官方一致：
  - 任何人暂停，全员暂停；任何人恢复，全员恢复
  - 任何人手动跳转，全员跟随跳转
  - 超前房间 4 秒以上自动回退对齐；超前 1.5 秒以上自动减速（0.95x）等待
  - 房间进度跟随最慢的播放者
"""

import argparse
import hashlib
import json
import math
import os
import queue
import random
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.parse

try:
    from .alist_diagnostics import (MEDIA_ACCESS_ERROR, print_report,
                                    probe_media_url)
    from .media_provider import MediaProvider
except ImportError:
    # 便携 Python 的 ._pth 隔离模式不会自动加入脚本目录。
    module_dir = os.path.dirname(os.path.abspath(__file__))
    if module_dir not in sys.path:
        sys.path.insert(0, module_dir)
    from alist_diagnostics import (MEDIA_ACCESS_ERROR, print_report,
                                   probe_media_url)
    from media_provider import MediaProvider

# ---------- 与官方 constants.py 对齐的同步参数 ----------
PROTOCOL_VERSION = "1.7.7"
REWIND_THRESHOLD = 4.0          # 超前房间超过该秒数 → 回退对齐
SEEK_DETECT_THRESHOLD = 1.2     # 本地位置偏离预测超过该秒数 → 视为用户手动跳转
SLOWDOWN_KICKIN = 1.5           # 超前超过该秒数 → 减速
SLOWDOWN_RATE = 0.95
SLOWDOWN_RESET = 0.1            # 差距缩小到该秒数内 → 恢复原速
PROTOCOL_TIMEOUT = 12.5         # 服务器无消息超时（秒）
PING_WEIGHT = 0.85
STATE_TICK = 0.5                # --host 模式服务器广播周期
POLL_INTERVAL = 0.3             # mpv 状态轮询周期
MAX_PROTOCOL_MESSAGE = 4 * 1024 * 1024
MAX_FILENAME_LENGTH = 250
MAX_IDENTITY_LENGTH = 128

# 控制/状态桥的默认位置。使用脚本目录而不是当前工作目录，避免从
# 不同快捷方式启动时找不到文件；路径也可以通过命令行参数覆盖。
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_COMMAND_FILE = os.path.join(SCRIPT_DIR, "syncplay_command.json")
DEFAULT_STATUS_FILE = os.path.join(SCRIPT_DIR, "syncplay_status.json")

def log(msg):
    text = time.strftime("[%H:%M:%S] ") + str(msg)
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        # Windows 控制台常用 GBK，无法直接输出 OSD 中的箭头/表情。
        # 日志降级为可打印字符，不能因为显示日志而断开同步连接。
        stream = getattr(sys.stdout, "buffer", None)
        if stream is not None:
            stream.write((text + "\n").encode(sys.stdout.encoding or "utf-8", "replace"))
            stream.flush()

def fmt_time(sec):
    sec = int(sec or 0)
    return "%d:%02d:%02d" % (sec // 3600, (sec % 3600) // 60, sec % 60)

def finite_number(value):
    """Return whether a protocol value is a real, finite JSON number."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False

def validated_media_url(value):
    """返回可公开播放的 HTTP(S) URL；非法或含凭据的地址返回 None。"""
    if (not isinstance(value, str) or not value or len(value) > 16384 or
            "\0" in value or
            any(character.isspace() for character in value)):
        return None
    try:
        value.encode("utf-8")
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        # 访问一次 port，让非法端口在这里被拒绝。
        parsed.port
    except (TypeError, ValueError, UnicodeEncodeError):
        return None
    return value


def normalized_remote_file(value):
    """Return a bounded, JSON-safe peer file object, or ``None`` if invalid."""
    if not isinstance(value, dict):
        return None
    if not value:
        return {}
    name = value.get("name")
    if not isinstance(name, str) or not name or len(name) > MAX_FILENAME_LENGTH:
        return None
    if any(ord(character) < 32 for character in name):
        return None
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return None

    duration = value.get("duration", 0.0)
    duration = max(0.0, float(duration)) if finite_number(duration) else 0.0
    raw_size = value.get("size", 0)
    if isinstance(raw_size, str):
        try:
            size_valid = (bool(raw_size) and len(raw_size) <= MAX_IDENTITY_LENGTH and
                          not any(ord(character) < 32 for character in raw_size))
            raw_size.encode("utf-8")
        except UnicodeEncodeError:
            size_valid = False
        size = raw_size if size_valid else 0
    elif finite_number(raw_size) and float(raw_size) >= 0:
        size = int(raw_size) if float(raw_size).is_integer() else float(raw_size)
    else:
        size = 0

    result = {"name": name, "duration": duration, "size": size}
    media_url = validated_media_url(value.get("media_url"))
    if value.get("source_type") == "alist" and media_url is not None:
        result["media_url"] = media_url
        result["source_type"] = "alist"
        owner = value.get("media_owner")
        if isinstance(owner, str):
            owner = owner.strip()
            try:
                owner_valid = (bool(owner) and len(owner) <= MAX_IDENTITY_LENGTH and
                               not any(ord(character) < 32 for character in owner))
                owner.encode("utf-8")
            except UnicodeEncodeError:
                owner_valid = False
            if owner_valid:
                result["media_owner"] = owner
    return result

def media_url_key(value):
    """生成仅供比较的 URL 标识；保留查询串以兼容未来的签名 URL。"""
    value = validated_media_url(value)
    if value is None:
        return None
    parsed = urllib.parse.urlsplit(value)
    port = parsed.port
    if port == (443 if parsed.scheme.lower() == "https" else 80):
        port = None
    unreserved = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    def normalize_escape(match):
        character = chr(int(match.group(1), 16))
        return character if character in unreserved else "%" + match.group(1).upper()
    normalized_path = re.sub(r"%([0-9A-Fa-f]{2})", normalize_escape, parsed.path)
    return (parsed.scheme.lower(), parsed.hostname.lower(), port,
            normalized_path, parsed.query)

def media_url_matches_server(value, server):
    """限制远程媒体为用户显式配置的 AList origin 及其 /d/ 路径。"""
    value = validated_media_url(value)
    server = validated_media_url(server)
    if value is None or server is None:
        return False
    target = urllib.parse.urlsplit(value)
    base = urllib.parse.urlsplit(server)
    target_origin = media_url_key(urllib.parse.urlunsplit(
        (target.scheme, target.netloc, "/", "", "")))[:3]
    base_origin = media_url_key(urllib.parse.urlunsplit(
        (base.scheme, base.netloc, "/", "", "")))[:3]
    if target_origin != base_origin:
        return False
    def fully_decode_path(path):
        for _round in range(8):
            decoded = urllib.parse.unquote(path)
            if ("\\" in decoded or "\0" in decoded or
                    any(part in (".", "..") for part in decoded.split("/"))):
                return None
            if decoded == path:
                return decoded
            path = decoded
        return None

    base_path = fully_decode_path(base.path)
    target_path = fully_decode_path(target.path)
    if base_path is None or target_path is None:
        return False
    base_path = base_path.rstrip("/")
    return target_path.startswith(base_path + "/d/")

class PingService:
    """与官方一致的往返延迟滑动平均，用于计算消息年龄补偿"""
    def __init__(self):
        self._rtt = 0.0
        self._forward = 0.0

    def new_timestamp(self):
        return time.time()

    def receive_message(self, timestamp, sender_rtt):
        if not finite_number(timestamp):
            return
        if not finite_number(sender_rtt):
            sender_rtt = 0.0
        rtt = max(0.0, time.time() - timestamp)
        fwd = max(0.0, rtt - (sender_rtt or 0.0))
        if self._rtt == 0.0:
            self._rtt, self._forward = rtt, fwd
        else:
            self._rtt = PING_WEIGHT * self._rtt + (1 - PING_WEIGHT) * rtt
            self._forward = PING_WEIGHT * self._forward + (1 - PING_WEIGHT) * fwd

    def get_rtt(self):
        return self._rtt

    def get_forward_delay(self):
        return self._forward


# ======================================================================
# 播放器后端：真实 mpv（IPC 管道）与虚拟播放器（测试用）共用一套接口
# ======================================================================
class MpvPlayer:
    def __init__(self, pipe_path, wait_seconds=60):
        self.pipe_path = pipe_path
        self._q = queue.Queue()
        self._dead = threading.Event()
        self.base_speed = 1.0
        self._f = None
        self._req_id = 0
        self._pending = {}
        self._plock = threading.Lock()
        self.last_media_test = None
        self._connect(wait_seconds)
        self._worker = threading.Thread(target=self._work_loop, daemon=True)
        self._worker.start()
        info = self.file_info()
        self.base_speed = self._simple_get("speed") or 1.0
        log("已连接 mpv（IPC 管道 %s），基础播放速度 %sx" % (pipe_path, self.base_speed))
        if info:
            log("当前文件：%s" % info.get("name"))

    # ---- 管道连接 ----
    def _connect(self, wait_seconds):
        deadline = time.time() + wait_seconds
        while True:
            try:
                self._f = open(self.pipe_path, "r+b", buffering=0)
                return
            except OSError:
                if time.time() > deadline:
                    raise SystemExit("连接 mpv IPC 管道失败：%s（请确认 mpv 已启动且配置了 input-ipc-server）" % self.pipe_path)
                time.sleep(0.5)

    def _send(self, obj):
        self._f.write((json.dumps(obj) + "\n").encode("utf-8"))

    def _read_line(self):
        while True:
            line = self._f.readline()
            if not line:
                raise ConnectionError("mpv IPC 管道已断开（mpv 可能已退出）")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if msg.get("event"):          # 事件消息：忽略（我们用轮询）
                continue
            rid = msg.get("request_id")
            if rid is not None:
                return msg
            # 没有 request_id 的应答不正常，跳过

    def _request(self, cmd, timeout=5.0):
        with self._plock:
            self._req_id += 1
            rid = self._req_id
        self._send({"command": cmd, "request_id": rid})
        deadline = time.time() + timeout
        while True:
            if self._dead.is_set():
                return {"error": "dead"}
            remaining = deadline - time.time()
            if remaining <= 0:
                return {"error": "timeout"}
            # readline 会阻塞，先在锁内读（所有管道 I/O 都在工作线程）
            msg = self._read_line()
            if msg.get("request_id") == rid:
                return msg

    # ---- 工作线程：串行处理操作队列 ----
    def _work_loop(self):
        while not self._dead.is_set():
            try:
                op, args = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if op == "req":
                    cmd, cb = args
                    resp = self._request(cmd)
                    cb(resp)
                elif op == "die":
                    return
            except (ConnectionError, OSError):
                self._dead.set()

    def _submit(self, cmd, timeout=5.0):
        if self._dead.is_set():
            return {"error": "dead"}
        ev = threading.Event()
        box = {}
        self._q.put(("req", (cmd, lambda r: (box.setdefault("r", r), ev.set()))))
        ev.wait(timeout + 1.0)
        return box.get("r", {"error": "timeout"})

    def _simple_get(self, prop):
        resp = self._submit(["get_property", prop])
        if resp.get("error") == "success":
            return resp.get("data")
        return None

    def _checked_submit(self, command, description, timeout=5.0):
        """执行必须成功的 IPC 命令，避免同步控制失败后静默继续。"""
        response = self._submit(command, timeout=timeout)
        error = (response.get("error", "unknown")
                 if isinstance(response, dict) else "invalid response")
        if error != "success":
            raise OSError("mpv %s失败：%s" % (description, error))
        return response

    def _set_property(self, name, value, description=None):
        # set_property 保留 JSON 值的类型；set 只适合字符串式命令参数。
        return self._checked_submit(
            ["set_property", name, value], description or ("设置 %s" % name)
        )

    # ---- 对外接口 ----
    def get_state(self):
        """返回 (position|None, paused|None)"""
        if self._dead.is_set():
            return None, None
        pos = self._simple_get("time-pos")
        paused = self._simple_get("pause")
        return pos, paused

    def seek(self, t):
        self._submit(["seek", float(t), "absolute"])

    def set_pause(self, paused):
        self._set_property("pause", bool(paused), "设置暂停状态")

    def set_speed(self, speed):
        self._set_property("speed", float(speed), "设置播放速度")

    def osd(self, text):
        try:
            self._submit(["show-text", text, 2500], timeout=2.0)
        except Exception:
            pass

    def file_info(self):
        name = self._simple_get("filename")
        if not name:
            return None
        info = {"name": os.path.basename(str(name))}
        dur = self._simple_get("duration")
        if dur:
            info["duration"] = float(dur)
        size = self._simple_get("file-size")
        if size:
            info["size"] = int(size)
        return info

    def current_path(self):
        """返回 mpv 当前媒体的 URL 或规范化本地绝对路径。"""
        path = self._simple_get("path")
        if not path:
            return None
        path = str(path)
        if validated_media_url(path):
            return path
        if not os.path.isabs(path):
            working_directory = self._simple_get("working-directory")
            if working_directory:
                path = os.path.join(str(working_directory), path)
        return os.path.realpath(path)

    def load_remote(self, url, readahead=20.0):
        """暂停旧媒体、启用 HTTP 缓存并以远程地址替换当前文件。"""
        url = validated_media_url(url)
        if url is None:
            raise ValueError("远程媒体 URL 必须是无凭据的 HTTP(S) 地址")
        media_test = probe_media_url(url)
        self.last_media_test = media_test
        print_report(media_test)
        if not media_test.ok:
            raise OSError(media_test.result)
        self._set_property("pause", True, "暂停旧媒体")
        self._set_property("cache", "yes", "启用 HTTP 缓存")
        self._set_property(
            "demuxer-readahead-secs", float(readahead), "设置 HTTP 预读"
        )
        self._checked_submit(["loadfile", url, "replace"], "loadfile ", timeout=10.0)

    def is_media_ready(self, url):
        """确认 mpv 已建立目标媒体，而不是只看 ``time-pos``。

        部分容器在暂停状态下会先完成 demux/轨道初始化，但暂时不提供
        ``time-pos``。旧逻辑会把这种合法状态误判为未加载，观看者随后
        一直显示黑屏直到 ready 超时。
        """
        if media_url_key(self.current_path()) != media_url_key(url):
            return False
        if self._simple_get("idle-active") is True:
            return False
        if self._simple_get("time-pos") is not None:
            return True
        duration = self._simple_get("duration")
        if finite_number(duration) and float(duration) > 0:
            return True
        tracks = self._simple_get("track-list")
        return isinstance(tracks, list) and bool(tracks)

    def is_alive(self):
        return not self._dead.is_set()

    def close(self):
        self._dead.set()
        try:
            self._q.put_nowait(("die", None))
        except Exception:
            pass
        try:
            if self._f is not None:
                self._f.close()
        except OSError:
            pass


class VirtualPlayer:
    """虚拟播放器：不依赖 mpv，支持 stdin 命令（seek/play/pause/pos/quit），用于测试"""
    def __init__(self, pos=0.0, paused=False, name="virtual.mkv", duration=7200.0):
        self.pos = pos
        self.paused = paused
        self.speed = 1.0
        self.base_speed = 1.0
        self._ts = time.time()
        self._lock = threading.Lock()
        self.file = {"name": name, "duration": duration, "size": 742_000_000}
        self.path = name
        self.cache = "no"
        self.readahead = 0.0
        self.command_log = []
        self._media_ready = True
        threading.Thread(target=self._stdin_loop, daemon=True).start()

    def _advance(self):
        with self._lock:
            if not self.paused:
                self.pos += (time.time() - self._ts) * self.speed
            self._ts = time.time()
            return self.pos

    def get_state(self):
        return self._advance(), self.paused

    def seek(self, t):
        self._advance()
        with self._lock:
            self.pos = float(t)
            self.command_log.append(["seek", float(t), "absolute"])
        log("【虚拟播放器】跳转到 %s" % fmt_time(t))

    def set_pause(self, paused):
        self._advance()
        with self._lock:
            self.paused = bool(paused)
            self.command_log.append(["set_property", "pause", bool(paused)])

    def set_speed(self, s):
        self._advance()
        with self._lock:
            self.speed = float(s)
            self.command_log.append(["set_property", "speed", float(s)])

    def osd(self, text):
        log("【OSD】%s" % text)

    def file_info(self):
        return dict(self.file)

    def current_path(self):
        return self.path

    def load_remote(self, url, readahead=20.0):
        url = validated_media_url(url)
        if url is None:
            raise ValueError("远程媒体 URL 必须是无凭据的 HTTP(S) 地址")
        self.set_pause(True)
        with self._lock:
            self.cache = "yes"
            self.command_log.append(["set_property", "cache", "yes"])
            self.readahead = float(readahead)
            self.command_log.append(
                ["set_property", "demuxer-readahead-secs", self.readahead]
            )
            self.command_log.append(["loadfile", url, "replace"])
            self.path = url
            name = os.path.basename(urllib.parse.unquote(urllib.parse.urlsplit(url).path))
            self.file = {"name": name or "remote", "duration": self.file.get("duration", 0.0)}
            self.pos = 0.0
            self._ts = time.time()
            self._media_ready = True

    def is_media_ready(self, url):
        with self._lock:
            return (self._media_ready and self.pos is not None and
                    media_url_key(self.path) == media_url_key(url))

    def is_alive(self):
        return True

    def close(self):
        pass

    def _stdin_loop(self):
        for line in sys.stdin:
            parts = line.strip().split()
            if not parts:
                continue
            cmd = parts[0].lower()
            try:
                if cmd == "seek":
                    self.seek(float(parts[1]))
                elif cmd == "play":
                    self.set_pause(False)
                elif cmd == "pause":
                    self.set_pause(True)
                elif cmd == "pos":
                    log("【虚拟播放器】当前位置 %s" % fmt_time(self._advance()))
                elif cmd == "quit":
                    os._exit(0)
            except (IndexError, ValueError):
                log("虚拟播放器命令格式：seek 秒数 | play | pause | pos | quit")


# ======================================================================
# Syncplay 客户端
# ======================================================================
class SyncClient:
    def __init__(self, args, player, media_provider=None):
        self.args = args
        self.player = player
        self.media_provider = media_provider
        enabled_option = getattr(args, "alist_enabled", None)
        self.media_sharing_enabled = (bool(media_provider) if enabled_option is None
                                      else bool(enabled_option))
        self.alist_server = validated_media_url(getattr(args, "alist_server", None))
        self.http_readahead = max(0.0, float(getattr(args, "http_readahead", 20.0)))
        self.media_ready_timeout = max(0.0, float(getattr(args, "media_ready_timeout", 30.0)))
        self.media_load_timeout = self.media_ready_timeout or 30.0
        self.ping = PingService()
        self.name = args.name
        self.room = args.room
        self.rewind_threshold = args.rewind
        self.sock = None
        self._sendlock = threading.Lock()
        # 监视线程在检测到本地暂停/跳转时会在状态锁内发送状态，而
        # _send_state() 也需要读取同一份状态。使用可重入锁避免首个
        # 状态变化把监视线程永久卡死。
        self._lock = threading.RLock()         # 状态锁
        self._stop = threading.Event()
        # 外部控制桥请求立即结束当前连接并重新握手。单独使用事件，
        # 不在状态锁内等待网络线程，避免 Windows 下 socket 关闭竞态。
        self._reconnect_requested = threading.Event()
        # 本地播放状态缓存
        self.last_pos = None
        self.last_ts = time.time()
        self.last_paused = None
        self.filename = None
        self._observed_path = None
        self._last_file_identity = None
        self._last_file_payload = None
        # 全局（房间）状态
        self.global_pos = None
        self.global_paused = None
        # AList 媒体只在 watcher 中执行 IPC；网络线程只更新以下队列。
        self._pending_media = None
        self._loading_media = None
        self._cancelled_media_restore = None
        self._current_media_source = None
        self._current_media_scope = None
        self._current_media_confirmed = False
        self._hosted_media_url = None
        self._media_owner = None
        self._media_gate = None
        self._failed_media = None
        # 同步过程中的保护状态
        self.client_ignore = 0
        self.server_ignore = 0
        self.ready = True
        self.speed_changed = False
        self.last_correction = 0.0
        # 服务器侧
        self.latency_calc = None
        self.users = {}
        self.logged = False
        self._received_user_list = False
        self.server_isolate_rooms = None
        # 最近一次可供控制面板显示的错误。连接成功后清除；断线时保留
        # 原因，便于 UI 在重连等待期间给出明确提示。
        self.last_error = None

    # ---------- 连接管理 ----------
    def run(self):
        while not self._stop.is_set():
            with self._lock:
                server = self.args.server
            host, _, port = server.partition(":")
            try:
                log("正在连接服务器 %s ..." % server)
                self.sock = socket.create_connection((host, int(port or 8995)), timeout=10)
                self.sock.settimeout(PROTOCOL_TIMEOUT)
                if self._reconnect_requested.is_set():
                    raise ConnectionError("收到重新连接请求")
                self._reader_loop()
            except SystemExit:
                raise
            except Exception as e:
                if self._stop.is_set():
                    break
                immediate = self._reconnect_requested.is_set()
                if not immediate:
                    with self._lock:
                        self.last_error = str(e)
                log("连接中断：%s%s" % (e, "，立即重连" if immediate else "，5 秒后重连"))
                self._on_disconnect()
                # Event.wait 可被 stop/reconnect 唤醒，不让退出卡住 5 秒。
                if immediate:
                    self._reconnect_requested.clear()
                else:
                    deadline = time.time() + 5.0
                    while not self._stop.is_set():
                        remaining = deadline - time.time()
                        if remaining <= 0:
                            break
                        # reconnect 命令也要能唤醒离线重试等待，而不必
                        # 等完整 5 秒；Windows Event.wait 可可靠跨线程。
                        if self._reconnect_requested.wait(min(remaining, 0.25)):
                            self._reconnect_requested.clear()
                            break
            finally:
                try:
                    if self.sock is not None:
                        self.sock.close()
                except Exception:
                    pass
                self.sock = None

    def request_reconnect(self):
        """中断当前连接，让 run() 立即按最新设置重新握手。"""
        self._reconnect_requested.set()
        sock = self.sock
        if sock is not None:
            # shutdown 会唤醒阻塞 recv；close 由 run() 的 finally 统一完成。
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def update_settings(self, server=None, room=None, name=None):
        """线程安全地更新连接参数，并返回实际变更的字段名集合。"""
        changed = set()
        restore_playback = False
        with self._lock:
            if server is not None:
                value = str(server).strip()
                if value and value != self.args.server:
                    self.args.server = value
                    changed.add("server")
            if room is not None:
                value = str(room).strip()
                if value and value != self.room:
                    self.room = value
                    self.args.room = value
                    changed.add("room")
            if name is not None:
                value = str(name).strip()
                if value and value != self.name:
                    self.name = value
                    self.args.name = value
                    changed.add("name")
            if changed:
                # Stop the watcher from publishing into a half-switched
                # connection.  A remotely loaded URL may only be re-announced
                # when reconnecting to the same server and room.
                self.logged = False
            if changed.intersection(("server", "room")):
                gate = self._media_gate or {}
                loading = self._loading_media or {}
                restore_playback = (bool(gate.get("resume")) or
                                    loading.get("was_paused") is False)
                for item in (self._pending_media, self._loading_media):
                    if isinstance(item, dict):
                        item["cancelled"] = True
                self._pending_media = None
                self._loading_media = None
                self._cancelled_media_restore = None
                self._current_media_source = None
                self._current_media_scope = None
                self._current_media_confirmed = False
                self._hosted_media_url = None
                self._media_owner = None
                self._media_gate = None
                self._failed_media = None
                self._last_file_identity = None
                self._last_file_payload = None
                self.ready = True
                self._received_user_list = False
                self.server_isolate_rooms = None
        if restore_playback:
            try:
                self.player.set_pause(False)
            except Exception:
                pass
        if changed:
            self.request_reconnect()
        return changed

    def _set_ready(self, value, manually=False, force=False):
        """更新并上报 readiness；媒体加载可强制发送同值的代次信号。"""
        value = bool(value)
        with self._lock:
            changed = self.ready != value
            self.ready = value
        if changed or force:
            self._send_set({"ready": {"isReady": value,
                                      "manuallyInitiated": bool(manually)}})

    def _build_file_payload(self, info, path):
        """构造兼容官方协议的 file 对象，不把本地绝对路径放上网络。"""
        if not isinstance(info, dict):
            return None
        payload = dict(info)
        if not payload.get("name"):
            if validated_media_url(path):
                name = os.path.basename(urllib.parse.unquote(
                    urllib.parse.urlsplit(path).path))
            else:
                name = os.path.basename(str(path or ""))
            payload["name"] = name or "unknown"
        try:
            duration = float(payload.get("duration", 0) or 0)
            if not math.isfinite(duration):
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            duration = 0.0
        try:
            size_value = payload.get("size", 0) or 0
            if isinstance(size_value, bool):
                raise ValueError
            size = int(size_value)
        except (TypeError, ValueError, OverflowError):
            size = 0
        payload["duration"] = max(0.0, duration)
        payload["size"] = max(0, size)
        provider_result = None
        if self.media_sharing_enabled and self.media_provider is not None and path:
            try:
                provider_result = self.media_provider.local_to_url(path)
            except (OSError, TypeError, ValueError):
                provider_result = None
        if self.server_isolate_rooms is True and isinstance(provider_result, dict):
            url = validated_media_url(provider_result.get("url"))
            source_type = provider_result.get("type")
            if url and source_type == "alist":
                payload["media_url"] = url
                payload["source_type"] = "alist"
                # 保留最初发布者，避免 List 中观看者的 URL 回显被误认成
                # 新房主。旧客户端会像忽略其他扩展字段一样忽略此字段。
                payload["media_owner"] = self.name
                return payload

        with self._lock:
            source = dict(self._current_media_source or {})
            source_scope = self._current_media_scope
            source_confirmed = self._current_media_confirmed
            current_scope = (self.args.server, self.room)
        source_url = validated_media_url(source.get("media_url"))
        if (self.media_sharing_enabled and self.server_isolate_rooms is True and
                source.get("source_type") == "alist" and
                source_confirmed and
                (source_scope is None or source_scope == current_scope) and
                media_url_key(path) == media_url_key(source_url)):
            payload["media_url"] = source_url
            payload["source_type"] = "alist"
            media_owner = source.get("media_owner")
            if isinstance(media_owner, str) and media_owner.strip():
                payload["media_owner"] = media_owner.strip()
        return payload

    @staticmethod
    def _file_identity(path, payload):
        media_url = payload.get("media_url") if isinstance(payload, dict) else None
        url_key = media_url_key(media_url)
        if validated_media_url(path):
            return ("remote", media_url_key(path), url_key)
        if path:
            try:
                return ("local", os.path.normcase(os.path.realpath(path)), url_key)
            except (OSError, TypeError, ValueError):
                pass
        if isinstance(payload, dict):
            return ("file", payload.get("name"), payload.get("duration"),
                    payload.get("size"), url_key)
        return None

    def _consider_remote_media(self, username, room, file_info):
        """验证同房间 AList 元数据并排队；绝不在网络读取线程中调用 mpv。"""
        if (not self.media_sharing_enabled or self.server_isolate_rooms is not True or
                username == self.name or room != self.room):
            return False
        with self._lock:
            if not self._received_user_list:
                return False
        if not isinstance(file_info, dict) or file_info.get("source_type") != "alist":
            return False
        url = validated_media_url(file_info.get("media_url"))
        if url is None or not media_url_matches_server(url, self.alist_server):
            return False
        target_key = media_url_key(url)
        declared_owner = file_info.get("media_owner")
        if declared_owner is not None:
            if not isinstance(declared_owner, str):
                return False
            declared_owner = declared_owner.strip()
            if (not declared_owner or len(declared_owner) > 128 or
                    any(ord(character) < 32 for character in declared_owner)):
                return False

        # 房主重连时，List 可能先带回观看者对本机 URL 的回显。只用
        # watcher 已观察到的路径做纯映射验证，不在网络线程调用 mpv IPC。
        local_anchor = False
        if declared_owner == self.name:
            with self._lock:
                hosted_url = self._hosted_media_url
                observed_path = self._observed_path
            local_anchor = media_url_key(hosted_url) == target_key
            if (not local_anchor and self.media_provider is not None and
                    observed_path):
                try:
                    local_media = self.media_provider.local_to_url(observed_path)
                except (OSError, TypeError, ValueError):
                    local_media = None
                local_anchor = (isinstance(local_media, dict) and
                                media_url_key(local_media.get("url")) == target_key)
        if local_anchor:
            report_ready = False
            with self._lock:
                if self._loading_media is not None:
                    return True
                report_ready = self._pending_media is not None and not self.ready
                self._pending_media = None
                self._failed_media = None
                self._media_owner = self.name
                self._hosted_media_url = url
                self._current_media_source = None
                self.ready = True
            if report_ready:
                self._send_set({"ready": {"isReady": True,
                                          "manuallyInitiated": False}})
            return True

        should_report_not_ready = False
        with self._lock:
            publisher = username
            if declared_owner is not None:
                declared_user = self.users.get(declared_owner)
                declared_file = (declared_user.get("file_info")
                                 if isinstance(declared_user, dict) else None)
                declared_url = (declared_file.get("media_url")
                                if isinstance(declared_file, dict) else None)
                declared_self_claim = (isinstance(declared_file, dict) and
                                       declared_file.get("media_owner") == declared_owner)
                owner_matches = (
                    declared_owner == username or
                    (isinstance(declared_user, dict) and
                     declared_user.get("room") == room and
                     isinstance(declared_file, dict) and
                     declared_self_claim and
                     declared_file.get("source_type") == "alist" and
                     media_url_key(declared_url) == target_key)
                )
                # 带发布者字段的回显必须能追溯到同房间、同 URL 的成员；
                # 原发布者已离开时不把观看者提升为新的媒体源。
                if not owner_matches:
                    return False
                publisher = declared_owner
            if self._media_gate:
                return True
            owner = self._media_owner
            owner_active = (owner == self.name and self._hosted_media_url is not None)
            if owner and owner != self.name:
                owner_user = self.users.get(owner)
                owner_active = (isinstance(owner_user, dict) and
                                owner_user.get("room") == self.room)
            if owner and owner != publisher and owner_active:
                return False
            if owner and not owner_active:
                self._media_owner = None
            # 房主收到观看者对同一地址的回显时必须继续使用本地原文件。
            if media_url_key(self._hosted_media_url) == target_key:
                return True
            current_key = media_url_key(self._observed_path)
            if current_key == target_key:
                self._current_media_source = dict(file_info)
                self._current_media_source["media_owner"] = publisher
                self._current_media_scope = (self.args.server, self.room)
                self._current_media_confirmed = True
                self._media_owner = publisher
                return True
            for queued in (self._pending_media, self._loading_media):
                if queued and media_url_key(queued.get("url")) == target_key:
                    return True
            if (self._failed_media and media_url_key(self._failed_media.get("url")) == target_key
                    and time.time() - self._failed_media.get("time", 0) < 10.0):
                return True
            self._pending_media = {"url": url, "owner": publisher,
                                   "file": dict(file_info), "queued": time.time(),
                                   "scope": (self.args.server, self.room)}
            self._pending_media["file"]["media_owner"] = publisher
            self._cancelled_media_restore = None
            self._media_owner = publisher
            self.ready = False
            should_report_not_ready = True
        if should_report_not_ready:
            self._send_set({"ready": {"isReady": False, "manuallyInitiated": False}})
            log("收到 %s 分享的 AList 媒体，等待 mpv 加载：%s" %
                (publisher, file_info.get("name") or url))
        return True

    def _reconcile_remote_media(self, username, room, file_info):
        """Invalidate a tracked owner's URL when their file announcement changes.

        Network messages and mpv loading run on different threads.  Clearing the
        exact queued/loading object makes a late load completion harmless; the
        completion path verifies object identity again before announcing ready.
        """
        if username == self.name or room != self.room:
            return False
        incoming_url = None
        if isinstance(file_info, dict) and file_info.get("source_type") == "alist":
            incoming_url = validated_media_url(file_info.get("media_url"))
        incoming_key = media_url_key(incoming_url)

        with self._lock:
            source = self._current_media_source
            source_tracks_owner = (isinstance(source, dict) and
                                   source.get("media_owner") == username)
            if self._media_owner not in (None, username):
                return False
            if self._media_owner is None and not source_tracks_owner:
                return False
            tracked = []
            for item in (self._pending_media, self._loading_media):
                if isinstance(item, dict) and item.get("owner") == username:
                    tracked.append(item.get("url"))
            if (isinstance(source, dict) and
                    source.get("media_owner") == username):
                tracked.append(source.get("media_url"))
            tracked_keys = {media_url_key(url) for url in tracked
                            if media_url_key(url) is not None}
            if incoming_key is not None and incoming_key in tracked_keys:
                return False

            cancelled_transition = False
            if (isinstance(self._pending_media, dict) and
                    self._pending_media.get("owner") == username):
                self._pending_media["cancelled"] = True
                self._pending_media = None
                cancelled_transition = True
            if (isinstance(self._loading_media, dict) and
                    self._loading_media.get("owner") == username):
                loading = self._loading_media
                loading["cancelled"] = True
                self._loading_media = None
                self._cancelled_media_restore = {
                    "position": self.global_pos,
                    "paused": (self.global_paused
                               if isinstance(self.global_paused, bool)
                               else loading.get("was_paused")),
                }
                cancelled_transition = True
            if (isinstance(source, dict) and
                    source.get("media_owner") == username):
                self._current_media_source = None
                self._current_media_scope = None
                self._current_media_confirmed = False
                self._last_file_identity = None
                self._last_file_payload = None
            if (isinstance(self._failed_media, dict) and
                    self._failed_media.get("owner") == username):
                self._failed_media = None
            self._media_owner = None
            return cancelled_transition

    def _begin_media_gate(self, url, position, paused):
        """发布新本地媒体时暂停房主，等待同房间成员加载同一 URL。"""
        url = validated_media_url(url)
        if url is None:
            return False
        resumed_previous_gate = False
        with self._lock:
            previous_gate = self._media_gate
            previous_resume = bool(previous_gate and previous_gate.get("resume"))
            self._hosted_media_url = url
            self._media_owner = self.name
            self._current_media_source = None
            waiters = {name for name, user in self.users.items()
                       if name != self.name and isinstance(user, dict)
                       and user.get("room") == self.room}
            if not waiters:
                self._media_gate = None
                if previous_resume:
                    self.player.set_pause(False)
                    if position is not None:
                        self.last_pos = float(position)
                    self.last_ts = time.time()
                    self.last_paused = False
                    self._corrected()
                    resumed_previous_gate = True
            resume = paused is False or previous_resume
            if not waiters:
                gate_created = False
            else:
                gate_created = True
            self._media_gate = {"url": url, "waiters": waiters,
                                "waiting": sorted(waiters),
                                "deadline": (time.time() + self.media_ready_timeout
                                             if self.media_ready_timeout > 0 else None),
                                "resume": resume} if gate_created else None
            if gate_created and resume:
                self.player.set_pause(True)
                now = time.time()
                if position is not None:
                    self.last_pos = float(position)
                self.last_ts = now
                self.last_paused = True
                self._corrected()
        if resumed_previous_gate:
            self._send_state(state_change=True, force_playstate=True)
        if not gate_created:
            return False
        if resume:
            self._send_state(state_change=True, force_playstate=True)
        log("已暂停，等待 %d 位成员加载 AList 媒体" % len(waiters))
        return True

    def _cancel_media_gate(self, position=None):
        """切换到非 AList 媒体时取消旧 gate，并恢复进入 gate 前的播放。"""
        resumed = False
        with self._lock:
            gate = self._media_gate
            self._media_gate = None
            self._hosted_media_url = None
            self._media_owner = None
            self._current_media_source = None
            if gate and gate.get("resume"):
                self.player.set_pause(False)
                if position is not None:
                    self.last_pos = float(position)
                self.last_ts = time.time()
                self.last_paused = False
                self._corrected()
                resumed = True
        if resumed:
            self._send_state(state_change=True, force_playstate=True)
        return resumed

    def _check_media_gate(self):
        """返回仍在等待与否；成员离开或超时不会把混合客户端永久锁死。"""
        resume = False
        timed_out = False
        waiting = []
        send_state = False
        with self._lock:
            gate = self._media_gate
            if not gate:
                return False
            target_key = media_url_key(gate.get("url"))
            active_waiters = set()
            for username in gate.get("waiters", set()):
                user = self.users.get(username)
                if not isinstance(user, dict) or user.get("room") != self.room:
                    continue
                active_waiters.add(username)
                file_info = user.get("file_info")
                ready_url = (file_info.get("media_url")
                             if isinstance(file_info, dict) else None)
                if not (user.get("ready") is True and
                        media_url_key(ready_url) == target_key):
                    waiting.append(username)
            gate["waiters"] = active_waiters
            gate["waiting"] = sorted(waiting)
            deadline = gate.get("deadline")
            timed_out = bool(waiting and deadline is not None and time.time() >= deadline)
            if waiting and not timed_out:
                return True
            resume = bool(gate.get("resume"))
            position = self.global_pos
            if position is not None:
                self.player.seek(max(0.0, float(position)))
                self.last_pos = float(position)
                send_state = True
            if resume:
                self.player.set_pause(False)
                self.last_paused = False
                send_state = True
            else:
                self.last_paused = True
            self.last_ts = time.time()
            self._corrected()
            self._media_gate = None

        if timed_out:
            message = "等待成员加载超时，已继续播放：%s" % ", ".join(sorted(waiting))
            with self._lock:
                self.last_error = message
            log(message)
        else:
            with self._lock:
                if str(self.last_error or "").startswith("等待成员加载超时"):
                    self.last_error = None
            log("房间成员已完成 AList 媒体加载")
        if send_state:
            self._send_state(state_change=True)
        return False

    def _fail_remote_media(self, item, reason):
        with self._lock:
            if (self._loading_media is not item or item.get("cancelled") or
                    item.get("scope") != (self.args.server, self.room) or
                    not self.logged):
                return False
            self._loading_media = None
            self._failed_media = {"url": item.get("url"),
                                  "owner": item.get("owner"),
                                  "time": time.time()}
            self.last_error = "AList 媒体加载失败：%s" % reason
        # 加载失败不等于就绪；保持 false，让房主明确等待或按超时策略处理。
        self._set_ready(False, manually=False, force=True)
        try:
            self.player.osd("AList 媒体加载失败：%s" % reason)
        except Exception:
            pass
        log("AList 媒体加载失败：%s" % reason)
        return True

    def _finish_remote_media(self, item):
        url = item["url"]
        with self._lock:
            if (self._loading_media is not item or item.get("cancelled") or
                    item.get("scope") != (self.args.server, self.room) or
                    not self.logged):
                return False
            position = self.global_pos
            paused = self.global_paused
            restore_speed = self.speed_changed
            self.speed_changed = False

        if restore_speed:
            self.player.set_speed(self.player.base_speed)
        if position is not None:
            self.player.seek(max(0.0, float(position)))
        target_paused = True if paused is None else bool(paused)
        self.player.set_pause(target_paused)
        info = self.player.file_info() or {"name": item.get("file", {}).get("name") or
                                          os.path.basename(urllib.parse.urlsplit(url).path)}
        path = self.player.current_path()
        payload = self._build_file_payload(info, path)
        if payload:
            payload["media_url"] = url
            payload["source_type"] = "alist"
            payload["media_owner"] = item.get("owner")
        identity = self._file_identity(path, payload)
        now = time.time()
        player_position = (float(position) if position is not None
                           else self.player.get_state()[0])
        with self._lock:
            if (self._loading_media is not item or item.get("cancelled") or
                    item.get("scope") != (self.args.server, self.room) or
                    not self.logged):
                return False
            self._current_media_source = dict(payload or item.get("file") or {})
            self._current_media_source["media_url"] = url
            self._current_media_source["source_type"] = "alist"
            self._current_media_source["media_owner"] = item.get("owner")
            self._current_media_scope = (self.args.server, self.room)
            self._current_media_confirmed = True
            self._observed_path = url
            self.filename = payload.get("name") if payload else None
            self._last_file_payload = dict(payload or {})
            self._last_file_identity = identity
            self._loading_media = None
            self._failed_media = None
            if str(self.last_error or "").startswith("AList 媒体加载失败"):
                self.last_error = None
            self.last_pos = player_position
            self.last_ts = now
            self.last_paused = target_paused
            self._corrected()
            # Keep completion publication serialized with owner replacement and
            # room changes.  Otherwise a new URL can announce ready=false while
            # this stale completion follows it with ready=true.
            if payload:
                self._send_set({"file": payload})
            self._set_ready(True, manually=False, force=True)
            if self.last_pos is not None:
                self._send_state(state_change=True)
        self.player.osd("已加载 %s 分享的在线媒体" % item.get("owner", "房主"))
        log("AList 媒体已加载并就绪：%s" % (payload or {}).get("name", url))
        return True

    def _process_media_loading(self):
        """推进非阻塞加载状态机；每轮 watcher 最多执行一个阶段。"""
        start_item = None
        with self._lock:
            if (self._pending_media is not None and
                    (self._pending_media.get("cancelled") or
                     self._pending_media.get("scope") !=
                     (self.args.server, self.room) or not self.logged)):
                self._pending_media["cancelled"] = True
                self._pending_media = None
            if self._loading_media is None and self._pending_media is not None:
                start_item = self._pending_media
                self._pending_media = None
                start_item["started"] = time.time()
                start_item["deadline"] = time.time() + self.media_load_timeout
                start_item["was_paused"] = self.last_paused
                self._loading_media = start_item
                self.last_paused = True
                self.last_correction = time.time()
            item = self._loading_media
        if item is None:
            return False

        if start_item is not None:
            try:
                self._set_ready(False, manually=False)
                with self._lock:
                    if (self._loading_media is not item or
                            item.get("cancelled")):
                        return True
                    restore_speed = self.speed_changed
                    self.speed_changed = False
                if restore_speed:
                    self.player.set_speed(self.player.base_speed)
                self.player.load_remote(item["url"], self.http_readahead)
            except Exception as exc:
                self._fail_remote_media(item, str(exc))
            return True

        try:
            checker = getattr(self.player, "is_media_ready", None)
            if checker is not None:
                loaded = bool(checker(item["url"]))
            else:
                path = self.player.current_path()
                position, _paused = self.player.get_state()
                loaded = media_url_key(path) == media_url_key(item["url"]) and position is not None
        except Exception as exc:
            self._fail_remote_media(item, str(exc))
            return True

        if loaded:
            try:
                self._finish_remote_media(item)
            except Exception as exc:
                self._fail_remote_media(item, str(exc))
        elif time.time() >= item.get("deadline", 0):
            self._fail_remote_media(item, "等待 mpv file-loaded 超时")
        return True

    def _restore_cancelled_media(self):
        """Restore the room timeline after an in-flight URL is withdrawn."""
        with self._lock:
            if (self._cancelled_media_restore is None or self._pending_media or
                    self._loading_media):
                return False
            restore = self._cancelled_media_restore
            self._cancelled_media_restore = None
            position = restore.get("position")
            paused = restore.get("paused")
        try:
            if finite_number(position):
                self.player.seek(max(0.0, float(position)))
            self.player.set_pause(paused if isinstance(paused, bool) else True)
            current_position, current_paused = self.player.get_state()
        except Exception as exc:
            with self._lock:
                self.last_error = "恢复撤回媒体后的播放状态失败：%s" % exc
            return True
        with self._lock:
            self.last_pos = current_position
            self.last_ts = time.time()
            self.last_paused = current_paused
            self._corrected()
        if current_position is not None:
            self._send_state(state_change=True)
        return True

    def force_sync(self):
        """立即将本机播放器对齐到最近一次房间状态。

        返回 ``False`` 表示尚未收到可用的房间 playstate；调用方可据此在
        控制面板显示“等待房间状态”，而不是盲目跳到 0 秒。成功后发送一
        个带 ignore 标记的 State，避免服务器把旧位置立刻回弹回来。
        """
        with self._lock:
            position = self.global_pos
            paused = self.global_paused
            if (position is None or paused is None or self._pending_media or
                    self._loading_media or self._cancelled_media_restore or
                    self._media_gate):
                return False
            try:
                position = float(position)
            except (TypeError, ValueError):
                return False
            restore_speed = bool(self.speed_changed)
            try:
                if restore_speed:
                    self.player.set_speed(self.player.base_speed)
                self.player.seek(position)
                self.player.set_pause(bool(paused))
            except Exception:
                # 让控制桥把具体异常转换成 last_error；不要破坏缓存状态。
                raise
            now = time.time()
            self.last_pos = position
            self.last_ts = now
            self.last_paused = bool(paused)
            self.speed_changed = False
            self._corrected()
        self._send_state(state_change=True)
        return True

    def _on_disconnect(self):
        restore_speed = False
        restore_playback = False
        with self._lock:
            gate = self._media_gate or {}
            loading = self._loading_media or {}
            restore_playback = (bool(gate.get("resume")) or
                                loading.get("was_paused") is False)
            self.logged = False
            self.global_pos = None
            self.global_paused = None
            self.client_ignore = 0
            self.server_ignore = 0
            self.last_pos = None
            self.last_ts = time.time()
            self.last_paused = None
            self.ready = True
            self.filename = None
            self._last_file_identity = None
            self._last_file_payload = None
            self._pending_media = None
            self._loading_media = None
            self._cancelled_media_restore = None
            self._hosted_media_url = None
            self._media_owner = None
            self._media_gate = None
            self._failed_media = None
            if self._current_media_source and self._current_media_scope is None:
                self._current_media_scope = (self.args.server, self.room)
            if self._current_media_source:
                self._current_media_confirmed = False
            self.latency_calc = None
            self.users = {}
            self._received_user_list = False
            self.server_isolate_rooms = None
            self.ping = PingService()
            # 断线时没有服务器状态可供减速对齐，必须恢复用户原本速度；
            # 否则重连失败期间 UI 会一直显示 0.95x。
            restore_speed = self.speed_changed
            self.speed_changed = False
            if restore_speed:
                # watcher 可能正好在恢复速度的 IPC 往返期间采样；短暂
                # 抑制其跳转检测，避免把恢复动作误报成用户 seek。
                self.last_correction = time.time()
        if restore_speed:
            try:
                self.player.set_speed(self.player.base_speed)
            except Exception:
                pass
        if restore_playback:
            try:
                self.player.set_pause(False)
            except Exception:
                pass

    # ---------- 协议收发 ----------
    def _effective_rate(self):
        return self.player.base_speed * (SLOWDOWN_RATE if self.speed_changed else 1.0)

    def _project(self, pos, ts):
        """从上次轮询值外推当前位置（mpv 按实际速度播放，需按速度外推）"""
        if pos is None or self.last_paused:
            return pos
        return pos + (time.time() - ts) * self._effective_rate()

    def _send(self, obj):
        with self._sendlock:
            sock = self.sock
            if sock is None:
                raise OSError("同步服务器连接尚未建立")
            try:
                data = (json.dumps(obj, ensure_ascii=True, allow_nan=False) +
                        "\r\n").encode("utf-8")  # 官方协议行结束符是 \r\n
            except (TypeError, ValueError, UnicodeError) as exc:
                raise OSError("同步协议消息无法序列化") from exc
            if len(data) > MAX_PROTOCOL_MESSAGE:
                raise OSError("同步协议消息超过 4 MiB 限制")
            sock.sendall(data)

    def _send_set(self, setting):
        try:
            self._send({"Set": setting})
        except OSError:
            pass

    def _hello(self):
        hello = {
            "username": self.name,
            "room": {"name": self.room},
            "version": "1.2.255",          # 兼容旧服务器的固定写法（照抄官方）
            "realversion": PROTOCOL_VERSION,
            "features": {
                "sharedPlaylists": False,  # 不共享播放列表
                "chat": False,             # 不需要聊天
                "uiMode": "console",
                "featureList": True,
                "readiness": True,
                "managedRooms": True,
                "persistentRooms": True,
                "setOthersReadiness": True,
            },
        }
        self._send({"Hello": hello})
        self._send({"List": None})

    def _send_state(self, do_seek=False, state_change=False, force_playstate=False):
        with self._lock:
            pos = self.last_pos
            if pos is not None:
                pos = self._project(pos, self.last_ts)
            paused = self.last_paused
            state = {"ping": {}}
            media_transition = bool(self._pending_media or self._loading_media or
                                    self._cancelled_media_restore or self._media_gate)
            playstate_allowed = (force_playstate or
                                 ((self.client_ignore == 0 or self.server_ignore != 0)
                                  and not media_transition))
            if playstate_allowed and pos is not None and paused is not None:
                state["playstate"] = {"position": round(pos, 3), "paused": paused}
                if do_seek:
                    state["playstate"]["doSeek"] = True
            if self.latency_calc is not None:
                state["ping"]["latencyCalculation"] = self.latency_calc
            state["ping"]["clientLatencyCalculation"] = self.ping.new_timestamp()
            state["ping"]["clientRtt"] = self.ping.get_rtt()
            if state_change:
                self.client_ignore += 1
            if self.server_ignore or self.client_ignore:
                state["ignoringOnTheFly"] = {}
                if self.server_ignore:
                    state["ignoringOnTheFly"]["server"] = self.server_ignore
                    self.server_ignore = 0
                if self.client_ignore:
                    state["ignoringOnTheFly"]["client"] = self.client_ignore
        try:
            self._send({"State": state})
        except OSError:
            pass

    # ---------- 服务器消息 ----------
    def _reader_loop(self):
        self._hello()
        buf = b""
        while not self._stop.is_set():
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                raise ConnectionError("服务器超过 %.0f 秒无消息" % PROTOCOL_TIMEOUT)
            if not data:
                raise ConnectionError("服务器关闭了连接")
            buf += data
            if len(buf) > MAX_PROTOCOL_MESSAGE and b"\n" not in buf:
                raise ConnectionError("服务器消息超过 4 MiB 限制")
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if len(line) > MAX_PROTOCOL_MESSAGE:
                    raise ConnectionError("服务器消息超过 4 MiB 限制")
                line = line.strip()
                if not line:
                    continue
                if getattr(self.args, "verbose", False):
                    log("<< %s" % line.decode("utf-8", "replace")[:400])
                try:
                    msg = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                for key, val in msg.items():
                    handler = getattr(self, "_on_" + key.lower(), None)
                    if handler:
                        handler(val)
            if len(buf) > MAX_PROTOCOL_MESSAGE:
                raise ConnectionError("服务器消息超过 4 MiB 限制")

    def _on_hello(self, hello):
        hello = hello if isinstance(hello, dict) else {}
        assigned_name = hello.get("username")
        assigned_room_info = hello.get("room")
        assigned_room = (assigned_room_info.get("name")
                         if isinstance(assigned_room_info, dict) else None)
        features = hello.get("features")
        isolate_rooms = (isinstance(features, dict) and
                         features.get("isolateRooms") is True)
        isolation_warning = None
        if self.media_sharing_enabled and self.alist_server and not isolate_rooms:
            isolation_warning = ("服务器未声明房间隔离，已禁用 AList 媒体共享；"
                                 "普通播放同步不受影响")
        restore_playback = False
        with self._lock:
            if isinstance(assigned_name, str) and assigned_name:
                self.name = assigned_name
                self.args.name = self.name
            if isinstance(assigned_room, str) and assigned_room:
                new_scope = (self.args.server, assigned_room)
                if assigned_room != self.room:
                    gate = self._media_gate or {}
                    loading = self._loading_media or {}
                    restore_playback = (bool(gate.get("resume")) or
                                        loading.get("was_paused") is False)
                    for item in (self._pending_media, self._loading_media):
                        if isinstance(item, dict):
                            item["cancelled"] = True
                    self._pending_media = None
                    self._loading_media = None
                    self._cancelled_media_restore = None
                    self._hosted_media_url = None
                    self._media_owner = None
                    self._media_gate = None
                    self._failed_media = None
                    self._current_media_source = None
                    self._current_media_scope = None
                    self._current_media_confirmed = False
                    self._last_file_identity = None
                    self._last_file_payload = None
                    self.global_pos = None
                    self.global_paused = None
                    self.users = {}
                    self._received_user_list = False
                    self.ready = True
                if (self._current_media_source and
                        self._current_media_scope not in (None, new_scope)):
                    self._current_media_source = None
                    self._current_media_scope = None
                    self._current_media_confirmed = False
                    self._last_file_identity = None
                    self._last_file_payload = None
                self.room = assigned_room
                self.args.room = assigned_room
            self.server_isolate_rooms = isolate_rooms
            self.logged = True
            self.last_error = isolation_warning
        if restore_playback:
            try:
                self.player.set_pause(False)
            except Exception:
                pass
        motd = hello.get("motd")
        log("已登录，房间「%s」，昵称「%s」" % (self.room, hello.get("username")))
        if isolation_warning:
            log(isolation_warning)
        if motd:
            log("服务器公告：%s" % str(motd).replace("\n", " "))

    def _on_error(self, err):
        message = err.get("message") if isinstance(err, dict) else err
        with self._lock:
            self.last_error = str(message or "服务器返回未知错误")
        log("服务器错误：%s" % message)

    def _on_chat(self, _msg):
        pass                                   # 不需要聊天

    def _on_tls(self, _msg):
        pass                                   # 明文模式，忽略 TLS 协商

    def _on_list(self, rooms):
        candidates = []
        new_users = {}
        room_items = rooms.items() if isinstance(rooms, dict) else ()
        for room, members in room_items:
            if not isinstance(room, str) or not isinstance(members, dict):
                continue
            for uname, u in (members or {}).items():
                if not isinstance(uname, str) or not isinstance(u, dict):
                    continue
                raw_ready = u.get("isReady")
                ready = raw_ready if isinstance(raw_ready, bool) else None
                controller = bool(u.get("controller"))
                raw_file = u.get("file")
                file_info = normalized_remote_file(raw_file)
                file_name = (file_info.get("name")
                             if isinstance(file_info, dict) else
                             (raw_file if isinstance(raw_file, str) else None))
                new_users[uname] = {"room": room, "file": file_name,
                                    "file_info": file_info, "ready": ready,
                                    "controller": controller}
                if file_info and uname != self.name:
                    candidates.append((not controller, uname, room, file_info))
        with self._lock:
            source_owner = ((self._current_media_source or {}).get("media_owner")
                            if isinstance(self._current_media_source, dict) else None)
            existing_owner = self._media_owner or source_owner
            self.users = new_users
            self._received_user_list = True
            if self._media_gate:
                additions = {uname for uname, user in self.users.items()
                             if uname != self.name and user.get("room") == self.room}
                self._media_gate["waiters"].update(additions)
                self._media_gate["waiting"] = sorted(self._media_gate["waiters"])
            observed_path = self._observed_path

        cancelled_transition = False
        if existing_owner and existing_owner != self.name:
            owner_user = new_users.get(existing_owner)
            if (not isinstance(owner_user, dict) or
                    owner_user.get("room") != self.room):
                cancelled_transition = self._reconcile_remote_media(
                    existing_owner, self.room, None
                )
            else:
                cancelled_transition = self._reconcile_remote_media(
                    existing_owner, self.room, owner_user.get("file_info")
                )

        local_key = None
        if self.media_provider is not None and observed_path:
            try:
                mapped = self.media_provider.local_to_url(observed_path)
            except (OSError, TypeError, ValueError):
                mapped = None
            if isinstance(mapped, dict):
                local_key = media_url_key(mapped.get("url"))

        def candidate_priority(candidate):
            not_controller, uname, _room, file_info = candidate
            declared = file_info.get("media_owner")
            candidate_key = media_url_key(file_info.get("media_url"))
            is_local_anchor = (declared == self.name and local_key is not None and
                               candidate_key == local_key)
            publisher = declared if isinstance(declared, str) and declared else uname
            is_existing_owner = bool(existing_owner and publisher == existing_owner)
            source_rank = (0 if declared == uname else
                           (1 if declared is None else 2))
            return (not is_local_anchor, not is_existing_owner, source_rank,
                    not_controller, uname)

        accepted_media = False
        for _not_controller, uname, room, file_info in sorted(
                candidates, key=candidate_priority):
            if self._consider_remote_media(uname, room, file_info):
                accepted_media = True
                break
        self._print_users()

    def _on_set(self, settings):
        if not isinstance(settings, dict):
            return
        for cmd, val in settings.items():
            if cmd == "user":
                if not isinstance(val, dict):
                    continue
                for uname, u in val.items():
                    if not isinstance(uname, str) or not isinstance(u, dict):
                        continue
                    event = u.get("event") if isinstance(u.get("event"), dict) else {}
                    if event.get("left"):
                        with self._lock:
                            previous = self.users.get(uname) or {}
                            previous_room = previous.get("room")
                        cancelled = self._reconcile_remote_media(
                            uname, previous_room or self.room, None
                        )
                        with self._lock:
                            self.users.pop(uname, None)
                            if self._media_owner == uname:
                                self._media_owner = None
                        log("%s 离开了" % uname)
                    else:
                        with self._lock:
                            previous = self.users.get(uname) or {}
                            previous_room = previous.get("room")
                            room_info = u.get("room")
                            announced_room = (room_info.get("name")
                                              if isinstance(room_info, dict) else None)
                            room = announced_room or previous.get("room")
                            has_file = "file" in u
                            raw_file = u.get("file") if has_file else None
                            new_file_info = normalized_remote_file(raw_file)
                            file_info = (new_file_info if has_file
                                         else previous.get("file_info"))
                            file_name = (new_file_info.get("name")
                                         if isinstance(new_file_info, dict) else
                                         (raw_file if isinstance(raw_file, str) else None))
                            raw_ready = u.get("isReady")
                            if ("isReady" in u and
                                    (isinstance(raw_ready, bool) or raw_ready is None)):
                                user_ready = raw_ready
                            else:
                                user_ready = previous.get("ready")
                            self.users[uname] = {
                                "room": room,
                                "file": file_name if file_name is not None else previous.get("file"),
                                "file_info": file_info,
                                "ready": user_ready,
                                "controller": previous.get("controller", False),
                            }
                            entered_room = (room == self.room and
                                            previous_room != self.room)
                            joined_room = bool(event.get("joined") or entered_room)
                            if (joined_room and room == self.room and
                                    self._media_gate and
                                    uname != self.name):
                                self._media_gate["waiters"].add(uname)
                                self._media_gate["waiting"] = sorted(
                                    self._media_gate["waiters"])
                        cancelled = (self._reconcile_remote_media(
                            uname, room, new_file_info
                        ) if has_file else False)
                        accepted = (self._consider_remote_media(
                            uname, room, new_file_info
                        ) if new_file_info else False)
                        if joined_room:
                            log("%s 加入了房间「%s」" % (uname, room))
                    self._print_users()
            elif cmd == "ready":
                if not isinstance(val, dict):
                    continue
                uname = val.get("username")
                ready_value = val.get("isReady")
                if not isinstance(uname, str) or not isinstance(ready_value, bool):
                    continue
                with self._lock:
                    if uname in self.users:
                        self.users[uname]["ready"] = ready_value
                state = "已准备好" if ready_value else "暂离（未准备好）"
                log("%s %s" % (uname, state))

    def _on_state(self, state):
        if not isinstance(state, dict):
            return
        ignore = state.get("ignoringOnTheFly")
        ignore = ignore if isinstance(ignore, dict) else {}
        with self._lock:
            server_generation = ignore.get("server")
            client_generation = ignore.get("client")
            if (isinstance(server_generation, int) and
                    not isinstance(server_generation, bool) and
                    0 <= server_generation <= 1000000000):
                self.server_ignore = server_generation
                self.client_ignore = 0
            if (isinstance(client_generation, int) and
                    not isinstance(client_generation, bool) and
                    client_generation == self.client_ignore):
                self.client_ignore = 0
        ping = state.get("ping")
        ping = ping if isinstance(ping, dict) else {}
        client_timestamp = ping.get("clientLatencyCalculation")
        if finite_number(client_timestamp):
            self.ping.receive_message(client_timestamp, ping.get("serverRtt", 0))
        latency_timestamp = ping.get("latencyCalculation")
        self.latency_calc = (float(latency_timestamp)
                             if finite_number(latency_timestamp) else None)
        # 服务器回传的 clientLatencyCalculation 用来估计报文在网络中的
        # 单向年龄；官方客户端会把这个年龄加到正在播放的位置上。
        message_age = self.ping.get_forward_delay()
        playstate = state.get("playstate")
        if isinstance(playstate, dict) and self.client_ignore == 0:
            position = playstate.get("position")
            paused = playstate.get("paused")
            do_seek = playstate.get("doSeek") is True
            set_by = playstate.get("setBy")
            if finite_number(position) and isinstance(paused, bool):
                position = float(position)
                self._apply_global(position, paused, do_seek, set_by, message_age)
        self._send_state()                       # 官方行为：每收到 State 立即回一份

    # ---------- 同步核心 ----------
    def _interp_pos(self):
        """插值出当前本地播放位置"""
        with self._lock:
            if self.last_pos is None:
                return None
            return self._project(self.last_pos, self.last_ts)

    def _corrected(self):
        self.last_correction = time.time()

    def _apply_global(self, position, paused, do_seek, set_by, message_age):
        with self._lock:
            made_change = False
            if not paused:
                position += message_age            # 官方：播放中补偿消息延迟
            first = self.global_pos is None
            old_global_paused = self.global_paused
            self.global_pos, self.global_paused = position, paused

            # 加载远程媒体时只缓存最新房间状态，不能把 seek/play 应用到
            # 即将被替换的旧文件。房主的 ready gate 同样保持强制暂停。
            if (self._pending_media or self._loading_media or
                    self._cancelled_media_restore or self._media_gate):
                return
            diff = self._interp_pos() - position if self.last_pos is not None else None

            # 初次收到房间状态：直接对齐
            if first and self.last_pos is not None:
                self.player.seek(position)
                self.player.set_pause(paused)
                self.last_pos, self.last_ts, self.last_paused = position, time.time(), paused
                self._corrected()
                made_change = True
                log("已同步到房间位置 %s" % fmt_time(position))

            # 有人跳转 → 全员跟随（官方：doSeek 广播）
            if do_seek and set_by != self.name and self.last_pos is not None:
                self.player.seek(position)
                self.last_pos, self.last_ts = position, time.time()
                self._corrected()
                made_change = True
                who = set_by or "有人"
                self.player.osd("⏩ %s 跳转到了 %s" % (who, fmt_time(position)))
                log("%s 跳转到了 %s，已跟随" % (who, fmt_time(position)))

            # 超前太多 → 回退对齐
            elif (diff is not None and diff > self.rewind_threshold and not do_seek
                  and set_by != self.name):
                self.player.seek(position)
                self.last_pos, self.last_ts = position, time.time()
                self._corrected()
                made_change = True
                self.player.osd("⏪ 与 %s 相差 %.1f 秒，已回退" % (set_by or "有人", diff))
                log("超前 %.1f 秒，已回退到 %s" % (diff, fmt_time(position)))

            # 超前一点 → 减速等待
            if not paused and diff is not None and not do_seek:
                if diff > SLOWDOWN_KICKIN and not self.speed_changed and set_by != self.name:
                    self.player.set_speed(self.player.base_speed * SLOWDOWN_RATE)
                    self.speed_changed = True
                    self._corrected()
                    made_change = True
                    self.player.osd("🐢 超前 %.1f 秒，已减速等待" % diff)
                elif self.speed_changed and diff < SLOWDOWN_RESET:
                    self.player.set_speed(self.player.base_speed)
                    self.speed_changed = False
                    self._corrected()
                    made_change = True
                    self.player.osd("▶ 已恢复原速")

            # 暂停联动
            who = set_by or "有人"
            if paused is True and self.last_paused is False:
                if set_by != self.name:
                    self.player.seek(position)     # 官方 SYNC_ON_PAUSE：暂停时对齐位置
                    self.last_pos, self.last_ts = position, time.time()
                self.player.set_pause(True)
                self.last_paused = True
                self._corrected()
                self.player.osd("⏸ %s 暂停了播放" % who)
                log("%s 暂停了播放" % who)
            elif (paused is False and self.last_paused is True
                  and (old_global_paused is True or first)):
                self.player.set_pause(False)
                self.last_paused = False
                self._corrected()
                self.player.osd("▶ %s 恢复了播放" % who)
                log("%s 恢复了播放" % who)

            if made_change:
                self._corrected()

    # ---------- 本地状态监视 ----------
    def _watcher_loop(self):
        while not self._stop.is_set():
            time.sleep(POLL_INTERVAL)
            if not self.logged:
                if not self.player.is_alive():
                    with self._lock:
                        self.last_error = "mpv 已退出"
                    self._stop.set()
                    return
                try:
                    offline_path = self.player.current_path()
                except (AttributeError, OSError, ValueError):
                    offline_path = None
                if offline_path:
                    with self._lock:
                        self._observed_path = offline_path
                continue

            # loadfile 由 watcher 分阶段推进，网络 reader 始终能及时应答。
            if self._restore_cancelled_media():
                self._check_media_gate()
                continue
            if self._process_media_loading():
                self._check_media_gate()
                continue

            gate_active = self._check_media_gate()
            pos, paused = self.player.get_state()
            if not self.player.is_alive():
                log("mpv 已退出，客户端关闭")
                with self._lock:
                    self.last_error = "mpv 已退出"
                self._stop.set()
                return
            info = self.player.file_info()
            try:
                path = self.player.current_path()
            except (AttributeError, OSError, ValueError):
                path = info.get("name") if info else None
            with self._lock:
                self._observed_path = path

            payload = self._build_file_payload(info, path)
            identity = self._file_identity(path, payload)
            announce = False
            gate_started = False
            if payload and identity is not None:
                with self._lock:
                    announce = identity != self._last_file_identity
                    old_hosted_url = self._hosted_media_url
                if announce:
                    media_url = validated_media_url(payload.get("media_url"))
                    local_host = bool(media_url and not validated_media_url(path))
                    with self._lock:
                        room_list_ready = self._received_user_list
                    if local_host and not room_list_ready:
                        # Hello 与 List 可能分批到达；先掌握现有成员，才能
                        # 正确建立“全员 ready”集合。下一轮会再次尝试。
                        announce = False
                    if announce and local_host and media_url_key(media_url) != media_url_key(old_hosted_url):
                        gate_started = self._begin_media_gate(media_url, pos, paused)
                        if gate_started and paused is False:
                            paused = True
                        elif not gate_started:
                            gate_active = False
                            pos, paused = self.player.get_state()
                    elif announce and not local_host and not media_url:
                        if self._cancel_media_gate(pos):
                            paused = False
                        gate_active = False
                    if announce:
                        with self._lock:
                            self.filename = payload.get("name")
                            self._last_file_payload = dict(payload)
                            self._last_file_identity = identity
                        self._send_set({"file": payload})
                        log("发送文件信息：%s%s" %
                            (self.filename, "（AList）" if media_url else ""))

            if gate_active or gate_started:
                # 等待期间即使本地用户按下播放，也立即恢复为暂停；ready
                # 保持为媒体可用状态，不把内部 gate 误报成“暂离”。
                if paused is not True:
                    self.player.set_pause(True)
                    paused = True
                    self._corrected()
                with self._lock:
                    if pos is not None:
                        self.last_pos = pos
                    self.last_ts = time.time()
                    self.last_paused = True
                continue

            with self._lock:
                now = time.time()
                if pos is None:
                    continue
                recently_corrected = now - self.last_correction < 0.8
                # 暂停/恢复检测（排除我们自己为同步而施加的操作）
                if paused is not None and paused != self.last_paused and not recently_corrected:
                    # 首次轮询时 last_pos 还是 None；先把当前采样写入缓存，
                    # 否则首个 State 只有 ping，没有 playstate，服务器不会
                    # 开始广播房间状态。
                    self.last_pos, self.last_ts = pos, now
                    self.last_paused = paused
                    self._set_ready(not paused, manually=True)
                    self._send_state(state_change=True)   # 防止服务器旧状态回弹
                # 用户手动跳转检测
                elif pos is not None and self.last_pos is not None and not recently_corrected and not self.speed_changed:
                    expected = self.last_pos
                    if not self.last_paused:
                        rate = self.player.base_speed * (SLOWDOWN_RATE if self.speed_changed else 1.0)
                        expected += (now - self.last_ts) * rate
                    if abs(pos - expected) > SEEK_DETECT_THRESHOLD:
                        self.last_pos, self.last_ts = pos, now
                        self._send_state(do_seek=True, state_change=True)
                        log("已把你的跳转同步给房间")
                        continue
                self.last_pos, self.last_ts = pos, now

    def _print_users(self):
        with self._lock:
            same_room = [(n, u) for n, u in self.users.items()
                         if u["room"] == self.room and n != self.name]
            names = ["%s(%s)" % (n, "已就绪" if u["ready"] in (True, None) else "暂离") for n, u in same_room]
        if names:
            log("房间成员：%s" % ", ".join(names))

    def status_snapshot(self):
        """返回给控制面板的 JSON-safe 状态快照。

        所有缓存字段都在状态锁内复制，控制面板线程不会与网络/监视线程
        共享可变 users 字典。位置使用本地采样外推，故即使状态文件每秒
        只刷新两次，进度条也能保持平滑。
        """
        with self._lock:
            position = None
            if self.last_pos is not None:
                try:
                    position = round(float(self._project(self.last_pos, self.last_ts)), 3)
                except (TypeError, ValueError, OverflowError):
                    position = None
            users = {}
            for uname, user in self.users.items():
                if not isinstance(user, dict):
                    continue
                file_value = user.get("file")
                if isinstance(file_value, dict):
                    file_value = dict(file_value)
                full_file = user.get("file_info")
                if isinstance(full_file, dict):
                    full_file = dict(full_file)
                users[str(uname)] = {
                    "room": user.get("room"),
                    "file": file_value,
                    "file_info": full_file,
                    "ready": user.get("ready"),
                }
            phase = ("stopped" if self._stop.is_set() else
                     ("connected" if self.logged else "connecting"))
            active_media = self._loading_media or self._pending_media or {}
            status_media_url = (active_media.get("url") or
                                (self._last_file_payload or {}).get("media_url") or
                                (self._current_media_source or {}).get("media_url"))
            status_source_type = ("alist" if active_media.get("url") else
                                  ((self._last_file_payload or {}).get("source_type") or
                                   (self._current_media_source or {}).get("source_type")))
            return {
                "logged": bool(self.logged),
                "connected": bool(self.logged),
                "phase": phase,
                "server": str(getattr(self.args, "server", "") or ""),
                "room": self.room,
                "name": self.name,
                "filename": self.filename,
                "position": position,
                "paused": self.last_paused,
                "speed_changed": bool(self.speed_changed),
                "users": users,
                "last_error": self.last_error,
                # 这些附加字段不影响旧 UI，但方便显示连接质量/房间进度。
                "global_position": self.global_pos,
                "global_paused": self.global_paused,
                "rtt": round(self.ping.get_rtt(), 3),
                "forward_delay": round(self.ping.get_forward_delay(), 3),
                "ready": bool(self.ready),
                "media_url": status_media_url,
                "source_type": status_source_type,
                "media_loading": bool(self._pending_media or self._loading_media),
                "waiting_for_ready": bool(self._media_gate),
                "waiting_users": (list(self._media_gate.get("waiting", []))
                                  if self._media_gate else []),
                "server_isolate_rooms": self.server_isolate_rooms,
                "timestamp": time.time(),
            }

    def stop(self):
        self._stop.set()
        self._reconnect_requested.set()
        with self._lock:
            self.logged = False
        sock = self.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


# ======================================================================
# 本地文件控制/状态桥（供简洁 UI 使用）
# ======================================================================
class FileControlBridge:
    """用两个 UTF-8 JSON 文件连接轻量控制面板。

    控制面板只需原子替换 ``syncplay_command.json``，本类会在下一个轮询
    周期消费一次；状态则原子写入 ``syncplay_status.json``。不依赖 HTTP、
    第三方库或 Windows 专用 API，因此 mpv 管道和虚拟播放器都可使用。
    """

    def __init__(self, client, command_file=DEFAULT_COMMAND_FILE,
                 status_file=DEFAULT_STATUS_FILE, interval=0.5):
        self.client = client
        command_file = command_file or DEFAULT_COMMAND_FILE
        status_file = status_file or DEFAULT_STATUS_FILE
        self.command_file = os.path.abspath(os.path.expanduser(str(command_file)))
        self.status_file = os.path.abspath(os.path.expanduser(str(status_file)))
        self.interval = max(0.1, min(float(interval or 0.5), 5.0))
        self._stop = threading.Event()
        self._thread = None
        self._last_digest = None
        self._last_write_error = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        # 自定义路径常见于多实例测试；只创建明确指定的父目录。
        for path in (self.command_file, self.status_file):
            parent = os.path.dirname(path)
            if parent:
                try:
                    os.makedirs(parent, exist_ok=True)
                except OSError as exc:
                    self._set_error("无法创建控制面板目录：%s" % exc)
        self._stop.clear()
        self._write_status()
        self._thread = threading.Thread(target=self._loop,
                                        name="syncplay-file-bridge",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.interval * 3.0))
        # 即使客户端因连接失败退出，也留下 logged=false 的最终快照。
        self._write_status()

    def poll_once(self):
        """执行一次命令轮询和状态写入，便于无 GUI 的自动化测试。"""
        self._consume_command()
        self._write_status()

    def _loop(self):
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def _set_error(self, message):
        with self.client._lock:
            self.client.last_error = str(message)

    @staticmethod
    def _command_items(payload):
        """兼容单命令、命令数组和常见的 ``set`` 包装格式。"""
        if isinstance(payload, str):
            return [{"command": payload}]
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        commands = payload.get("commands")
        if isinstance(commands, list):
            return commands
        if any(key in payload for key in ("command", "action", "cmd")):
            return [payload]
        if isinstance(payload.get("set"), dict):
            item = dict(payload["set"])
            item["command"] = "set"
            return [item]
        # 允许 UI 直接写 {server, room, name}，减少按钮端代码。
        if any(key in payload for key in ("server", "room", "name")):
            item = dict(payload)
            item["command"] = "set"
            return [item]
        return []

    def _consume_command(self):
        try:
            # 读取后立即关闭句柄，允许 Windows UI 用 os.replace 换入下一
            # 条命令；成功消费时清理函数会再次校验内容再截断。
            with open(self.command_file, "rb") as stream:
                raw = stream.read()
        except FileNotFoundError:
            # 删除后再写入同一命令时允许再次执行。
            self._last_digest = None
            return
        except OSError:
            # UI 可能正在 Windows 上替换文件；下一轮重试即可。
            return

        if not raw.strip():
            self._last_digest = None
            return
        digest = hashlib.sha256(raw).hexdigest()
        if digest == self._last_digest:
            return
        self._last_digest = digest
        try:
            payload = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            self._set_error("控制文件不是有效 JSON：%s" % exc)
            return

        items = self._command_items(payload)
        if not items:
            self._set_error("控制文件未包含 command/set 命令")
            return
        consumed = True
        for item in items:
            try:
                handled = self._handle_command(item)
            except Exception as exc:
                self._set_error("控制命令执行失败：%s" % exc)
                handled = False
            if not handled:
                consumed = False
        if consumed:
            self._clear_consumed_command(digest)

    def _clear_consumed_command(self, digest):
        """校验后清空命令；并发换入的新内容不会被清理。"""
        stream = None
        try:
            stream = open(self.command_file, "r+b")
            current = stream.read()
            if hashlib.sha256(current).hexdigest() != digest:
                return
            stream.seek(0)
            stream.truncate()
            stream.flush()
            try:
                os.fsync(stream.fileno())
            except OSError:
                pass
            # 空文件会在下一轮把 digest 复位；若 UI 紧接着换入同样的
            # JSON，仍会被视作一次新命令。
            self._last_digest = None
        except (OSError, ValueError):
            # 清理失败不影响客户端；digest 去重仍会避免重复执行。
            return
        finally:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _handle_command(self, item):
        if isinstance(item, str):
            item = {"command": item}
        if not isinstance(item, dict):
            self._set_error("控制命令必须是 JSON 对象")
            return False
        command = item.get("command", item.get("action", item.get("cmd", item.get("type"))))
        if command is None and isinstance(item.get("set"), dict):
            command = "set"
        command = str(command or "").strip().lower().replace("-", "_")

        if command in ("stop", "quit", "exit"):
            self.client.stop()
            return True
        if command in ("reconnect", "retry", "reload"):
            # UI 可能把最新 server/room/name 随 reconnect 一起传来；先
            # 应用设置，再显式唤醒连接线程。update_settings 在有变更时
            # 自身也会请求重连，重复设置不会造成第二次握手。
            values = (item.get("settings") or item.get("params") or
                      item.get("value") or item)
            changed = set()
            if isinstance(values, dict):
                changed = self.client.update_settings(
                    server=values.get("server"),
                    room=values.get("room"),
                    name=values.get("name"),
                )
            if not changed:
                self.client.request_reconnect()
            return True
        if command in ("set", "settings", "configure", "update"):
            values = (item.get("settings") or item.get("params") or
                      item.get("set") or item.get("value") or item)
            if not isinstance(values, dict):
                self._set_error("set 命令的值必须是对象")
                return False
            changed = self.client.update_settings(
                server=values.get("server"),
                room=values.get("room"),
                name=values.get("name"),
            )
            if not changed and not any(values.get(k) for k in ("server", "room", "name")):
                self._set_error("set 命令至少需要 server、room 或 name")
                return False
            return True

        if command in ("sync", "resync", "align"):
            try:
                if not self.client.force_sync():
                    self._set_error("暂时没有可用的房间状态，无法立即同步")
                    return False
            except Exception as exc:
                self._set_error("立即同步失败：%s" % exc)
                return False
            return True

        # 下面几个播放控制命令是可选便利项；UI 不需要直接连接 mpv IPC。
        if command in ("pause", "play", "toggle_pause", "toggle"):
            try:
                _pos, paused = self.client.player.get_state()
                target = (not bool(paused)) if command in ("toggle_pause", "toggle") else command == "pause"
                self.client.player.set_pause(target)
            except Exception as exc:
                self._set_error("播放控制失败：%s" % exc)
                return False
            return True
        if command in ("seek", "jump"):
            value = item.get("position", item.get("seconds", item.get("value")))
            try:
                self.client.player.seek(float(value))
            except (TypeError, ValueError, OSError) as exc:
                self._set_error("跳转命令无效：%s" % exc)
                return False
            return True

        self._set_error("未知控制命令：%s" % command)
        return False

    def _write_status(self):
        try:
            payload = self.client.status_snapshot()
            media_test = getattr(self.client.player, "last_media_test", None)
            if media_test is not None:
                payload["alist_media_test"] = media_test.as_dict()
                if (getattr(media_test, "signature_required", False) and
                        MEDIA_ACCESS_ERROR in str(payload.get("last_error") or "")):
                    payload["last_error"] = MEDIA_ACCESS_ERROR
            encoded = (json.dumps(payload, ensure_ascii=True, indent=2,
                                  allow_nan=False) + "\n").encode("utf-8")
        except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
            self._set_error("生成状态文件失败：%s" % exc)
            return

        parent = os.path.dirname(self.status_file) or "."
        base = os.path.basename(self.status_file) or "syncplay_status.json"
        fd = None
        temp_path = None
        try:
            fd, temp_path = tempfile.mkstemp(prefix=".%s." % base,
                                             suffix=".tmp", dir=parent)
            with os.fdopen(fd, "wb") as stream:
                fd = None
                stream.write(encoded)
                stream.flush()
                try:
                    os.fsync(stream.fileno())
                except OSError:
                    pass
            os.replace(temp_path, self.status_file)
            temp_path = None
            self._last_write_error = None
        except OSError as exc:
            # 读者短暂占用旧文件时不要让桥线程退出；原子替换下下一轮
            # 会自动恢复。相同错误只记录一次，避免污染控制台。
            text = str(exc)
            if text != self._last_write_error:
                self._last_write_error = text
                log("写入状态文件失败：%s" % text)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass


# ======================================================================
# 精简中继服务器（--host）：给自己人开黑用，无需下载官方服务端
# ======================================================================
class MiniServer:
    def __init__(self, port):
        self.port = port
        self.rooms = {}                            # room -> list[Watcher]
        self.lock = threading.Lock()
        # 每房间的聚合状态：seek 覆盖、暂停发起者、最近操作者
        self.room_seek = {}                        # room -> {"position","set_by","ts","until","flagged"}
        self.room_paused = {}                      # room -> bool（最后一次暂停/恢复）
        self.room_actor = {}

    class Watcher:
        def __init__(self, conn, addr):
            self.conn = conn
            self.addr = addr
            self.name = None
            self.room = None
            self.ready = True
            self.pos = None
            self.paused = True
            self.pos_ts = time.time()
            self.client_ts = None
            self.rtt = 0.0
            self.file = None
            self._ack_ignore = 0
            self.initial_sync_token = None
            self.connected_at = time.monotonic()
            self.last_valid_state = self.connected_at
            self.dead = False
            self.sendlock = threading.Lock()

        def interp(self):
            if self.pos is None:
                return None
            if self.paused:
                return self.pos
            return self.pos + (time.time() - self.pos_ts)

        def send(self, obj):
            if self.dead:
                return False
            try:
                data = (json.dumps(obj, ensure_ascii=True, allow_nan=False) +
                        "\r\n").encode("utf-8")
            except (TypeError, ValueError, UnicodeError):
                # A malformed internal payload must not be able to disconnect
                # an otherwise healthy receiving client.
                return False
            if len(data) > MAX_PROTOCOL_MESSAGE:
                try:
                    data = (json.dumps({"Error": {"message":
                            "服务器响应超过 4 MiB 限制"}}) + "\r\n").encode("utf-8")
                except (TypeError, ValueError, UnicodeError):
                    return False
            try:
                with self.sendlock:
                    self.conn.sendall(data)
                return True
            except OSError:
                self.dead = True
                try:
                    self.conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return False

    @staticmethod
    def _clean_text(value, maximum, strip=True):
        if not isinstance(value, str):
            return None
        value = value.strip() if strip else value
        if not value or any(ord(character) < 32 for character in value):
            return None
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return None
        return value[:maximum]

    @classmethod
    def _clean_label(cls, value):
        return cls._clean_text(value, MAX_IDENTITY_LENGTH)

    @staticmethod
    def _finite_number(value):
        return finite_number(value)

    _INVALID_FILE = object()

    @classmethod
    def _clean_file(cls, value):
        """Normalize the fields understood by official and extended clients."""
        if value is None:
            return None
        if not isinstance(value, dict):
            return cls._INVALID_FILE
        if not value:
            return {}

        name = cls._clean_text(value.get("name"), MAX_FILENAME_LENGTH,
                               strip=False)
        if name is None:
            return cls._INVALID_FILE
        duration = value.get("duration", 0.0)
        if not finite_number(duration) or float(duration) < 0:
            duration = 0.0
        else:
            duration = float(duration)

        raw_size = value.get("size", 0)
        if isinstance(raw_size, str):
            size = cls._clean_text(raw_size, MAX_IDENTITY_LENGTH)
            if size is None:
                size = 0
        elif finite_number(raw_size) and float(raw_size) >= 0:
            size = int(raw_size) if float(raw_size).is_integer() else float(raw_size)
        else:
            size = 0

        result = {"name": name[:MAX_FILENAME_LENGTH],
                  "duration": duration, "size": size}
        media_url = validated_media_url(value.get("media_url"))
        if value.get("source_type") == "alist" and media_url is not None:
            result["media_url"] = media_url
            result["source_type"] = "alist"
            owner = cls._clean_label(value.get("media_owner"))
            if owner is not None:
                result["media_owner"] = owner
        return result

    def _clean_empty_room(self, room):
        if self.rooms.get(room):
            return
        self.rooms.pop(room, None)
        self.room_seek.pop(room, None)
        self.room_paused.pop(room, None)
        self.room_actor.pop(room, None)

    def _room_playstate_locked(self, room, now, consume_seek=False):
        alive = [member for member in self.rooms.get(room, [])
                 if not member.dead and member.pos is not None]
        if not alive:
            return None
        paused = self.room_paused.get(room, False)
        override = self.room_seek.get(room)
        do_seek = False
        if override and now < override["until"]:
            position = override["position"]
            if not paused:
                position += now - override["ts"]
            if consume_seek and override.pop("flagged", False):
                do_seek = True
        else:
            if override:
                self.room_seek.pop(room, None)
            position = min(member.interp() for member in alive)
        alive_names = {member.name for member in alive}
        actor = self.room_actor.get(room)
        if actor not in alive_names:
            actor = alive[0].name
            self.room_actor[room] = actor
        playstate = {"position": round(position, 3),
                     "paused": paused,
                     "setBy": actor}
        if do_seek:
            playstate["doSeek"] = True
        return playstate

    def start(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", self.port))
        srv.listen(16)
        log("精简 Syncplay 服务器已启动，端口 %d（Ctrl+C 退出）" % self.port)
        threading.Thread(target=self._tick, daemon=True).start()
        while True:
            conn, addr = srv.accept()
            conn.settimeout(5.0)
            w = self.Watcher(conn, addr)
            threading.Thread(target=self._client_loop, args=(w,), daemon=True).start()

    # ---- 房间广播线程 ----
    def _tick(self):
        while True:
            time.sleep(STATE_TICK)
            self._tick_once()

    def _tick_once(self, now=None):
        """Build and send one room-state heartbeat cycle."""
        now = time.time() if now is None else float(now)
        outbox = []
        with self.lock:
            for room_name, watchers in list(self.rooms.items()):
                # Even an idle room needs ping-only State messages, or clients
                # without a loaded file time out and reconnect.
                playstate = self._room_playstate_locked(
                    room_name, now, consume_seek=True
                )
                for w in list(watchers):
                    if w.dead:
                        continue
                    state = {"ping": {"latencyCalculation":
                                      w.initial_sync_token or now}}
                    if playstate is not None:
                        state["playstate"] = dict(playstate)
                        if w.initial_sync_token is not None:
                            state["playstate"]["doSeek"] = True
                    if w.client_ts is not None:
                        state["ping"]["clientLatencyCalculation"] = w.client_ts
                        state["ping"]["serverRtt"] = w.rtt
                    if getattr(w, "_ack_ignore", 0):
                        state["ignoringOnTheFly"] = {"client": w._ack_ignore}
                        w._ack_ignore = 0
                    outbox.append((w, {"State": state}))
        for watcher, message in outbox:
            watcher.send(message)
        return len(outbox)

    def _broadcast_user_event(self, room, event, name, exclude=None):
        payload = dict(event or {})
        if "event" in payload:
            payload.setdefault("room", {"name": room})
        with self.lock:
            watchers = list(self.rooms.get(room, []))
        for w in watchers:
            if w.name != exclude:
                w.send({"Set": {"user": {name: payload}}})

    def _send_list(self, w):
        rooms = {}
        with self.lock:
            room = w.room
            watchers = list(self.rooms.get(room, []))
            rooms[room] = {}
            for m in watchers:
                if m.name:
                    rooms[room][m.name] = {
                        "file": m.file if hasattr(m, "file") else None,
                        "isReady": m.ready,
                        "controller": False,
                    }
        w.send({"List": rooms})

    # ---- 单客户端连接 ----
    def _client_loop(self, w):
        buf = b""
        try:
            while True:
                try:
                    data = w.conn.recv(65536)
                except socket.timeout:
                    if w.dead:
                        break
                    activity = (w.last_valid_state if w.name is not None
                                else w.connected_at)
                    if time.monotonic() - activity > PROTOCOL_TIMEOUT:
                        break
                    continue
                if not data:
                    break
                buf += data
                if len(buf) > MAX_PROTOCOL_MESSAGE and b"\n" not in buf:
                    raise ConnectionError("客户端消息超过 4 MiB 限制")
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if len(line) > MAX_PROTOCOL_MESSAGE:
                        raise ConnectionError("客户端消息超过 4 MiB 限制")
                    line = line.strip()
                    if line:
                        try:
                            self._handle(w, json.loads(line.decode("utf-8")))
                        except ValueError:
                            pass
                if len(buf) > MAX_PROTOCOL_MESSAGE:
                    raise ConnectionError("客户端消息超过 4 MiB 限制")
                activity = (w.last_valid_state if w.name is not None
                            else w.connected_at)
                if time.monotonic() - activity > PROTOCOL_TIMEOUT:
                    break
        except OSError:
            pass
        finally:
            self._drop(w)

    def _handle(self, w, msg):
        if not isinstance(msg, dict):
            w.send({"Error": {"message": "协议消息格式无效"}})
            return
        if "Hello" in msg:
            h = msg["Hello"]
            if w.name is not None:
                w.send({"Error": {"message": "同一连接不能重复登录"}})
                return
            if not isinstance(h, dict):
                w.send({"Error": {"message": "登录信息格式无效"}})
                return
            requested_name = self._clean_label(h.get("username"))
            if requested_name is None:
                requested_name = "匿名%d" % random.randrange(1000)
            room_info = h.get("room")
            requested_room = self._clean_label(
                room_info.get("name") if isinstance(room_info, dict) else None)
            if requested_room is None:
                requested_room = "默认房间"
            initial_token = time.time()
            with self.lock:
                initial_playstate = self._room_playstate_locked(
                    requested_room, initial_token
                )
                members = self.rooms.setdefault(requested_room, [])
                used_names = {member.name.casefold() for member in members
                              if isinstance(member.name, str) and member.name}
                assigned_name = requested_name
                suffix = 2
                while assigned_name.casefold() in used_names:
                    marker = " (%d)" % suffix
                    assigned_name = requested_name[:max(1, 128 - len(marker))] + marker
                    suffix += 1
                w.name = assigned_name
                w.room = requested_room
                w.last_valid_state = time.monotonic()
                if initial_playstate is not None:
                    w.initial_sync_token = initial_token
                    w.paused = initial_playstate["paused"]
                members.append(w)
            w.send({"Hello": {"username": w.name, "room": {"name": w.room},
                              "version": PROTOCOL_VERSION,
                              "motd": "mpv 精简同步服务器（无聊天）",
                              "features": {"persistentRooms": False, "chat": False,
                                           "managedRooms": False, "featureList": True,
                                           "readiness": True, "isolateRooms": True,
                                           "maxUsernameLength": MAX_IDENTITY_LENGTH,
                                           "maxRoomNameLength": MAX_IDENTITY_LENGTH,
                                           "maxFilenameLength": MAX_FILENAME_LENGTH}}})
            log("%s 从 %s 加入了房间「%s」" % (w.name, w.addr[0], w.room))
            self._broadcast_user_event(w.room, {"event": {"joined": True}}, w.name, exclude=w.name)
            self._send_list(w)
            if initial_playstate is not None:
                initial_playstate = dict(initial_playstate)
                initial_playstate["doSeek"] = True
                w.send({"State": {"playstate": initial_playstate,
                                  "ping": {"latencyCalculation": initial_token}}})
        elif "Set" in msg:
            if w.name is None:
                w.send({"Error": {"message": "请先登录"}})
                return
            self._handle_set(w, msg["Set"])
        elif "State" in msg:
            if w.name is None:
                w.send({"Error": {"message": "请先登录"}})
                return
            self._handle_state(w, msg["State"])
        elif "List" in msg:
            if w.name is None:
                w.send({"Error": {"message": "请先登录"}})
                return
            self._send_list(w)
        elif "Chat" in msg:
            pass                                    # 无聊天功能

    def _handle_set(self, w, setting):
        if not isinstance(setting, dict):
            return
        for cmd, val in (setting or {}).items():
            if cmd == "room":
                old = w.room
                raw_room = val.get("name") if isinstance(val, dict) else None
                if raw_room is None:
                    continue
                new = self._clean_label(raw_room)
                if new is None:
                    w.send({"Error": {"message": "房间名格式无效"}})
                    continue
                if new != old:
                    conflict = False
                    initial_token = time.time()
                    initial_playstate = None
                    with self.lock:
                        destination = self.rooms.setdefault(new, [])
                        conflict = any(
                            member is not w and isinstance(member.name, str) and
                            isinstance(w.name, str) and
                            member.name.casefold() == w.name.casefold()
                            for member in destination)
                        if not conflict:
                            previous = self.rooms.get(old, [])
                            if w in previous:
                                previous.remove(w)
                            destination.append(w)
                            w.room = new
                            w.pos = None
                            w.client_ts = None
                            initial_playstate = self._room_playstate_locked(
                                new, initial_token
                            )
                            w.initial_sync_token = (initial_token
                                                    if initial_playstate is not None
                                                    else None)
                            if initial_playstate is not None:
                                w.paused = initial_playstate["paused"]
                            self._clean_empty_room(old)
                    if conflict:
                        w.send({"Error": {"message":
                                "目标房间已有同名用户，请重新连接并使用其他昵称"}})
                        continue
                    self._broadcast_user_event(old, {"event": {"left": True}}, w.name)
                    self._broadcast_user_event(new, {"event": {"joined": True}}, w.name, exclude=w.name)
                    self._send_list(w)
                    if initial_playstate is not None:
                        initial_playstate = dict(initial_playstate)
                        initial_playstate["doSeek"] = True
                        w.send({"State": {"playstate": initial_playstate,
                                          "ping": {"latencyCalculation":
                                                   initial_token}}})
                    log("%s 移动到房间「%s」" % (w.name, new))
            elif cmd == "file":
                cleaned = self._clean_file(val)
                if cleaned is self._INVALID_FILE:
                    w.send({"Error": {"message": "文件信息格式无效"}})
                    continue
                w.file = cleaned
                # 只转发官方字段及经过验证的媒体扩展，避免一个畸形客户端
                # 把缺字段对象或私有字段传播给整个房间。
                update = {"room": {"name": w.room}, "file": cleaned}
                self._broadcast_user_event(w.room, update, w.name)
            elif cmd == "ready":
                if not isinstance(val, dict):
                    continue
                ready_value = val.get("isReady")
                if not isinstance(ready_value, bool):
                    continue
                w.ready = ready_value
                ready = {"username": w.name, "isReady": w.ready,
                         "manuallyInitiated": bool(val.get("manuallyInitiated", True))}
                with self.lock:
                    watchers = list(self.rooms.get(w.room, []))
                for other in watchers:
                    if other.name != w.name:
                        other.send({"Set": {"ready": ready}})

    def _handle_state(self, w, state):
        if not isinstance(state, dict):
            return
        ignore = state.get("ignoringOnTheFly")
        ignore = ignore if isinstance(ignore, dict) else {}
        generation = ignore.get("client")
        if (isinstance(generation, int) and not isinstance(generation, bool) and
                0 <= generation <= 1000000000):
            w._ack_ignore = generation              # 下次广播时回执给客户端
        raw_ping = state.get("ping")
        ping = raw_ping if isinstance(raw_ping, dict) else {}
        latency_timestamp = ping.get("latencyCalculation")
        playstate = state.get("playstate")
        if isinstance(playstate, dict):             # 报告本身始终处理（含 doSeek）
            position = playstate.get("position")
            paused = playstate.get("paused")
            if not self._finite_number(position) or not isinstance(paused, bool):
                playstate = None

        challenge = None
        challenge_token = None
        if playstate:
            now = time.time()
            with self.lock:
                room = w.room or ""
                canonical = self._room_playstate_locked(room, now)
                token = w.initial_sync_token
                accept_playstate = False
                if token is not None:
                    acknowledged = (self._finite_number(latency_timestamp) and
                                    float(latency_timestamp) == float(token))
                    if canonical is None:
                        accept_playstate = acknowledged
                    elif acknowledged:
                        accept_playstate = (
                            playstate["paused"] == canonical["paused"] and
                            abs(float(playstate["position"]) -
                                float(canonical["position"])) <= SEEK_DETECT_THRESHOLD
                        )
                    if accept_playstate:
                        w.initial_sync_token = None
                    else:
                        challenge_token = now
                        w.initial_sync_token = challenge_token
                        w.paused = (canonical or playstate)["paused"]
                        challenge = dict(canonical) if canonical else None
                elif w.pos is None and canonical is not None:
                    # Two clients can join an empty room concurrently.  The
                    # first report seeds it; later first reports synchronize
                    # before participating in the room's minimum position.
                    challenge_token = now
                    w.initial_sync_token = challenge_token
                    w.paused = canonical["paused"]
                    challenge = dict(canonical)
                else:
                    accept_playstate = True

                if accept_playstate:
                    old_pos = w.pos
                    old_paused = w.paused
                    w.pos = float(playstate["position"])
                    w.paused = playstate["paused"]
                    w.pos_ts = now
                else:
                    old_pos = None
                    old_paused = w.paused
                pause_changed = (room not in self.room_paused or
                                 (old_pos is not None and old_paused != w.paused))
                if accept_playstate and pause_changed:
                    self.room_paused[room] = w.paused
                    self.room_actor[room] = w.name
                if accept_playstate and playstate.get("doSeek") is True:
                    # 房间位置采纳跳转目标，并在保护窗口内以它为准前进
                    self.room_seek[room] = {"position": float(w.pos or 0), "set_by": w.name,
                                            "ts": now,
                                            "until": now + 2.0, "flagged": True}
                    self.room_paused[room] = w.paused
                    self.room_actor[room] = w.name
        if challenge is not None:
            challenge["doSeek"] = True
            w.send({"State": {"playstate": challenge,
                              "ping": {"latencyCalculation": challenge_token}}})
        if self._finite_number(latency_timestamp):
            w.rtt = max(0.0, time.time() - float(latency_timestamp))
        client_timestamp = ping.get("clientLatencyCalculation")
        if self._finite_number(client_timestamp):
            w.client_ts = float(client_timestamp)
        if isinstance(raw_ping, dict) or playstate:
            w.last_valid_state = time.monotonic()

    def _drop(self, w):
        with self.lock:
            room = self.rooms.get(w.room or "", [])
            if w in room:
                room.remove(w)
            self._clean_empty_room(w.room or "")
        if w.name:
            log("%s 断开了" % w.name)
            self._broadcast_user_event(w.room or "", {"event": {"left": True}}, w.name)
        try:
            w.conn.close()
        except OSError:
            pass


# ======================================================================
def main():
    ap = argparse.ArgumentParser(description="mpv 精简版 Syncplay（无聊天）")
    ap.add_argument("--server", default="syncplay.pl:8995", help="服务器 地址:端口（默认 syncplay.pl:8995）")
    ap.add_argument("--room", default=None, help="房间名")
    ap.add_argument("--name", default=None, help="昵称")
    ap.add_argument("--pipe", default="\\\\.\\pipe\\mpvpipe", help="mpv IPC 管道名")
    ap.add_argument("--rewind", type=float, default=REWIND_THRESHOLD, help="超前多少秒回退（默认 4）")
    ap.add_argument("--host", action="store_true", help="以精简中继服务器模式运行（--port 指定端口）")
    ap.add_argument("--port", type=int, default=8995, help="--host 模式监听端口")
    ap.add_argument("--virtual", action="store_true", help="使用虚拟播放器（测试用，stdin 可控制）")
    ap.add_argument("--verbose", action="store_true", help="输出收到的原始协议报文（调试用）")
    ap.add_argument("--control-file", "--ui-command", dest="control_file",
                    default=DEFAULT_COMMAND_FILE,
                    help="控制面板 JSON 文件（默认脚本目录 syncplay_command.json）")
    ap.add_argument("--status-file", "--ui-state", dest="status_file",
                    default=DEFAULT_STATUS_FILE,
                    help="状态面板 JSON 文件（默认脚本目录 syncplay_status.json）")
    ap.add_argument("--control-interval", type=float, default=0.5,
                    help="控制/状态文件轮询秒数（默认 0.5）")
    ap.add_argument("--alist-enabled", action="store_true",
                    help="启用 AList 媒体 URL 发布与自动加载（默认关闭）")
    ap.add_argument("--alist-server", default=None,
                    help="观看者可直接访问的 AList 公网地址")
    ap.add_argument("--alist-root", "--alist-local-root", dest="alist_root", default=None,
                    help="房主本地共享根目录（需与 AList Local 存储一致）")
    ap.add_argument("--alist-virtual-root", default="/media",
                    help="--alist-root 对应的 AList 挂载路径（默认 /media）")
    ap.add_argument("--alist-map", action="append", default=[], metavar="LOCAL=VIRTUAL",
                    help="增加本地目录到 AList 挂载路径的映射，可重复指定")
    ap.add_argument("--media-ready-timeout", type=float, default=30.0,
                    help="等待观看者加载媒体的最长秒数，0 表示严格等待（默认 30）")
    ap.add_argument("--http-readahead", type=float, default=20.0,
                    help="HTTP 媒体预读秒数（默认 20）")
    ap.add_argument("--alist-test-url", default=None, metavar="MEDIA_URL",
                    help="检测 AList 直链及 HTTP Range 后退出")
    args = ap.parse_args()

    if args.alist_test_url:
        media_test = probe_media_url(args.alist_test_url)
        print_report(media_test)
        raise SystemExit(0 if media_test.ok else 1)

    if args.host:
        MiniServer(args.port).start()
        return

    if not args.room:
        args.room = input("请输入房间名：").strip() or "mpv"
    if not args.name:
        args.name = ("MPV用户%d" % random.randrange(100, 999))

    mappings = list(args.alist_map or [])
    if args.alist_root:
        mappings.append((args.alist_root, args.alist_virtual_root))
    try:
        media_provider = MediaProvider(args.alist_server, mappings,
                                       enabled=args.alist_enabled)
    except ValueError as exc:
        ap.error(str(exc))

    if args.alist_enabled:
        if media_provider.enabled:
            log("AList 媒体共享已启用：%s" % args.alist_server)
        elif args.alist_server:
            log("AList 自动加载已启用；未配置发布映射，本机仅作为观看者")
        else:
            log("AList 已开启但缺少服务器地址，将保持本地媒体行为")

    player = VirtualPlayer() if args.virtual else MpvPlayer(args.pipe)
    client = SyncClient(args, player, media_provider=media_provider)
    watcher = threading.Thread(target=client._watcher_loop, daemon=True)
    watcher.start()
    bridge = FileControlBridge(client, args.control_file, args.status_file,
                               args.control_interval)
    bridge.start()
    try:
        client.run()
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
        if isinstance(player, MpvPlayer) and client.speed_changed:
            player.set_speed(player.base_speed)    # 退出前恢复用户原本的播放速度
            with client._lock:
                client.speed_changed = False
        bridge.stop()
        log("已退出同步")


if __name__ == "__main__":
    main()
