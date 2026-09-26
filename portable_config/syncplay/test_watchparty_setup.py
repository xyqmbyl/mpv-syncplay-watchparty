#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""watchparty_setup 纯函数部分的单元测试（不触碰真实 AList 进程）。"""

import json
import io
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import watchparty_setup as setup


def _patch(module, **overrides):
    """临时替换模块属性。"""
    saved = {key: getattr(module, key) for key in overrides}
    for key, value in overrides.items():
        setattr(module, key, value)
    return saved


class RenderConfigTests(unittest.TestCase):
    def test_forces_public_listen_and_random_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, "config.json")
            saved = _patch(setup, ALIST_CONFIG=config_path, ALIST_TEMPLATE=os.path.join(tmp, "missing.json"))
            try:
                config = setup.render_alist_config()
                with open(config_path, encoding="utf-8") as stream:
                    on_disk = json.load(stream)
            finally:
                _patch(setup, **saved)
        self.assertEqual(config["scheme"]["address"], "0.0.0.0")
        self.assertEqual(config["scheme"]["http_port"], 5244)
        self.assertTrue(config["jwt_secret"])
        self.assertEqual(on_disk["scheme"]["address"], "0.0.0.0")

    def test_template_values_win_except_listen_and_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            template_path = os.path.join(tmp, "template.json")
            with open(template_path, "w", encoding="utf-8") as stream:
                json.dump({"scheme": {"address": "127.0.0.1", "http_port": 9999},
                           "jwt_secret": "preset",
                           "custom": True}, stream)
            config_path = os.path.join(tmp, "config.json")
            saved = _patch(setup, ALIST_CONFIG=config_path, ALIST_TEMPLATE=template_path)
            try:
                config = setup.render_alist_config()
            finally:
                _patch(setup, **saved)
        self.assertEqual(config["scheme"]["address"], "0.0.0.0")
        self.assertEqual(config["scheme"]["http_port"], 5244)
        self.assertEqual(config["jwt_secret"], "preset")  # 模板自带则保留
        self.assertTrue(config["custom"])


class AlistPingTests(unittest.TestCase):
    def test_plain_pong(self):
        saved = _patch(setup, http_get=lambda url, timeout=4.0: (200, "pong\n"))
        try:
            self.assertTrue(setup.alist_ping("http://127.0.0.1:5244"))
        finally:
            _patch(setup, **saved)

    def test_json_pong(self):
        saved = _patch(setup, http_get=lambda url, timeout=4.0: (200, '{"code":200,"message":"PONG"}'))
        try:
            self.assertTrue(setup.alist_ping("http://127.0.0.1:5244"))
        finally:
            _patch(setup, **saved)

    def test_other_service_is_not_alist(self):
        saved = _patch(setup, http_get=lambda url, timeout=4.0: (200, "<html>router</html>"))
        try:
            self.assertFalse(setup.alist_ping("http://127.0.0.1:5244"))
        finally:
            _patch(setup, **saved)

    def test_connection_refused(self):
        saved = _patch(setup, http_get=lambda url, timeout=4.0: (None, "refused"))
        try:
            self.assertFalse(setup.alist_ping("http://127.0.0.1:5244"))
        finally:
            _patch(setup, **saved)


