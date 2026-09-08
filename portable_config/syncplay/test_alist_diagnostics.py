#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Loopback-only tests for the AList public media URL diagnostic."""

import json
import threading
import time
import unittest
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

try:
    from .alist_diagnostics import MEDIA_ACCESS_ERROR, probe_media_url
except ImportError:  # Allows running this file directly.
    from alist_diagnostics import MEDIA_ACCESS_ERROR, probe_media_url


def _send_response(handler, status, body=b"", headers=None):
    handler.send_response(status)
    for name, value in (headers or {}).items():
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    if handler.command != "HEAD" and body:
        handler.wfile.write(body)


def _send_json(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    _send_response(
        handler,
        status,
        body,
        {"Content-Type": "application/json; charset=utf-8"},
    )


def _send_range_video(handler, include_accept_ranges=True):
    headers = {
        "Content-Type": "video/mp4",
        "Content-Range": "bytes 0-0/128",
    }
    if include_accept_ranges:
        headers["Accept-Ranges"] = "bytes"
    _send_response(handler, 206, b"\x00", headers)


@contextmanager
def _serve(callback):
    events = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _dispatch(self):
            events.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "range": self.headers.get("Range"),
                    "cookie": self.headers.get("Cookie"),
                    "referer": self.headers.get("Referer"),
                    "authorization": self.headers.get("Authorization"),
                }
            )
            callback(self)

        def do_GET(self):
            self._dispatch()

        def do_HEAD(self):
            self._dispatch()

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    host, port = server.server_address
    url = "http://%s:%s/d/media/movie.mp4" % (host, port)
    try:
        yield url, events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


