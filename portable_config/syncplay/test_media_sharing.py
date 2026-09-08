"""Regression tests for Syncplay AList media sharing.

The tests use only virtual players, temporary files, and loopback sockets.  They
must never connect to the configured mpv IPC pipe, AList instance, or Syncplay
server used by the desktop application.
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mpv_syncplay as syncplay


MEDIA_URL = "https://alist.example.test/d/media/movie.mkv"


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


def wait_for(predicate, timeout=1.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class RecordingPlayer:
    """Small deterministic player used to observe SyncClient side effects."""

    def __init__(self, timeline=None, path="C:\\Videos\\old.mkv", paused=False):
        self.timeline = timeline if timeline is not None else []
        self.path = path
        self.pending_path = None
        self.pos = 0.0
        self.paused = bool(paused)
        self.speed = 1.0
        self.base_speed = 1.0
        self.alive = True
        self.file = {"name": os.path.basename(path), "duration": 120.0, "size": 1000}

    def get_state(self):
        return self.pos, self.paused

    def seek(self, position):
        self.pos = float(position)
        self.timeline.append(("seek", self.pos))

    def set_pause(self, paused):
        self.paused = bool(paused)
        self.timeline.append(("pause", self.paused))

    def set_speed(self, speed):
        self.speed = float(speed)
        self.timeline.append(("speed", self.speed))

    def osd(self, text):
        self.timeline.append(("osd", str(text)))

    def file_info(self):
        if self.pending_path is not None:
            return None
        return dict(self.file)

    def current_path(self):
        return self.path

    def load_remote(self, url, readahead=20):
        self.pending_path = str(url)
        self.timeline.append(("load_remote", str(url), float(readahead)))
        return True

    def complete_remote_load(self):
        if self.pending_path is None:
            raise AssertionError("no remote load is pending")
        self.path = self.pending_path
        self.pending_path = None
        name = os.path.basename(urlsplit(self.path).path) or "remote-media"
        self.file = {"name": name, "duration": 120.0, "size": 1000}
        self.pos = 0.0
        self.timeline.append(("file_loaded", self.path))

    def is_alive(self):
        return self.alive

    def close(self):
        self.alive = False


class ClientHarness:
    def __init__(self, player=None, media_provider=None, **arg_overrides):
        self.timeline = (player.timeline if player is not None and
                         hasattr(player, "timeline") else [])
        self.player = player or RecordingPlayer(self.timeline)
        self.client = syncplay.SyncClient(
            make_args(**arg_overrides), self.player, media_provider=media_provider
        )
        self.sent_settings = []

        def record_setting(setting):
            copied = json.loads(json.dumps(setting))
            self.sent_settings.append(copied)
            file_info = copied.get("file")
            if isinstance(file_info, dict):
                self.timeline.append(("file", file_info))
            ready = copied.get("ready")
            if isinstance(ready, dict):
                self.timeline.append(("ready", bool(ready.get("isReady"))))

        self.client._send_set = record_setting
        self.client.logged = True
        self.client._received_user_list = True
        self.client.server_isolate_rooms = True
        self.client.last_pos = self.player.pos
        self.client.last_ts = time.time()
        self.client.last_paused = self.player.paused
        info = self.player.file_info()
        self.client.filename = info.get("name") if info else None
        self.thread = None
        self._old_poll_interval = None

    def start_watcher(self):
        self._old_poll_interval = syncplay.POLL_INTERVAL
        syncplay.POLL_INTERVAL = 0.01
        self.thread = threading.Thread(target=self.client._watcher_loop, daemon=True)
        self.thread.start()

    def close(self):
        self.client.stop()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        if self._old_poll_interval is not None:
            syncplay.POLL_INTERVAL = self._old_poll_interval

    def ready_values(self):
        result = []
        for setting in self.sent_settings:
            ready = setting.get("ready")
            if isinstance(ready, dict):
                result.append(bool(ready.get("isReady")))
        return result


def media_user_payload(username="host", url=MEDIA_URL, media_owner=None):
    file_info = {
        "name": "movie.mkv",
        "duration": 120.0,
        "media_url": url,
        "source_type": "alist",
    }
    if media_owner is not None:
        file_info["media_owner"] = media_owner
    return {
        "user": {
            username: {
                "room": {"name": "test-room"},
                "file": file_info,
            }
        }
    }


def loopback_pair():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    client = socket.create_connection(listener.getsockname(), timeout=1.0)
    server, _address = listener.accept()
    listener.close()
    client.settimeout(1.0)
    server.settimeout(1.0)
    return server, client


def recv_json_line(sock):
    data = b""
    while b"\n" not in data:
        chunk = sock.recv(65536)
        if not chunk:
            raise AssertionError("socket closed before a JSON line was received")
        data += chunk
    line, _separator, _rest = data.partition(b"\n")
    return json.loads(line.strip().decode("utf-8"))


def recv_json_lines(sock, count):
    data = b""
    while data.count(b"\n") < count:
        chunk = sock.recv(65536)
        if not chunk:
            raise AssertionError("socket closed before all JSON lines were received")
        data += chunk
    return [json.loads(line.strip().decode("utf-8"))
            for line in data.splitlines()[:count]]


class MediaProviderTests(unittest.TestCase):
    def test_mapping_encodes_path_and_rejects_files_outside_root(self):
        try:
            from media_provider import MediaProvider
        except ImportError as exc:
            self.fail("media_provider.MediaProvider is required: %s" % exc)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir, "shared")
            nested = root / "Series Name"
            nested.mkdir(parents=True)
            movie = nested / "episode #1.mkv"
            movie.touch()
            outside = Path(temp_dir, "private.mkv")
            outside.touch()

            provider = MediaProvider(
                server="https://alist.example.test/",
                mappings=[(str(root), "/media")],
                enabled=True,
            )

            self.assertEqual(
                provider.local_to_url(str(movie)),
                {
                    "url": "https://alist.example.test/d/media/Series%20Name/episode%20%231.mkv",
                    "type": "alist",
                },
            )
            self.assertIsNone(provider.local_to_url(str(outside)))

    def test_disabled_provider_preserves_legacy_behavior(self):
        try:
            from media_provider import MediaProvider
        except ImportError as exc:
            self.fail("media_provider.MediaProvider is required: %s" % exc)

        provider = MediaProvider(
            server="https://alist.example.test",
            mappings=[("C:\\Movies", "/movies")],
            enabled=False,
        )
        self.assertIsNone(provider.local_to_url("C:\\Movies\\movie.mkv"))


class VirtualPlayerContractTests(unittest.TestCase):
    def test_virtual_player_exposes_remote_load_state(self):
        player = syncplay.VirtualPlayer(paused=True, name="old.mkv")
        self.assertTrue(callable(getattr(player, "current_path", None)))
        self.assertTrue(callable(getattr(player, "load_remote", None)))

        player.load_remote(MEDIA_URL, readahead=37)

        self.assertEqual(player.current_path(), MEDIA_URL)
        self.assertEqual(player.file_info().get("name"), "movie.mkv")

    def test_remote_load_command_order_includes_cache_and_readahead(self):
        player = syncplay.VirtualPlayer(paused=False, name="old.mkv")

        player.load_remote(MEDIA_URL, readahead=37)

        self.assertEqual(
            player.command_log[:4],
            [
                ["set_property", "pause", True],
                ["set_property", "cache", "yes"],
                ["set_property", "demuxer-readahead-secs", 37.0],
                ["loadfile", MEDIA_URL, "replace"],
            ],
        )
        self.assertTrue(player.paused)
        self.assertEqual(player.cache, "yes")
        self.assertEqual(player.readahead, 37.0)


class RemoteMediaLifecycleTests(unittest.TestCase):
    def test_non_isolated_server_disables_alist_extension_only(self):
        harness = ClientHarness()
        try:
            harness.client._on_hello({
                "username": "viewer",
                "features": {"isolateRooms": False},
            })
            harness.client._on_list({
                "test-room": {
                    "host": {
                        "file": media_user_payload()["user"]["host"]["file"],
                        "isReady": True,
                    }
                }
            })

            self.assertFalse(harness.client.server_isolate_rooms)
            self.assertIsNone(harness.client._pending_media)
            self.assertIn("已禁用 AList", harness.client.last_error)
            legacy = harness.client._build_file_payload(
                harness.player.file_info(), harness.player.current_path()
            )
            self.assertNotIn("media_url", legacy)
            self.assertNotIn("source_type", legacy)
        finally:
            harness.close()

    def test_remote_set_waits_for_initial_room_list(self):
        harness = ClientHarness()
        harness.client._received_user_list = False
        harness.start_watcher()
        try:
            harness.client._on_set(media_user_payload())
            time.sleep(0.05)

            self.assertIsNone(harness.client._pending_media)
            self.assertIsNone(harness.client._loading_media)
            self.assertFalse(any(event[0] == "load_remote"
                                 for event in harness.timeline))

            file_info = media_user_payload()["user"]["host"]["file"]
            harness.client._on_list({
                "test-room": {"host": {"file": file_info, "isReady": True}}
            })
            self.assertTrue(wait_for(
                lambda: harness.player.pending_path == MEDIA_URL
            ))
        finally:
            harness.close()

    def test_server_assigned_name_updates_identity_before_own_file_echo(self):
        harness = ClientHarness(name="requested-name")
        try:
            harness.client._on_hello({"username": "assigned-name"})

            self.assertEqual(harness.client.name, "assigned-name")
            self.assertEqual(harness.client.args.name, "assigned-name")

            harness.client._on_set(media_user_payload(username="assigned-name"))

            self.assertIsNone(harness.client._pending_media)
            self.assertIsNone(harness.client._loading_media)
            self.assertEqual(harness.ready_values(), [])
            self.assertFalse(any(event[0] == "load_remote"
                                 for event in harness.timeline))
        finally:
            harness.close()

    def test_media_owner_blocks_takeover_until_the_owner_leaves(self):
        first_url = "https://alist.example.test/d/media/first.mkv"
        second_url = "https://alist.example.test/d/media/second.mkv"
        harness = ClientHarness()
        try:
            harness.client._on_set(media_user_payload("owner-a", first_url))
            self.assertEqual(harness.client._media_owner, "owner-a")
            self.assertEqual(harness.client._pending_media["url"], first_url)

            harness.client._on_set(media_user_payload("owner-b", second_url))
            self.assertEqual(harness.client._media_owner, "owner-a")
            self.assertEqual(harness.client._pending_media["url"], first_url)

            harness.client._on_set({
                "user": {"owner-a": {"event": {"left": True}}}
            })
            harness.client._on_set(media_user_payload("owner-b", second_url))

            self.assertEqual(harness.client._media_owner, "owner-b")
            self.assertEqual(harness.client._pending_media["url"], second_url)
        finally:
            harness.close()

    def test_initial_list_keeps_original_owner_when_viewer_echo_sorts_first(self):
        harness = ClientHarness(name="local-viewer")
        try:
            file_info = media_user_payload(
                username="zzz-host", media_owner="zzz-host"
            )["user"]["zzz-host"]["file"]
            harness.client._on_list({
                "test-room": {
                    "aaa-viewer": {"file": dict(file_info), "isReady": True},
                    "zzz-host": {"file": dict(file_info), "isReady": True},
                }
            })

            self.assertEqual(harness.client._media_owner, "zzz-host")
            self.assertEqual(harness.client._pending_media["owner"], "zzz-host")
            self.assertEqual(harness.client._pending_media["url"], MEDIA_URL)
        finally:
            harness.close()

    def test_relay_cannot_claim_an_owner_without_a_matching_self_claim(self):
        harness = ClientHarness(name="local-viewer")
        try:
            victim_file = media_user_payload(username="victim")["user"]["victim"]["file"]
            harness.client.users = {
                "victim": {
                    "room": "test-room",
                    "file": "movie.mkv",
                    "file_info": victim_file,
                    "ready": True,
                }
            }

            harness.client._on_set(media_user_payload(
                username="relay", media_owner="victim"
            ))

            self.assertIsNone(harness.client._media_owner)
            self.assertIsNone(harness.client._pending_media)
            self.assertEqual(harness.ready_values(), [])
        finally:
            harness.close()

    def test_remote_alist_source_reannounces_only_after_owner_confirmation(self):
        player = RecordingPlayer(path=MEDIA_URL, paused=True)
        harness = ClientHarness(player=player)
        try:
            harness.client._current_media_source = {
                "name": "movie.mkv",
                "media_url": MEDIA_URL,
                "source_type": "alist",
                "media_owner": "host",
            }
            harness.client._on_disconnect()
            harness.client._on_hello({
                "username": "viewer",
                "features": {"isolateRooms": True},
            })
            harness.start_watcher()

            self.assertTrue(wait_for(lambda: any(
                isinstance(setting.get("file"), dict)
                for setting in harness.sent_settings
            )))
            initial_payload = next(
                setting["file"] for setting in harness.sent_settings
                if isinstance(setting.get("file"), dict)
            )
            self.assertNotIn("media_url", initial_payload)
            self.assertFalse(harness.client._current_media_confirmed)

            owner_file = media_user_payload(
                username="host", media_owner="host"
            )["user"]["host"]["file"]
            harness.client._on_list({
                "test-room": {
                    "host": {"file": owner_file, "isReady": True},
                    "viewer": {"file": initial_payload, "isReady": True},
                }
            })
            self.assertTrue(wait_for(lambda: any(
                isinstance(setting.get("file"), dict) and
                setting["file"].get("media_url") == MEDIA_URL
                for setting in harness.sent_settings
            )))
            payload = next(
                setting["file"] for setting in reversed(harness.sent_settings)
                if isinstance(setting.get("file"), dict) and
                setting["file"].get("media_url") == MEDIA_URL
            )
            self.assertEqual(payload.get("media_url"), MEDIA_URL)
            self.assertEqual(payload.get("source_type"), "alist")
            self.assertEqual(payload.get("media_owner"), "host")
            self.assertIsNotNone(harness.client._current_media_source)
            self.assertTrue(harness.client._current_media_confirmed)
        finally:
            harness.close()

    def test_host_reconnect_does_not_load_its_own_url_from_a_viewer_echo(self):
        from media_provider import MediaProvider

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir, "shared")
            movie = root / "movie.mkv"
            root.mkdir()
            movie.touch()
            player = RecordingPlayer(path=str(movie), paused=True)
            provider = MediaProvider(
                server="https://alist.example.test",
                mappings=[(str(root), "/media")],
                enabled=True,
            )
            harness = ClientHarness(
                player=player,
                media_provider=provider,
                name="host",
                alist_enabled=True,
            )
            try:
                harness.client._observed_path = str(movie)
                echo = media_user_payload(
                    username="viewer", media_owner="host"
                )["user"]["viewer"]["file"]
                harness.client._on_list({
                    "test-room": {
                        "aaa-other-source": {
                            "file": media_user_payload(
                                username="aaa-other-source",
                                url="https://alist.example.test/d/media/other.mkv",
                                media_owner="aaa-other-source",
                            )["user"]["aaa-other-source"]["file"],
                            "isReady": True,
                        },
                        "host": {"file": None, "isReady": True},
                        "viewer": {"file": echo, "isReady": True},
                    }
                })

                self.assertEqual(harness.client._media_owner, "host")
                self.assertEqual(harness.client._hosted_media_url, MEDIA_URL)
                self.assertIsNone(harness.client._pending_media)
                self.assertIsNone(harness.client._loading_media)
                self.assertFalse(any(event[0] == "load_remote"
                                     for event in harness.timeline))
                self.assertEqual(player.path, str(movie))
            finally:
                harness.close()

    def test_remote_load_failure_forces_ready_false(self):
        harness = ClientHarness()
        item = {
            "url": MEDIA_URL,
            "owner": "host",
            "scope": (harness.client.args.server, harness.client.room),
        }
        try:
            harness.client.ready = False
            harness.client._loading_media = item

            harness.client._fail_remote_media(item, "simulated load error")

            self.assertFalse(harness.client.ready)
            self.assertEqual(harness.ready_values(), [False])
            self.assertIsNone(harness.client._loading_media)
            self.assertEqual(harness.client._failed_media["url"], MEDIA_URL)
            self.assertIn("simulated load error", harness.client.last_error)
        finally:
            harness.close()

    def test_media_url_key_preserves_encoded_path_separators(self):
        encoded_upper = "https://alist.example.test/d/media/dir%2Fmovie.mkv"
        encoded_lower = "https://alist.example.test/d/media/dir%2fmovie.mkv"
        literal_slash = "https://alist.example.test/d/media/dir/movie.mkv"

        self.assertEqual(
            syncplay.media_url_key(encoded_upper),
            syncplay.media_url_key(encoded_lower),
        )
        self.assertNotEqual(
            syncplay.media_url_key(encoded_upper),
            syncplay.media_url_key(literal_slash),
        )

    def test_legacy_file_payload_never_loads_a_remote_media(self):
        harness = ClientHarness()
        harness.start_watcher()
        try:
            harness.client._on_set({
                "user": {
                    "host": {
                        "room": {"name": "test-room"},
                        "file": {"name": "movie.mkv", "duration": 120.0},
                    }
                }
            })
            time.sleep(0.1)
            loads = [event for event in harness.timeline if event[0] == "load_remote"]
            self.assertEqual(loads, [])
        finally:
            harness.close()

    def test_alist_payload_is_queued_and_ready_only_after_load(self):
        harness = ClientHarness(http_readahead=33.0)
        harness.start_watcher()
        try:
            harness.client._on_set(media_user_payload())
            self.assertTrue(
                wait_for(lambda: harness.player.pending_path == MEDIA_URL),
                "AList payload was not sent to player.load_remote",
            )

            self.assertEqual(harness.ready_values(), [False])
            load_index = next(i for i, event in enumerate(harness.timeline)
                              if event[0] == "load_remote")
            not_ready_index = harness.timeline.index(("ready", False))
            self.assertLess(not_ready_index, load_index)
            self.assertEqual(
                harness.timeline[load_index],
                ("load_remote", MEDIA_URL, 33.0),
            )

            harness.player.complete_remote_load()
            self.assertTrue(
                wait_for(lambda: harness.ready_values() == [False, True]),
                "client did not announce ready=true after the remote file loaded",
            )
            remote_file_index = next(
                i for i, event in enumerate(harness.timeline)
                if event[0] == "file" and event[1].get("media_url") == MEDIA_URL
            )
            ready_index = harness.timeline.index(("ready", True))
            self.assertLess(
                remote_file_index,
                ready_index,
                "the loaded file identity must be announced before ready=true",
            )
        finally:
            harness.close()

    def test_owner_withdrawal_cancels_pending_media_without_ready_true(self):
        harness = ClientHarness()
        try:
            harness.client._on_set(media_user_payload())
            pending = harness.client._pending_media

            harness.client._on_set({
                "user": {
                    "host": {
                        "room": {"name": "test-room"},
                        "file": {"name": "local.mkv", "duration": 120.0,
                                 "size": 1000},
                    }
                }
            })

            self.assertTrue(pending.get("cancelled"))
            self.assertIsNone(harness.client._pending_media)
            self.assertIsNone(harness.client._loading_media)
            self.assertIsNone(harness.client._media_owner)
            self.assertFalse(harness.client.ready)
            self.assertEqual(harness.ready_values(), [False])
        finally:
            harness.close()

    def test_owner_withdrawal_cancels_loading_without_stale_ready(self):
        harness = ClientHarness()
        try:
            harness.client._on_set(media_user_payload())
            old_item = harness.client._pending_media
            old_item["was_paused"] = False
            with harness.client._lock:
                harness.client._pending_media = None
                harness.client._loading_media = old_item
                harness.client.global_pos = 42.0
                harness.client.global_paused = False

            harness.client._on_set({
                "user": {
                    "host": {
                        "room": {"name": "test-room"},
                        "file": {"name": "local.mkv", "duration": 120.0,
                                 "size": 1000},
                    }
                }
            })
            harness.player.path = MEDIA_URL
            harness.player.file = {
                "name": "movie.mkv", "duration": 120.0, "size": 1000
            }

            self.assertTrue(old_item.get("cancelled"))
            self.assertIsNone(harness.client._pending_media)
            self.assertIsNone(harness.client._loading_media)
            self.assertIsNotNone(harness.client._cancelled_media_restore)
            self.assertFalse(harness.client.ready)
            self.assertFalse(harness.client._finish_remote_media(old_item))
            self.assertNotIn(True, harness.ready_values())
            self.assertFalse(any(
                isinstance(setting.get("file"), dict) and
                setting["file"].get("media_url") == MEDIA_URL
                for setting in harness.sent_settings
            ))
        finally:
            harness.close()

    def test_owner_switch_invalidates_old_loading_completion(self):
        second_url = "https://alist.example.test/d/media/second.mkv"
        harness = ClientHarness()
        try:
            harness.client._on_set(media_user_payload(url=MEDIA_URL))
            old_item = harness.client._pending_media
            with harness.client._lock:
                harness.client._pending_media = None
                harness.client._loading_media = old_item

            harness.client._on_set(media_user_payload(url=second_url))
            harness.player.path = MEDIA_URL
            harness.player.file = {
                "name": "movie.mkv", "duration": 120.0, "size": 1000
            }

            self.assertFalse(harness.client._finish_remote_media(old_item))
            self.assertTrue(old_item.get("cancelled"))
            self.assertEqual(harness.client._pending_media["url"], second_url)
            self.assertFalse(harness.client.ready)
            self.assertEqual(harness.ready_values(), [False, False])
            self.assertFalse(any(
                isinstance(setting.get("file"), dict) and
                setting["file"].get("media_url") == MEDIA_URL
                for setting in harness.sent_settings
            ))
        finally:
            harness.close()

    def test_finish_and_owner_switch_leave_new_media_not_ready(self):
        second_url = "https://alist.example.test/d/media/second.mkv"
        harness = ClientHarness()
        publication_started = threading.Event()
        release_publication = threading.Event()
        base_send = harness.client._send_set

        def blocking_send(setting):
            file_info = setting.get("file")
            if (isinstance(file_info, dict) and
                    file_info.get("media_url") == MEDIA_URL):
                publication_started.set()
                release_publication.wait(timeout=1.0)
            base_send(setting)

        harness.client._send_set = blocking_send
        try:
            harness.client._on_set(media_user_payload(url=MEDIA_URL))
            old_item = harness.client._pending_media
            with harness.client._lock:
                harness.client._pending_media = None
                harness.client._loading_media = old_item
                harness.client.global_pos = 12.0
                harness.client.global_paused = True
            harness.player.path = MEDIA_URL
            harness.player.file = {
                "name": "movie.mkv", "duration": 120.0, "size": 1000
            }

            finish_thread = threading.Thread(
                target=harness.client._finish_remote_media,
                args=(old_item,), daemon=True,
            )
            finish_thread.start()
            self.assertTrue(publication_started.wait(timeout=1.0))

            switch_thread = threading.Thread(
                target=harness.client._on_set,
                args=(media_user_payload(url=second_url),), daemon=True,
            )
            switch_thread.start()
            time.sleep(0.03)
            self.assertTrue(switch_thread.is_alive())

            release_publication.set()
            finish_thread.join(timeout=1.0)
            switch_thread.join(timeout=1.0)
            self.assertFalse(finish_thread.is_alive())
            self.assertFalse(switch_thread.is_alive())
            self.assertEqual(harness.client._pending_media["url"], second_url)
            self.assertFalse(harness.client.ready)
            self.assertEqual(harness.ready_values()[-1], False)
        finally:
            release_publication.set()
            harness.close()

    def test_assigned_room_change_cancels_finish_in_progress(self):
        entered_file_info = threading.Event()
        release_file_info = threading.Event()

        class BlockingPlayer(RecordingPlayer):
            block_file_info = False

            def file_info(self):
                if self.block_file_info:
                    entered_file_info.set()
                    release_file_info.wait(timeout=1.0)
                return super().file_info()

        player = BlockingPlayer(path="C:\\Videos\\old.mkv", paused=False)
        harness = ClientHarness(player=player)
        try:
            harness.client._on_set(media_user_payload())
            old_item = harness.client._pending_media
            old_item["was_paused"] = False
            with harness.client._lock:
                harness.client._pending_media = None
                harness.client._loading_media = old_item
            player.path = MEDIA_URL
            player.file = {
                "name": "movie.mkv", "duration": 120.0, "size": 1000
            }
            player.block_file_info = True

            worker = threading.Thread(
                target=harness.client._finish_remote_media,
                args=(old_item,), daemon=True,
            )
            worker.start()
            self.assertTrue(entered_file_info.wait(timeout=1.0))

            harness.client._on_hello({
                "username": "viewer",
                "room": {"name": "assigned-room"},
                "features": {"isolateRooms": True},
            })
            release_file_info.set()
            worker.join(timeout=1.0)

            self.assertFalse(worker.is_alive())
            self.assertTrue(old_item.get("cancelled"))
            self.assertIsNone(harness.client._loading_media)
            self.assertIsNone(harness.client._pending_media)
            self.assertEqual(harness.client.room, "assigned-room")
            self.assertNotIn(True, harness.ready_values())
            self.assertFalse(any(
                isinstance(setting.get("file"), dict) and
                setting["file"].get("media_url") == MEDIA_URL
                for setting in harness.sent_settings
            ))
            self.assertFalse(player.paused)
        finally:
            release_file_info.set()
            harness.close()

    def test_server_or_room_change_clears_remote_source_scope(self):
        changes = (
            {"room": "other-room"},
            {"server": "127.0.0.2:8995"},
        )
        for change in changes:
            with self.subTest(change=change):
                player = RecordingPlayer(path=MEDIA_URL, paused=True)
                harness = ClientHarness(player=player)
                try:
                    harness.client._current_media_source = {
                        "name": "movie.mkv",
                        "media_url": MEDIA_URL,
                        "source_type": "alist",
                        "media_owner": "host",
                    }
                    harness.client._current_media_scope = (
                        harness.client.args.server, harness.client.room
                    )
                    harness.client._current_media_confirmed = True
                    with mock.patch.object(harness.client, "request_reconnect"):
                        harness.client.update_settings(**change)

                    harness.client.server_isolate_rooms = True
                    payload = harness.client._build_file_payload(
                        player.file_info(), player.current_path()
                    )
                    self.assertIsNone(harness.client._current_media_source)
                    self.assertIsNone(harness.client._current_media_scope)
                    self.assertFalse(harness.client._current_media_confirmed)
                    self.assertNotIn("media_url", payload)
                    self.assertNotIn("source_type", payload)
                finally:
                    harness.close()

    def test_settings_change_cancels_all_media_transitions(self):
        player = RecordingPlayer(path=MEDIA_URL, paused=True)
        harness = ClientHarness(player=player)
        pending = {
            "url": MEDIA_URL,
            "scope": (harness.client.args.server, harness.client.room),
        }
        loading = {
            "url": MEDIA_URL,
            "scope": (harness.client.args.server, harness.client.room),
            "was_paused": False,
        }
        try:
            with harness.client._lock:
                harness.client._pending_media = pending
                harness.client._loading_media = loading
                harness.client._media_gate = {
                    "url": MEDIA_URL, "resume": True, "waiters": {"peer"}
                }
                harness.client._media_owner = "host"
                harness.client._hosted_media_url = MEDIA_URL
                harness.client.ready = False
            with mock.patch.object(harness.client, "request_reconnect"):
                changed = harness.client.update_settings(room="new-room")

            self.assertEqual(changed, {"room"})
            self.assertTrue(pending.get("cancelled"))
            self.assertTrue(loading.get("cancelled"))
            self.assertIsNone(harness.client._pending_media)
            self.assertIsNone(harness.client._loading_media)
            self.assertIsNone(harness.client._media_gate)
            self.assertIsNone(harness.client._media_owner)
            self.assertIsNone(harness.client._hosted_media_url)
            self.assertFalse(harness.client.logged)
            self.assertTrue(harness.client.ready)
            self.assertFalse(player.paused)
            self.assertFalse(harness.client._finish_remote_media(loading))
        finally:
            harness.close()

    def test_server_assigned_room_updates_client_room(self):
        harness = ClientHarness(room="requested-room")
        try:
            harness.client._on_hello({
                "username": "viewer",
                "room": {"name": "assigned-room"},
                "features": {"isolateRooms": True},
            })

            self.assertEqual(harness.client.room, "assigned-room")
            self.assertEqual(harness.client.args.room, "assigned-room")
        finally:
            harness.close()

    def test_initial_room_list_can_queue_alist_media(self):
        harness = ClientHarness()
        harness.start_watcher()
        try:
            file_info = media_user_payload()["user"]["host"]["file"]
            harness.client._on_list({
                "test-room": {
                    "host": {"file": file_info, "isReady": True},
                    "viewer": {"file": {}, "isReady": False},
                }
            })
            self.assertTrue(
                wait_for(lambda: harness.player.pending_path == MEDIA_URL),
                "AList media present in the initial List response was ignored",
            )
        finally:
            harness.close()

    def test_own_file_echo_does_not_replace_the_hosts_local_file(self):
        harness = ClientHarness(name="host")
        harness.start_watcher()
        try:
            harness.client._on_set(media_user_payload(username="host"))
            time.sleep(0.1)
            self.assertIsNone(harness.player.pending_path)
            self.assertFalse(any(event[0] == "load_remote" for event in harness.timeline))
        finally:
            harness.close()

    def test_cross_room_and_untrusted_urls_are_not_queued(self):
        cases = [
            ("other-room", MEDIA_URL),
            ("test-room", "file:///C:/Movies/movie.mkv"),
            ("test-room", "ftp://alist.example.test/media/movie.mkv"),
            ("test-room", "https://admin:secret@alist.example.test/d/media/movie.mkv"),
            ("test-room", "https://cdn.example.test/d/media/movie.mkv"),
            ("test-room", "https://alist.example.test/media/movie.mkv"),
            ("test-room", "https://alist.example.test/api/fs/get?path=/media/movie.mkv"),
            ("test-room", "https://alist.example.test:8443/d/media/movie.mkv"),
            ("test-room", "https://alist.example.test/d/%252e%252e/api/fs/get"),
            ("test-room", "https://alist.example.test/d/%255c..%255capi/file"),
            ("test-room", "https://alist.example.test/d/%2500api/file"),
            ("test-room", "https://alist.example.test/d/" + chr(0xD800) + ".mkv"),
        ]
        for room, url in cases:
            with self.subTest(room=room, url=url):
                harness = ClientHarness()
                try:
                    payload = media_user_payload(url=url)
                    payload["user"]["host"]["room"]["name"] = room
                    harness.client._on_set(payload)

                    self.assertIsNone(harness.client._pending_media)
                    self.assertIsNone(harness.client._loading_media)
                    self.assertEqual(harness.ready_values(), [])
                    self.assertFalse(
                        any(event[0] == "load_remote" for event in harness.timeline)
                    )
                finally:
                    harness.close()


class HostPublishingTests(unittest.TestCase):
    def test_local_mapping_publishes_public_url_without_local_path(self):
        from media_provider import MediaProvider

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir, "shared")
            movie = root / "Feature Film" / "movie one.mkv"
            movie.parent.mkdir(parents=True)
            movie.touch()
            timeline = []
            player = RecordingPlayer(timeline, path=str(movie), paused=True)
            provider = MediaProvider(
                server="https://alist.example.test",
                mappings=[(str(root), "/media")],
                enabled=True,
            )
            harness = ClientHarness(
                player=player,
                media_provider=provider,
                name="host",
                alist_enabled=True,
            )
            # A real server answers List before the host publishes its file;
            # the client needs that snapshot to build a complete ready gate.
            harness.client._on_list({"test-room": {}})
            harness.start_watcher()
            try:
                self.assertTrue(
                    wait_for(lambda: any("file" in item for item in harness.sent_settings)),
                    "host did not publish the mapped local media",
                )
                file_payload = next(
                    item["file"] for item in harness.sent_settings if "file" in item
                )
                self.assertEqual(file_payload["source_type"], "alist")
                self.assertEqual(file_payload["media_owner"], "host")
                self.assertEqual(
                    file_payload["media_url"],
                    "https://alist.example.test/d/media/Feature%20Film/movie%20one.mkv",
                )
                serialized = json.dumps(file_payload, ensure_ascii=False)
                self.assertNotIn(str(root), serialized)
                self.assertNotIn(str(movie), serialized)
                self.assertNotIn("path", file_payload)
            finally:
                harness.close()

    def test_remote_file_payload_keeps_official_fields_when_metadata_is_missing(self):
        harness = ClientHarness()
        try:
            harness.player.path = MEDIA_URL
            harness.client._current_media_source = {
                "media_url": MEDIA_URL,
                "source_type": "alist",
                "media_owner": "host",
            }
            harness.client._current_media_scope = (
                harness.client.args.server, harness.client.room
            )
            harness.client._current_media_confirmed = True

            payload = harness.client._build_file_payload(
                {"name": "movie.mkv"}, MEDIA_URL
            )

            self.assertEqual(payload["name"], "movie.mkv")
            self.assertEqual(payload["duration"], 0.0)
            self.assertEqual(payload["size"], 0)
            self.assertEqual(payload["media_url"], MEDIA_URL)
            self.assertEqual(payload["source_type"], "alist")
        finally:
            harness.close()


class ReadyGateTests(unittest.TestCase):
    @staticmethod
    def _user(room="test-room", ready=False, url=None):
        file_info = {"name": "movie.mkv"}
        if url is not None:
            file_info.update({"media_url": url, "source_type": "alist"})
        return {
            "room": room,
            "file": file_info["name"],
            "file_info": file_info,
            "ready": ready,
        }

    def test_gate_requires_matching_url_and_ready_before_resume(self):
        harness = ClientHarness(name="host")
        try:
            harness.client.users = {
                "viewer": self._user(ready=False, url="https://alist.example.test/d/media/old.mkv"),
                "elsewhere": self._user(room="other-room", ready=False, url=None),
            }

            self.assertTrue(harness.client._begin_media_gate(MEDIA_URL, 15.0, False))
            self.assertTrue(harness.player.paused)
            self.assertEqual(harness.client._media_gate["waiters"], {"viewer"})
            self.assertTrue(harness.client._check_media_gate())

            harness.client._on_set({
                "user": {
                    "viewer": {
                        "room": {"name": "test-room"},
                        "file": media_user_payload()["user"]["host"]["file"],
                    }
                }
            })
            self.assertTrue(
                harness.client._check_media_gate(),
                "matching media without ready=true released the gate",
            )

            harness.client._on_set({
                "ready": {"username": "viewer", "isReady": True,
                          "manuallyInitiated": False}
            })
            self.assertFalse(harness.client._check_media_gate())
            self.assertFalse(harness.player.paused)
            pause_events = [event for event in harness.timeline if event[0] == "pause"]
            self.assertEqual(pause_events, [("pause", True), ("pause", False)])
        finally:
            harness.close()

    def test_gate_timeout_resumes_without_matching_ready(self):
        harness = ClientHarness(name="host")
        try:
            harness.client.users = {"viewer": self._user(ready=False, url=None)}
            self.assertTrue(harness.client._begin_media_gate(MEDIA_URL, 15.0, False))
            harness.client._media_gate["deadline"] = time.time() - 1.0

            self.assertFalse(harness.client._check_media_gate())
            self.assertIsNone(harness.client._media_gate)
            self.assertFalse(harness.player.paused)
        finally:
            harness.close()

    def test_zero_timeout_waits_indefinitely_for_ready(self):
        harness = ClientHarness(name="host", media_ready_timeout=0)
        try:
            harness.client.users = {"viewer": self._user(ready=False, url=None)}
            self.assertTrue(harness.client._begin_media_gate(MEDIA_URL, 15.0, False))
            self.assertIsNone(harness.client._media_gate["deadline"])

            future = time.time() + 86400.0
            with mock.patch.object(syncplay.time, "time", return_value=future):
                self.assertTrue(harness.client._check_media_gate())

            self.assertIsNotNone(harness.client._media_gate)
            self.assertTrue(harness.player.paused)
            self.assertFalse(str(harness.client.last_error or "").startswith(
                "等待成员加载超时"
            ))
        finally:
            harness.close()

    def test_joined_room_member_is_added_to_active_gate(self):
        harness = ClientHarness(name="host")
        try:
            harness.client.users = {"viewer": self._user(ready=False, url=None)}
            self.assertTrue(harness.client._begin_media_gate(MEDIA_URL, 15.0, False))

            harness.client._on_set({
                "user": {
                    "late-viewer": {
                        "room": {"name": "test-room"},
                        "event": {"joined": True},
                    }
                }
            })

            self.assertEqual(
                harness.client._media_gate["waiters"],
                {"viewer", "late-viewer"},
            )
            self.assertEqual(
                harness.client._media_gate["waiting"],
                ["late-viewer", "viewer"],
            )
            self.assertTrue(harness.client._check_media_gate())
        finally:
            harness.close()

    def test_room_switch_without_joined_event_adds_member_to_active_gate(self):
        harness = ClientHarness(name="host")
        try:
            harness.client.users = {
                "viewer": self._user(room="other-room", ready=False, url=None)
            }
            self.assertFalse(
                harness.client._begin_media_gate(MEDIA_URL, 15.0, False)
            )
            harness.client.users["waiting-viewer"] = self._user(
                ready=False, url=None
            )
            self.assertTrue(
                harness.client._begin_media_gate(MEDIA_URL, 15.0, False)
            )

            harness.client._on_set({
                "user": {
                    "viewer": {"room": {"name": "test-room"}}
                }
            })

            self.assertIn("viewer", harness.client._media_gate["waiters"])
            self.assertIn("viewer", harness.client._media_gate["waiting"])
            self.assertTrue(harness.client._check_media_gate())
        finally:
            harness.close()

    def test_join_event_in_another_room_does_not_add_a_waiter(self):
        harness = ClientHarness(name="host")
        try:
            harness.client.users = {
                "viewer": self._user(ready=False, url=None)
            }
            self.assertTrue(
                harness.client._begin_media_gate(MEDIA_URL, 15.0, False)
            )

            harness.client._on_set({
                "user": {
                    "elsewhere": {
                        "room": {"name": "other-room"},
                        "event": {"joined": True},
                    }
                }
            })

            self.assertEqual(harness.client._media_gate["waiters"], {"viewer"})
            self.assertNotIn("elsewhere", harness.client._media_gate["waiting"])
        finally:
            harness.close()

    def test_list_rebuild_does_not_expose_an_empty_user_snapshot(self):
        entered_items = threading.Event()
        release_items = threading.Event()

        class BlockingRooms(dict):
            def items(self):
                entered_items.set()
                release_items.wait(timeout=1.0)
                return super().items()

        harness = ClientHarness(name="host")
        try:
            harness.client.users = {
                "viewer": self._user(ready=False, url=None)
            }
            self.assertTrue(
                harness.client._begin_media_gate(MEDIA_URL, 15.0, False)
            )
            rooms = BlockingRooms({
                "test-room": {
                    "viewer": {"file": None, "isReady": False}
                }
            })
            worker = threading.Thread(
                target=harness.client._on_list, args=(rooms,), daemon=True
            )
            worker.start()
            self.assertTrue(entered_items.wait(timeout=1.0))

            self.assertTrue(harness.client._check_media_gate())
            self.assertIsNotNone(harness.client._media_gate)
            self.assertTrue(harness.player.paused)

            release_items.set()
            worker.join(timeout=1.0)
            self.assertFalse(worker.is_alive())
            self.assertIn("viewer", harness.client.users)
        finally:
            release_items.set()
            harness.close()

    def test_switching_away_from_alist_clears_gate_and_resumes(self):
        targets = (
            ("plain-http", "https://video.example.test/plain.mkv"),
            ("local-file", "C:\\Videos\\plain.mkv"),
        )
        for label, target_path in targets:
            with self.subTest(target=label):
                player = RecordingPlayer(paused=False)
                harness = ClientHarness(player=player, name="host")
                try:
                    harness.client.users = {
                        "viewer": self._user(ready=False, url=None)
                    }
                    self.assertTrue(
                        harness.client._begin_media_gate(MEDIA_URL, 15.0, False)
                    )
                    harness.client._current_media_source = {
                        "media_url": MEDIA_URL,
                        "source_type": "alist",
                    }
                    player.path = target_path
                    player.file = {
                        "name": "plain.mkv",
                        "duration": 120.0,
                        "size": 1000,
                    }
                    harness.start_watcher()

                    self.assertTrue(
                        wait_for(lambda: harness.client._media_gate is None),
                        "switching media did not cancel the old ready gate",
                    )
                    self.assertTrue(
                        wait_for(lambda: any("file" in setting
                                             for setting in harness.sent_settings)),
                        "the replacement media was not announced",
                    )

                    self.assertFalse(player.paused)
                    self.assertIsNone(harness.client._hosted_media_url)
                    self.assertIsNone(harness.client._media_owner)
                    self.assertIsNone(harness.client._current_media_source)
                    file_payload = next(
                        setting["file"] for setting in harness.sent_settings
                        if "file" in setting
                    )
                    self.assertNotIn("media_url", file_payload)
                    self.assertNotIn("source_type", file_payload)
                finally:
                    harness.close()

    def test_gate_applies_latest_global_seek_before_resuming(self):
        harness = ClientHarness(name="host")
        try:
            harness.client.users = {"viewer": self._user(ready=False, url=None)}
            self.assertTrue(harness.client._begin_media_gate(MEDIA_URL, 15.0, False))
            ignore_generation = harness.client.client_ignore

            harness.client._on_state({
                "ignoringOnTheFly": {"client": ignore_generation},
                "playstate": {
                    "position": 84.0,
                    "paused": False,
                    "doSeek": True,
                    "setBy": "viewer",
                },
                "ping": {},
            })
            self.assertEqual(harness.client.global_pos, 84.0)
            self.assertFalse(any(event[0] == "seek" for event in harness.timeline))

            harness.client._on_set({
                "user": {
                    "viewer": {
                        "room": {"name": "test-room"},
                        "file": media_user_payload()["user"]["host"]["file"],
                    }
                },
                "ready": {"username": "viewer", "isReady": True,
                          "manuallyInitiated": False},
            })
            self.assertFalse(harness.client._check_media_gate())

            seek_index = harness.timeline.index(("seek", 84.0))
            resume_index = harness.timeline.index(("pause", False))
            self.assertLess(seek_index, resume_index)
            self.assertEqual(harness.player.pos, 84.0)
            self.assertFalse(harness.player.paused)
        finally:
            harness.close()

    def test_force_sync_is_blocked_while_gate_is_active(self):
        harness = ClientHarness(name="host")
        try:
            harness.client.users = {"viewer": self._user(ready=False, url=None)}
            harness.client.global_pos = 64.0
            harness.client.global_paused = False
            self.assertTrue(harness.client._begin_media_gate(MEDIA_URL, 15.0, False))
            timeline_before = list(harness.timeline)

            self.assertFalse(harness.client.force_sync())
            self.assertEqual(harness.timeline, timeline_before)
            self.assertIsNotNone(harness.client._media_gate)
            self.assertTrue(harness.player.paused)
        finally:
            harness.close()

    def test_disconnect_restores_playback_state_from_before_gate(self):
        for originally_paused in (False, True):
            with self.subTest(originally_paused=originally_paused):
                player = RecordingPlayer(paused=originally_paused)
                harness = ClientHarness(player=player, name="host")
                try:
                    harness.client.users = {"viewer": self._user(ready=False, url=None)}
                    self.assertTrue(
                        harness.client._begin_media_gate(
                            MEDIA_URL, 15.0, originally_paused
                        )
                    )
                    self.assertTrue(harness.player.paused)

                    harness.client._on_disconnect()

                    self.assertEqual(harness.player.paused, originally_paused)
                    self.assertIsNone(harness.client._media_gate)
                    self.assertFalse(harness.client.logged)
                finally:
                    harness.close()


class TransitionStateReplyTests(unittest.TestCase):
    def test_pending_and_loading_state_replies_are_ping_only(self):
        for transition_field in ("_pending_media", "_loading_media"):
            with self.subTest(transition_field=transition_field):
                harness = ClientHarness()
                replies = []
                harness.client._send = lambda message: replies.append(
                    json.loads(json.dumps(message))
                )
                try:
                    setattr(harness.client, transition_field, {"url": MEDIA_URL})
                    harness.client._on_state({
                        "playstate": {
                            "position": 48.0,
                            "paused": False,
                            "doSeek": True,
                            "setBy": "host",
                        },
                        "ping": {},
                    })

                    self.assertEqual(len(replies), 1)
                    reply = replies[0]["State"]
                    self.assertEqual(set(reply), {"ping"})
                    self.assertNotIn("playstate", reply)
                    self.assertFalse(
                        any(event[0] in ("seek", "pause") for event in harness.timeline)
                    )
                finally:
                    harness.close()


class ProtocolEncodingTests(unittest.TestCase):
    def test_client_escapes_lone_surrogate_on_the_wire(self):
        server_side, client_side = loopback_pair()
        self.addCleanup(server_side.close)
        self.addCleanup(client_side.close)
        client = syncplay.SyncClient(
            make_args(alist_enabled=False), RecordingPlayer()
        )
        client.sock = server_side
        value = "room-" + chr(0xD800)

        client._send({"Set": {"room": {"name": value}}})
        raw = client_side.recv(65536)

        self.assertIn(b"\\ud800", raw.lower())
        self.assertEqual(
            json.loads(raw.strip().decode("utf-8"))["Set"]["room"]["name"],
            value,
        )

    def test_client_rejects_non_finite_and_oversized_messages(self):
        server_side, client_side = loopback_pair()
        self.addCleanup(server_side.close)
        self.addCleanup(client_side.close)
        client = syncplay.SyncClient(
            make_args(alist_enabled=False), RecordingPlayer()
        )
        client.sock = server_side

        with self.assertRaises(OSError):
            client._send({"State": {"position": float("nan")}})
        with self.assertRaises(OSError):
            client._send({"Set": {"file": {"name":
                         "x" * syncplay.MAX_PROTOCOL_MESSAGE}}})

    def test_status_writer_handles_untrusted_surrogate_text(self):
        client = syncplay.SyncClient(
            make_args(alist_enabled=False), RecordingPlayer()
        )
        value = "viewer-" + chr(0xD800)
        client.name = value
        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = syncplay.FileControlBridge(
                client,
                command_file=str(Path(temp_dir, "command.json")),
                status_file=str(Path(temp_dir, "status.json")),
                interval=0.1,
            )

            bridge._write_status()

            payload = json.loads(
                Path(temp_dir, "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(payload["name"], value)

    def test_inbound_list_normalizes_non_finite_metadata(self):
        harness = ClientHarness(alist_enabled=False)
        try:
            harness.client._on_list({
                "test-room": {
                    "peer": {
                        "file": {
                            "name": "movie.mkv",
                            "duration": float("nan"),
                            "size": float("inf"),
                        },
                        "isReady": float("nan"),
                    }
                }
            })

            peer = harness.client.users["peer"]
            self.assertEqual(peer["file_info"]["duration"], 0.0)
            self.assertEqual(peer["file_info"]["size"], 0)
            self.assertIsNone(peer["ready"])
            json.dumps(harness.client.status_snapshot(), allow_nan=False)
        finally:
            harness.close()


class MpvPlayerCommandTests(unittest.TestCase):
    @staticmethod
    def _player_with_recorded_ipc():
        player = object.__new__(syncplay.MpvPlayer)
        commands = []

        def submit(command, timeout=5.0):
            commands.append((list(command), float(timeout)))
            return {"error": "success"}

        player._submit = submit
        return player, commands

    def test_remote_load_uses_pause_cache_readahead_then_replace(self):
        player, commands = self._player_with_recorded_ipc()

        media_test = SimpleNamespace(ok=True)
        with mock.patch.object(syncplay, "probe_media_url", return_value=media_test), \
                mock.patch.object(syncplay, "print_report"):
            player.load_remote(MEDIA_URL, readahead=24)

        self.assertEqual(
            [command for command, _timeout in commands],
            [
                ["set_property", "pause", True],
                ["set_property", "cache", "yes"],
                ["set_property", "demuxer-readahead-secs", 24.0],
                ["loadfile", MEDIA_URL, "replace"],
            ],
        )
        self.assertEqual(commands[-1][1], 10.0)

    def test_pause_and_speed_use_typed_set_property(self):
        player, commands = self._player_with_recorded_ipc()

        player.set_pause(True)
        player.set_speed(1.125)

        self.assertEqual(
            [command for command, _timeout in commands],
            [
                ["set_property", "pause", True],
                ["set_property", "speed", 1.125],
            ],
        )

    def test_property_failure_is_not_silently_ignored(self):
        player = object.__new__(syncplay.MpvPlayer)
        player._submit = lambda command, timeout=5.0: {"error": "invalid parameter"}

        with self.assertRaisesRegex(OSError, "设置暂停状态.*invalid parameter"):
            player.set_pause(True)

    def test_remote_load_stops_when_stream_option_fails(self):
        player = object.__new__(syncplay.MpvPlayer)
        player.last_media_test = None
        commands = []

        def submit(command, timeout=5.0):
            commands.append(list(command))
            if command[:2] == ["set_property", "cache"]:
                return {"error": "property unavailable"}
            return {"error": "success"}

        player._submit = submit
        media_test = SimpleNamespace(ok=True)
        with mock.patch.object(syncplay, "probe_media_url", return_value=media_test), \
                mock.patch.object(syncplay, "print_report"):
            with self.assertRaisesRegex(OSError, "启用 HTTP 缓存.*property unavailable"):
                player.load_remote(MEDIA_URL, readahead=24)

        self.assertEqual(
            commands,
            [
                ["set_property", "pause", True],
                ["set_property", "cache", "yes"],
            ],
        )

    def test_remote_load_rejects_failed_probe_before_mpv_ipc(self):
        player, commands = self._player_with_recorded_ipc()
        media_test = SimpleNamespace(
            ok=False,
            result=syncplay.MEDIA_ACCESS_ERROR,
        )

        with mock.patch.object(syncplay, "probe_media_url", return_value=media_test), \
                mock.patch.object(syncplay, "print_report"):
            with self.assertRaisesRegex(OSError, syncplay.MEDIA_ACCESS_ERROR):
                player.load_remote(MEDIA_URL, readahead=24)

        self.assertEqual(commands, [])
        self.assertIs(player.last_media_test, media_test)

    def test_http_stream_seek_remains_absolute(self):
        player, commands = self._player_with_recorded_ipc()

        player.seek(91.25)

        self.assertEqual(commands, [(["seek", 91.25, "absolute"], 5.0)])


class MiniServerMediaTests(unittest.TestCase):
    @staticmethod
    def _hello(name, room):
        return {"Hello": {"username": name, "room": {"name": room}}}

    def test_file_update_is_broadcast_with_extension_fields(self):
        host_server, host_client = loopback_pair()
        viewer_server, viewer_client = loopback_pair()
        self.addCleanup(host_server.close)
        self.addCleanup(host_client.close)
        self.addCleanup(viewer_server.close)
        self.addCleanup(viewer_client.close)

        server = syncplay.MiniServer(0)
        host = server.Watcher(host_server, ("127.0.0.1", 1))
        viewer = server.Watcher(viewer_server, ("127.0.0.1", 2))
        host.name, host.room = "host", "test-room"
        viewer.name, viewer.room = "viewer", "test-room"
        server.rooms["test-room"] = [host, viewer]
        file_info = media_user_payload()["user"]["host"]["file"]

        server._handle_set(host, {"file": file_info})
        message = recv_json_line(viewer_client)

        update = message["Set"]["user"]["host"]
        normalized_file = dict(file_info)
        normalized_file["size"] = 0
        self.assertEqual(update["room"], {"name": "test-room"})
        self.assertEqual(update["file"], normalized_file)
        self.assertEqual(host.file, normalized_file)

    def test_file_payload_is_sanitized_and_hash_size_is_preserved(self):
        server = syncplay.MiniServer(0)
        cleaned = server._clean_file({
            "name": "movie.mkv",
            "duration": 90,
            "size": "official-size-hash",
            "media_url": MEDIA_URL,
            "source_type": "alist",
            "media_owner": "host",
            "local_path": "C:\\Private\\movie.mkv",
            "admin_token": "must-not-leak",
        })

        self.assertEqual(cleaned, {
            "name": "movie.mkv",
            "duration": 90.0,
            "size": "official-size-hash",
            "media_url": MEDIA_URL,
            "source_type": "alist",
            "media_owner": "host",
        })
        self.assertIs(
            server._clean_file({"name": "bad-" + chr(0xD800)}),
            server._INVALID_FILE,
        )
        unsafe_url = "https://alist.example.test/d/" + chr(0xD800) + ".mkv"
        without_unsafe_url = server._clean_file({
            "name": "movie.mkv",
            "media_url": unsafe_url,
            "source_type": "alist",
        })
        self.assertNotIn("media_url", without_unsafe_url)
        self.assertNotIn("source_type", without_unsafe_url)

    def test_server_send_escapes_surrogates_and_bounds_responses(self):
        server_side, client_side = loopback_pair()
        self.addCleanup(server_side.close)
        self.addCleanup(client_side.close)
        watcher = syncplay.MiniServer.Watcher(
            server_side, ("127.0.0.1", 1)
        )
        value = "name-" + chr(0xD800)

        self.assertTrue(watcher.send({"Value": value}))
        first_raw = client_side.recv(65536)
        self.assertIn(b"\\ud800", first_raw.lower())
        self.assertEqual(
            json.loads(first_raw.strip().decode("utf-8"))["Value"], value
        )

        self.assertTrue(watcher.send({
            "Value": "x" * syncplay.MAX_PROTOCOL_MESSAGE
        }))
        second = recv_json_line(client_side)
        self.assertIn("4 MiB", second["Error"]["message"])

    def test_idle_room_tick_sends_ping_only_state(self):
        server_side, client_side = loopback_pair()
        self.addCleanup(server_side.close)
        self.addCleanup(client_side.close)
        server = syncplay.MiniServer(0)
        watcher = server.Watcher(server_side, ("127.0.0.1", 1))
        watcher.name, watcher.room = "viewer", "idle-room"
        watcher.pos = None
        server.rooms["idle-room"] = [watcher]

        self.assertEqual(server._tick_once(now=123.0), 1)
        message = recv_json_line(client_side)

        self.assertEqual(set(message["State"]), {"ping"})
        self.assertEqual(message["State"]["ping"]["latencyCalculation"], 123.0)

    def test_socket_read_timeout_does_not_drop_an_active_client(self):
        server_side, client_side = loopback_pair()
        self.addCleanup(server_side.close)
        self.addCleanup(client_side.close)
        server_side.settimeout(0.02)
        server = syncplay.MiniServer(0)
        watcher = server.Watcher(server_side, ("127.0.0.1", 1))

        with mock.patch.object(syncplay, "PROTOCOL_TIMEOUT", 0.3):
            worker = threading.Thread(
                target=server._client_loop, args=(watcher,), daemon=True
            )
            worker.start()
            client_side.sendall((json.dumps(
                self._hello("viewer", "test-room")
            ) + "\r\n").encode("utf-8"))
            self.assertTrue(wait_for(lambda: watcher.name == "viewer"))
            baseline = watcher.last_valid_state

            time.sleep(0.08)
            self.assertTrue(worker.is_alive())
            client_side.sendall((json.dumps({
                "State": {"ping": {"clientLatencyCalculation": time.time()}}
            }) + "\r\n").encode("utf-8"))
            self.assertTrue(wait_for(
                lambda: watcher.last_valid_state > baseline
            ))

            client_side.shutdown(socket.SHUT_RDWR)
            worker.join(timeout=1.0)
            self.assertFalse(worker.is_alive())

    def test_new_member_must_ack_canonical_state_before_aggregation(self):
        host_server, host_client = loopback_pair()
        newcomer_server, newcomer_client = loopback_pair()
        self.addCleanup(host_server.close)
        self.addCleanup(host_client.close)
        self.addCleanup(newcomer_server.close)
        self.addCleanup(newcomer_client.close)
        server = syncplay.MiniServer(0)
        host = server.Watcher(host_server, ("127.0.0.1", 1))
        host.name, host.room = "host", "test-room"
        host.pos, host.paused, host.pos_ts = 100.0, False, time.time()
        newcomer = server.Watcher(newcomer_server, ("127.0.0.1", 2))
        server.rooms["test-room"] = [host]
        # Simulate a departed actor whose name is reused by the newcomer.
        server.room_actor["test-room"] = "newcomer"

        server._handle(newcomer, self._hello("newcomer", "test-room"))
        hello, room_list, initial_state = recv_json_lines(newcomer_client, 3)

        self.assertIn("Hello", hello)
        self.assertIn("List", room_list)
        self.assertEqual(initial_state["State"]["playstate"]["setBy"], "host")
        first_token = newcomer.initial_sync_token
        self.assertIsNotNone(first_token)
        self.assertIsNone(newcomer.pos)

        server._handle_state(newcomer, {
            "ping": {"latencyCalculation": first_token}
        })
        self.assertEqual(newcomer.initial_sync_token, first_token)
        self.assertIsNone(newcomer.pos)

        server._handle_state(newcomer, {
            "playstate": {"position": 95.1, "paused": False},
            "ping": {"latencyCalculation": first_token},
        })
        self.assertIsNone(newcomer.pos)
        self.assertNotEqual(newcomer.initial_sync_token, first_token)
        with server.lock:
            canonical = server._room_playstate_locked("test-room", time.time())
        self.assertGreater(canonical["position"], 99.0)

        accepted_token = newcomer.initial_sync_token
        server._handle_state(newcomer, {
            "playstate": {
                "position": canonical["position"],
                "paused": canonical["paused"],
            },
            "ping": {"latencyCalculation": accepted_token},
        })
        self.assertIsNone(newcomer.initial_sync_token)
        self.assertIsNotNone(newcomer.pos)

    def test_concurrent_empty_room_first_reports_do_not_rewind_seed(self):
        first_server, first_client = loopback_pair()
        second_server, second_client = loopback_pair()
        self.addCleanup(first_server.close)
        self.addCleanup(first_client.close)
        self.addCleanup(second_server.close)
        self.addCleanup(second_client.close)
        server = syncplay.MiniServer(0)
        first = server.Watcher(first_server, ("127.0.0.1", 1))
        second = server.Watcher(second_server, ("127.0.0.1", 2))
        first.name, first.room = "first", "test-room"
        second.name, second.room = "second", "test-room"
        server.rooms["test-room"] = [first, second]

        server._handle_state(first, {
            "playstate": {"position": 100.0, "paused": False},
            "ping": {},
        })
        server._handle_state(second, {
            "playstate": {"position": 0.0, "paused": False},
            "ping": {},
        })

        self.assertAlmostEqual(first.pos, 100.0)
        self.assertIsNone(second.pos)
        self.assertIsNotNone(second.initial_sync_token)
        challenge = recv_json_line(second_client)["State"]
        self.assertTrue(challenge["playstate"]["doSeek"])
        self.assertGreater(challenge["playstate"]["position"], 99.0)
        with server.lock:
            canonical = server._room_playstate_locked("test-room", time.time())
        self.assertGreater(canonical["position"], 99.0)

    def test_protocol_timeout_removes_stale_clients(self):
        for logged_in in (False, True):
            with self.subTest(logged_in=logged_in):
                server_side, client_side = loopback_pair()
                self.addCleanup(server_side.close)
                self.addCleanup(client_side.close)
                server_side.settimeout(0.02)
                server = syncplay.MiniServer(0)
                watcher = server.Watcher(server_side, ("127.0.0.1", 1))
                if logged_in:
                    watcher.name, watcher.room = "viewer", "test-room"
                    watcher.last_valid_state = time.monotonic() - 1.0
                    server.rooms["test-room"] = [watcher]
                else:
                    watcher.connected_at = time.monotonic() - 1.0

                with mock.patch.object(syncplay, "PROTOCOL_TIMEOUT", 0.05):
                    worker = threading.Thread(
                        target=server._client_loop, args=(watcher,), daemon=True
                    )
                    worker.start()
                    worker.join(timeout=1.0)

                self.assertFalse(worker.is_alive())
                if logged_in:
                    self.assertNotIn(watcher, server.rooms.get("test-room", []))

    def test_user_list_does_not_disclose_other_rooms_media_urls(self):
        receiver_server, receiver_client = loopback_pair()
        self.addCleanup(receiver_server.close)
        self.addCleanup(receiver_client.close)

        server = syncplay.MiniServer(0)
        receiver = server.Watcher(receiver_server, ("127.0.0.1", 1))
        receiver.name, receiver.room = "viewer", "test-room"
        receiver.file = {"name": "local.mkv"}
        same_room = SimpleNamespace(
            name="host",
            ready=True,
            file=media_user_payload()["user"]["host"]["file"],
        )
        private_url = "https://alist.example.test/d/private/secret.mkv"
        other_room = SimpleNamespace(
            name="other-host",
            ready=True,
            file={
                "name": "secret.mkv",
                "media_url": private_url,
                "source_type": "alist",
            },
        )
        server.rooms = {
            "test-room": [receiver, same_room],
            "private-room": [other_room],
        }

        server._send_list(receiver)
        message = recv_json_line(receiver_client)

        self.assertEqual(set(message["List"]), {"test-room"})
        self.assertEqual(
            message["List"]["test-room"]["host"]["file"]["media_url"],
            MEDIA_URL,
        )
        self.assertNotIn("private-room", message["List"])
        self.assertNotIn(private_url, json.dumps(message, ensure_ascii=False))

    def test_repeated_hello_cannot_leave_one_connection_in_two_rooms(self):
        server_side, client_side = loopback_pair()
        self.addCleanup(server_side.close)
        self.addCleanup(client_side.close)
        server = syncplay.MiniServer(0)
        watcher = server.Watcher(server_side, ("127.0.0.1", 1))

        server._handle(watcher, self._hello("viewer", "room-a"))
        server._handle(watcher, self._hello("viewer", "room-b"))

        self.assertEqual(watcher.room, "room-a")
        self.assertEqual(server.rooms["room-a"], [watcher])
        self.assertNotIn("room-b", server.rooms)

    def test_duplicate_names_are_renamed_and_cannot_enter_by_room_switch(self):
        sockets = [loopback_pair() for _index in range(3)]
        for pair in sockets:
            self.addCleanup(pair[0].close)
            self.addCleanup(pair[1].close)
        server = syncplay.MiniServer(0)
        first = server.Watcher(sockets[0][0], ("127.0.0.1", 1))
        duplicate = server.Watcher(sockets[1][0], ("127.0.0.1", 2))
        mover = server.Watcher(sockets[2][0], ("127.0.0.1", 3))

        server._handle(first, self._hello("Host", "target-room"))
        server._handle(duplicate, self._hello("host", "target-room"))
        server._handle(mover, self._hello("hOsT", "source-room"))

        self.assertEqual(first.name, "Host")
        self.assertEqual(duplicate.name, "host (2)")
        self.assertEqual(mover.name, "hOsT")

        server._handle_set(mover, {"room": {"name": "target-room"}})

        self.assertEqual(mover.room, "source-room")
        self.assertIn(mover, server.rooms["source-room"])
        self.assertNotIn(mover, server.rooms["target-room"])

    def test_invalid_playstate_numbers_are_ignored(self):
        server_side, client_side = loopback_pair()
        self.addCleanup(server_side.close)
        self.addCleanup(client_side.close)
        server = syncplay.MiniServer(0)
        watcher = server.Watcher(server_side, ("127.0.0.1", 1))
        watcher.name, watcher.room = "viewer", "test-room"

        for position in ("bad", True, float("nan"), float("inf"), -float("inf")):
            with self.subTest(position=position):
                server._handle_state(watcher, {
                    "playstate": {"position": position, "paused": False},
                    "ping": {
                        "latencyCalculation": position,
                        "clientLatencyCalculation": position,
                    },
                })

        self.assertIsNone(watcher.pos)
        self.assertIsNone(watcher.client_ts)
        self.assertEqual(watcher.rtt, 0.0)

        server._handle_state(watcher, {
            "playstate": {"position": 12.5, "paused": False},
            "ping": {"clientLatencyCalculation": 123.0},
        })
        self.assertEqual(watcher.pos, 12.5)
        self.assertFalse(watcher.paused)
        self.assertEqual(watcher.client_ts, 123.0)

    def test_room_switch_validates_name_and_cleans_empty_room_state(self):
        server_side, client_side = loopback_pair()
        self.addCleanup(server_side.close)
        self.addCleanup(client_side.close)
        server = syncplay.MiniServer(0)
        watcher = server.Watcher(server_side, ("127.0.0.1", 1))
        watcher.name, watcher.room = "viewer", "old-room"
        server.rooms["old-room"] = [watcher]
        server.room_seek["old-room"] = {"position": 1.0}
        server.room_paused["old-room"] = True
        server.room_actor["old-room"] = "viewer"

        server._handle_set(watcher, {"room": {"name": ["invalid"]}})
        self.assertEqual(watcher.room, "old-room")

        server._handle_set(watcher, {"room": {"name": "new-room"}})

        self.assertEqual(watcher.room, "new-room")
        self.assertEqual(server.rooms["new-room"], [watcher])
        self.assertNotIn("old-room", server.rooms)
        self.assertNotIn("old-room", server.room_seek)
        self.assertNotIn("old-room", server.room_paused)
        self.assertNotIn("old-room", server.room_actor)


class ExistingSynchronizationRegressionTests(unittest.TestCase):
    def test_global_pause_play_and_seek_still_control_player(self):
        player = RecordingPlayer(paused=False)
        client = syncplay.SyncClient(make_args(alist_enabled=False), player)
        client.last_pos = 10.0
        client.last_ts = time.time()
        client.last_paused = False
        client.global_pos = 10.0
        client.global_paused = False

        client._apply_global(12.0, True, False, "host", 0.0)
        self.assertTrue(player.paused)
        self.assertAlmostEqual(player.pos, 12.0)

        client._apply_global(12.0, False, False, "host", 0.0)
        self.assertFalse(player.paused)

        client._apply_global(90.0, False, True, "host", 0.0)
        self.assertAlmostEqual(player.pos, 90.0)

    def test_file_control_bridge_uses_only_temporary_files(self):
        player = RecordingPlayer(paused=False)
        client = syncplay.SyncClient(make_args(alist_enabled=False), player)
        with tempfile.TemporaryDirectory() as temp_dir:
            command_path = Path(temp_dir, "command.json")
            status_path = Path(temp_dir, "status.json")
            bridge = syncplay.FileControlBridge(
                client,
                command_file=str(command_path),
                status_file=str(status_path),
                interval=0.1,
            )

            for command in (
                {"command": "pause"},
                {"command": "play"},
                {"command": "seek", "position": 42.5},
            ):
                command_path.write_text(json.dumps(command), encoding="utf-8")
                bridge.poll_once()

            self.assertFalse(player.paused)
            self.assertAlmostEqual(player.pos, 42.5)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertIn("logged", status)
            self.assertEqual(status["room"], "test-room")

    def test_signature_failure_status_uses_actionable_message(self):
        player = RecordingPlayer(paused=True)
        player.last_media_test = SimpleNamespace(
            signature_required=True,
            as_dict=lambda: {
                "signature_required": True,
                "result": syncplay.MEDIA_ACCESS_ERROR,
            },
        )
        client = syncplay.SyncClient(make_args(), player)
        client.last_error = "AList 媒体加载失败：" + syncplay.MEDIA_ACCESS_ERROR

        with tempfile.TemporaryDirectory() as temp_dir:
            bridge = syncplay.FileControlBridge(
                client,
                command_file=str(Path(temp_dir, "command.json")),
                status_file=str(Path(temp_dir, "status.json")),
            )
            bridge._write_status()
            status = json.loads(
                Path(bridge.status_file).read_text(encoding="utf-8")
            )

            client.last_error = "后续连接错误"
            bridge._write_status()
            updated_status = json.loads(
                Path(bridge.status_file).read_text(encoding="utf-8")
            )

        self.assertEqual(status["last_error"], syncplay.MEDIA_ACCESS_ERROR)
        self.assertTrue(status["alist_media_test"]["signature_required"])
        self.assertEqual(updated_status["last_error"], "后续连接错误")


if __name__ == "__main__":
    unittest.main(verbosity=2)