class AdminPasswordTests(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            password_file = os.path.join(tmp, "ADMIN_PASSWORD.txt")
            saved = _patch(setup, ADMIN_PASSWORD_FILE=password_file)
            try:
                self.assertIsNone(setup.read_admin_password())
                setup.write_admin_password("WP-aa-bb-cc")
                self.assertEqual(setup.read_admin_password(), "WP-aa-bb-cc")
            finally:
                _patch(setup, **saved)

    def test_reads_existing_host_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            password_file = os.path.join(tmp, "ADMIN_PASSWORD.txt")
            with open(password_file, "w", encoding="utf-8") as stream:
                stream.write("用户名：admin\n密码：WP-1234-5678-9abc\n\n说明文字\n")
            saved = _patch(setup, ADMIN_PASSWORD_FILE=password_file)
            try:
                self.assertEqual(setup.read_admin_password(), "WP-1234-5678-9abc")
            finally:
                _patch(setup, **saved)


class AlistAdminContractTests(unittest.TestCase):
    def test_setting_save_uses_alist_v3_array_payload(self):
        admin = setup.AlistAdmin.__new__(setup.AlistAdmin)
        posted = []
        admin._post = lambda path, payload: posted.append((path, payload))

        admin.save_setting("sign_all", "false")

        self.assertEqual(posted, [
            ("/api/admin/setting/save", [
                {"key": "sign_all", "value": "false"},
            ]),
        ])


class MediaStorageDecisionTests(unittest.TestCase):
    class FakeAdmin:
        def __init__(self, storages):
            self._storages = storages
            self.created = None
            self.updated = None

        def list_storages(self):
            return self._storages

        def ensure_media_storage(self, media_dir):  # pragma: no cover - replaced below
            raise AssertionError

    def test_same_root_without_sign_is_ok(self):
        media_dir = r"D:\proj\WatchParty\media"
        storage = {"mount_path": "/media", "enable_sign": False, "disabled": False,
                   "addition": json.dumps({"root_folder_path": media_dir})}
        admin = setup.AlistAdmin.__new__(setup.AlistAdmin)
        posted = []

        admin._post = lambda path, payload: posted.append((path, payload)) or None
        admin.list_storages = lambda: [storage]
        outcome = admin.ensure_media_storage(media_dir)
        self.assertEqual(outcome, "ok")
        self.assertEqual(posted, [])

    def test_missing_storage_is_created(self):
        admin = setup.AlistAdmin.__new__(setup.AlistAdmin)
        posted = []
        admin._post = lambda path, payload: posted.append((path, payload)) or None
        admin.list_storages = lambda: []
        outcome = admin.ensure_media_storage(r"D:\proj\WatchParty\media")
        self.assertEqual(outcome, "created")
        self.assertEqual(posted[0][0], "/api/admin/storage/create")
        addition = json.loads(posted[0][1]["addition"])
        self.assertEqual(addition["root_folder_path"], r"D:\proj\WatchParty\media")

    def test_absolute_foreign_root_is_updated(self):
        storage = {"id": 7, "mount_path": "/media", "enable_sign": True,
                   "disabled": False,
                   "addition": json.dumps({"root_folder_path": r"C:\other\machine\media"})}
        admin = setup.AlistAdmin.__new__(setup.AlistAdmin)
        posted = []
        admin._post = lambda path, payload: posted.append((path, payload)) or None
        admin.list_storages = lambda: [storage]
        outcome = admin.ensure_media_storage(r"D:\proj\WatchParty\media")
        self.assertEqual(outcome, "updated")
        self.assertEqual(posted[0][0], "/api/admin/storage/update")
        addition = json.loads(posted[0][1]["addition"])
        self.assertEqual(addition["root_folder_path"], r"D:\proj\WatchParty\media")
        self.assertFalse(posted[0][1]["enable_sign"])


class HostWizardExitTests(unittest.TestCase):
    def _run_wizard(self, tailscale_status, message, doctor_code=0):
        calls = []

        def final_doctor(config_path):
            calls.append("doctor")
            if doctor_code:
                setup.log("Funnel               无法确认 (FAIL)")
            return doctor_code

        saved = _patch(
            setup,
            ensure_alist=lambda: calls.append("ensure"),
            configure_alist=lambda: calls.append("configure"),
            verify_sharing=lambda: calls.append("range"),
            apply_tailscale=lambda config, install: (
                calls.append("tailscale") or (tailscale_status, message)),
            diagnose_local_host=final_doctor,
        )
        try:
            buffer = io.StringIO()
            original_stdout = sys.stdout
            sys.stdout = buffer
            try:
                code = setup.run_host_wizard("test.conf")
            finally:
                sys.stdout = original_stdout
            return code, buffer.getvalue(), calls
        finally:
            _patch(setup, **saved)

    def test_missing_tailscale_returns_nonzero_after_preserving_alist(self):
        code, output, calls = self._run_wizard(
            {"installed": False, "online": False, "ipv4": ""},
            "未检测到 Tailscale。",
        )
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["ensure", "configure", "range", "tailscale"])
        self.assertIn("首次设置尚未完成", output)
        self.assertIn("AList", output)
        self.assertIn("均已保留", output)
        self.assertNotIn("向导完成。日常使用", output)

    def test_not_logged_in_returns_nonzero_and_requests_retry(self):
        code, output, calls = self._run_wizard(
            {"installed": True, "online": False, "ipv4": ""},
            "请在 Tailscale 窗口完成登录。",
        )
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["ensure", "configure", "range", "tailscale"])
        self.assertIn("完成登录", output)
        self.assertIn("重新运行本向导", output)
        self.assertNotIn("向导完成。日常使用", output)

    def test_ready_tailscale_returns_zero_and_prints_completion(self):
        code, output, calls = self._run_wizard(
            {"installed": True, "online": True, "ipv4": "100.64.0.9"},
            "",
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            calls, ["ensure", "configure", "range", "tailscale", "doctor"])
        self.assertIn("向导完成。日常使用", output)
        self.assertNotIn("首次设置尚未完成", output)
        self.assertIn("默认只自动共享此目录", output)
        self.assertIn("alist_map", output)

    def test_final_doctor_failure_prevents_ready_result(self):
        code, output, calls = self._run_wizard(
            {"installed": True, "online": True, "ipv4": "100.64.0.9"},
            "",
            doctor_code=1,
        )
        self.assertEqual(code, 1)
        self.assertEqual(
            calls, ["ensure", "configure", "range", "tailscale", "doctor"])
        self.assertIn("Funnel               无法确认", output)
        self.assertIn("最终 doctor 未通过", output)
        self.assertIn("不会自动修改 Funnel/Serve", output)
        self.assertNotIn("向导完成。日常使用", output)


