#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standard-library tests for the local Tailscale setup helpers."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from . import tailscale_integration as tailscale
except ImportError:  # Allows running this file directly.
    import tailscale_integration as tailscale


DNS_NAME = "watch-host.example-tailnet.ts.net"
TAILSCALE_IPV4 = "100.100.20.30"


def _status_payload():
    return {
        "BackendState": "Running",
        "AuthURL": "https://login.example.invalid/private",
        "Self": {
            "ID": "sensitive-self-id",
            "DNSName": DNS_NAME.upper() + ".",
            "TailscaleIPs": [TAILSCALE_IPV4, "fd7a:115c:a1e0::1234"],
            "UserID": 123456,
        },
        "Peer": {
            "peer-key": {
                "DNSName": "peer.other-tailnet.ts.net.",
                "TailscaleIPs": ["100.101.22.33"],
            }
        },
        "CurrentTailnet": {
            "Name": "private-account@example.invalid",
            "MagicDNSSuffix": "example-tailnet.ts.net",
        },
    }


class NormalizeHostTests(unittest.TestCase):
    def test_full_magicdns_name_becomes_https_origin(self):
        self.assertEqual(
            tailscale.normalize_host(DNS_NAME.upper() + "."),
            "https://" + DNS_NAME,
        )
        self.assertEqual(
            tailscale.normalize_host("https://%s./" % DNS_NAME.upper()),
            "https://" + DNS_NAME,
        )

    def test_tailscale_ipv4_becomes_direct_alist_origin(self):
        self.assertEqual(
            tailscale.normalize_host(TAILSCALE_IPV4),
            "http://%s:5244" % TAILSCALE_IPV4,
        )
        self.assertEqual(
            tailscale.normalize_host("100.64.0.1"),
            "http://100.64.0.1:5244",
        )
        self.assertEqual(
            tailscale.normalize_host("100.127.255.254"),
            "http://100.127.255.254:5244",
        )

    def test_explicit_safe_origins_are_normalized(self):
        self.assertEqual(
            tailscale.normalize_host("http://%s:5244/" % TAILSCALE_IPV4),
            "http://%s:5244" % TAILSCALE_IPV4,
        )
        self.assertEqual(
            tailscale.normalize_host("https://%s:443/" % DNS_NAME.upper()),
            "https://" + DNS_NAME,
        )

    def test_credentials_query_paths_and_non_tailscale_hosts_are_rejected(self):
        invalid_values = (
            "https://admin:secret@%s/" % DNS_NAME,
            "https://%s/?sign=secret" % DNS_NAME,
            "https://%s/#fragment" % DNS_NAME,
            "https://%s/d/media/movie.mkv" % DNS_NAME,
            "http://%s/" % DNS_NAME,
            "https://alist.example.com/",
            "watch-host",
            "192.168.1.20",
            "100.63.255.255",
            "100.128.0.1",
            "ftp://%s/" % DNS_NAME,
        )

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    tailscale.normalize_host(value)