class AListDiagnosticsTests(unittest.TestCase):
    def test_probe_does_not_read_or_use_ambient_proxy_configuration(self):
        with _serve(_send_range_video) as (url, _events):
            with mock.patch.object(
                    urllib.request,
                    "getproxies",
                    side_effect=AssertionError("ambient proxy was inspected")):
                probe = probe_media_url(url, timeout=1.0)

        self.assertTrue(probe.ok)
        self.assertEqual(probe.http_status, 206)

    def test_public_media_supports_range_without_auth_headers(self):
        with _serve(_send_range_video) as (url, events):
            probe = probe_media_url(url, timeout=1.0)

        self.assertTrue(probe.ok)
        self.assertEqual(probe.url, url)
        self.assertEqual(probe.http_status, 206)
        self.assertIsNone(probe.alist_code)
        self.assertIsNone(probe.alist_message)
        self.assertEqual(probe.content_type, "video/mp4")
        self.assertEqual(probe.accept_ranges.lower(), "bytes")
        self.assertTrue(probe.range_supported)
        self.assertFalse(probe.cookie_required)
        self.assertFalse(probe.referer_required)
        self.assertFalse(probe.signature_required)
        self.assertTrue(events)
        self.assertTrue(any(event["range"] == "bytes=0-0" for event in events))
        self.assertTrue(all(event["cookie"] is None for event in events))
        self.assertTrue(all(event["referer"] is None for event in events))
        self.assertTrue(all(event["authorization"] is None for event in events))

        details = probe.as_dict()
        self.assertEqual(details["url"], url)
        self.assertEqual(details["http_status"], 206)
        self.assertTrue(details["range_supported"])

    def test_valid_206_proves_range_without_accept_ranges_header(self):
        def respond(handler):
            _send_range_video(handler, include_accept_ranges=False)

        with _serve(respond) as (url, _events):
            probe = probe_media_url(url, timeout=1.0)

        self.assertTrue(probe.ok)
        self.assertFalse(probe.accept_ranges)
        self.assertTrue(probe.range_supported)

    def test_cross_origin_redirect_preserves_range_without_credentials(self):
        with _serve(_send_range_video) as (target_url, target_events):
            def redirect(handler):
                _send_response(handler, 302, headers={"Location": target_url})

            with _serve(redirect) as (source_url, source_events):
                probe = probe_media_url(source_url, timeout=1.0)

        self.assertTrue(probe.ok)
        self.assertEqual(probe.http_status, 206)
        self.assertEqual(source_events[0]["range"], "bytes=0-0")
        self.assertEqual(target_events[0]["range"], "bytes=0-0")
        self.assertTrue(all(event["cookie"] is None
                            for event in source_events + target_events))
        self.assertTrue(all(event["authorization"] is None
                            for event in source_events + target_events))

    def test_http_200_alist_expire_missing_is_application_failure(self):
        def respond(handler):
            _send_json(
                handler,
                200,
                {"code": 401, "message": "expire missing", "data": None},
            )

        with _serve(respond) as (url, _events):
            probe = probe_media_url(url, timeout=1.0)

        self.assertFalse(probe.ok)
        self.assertEqual(probe.http_status, 200)
        self.assertEqual(probe.alist_code, 401)
        self.assertEqual(probe.alist_message, "expire missing")
        self.assertTrue(probe.signature_required)
        self.assertFalse(probe.range_supported)
        self.assertEqual(probe.result, MEDIA_ACCESS_ERROR)

    def test_http_401_body_is_still_parsed_for_alist_error(self):
        def respond(handler):
            _send_json(
                handler,
                401,
                {"code": 401, "message": "expire missing", "data": None},
            )

        with _serve(respond) as (url, _events):
            probe = probe_media_url(url, timeout=1.0)

        self.assertFalse(probe.ok)
        self.assertEqual(probe.http_status, 401)
        self.assertEqual(probe.alist_code, 401)
        self.assertEqual(probe.alist_message, "expire missing")
        self.assertTrue(probe.signature_required)
        self.assertEqual(probe.result, MEDIA_ACCESS_ERROR)

    def test_accept_ranges_header_does_not_prove_server_honored_range(self):
        def respond(handler):
            _send_response(
                handler,
                200,
                b"x",
                {
                    "Content-Type": "video/mp4",
                    "Accept-Ranges": "bytes",
                },
            )

        with _serve(respond) as (url, _events):
            probe = probe_media_url(url, timeout=1.0)

        self.assertFalse(probe.ok)
        self.assertEqual(probe.http_status, 200)
        self.assertEqual(probe.accept_ranges.lower(), "bytes")
        self.assertFalse(probe.range_supported)
        self.assertFalse(probe.cookie_required)
        self.assertFalse(probe.referer_required)
        self.assertIn("Cookie: 不需要", probe.auth_text)

    def test_referer_requirement_is_detected_by_retry(self):
        def respond(handler):
            if handler.headers.get("Referer"):
                _send_range_video(handler)
            else:
                _send_json(
                    handler,
                    403,
                    {"code": 403, "message": "referer missing", "data": None},
                )

        with _serve(respond) as (url, events):
            probe = probe_media_url(url, timeout=1.0)

        self.assertFalse(probe.ok)
        self.assertEqual(probe.http_status, 206)
        self.assertTrue(probe.range_supported)
        self.assertTrue(probe.referer_required)
        self.assertFalse(probe.cookie_required)
        self.assertFalse(probe.signature_required)
        self.assertIn("当前共享模式不可用", probe.result)
        self.assertGreaterEqual(len(events), 2)
        self.assertIsNone(events[0]["referer"])
        self.assertTrue(any(event["referer"] for event in events[1:]))

    def test_html_login_page_is_reported_as_cookie_authentication(self):
        body = (
            b"<!doctype html><html><head><title>AList Login</title></head>"
            b'<body><form action="/@login"><input type="password"></form></body></html>'
        )

        def respond(handler):
            _send_response(
                handler,
                200,
                body,
                {
                    "Content-Type": "text/html; charset=utf-8",
                    "Set-Cookie": "session=required; Path=/; HttpOnly",
                },
            )

        with _serve(respond) as (url, _events):
            probe = probe_media_url(url, timeout=1.0)

        self.assertFalse(probe.ok)
        self.assertEqual(probe.http_status, 200)
        self.assertEqual(probe.content_type, "text/html; charset=utf-8")
        self.assertTrue(probe.cookie_required)
        self.assertFalse(probe.range_supported)

    def test_malformed_content_range_is_not_accepted(self):
        def respond(handler):
            _send_response(
                handler,
                206,
                b"\x00",
                {
                    "Content-Type": "video/mp4",
                    "Accept-Ranges": "bytes",
                    "Content-Range": "not-a-content-range",
                },
            )

        with _serve(respond) as (url, _events):
            probe = probe_media_url(url, timeout=1.0)

        self.assertFalse(probe.ok)
        self.assertEqual(probe.http_status, 206)
        self.assertFalse(probe.range_supported)

    def test_empty_206_response_does_not_prove_range_support(self):
        def respond(handler):
            _send_response(
                handler,
                206,
                b"",
                {
                    "Content-Type": "video/mp4",
                    "Accept-Ranges": "bytes",
                    "Content-Range": "bytes 0-0/128",
                },
            )

        with _serve(respond) as (url, _events):
            probe = probe_media_url(url, timeout=1.0)

        self.assertFalse(probe.ok)
        self.assertFalse(probe.range_supported)

    def test_http_404_is_reported_as_a_mapping_error(self):
        def respond(handler):
            _send_response(
                handler,
                404,
                b"not found",
                {"Content-Type": "text/plain; charset=utf-8"},
            )

        with _serve(respond) as (url, _events):
            probe = probe_media_url(url, timeout=1.0)

        self.assertFalse(probe.ok)
        self.assertEqual(probe.http_status, 404)
        self.assertIn("路径映射错误", probe.result)

    def test_206_headers_do_not_hide_a_body_read_timeout(self):
        def respond(handler):
            handler.send_response(206)
            handler.send_header("Content-Type", "video/mp4")
            handler.send_header("Content-Range", "bytes 0-0/128")
            handler.send_header("Content-Length", "1")
            handler.end_headers()
            time.sleep(0.2)

        with _serve(respond) as (url, _events):
            probe = probe_media_url(url, timeout=0.05)

        self.assertFalse(probe.ok)
        self.assertEqual(probe.http_status, 206)
        self.assertFalse(probe.range_supported)
        self.assertIn("读取失败", probe.result)

    def test_report_has_required_heading_fields_and_order(self):
        with _serve(_send_range_video) as (url, _events):
            probe = probe_media_url(url, timeout=1.0)

        report = probe.format_report()
        lines = report.splitlines()
        self.assertEqual(lines[0], "[AList Media Test]")
        self.assertEqual(lines[1], "")
        self.assertEqual(
            [line.split(":", 1)[0] for line in lines[2:8]],
            ["URL", "Status", "Auth", "Range", "Content-Type", "Result"],
        )
        self.assertIn(url, lines[2])
        self.assertIn("206", lines[3])
        self.assertTrue(all(":" in line and line.split(":", 1)[1].strip()
                            for line in lines[2:8]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
