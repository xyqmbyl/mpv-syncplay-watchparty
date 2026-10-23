#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mpv_syncplay 客户端日志能力单测：--log-file 落盘 / 轮转 / 脱敏 / 异常安全。

跑法（仓库根目录）：
  python -m unittest discover -s portable_config/syncplay -p "test_*.py" -v
"""

import contextlib
import io
import os
import shutil
import tempfile
import unittest
from unittest import mock

try:
    from . import log_redact
    from . import mpv_syncplay
except ImportError:  # 允许直接运行本文件 / unittest discover 顶层导入
    import log_redact
    import mpv_syncplay


class _LogTestBase(unittest.TestCase):
    """公共脚手架：临时目录 + 测试后恢复全局日志状态。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="watchparty-log-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # 无论测试是否通过，都关闭落盘，避免污染其他用例。
        self.addCleanup(mpv_syncplay.set_log_file, None)

    def _capture_log(self, msg):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mpv_syncplay.log(msg)
        return buf.getvalue()

    def _read(self, path):
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8")

    def _write(self, name, text):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path


class LogFileWriteTests(_LogTestBase):
    """需求 3.5-1：给了 --log-file 会写文件，且 stdout 行为不变。"""

    def test_default_is_disabled(self):
        self.assertIsNone(mpv_syncplay._log_file_path)

    def test_log_file_receives_lines_and_stdout_unchanged(self):
        path = os.path.join(self.tmp, "syncplay.log")
        mpv_syncplay.set_log_file(path)
        out = self._capture_log("你好 ?token=SECRET1")
        # stdout 保持旧行为：原样输出、不做脱敏。
        self.assertIn("你好 ?token=SECRET1", out)
        content = self._read(path)
        self.assertIn("你好", content)
        self.assertIn("token=***", content)
        # 红线：凭据不落盘。
        self.assertNotIn("SECRET1", content)

    def test_unicode_written_as_utf8(self):
        path = os.path.join(self.tmp, "syncplay.log")
        mpv_syncplay.set_log_file(path)
        self._capture_log("▶ 暂停 ⇄ 同步")
        self.assertIn("▶ 暂停", self._read(path))

    def test_append_not_truncate(self):
        path = os.path.join(self.tmp, "syncplay.log")
        mpv_syncplay.set_log_file(path)
        self._capture_log("line-one")
        self._capture_log("line-two")
        content = self._read(path)
        self.assertIn("line-one", content)
        self.assertIn("line-two", content)

    def test_disable_stops_writing(self):
        path = os.path.join(self.tmp, "syncplay.log")
        mpv_syncplay.set_log_file(path)
        self._capture_log("before")
        mpv_syncplay.set_log_file(None)
        self._capture_log("after")
        self.assertNotIn("after", self._read(path))


class LogRotationTests(_LogTestBase):
    """需求 3.5-2：超上限归档、份数封顶、最老的被丢弃。"""

    def test_rotation_archives_caps_count_and_drops_oldest(self):
        path = os.path.join(self.tmp, "syncplay.log")
        mpv_syncplay.set_log_file(path)
        with mock.patch.object(mpv_syncplay, "LOG_MAX_SIZE", 200):
            for index in range(60):
                self._capture_log("mark-%03d %s" % (index, "x" * 20))
        names = set(os.listdir(self.tmp))
        archives = sorted(n for n in names if n.startswith("syncplay.log."))
        self.assertGreaterEqual(len(archives), 1)
        self.assertLessEqual(len(archives), mpv_syncplay.LOG_BACKUP_COUNT)
        self.assertIn("syncplay.log.1", names)
        # 最早的行在最老的归档被丢弃后应彻底消失。
        for name in sorted(n for n in names if n.startswith("syncplay.log")):
            self.assertNotIn("mark-000", self._read(os.path.join(self.tmp, name)))
        # 轮转后当前文件继续可写。
        self._capture_log("after-rotation")
        self.assertIn("after-rotation", self._read(path))

    def test_rotation_failure_keeps_logging(self):
        path = os.path.join(self.tmp, "syncplay.log")
        mpv_syncplay.set_log_file(path)
        self._capture_log("before")
        with mock.patch.object(mpv_syncplay.os, "replace",
                               side_effect=OSError("locked")), \
                mock.patch.object(mpv_syncplay, "LOG_MAX_SIZE", 10):
            self._capture_log("during")
        # 轮转失败被吞掉，日志仍写入当前文件。
        self.assertIn("during", self._read(path))


