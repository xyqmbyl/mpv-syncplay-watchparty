"""Guard the v0.3.0 player UI against accidental upstream uosc replacement.

Run ``python WatchParty/ViewerPackage/test_ui_baseline.py`` to check sources, or
pass ``--package PATH`` to also inspect an extracted WatchParty package.
The merged package replaces the old Host/Viewer split, so every template is
checked for the neutral "no role chosen yet" state.
This test uses only the Python standard library and does not start mpv.
"""

import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import re
import sys
import unittest


BUILDER_DIR = Path(__file__).resolve().parent
REPO_ROOT = BUILDER_DIR.parent.parent
BASELINE_DIR = BUILDER_DIR / "ui-baseline" / "uosc"
PORTABLE_DIR = REPO_ROOT / "portable_config"
TEMPLATE_DIR = BUILDER_DIR / "templates"

# SHA-256 of every non-binary uosc entry shipped by v0.3.0. The merged package
# keeps these files byte-for-byte; only the role split around them was removed.
# Ziggy is added separately by each platform builder and intentionally is not
# pinned here.
V03_UOSC = {
    "char-conv/zh-hans.json": "6d2ebcaf98e9a9fc565d1d7e05929d944ead8ce829db59b6c46b180c5db2d21b",
    "char-conv/zh.json": "0ee5c7a215907410b3cd4a50ef5ff18958c47325b8bed4607ccb27ed035ab9d8",
    "elements/BufferingIndicator.lua": "f7ceb7c88916e49ee5aef2a8c45d53ee5a6f9931b7977898ecf96ebfa543c1c4",
    "elements/Button.lua": "c5b9f1df2025a010f6606c708144abe383c99be485c51fae1dbdc7ee17f94f63",
    "elements/Controls.lua": "e9716781776ed107555280e99a1ad49deebbcbc5f66fed19a222771126b48196",
    "elements/Curtain.lua": "7f965119a6fc70d8cce70cf025db764a5d83433321da79a7734c691afdbb1e32",
    "elements/CycleButton.lua": "e49e20110bf84d852ce4d85f26e36a24330ef9a54f28f0886cc658352e09aa75",
    "elements/Element.lua": "381141cd760a42769c0abdf7cfdf7ef26930249e133fa9800e7c8623f55bc016",
    "elements/Elements.lua": "f44e57ea4c4c60f82f3775df9337064fdbcb7cb4bc157b47a5c07a12ee8a62c7",
    "elements/Logo.lua": "390e2b154ba90b5dc6771f2511aa32a6cf953ea294df367923a5d0f7eec62b3f",
    "elements/ManagedButton.lua": "da1c1ecf4d145e5c06fb7077a654b1c58412ae106e54b3f5946b919dabd01446",
    "elements/Menu.lua": "a8c7e681aebf3e08fb6ce9aac2d53720e85ce5dabba329299bd7bce2a5862e21",
    "elements/PauseIndicator.lua": "3f15a46b1a7e74e1f0e23942ff1814b44c2f5fd9d7c9337222a9743271f090c6",
    "elements/Speed.lua": "fee532d01e108cb4d74fbfdedaf91ea2fff81af3b4f3f4e3543fd8aaefa92326",
    "elements/Timeline.lua": "696637de58fecf46035cc688bafdddc34b3bc39c393b356689d101a2a256182a",
    "elements/TopBar.lua": "1cb99c1416765dafca8d2a3cc15a6f426fa41845c5f3f8fad268b6cddf57c143",
    "elements/Volume.lua": "62995ad1cbe4d424a4118da30ba26493727a0f43aa5e787dc7fd15ce373682aa",
    "elements/WindowBorder.lua": "61435c9c6172d47cf24c6bddf65f3e0081939cd88a20b0ae4b4a103aa613a3a2",
    "lib/ass.lua": "e6fbfce5de8be51bdd79cbce6d1eaf467d1f7e64506b26258d09cb10dffebaa7",
    "lib/buttons.lua": "f79302eda925010a8ccbfa7e960e0fc7760114b920d641897474ed91ea318f73",
    "lib/char_conv.lua": "57dca5a0498d254826d8c18a1fc800b3556a6f49318e7ad0fbefc51b7167660f",
    "lib/cursor.lua": "8a9f7bb14eae92f4b171c5655899c9954f4050a897c08db42aa90f72a84a3c0c",
    "lib/fzy.lua": "5b1b53aaf56c8b51bd3507f6f6b76c042b92ceb55eaac8533277754158426711",
    "lib/lang.lua": "77d679059e1cd0aa6594c9303a0b39708aa1bde6b5bf0e937255e4b91bb4b127",
    "lib/menus.lua": "f7e5d13c6ff696ee43122d07a70caf827639509d9f105b43cefa9054ad873eb8",
    "lib/std.lua": "fdaedd93bdb9b716dbd99ec1c8cff08b3d316c9fdc793718e2b4ee7a504799f5",
    "lib/text.lua": "f4e040412b2e3c2d276d8098aa1eea01e020e916123336470dd2480a5968eb07",
    "lib/utils.lua": "be34f81c8b123679548a8799a4dadd1a94634538aa554b39c08d605e7f1664aa",
    "main.lua": "20482d3906000c52de11d4ffd73d19c530b447ae28a2857bba6dd072ddeea319",
}

