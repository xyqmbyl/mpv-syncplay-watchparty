#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""log_maintenance.py —— WatchParty 日志目录维护助手（纯标准库）。

背景：mpv 便携模式不会自动创建日志父目录（日志路径不存在时只在 stderr
报一句 Failed to open log file 后静默失败），而 mpv 的 Lua 侧没有
utils.mkdir。因此「建目录 / 清理过期日志 / 看目录概况」统一交给本助手，
由 Lua 侧（contextmenu_plus.lua）与启动器脚本调用。

CLI 契约（已冻结，Lua 侧按此调用，不要改参数名）：
  log_maintenance.py ensure-dir --dir <logs>
      建目录（含父目录）；已存在也返回 0。
  log_maintenance.py clean --dir <logs> --days N [--json]
      删除该目录下修改时间早于 N 天的日志文件。
  log_maintenance.py status --dir <logs> [--json]
      输出目录概况（文件数、总字节、最老/最新时间）。

约定：
  * --json 时 stdout 恰好一行 JSON（便于 Lua 解析），字段至少含
    ok / removed / freed_bytes / dir；非 json 模式输出给人读的中文摘要。
  * 年龄判定用「修改时间」，边界严格大于（恰好 N 天不算过期）。
  * clean 只删匹配日志命名的文件（mpv-*.log、client-*.log、launcher.log
    及其 .1/.2/.3 轮转后缀），其它文件与 .last-clean 这类标记文件一律不动；
    删除前校验目标确实位于 --dir 之内（防路径逃逸）。

红线：日志只是辅助能力。任何失败都只返回非 0 与 ok=false + error 文案，
绝不抛未捕获异常，绝不能影响播放/同步。
"""

import argparse
import datetime
import json
import math
import os
import re
import sys
import time

__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "LOG_FILE_NAME_RE",
    "SECONDS_PER_DAY",
    "build_parser",
    "clean_directory",
    "directory_status",
    "ensure_dir",
    "format_bytes",
    "human_summary",
    "is_expired",
    "is_log_file_name",
    "is_protected_name",
    "main",
    "parse_days",
    "parse_now",
    "resolve_inside",
]

# 默认保留一个月（右键菜单「清理周期」默认 30 天，可用 --days 覆盖）。
DEFAULT_RETENTION_DAYS = 30
SECONDS_PER_DAY = 24 * 60 * 60

# 只清理这些命名（含 .1/.2/.3 轮转归档）：
#   mpv-YYYYMMDD-HHMMSS.log[.N]    mpv 本体日志（一次会话一个文件）
#   client-YYYYMMDD-HHMMSS.log[.N] 联机客户端日志（--log-file）
#   launcher.log[.N]               启动器日志（每次启动追加）
# 其余命名（如 alist 的 server-stdout.log、任意用户文件名）一律不动：
# 宁可不删也不能误删。
LOG_FILE_NAME_RE = re.compile(r"(?i)^(?:mpv-.*|client-.*|launcher)\.log(?:\.[1-3])?$")

# 显式保护名单（小写比较）。.last-clean 是 Lua 侧记录「上次自动清理日期」
# 的标记文件，清理时必须保留。
PROTECTED_NAMES = (".last-clean",)


def _write_stdout(text):
    """把一行文本写到 stdout，保证在任何控制台编码下都不报 UnicodeEncodeError。

    Windows 控制台常见 cp936，无法直接输出箭头/表情（对齐 mpv_syncplay.log
    的降级方式）；拿不到 buffer（如测试里的 StringIO）时退化为转义输出。
    写不出去也不抛异常：日志能力的输出失败不应影响调用方。
    """
    line = text + "\n"
    try:
        sys.stdout.write(line)
        sys.stdout.flush()
        return
    except UnicodeEncodeError:
        pass
    except (OSError, ValueError):
        return
    try:
        stream = getattr(sys.stdout, "buffer", None)
        if stream is not None:
            stream.write(line.encode(sys.stdout.encoding or "utf-8", "replace"))
            stream.flush()
        else:
            sys.stdout.write(line.encode("ascii", "backslashreplace").decode("ascii"))
            sys.stdout.flush()
    except Exception:  # noqa: BLE001 - 输出失败绝不能让调用方失败
        return


def parse_now(value):
    """把 --now 注入点转成 epoch 秒；None 表示用真实当前时间。

    接受 epoch 秒（``1750000000`` / ``1750000000.5``）或 ISO 8601
    （``2026-10-23T02:53:52``、带时区或末尾 Z 亦可）。非法输入抛 ValueError。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        stamp = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            "时间注入点非法：%r（应为 epoch 秒或 ISO 8601 时间）" % (value,))
    # 无时区的 ISO 时间按本机时区解释，与用户直觉一致。
    return stamp.timestamp()


