#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""log_redact.py —— 客户端日志脱敏（纯标准库，独立模块）。

背景：mpv 会记录命令行与所播放的媒体 URL。若 URL 带签名查询参数
（如 ``?token=`` / ``sign=``），凭据就会随日志落盘。协作红线要求日志
不得包含 AList 管理员 Token、Cookie、Tailscale 凭据，因此所有落盘内容
必须先经过本模块打码。

公开接口：
  redact(text) -> str       打码查询串中凭据参数的值（保留参数名）
  redact_file(path)         对单个日志文件原地脱敏
  redact_directory(dir)     对目录下 *.log / *.log.N 原地脱敏

命令行（面板「打开日志文件夹」会在打开前调用一次，把当前会话文件
尚未脱敏的缺口关掉）：
  python log_redact.py --dir <日志目录>
"""

import argparse
import os
import re
import sys

# 凭据类查询参数：参数名保留，仅把值替换为 ***。
# 命中条件：参数名中含下述关键整词（允许 access_token、X-Sign、
# api-key 等写法），且位于查询串里。普通参数（如 name=）不受影响。
_SECRET_PARAM_RE = re.compile(
    r"(?i)([?&;][^=&\s]*?"
    r"(?<![A-Za-z0-9])(?:token|sign|password|passwd|pwd|secret|auth|"
    r"credential|key|cookie|authorization)(?![A-Za-z0-9])"
    r"[^=&\s]*?=)"
    r"([^&\s\"']*)"
)

# Cookie / Authorization 请求头形式（"Cookie: xxx"）：冒号后的整段值打码，
# 避免多段 Cookie 值（a=1; b=2）只打码第一段的漏洞。
_SECRET_HEADER_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])((?:proxy-)?(?:cookie|authorization))(\s*:\s*)([^\r\n]*)"
)

# 视为日志文件的命名：*.log 及其轮转归档 *.log.1 / *.log.2 ...
_LOG_FILE_NAME_RE = re.compile(r"(?i)^.*\.log(\.\d+)?$")

MASK = "***"


def redact(text):
    """把文本中凭据类查询参数与 Cookie/Authorization 头的值打码。

    参数名保留（排查时仍能看出是哪个参数），值统一替换为 ``***``。
    """
    if not isinstance(text, str):
        text = str(text)
    text = _SECRET_PARAM_RE.sub(lambda m: m.group(1) + MASK, text)
    text = _SECRET_HEADER_RE.sub(lambda m: m.group(1) + m.group(2) + MASK, text)
    return text


def redact_file(path, encoding="utf-8"):
    """对单个日志文件原地脱敏。

    返回 (文件名, 是否有改动, 说明)。读取或写回失败不抛异常，
    只在说明里给出原因——脱敏失败不能阻塞「打开日志文件夹」。
    """
    name = os.path.basename(path)
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        return name, False, "读取失败：%s" % exc
    try:
        text = raw.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        text = raw.decode("utf-8", "replace")
    cleaned = redact(text)
    if cleaned == text:
        return name, False, "无需脱敏"
    try:
        # newline="" 保持原文件换行风格，避免 CRLF 被二次改写。
        with open(path, "w", encoding=encoding, newline="") as fh:
            fh.write(cleaned)
    except OSError as exc:
        return name, False, "写回失败：%s" % exc
    return name, True, "已脱敏"


def redact_directory(dir_path):
    """对目录下已有日志文件（*.log 与 *.log.N）原地脱敏。

    返回 [(文件名, 是否有改动, 说明), ...]。
    """
    try:
        entries = sorted(os.listdir(dir_path))
    except OSError as exc:
        return [(os.path.basename(dir_path), False, "目录不可读：%s" % exc)]
    results = []
    for entry in entries:
        if not _LOG_FILE_NAME_RE.match(entry):
            continue
        results.append(redact_file(os.path.join(dir_path, entry)))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="对目录下已有日志文件原地脱敏（打码 URL 中的凭据参数）")
    parser.add_argument("--dir", required=True,
                        help="日志目录（处理其中的 *.log 与 *.log.N）")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.dir):
        print("目录不存在：%s" % args.dir)
        return 1
    results = redact_directory(args.dir)
    if not results:
        print("目录下没有日志文件（*.log / *.log.N）：%s" % args.dir)
        return 0
    for name, _changed, detail in results:
        print("%s：%s" % (name, detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
