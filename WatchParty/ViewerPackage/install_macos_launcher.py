"""Install the Finder entry point in an extracted WatchParty macOS package.

Usage: python3 install_macos_launcher.py PACKAGE_DIR [--arch arm64|intel]
Requires macOS and Xcode Command Line Tools on the packaging machine only.
The upstream mpv code and bundled libraries are preserved. The executable's
ad-hoc signature is refreshed because it includes the changed Info.plist.
"""

import argparse
from pathlib import Path
import plistlib
import subprocess
import tempfile


LAUNCHER_NAME = "watchparty-launcher"
SOURCE = Path(__file__).resolve().parent / "templates/macos/watchparty-launcher.c"


def install_launcher(package, arch=None):
    package = Path(package).resolve()
    app = package / "mpv.app"
    executable = app / "Contents/MacOS/mpv"
    plist_path = app / "Contents/Info.plist"
    if not executable.is_file() or not (package / "portable_config").is_dir():
        raise ValueError("expected mpv.app and portable_config in the package directory")

    original_plist = plist_path.read_bytes()
    info = plistlib.loads(original_plist)
    if info.get("CFBundleExecutable") not in ("mpv", LAUNCHER_NAME):
        raise ValueError("refusing to replace an unknown CFBundleExecutable")
    architectures = subprocess.check_output(
        ["/usr/bin/lipo", "-archs", str(executable)], text=True
    ).split()
    if not architectures or any(item not in ("arm64", "x86_64") for item in architectures):
        raise ValueError("unsupported mpv architectures: " + repr(architectures))
    if arch:
        requested = "x86_64" if arch == "intel" else arch
        if architectures != [requested]:
            raise ValueError("requested architecture does not match the bundled mpv")

    launcher = app / "Contents/MacOS" / LAUNCHER_NAME
    # Compile before touching the application. The original main executable's
    # signature contains an Info.plist hash, so it also needs a new signature.
    # Do not use --deep signing: bundled upstream libraries need no changes.
    with tempfile.TemporaryDirectory(prefix="watchparty-launcher-") as work:
        compiled = Path(work) / LAUNCHER_NAME
        command = ["/usr/bin/xcrun", "clang", "-Os", "-Wall", "-Wextra", "-Werror"]
        for architecture in architectures:
            command.extend(["-arch", architecture])
        command.extend(["-mmacosx-version-min=11.0", str(SOURCE), "-o", str(compiled)])
        subprocess.run(command, check=True)
        launcher.write_bytes(compiled.read_bytes())
    launcher.chmod(0o755)
    info["CFBundleExecutable"] = LAUNCHER_NAME
    plist_path.write_bytes(plistlib.dumps(info, sort_keys=False))
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-",
         "--preserve-metadata=identifier,entitlements,flags", str(executable)],
        check=True,
    )
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True
    )
    return launcher


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path, help="extracted package directory")
    parser.add_argument("--arch", choices=("arm64", "intel", "x86_64"))
    args = parser.parse_args()
    print(install_launcher(args.package, args.arch))
