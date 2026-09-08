#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public media URL providers used by the bundled Syncplay client.

The AList provider is intentionally a pure path mapper.  It never reads AList
configuration, credentials, or management APIs.
"""

import ntpath
import os
import posixpath
from urllib.parse import quote, urlsplit, urlunsplit


class MediaProvider:
    """Map local Windows paths to public AList ``/d/`` URLs."""

    SOURCE_TYPE = "alist"

    def __init__(self, server=None, mappings=None, enabled=True):
        self._enabled = False
        self._server = None
        self._mappings = ()

        if not enabled:
            return

        self._server = self._validate_server(server)
        self._mappings = tuple(self._parse_mappings(mappings))
        self._enabled = bool(self._server and self._mappings)

    @property
    def enabled(self):
        return self._enabled

    def local_to_url(self, path):
        """Return an AList media descriptor, or ``None`` when not mapped."""
        if not self._enabled:
            return None

        local_path = self._normalize_input_path(path)
        if local_path is None:
            return None
        comparable_path = ntpath.normcase(local_path)

        for local_root, comparable_root, virtual_root in self._mappings:
            try:
                common = ntpath.commonpath((comparable_root, comparable_path))
            except ValueError:
                continue
            if common != comparable_root:
                continue

            relative = ntpath.relpath(local_path, local_root)
            if relative == ntpath.curdir:
                relative = ""
            relative = relative.replace("\\", "/")
            if relative:
                public_path = posixpath.join(virtual_root, relative)
            else:
                public_path = virtual_root
            encoded_path = quote("/" + public_path.lstrip("/"), safe="/")
            return {
                "url": self._server + "/d" + encoded_path,
                "type": self.SOURCE_TYPE,
            }
        return None

    @staticmethod
    def _validate_server(server):
        if server is None:
            return None
        try:
            text = os.fspath(server)
        except TypeError as exc:
            raise ValueError("AList server must be a URL") from exc
        if isinstance(text, bytes):
            raise ValueError("AList server must be text")
        text = text.strip()
        if not text:
            return None
        if any(character.isspace() for character in text):
            raise ValueError("AList server URL must not contain whitespace")

        try:
            parsed = urlsplit(text)
            hostname = parsed.hostname
            parsed.port  # Validate malformed and out-of-range ports.
        except ValueError as exc:
            raise ValueError("Invalid AList server URL") from exc
        if parsed.scheme.lower() not in ("http", "https") or not hostname:
            raise ValueError("AList server must use HTTP or HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("AList server URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("AList server URL must not contain a query or fragment")

        path = parsed.path.rstrip("/")
        return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))

    @classmethod
    def _parse_mappings(cls, mappings):
        if mappings is None:
            return []
        if isinstance(mappings, dict):
            entries = list(mappings.items())
        elif isinstance(mappings, tuple) and len(mappings) == 2 and all(
                cls._is_path_value(value) for value in mappings):
            # A tuple can be either one ``(local, virtual)`` pair or an
            # immutable sequence containing exactly two LOCAL=VIRTUAL items.
            entries = (list(mappings) if all(cls._has_mapping_separator(value)
                                              for value in mappings)
                       else [mappings])
        elif cls._is_path_value(mappings):
            entries = [mappings]
        else:
            try:
                entries = list(mappings)
            except TypeError as exc:
                raise ValueError("AList mappings must be an iterable") from exc

        normalized = {}
        for entry in entries:
            local_root, virtual_root = cls._split_mapping(entry)
            local_root = cls._normalize_mapping_root(local_root)
            virtual_root = cls._normalize_virtual_root(virtual_root)
            comparable_root = ntpath.normcase(local_root)
            normalized[comparable_root] = (
                local_root,
                comparable_root,
                virtual_root,
            )

        # A nested mapping must win over a broader parent mapping.
        return sorted(normalized.values(), key=lambda value: len(value[1]), reverse=True)

    @staticmethod
    def _is_path_value(value):
        return isinstance(value, (str, bytes, os.PathLike))

    @staticmethod
    def _has_mapping_separator(value):
        try:
            value = os.fspath(value)
        except TypeError:
            return False
        return isinstance(value, str) and "=" in value

    @classmethod
    def _split_mapping(cls, mapping):
        if isinstance(mapping, (tuple, list)) and len(mapping) == 2:
            return mapping[0], mapping[1]
        if not cls._is_path_value(mapping):
            raise ValueError("Each AList mapping must be LOCAL=VIRTUAL")
        try:
            text = os.fspath(mapping)
        except TypeError as exc:
            raise ValueError("Each AList mapping must be LOCAL=VIRTUAL") from exc
        if isinstance(text, bytes):
            raise ValueError("AList mappings must be text")
        local_root, separator, virtual_root = text.rpartition("=")
        if not separator:
            raise ValueError("Each AList mapping must be LOCAL=VIRTUAL")
        return local_root, virtual_root

    @staticmethod
    def _as_text(value, label):
        try:
            text = os.fspath(value)
        except TypeError as exc:
            raise ValueError("%s must be a path" % label) from exc
        if isinstance(text, bytes):
            raise ValueError("%s must be text" % label)
        text = text.strip()
        if not text or "\x00" in text:
            raise ValueError("%s must not be empty" % label)
        return text

    @classmethod
    def _normalize_mapping_root(cls, value):
        text = cls._as_text(value, "AList local root")
        normalized = ntpath.normpath(text)
        drive, tail = ntpath.splitdrive(normalized)
        is_unc = drive.startswith("\\\\")
        if not ntpath.isabs(normalized) or not drive:
            raise ValueError("AList local root must be an absolute Windows path")
        if not is_unc and not tail.startswith(("\\", "/")):
            raise ValueError("AList local root must be drive-absolute")
        if os.name == "nt":
            # realpath resolves existing junctions/symlinks so they cannot move
            # the effective sharing boundary outside the configured root.
            normalized = ntpath.normpath(os.path.realpath(normalized))
        return normalized

    @classmethod
    def _normalize_virtual_root(cls, value):
        text = cls._as_text(value, "AList virtual root").replace("\\", "/")
        if any(part == ".." for part in text.split("/")):
            raise ValueError("AList virtual root must not contain '..'")
        normalized = posixpath.normpath("/" + text.lstrip("/"))
        return "/" + normalized.lstrip("/")

    @staticmethod
    def _normalize_input_path(path):
        if path is None:
            return None
        try:
            text = os.fspath(path)
        except TypeError:
            return None
        if isinstance(text, bytes):
            return None
        text = text.strip()
        if not text or "\x00" in text:
            return None

        normalized = ntpath.normpath(text)
        drive, tail = ntpath.splitdrive(normalized)
        is_unc = drive.startswith("\\\\")
        if not ntpath.isabs(normalized) or not drive:
            return None
        if not is_unc and not tail.startswith(("\\", "/")):
            return None
        if os.name == "nt":
            normalized = ntpath.normpath(os.path.realpath(normalized))
        return normalized