class ApplyTailscaleCommandTests(unittest.TestCase):
    def test_incomplete_status_returns_nonzero(self):
        statuses = (
            {"installed": False, "online": False, "ipv4": ""},
            {"installed": True, "online": False, "ipv4": ""},
            {"installed": True, "online": True, "ipv4": ""},
        )
        for status in statuses:
            with self.subTest(status=status):
                saved = _patch(
                    setup,
                    apply_tailscale=lambda _config, _install: (
                        status, "请完成 Tailscale 设置"),
                    log=lambda _message: None,
                )
                try:
                    self.assertEqual(
                        setup.run_apply_tailscale("config", False), 1)
                finally:
                    _patch(setup, **saved)

    def test_ready_status_returns_zero(self):
        status = {
            "installed": True,
            "online": True,
            "ipv4": "100.100.20.30",
        }
        saved = _patch(
            setup,
            apply_tailscale=lambda _config, _install: (status, ""),
            log=lambda _message: None,
        )
        try:
            self.assertEqual(setup.run_apply_tailscale("config", False), 0)
        finally:
            _patch(setup, **saved)


class DiagnoseTests(unittest.TestCase):
    def _media_result(self, **overrides):
        values = {
            "ok": True,
            "http_status": 206,
            "alist_code": None,
            "alist_message": None,
            "signature_required": False,
            "range_supported": True,
            "result": "通过",
            "status_text": "HTTP 206",
        }
        values.update(overrides)
        return types.SimpleNamespace(**values)

    def _run_doctor(self, host="100.64.0.1", status=None, http_get=None,
                    peer=(True, "pong"), port=True, discovery=None,
                    media_result=None, config_path=setup.UI_CONFIG):
        status = status or {
            "installed": True,
            "online": True,
            "backend_state": "Running",
            "ipv4": "100.64.0.9",
            "cli_path": "tailscale.exe",
        }
        http_get = http_get or (lambda url, timeout=6.0: (200, "pong"))
        discovery = discovery or (
            "http://100.64.0.1:5244/d/media/movie.mkv", None, "")
        media_result = media_result or self._media_result()
        saved = _patch(
            setup,
            http_get=http_get,
            probe_tailscale_peer=lambda ts, peer_ip: peer,
            probe_port=lambda host_ip, port_number, timeout=2.0: port,
            find_anonymous_video=lambda origin: discovery,
        )
        saved_ts = _patch(setup.tailscale_integration,
                          query_status=lambda cli=None: status)
        saved_media = _patch(
            setup.alist_diagnostics,
            probe_media_url=lambda url, timeout=8.0: media_result,
        )
        try:
            buffer = io.StringIO()
            original_stdout = sys.stdout
            sys.stdout = buffer
            try:
                code = setup.diagnose_host(host, config_path=config_path)
            finally:
                sys.stdout = original_stdout
            return code, buffer.getvalue()
        finally:
            _patch(setup, **saved)
            _patch(setup.tailscale_integration, **saved_ts)
            _patch(setup.alist_diagnostics, **saved_media)

    def test_tailscale_not_installed_is_reported_before_network(self):
        status = {
            "installed": False,
            "online": False,
            "backend_state": "NotInstalled",
        }
        code, output = self._run_doctor(status=status)
        self.assertEqual(code, 1)
        self.assertIn("Tailscale 未安装", output)

    def test_tailscale_installed_but_not_logged_in_is_specific(self):
        status = {
            "installed": True,
            "online": False,
            "backend_state": "NeedsLogin",
        }
        code, output = self._run_doctor(status=status)
        self.assertEqual(code, 1)
        self.assertIn("Tailscale 未登录", output)

    def test_missing_alist_server_is_specific(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "empty.conf")
            code, output = self._run_doctor(host=None, config_path=config_path)
        self.assertEqual(code, 1)
        self.assertIn("alist_server 尚未配置", output)

    def test_alist_server_is_read_from_viewer_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "syncplay_ui.conf")
            Path(config_path).write_text(
                "alist_server=http://100.64.0.1:5244\n",
                encoding="utf-8",
            )
            code, output = self._run_doctor(host=None, config_path=config_path)
        self.assertEqual(code, 0)
        self.assertIn("alist_server          http://100.64.0.1:5244", output)

    def test_old_https_serve_address_is_rejected(self):
        code, output = self._run_doctor(host="https://host.tailnet.ts.net")
        self.assertEqual(code, 1)
        self.assertIn("旧的 Tailscale Serve/HTTPS", output)

    def test_non_tailscale_ipv4_is_rejected(self):
        code, output = self._run_doctor(host="http://192.168.1.5:5244")
        self.assertEqual(code, 1)
        self.assertIn("不是有效的 Tailscale 100.x", output)

    def test_failed_tailscale_ping_does_not_override_working_tcp(self):
        code, output = self._run_doctor(peer=(False, "no matching peer"))
        self.assertEqual(code, 0)
        self.assertIn("继续检测 5244 TCP", output)
        self.assertIn("忽略 ping 的假阴性", output)
        self.assertIn("HTTP Range           206 Partial Content", output)

    def test_failed_ping_and_5244_mentions_device_share_and_port(self):
        code, output = self._run_doctor(
            peer=(False, "no matching peer"), port=False)
        self.assertEqual(code, 1)
        self.assertIn("无法访问房主 AList 5244 端口", output)
        self.assertIn("尚未接受房主设备共享", output)

    def test_peer_reachable_but_5244_closed_is_specific(self):
        code, output = self._run_doctor(port=False)
        self.assertEqual(code, 1)
        self.assertIn("无法访问房主 AList 5244 端口", output)

    def test_5244_service_must_return_pong(self):
        code, output = self._run_doctor(
            http_get=lambda url, timeout=6.0: (200, "<html>router</html>"))
        self.assertEqual(code, 1)
        self.assertIn("/ping 未返回 pong", output)

    def test_guest_disabled_is_reported_from_anonymous_listing(self):
        code, output = self._run_doctor(
            discovery=(None, "guest", "Guest user is disabled"))
        self.assertEqual(code, 1)
        self.assertIn("AList 游客访问未开启", output)
        self.assertIn("Guest user is disabled", output)

    def test_expire_missing_is_reported_as_signing(self):
        result = self._media_result(
            ok=False,
            http_status=401,
            alist_code=401,
            alist_message="expire missing",
            signature_required=True,
            range_supported=False,
            result="媒体源不可访问",
        )
        code, output = self._run_doctor(media_result=result)
        self.assertEqual(code, 1)
        self.assertIn("AList 仍启用了签名", output)
        self.assertIn("expire missing", output)

    def test_generic_401_is_reported_as_anonymous_permission(self):
        result = self._media_result(
            ok=False,
            http_status=401,
            alist_code=401,
            alist_message="unauthorized",
            range_supported=False,
            result="访问被拒绝",
        )
        code, output = self._run_doctor(media_result=result)
        self.assertEqual(code, 1)
        self.assertIn("匿名访问返回 401", output)

    def test_range_must_be_206_partial_content(self):
        result = self._media_result(
            ok=False,
            http_status=200,
            range_supported=False,
            result="媒体源不支持可靠的 HTTP Range 请求",
        )
        code, output = self._run_doctor(media_result=result)
        self.assertEqual(code, 1)
        self.assertIn("视频服务器不支持 HTTP Range", output)
        self.assertIn("206 Partial Content", output)

    def test_success_reports_anonymous_access_and_206(self):
        code, output = self._run_doctor()
        self.assertEqual(code, 0)
        self.assertIn("匿名视频访问          OK", output)
        self.assertIn("HTTP Range           206 Partial Content", output)
        self.assertIn("观看者网络环境已就绪", output)


