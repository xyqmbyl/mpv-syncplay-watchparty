# -*- coding: utf-8 -*-
"""右键菜单双体系静态回归测试。

背景（2026-10-24 修复，见 docs/RELEASES.md 第十节）：本项目有两套互不相通的
右键菜单——

- 普通右键 = uosc 菜单，数据源 ``portable_config/input_uosc.conf``
  （``mpv.conf`` 的 ``input-conf`` 指向它；uosc 的解析器在
  ``scripts/uosc/lib/menus.lua``，**不认识** ``#@`` 动态子菜单标记）；
- Ctrl+右键 = mpv 原生菜单，数据源 ``portable_config/input_contextmenu_plus.conf``
  （仅 ``scripts/contextmenu_plus.lua`` 消费，支持 ``#@`` 动态子菜单）。

曾发生「日志菜单只写进 input_contextmenu_plus.conf，用户普通右键永远看不到」
的回归（提交 e01c566/13ce3b5 时期，修复于 27e4b51）。本测试把关键事实钉死：

1. input_uosc.conf 必须含「工具 > 日志」子菜单（四个动作 + 5 档清理周期），
   且命令与 contextmenu_plus.lua 注册的 log-menu-* 消息一一对应；
2. input_uosc.conf 不得混入 ``#@`` 动态子菜单标记（uosc 渲染不出来，正是
   上述 bug 的错误类别）；
3. input_contextmenu_plus.conf 的日志动态入口必须挂在「工具」组下，
   不得回到已废弃的「导航」位置；
4. 两处接线（mpv.conf 的 input-conf、script-opts.conf 的
   contextmenu_plus-input_conf）必须指向正确的文件；
5. input_uosc.conf 顶级分组基线（防止无意破坏菜单结构）。
"""
import io
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PORTABLE = os.path.dirname(HERE)
REPO = os.path.dirname(PORTABLE)
UOSC_CONF = os.path.join(PORTABLE, 'input_uosc.conf')
PLUS_CONF = os.path.join(PORTABLE, 'input_contextmenu_plus.conf')
PLUS_LUA = os.path.join(PORTABLE, 'scripts', 'contextmenu_plus.lua')
# 仓库不跟踪 mpv.conf（运行目录专属）；发布包的 mpv.conf 由该模板生成
MPV_BASE_CONF = os.path.join(REPO, 'WatchParty', 'ViewerPackage', 'templates',
                             'mpv-base.conf')
SCRIPT_OPTS = os.path.join(PORTABLE, 'script-opts.conf')

TOP_LEVEL_GROUPS = ['加载', '导航', 'VF 滤镜', '着色器', '截屏', '视频', '工具', '关于']
LOG_DIRECT_ACTIONS = {
    '📂 打开日志文件夹': 'script-message log-menu-open-dir',
    '🧹 自动清理（开/关切换）': 'script-message log-menu-toggle-auto',
    '🧹 立即清理一次': 'script-message log-menu-clean-now',
}
LOG_DAY_CHOICES = (7, 14, 30, 60, 90)


def _read(path):
    with io.open(path, 'r', encoding='utf-8') as fh:
        return fh.read()


def _parse_uosc_menu(text):
    """忠实复刻 uosc lib/menus.lua 的 get_all_user_bindings/get_menu_items 解析：

    - 行格式 ``键位 命令 感叹号标题``；键位 ``#`` 表示纯菜单项（无快捷键）；
    - 标题按 ``>`` 分级建子菜单；标题 ``---`` 是分隔线；
    - 命令为 ``ignore`` 的行不生成条目（动态子菜单标记因此整体失效）。
    """
    root = {'items': [], 'submenus': {}}

    def ensure(path_parts):
        node = root
        for name in path_parts:
            if name not in node['submenus']:
                child = {'items': [], 'submenus': {}}
                node['submenus'][name] = child
                node['items'].append({'title': name, 'submenu': child})
            node = node['submenus'][name]
        return node

    for raw in text.splitlines():
        m = re.match(r'\s*(\S+)\s+([^#]*)\s*(.*?)\s*$', raw)
        if not m:
            continue
        key, command, comment = m.group(1), m.group(2).strip(), m.group(3)
        if not key:
            continue
        commented_out = key[0] == '#'
        if comment:
            comment = comment[1:]
        is_menu = bool(comment) and (comment.startswith('!') or comment.startswith('menu:'))
        if commented_out and len(key) > 1:
            continue  # 形如 #F2 的整行注释
        if commented_out and not is_menu:
            continue  # 普通注释行
        title = ''
        if comment:
            menu_parts = [p for p in comment.split('#')
                          if p.startswith('!') or p.startswith('menu:')]
            if menu_parts:
                mm = (re.match(r'^!\s*(.*?)\s*$', menu_parts[0])
                      or re.match(r'^menu:\s*(.*?)\s*$', menu_parts[0]))
                title = mm.group(1) if mm else ''
        if not title:
            continue
        pieces = re.split(r' *> *', title)
        target = ensure(pieces[:-1])
        last = pieces[-1]
        if last.startswith('---'):
            if target['items']:
                target['items'][-1]['separator'] = True
        elif command != 'ignore':
            target['items'].append({'title': last, 'cmd': command})
    return root


