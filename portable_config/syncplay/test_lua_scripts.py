#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""syncplay_ui.lua 的静态回归测试。

mpv 只在运行到出问题的那一行时才报错，面板上的一个坏分支往往表现为
"点了没反应"。这里用纯文本检查把两类可静态发现的错误挡在提交前：

1. Lua 的 ``local`` 只在声明语句之后可见。把 ``local function f`` 写在
   调用点之后，调用点会去找一个不存在的全局 ``f``，运行时抛
   ``attempt to call global 'f' (a nil value)``。
2. 菜单条目通过 ``action_value("x")`` 声明动作，``run_action`` 负责实现。
   新增条目却忘了加分支时，点击同样"没反应"。
"""

import re
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "syncplay_ui.lua"

LOCAL_FUNCTION = re.compile(r"^\s*local\s+function\s+([A-Za-z_][A-Za-z0-9_]*)")
LOCAL_ASSIGN = re.compile(r"^\s*local\s+([A-Za-z_][A-Za-z0-9_]*)\s*=")
LOCAL_BARE = re.compile(r"^\s*local\s+([A-Za-z_][A-Za-z0-9_]*)\s*$")


def strip_comment(line):
    return line.split("--", 1)[0]


def local_declarations(lines):
    """返回 {名字: 声明行号}；同名重复声明保留第一次。"""
    declared = {}
    for number, line in enumerate(lines, start=1):
        code = strip_comment(line)
        for pattern in (LOCAL_FUNCTION, LOCAL_ASSIGN, LOCAL_BARE):
            match = pattern.match(code)
            if match:
                declared.setdefault(match.group(1), number)
                break
    return declared


class LuaLocalScopeTests(unittest.TestCase):
    def setUp(self):
        self.lines = SCRIPT.read_text(encoding="utf-8").splitlines()

    def test_script_exists(self):
        self.assertTrue(SCRIPT.is_file(), "找不到 %s" % SCRIPT)

    def test_no_local_is_used_before_its_declaration(self):
        problems = []
        for name, declared_at in sorted(local_declarations(self.lines).items()):
            call = re.compile(r"(?<![A-Za-z0-9_.:])" + re.escape(name) + r"\s*\(")
            for number, line in enumerate(self.lines, start=1):
                if number >= declared_at:
                    break
                if call.search(strip_comment(line)):
                    problems.append(
                        "%s: 第 %d 行调用，但它在第 %d 行才声明"
                        % (name, number, declared_at))
        self.assertEqual(
            problems, [],
            "以下 local 在被声明之前就被调用（运行时会变成 nil 全局）：\n  "
            + "\n  ".join(problems))


class LuaActionCoverageTests(unittest.TestCase):
    """菜单里给出的每个动作都必须有实现分支。"""

    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8")

    def test_every_menu_action_has_a_handler(self):
        offered = set(re.findall(r'action_value\("([^"]+)"\)', self.text))
        handled = set(re.findall(r'action\s*==\s*"([^"]+)"', self.text))
        self.assertTrue(offered, "没有解析到任何 action_value(...)，检查方式可能已失效")
        missing = sorted(offered - handled)
        self.assertEqual(
            missing, [],
            "菜单给出了这些动作但 run_action 没有对应分支：%s" % ", ".join(missing))

    def test_notify_supports_an_explicit_duration(self):
        """耗时操作（UAC、AList 检查）需要更长的提示，否则像"没反应"。"""
        self.assertRegex(
            self.text,
            r"local function notify\(text, seconds\)",
            "notify 应接受第二个参数控制 OSD 时长")


class LuaCrashContainmentTests(unittest.TestCase):
    """单个动作出错不能终止整个脚本。

    mpv 对脚本消息 / 定时器 / 异步回调里未捕获的 Lua 错误会终止整个脚本
    （日志表现为 "Destroying client handle"）。uosc 是独立脚本仍会继续渲染
    面板，于是所有点击都在发往一个已经不存在的脚本——用户看到的就是
    "点击几次后所有点击都失效"。业务入口只允许经 safe_call 调用。
    """

    def setUp(self):
        self.lines = SCRIPT.read_text(encoding="utf-8").splitlines()
        self.text = "\n".join(self.lines)

    def test_safe_call_helper_exists(self):
        self.assertRegex(
            self.text,
            r"local function safe_call\(name, fn",
            "缺少统一错误隔离入口 safe_call")

    def test_run_action_is_never_called_bare(self):
        """run_action 唯一的调用点必须经 safe_call（裸调用出错会杀死脚本）。"""
        offenders = []
        for number, line in enumerate(self.lines, start=1):
            code = strip_comment(line)
            if (re.search(r"(?<![\w.:])run_action\s*\(", code)
                    and not re.search(r"function\s+run_action\s*\(", code)):
                offenders.append("%s: %s" % (number, line.strip()))
        self.assertEqual(
            offenders, [],
            "存在未经 safe_call 的 run_action 裸调用：\n  " + "\n  ".join(offenders))

    def test_menu_event_reopens_panel_even_on_error(self):
        """动作抛错时不能跳过 0.15s 的面板重开定时器。"""
        self.assertRegex(
            self.text,
            r'safe_call\("菜单操作 " \.\. action, run_action, action\)',
            "syncplay-menu-event 里 run_action 必须经 safe_call 调用")
        self.assertRegex(
            self.text,
            r'mp\.add_timeout\(0\.15, function\(\) if panel_open then '
            r'safe_call\("刷新面板", open_panel\) end end\)',
            "动作出错也不能跳过面板重开定时器")

    def test_input_submissions_are_isolated(self):
        for expected in (
            r'safe_call\("输入提交", apply_text',
            r'safe_call\("写入共享地址", configure_tailscale_viewer, query\)',
            r'safe_call\("切换观看者模式", configure_tailscale_viewer, text\)',
            r'safe_call\("切换房主模式", activate_host_mode\)',
        ):
            self.assertRegex(self.text, expected,
                             "输入回调必须经 safe_call 隔离：%s" % expected)

    def test_run_helper_watchdog_releases_the_serial_lock(self):
        """辅助进程回调若永远不来，tailscale_busy 会锁死后续所有网络操作。"""
        self.assertRegex(
            self.text, r"local HELPER_TIMEOUT",
            "必须定义辅助进程超时上限")
        self.assertRegex(
            self.text,
            r"pcall\(mp\.abort_async_command, handle\)",
            "看门狗超时后必须中止卡死的辅助命令")
        self.assertRegex(
            self.text,
            r"if watchdog then watchdog:kill\(\) end",
            "辅助进程正常返回后必须撤销看门狗")


if __name__ == "__main__":
    unittest.main()
