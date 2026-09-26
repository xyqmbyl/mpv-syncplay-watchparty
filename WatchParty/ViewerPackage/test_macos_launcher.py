"""Exercise the real native launcher, bundle signing, and argument forwarding.

Run on macOS: python3 WatchParty/ViewerPackage/test_macos_launcher.py
No player, network service, or user-global configuration is started or changed.
"""

from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import tempfile
import unittest

# Embedded Python's ._pth isolation omits the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from install_macos_launcher import LAUNCHER_NAME, install_launcher


RECORDER = r"""
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(int argc, char **argv) {
    char *cwd = getcwd(NULL, 0);
    printf("%ld%c%s%c", (long)getpid(), 0, cwd, 0);
    free(cwd);
    for (int i = 0; i < argc; i++) printf("%s%c", argv[i], 0);
    return 0;
}
"""


@unittest.skipUnless(platform.system() == "Darwin", "native macOS launcher")
class MacosLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.TemporaryDirectory(prefix="watchparty-launcher-test-")
        cls.root = Path(cls.work.name).resolve()
        cls.package = cls.root / "电影 night $(touch SHOULD_NOT_EXIST)"
        cls.app = cls.package / "mpv.app"
        cls.mac = cls.app / "Contents/MacOS"
        cls.mac.mkdir(parents=True)
        (cls.package / "portable_config").mkdir()
        cls.plist = cls.app / "Contents/Info.plist"
        cls.original_info = {
            "CFBundleExecutable": "mpv",
            "CFBundleIdentifier": "test.watchparty.launcher",
            "CFBundleName": "mpv",
            "CFBundlePackageType": "APPL",
            "CFBundleDocumentTypes": [{"CFBundleTypeName": "Movie", "CFBundleTypeRole": "Viewer"}],
        }
        cls.plist.write_bytes(plistlib.dumps(cls.original_info))
        recorder = cls.root / "recorder.c"
        recorder.write_text(RECORDER)
        subprocess.run(
            ["/usr/bin/xcrun", "clang", str(recorder), "-o", str(cls.mac / "mpv")],
            check=True,
        )
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(cls.app)], check=True)
        cls.mpv_code = subprocess.check_output(
            ["/usr/bin/otool", "-s", "__TEXT", "__text", str(cls.mac / "mpv")]
        )
        cls.launcher = install_launcher(cls.package)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    def launch(self, *arguments, executable=None):
        process = subprocess.Popen(
            [str(executable or self.launcher), *arguments], cwd=self.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        output, errors = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, errors.decode())
        fields = output.decode().rstrip("\0").split("\0")
        self.assertEqual(int(fields[0]), process.pid, "exec must preserve the Finder process")
        self.assertEqual(fields[1], str(self.root), "relative media arguments retain cwd")
        return fields[2:]

    def test_finder_default_loads_sibling_config(self):
        self.assertEqual(self.launch(), [str(self.mac / "mpv"), "--config-dir=" + str(self.package / "portable_config")])

    def test_arguments_preserve_unicode_spaces_and_shell_metacharacters(self):
        args = ["电影 with spaces.mkv", "https://example.invalid/a?q=x&v=$hello", "--", "-filename.mkv"]
        self.assertEqual(self.launch(*args)[2:], args)
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())

    def test_explicit_config_and_no_config_options_can_override_default(self):
        args = ["--config-dir=/another config", "--no-config", "--version"]
        self.assertEqual(self.launch(*args)[2:], args)

    def test_symlink_and_package_relocation(self):
        relocated = self.root / "重命名 package"
        self.package.rename(relocated)
        link = self.root / "launcher-link"
        target = relocated / "mpv.app/Contents/MacOS" / LAUNCHER_NAME
        link.symlink_to(target)
        try:
            self.assertEqual(self.launch(executable=link)[1], "--config-dir=" + str(relocated / "portable_config"))
        finally:
            link.unlink()
            relocated.rename(self.package)

    def test_missing_portable_config_fails_instead_of_silently_loading_global_config(self):
        config = self.package / "portable_config"
        config.rmdir()
        try:
            result = subprocess.run([str(self.launcher)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("portable_config is missing beside mpv.app", result.stderr)
            self.assertEqual(result.stdout, "")
        finally:
            config.mkdir()

    def test_bundle_metadata_binary_and_signature_survive_repeat_install(self):
        install_launcher(self.package)
        info = plistlib.loads(self.plist.read_bytes())
        self.assertEqual(info.pop("CFBundleExecutable"), LAUNCHER_NAME)
        expected = dict(self.original_info)
        expected.pop("CFBundleExecutable")
        self.assertEqual(info, expected)
        self.assertEqual(subprocess.check_output(
            ["/usr/bin/otool", "-s", "__TEXT", "__text", str(self.mac / "mpv")]
        ), self.mpv_code)
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(self.app)], check=True)

    def test_wrong_architecture_is_rejected_before_mutating_bundle(self):
        other = "intel" if platform.machine() == "arm64" else "arm64"
        previous = self.plist.read_bytes()
        with self.assertRaisesRegex(ValueError, "does not match"):
            install_launcher(self.package, other)
        self.assertEqual(self.plist.read_bytes(), previous)

    def test_intel_package_gets_an_intel_launcher_on_any_mac(self):
        package = self.root / "Intel package"
        app = package / "mpv.app"
        mac = app / "Contents/MacOS"
        mac.mkdir(parents=True)
        (package / "portable_config").mkdir()
        (app / "Contents/Info.plist").write_bytes(plistlib.dumps(self.original_info))
        subprocess.run(
            ["/usr/bin/xcrun", "clang", "-arch", "x86_64", str(self.root / "recorder.c"),
             "-o", str(mac / "mpv")], check=True,
        )
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
        launcher = install_launcher(package, "intel")
        self.assertEqual(subprocess.check_output(
            ["/usr/bin/lipo", "-archs", str(launcher)], text=True
        ).strip(), "x86_64")


if __name__ == "__main__":
    unittest.main()