V03_FONTS = {
    "MaterialIconsRound-Regular.otf": "bad85e5454b6288104ce03806c37323bcd8f145e3094e727860173ac8c91062e",
    "uosc_textures.ttf": "ccc0660f284dfceb5ab31eb363ccb2355df30fcdf628e781ee374b7d4172ada5",
}

# The published packages carry the author's full local UI, not a minimal config.
# Each of these files must be staged by every platform builder and must exist in
# every extracted package.
UI_LAYER_FILES = (
    "input_uosc.conf",
    "input_contextmenu_plus.conf",
    "profiles.conf",
    "script-opts.conf",
    "script-opts/mpv360.conf",
    "scripts/syncplay_ui.lua",
    "scripts/autoload.lua",
    "scripts/contextmenu_plus.lua",
    "scripts/copy-paste-URL.lua",
    "scripts/input_plus.lua",
    "scripts/mpv360.lua",
    "scripts/pressaction.lua",
    "scripts/save_global_props.lua",
    "scripts/stats_mediainfo.lua",
    "scripts/thumbfast.lua",
    # The retained shader hotkeys resolve ~~/shaders/... by exact path; without
    # this file mpv360.lua and the Ctrl+ shortcuts fail at runtime.
    "shaders/mpv360.glsl",
)

# The only intentional difference from the author's local menu: these two uosc
# main-menu groups are removed. Their keybindings stay live.
REMOVED_MENU_GROUPS = ("VF 滤镜", "着色器")

# Files the local config asks mpv to load; the shared base must reach the package
# or the shipped UI silently falls back to mpv defaults.
MPV_BASE_INCLUDE = 'include = "~~/mpv-base.conf"'

# One merged package serves both roles: the user picks 房主模式 / 观看者模式 at
# runtime from the panel (Ctrl+Shift+S -> 联机 -> 运行模式), which then rewrites
# these values in memory. Every template must therefore ship the safe, neutral
# state and must never pin a host address or enable sharing on its own.
NEUTRAL_SYNCPLAY_VALUES = {
    "server": "syncplay.pl:8995",
    "auto_start": "no",
    "alist_enabled": "no",
    "alist_server": "http://127.0.0.1:5244",
    "alist_root": "~~/../WatchParty/media",
    "alist_virtual_root": "/media",
    "alist_map": "",
    "tailscale_mode": "off",
    "tailscale_host": "",
}

