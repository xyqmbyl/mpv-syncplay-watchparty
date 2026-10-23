#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""log_maintenance 单测：命名匹配 / 过期边界 / 失败路径 / CLI 契约。

跑法（仓库根目录，必须用便携运行目录的解释器）：
  python -m unittest discover -s portable_config\\syncplay -p "test_*.py" -v

全部用例只用 tempfile 建临时目录，绝不触碰真实的
D:\\MPV-Duke-Enhanced-Extra\\logs。
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

try:
    from . import log_maintenance
except ImportError:  # 允许直接运行本文件 / discover 顶层导入
    import log_maintenance


DAY = 86400.0


class _LogMaintenanceTestBase(unittest.TestCase):
    """公共脚手架：临时目录（自动清理）。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="watchparty-logmaint-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make_logs_dir(self):
        """在临时目录下建 logs 子目录并返回其路径。"""
        path = os.path.join(self.tmp, "logs")
        os.makedirs(path, exist_ok=True)
        return path

    def make_file(self, name, content=b"x" * 10, age_days=None, now=None):
        """建一个文件；age_days 给出时把修改时间设为 now-ago。"""
        path = os.path.join(self.make_logs_dir(), name)
        with open(path, "wb") as handle:
            handle.write(content)
        if age_days is not None:
            stamp = (now if now is not None else time.time()) - age_days * DAY
            os.utime(path, (stamp, stamp))
        return path

    def run_cli(self, argv):
        """跑 CLI 并返回 (退出码, stdout 文本, stderr 文本)。"""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = log_maintenance.main(argv)
            except SystemExit as exc:  # argparse 的正常退出（--help 等）
                code = exc.code if isinstance(exc.code, int) else 0
        return code, out.getvalue(), err.getvalue()


class LogNameMatchingTests(unittest.TestCase):
    """clean 只认受管日志名，宁可不删也不能误删。"""

    def test_managed_names_match(self):
        for name in ("mpv-20261023-025352.log",
                     "mpv-20261023-025352.log.1",
                     "mpv-20261023-025352.log.3",
                     "client-20261023-025352.log",
                     "client-20261023-025352.log.2",
                     "launcher.log",
                     "launcher.log.1",
                     "MPV-20261023-025352.LOG"):
            self.assertTrue(log_maintenance.is_log_file_name(name), name)

    def test_other_names_do_not_match(self):
        for name in ("server-stdout.log", "alist.log", "notes.txt",
                     "mpv-20261023-025352.log.4",
                     "mpv-20261023-025352.log.bak",
                     ".last-clean", "danmaku-history.json",
                     "launcher.log.old", "connection.json", "mpv-log.txt"):
            self.assertFalse(log_maintenance.is_log_file_name(name), name)

    def test_protected_names(self):
        self.assertTrue(log_maintenance.is_protected_name(".last-clean"))
        self.assertTrue(log_maintenance.is_protected_name(""))
        self.assertFalse(log_maintenance.is_protected_name("launcher.log"))


class ExpiryBoundaryTests(unittest.TestCase):
    """年龄判定：修改时间早于 N 天才算过期，边界严格大于。"""

    def test_exactly_n_days_is_kept(self):
        now = 1_800_000_000.0
        self.assertFalse(log_maintenance.is_expired(now - 30 * DAY, 30, now))

    def test_just_over_n_days_is_expired(self):
        now = 1_800_000_000.0
        self.assertTrue(log_maintenance.is_expired(now - 30 * DAY - 1, 30, now))

    def test_just_under_n_days_is_kept(self):
        now = 1_800_000_000.0
        self.assertFalse(log_maintenance.is_expired(now - 30 * DAY + 1, 30, now))

    def test_zero_days_only_deletes_older_than_now(self):
        now = 1_800_000_000.0
        self.assertFalse(log_maintenance.is_expired(now, 0, now))
        self.assertTrue(log_maintenance.is_expired(now - 1, 0, now))

    def test_bad_input_is_not_expired(self):
        self.assertFalse(log_maintenance.is_expired(None, 30, 1_800_000_000.0))


class ParseArgumentTests(unittest.TestCase):
    """--days / --now 解析：非法输入抛 ValueError，由 main 转成 ok=false。"""

    def test_days_default_and_values(self):
        self.assertEqual(log_maintenance.parse_days(None),
                         log_maintenance.DEFAULT_RETENTION_DAYS)
        self.assertEqual(log_maintenance.parse_days("7"), 7)
        self.assertEqual(log_maintenance.parse_days("14"), 14)
        self.assertEqual(log_maintenance.parse_days("0"), 0)
        self.assertEqual(log_maintenance.parse_days("2.5"), 2.5)

    def test_days_invalid(self):
        for bad in ("abc", "-1", "", "nan", "inf", "-inf"):
            with self.assertRaises(ValueError, msg=bad):
                log_maintenance.parse_days(bad)

    def test_now_epoch_and_iso(self):
        self.assertEqual(log_maintenance.parse_now("1800000000"), 1_800_000_000.0)
        self.assertEqual(log_maintenance.parse_now(None), None)
        self.assertEqual(log_maintenance.parse_now(""), None)
        stamp = log_maintenance.parse_now("2026-10-23T02:53:52")
        self.assertAlmostEqual(
            stamp,
            time.mktime(time.strptime("2026-10-23 02:53:52", "%Y-%m-%d %H:%M:%S")),
            places=0)

    def test_now_invalid(self):
        with self.assertRaises(ValueError):
            log_maintenance.parse_now("昨天")


class EnsureDirTests(_LogMaintenanceTestBase):
    """ensure-dir：建目录（含父目录）、幂等、失败返回 ok=false。"""

    def test_creates_nested_and_reports_created(self):
        target = os.path.join(self.tmp, "portable", "logs")
        result = log_maintenance.ensure_dir(target)
        self.assertTrue(result["ok"])
        self.assertTrue(result["created"])
        self.assertTrue(os.path.isdir(target))

    def test_idempotent_second_call(self):
        target = os.path.join(self.tmp, "logs")
        first = log_maintenance.ensure_dir(target)
        second = log_maintenance.ensure_dir(target)
        self.assertTrue(first["ok"])
        self.assertTrue(first["created"])
        self.assertTrue(second["ok"])
        self.assertFalse(second["created"])

    def test_existing_dir_ok(self):
        result = log_maintenance.ensure_dir(self.make_logs_dir())
        self.assertTrue(result["ok"])
        self.assertFalse(result["created"])

    def test_empty_dir_argument(self):
        result = log_maintenance.ensure_dir("")
        self.assertFalse(result["ok"])
        self.assertIn("空", result["error"])

    def test_failure_returns_false_not_raise(self):
        # 用同名文件占住目录位置 → makedirs 必然失败。
        blocker = os.path.join(self.tmp, "logs")
        with open(blocker, "wb") as handle:
            handle.write(b"not a directory")
        result = log_maintenance.ensure_dir(blocker)
        self.assertFalse(result["ok"])
        self.assertIn("创建日志目录失败", result["error"])

    def test_permission_error_swallowed(self):
        with mock.patch.object(log_maintenance.os, "makedirs",
                               side_effect=PermissionError(5, "拒绝访问")):
            result = log_maintenance.ensure_dir(os.path.join(self.tmp, "logs"))
        self.assertFalse(result["ok"])
        self.assertIn("拒绝访问", result["error"])


class SafePathTests(unittest.TestCase):
    """防路径逃逸：目标必须落在 --dir 之内。"""

    def setUp(self):
        self.logs = tempfile.mkdtemp(prefix="watchparty-logmaint-safe-")
        self.addCleanup(shutil.rmtree, self.logs, True)

    def test_plain_name_resolves_inside(self):
        got = log_maintenance.resolve_inside(self.logs, "launcher.log")
        self.assertEqual(os.path.realpath(got),
                         os.path.realpath(os.path.join(self.logs, "launcher.log")))

    def test_escape_attempts_rejected(self):
        for bad in (".." + os.sep + "evil.log",
                    "sub" + os.sep + "evil.log",
                    os.pardir,
                    os.curdir,
                    "a\0b.log",
                    ""):
            self.assertIsNone(log_maintenance.resolve_inside(self.logs, bad), bad)


class CleanBoundaryTests(_LogMaintenanceTestBase):
    """clean 的核心边界：恰好 N 天不删、超过才删、非日志与标记文件不删。"""

    def test_exactly_n_days_kept_and_n_plus_one_removed(self):
        now = 1_800_000_000.0
        keep = self.make_file("mpv-20261001-000000.log", age_days=30, now=now)
        drop = self.make_file("mpv-20261002-000000.log", age_days=31, now=now)
        result = log_maintenance.clean_directory(self.make_logs_dir(), 30, now=now)
        self.assertTrue(result["ok"], result)
        self.assertTrue(os.path.exists(keep))
        self.assertFalse(os.path.exists(drop))
        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["removed_files"], ["mpv-20261002-000000.log"])
        self.assertEqual(result["kept"], 1)

    def test_only_expired_managed_files_removed(self):
        now = 1_800_000_000.0
        old_log = self.make_file("client-20260901-000000.log", age_days=60, now=now)
        old_archive = self.make_file("launcher.log.2", age_days=60, now=now)
        fresh_log = self.make_file("client-20261023-000000.log", age_days=1, now=now)
        other_log = self.make_file("server-stdout.log", age_days=60, now=now)
        other_file = self.make_file("notes.txt", age_days=60, now=now)
        marker = self.make_file(".last-clean", age_days=60, now=now)
        result = log_maintenance.clean_directory(self.make_logs_dir(), 30, now=now)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["removed"], 2)
        self.assertFalse(os.path.exists(old_log))
        self.assertFalse(os.path.exists(old_archive))
        for path in (fresh_log, other_log, other_file, marker):
            self.assertTrue(os.path.exists(path), path)

    def test_freed_bytes_counts_removed_sizes(self):
        now = 1_800_000_000.0
        self.make_file("mpv-a.log", content=b"a" * 100, age_days=40, now=now)
        self.make_file("mpv-b.log", content=b"b" * 250, age_days=40, now=now)
        result = log_maintenance.clean_directory(self.make_logs_dir(), 30, now=now)
        self.assertEqual(result["removed"], 2)
        self.assertEqual(result["freed_bytes"], 350)

    def test_default_days_is_thirty(self):
        now = time.time()
        aged = self.make_file("mpv-old.log", age_days=45, now=now)
        recent = self.make_file("mpv-new.log", age_days=5, now=now)
        result = log_maintenance.clean_directory(self.make_logs_dir(), now=now)
        self.assertEqual(result["days"], 30)
        self.assertFalse(os.path.exists(aged))
        self.assertTrue(os.path.exists(recent))

    def test_subdirectory_named_like_log_is_skipped(self):
        now = 1_800_000_000.0
        logs = self.make_logs_dir()
        nested = os.path.join(logs, "launcher.log")
        os.makedirs(nested)
        result = log_maintenance.clean_directory(logs, 30, now=now)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["removed"], 0)
        self.assertTrue(os.path.isdir(nested))

    def test_empty_directory_is_ok(self):
        result = log_maintenance.clean_directory(self.make_logs_dir(), 30)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["freed_bytes"], 0)

    def test_missing_directory_reports_error(self):
        missing = os.path.join(self.tmp, "nope")
        result = log_maintenance.clean_directory(missing, 30)
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["error"])
        self.assertEqual(result["removed"], 0)

    def test_negative_days_rejected(self):
        result = log_maintenance.clean_directory(self.make_logs_dir(), -1)
        self.assertFalse(result["ok"])
        self.assertIn("清理周期非法", result["error"])


class CleanFailureTests(_LogMaintenanceTestBase):
    """失败路径：被占用 / 无权限 / 目录不可读 → ok=false 且不抛异常。"""

    def test_permission_error_recorded_and_others_continue(self):
        now = 1_800_000_000.0
        blocked = self.make_file("mpv-locked.log", age_days=60, now=now)
        other = self.make_file("mpv-free.log", age_days=60, now=now)
        real_remove = os.remove

        def fake_remove(path):
            if os.path.basename(path) == "mpv-locked.log":
                raise PermissionError(32, "另一个程序正在使用此文件")
            return real_remove(path)

        with mock.patch.object(log_maintenance.os, "remove",
                               side_effect=fake_remove):
            result = log_maintenance.clean_directory(self.make_logs_dir(), 30,
                                                    now=now)
        # 一个失败 → ok=false，但其余文件仍然被清理（不中断循环）。
        self.assertFalse(result["ok"])
        self.assertEqual(result["removed"], 1)
        self.assertTrue(os.path.exists(blocked))
        self.assertFalse(os.path.exists(other))
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["name"], "mpv-locked.log")
        self.assertIn("删除失败", result["failed"][0]["error"])
        self.assertIn("删除失败", result["error"])

    def test_listdir_permission_error(self):
        with mock.patch.object(log_maintenance.os, "listdir",
                               side_effect=PermissionError(5, "拒绝访问")):
            result = log_maintenance.clean_directory(self.make_logs_dir(), 30)
        self.assertFalse(result["ok"])
        self.assertIn("不可读", result["error"])

    def test_stat_error_recorded(self):
        now = 1_800_000_000.0
        self.make_file("mpv-x.log", age_days=60, now=now)
        with mock.patch.object(log_maintenance.os, "stat",
                               side_effect=OSError("stat 失败")):
            result = log_maintenance.clean_directory(self.make_logs_dir(), 30,
                                                    now=now)
        self.assertFalse(result["ok"])
        self.assertEqual(result["removed"], 0)
        self.assertEqual(result["failed"][0]["name"], "mpv-x.log")

    @unittest.skipUnless(os.name == "nt", "Windows 独占锁语义")
    def test_really_locked_file_on_windows(self):
        """真实占用：Windows 上打开中的文件无法删除，必须被记为失败。"""
        now = 1_800_000_000.0
        locked = self.make_file("mpv-really-locked.log", age_days=60, now=now)
        with open(locked, "rb"):
            result = log_maintenance.clean_directory(self.make_logs_dir(), 30,
                                                     now=now)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["removed"], 0)
        self.assertTrue(os.path.exists(locked))
        self.assertEqual(result["failed"][0]["name"], "mpv-really-locked.log")


class StatusTests(_LogMaintenanceTestBase):
    """status 统计：文件数、总字节、最老/最新。"""

    def test_counts_and_times(self):
        now = 1_800_000_000.0
        self.make_file("mpv-oldest.log", content=b"a" * 100,
                       age_days=10, now=now)
        self.make_file("client-newest.log", content=b"b" * 50,
                       age_days=1, now=now)
        self.make_file("notes.txt", content=b"c" * 7, age_days=5, now=now)
        result = log_maintenance.directory_status(self.make_logs_dir())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files"], 3)
        self.assertEqual(result["total_bytes"], 157)
        self.assertEqual(result["log_files"], 2)
        self.assertEqual(result["log_bytes"], 150)
        # 最老 = 10 天前那个；最新 = 1 天前那个。
        self.assertAlmostEqual(result["oldest_mtime"], now - 10 * DAY, places=0)
        self.assertAlmostEqual(result["newest_mtime"], now - 1 * DAY, places=0)
        self.assertRegex(result["oldest"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

    def test_empty_directory(self):
        result = log_maintenance.directory_status(self.make_logs_dir())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files"], 0)
        self.assertEqual(result["total_bytes"], 0)
        self.assertIsNone(result["oldest"])
        self.assertIsNone(result["newest"])

    def test_missing_directory(self):
        result = log_maintenance.directory_status(os.path.join(self.tmp, "nope"))
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["error"])

    def test_unreadable_directory(self):
        with mock.patch.object(log_maintenance.os, "listdir",
                               side_effect=PermissionError(5, "拒绝访问")):
            result = log_maintenance.directory_status(self.make_logs_dir())
        self.assertFalse(result["ok"])
        self.assertIn("不可读", result["error"])


class CliContractTests(_LogMaintenanceTestBase):
    """CLI 契约：--json 恰好一行且可 json.loads；退出码 0/非 0 语义。"""

    def test_ensure_dir_success_and_created_flag(self):
        target = os.path.join(self.tmp, "portable", "logs")
        code, out, _err = self.run_cli(["ensure-dir", "--dir", target])
        self.assertEqual(code, 0)
        self.assertIn("日志目录已就绪", out)
        self.assertTrue(os.path.isdir(target))
        code, out, _err = self.run_cli(["ensure-dir", "--dir", target, "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.strip())
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["created"])

    def test_ensure_dir_json_has_required_fields(self):
        target = os.path.join(self.tmp, "logs")
        code, out, _err = self.run_cli(["ensure-dir", "--dir", target, "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.strip())
        self.assertEqual(payload["dir"], target)
        self.assertTrue(payload["ok"])

    def test_clean_json_is_single_line_and_parseable(self):
        target = self.make_logs_dir()
        now = time.time()
        path = os.path.join(target, "mpv-20260901-000000.log")
        with open(path, "wb") as handle:
            handle.write(b"z" * 64)
        os.utime(path, (now - 60 * DAY, now - 60 * DAY))
        stamp = repr(now)
        code, out, _err = self.run_cli(
            ["clean", "--dir", target, "--days", "30", "--json", "--now", stamp])
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("\n"), 1, repr(out))
        payload = json.loads(out.strip())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["removed"], 1)
        self.assertEqual(payload["freed_bytes"], 64)
        self.assertEqual(payload["dir"], target)
        self.assertFalse(os.path.exists(path))

    def test_clean_json_output_is_ascii(self):
        """JSON 走 ASCII 转义，任何控制台编码（cp936 等）都能解析。"""
        target = self.make_logs_dir()
        code, out, _err = self.run_cli(["clean", "--dir", target, "--json"])
        self.assertEqual(code, 0)
        out.encode("ascii")  # 不抛 UnicodeEncodeError 即通过
        json.loads(out.strip())

    def test_clean_human_summary(self):
        target = self.make_logs_dir()
        code, out, _err = self.run_cli(["clean", "--dir", target, "--days", "30"])
        self.assertEqual(code, 0)
        self.assertIn("清理完成", out)
        self.assertIn("删除 0 个日志文件", out)

    def test_clean_missing_dir_exit_code(self):
        missing = os.path.join(self.tmp, "nope")
        code, out, _err = self.run_cli(["clean", "--dir", missing, "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(out.strip())
        self.assertFalse(payload["ok"])
        self.assertIn("不存在", payload["error"])
        code, out, _err = self.run_cli(["clean", "--dir", missing])
        self.assertEqual(code, 1)
        self.assertIn("清理未完成", out)

    def test_clean_bad_days_keeps_json_contract(self):
        target = self.make_logs_dir()
        code, out, _err = self.run_cli(
            ["clean", "--dir", target, "--days", "abc", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(out.count("\n"), 1)
        payload = json.loads(out.strip())
        self.assertFalse(payload["ok"])
        self.assertIn("清理周期非法", payload["error"])

    def test_status_json_and_human(self):
        target = self.make_logs_dir()
        self.make_file("mpv-20261023-000000.log", content=b"a" * 20)
        code, out, _err = self.run_cli(["status", "--dir", target, "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out.strip())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["files"], 1)
        self.assertEqual(payload["total_bytes"], 20)
        self.assertIsNotNone(payload["oldest"])
        code, out, _err = self.run_cli(["status", "--dir", target])
        self.assertEqual(code, 0)
        self.assertIn("日志目录：", out)

    def test_status_missing_dir(self):
        code, out, _err = self.run_cli(
            ["status", "--dir", os.path.join(self.tmp, "nope"), "--json"])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.strip())["ok"])

    def test_days_default_matches_documented_default(self):
        target = self.make_logs_dir()
        code, out, _err = self.run_cli(["clean", "--dir", target, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.strip())["days"],
                         log_maintenance.DEFAULT_RETENTION_DAYS)

    def test_missing_required_dir_is_usage_error(self):
        code, _out, _err = self.run_cli(["clean", "--days", "30"])
        self.assertNotEqual(code, 0)

    def test_help_exits_zero(self):
        code, out, _err = self.run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("日志", out)


class EncodingSafetyTests(_LogMaintenanceTestBase):
    """cp936 控制台下列中文不报 UnicodeEncodeError。"""

    def test_cp936_stdout_does_not_raise(self):
        target = self.make_logs_dir()
        raw = io.BytesIO()
        wrapped = io.TextIOWrapper(raw, encoding="cp936")
        self.addCleanup(wrapped.detach)
        with mock.patch.object(sys, "stdout", wrapped):
            code = log_maintenance.main(["status", "--dir", target])
            wrapped.flush()
        self.assertEqual(code, 0)
        self.assertIn("日志目录", raw.getvalue().decode("cp936", "replace"))

    def test_stdout_without_buffer_falls_back(self):
        """没有 buffer 的 stdout（如 StringIO）也能输出，不抛异常。"""
        target = self.make_logs_dir()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            log_maintenance._write_stdout("日志 📂 ▶")
        self.assertIn("日志", out.getvalue())

    def test_write_failure_is_swallowed(self):
        broken = mock.Mock()
        broken.write.side_effect = OSError("管道已关闭")
        with mock.patch.object(sys, "stdout", broken):
            log_maintenance._write_stdout("随便什么")  # 不抛异常即通过


class FormatHelperTests(unittest.TestCase):
    """格式助手：字节数与中文摘要。"""

    def test_format_bytes(self):
        self.assertEqual(log_maintenance.format_bytes(0), "0 B")
        self.assertEqual(log_maintenance.format_bytes(512), "512 B")
        self.assertEqual(log_maintenance.format_bytes(2048), "2.0 KB")
        self.assertEqual(log_maintenance.format_bytes(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(log_maintenance.format_bytes("bad"), "0 B")

    def test_human_summary_clean_error(self):
        text = log_maintenance.human_summary({
            "ok": False, "command": "clean", "dir": r"D:\logs",
            "removed": 0, "freed_bytes": 0, "error": "日志目录不存在：D:\\logs"})
        self.assertIn("清理未完成", text)
        self.assertIn("日志目录不存在", text)

    def test_human_summary_tolerates_non_dict(self):
        self.assertEqual(log_maintenance.human_summary("x"), "x")


if __name__ == "__main__":
    unittest.main()
