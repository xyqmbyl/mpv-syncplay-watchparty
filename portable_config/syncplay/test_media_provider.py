#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest
from pathlib import PureWindowsPath
from urllib.parse import urlsplit

try:
    from .media_provider import MediaProvider
except ImportError:  # Allows running this file directly.
    from media_provider import MediaProvider


class MediaProviderTests(unittest.TestCase):
    def test_maps_windows_path_and_encodes_url(self):
        provider = MediaProvider(
            "https://alist.example.com/",
            [r"D:\Movies=/movies"],
        )

        result = provider.local_to_url(
            PureWindowsPath(r"d:\movies\Sci Fi\A&B #1 中文.mkv")
        )

        self.assertEqual(
            result,
            {
                "url": (
                    "https://alist.example.com/d/movies/Sci%20Fi/"
                    "A%26B%20%231%20%E4%B8%AD%E6%96%87.mkv"
                ),
                "type": "alist",
            },
        )
        self.assertEqual(urlsplit(result["url"]).query, "")

    def test_multiple_mappings_use_most_specific_root(self):
        provider = MediaProvider(
            "https://alist.example.com/base/",
            [
                r"D:\Media=/all-media",
                r"D:\Media\Anime=/anime",
                r"E:\Concerts=/concerts",
            ],
        )

        self.assertEqual(
            provider.local_to_url(r"D:\Media\Anime\Show\01.mkv")["url"],
            "https://alist.example.com/base/d/anime/Show/01.mkv",
        )
        self.assertEqual(
            provider.local_to_url(r"E:\Concerts\Live.ts")["url"],
            "https://alist.example.com/base/d/concerts/Live.ts",
        )

    def test_supports_unc_roots(self):
        provider = MediaProvider(
            "http://127.0.0.1:5244",
            [(r"\\NAS\Media\Movies", "/library")],
        )

        self.assertEqual(
            provider.local_to_url(r"\\nas\media\movies\Drama\Film.mkv")["url"],
            "http://127.0.0.1:5244/d/library/Drama/Film.mkv",
        )

    def test_dictionary_mappings_are_supported(self):
        provider = MediaProvider(
            "https://alist.example.com",
            {r"D:\Movies": "movies", r"E:\TV": "/shows"},
        )

        self.assertEqual(
            provider.local_to_url(r"E:\TV\Series\Episode 1.mp4")["url"],
            "https://alist.example.com/d/shows/Series/Episode%201.mp4",
        )

    def test_tuple_can_contain_two_mapping_strings(self):
        provider = MediaProvider(
            "https://alist.example.com",
            (r"D:\Movies=/movies", r"E:\TV=/shows"),
        )

        self.assertEqual(
            provider.local_to_url(r"D:\Movies\Film.mkv")["url"],
            "https://alist.example.com/d/movies/Film.mkv",
        )
        self.assertEqual(
            provider.local_to_url(r"E:\TV\Episode.mkv")["url"],
            "https://alist.example.com/d/shows/Episode.mkv",
        )

    def test_rejects_sibling_prefix_and_parent_traversal(self):
        provider = MediaProvider(
            "https://alist.example.com",
            [r"D:\Movies=/movies"],
        )

        self.assertIsNone(provider.local_to_url(r"D:\Movies-Archive\old.mkv"))
        self.assertIsNone(provider.local_to_url(r"D:\Movies\..\Private\secret.mkv"))
        self.assertIsNone(provider.local_to_url(r"E:\Movies\other.mkv"))

    def test_unmatched_relative_and_url_inputs_return_none(self):
        provider = MediaProvider(
            "https://alist.example.com",
            [r"D:\Movies=/movies"],
        )

        self.assertIsNone(provider.local_to_url(r"Movies\relative.mkv"))
        self.assertIsNone(provider.local_to_url("https://other.example/movie.mkv"))
        self.assertIsNone(provider.local_to_url(None))

    def test_disabled_or_incomplete_provider_returns_none(self):
        disabled = MediaProvider(
            "not a URL",
            ["not a mapping"],
            enabled=False,
        )
        no_server = MediaProvider(None, [r"D:\Movies=/movies"])
        no_mappings = MediaProvider("https://alist.example.com", [])

        self.assertFalse(disabled.enabled)
        self.assertFalse(no_server.enabled)
        self.assertFalse(no_mappings.enabled)
        self.assertIsNone(disabled.local_to_url(r"D:\Movies\movie.mkv"))
        self.assertIsNone(no_server.local_to_url(r"D:\Movies\movie.mkv"))
        self.assertIsNone(no_mappings.local_to_url(r"D:\Movies\movie.mkv"))

    def test_invalid_servers_are_rejected(self):
        invalid_servers = (
            "ftp://alist.example.com",
            "https:///missing-host",
            "https://admin:secret@alist.example.com",
            "https://alist.example.com?token=secret",
            "https://alist.example.com/#fragment",
            "https://alist.example.com:not-a-port",
            "https://alist.example.com:70000",
        )

        for server in invalid_servers:
            with self.subTest(server=server):
                with self.assertRaises(ValueError):
                    MediaProvider(server, [r"D:\Movies=/movies"])

    def test_invalid_mapping_configuration_is_rejected(self):
        invalid_mappings = (
            ["missing-separator"],
            [r"Movies=/movies"],
            [r"D:\Movies=/movies/../private"],
            [(r"D:\Movies", "")],
        )

        for mappings in invalid_mappings:
            with self.subTest(mappings=mappings):
                with self.assertRaises(ValueError):
                    MediaProvider("https://alist.example.com", mappings)


if __name__ == "__main__":
    unittest.main()