class MediaDiscoveryTests(unittest.TestCase):
    def test_anonymous_listing_finds_and_url_encodes_nested_video(self):
        responses = {
            "/media": {
                "code": 200,
                "data": {"content": [{"name": "子目录", "is_dir": True}]},
            },
            "/media/子目录": {
                "code": 200,
                "data": {"content": [{"name": "影片 01.mkv", "is_dir": False}]},
            },
        }

        def post_json(url, payload, timeout=6.0):
            return 200, json.dumps(responses[payload["path"]], ensure_ascii=False)

        saved = _patch(setup, http_post_json=post_json)
        try:
            url, error, detail = setup.find_anonymous_video(
                "http://100.64.0.1:5244")
        finally:
            _patch(setup, **saved)
        self.assertIsNone(error)
        self.assertEqual(detail, "")
        self.assertEqual(
            url,
            "http://100.64.0.1:5244/d/media/%E5%AD%90%E7%9B%AE%E5%BD%95/"
            "%E5%BD%B1%E7%89%87%2001.mkv",
        )

    def test_guest_disabled_response_is_classified(self):
        body = json.dumps({
            "code": 403,
            "message": "Guest user is disabled",
            "data": None,
        })
        saved = _patch(setup, http_post_json=lambda *args, **kwargs: (200, body))
        try:
            url, error, detail = setup.find_anonymous_video(
                "http://100.64.0.1:5244")
        finally:
            _patch(setup, **saved)
        self.assertIsNone(url)
        self.assertEqual(error, "guest")
        self.assertEqual(detail, "Guest user is disabled")