def parse_days(value):
    """把 --days 文本转成 >= 0 的天数；None 用默认值；非法输入抛 ValueError。

    非法值不交给 argparse 直接退出，是为了保证 --json 契约始终成立：
    即使参数写错，stdout 也仍是一行可解析的 JSON（ok=false + error）。
    """
    if value is None:
        return DEFAULT_RETENTION_DAYS
    text = str(value).strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        raise ValueError("清理周期非法：%r（应为 >= 0 的天数）" % (value,))
    if not math.isfinite(number) or number < 0:
        raise ValueError("清理周期非法：%r（应为 >= 0 的天数）" % (value,))
    return int(number) if number.is_integer() else number


def is_log_file_name(name):
    """判断文件名是否为受管日志（mpv-/client-/launcher.log 及其 .1-.3）。"""
    return bool(name) and bool(LOG_FILE_NAME_RE.match(name))


def is_protected_name(name):
    """标记文件（.last-clean 等隐藏文件）永不删除。"""
    if not name:
        return True
    if name.lower() in PROTECTED_NAMES:
        return True
    # 隐藏文件（.last-clean、.stfolder 之类）一律保护。
    return name.startswith(".")


def is_expired(mtime, days, now, seconds_per_day=SECONDS_PER_DAY):
    """mtime 是否早于 now 之前 days 天。

    边界严格大于：恰好 days 天（差值为 0）不算过期，必须再多一点才删。
    """
    try:
        age = float(now) - float(mtime)
        limit = float(days) * float(seconds_per_day)
    except (TypeError, ValueError):
        return False
    return age > limit


def resolve_inside(directory, name):
    """返回 directory 内名为 name 的目标路径；越出目录或名字可疑时返回 None。

    防路径逃逸：拒绝带分隔符/空字节的名字，并要求 realpath 之后的父目录
    正好是 --dir（因此指向目录外的符号链接也会被拒绝）。
    """
    if not isinstance(name, str) or not name or name in (os.curdir, os.pardir):
        return None
    if "\0" in name or os.sep in name:
        return None
    if os.altsep and os.altsep in name:
        return None
    target = os.path.join(directory, name)
    try:
        base = os.path.realpath(directory)
        real = os.path.realpath(target)
    except (OSError, ValueError):
        return None
    if os.path.normcase(os.path.dirname(real)) != os.path.normcase(base):
        return None
    return target


def ensure_dir(directory):
    """确保日志目录存在（含父目录）；已存在也成功。返回结果 dict。"""
    result = {
        "ok": False,
        "command": "ensure-dir",
        "dir": directory,
        "created": False,
        "error": None,
    }
    if not isinstance(directory, str) or not directory.strip():
        result["error"] = "日志目录参数为空"
        return result
    try:
        existed = os.path.isdir(directory)
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        result["error"] = "创建日志目录失败：%s" % exc
        return result
    result["created"] = not existed
    result["ok"] = True
    return result


