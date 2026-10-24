"""外挂字幕同步回归测试（房主广播 / 观看者 sub-add / 校验与撤销）。

只使用虚拟播放器、临时目录与进程内对象，绝不连接真实 mpv IPC、AList
或 Syncplay 服务器。
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mpv_syncplay as syncplay
from media_provider import MediaProvider


MEDIA_URL = "https://alist.example.test/d/media/movie.mkv"
SUB_URL = "https://alist.example.test/d/media/movie.zh.ass"
SUB_URL_2 = "https://alist.example.test/d/media/movie.en.srt"
FOREIGN_SUB_URL = "https://evil.example.com/d/media/movie.zh.ass"


def make_args(**overrides):
    values = {
        "server": "127.0.0.1:8995",
        "room": "test-room",
        "name": "viewer",
        "rewind": syncplay.REWIND_THRESHOLD,
        "verbose": False,
        "alist_enabled": True,
        "alist_server": "https://alist.example.test",
        "alist_root": None,
        "alist_local_root": None,
        "alist_virtual_root": "/media",
        "alist_map": [],
        "media_ready_timeout": 1.0,
        "http_readahead": 20.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class SubtitleRecordingPlayer:
    """观看者侧虚拟播放器：记录 sub-add / sub-remove 调用。"""

    def __init__(self, path=MEDIA_URL):
        self.pos = 0.0
        self.paused = True
        self.speed = 1.0
        self.base_speed = 1.0
        self.alive = True
        self.path = path
        self.file = {"name": "movie.mkv", "duration": 120.0, "size": 1000}
        self.command_log = []
        self.osd_log = []
        self.external = []
        self._next_id = 1
        self.add_error = None

    def get_state(self):
        return self.pos, self.paused

    def seek(self, position):
        self.pos = float(position)

    def set_pause(self, paused):
        self.paused = bool(paused)

    def set_speed(self, speed):
        self.speed = float(speed)

    def osd(self, text):
        self.osd_log.append(str(text))

    def file_info(self):
        return dict(self.file)

    def current_path(self):
        return self.path

    def load_remote(self, url, readahead=20.0):
        self.path = str(url)

    def is_media_ready(self, url):
        return syncplay.media_url_key(self.path) == syncplay.media_url_key(url)

    def is_alive(self):
        return self.alive

    def close(self):
        self.alive = False

    def external_sub_tracks(self):
        return [{"id": entry["id"], "path": entry["filename"],
                 "title": entry["title"], "lang": entry["lang"],
                 "selected": entry["selected"]}
                for entry in self.external]

    def sub_add(self, url, title=None, lang=None, selected=True):
        # 即使失败也先记录尝试，供重试上限测试统计调用次数。
        self.command_log.append(["sub-add", str(url),
                                 "select" if selected else "auto",
                                 str(title or ""), str(lang or "")])
        if self.add_error is not None:
            return self.add_error
        entry = {"id": self._next_id, "filename": str(url),
                 "title": str(title or ""), "lang": str(lang or ""),
                 "selected": bool(selected)}
        self._next_id += 1
        self.external.append(entry)
        return None

    def sub_remove(self, url):
        for index, entry in enumerate(self.external):
            if entry["filename"] == str(url):
                self.external.pop(index)
                self.command_log.append(["sub-remove", entry["id"]])
                return True
        return False


def make_viewer_client(player=None, provider=None):
    player = player or SubtitleRecordingPlayer()
    if provider is None:
        provider = MediaProvider(server="https://alist.example.test",
                                 mappings=[("C:\\Nowhere", "/media")],
                                 enabled=True)
    client = syncplay.SyncClient(make_args(), player,
                                 media_provider=provider)
    client.logged = True
    client._received_user_list = True
    client.server_isolate_rooms = True
    client.last_pos = player.pos
    client.last_ts = time.time()
    client.last_paused = player.paused
    client.filename = "movie.mkv"
    client.users = {"host": {"room": client.room, "file": "movie.mkv",
                             "file_info": {}, "ready": True,
                             "controller": False}}
    # 观看者已在播放房主分享的媒体
    client._observed_path = MEDIA_URL
    client._current_media_source = {
        "name": "movie.mkv", "media_url": MEDIA_URL, "source_type": "alist",
        "media_owner": "host",
    }
    client._current_media_scope = (client.args.server, client.room)
    client._current_media_confirmed = True
    client._media_owner = "host"
    return client


def subtitle_user_payload(subtitles=None, url=MEDIA_URL, username="host"):
    file_info = {
        "name": "movie.mkv",
        "duration": 120.0,
        "media_url": url,
        "source_type": "alist",
    }
    if subtitles is not None:
        file_info["subtitles"] = subtitles
    return {"user": {username: {"room": {"name": "test-room"},
                                "file": file_info}}}


class ValidatedSubtitleTracksTests(unittest.TestCase):
    def test_normalizes_and_keeps_valid_descriptors(self):
        value = [{"url": SUB_URL, "title": "简体中文", "lang": "zh",
                  "selected": True},
                 {"url": SUB_URL_2}]
        self.assertEqual(
            syncplay.validated_subtitle_tracks(value),
            [{"url": SUB_URL, "title": "简体中文", "lang": "zh",
              "selected": True},
             {"url": SUB_URL_2, "title": "", "lang": "",
              "selected": False}],
        )

    def test_dedupes_same_url_entries(self):
        value = [{"url": SUB_URL, "title": "a"},
                 {"url": SUB_URL, "title": "b"}]
        result = syncplay.validated_subtitle_tracks(value)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "a")

    def test_rejects_malformed_values(self):
        self.assertIsNone(syncplay.validated_subtitle_tracks(None))
        self.assertIsNone(syncplay.validated_subtitle_tracks("sub"))
        self.assertIsNone(syncplay.validated_subtitle_tracks(
            [{"title": "no url"}]))
        self.assertIsNone(syncplay.validated_subtitle_tracks(
            [{"url": "not a url"}]))
        self.assertIsNone(syncplay.validated_subtitle_tracks(
            [{"url": SUB_URL, "title": "x" * 200}]))
        self.assertIsNone(syncplay.validated_subtitle_tracks(
            [{"url": SUB_URL, "title": "bad\nnewline"}]))
        self.assertIsNone(syncplay.validated_subtitle_tracks(
            [{"url": SUB_URL}] * (syncplay.MAX_SUBTITLE_TRACKS + 1)))


class NormalizedRemoteFileTests(unittest.TestCase):
    def test_subtitles_ride_alist_media_payload(self):
        result = syncplay.normalized_remote_file({
            "name": "movie.mkv", "duration": 10, "size": 1,
            "media_url": MEDIA_URL, "source_type": "alist",
            "subtitles": [{"url": SUB_URL, "title": "zh", "selected": True}],
        })
        self.assertEqual(result.get("subtitles"),
                         [{"url": SUB_URL, "title": "zh", "lang": "",
                           "selected": True}])

    def test_subtitles_dropped_without_alist_media_or_when_invalid(self):
        plain = syncplay.normalized_remote_file(
            {"name": "movie.mkv", "duration": 10, "size": 1,
             "subtitles": [{"url": SUB_URL}]})
        self.assertNotIn("subtitles", plain)
        bad = syncplay.normalized_remote_file({
            "name": "movie.mkv", "duration": 10, "size": 1,
            "media_url": MEDIA_URL, "source_type": "alist",
            "subtitles": [{"url": SUB_URL, "oops": 1}, "str"],
        })
        self.assertNotIn("subtitles", bad)


class MiniServerCleanFileTests(unittest.TestCase):
    def test_server_forwards_validated_subtitles(self):
        cleaned = syncplay.MiniServer._clean_file({
            "name": "movie.mkv", "duration": 10, "size": 1,
            "media_url": MEDIA_URL, "source_type": "alist",
            "media_owner": "host",
            "subtitles": [{"url": SUB_URL, "title": "zh", "selected": True}],
        })
        self.assertEqual(cleaned.get("subtitles"),
                         [{"url": SUB_URL, "title": "zh", "lang": "",
                           "selected": True}])

    def test_server_strips_invalid_subtitles_but_keeps_media(self):
        cleaned = syncplay.MiniServer._clean_file({
            "name": "movie.mkv", "duration": 10, "size": 1,
            "media_url": MEDIA_URL, "source_type": "alist",
            "subtitles": [{"no_url": True}],
        })
        self.assertNotIn("subtitles", cleaned)
        self.assertEqual(cleaned.get("media_url"), MEDIA_URL)


class HostAnnounceTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name, "shared")
        root.mkdir()
        self.movie = root / "movie.mkv"
        self.movie.touch()
        self.sub_zh = root / "movie.zh.ass"
        self.sub_zh.touch()
        self.sub_en = root / "movie.en.srt"
        self.sub_en.touch()
        self.outside = Path(self._temp.name, "outside.ass")
        self.outside.touch()
        self.provider = MediaProvider(
            server="https://alist.example.test",
            mappings=[(str(root), "/media")],
            enabled=True,
        )
        self.player = syncplay.VirtualPlayer(paused=True,
                                             name=str(self.movie))
        self.player.path = str(self.movie)
        self.player.file = {"name": "movie.mkv", "duration": 120.0,
                            "size": 1000}
        self.client = syncplay.SyncClient(
            make_args(name="host", alist_root=str(root)), self.player,
            media_provider=self.provider)
        self.client.logged = True
        self.client._received_user_list = True
        self.client.server_isolate_rooms = True
        self.client.last_pos = 0.0
        self.client.last_ts = time.time()
        self.client.last_paused = True
        self.client.filename = "movie.mkv"
        self.sent_files = []

        def record_setting(setting):
            file_info = setting.get("file")
            if isinstance(file_info, dict):
                self.sent_files.append(json.loads(json.dumps(file_info)))

        self.client._send_set = record_setting
        self._old_poll = syncplay.POLL_INTERVAL
        syncplay.POLL_INTERVAL = 0.01
        self.thread = threading.Thread(target=self.client._watcher_loop,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.client.stop()
        self.thread.join(timeout=1.0)
        syncplay.POLL_INTERVAL = self._old_poll
        self._temp.cleanup()

    def test_host_announces_mapped_subtitles(self):
        self.player.local_external_subs = [
            {"path": str(self.sub_zh), "title": "movie.zh.ass",
             "lang": "zh", "selected": True},
        ]
        self.assertTrue(wait_for(lambda: self.sent_files))
        payload = self.sent_files[0]
        self.assertEqual(payload.get("media_url"), MEDIA_URL)
        self.assertEqual(payload.get("subtitles"),
                         [{"url": SUB_URL, "title": "movie.zh.ass",
                           "lang": "zh", "selected": True}])

    def test_subtitle_change_reannounces_without_media_switch(self):
        self.player.local_external_subs = [
            {"path": str(self.sub_zh), "title": "movie.zh.ass",
             "lang": "zh", "selected": True},
        ]
        self.assertTrue(wait_for(lambda: self.sent_files))
        self.player.local_external_subs.append(
            {"path": str(self.sub_en), "title": "movie.en.srt",
             "lang": "en", "selected": False})
        self.assertTrue(wait_for(lambda: len(self.sent_files) >= 2))
        self.assertEqual(
            sorted(item["url"] for item in self.sent_files[-1]["subtitles"]),
            sorted([SUB_URL, SUB_URL_2]))

    def test_unmapped_subtitles_are_not_announced(self):
        self.player.local_external_subs = [
            {"path": str(self.outside), "title": "outside.ass",
             "lang": "", "selected": True},
        ]
        self.assertTrue(wait_for(lambda: self.sent_files))
        self.assertNotIn("subtitles", self.sent_files[0])


class ViewerApplyTests(unittest.TestCase):
    def test_applies_announced_subtitle_and_dedupes(self):
        player = SubtitleRecordingPlayer()
        client = make_viewer_client(player)
        client._on_set(subtitle_user_payload(
            [{"url": SUB_URL, "title": "movie.zh.ass", "lang": "zh",
              "selected": True}]))
        self.assertTrue(client._sync_remote_subtitles())
        self.assertEqual(
            player.command_log,
            [["sub-add", SUB_URL, "select", "movie.zh.ass", "zh"]])
        self.assertFalse(client._sync_remote_subtitles())
        self.assertEqual(len(player.command_log), 1)
        self.assertEqual(player.external_sub_tracks()[0]["path"], SUB_URL)
        snapshot = client.status_snapshot()
        self.assertEqual(snapshot.get("synced_subtitles"), 1)

    def test_removes_subtitle_when_host_withdraws_it(self):
        player = SubtitleRecordingPlayer()
        client = make_viewer_client(player)
        client._on_set(subtitle_user_payload(
            [{"url": SUB_URL, "title": "zh", "selected": True}]))
        self.assertTrue(client._sync_remote_subtitles())
        client._on_set(subtitle_user_payload([]))
        self.assertTrue(client._sync_remote_subtitles())
        self.assertEqual(player.command_log[-1], ["sub-remove", 1])
        self.assertEqual(player.external, [])
        self.assertEqual(client.status_snapshot().get("synced_subtitles"), 0)

    def test_rejects_foreign_origin_subtitle_urls(self):
        player = SubtitleRecordingPlayer()
        client = make_viewer_client(player)
        client._on_set(subtitle_user_payload(
            [{"url": FOREIGN_SUB_URL, "title": "evil", "selected": True}]))
        self.assertFalse(client._sync_remote_subtitles())
        self.assertEqual(player.command_log, [])

    def test_disabled_without_room_isolation(self):
        player = SubtitleRecordingPlayer()
        client = make_viewer_client(player)
        client.server_isolate_rooms = None
        client._on_set(subtitle_user_payload(
            [{"url": SUB_URL, "title": "zh", "selected": True}]))
        self.assertFalse(client._sync_remote_subtitles())
        self.assertEqual(player.command_log, [])

    def test_retry_limit_for_failing_subtitle_url(self):
        player = SubtitleRecordingPlayer()
        player.add_error = "unable to load"
        client = make_viewer_client(player)
        client._on_set(subtitle_user_payload(
            [{"url": SUB_URL, "title": "zh", "selected": True}]))
        for _ in range(6):
            client._sync_remote_subtitles()
        add_attempts = [cmd for cmd in player.command_log
                        if cmd[0] == "sub-add"]
        self.assertEqual(len(add_attempts), 3)


class LoadingRefreshTests(unittest.TestCase):
    def test_late_subtitle_update_refreshes_queued_media_snapshot(self):
        player = SubtitleRecordingPlayer(path="C:\\old.mkv")
        client = make_viewer_client(player)
        client._observed_path = "C:\\old.mkv"
        client._current_media_source = None
        client._current_media_scope = None
        client._current_media_confirmed = False
        client._pending_media = {
            "url": MEDIA_URL, "owner": "host",
            "file": {"name": "movie.mkv", "duration": 120.0,
                     "media_url": MEDIA_URL, "source_type": "alist",
                     "media_owner": "host"},
            "queued": time.time(), "scope": (client.args.server, client.room),
        }
        client._on_set(subtitle_user_payload(
            [{"url": SUB_URL, "title": "zh", "selected": True}]))
        self.assertEqual(
            client._pending_media["file"].get("subtitles"),
            [{"url": SUB_URL, "title": "zh", "lang": "",
              "selected": True}])

    def test_finish_remote_media_carries_subtitles_into_source(self):
        player = SubtitleRecordingPlayer()
        client = make_viewer_client(player)
        client._observed_path = "C:\\old.mkv"
        client._current_media_source = None
        client._current_media_confirmed = False
        item = {
            "url": MEDIA_URL, "owner": "host",
            "file": {"name": "movie.mkv", "duration": 120.0,
                     "media_url": MEDIA_URL, "source_type": "alist",
                     "media_owner": "host",
                     "subtitles": [{"url": SUB_URL, "title": "zh",
                                    "selected": True}]},
            "started": time.time(), "deadline": time.time() + 30,
            "was_paused": True,
            "scope": (client.args.server, client.room),
        }
        client._loading_media = item
        client._send_set = lambda setting: None
        self.assertTrue(client._finish_remote_media(item))
        self.assertEqual(
            client._current_media_source.get("subtitles"),
            [{"url": SUB_URL, "title": "zh", "lang": "",
              "selected": True}])


class VirtualPlayerSubtitleTests(unittest.TestCase):
    def test_sub_add_and_remove_update_track_views(self):
        player = syncplay.VirtualPlayer(name="movie.mkv")
        self.assertEqual(player.external_sub_tracks(), [])
        self.assertIsNone(player.sub_add(SUB_URL, "zh", "zh", True))
        tracks = player.external_sub_tracks()
        self.assertEqual(tracks[0]["path"], SUB_URL)
        self.assertTrue(tracks[0]["selected"])
        self.assertTrue(player.sub_remove(SUB_URL))
        self.assertEqual(player.external_sub_tracks(), [])
        self.assertFalse(player.sub_remove(SUB_URL))


if __name__ == "__main__":
    unittest.main()