class HostDoctorTests(unittest.TestCase):
    def _run_host_doctor(self, funnel=(False, "{}"), range_ok=True):
        with tempfile.TemporaryDirectory() as directory:
            alist_exe = os.path.join(directory, "alist.exe")
            alist_config = os.path.join(directory, "config.json")
            ui_config = os.path.join(directory, "syncplay_ui.conf")
            Path(alist_exe).write_bytes(b"test")
            Path(alist_config).write_text(json.dumps({
                "scheme": {"address": "0.0.0.0", "http_port": 5244},
            }), encoding="utf-8")
            Path(ui_config).write_text(
                "tailscale_mode=host\n"
                "alist_server=http://100.64.0.9:5244\n",
                encoding="utf-8",
            )
            media_result = types.SimpleNamespace(
                ok=range_ok, range_supported=range_ok,
                http_status=206 if range_ok else 200)
            saved = _patch(
                setup,
                ALIST_EXE=alist_exe,
                ALIST_CONFIG=alist_config,
                http_get=lambda url, timeout=5.0: (200, "pong"),
                inspect_host_alist=lambda: {
                    "media_ok": True,
                    "guest_ok": True,
                    "sign_off": True,
                },
                probe_local_range=lambda: media_result,
                query_funnel_enabled=lambda status: funnel,
            )
            saved_ts = _patch(
                setup.tailscale_integration,
                query_status=lambda cli=None: {
                    "installed": True,
                    "online": True,
                    "backend_state": "Running",
                    "ipv4": "100.64.0.9",
                    "cli_path": "tailscale.exe",
                },
            )
            try:
                buffer = io.StringIO()
                original_stdout = sys.stdout
                sys.stdout = buffer
                try:
                    code = setup.diagnose_local_host(ui_config)
                finally:
                    sys.stdout = original_stdout
                return code, buffer.getvalue()
            finally:
                _patch(setup, **saved)
                _patch(setup.tailscale_integration, **saved_ts)

    def test_ready_host_reports_funnel_off_and_manual_device_share(self):
        code, output = self._run_host_doctor()
        self.assertEqual(code, 0)
        self.assertIn("Funnel               OFF", output)
        self.assertIn("Device Share:", output)
        self.assertIn("手动确认", output)
        self.assertIn("房主共享环境已就绪", output)

    def test_unknown_funnel_is_not_reported_as_off(self):
        code, output = self._run_host_doctor(funnel=(None, "unsupported"))
        self.assertEqual(code, 1)
        self.assertIn("Funnel               无法确认", output)
        self.assertNotIn("Funnel               OFF", output)

    def test_range_failure_prevents_ready_result(self):
        code, output = self._run_host_doctor(range_ok=False)
        self.assertEqual(code, 1)
        self.assertIn("HTTP Range           FAIL", output)
        self.assertIn("Range 请求未返回 206", output)