def clean_directory(directory, days=DEFAULT_RETENTION_DAYS, now=None):
    """删除 directory 下修改时间早于 days 天的受管日志文件。返回结果 dict。

    任何异常都在函数内转成 ok=false + error，不向上抛。
    """
    result = {
        "ok": False,
        "command": "clean",
        "dir": directory,
        "days": days,
        "now": None,
        "removed": 0,
        "freed_bytes": 0,
        "removed_files": [],
        "kept": 0,
        "skipped": 0,
        "failed": [],
        "error": None,
    }
    if isinstance(days, bool) or not isinstance(days, (int, float)) or days < 0:
        result["error"] = "清理周期非法：%r（应为 >= 0 的天数）" % (days,)
        return result
    if not isinstance(directory, str) or not directory.strip():
        result["error"] = "日志目录参数为空"
        return result
    if not os.path.isdir(directory):
        result["error"] = "日志目录不存在：%s" % directory
        return result
    if now is None:
        try:
            now = time.time()
        except OSError:  # 极端情况下的兜底
            now = 0.0
    result["now"] = now
    try:
        entries = sorted(os.listdir(directory))
    except OSError as exc:
        result["error"] = "日志目录不可读：%s" % exc
        return result

    for name in entries:
        if is_protected_name(name) or not is_log_file_name(name):
            continue
        target = os.path.join(directory, name)
        if os.path.islink(target):
            # 符号链接不删（删链接会让排查时莫名少文件，也避免误判目标）。
            result["skipped"] += 1
            continue
        try:
            if not os.path.isfile(target):
                continue  # 目录等同名条目不动
        except OSError:
            continue
        safe = resolve_inside(directory, name)
        if safe is None:
            result["failed"].append({"name": name, "error": "目标越出日志目录，已跳过"})
            continue
        try:
            stat = os.stat(safe)
        except OSError as exc:
            result["failed"].append({"name": name, "error": "读取属性失败：%s" % exc})
            continue
        if not is_expired(stat.st_mtime, days, now):
            result["kept"] += 1
            continue
        try:
            os.remove(safe)
        except OSError as exc:
            # 文件被占用（WinError 32）、权限不足（WinError 5）等：记下来继续处理下一个。
            result["failed"].append({"name": name, "error": "删除失败：%s" % exc})
            continue
        result["removed"] += 1
        result["freed_bytes"] += stat.st_size
        result["removed_files"].append(name)

    if result["failed"]:
        names = "，".join(item["name"] for item in result["failed"][:3])
        result["error"] = "%d 个日志文件删除失败（可能被占用）：%s" % (
            len(result["failed"]), names)
    else:
        result["ok"] = True
    return result


def directory_status(directory):
    """统计目录概况：文件数、总字节、最老/最新时间。返回结果 dict。"""
    result = {
        "ok": False,
        "command": "status",
        "dir": directory,
        "files": 0,
        "total_bytes": 0,
        "log_files": 0,
        "log_bytes": 0,
        "oldest": None,
        "oldest_mtime": None,
        "newest": None,
        "newest_mtime": None,
        "error": None,
    }
    if not isinstance(directory, str) or not directory.strip():
        result["error"] = "日志目录参数为空"
        return result
    if not os.path.isdir(directory):
        result["error"] = "日志目录不存在：%s" % directory
        return result
    try:
        entries = sorted(os.listdir(directory))
    except OSError as exc:
        result["error"] = "日志目录不可读：%s" % exc
        return result

    oldest = None
    newest = None
    for name in entries:
        target = os.path.join(directory, name)
        try:
            if not os.path.isfile(target):
                continue
            stat = os.stat(target)
        except OSError:
            continue
        result["files"] += 1
        result["total_bytes"] += stat.st_size
        if is_log_file_name(name) and not is_protected_name(name):
            result["log_files"] += 1
            result["log_bytes"] += stat.st_size
        if oldest is None or stat.st_mtime < oldest:
            oldest = stat.st_mtime
        if newest is None or stat.st_mtime > newest:
            newest = stat.st_mtime

    result["oldest_mtime"] = oldest
    result["newest_mtime"] = newest
    result["oldest"] = _format_time(oldest)
    result["newest"] = _format_time(newest)
    result["ok"] = True
    return result


def _format_time(stamp):
    """把 epoch 秒转成本地时间字符串；None 原样返回。"""
    if stamp is None:
        return None
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(stamp)))
    except (OSError, OverflowError, ValueError):
        return None


def format_bytes(size):
    """人类可读的字节数（B / KB / MB / GB）。"""
    try:
        value = float(size)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return "%d B" % int(value)
            return "%.1f %s" % (value, unit)
        value /= 1024.0
    return "%d B" % int(size)


