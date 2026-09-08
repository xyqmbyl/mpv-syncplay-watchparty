"""Opt-in smoke test against the bundled real mpv JSON IPC server."""

import os
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mpv_syncplay as syncplay


RUN_REAL_MPV = os.environ.get("SYNCPLAY_RUN_MPV_INTEGRATION") == "1"


@unittest.skipUnless(sys.platform == "win32", "Windows named-pipe test")
@unittest.skipUnless(RUN_REAL_MPV, "set SYNCPLAY_RUN_MPV_INTEGRATION=1 to run")
class RealMpvIpcTests(unittest.TestCase):
    def test_typed_properties_round_trip_through_real_mpv(self):
        root = Path(__file__).resolve().parents[2]
        mpv = root / "mpv.exe"
        if not mpv.is_file():
            self.skipTest("bundled mpv.exe is unavailable")

        pipe = r"\\.\pipe\syncplay-ipc-test-%s" % uuid.uuid4().hex
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen(
            [
                str(mpv),
                "--no-config",
                "--idle=yes",
                "--vo=null",
                "--ao=null",
                "--no-terminal",
                "--input-ipc-server=%s" % pipe,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
        player = None
        try:
            player = syncplay.MpvPlayer(pipe, wait_seconds=10)
            player.set_pause(True)
            player.set_speed(1.125)
            player._set_property("cache", "yes", "启用 HTTP 缓存")
            player._set_property(
                "demuxer-readahead-secs", 20.0, "设置 HTTP 预读"
            )

            self.assertIs(player._simple_get("pause"), True)
            self.assertAlmostEqual(player._simple_get("speed"), 1.125, places=3)
            self.assertIs(player._simple_get("cache"), True)
            self.assertAlmostEqual(
                player._simple_get("demuxer-readahead-secs"), 20.0, places=3
            )
        finally:
            if player is not None:
                player.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            # Let mpv release the named pipe before another test process starts.
            time.sleep(0.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