class FunnelStatusTests(unittest.TestCase):
    def _query(self, payload, returncode=0):
        result = types.SimpleNamespace(
            stdout=payload,
            stderr="",
            returncode=returncode,
        )
        saved = _patch(setup, _run_tailscale_command=lambda *args, **kwargs: result)
        try:
            return setup.query_funnel_enabled({"cli_path": "tailscale.exe"})
        finally:
            _patch(setup, **saved)

    def test_serve_configuration_without_allow_funnel_is_off(self):
        enabled, _detail = self._query(json.dumps({
            "TCP": {"443": {"HTTPS": True}},
            "Web": {"host.ts.net:443": {"Handlers": {}}},
        }))
        self.assertFalse(enabled)

    def test_allow_funnel_true_is_on(self):
        enabled, _detail = self._query(json.dumps({
            "AllowFunnel": {"host.ts.net:443": True},
        }))
        self.assertTrue(enabled)

    def test_unrecognized_error_is_unknown_not_off(self):
        enabled, detail = self._query("unsupported command", returncode=1)
        self.assertIsNone(enabled)
        self.assertIn("unsupported", detail)


class DoctorRoleTests(unittest.TestCase):
    def test_auto_role_uses_viewer_when_config_says_viewer(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = os.path.join(directory, "syncplay_ui.conf")
            Path(config_path).write_text("tailscale_mode=viewer\n", encoding="utf-8")
            saved = _patch(
                setup,
                diagnose_host=lambda host, media, config: 27,
                diagnose_local_host=lambda config: 38,
            )
            try:
                self.assertEqual(setup.run_doctor(config_path=config_path), 27)
            finally:
                _patch(setup, **saved)

    def test_explicit_host_role_wins(self):
        saved = _patch(
            setup,
            diagnose_host=lambda host, media, config: 27,
            diagnose_local_host=lambda config: 38,
        )
        try:
            self.assertEqual(setup.run_doctor(role="host"), 38)
        finally:
            _patch(setup, **saved)


class HostFirewallTests(unittest.TestCase):
    """房主防火墙步骤：必须只提权 netsh，并且失败时给出可操作提示。"""

    def _patch_firewall(self, **overrides):
        defaults = dict(
            IS_WINDOWS=True,
            ALIST_EXE=os.path.join("C:", "fake", "alist.exe"),
            HOST_FIREWALL_SCRIPT=os.path.join("C:", "fake", "configure-host-firewall.bat"),
            is_admin=lambda: False,
            elevate_and_wait=lambda target, parameters=None, timeout=900.0: 0,
            _run_hidden=lambda arguments, timeout=900.0: 0,
        )
        defaults.update(overrides)
        # 两个路径必须"存在"，用临时文件充当。
        directory = tempfile.mkdtemp()
        alist = os.path.join(directory, "alist.exe")
        script = os.path.join(directory, "configure-host-firewall.bat")
        for path in (alist, script):
            Path(path).write_text("", encoding="utf-8")
        defaults["ALIST_EXE"] = alist
        defaults["HOST_FIREWALL_SCRIPT"] = script
        return _patch(setup, **defaults)

    def test_non_windows_is_a_noop(self):
        saved = _patch(setup, IS_WINDOWS=False)
        try:
            self.assertEqual(setup.configure_host_firewall(),
                             {"configured": False, "reason": "unsupported"})
        finally:
            _patch(setup, **saved)

    def test_missing_alist_is_reported(self):
        saved = self._patch_firewall()
        try:
            setup.ALIST_EXE = os.path.join(tempfile.mkdtemp(), "alist.exe")
            with self.assertRaises(setup.SetupError) as context:
                setup.configure_host_firewall()
            self.assertIn("AList", str(context.exception))
        finally:
            _patch(setup, **saved)

    def test_missing_firewall_script_is_reported(self):
        saved = self._patch_firewall()
        try:
            setup.HOST_FIREWALL_SCRIPT = os.path.join(tempfile.mkdtemp(), "nope.bat")
            with self.assertRaises(setup.SetupError) as context:
                setup.configure_host_firewall()
            self.assertIn("configure-host-firewall.bat", str(context.exception))
        finally:
            _patch(setup, **saved)

    def test_admin_runs_netsh_directly_without_prompting(self):
        prompts = []
        saved = self._patch_firewall(
            is_admin=lambda: True,
            elevate_and_wait=lambda *args, **kwargs: prompts.append(args) or 0,
        )
        try:
            result = setup.configure_host_firewall()
        finally:
            _patch(setup, **saved)
        self.assertEqual(prompts, [])
        self.assertTrue(result["configured"])
        self.assertEqual(result["rule"], setup.FIREWALL_RULE_NAME)

    def test_non_admin_elevates_only_the_helper_script(self):
        elevated = []
        saved = self._patch_firewall(
            elevate_and_wait=lambda target, parameters=None, timeout=900.0: (
                elevated.append(target) or 0),
        )
        try:
            expected = setup.HOST_FIREWALL_SCRIPT
            result = setup.configure_host_firewall()
        finally:
            _patch(setup, **saved)
        self.assertEqual(elevated, [expected])
        self.assertTrue(result["configured"])

    def test_cancelled_uac_asks_the_user_to_retry(self):
        saved = self._patch_firewall(
            elevate_and_wait=lambda target, parameters=None, timeout=900.0:
                setup.ELEVATION_CANCELLED,
        )
        try:
            with self.assertRaises(setup.SetupError) as context:
                setup.configure_host_firewall()
        finally:
            _patch(setup, **saved)
        self.assertIn("取消", str(context.exception))
        self.assertIn("房主模式", str(context.exception))

    def test_netsh_failure_is_reported_with_exit_code(self):
        saved = self._patch_firewall(
            elevate_and_wait=lambda target, parameters=None, timeout=900.0: 1,
        )
        try:
            with self.assertRaises(setup.SetupError) as context:
                setup.configure_host_firewall()
        finally:
            _patch(setup, **saved)
        self.assertIn("退出码 1", str(context.exception))


class ModeHostTests(unittest.TestCase):
    """mode-host 是面板一键切换房主模式的入口，必须返回稳定的 JSON 字典。"""

    def _run(self, wizard_code, status):
        calls = []

        def firewall():
            calls.append("firewall")
            return {"configured": True, "rule": setup.FIREWALL_RULE_NAME}

        def wizard(config_path, install_if_missing):
            calls.append("wizard")
            return wizard_code

        saved = _patch(
            setup,
            IS_WINDOWS=True,
            configure_host_firewall=firewall,
            run_host_wizard=wizard,
            tailscale_integration=types.SimpleNamespace(
                query_status=lambda: status,
                read_syncplay_config=lambda path: {
                    "alist_server": "http://100.64.0.9:5244",
                    "alist_root": "~~/../WatchParty/media",
                    "alist_virtual_root": "/media",
                    "tailscale_mode": "host",
                    "tailscale_host": "100.64.0.9",
                },
            ),
        )
        try:
            return setup.run_mode_host("test.conf"), calls
        finally:
            _patch(setup, **saved)

    def test_success_publishes_media_and_returns_payload_for_the_panel(self):
        result, calls = self._run(
            0, {"installed": True, "online": True, "ipv4": "100.64.0.9"})
        self.assertEqual(calls, ["firewall", "wizard"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["tailscale_mode"], "host")
        self.assertEqual(result["alist_server"], "http://100.64.0.9:5244")
        self.assertEqual(result["alist_root"], "~~/../WatchParty/media")
        self.assertTrue(result["tailscale_ready"])
        self.assertIn("Share", result["message"])

    def test_missing_tailscale_keeps_alist_work_and_explains_next_step(self):
        result, _ = self._run(1, {"installed": False, "online": False, "ipv4": ""})
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "tailscale")
        self.assertIn("安装", result["error"])
        self.assertIn("alist_server", result)

    def test_not_logged_in_is_distinguished_from_not_installed(self):
        result, _ = self._run(1, {"installed": True, "online": False, "ipv4": ""})
        self.assertFalse(result["ok"])
        self.assertIn("登录", result["error"])

    def test_ready_tailscale_but_failed_doctor_reports_verify_stage(self):
        result, _ = self._run(
            1, {"installed": True, "online": True, "ipv4": "100.64.0.9"})
        self.assertEqual(result["stage"], "verify")
        self.assertIn("自检", result["error"])


class BootstrapTests(unittest.TestCase):
    """合并包首次运行向导：只准备 Tailscale，角色留给面板选择。"""

    def _run(self, status, windows=True, installer=True):
        calls = []
        messages = []
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "WatchParty")
            os.makedirs(os.path.join(root, "Tailscale"))
            if installer:
                path = os.path.join(root, "Tailscale", "install-tailscale.bat")
                with open(path, "w", encoding="utf-8") as stream:
                    stream.write("@echo off\r\n")
            saved = _patch(
                setup,
                IS_WINDOWS=windows,
                WATCHPARTY_DIR=root,
                _safe_tailscale_status=lambda: status,
                log=messages.append,
                subprocess=types.SimpleNamespace(
                    run=lambda *args, **kwargs: (
                        calls.append(args),
                        types.SimpleNamespace(returncode=0))[1]),
            )
            try:
                code = setup.run_bootstrap("test.conf")
            finally:
                _patch(setup, **saved)
        return code, calls, "\n".join(messages)

    def test_missing_tailscale_opens_the_official_installer(self):
        code, calls, text = self._run({"installed": False, "online": False, "ipv4": ""})
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("install-tailscale.bat", calls[0][0][2])
        self.assertIn("运行模式", text)

    def test_installed_and_connected_never_launches_an_installer(self):
        code, calls, text = self._run(
            {"installed": True, "online": True, "ipv4": "100.64.0.9"})
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertIn("已连接", text)
        self.assertNotIn("安装器", text)

    def test_logged_out_tailscale_is_reported_without_installing(self):
        _code, calls, text = self._run(
            {"installed": True, "online": False, "ipv4": ""})
        self.assertEqual(calls, [])
        self.assertIn("登录", text)


class JsonStandardOutputTests(unittest.TestCase):
    """mpv 面板直接解析 stdout，任何进度日志都不允许混进去。"""

    def _run_cli(self, arguments):
        script = os.path.join(
            os.path.dirname(os.path.abspath(setup.__file__)), "watchparty_setup.py")
        return subprocess.run(
            [sys.executable, script] + arguments,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            timeout=300, check=False)

    def test_progress_logs_go_to_stderr_not_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            completed = self._run_cli([
                "--json",
                "--config", os.path.join(directory, "syncplay_ui.conf"),
                "doctor", "--role", "viewer",
            ])

        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, completed.stdout)
        self.assertFalse(json.loads(lines[0])["ok"])
        # 人读报告必须全都落到 stderr，面板才不会解析失败。
        self.assertTrue(completed.stderr.strip())

    def test_setup_error_is_reported_as_one_json_line(self):
        completed = self._run_cli(["--json", "verify-sharing"])

        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, completed.stdout)
        payload = json.loads(lines[0])
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