class RedactFunctionTests(unittest.TestCase):
    """需求 3.5-3：脱敏纯函数。"""

    def test_query_values_masked_and_names_kept(self):
        cases = {
            "http://h/d/a.mkv?token=T1&x=2": "http://h/d/a.mkv?token=***&x=2",
            "http://h/d/a.mkv?sign=S1": "http://h/d/a.mkv?sign=***",
            "http://h/d/a.mkv?password=P1": "http://h/d/a.mkv?password=***",
            "http://h/d/a.mkv?secret=S1": "http://h/d/a.mkv?secret=***",
            "http://h/d/a.mkv?access_token=A1": "http://h/d/a.mkv?access_token=***",
            "http://h/d/a.mkv?X-Sign=S1&keep=1": "http://h/d/a.mkv?X-Sign=***&keep=1",
            "Cookie: session=abc123; other=1": "Cookie: ***",
        }
        for src, want in cases.items():
            self.assertEqual(log_redact.redact(src), want, src)

    def test_case_insensitive(self):
        out = log_redact.redact("http://h/?Password=P1&TOKEN=T2")
        self.assertEqual(out, "http://h/?Password=***&TOKEN=***")

    def test_normal_params_untouched(self):
        text = "http://h/d/a.mkv?name=sign_language.txt&monkey=1"
        self.assertEqual(log_redact.redact(text), text)

    def test_credentials_never_survive(self):
        out = log_redact.redact("http://h/?token=TOKVALUE&sign=SIGNVALUE")
        self.assertNotIn("TOKVALUE", out)
        self.assertNotIn("SIGNVALUE", out)

    def test_plain_text_unchanged(self):
        plain = "普通日志：没有 URL，也没有凭据"
        self.assertEqual(log_redact.redact(plain), plain)


class RedactFileToolTests(_LogTestBase):
    """log_redact 的 --dir 落地能力：原地脱敏已有日志。"""

    def test_redact_directory_in_place(self):
        logs = os.path.join(self.tmp, "logs")
        os.makedirs(logs)
        p1 = self._write(os.path.join("logs", "session.log"),
                         "http://h/?password=PW1")
        p2 = self._write(os.path.join("logs", "session.log.1"),
                         "干净内容，无需改动")
        p3 = self._write(os.path.join("logs", "notes.txt"),
                         "http://h/?token=T9")
        results = {name: (changed, detail)
                   for name, changed, detail in log_redact.redact_directory(logs)}
        self.assertIn("session.log", results)
        self.assertIn("session.log.1", results)
        self.assertTrue(results["session.log"][0])
        self.assertFalse(results["session.log.1"][0])
        content1 = self._read(p1)
        self.assertIn("password=***", content1)
        self.assertNotIn("PW1", content1)
        self.assertEqual(self._read(p2), "干净内容，无需改动")
        # 非 *.log 命名不在处理范围，保持原样。
        self.assertEqual(self._read(p3), "http://h/?token=T9")

    def test_cli_main(self):
        logs = os.path.join(self.tmp, "logs")
        os.makedirs(logs)
        target = os.path.join(logs, "a.log")
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("http://h/?token=CLI1")
        self.assertEqual(log_redact.main(["--dir", logs]), 0)
        with open(target, "r", encoding="utf-8") as fh:
            self.assertNotIn("CLI1", fh.read())
        self.assertEqual(log_redact.main(["--dir",
                                          os.path.join(self.tmp, "missing")]), 1)

    def test_redact_file_missing_returns_reason(self):
        name, changed, detail = log_redact.redact_file(
            os.path.join(self.tmp, "nope.log"))
        self.assertFalse(changed)
        self.assertIn("读取失败", detail)


class LogFailureSafetyTests(_LogTestBase):
    """需求 3.5-4：文件写不进去时不抛异常、不影响后续调用。"""

    def test_directory_target_does_not_raise(self):
        # 路径是个目录 → open 必然失败，log() 必须吞掉 OSError。
        mpv_syncplay.set_log_file(self.tmp)
        try:
            self._capture_log("must not raise")
        except Exception as exc:  # noqa: BLE001 - 测试要证明“任何异常都不抛”
            self.fail("log() 在日志不可写时抛出了 %r" % exc)
        good = os.path.join(self.tmp, "ok.log")
        mpv_syncplay.set_log_file(good)
        self._capture_log("recovered")
        self.assertIn("recovered", self._read(good))

    def test_bad_encoding_content_still_lands(self):
        path = os.path.join(self.tmp, "syncplay.log")
        mpv_syncplay.set_log_file(path)
        # 含 GBK 无法表示的字符：文件侧按 UTF-8 errors=replace 写入。
        self._capture_log("▶\U0001f514ok")
        content = self._read(path)
        self.assertIn("▶", content)


if __name__ == "__main__":
    unittest.main()