# The mpv overlay of each platform, and the template the Windows builder renders
# into portable_config/script-opts/syncplay_ui.conf.
TEMPLATE_OVERLAYS = ("watchparty-mpv.conf", "macos/mpv.conf")
TEMPLATE_SYNCPLAY_CONFIGS = (
    "watchparty-syncplay_ui.conf",
    "macos/watchparty-syncplay_ui.conf",
)

# Templates that belonged to the removed Host/Viewer split. Their reappearance
# means a role-specific package crept back into a single-package build.
REMOVED_ROLE_TEMPLATES = (
    "host-mpv.conf", "mpv.conf",
    "host-syncplay_ui.conf", "syncplay_ui.conf",
    "host-THIRD_PARTY_NOTICES.txt", "THIRD_PARTY_NOTICES.txt",
    "启动观看.bat", "观看者使用说明.md", "观看者首次运行.bat",
    "macos/syncplay_ui.conf", "macos/syncplay_ui-viewer.conf",
    "macos/启动观看.command", "macos/观看者首次设置.command",
    "macos/房主使用说明.md", "macos/观看者使用说明.md",
)

# Every ~~/shaders/... path these shipped files mention must exist in the
# package. mpv resolves the path literally against the config directory and has
# no fallback search, so a missing file means a visibly broken hotkey.
SHADER_REFERENCE_SOURCES = (
    "input_uosc.conf",
    "scripts/mpv360.lua",
)
SHADER_REFERENCE = re.compile(r"~~/shaders/([^\"';]+)")

# The macOS builder cannot run on Windows, so its source paths are checked
# statically: a template it copies but that does not exist only surfaces as a
# failed CI run otherwise. ``VAR="$SCRIPT_DIR/<relative>"`` assignments are read
# back from the script so these checks follow the builder instead of restating
# its layout here.
MACOS_BUILDER = BUILDER_DIR / "build-macos-package.sh"
WINDOWS_BUILDER = BUILDER_DIR / "build-watchparty-package.ps1"
BUILDER_DIR_VARIABLE = re.compile(r'^([A-Z_]+)="\$SCRIPT_DIR/([^"]+)"\s*$', re.MULTILINE)
BUILDER_PATH_REFERENCE = re.compile(r'\$([A-Z_]+)/([^"\s]+)')
BUILDER_LOOP = re.compile(r"for ([A-Za-z_]+) in (.+?); do(.*?)\bdone\b", re.DOTALL)
BUILDER_CONTINUATION = re.compile(r"\\\n[ \t]*")
# Only directories that ship in the repository. The builder's other variables
# (CACHE_DIR, STAGE, ...) legitimately hold downloaded or generated content that
# does not exist before a build, so they are deliberately not checked here.
BUILDER_SOURCE_DIRECTORIES = ("TEMPLATE_DIR", "COMMON_TEMPLATE_DIR", "SCRIPT_DIR", "REPO_ROOT")
# Directories the builder creates for itself rather than reading from the
# checkout, so they legitimately do not exist before a build.
BUILDER_GENERATED_DIRECTORIES = ("output", "artifacts")


def builder_loops(text):
    """Yield (variable, names, body) for each `for VAR in ...; do` in the script."""
    joined = BUILDER_CONTINUATION.sub(" ", text)
    for match in BUILDER_LOOP.finditer(joined):
        yield match.group(1), match.group(2).split(), match.group(3)


def shader_references(root):
    """Shipped shader paths, relative to ~~/shaders/, requested by the UI layer."""
    found = set()
    for relative in SHADER_REFERENCE_SOURCES:
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found.update(match.group(1) for match in SHADER_REFERENCE.finditer(text))
    return found


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def ui_files(root):
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and "bin" not in path.relative_to(root).parts
    }


def parse_conf(path):
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def keybindings(text):
    """Map a keybinding signature -> list of raw lines for non-menu lines.

    A uosc menu entry is a normal input.conf binding carrying a trailing
    ``#! Group > Item`` label. Dropping the label removes the menu entry while
    keeping the binding, so the signature deliberately ignores the label.
    """
    bindings = {}
    for raw in text.splitlines():
        line = raw.split("#!", 1)[0].rstrip()
        if not line.strip():
            continue
        parts = line.split(None, 2)
        if len(parts) < 2 or parts[0] == "#":
            continue
        bindings.setdefault((parts[0], parts[1]), []).append(raw)
    return bindings