def human_summary(payload):
    """把结果 dict 转成给人读的中文摘要（非 --json 模式使用）。"""
    if not isinstance(payload, dict):
        return str(payload)
    command = payload.get("command")
    directory = payload.get("dir") or "(未指定)"
    error = payload.get("error")
    lines = []
    if command == "ensure-dir":
        if payload.get("ok"):
            lines.append("日志目录已就绪：%s%s"
                         % (directory, "（本次新建）" if payload.get("created") else ""))
        else:
            lines.append("日志目录创建失败：%s" % directory)
    elif command == "clean":
        if payload.get("ok"):
            lines.append(
                "清理完成：删除 %s 个日志文件，释放 %s；保留 %s 个（周期 %s 天）"
                % (payload.get("removed", 0),
                   format_bytes(payload.get("freed_bytes", 0)),
                   payload.get("kept", 0),
                   payload.get("days")))
            if payload.get("skipped"):
                lines.append("另有 %s 个条目被跳过（符号链接等）" % payload["skipped"])
        else:
            lines.append("清理未完成：%s" % directory)
            if payload.get("removed"):
                lines.append("已删除 %s 个日志文件，释放 %s"
                             % (payload["removed"],
                                format_bytes(payload.get("freed_bytes", 0))))
    elif command == "status":
        if payload.get("ok"):
            lines.append("日志目录：%s" % directory)
            lines.append("日志文件：%s 个 / %s（含其它文件共 %s 个 / %s）"
                         % (payload.get("log_files", 0),
                            format_bytes(payload.get("log_bytes", 0)),
                            payload.get("files", 0),
                            format_bytes(payload.get("total_bytes", 0))))
            lines.append("最老：%s" % (payload.get("oldest") or "无"))
            lines.append("最新：%s" % (payload.get("newest") or "无"))
        else:
            lines.append("无法读取日志目录：%s" % directory)
    else:
        lines.append("完成：%s" % directory)
    if error:
        lines.append("错误：%s" % error)
    return "\n".join(lines)


def _emit(payload, as_json):
    """按契约输出：--json 一行 JSON（ASCII 保证任何控制台编码都能解析）；否则中文摘要。"""
    if as_json:
        try:
            text = json.dumps(payload, ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError):
            text = json.dumps({"ok": False, "error": "结果序列化失败"},
                              ensure_ascii=True)
        _write_stdout(text)
        return
    _write_stdout(human_summary(payload))


def build_parser():
    """构造 CLI 解析器（子命令与参数名已冻结，不要改名）。"""
    parser = argparse.ArgumentParser(
        prog="log_maintenance.py",
        description="WatchParty 日志目录维护助手：建目录 / 清理过期日志 / 目录概况"
                    "（纯标准库，失败只返回非 0，不影响播放与同步）")
    subparsers = parser.add_subparsers(dest="command", metavar="命令")
    subparsers.required = True

    ensure = subparsers.add_parser(
        "ensure-dir", help="确保日志目录存在（含父目录），已存在也返回 0")
    ensure.add_argument("--dir", required=True, help="日志目录（便携根下的 logs）")
    ensure.add_argument("--json", action="store_true", help="以单行 JSON 输出结果")

    clean = subparsers.add_parser(
        "clean", help="删除修改时间早于 N 天的日志文件")
    clean.add_argument("--dir", required=True, help="日志目录（便携根下的 logs）")
    clean.add_argument("--days", default=None,
                       help="保留天数，早于此天数的日志被删除（默认 %d）"
                            % DEFAULT_RETENTION_DAYS)
    clean.add_argument("--json", action="store_true", help="以单行 JSON 输出结果")
    clean.add_argument("--now", default=None,
                       help=argparse.SUPPRESS)  # 时间注入点，仅供单测/排查

    status = subparsers.add_parser(
        "status", help="输出目录概况（文件数、总字节、最老/最新时间）")
    status.add_argument("--dir", required=True, help="日志目录（便携根下的 logs）")
    status.add_argument("--json", action="store_true", help="以单行 JSON 输出结果")

    return parser


def main(argv=None):
    """CLI 入口：返回进程退出码（0 成功；非 0 且 JSON 里 ok=false）。"""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code in (0, None):
            raise  # --help / --version：保留 argparse 的正常退出语义
        return code if isinstance(code, int) else 2
    as_json = bool(getattr(args, "json", False))
    try:
        if args.command == "clean":
            days = parse_days(args.days)
            now = parse_now(args.now)
            payload = clean_directory(args.dir, days, now=now)
        elif args.command == "ensure-dir":
            payload = ensure_dir(args.dir)
        else:
            payload = directory_status(args.dir)
    except ValueError as exc:
        payload = {"ok": False, "command": args.command, "dir": args.dir,
                   "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 红线：任何意外都不得抛出
        payload = {"ok": False, "command": args.command, "dir": args.dir,
                   "error": "未预期的内部错误：%s" % exc}
    _emit(payload, as_json)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