class ExtractStatusTests(unittest.TestCase):
    def test_only_local_status_fields_are_extracted(self):
        cli_path = r"C:\Program Files\Tailscale\tailscale.exe"

        status = tailscale.extract_status(_status_payload(), cli_path)

        self.assertEqual(status["backend_state"], "Running")
        self.assertEqual(status["ipv4"], TAILSCALE_IPV4)
        self.assertEqual(status["dns_name"], DNS_NAME)
        self.assertEqual(status["cli_path"], cli_path)
        serialized = repr(status)
        self.assertNotIn("sensitive-self-id", serialized)
        self.assertNotIn("peer.other-tailnet.ts.net", serialized)
        self.assertNotIn("private-account@example.invalid", serialized)
        self.assertNotIn("login.example.invalid", serialized)

    def test_invalid_self_addresses_and_dns_name_are_not_exposed(self):
        payload = {
            "BackendState": "Stopped",
            "Self": {
                "DNSName": "not-a-tailscale-name.example.com.",
                "TailscaleIPs": ["192.168.1.25", "100.128.0.1", "not-an-ip"],
            },
        }

        status = tailscale.extract_status(payload, "tailscale.exe")

        self.assertEqual(status["backend_state"], "Stopped")
        self.assertEqual(status["ipv4"], "")
        self.assertEqual(status["dns_name"], "")

    def test_non_object_status_is_rejected(self):
        for value in (None, [], "{}"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    tailscale.extract_status(value, "tailscale.exe")


class SyncplayConfigUpdateTests(unittest.TestCase):
    def test_existing_keys_are_updated_and_missing_keys_are_appended_atomically(self):
        original = (
            "# Keep this comment\r\n"
            "alist_enabled=no\r\n"
            "alist_server=http://127.0.0.1:5244\r\n"
            "room_setting=keep-me\r\n"
        ).encode("utf-8")
        updates = {
            "alist_enabled": "yes",
            "tailscale_mode": "viewer",
            "tailscale_host": DNS_NAME,
            "alist_server": "https://" + DNS_NAME,
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "syncplay_ui.conf")
            path.write_bytes(original)
            real_replace = os.replace
            with mock.patch.object(
                    tailscale.os, "replace", wraps=real_replace) as replace_mock:
                tailscale.update_syncplay_config(path, updates)

            raw = path.read_bytes()
            replace_mock.assert_called_once()
            temp_path, destination = replace_mock.call_args.args[:2]
            self.assertEqual(os.path.dirname(os.path.abspath(temp_path)), directory)
            self.assertEqual(os.path.abspath(destination), os.path.abspath(path))
            self.assertNotEqual(os.path.abspath(temp_path), os.path.abspath(path))
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))
            text = raw.decode("utf-8")
            self.assertIn("# Keep this comment\r\n", text)
            self.assertIn("room_setting=keep-me\r\n", text)
            self.assertIn("alist_enabled=yes\r\n", text)
            self.assertIn("alist_server=https://%s\r\n" % DNS_NAME, text)
            self.assertIn("tailscale_mode=viewer\r\n", text)
            self.assertIn("tailscale_host=%s\r\n" % DNS_NAME, text)
            self.assertNotIn("alist_enabled=no", text)
            self.assertNotIn("http://127.0.0.1:5244", text)
            self.assertFalse(Path(temp_path).exists())

    def test_new_config_is_created_with_all_updates(self):
        updates = {
            "alist_enabled": "yes",
            "tailscale_mode": "viewer",
            "tailscale_host": TAILSCALE_IPV4,
            "alist_server": "http://%s:5244" % TAILSCALE_IPV4,
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "nested", "syncplay_ui.conf")
            tailscale.update_syncplay_config(path, updates)

            values = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                key, value = line.split("=", 1)
                values[key] = value
            self.assertEqual(values, updates)

    def test_failed_atomic_replace_preserves_original_and_cleans_temp_file(self):
        original = b"alist_enabled=no\nroom_setting=keep-me\n"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "syncplay_ui.conf")
            path.write_bytes(original)
            before = set(Path(directory).iterdir())
            with mock.patch.object(
                    tailscale.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    tailscale.update_syncplay_config(
                        path, {"alist_enabled": "yes"}
                    )

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(set(Path(directory).iterdir()), before)

    def test_newlines_in_config_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "syncplay_ui.conf")
            with self.assertRaises(ValueError):
                tailscale.update_syncplay_config(
                    path, {"tailscale_host": DNS_NAME + "\nextra_key=yes"}
                )
            self.assertFalse(path.exists())


class ConfigurationBuilderTests(unittest.TestCase):
    def test_host_configuration_prefers_full_dns_name_and_https(self):
        status = tailscale.extract_status(_status_payload(), "tailscale.exe")

        configuration = tailscale.build_host_configuration(status)

        self.assertEqual(configuration, {
            "alist_enabled": "yes",
            "alist_server": "https://" + DNS_NAME,
            "tailscale_mode": "host",
            "tailscale_host": DNS_NAME,
        })

    def test_viewer_configuration_from_magicdns_name(self):
        configuration = tailscale.build_viewer_configuration(
            "https://%s./" % DNS_NAME.upper()
        )

        self.assertEqual(configuration, {
            "alist_enabled": "yes",
            "alist_server": "https://" + DNS_NAME,
            "alist_root": "",
            "alist_map": "",
            "tailscale_mode": "viewer",
            "tailscale_host": DNS_NAME,
        })

    def test_viewer_configuration_from_tailscale_ipv4(self):
        configuration = tailscale.build_viewer_configuration(TAILSCALE_IPV4)

        self.assertEqual(configuration, {
            "alist_enabled": "yes",
            "alist_server": "http://%s:5244" % TAILSCALE_IPV4,
            "alist_root": "",
            "alist_map": "",
            "tailscale_mode": "viewer",
            "tailscale_host": TAILSCALE_IPV4,
        })

    def test_viewer_configuration_clears_copied_host_mappings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "syncplay_ui.conf")
            path.write_text(
                "alist_root=D:\\PrivateVideos\n"
                "alist_map=D:\\Secret.mkv=/media/Secret.mkv\n"
                "tailscale_mode=host\n",
                encoding="utf-8",
            )
            tailscale.update_syncplay_config(
                path, tailscale.build_viewer_configuration(DNS_NAME)
            )

            values = tailscale.read_syncplay_config(path)
            self.assertEqual(values["alist_root"], "")
            self.assertEqual(values["alist_map"], "")
            self.assertEqual(values["tailscale_mode"], "viewer")

    def test_host_configuration_requires_running_tailscale_and_full_dns(self):
        invalid_statuses = (
            {},
            {"installed": False, "backend_state": "Running", "dns_name": DNS_NAME},
            {"installed": True, "backend_state": "Stopped", "dns_name": DNS_NAME},
            {"installed": True, "backend_state": "Running", "dns_name": ""},
            {
                "installed": True,
                "backend_state": "Running",
                "dns_name": "short-host",
            },
        )

        for status in invalid_statuses:
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    tailscale.build_host_configuration(status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