def menu_labels(text):
    labels = []
    for raw in text.splitlines():
        if "#!" in raw:
            labels.append(raw.split("#!", 1)[1].strip())
    return labels


def package_root(path):
    path = path.resolve()
    if (path / "portable_config").is_dir():
        return path
    children = [child for child in path.iterdir() if (child / "portable_config").is_dir()]
    if len(children) == 1:
        return children[0]
    raise ValueError("--package must point to an extracted package or its one-folder parent")


class UiBaselineTests(unittest.TestCase):
    def check_uosc(self, root):
        self.assertTrue(root.is_dir(), f"uosc directory missing: {root}")
        self.assertEqual(ui_files(root), set(V03_UOSC), "uosc UI file list differs from v0.3.0")
        for relative, expected in V03_UOSC.items():
            with self.subTest(file=relative):
                self.assertEqual(sha256(root / relative), expected, relative)

    def test_v03_uosc_baseline(self):
        self.check_uosc(BASELINE_DIR)

    def test_v03_controls_and_menu_behavior(self):
        main = (BASELINE_DIR / "main.lua").read_text(encoding="utf-8")
        menu = (BASELINE_DIR / "elements" / "Menu.lua").read_text(encoding="utf-8")
        for marker in (
            "play_pause", "ST-stats_tog", "ST-thumb_tog",
            "open_search_danmaku_menu", "open_add_source_menu",
            "open_add_total_menu", "show_danmaku@uosc_danmaku",
        ):
            with self.subTest(control=marker):
                self.assertIn(marker, main)
        self.assertIn("require('lib/lang')", main)
        self.assertIn("self.root.persistent", menu)
        self.assertIn("submenu_rect = draw_menu", menu)

    def test_syncplay_panel_stays_open(self):
        script = (PORTABLE_DIR / "scripts" / "syncplay_ui.lua").read_text(encoding="utf-8")
        for marker in (
            'title = "关闭面板"', 'action_value("close")',
            "keep_open = true", "persistent = true",
            '"uosc", "open-menu"', '"uosc", "update-menu"',
            '"uosc", "close-menu"',
            'id = "syncplay.overview"', 'id = "syncplay.playback"',
            'id = "syncplay.members"', 'id = "syncplay.tailscale"',
            # The merged package picks its role here instead of at build time.
            "运行模式", "房主模式", "观看者模式", "tailscale_mode",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, script)

    def check_neutral_syncplay_config(self, path):
        """A shipped syncplay_ui.conf must not prefer either role."""
        values = parse_conf(path)
        for key, expected in NEUTRAL_SYNCPLAY_VALUES.items():
            with self.subTest(config=path.name, key=key):
                self.assertEqual(values.get(key), expected, f"{path.name}: {key}")
        # mpv's IPC socket is how the panel reaches the running player; an empty
        # value silently disables every runtime mode switch.
        self.assertTrue(values.get("pipe"), f"{path.name}: pipe must not be empty")

    def test_templates_keep_custom_ui(self):
        for name in TEMPLATE_OVERLAYS:
            with self.subTest(template=name):
                values = parse_conf(TEMPLATE_DIR / name)
                self.assertEqual(values.get("osc"), "no")
                self.assertEqual(values.get("force-window"), "yes")
                self.assertIn("input-ipc-server", values)
        for name in TEMPLATE_SYNCPLAY_CONFIGS:
            with self.subTest(template=name):
                self.check_neutral_syncplay_config(TEMPLATE_DIR / name)

    def test_role_specific_templates_are_gone(self):
        leftovers = [
            name for name in REMOVED_ROLE_TEMPLATES if (TEMPLATE_DIR / name).exists()
        ]
        self.assertEqual(leftovers, [], "role-split templates must not come back")
        for name in ("watchparty-mpv.conf", "watchparty-syncplay_ui.conf"):
            with self.subTest(template=name):
                self.assertTrue(
                    (TEMPLATE_DIR / name).is_file(),
                    f"merged template is missing: {name}",
                )

    def test_windows_builder_builds_one_merged_package(self):
        """The Windows builder must render the neutral config for both arches."""
        text = WINDOWS_BUILDER.read_text(encoding="utf-8", errors="replace")
        for marker in (
            "'watchparty-mpv.conf'", "'watchparty-syncplay_ui.conf'",
            "'watchparty-THIRD_PARTY_NOTICES.txt'", "'mpv-base.conf'",
            "portable_config\\mpv.conf", "script-opts\\syncplay_ui.conf",
            "$NativeRoot",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)
        self.assertIn("'x64', 'x86'", text, "the builder must keep both x86 and x64")
        self.assertIn("WatchParty-Windows-$Arch", text)
        for name in ("首次运行.bat", "启动.bat"):
            with self.subTest(entry=name):
                self.assertTrue((REPO_ROOT / "WatchParty" / name).is_file())

    def test_macos_uses_bundled_chinese_font(self):
        values = parse_conf(TEMPLATE_DIR / "macos" / "mpv.conf")
        self.assertEqual(values.get("osd-font"), "LXGW WenKai Mono Lite")
        self.assertEqual(values.get("sub-font"), "LXGW WenKai Mono Lite")
        danmaku = parse_conf(TEMPLATE_DIR / "macos" / "uosc_danmaku.conf")
        self.assertEqual(danmaku.get("fontname"), "LXGW WenKai Mono Lite")

    def test_shipped_ui_layer_is_complete(self):
        for relative in UI_LAYER_FILES:
            with self.subTest(file=relative):
                self.assertTrue(
                    (PORTABLE_DIR / relative).is_file(),
                    f"shipped UI layer is missing {relative}",
                )

    def test_main_menu_drops_only_the_two_groups(self):
        labels = menu_labels((PORTABLE_DIR / "input_uosc.conf").read_text(encoding="utf-8"))
        for group in REMOVED_MENU_GROUPS:
            with self.subTest(group=group):
                self.assertFalse(
                    any(label == group or label.startswith(group + " >") for label in labels),
                    f"{group} must not appear in the shipped main menu",
                )
        # Other groups must still be present, otherwise the menu was gutted.
        for group in ("截屏", "视频", "工具", "导航", "关于", "加载"):
            with self.subTest(group=group):
                self.assertTrue(
                    any(label == group or label.startswith(group + " >") for label in labels),
                    f"{group} is missing from the shipped main menu",
                )

    def test_keybindings_are_not_trimmed(self):
        # The packages must keep every real binding; only the menu labels change.
        shipped = keybindings(
            (PORTABLE_DIR / "input_uosc.conf").read_text(encoding="utf-8")
        )
        for keys in (
            ("~", 'vf'), ("Ctrl+6", "change-list"),
            ("Ctrl+`", "change-list"), ("Ctrl+s", "screenshot"),
        ):
            with self.subTest(key=keys[0]):
                self.assertIn(keys, shipped, f"{keys[0]} binding lost")

    def test_shader_hotkey_targets_are_shipped(self):
        # mpv expands ~~/shaders/x literally against the config directory and
        # never falls back to a subdirectory, so every referenced file must be
        # present or the hotkey dies with "Failed to open ...".
        references = shader_references(PORTABLE_DIR)
        self.assertTrue(references, "no shader references found in the shipped UI layer")
        for relative in sorted(references):
            with self.subTest(shader=relative):
                self.assertTrue(
                    (PORTABLE_DIR / "shaders" / relative).is_file(),
                    f"a shipped hotkey references a missing shader: shaders/{relative}",
                )

    def test_shared_base_config_reaches_every_package(self):
        base = parse_conf(TEMPLATE_DIR / "mpv-base.conf")
        self.assertEqual(base.get("input-conf"), '"~~/input_uosc.conf"')
        self.assertEqual(base.get("osc"), "no")
        self.assertEqual(base.get("sub-scale"), "1")
        for name in TEMPLATE_OVERLAYS:
            with self.subTest(template=name):
                text = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
                self.assertIn(MPV_BASE_INCLUDE, text)
                # The overlay must not smuggle the local filters back in.
                for line in text.splitlines():
                    stripped = line.strip()
                    self.assertFalse(
                        stripped.startswith("vf-pre") or stripped.startswith("glsl-shaders"),
                        f"{name} enables a local filter/shader: {line}",
                    )

    def test_macos_builder_sources_exist(self):
        """Every source the macOS builder copies must exist in this checkout."""
        text = MACOS_BUILDER.read_text(encoding="utf-8")
        # $SCRIPT_DIR is the builder directory itself; REPO_ROOT is its grandparent.
        directories = {
            name: BUILDER_DIR / relative
            for name, relative in BUILDER_DIR_VARIABLE.findall(text)
            if name in BUILDER_SOURCE_DIRECTORIES
        }
        directories.setdefault("SCRIPT_DIR", BUILDER_DIR)
        directories.setdefault("REPO_ROOT", REPO_ROOT)
        self.assertIn("TEMPLATE_DIR", directories, "builder must define TEMPLATE_DIR")
        self.assertIn("COMMON_TEMPLATE_DIR", directories)

        # Drop the `VAR="$SCRIPT_DIR/..."` assignments themselves: they name the
        # downloaded/generated directories and are not file loads.
        body = BUILDER_DIR_VARIABLE.sub("", text)

        # 1. Literal loads such as `cp "$TEMPLATE_DIR/mpv.conf" ...`. A template
        #    load is always a file; the other roots may legitimately be
        #    directories (shaders/, scripts/uosc_danmaku/).
        references = {
            (variable, relative)
            for variable, relative in BUILDER_PATH_REFERENCE.findall(body)
            if variable in directories
            and "$" not in relative
            and not (
                variable == "SCRIPT_DIR"
                and relative.split("/")[0] in BUILDER_GENERATED_DIRECTORIES
            )
        }
        self.assertTrue(references, "no builder source references found")
        for variable, relative in sorted(references):
            target = directories[variable] / relative
            is_template = variable in ("TEMPLATE_DIR", "COMMON_TEMPLATE_DIR")
            with self.subTest(source=f"{variable}/{relative}"):
                self.assertTrue(
                    target.is_file() if is_template else target.exists(),
                    f"build-macos-package.sh loads a missing source: {relative}",
                )

        # 2. `for X in <names>; do cp "$ROOT/<sub>/$X" ...` loops: each listed
        #    name is a file the package needs, so check them individually.
        loop_prefixes = {
            "$TEMPLATE_DIR/": directories["TEMPLATE_DIR"],
            "$COMMON_TEMPLATE_DIR/": directories["COMMON_TEMPLATE_DIR"],
            "$REPO_ROOT/portable_config/scripts/": REPO_ROOT / "portable_config" / "scripts",
            "$REPO_ROOT/portable_config/": REPO_ROOT / "portable_config",
        }
        checked = 0
        for variable, names, loop_body in builder_loops(text):
            for prefix, base in loop_prefixes.items():
                if prefix + "$" + variable not in loop_body:
                    continue
                self.assertTrue(names, f"empty copy loop for {prefix}")
                for name in names:
                    checked += 1
                    with self.subTest(loop=prefix, name=name):
                        self.assertTrue(
                            (base / name).is_file(),
                            f"build-macos-package.sh copies a missing file: {name}",
                        )
        self.assertTrue(checked, "no builder copy loop was checked")

        self.assertTrue(
            list((directories["SCRIPT_DIR"] / "templates" / "licenses").glob("*.txt")),
            "third-party license templates are missing",
        )

    def test_extracted_package(self):
        candidate = os.environ.get("WATCHPARTY_UI_PACKAGE")
        if not candidate:
            self.skipTest("set --package or WATCHPARTY_UI_PACKAGE to inspect an extracted ZIP")
        root = package_root(Path(candidate))
        portable = root / "portable_config"
        self.check_uosc(portable / "scripts" / "uosc")
        for relative in UI_LAYER_FILES:
            with self.subTest(file=relative):
                self.assertTrue(
                    (portable / relative).is_file(),
                    f"package is missing {relative}",
                )
        overlay = (portable / "mpv.conf").read_text(encoding="utf-8")
        self.assertIn(MPV_BASE_INCLUDE, overlay, "package mpv.conf does not include mpv-base.conf")
        self.assertEqual(
            shader_references(portable),
            shader_references(PORTABLE_DIR),
            "packaged files reference a different shader set than the source config",
        )
        for relative in sorted(shader_references(portable)):
            with self.subTest(shader=relative):
                self.assertTrue(
                    (portable / "shaders" / relative).is_file(),
                    f"package hotkey references a missing shader: shaders/{relative}",
                )
        for line in (portable / "mpv-base.conf").read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            self.assertFalse(
                stripped.startswith("vf-pre") or stripped.startswith("glsl-shaders"),
                f"packaged mpv-base.conf enables a local filter/shader: {line}",
            )
        shipped_input = (portable / "input_uosc.conf").read_text(encoding="utf-8")
        self.assertEqual(
            set(keybindings(shipped_input)),
            set(keybindings((PORTABLE_DIR / "input_uosc.conf").read_text(encoding="utf-8"))),
            "packaged keybindings differ from the source config",
        )
        labels = menu_labels(shipped_input)
        for group in REMOVED_MENU_GROUPS:
            with self.subTest(group=group):
                self.assertFalse(
                    any(label == group or label.startswith(group + " >") for label in labels),
                    f"package menu still contains {group}",
                )
        self.assertEqual(
            sha256(portable / "scripts" / "syncplay_ui.lua"),
            sha256(PORTABLE_DIR / "scripts" / "syncplay_ui.lua"),
            "packaged Syncplay panel differs from the current source",
        )
        for name, expected in V03_FONTS.items():
            with self.subTest(font=name):
                self.assertEqual(sha256(portable / "fonts" / name), expected)
        self.assertEqual(parse_conf(portable / "mpv.conf").get("osc"), "no")
        # A freshly extracted package must not share anything until the user
        # picks a mode, so the packaged config has to stay neutral.
        self.check_neutral_syncplay_config(portable / "script-opts" / "syncplay_ui.conf")
        if (root / "mpv.app").is_dir():
            self.assertEqual(
                sha256(portable / "fonts/LXGWWenKaiMonoLite-Regular.ttf"),
                "03d04443c99a261c5d1ac5cca1ef3e174194a5e1126d05f7a19c33617eb183f3",
            )
            app = root / "mpv.app"
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            self.assertEqual(info.get("CFBundleExecutable"), "watchparty-launcher")
            self.assertTrue(os.access(app / "Contents/MacOS/watchparty-launcher", os.X_OK))
            values = parse_conf(portable / "mpv.conf")
            self.assertEqual(values.get("osd-font"), "LXGW WenKai Mono Lite")
            self.assertEqual(values.get("sub-font"), "LXGW WenKai Mono Lite")
            danmaku = parse_conf(portable / "script-opts" / "uosc_danmaku.conf")
            self.assertEqual(danmaku.get("fontname"), "LXGW WenKai Mono Lite")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", help="Extracted WatchParty package directory")
    args, unittest_args = parser.parse_known_args()
    if args.package:
        os.environ["WATCHPARTY_UI_PACKAGE"] = args.package
    unittest.main(argv=[sys.argv[0], *unittest_args])
