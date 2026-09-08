#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Connectivity checks for public AList media URLs.

The probe deliberately uses no Cookie, Authorization header, or AList API.  It
requests one byte so an ignored Range header cannot download the whole video.
"""

import argparse
import http.client
import json
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request


MEDIA_ACCESS_ERROR = "媒体源不可访问，请检查 AList 签名或权限"
DEFAULT_TIMEOUT = 8.0
MAX_RESPONSE_BYTES = 64 * 1024
_CONTENT_RANGE_RE = re.compile(
    r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE
)


class MediaTestResult:
    """Structured result plus the stable, human-readable diagnostic report."""

    def __init__(self, url):
        self.url = url
        self.final_url = None
        self.http_status = None
        self.alist_code = None
        self.alist_message = None
        self.content_type = None
        self.accept_ranges = None
        self.content_range = None
        self.range_supported = False
        self.cookie_required = None
        self.referer_required = None
        self.signature_required = False
        self.ok = False
        self.result = "媒体源检测尚未完成"
        self.network_error = None
        self._referer_probe = False

    @property
    def status_text(self):
        if self.http_status is None:
            return "请求失败%s" % (
                " (%s)" % self.network_error if self.network_error else ""
            )
        text = "HTTP %s" % self.http_status
        if self.alist_code is not None and self.alist_code != 200:
            text += " / AList %s" % self.alist_code
            if self.alist_message:
                text += " (%s)" % self.alist_message
        return text

    @property
    def auth_text(self):
        if self.signature_required:
            return "需要 AList 签名；Cookie: 未发送；Referer: 未发送"
        if self.referer_required is True:
            return "Cookie: 不需要；Referer: 需要（同源 Referer 探测成功）"
        if self.ok:
            return "Cookie: 不需要；Referer: 不需要（均未发送）"
        if self.cookie_required is False and self.referer_required is False:
            return "Cookie: 不需要；Referer: 不需要（匿名请求已取得媒体响应）"
        if self.cookie_required is True:
            return "Cookie: 可能需要；Referer: 未确认（探测未发送 Cookie）"
        return "Cookie: 未发送，是否需要未知；Referer: 未发送，是否需要未知"

    @property
    def range_text(self):
        accept_ranges = self.accept_ranges or "未声明"
        content_range = self.content_range or "未提供"
        if self.range_supported:
            prefix = "支持，但仅在发送 Referer 时" if self._referer_probe else "支持"
            return "%s（Content-Range: %s；Accept-Ranges: %s）" % (
                prefix, content_range, accept_ranges
            )
        if self.http_status is None:
            return "不可用（请求失败；Accept-Ranges: %s）" % accept_ranges
        return "不支持（Range 请求返回 HTTP %s；Content-Range: %s；Accept-Ranges: %s）" % (
            self.http_status, content_range, accept_ranges
        )

    def as_dict(self):
        return {
            "url": self.url,
            "ok": self.ok,
            "http_status": self.http_status,
            "alist_code": self.alist_code,
            "alist_message": self.alist_message,
            "content_type": self.content_type,
            "accept_ranges": self.accept_ranges,
            "content_range": self.content_range,
            "range_supported": self.range_supported,
            "cookie_required": self.cookie_required,
            "referer_required": self.referer_required,
            "signature_required": self.signature_required,
            "status": self.status_text,
            "auth": self.auth_text,
            "range": self.range_text,
            "result": self.result,
        }

    def format_report(self):
        return "\n".join((
            "[AList Media Test]",
            "",
            "URL: %s" % self.url,
            "Status: %s" % self.status_text,
            "Auth: %s" % self.auth_text,
            "Range: %s" % self.range_text,
            "Content-Type: %s" % (self.content_type or "未提供"),
            "Result: %s" % self.result,
        ))


class _ResponseSample:
    def __init__(self):
        self.status = None
        self.final_url = None
        self.content_type = None
        self.accept_ranges = None
        self.content_range = None
        self.set_cookie = None
        self.body = b""
        self.error = None


def _validated_url(value):
    if (not isinstance(value, str) or not value or len(value) > 16384 or
            "\0" in value or any(character.isspace() for character in value)):
        return None
    try:
        value.encode("utf-8")
        parsed = urllib.parse.urlsplit(value)
        parsed.port
    except (TypeError, ValueError, UnicodeEncodeError):
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return value


def _request_sample(url, timeout, referer=None):
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Range": "bytes=0-0",
        "User-Agent": "mpv-syncplay-alist-test/1.0",
    }
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers, method="GET")
    # A tailnet URL is private connectivity, and ambient HTTP(S) proxies can
    # both leak the media URL and turn a healthy Tailscale Serve endpoint into
    # a misleading TLS failure.  Diagnose the client-to-source path directly.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    completed = threading.Event()
    state = {}

    # urllib's socket timeout applies to each read, so a peer sending one byte
    # at a time could otherwise hold this probe indefinitely.  Keep the whole
    # request behind one wall-clock deadline and close its response on timeout.
    def perform_request():
        sample = _ResponseSample()
        state["active_sample"] = sample
        response = None
        try:
            try:
                response = opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            state["response"] = response
            sample.status = response.getcode()
            sample.final_url = response.geturl()
            sample.content_type = response.headers.get("Content-Type")
            sample.accept_ranges = response.headers.get("Accept-Ranges")
            sample.content_range = response.headers.get("Content-Range")
            sample.set_cookie = response.headers.get("Set-Cookie")

            content_length = response.headers.get("Content-Length")
            try:
                content_length = int(content_length)
            except (TypeError, ValueError, OverflowError):
                content_length = None
            if sample.status == 206:
                read_limit = 1
            elif content_length is not None and 0 <= content_length <= MAX_RESPONSE_BYTES:
                read_limit = max(1, content_length)
            else:
                read_limit = min(4096, MAX_RESPONSE_BYTES)
            sample.body = response.read(read_limit)[:MAX_RESPONSE_BYTES]
        except http.client.IncompleteRead as exc:
            sample.body = (exc.partial or b"")[:MAX_RESPONSE_BYTES]
            sample.error = str(exc)
        except (OSError, ValueError, urllib.error.URLError,
                http.client.HTTPException) as exc:
            sample.error = str(getattr(exc, "reason", exc))
        finally:
            if response is not None:
                try:
                    response.close()
                except OSError:
                    pass
            state["sample"] = sample
            completed.set()

    worker = threading.Thread(
        target=perform_request, name="alist-media-probe", daemon=True
    )
    worker.start()
    if completed.wait(timeout):
        return state["sample"]

    response = state.get("response")
    if response is not None:
        try:
            response.close()
        except OSError:
            pass
    sample = _ResponseSample()
    active_sample = state.get("active_sample")
    if active_sample is not None:
        for name in ("status", "final_url", "content_type", "accept_ranges",
                     "content_range", "set_cookie", "body"):
            setattr(sample, name, getattr(active_sample, name))
    sample.error = "请求超过 %.1f 秒" % timeout
    return sample


def _body_text(sample):
    if not sample.body:
        return ""
    charset = "utf-8"
    content_type = sample.content_type or ""
    match = re.search(r"charset\s*=\s*([^;\s]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1).strip("\"'")
    try:
        return sample.body.decode(charset, "replace")
    except LookupError:
        return sample.body.decode("utf-8", "replace")


def _alist_response(sample):
    text = _body_text(sample).lstrip("\ufeff\r\n\t ")
    content_type = (sample.content_type or "").lower()
    if not text.startswith("{") and "json" not in content_type:
        return None, None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(payload, dict) or "code" not in payload:
        return None, None
    code = payload.get("code")
    if isinstance(code, str) and code.isdigit():
        code = int(code)
    if not isinstance(code, int) or isinstance(code, bool):
        code = None
    message = payload.get("message")
    if not isinstance(message, str):
        message = None
    return code, message


def _valid_content_range(value):
    if not value:
        return False
    match = _CONTENT_RANGE_RE.fullmatch(value.strip())
    if not match:
        return False
    start, end = int(match.group(1)), int(match.group(2))
    total = match.group(3)
    if start != 0 or end != 0:
        return False
    return total == "*" or int(total) > end


def _looks_like_login(sample, text):
    try:
        path = urllib.parse.urlsplit(sample.final_url or "").path.lower()
    except ValueError:
        path = ""
    lowered = text.lower()
    return ("/@login" in path or "type=\"password\"" in lowered or
            "type='password'" in lowered or "密码登录" in text or
            ("<html" in lowered and "login" in lowered))


def _analyse(url, sample):
    result = MediaTestResult(url)
    result.final_url = sample.final_url
    result.http_status = sample.status
    result.content_type = sample.content_type
    result.accept_ranges = sample.accept_ranges
    result.content_range = sample.content_range
    result.network_error = sample.error
    result.alist_code, result.alist_message = _alist_response(sample)

    message = result.alist_message or ""
    body = _body_text(sample)
    combined = (message + "\n" + body).lower()
    result.signature_required = (
        result.alist_code == 401 and "expire missing" in combined
    )
    result.range_supported = (
        sample.status == 206 and _valid_content_range(sample.content_range) and
        len(sample.body) >= 1
    )
    login_page = _looks_like_login(sample, body)
    content_type = (sample.content_type or "").lower()
    non_media_document = ("application/json" in content_type or
                          "text/html" in content_type)
    anonymous_media_response = (
        sample.status in (200, 206) and
        result.alist_code in (None, 200) and
        not login_page and not non_media_document
    )
    if anonymous_media_response:
        result.cookie_required = False
        result.referer_required = False

    if result.signature_required:
        result.result = MEDIA_ACCESS_ERROR
    elif sample.error:
        result.result = "%s失败：%s" % (
            "媒体源连接" if sample.status is None else "媒体源响应读取",
            sample.error,
        )
    elif login_page:
        result.cookie_required = True
        result.result = "媒体源返回登录页，当前共享模式不可用"
    elif sample.status in (401, 403):
        result.cookie_required = bool(sample.set_cookie) or sample.status == 401
        result.result = "媒体源访问被拒绝（HTTP %s）" % sample.status
    elif result.alist_code is not None and result.alist_code != 200:
        result.result = "AList 返回错误：%s%s" % (
            result.alist_code,
            " (%s)" % result.alist_message if result.alist_message else "",
        )
    elif sample.status is not None and not 200 <= sample.status < 300:
        if sample.status == 404:
            result.result = "媒体源不存在或路径映射错误（HTTP 404）"
        elif sample.status == 416:
            result.result = "媒体源拒绝 Range 请求（HTTP 416）"
        elif sample.status == 429:
            result.result = "媒体源请求过于频繁（HTTP 429）"
        elif sample.status >= 500:
            result.result = "AList 或上游存储服务异常（HTTP %s）" % sample.status
        else:
            result.result = "媒体源请求失败（HTTP %s）" % sample.status
    elif not result.range_supported:
        result.result = "媒体源不支持可靠的 HTTP Range 请求"
    elif non_media_document:
        result.result = "媒体源响应不是可播放的媒体内容"
    else:
        result.ok = True
        result.cookie_required = False
        result.referer_required = False
        result.result = "通过：无需 Cookie/Referer，且支持 HTTP Range"
    return result, combined


def _same_origin_referer(url):
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def probe_media_url(url, timeout=DEFAULT_TIMEOUT):
    """Test a public media URL without credentials and return a report object."""
    validated = _validated_url(url)
    if validated is None:
        result = MediaTestResult(str(url))
        result.result = "媒体源 URL 必须是无凭据的 HTTP(S) 地址"
        return result
    try:
        timeout = max(0.1, min(float(timeout), 60.0))
    except (TypeError, ValueError, OverflowError):
        timeout = DEFAULT_TIMEOUT

    sample = _request_sample(validated, timeout)
    result, response_text = _analyse(validated, sample)
    if result.ok or result.signature_required or sample.status is None:
        return result

    referer_hint = "referer" in response_text or "referrer" in response_text or "防盗链" in response_text
    if sample.status == 403 or referer_hint:
        referer_sample = _request_sample(
            validated, timeout, referer=_same_origin_referer(validated)
        )
        referer_result, _unused = _analyse(validated, referer_sample)
        if referer_result.ok:
            referer_result.ok = False
            referer_result.referer_required = True
            referer_result.cookie_required = False
            referer_result._referer_probe = True
            referer_result.result = "媒体源需要 Referer，当前共享模式不可用"
            return referer_result
    return result


def print_report(result, stream=None):
    stream = stream or sys.stdout
    text = result.format_report()
    try:
        print(text, file=stream, flush=True)
    except UnicodeEncodeError:
        raw_stream = getattr(stream, "buffer", None)
        if raw_stream is None:
            raise
        raw_stream.write((text + "\n").encode(
            getattr(stream, "encoding", None) or "utf-8", "replace"
        ))
        raw_stream.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="检测 AList 公开媒体直链和 HTTP Range")
    parser.add_argument("media_url", help="要检测的 AList /d/ 媒体地址")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help="每次请求的超时秒数（默认 8）")
    args = parser.parse_args(argv)
    result = probe_media_url(args.media_url, args.timeout)
    print_report(result)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