def _get_submenu(root, path):
    node = root
    for name in path:
        matches = [it['submenu'] for it in node['items']
                   if it.get('title') == name and 'submenu' in it]
        if not matches:
            raise AssertionError('菜单里找不到子菜单 %r（路径 %r）' % (name, path))
        node = matches[0]
    return node


class UoscConfTests(unittest.TestCase):
    """普通右键（uosc）菜单的数据源 input_uosc.conf。"""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(UOSC_CONF)
        cls.menu = _parse_uosc_menu(cls.text)

    def test_top_level_groups_baseline(self):
        titles = [it['title'] for it in self.menu['items'] if 'submenu' in it]
        self.assertEqual(titles, TOP_LEVEL_GROUPS)

    def test_tools_has_log_submenu(self):
        log = _get_submenu(self.menu, ['工具', '日志'])
        cmds = {it['title']: it['cmd'] for it in log['items'] if 'cmd' in it}
        for title, cmd in LOG_DIRECT_ACTIONS.items():
            self.assertEqual(cmds.get(title), cmd,
                             '「工具 > 日志」缺少动作 %r' % title)

    def test_tools_log_period_day_choices(self):
        period = _get_submenu(self.menu, ['工具', '日志', '🗓 清理周期'])
        days = [(it['title'], it['cmd']) for it in period['items'] if 'cmd' in it]
        expected = [('%d 天' % d, 'script-message log-menu-set-days %d' % d)
                    for d in LOG_DAY_CHOICES]
        self.assertEqual(days, expected)

    def test_log_menu_commands_registered_in_lua(self):
        conf_cmds = set(re.findall(r'script-message (log-menu-[a-z-]+)', self.text))
        self.assertTrue(conf_cmds, 'input_uosc.conf 里没有 log-menu-* 命令')
        lua = _read(PLUS_LUA)
        registered = set(re.findall(
            r"mp\.register_script_message\('(log-menu-[a-z-]+)'", lua))
        unregistered = conf_cmds - registered
        self.assertFalse(
            unregistered,
            '这些菜单命令未在 contextmenu_plus.lua 注册，点击会无效: %s' % sorted(unregistered))

    def test_no_dynamic_menu_markers(self):
        # uosc 解析器不认识 #@ 动态子菜单标记；这类行写进本文件只会得到
        # 空壳分组或整行失效（2026-10-24 修复的那类错误）。
        bad = [line for line in self.text.splitlines() if re.search(r'#!.*#@', line)]
        self.assertFalse(bad, 'input_uosc.conf 出现了动态子菜单标记: %r' % bad)


class ContextmenuPlusConfTests(unittest.TestCase):
    """Ctrl+右键（mpv 原生菜单）的数据源 input_contextmenu_plus.conf。"""

    @classmethod
    def setUpClass(cls):
        cls.text = _read(PLUS_CONF)

    def test_log_entry_under_tools(self):
        self.assertIn('工具 > ⇲ 日志 > #@log', self.text,
                      'contextmenu_plus 菜单的日志入口应在「工具」组下')

    def test_log_entry_not_under_navigation(self):
        # 「导航」是 2026-10 已废弃的旧位置（用户裁决 m00981），不得回归。
        self.assertNotIn('导航 > ⇲ 日志', self.text)
        self.assertNotIn('导航 > 日志', self.text)


class MenuWiringTests(unittest.TestCase):
    """两套菜单的接线必须指向正确的文件。"""

    def test_mpv_conf_input_conf_targets_uosc_conf(self):
        for line in _read(MPV_BASE_CONF).splitlines():
            stripped = line.strip()
            if stripped.startswith('input-conf'):
                self.assertIn('input_uosc.conf', stripped,
                              'mpv.conf 模板的 input-conf 必须指向 input_uosc.conf（uosc 右键菜单的数据源）')
                return
        self.fail('mpv-base.conf 模板缺少 input-conf 配置')

    def test_script_opts_input_conf_targets_plus_conf(self):
        text = _read(SCRIPT_OPTS)
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            if 'contextmenu_plus-input_conf' in stripped:
                self.assertIn('input_contextmenu_plus.conf', stripped)
                return
        self.fail('script-opts.conf 缺少 contextmenu_plus-input_conf 接线')


if __name__ == '__main__':
    unittest.main()
