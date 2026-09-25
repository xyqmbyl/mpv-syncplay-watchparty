"""Guard the v0.3.0 player UI against accidental upstream uosc replacement.

Run ``python WatchParty/ViewerPackage/test_ui_baseline.py`` to check sources, or
pass ``--package PATH`` to also inspect an extracted Host/Viewer package.
This test uses only the Python standard library and does not start mpv.
"""

import argparse
import hashlib
import os
from pathlib import Path
import sys
import unittest


BUILDER_DIR = Path(__file__).resolve().parent
REPO_ROOT = BUILDER_DIR.parent.parent
BASELINE_DIR = BUILDER_DIR / "ui-baseline" / "uosc"
PORTABLE_DIR = REPO_ROOT / "portable_config"
TEMPLATE_DIR = BUILDER_DIR / "templates"

# SHA-256 of every non-binary uosc entry in the published v0.3.0 Host ZIP.
# Its Viewer ZIP contains byte-for-byte identical uosc files. Ziggy is added
# separately by each platform builder and intentionally is not pinned here.
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
    "elements/Menu.lua": "9ad3ea9a462d2facc6a5b1293c16ae81def5d5384220d8dbbd04ddc41611703e",
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
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, script)

    def test_templates_keep_custom_ui(self):
        for name in ("host-mpv.conf", "mpv.conf", "macos/mpv.conf"):
            with self.subTest(template=name):
                values = parse_conf(TEMPLATE_DIR / name)
                self.assertEqual(values.get("osc"), "no")
                self.assertEqual(values.get("force-window"), "yes")
                self.assertIn("input-ipc-server", values)
        for name, role in (
            ("host-syncplay_ui.conf", "host"),
            ("syncplay_ui.conf", "viewer"),
            ("macos/syncplay_ui.conf", "host"),
            ("macos/syncplay_ui-viewer.conf", "viewer"),
        ):
            with self.subTest(template=name):
                values = parse_conf(TEMPLATE_DIR / name)
                self.assertEqual(values.get("tailscale_mode"), role)
                self.assertEqual(values.get("auto_start"), "no")
                self.assertEqual(values.get("alist_virtual_root"), "/media")

    def test_extracted_package(self):
        candidate = os.environ.get("WATCHPARTY_UI_PACKAGE")
        if not candidate:
            self.skipTest("set --package or WATCHPARTY_UI_PACKAGE to inspect an extracted ZIP")
        root = package_root(Path(candidate))
        portable = root / "portable_config"
        self.check_uosc(portable / "scripts" / "uosc")
        self.assertEqual(
            sha256(portable / "scripts" / "syncplay_ui.lua"),
            sha256(PORTABLE_DIR / "scripts" / "syncplay_ui.lua"),
            "packaged Syncplay panel differs from the current source",
        )
        for name, expected in V03_FONTS.items():
            with self.subTest(font=name):
                self.assertEqual(sha256(portable / "fonts" / name), expected)
        self.assertEqual(parse_conf(portable / "mpv.conf").get("osc"), "no")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", help="Extracted Host/Viewer package directory")
    args, unittest_args = parser.parse_known_args()
    if args.package:
        os.environ["WATCHPARTY_UI_PACKAGE"] = args.package
    unittest.main(argv=[sys.argv[0], *unittest_args])
