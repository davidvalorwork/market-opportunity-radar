"""Minimal Telegram Bot API client over HTTPS (urllib, no third-party code).

The bot token only ever appears in the request URL path. Logs, exceptions and
repr() never contain it: errors carry the method name, HTTP status and a
redacted description, and transport exceptions are re-raised without chaining.

429 responses raise ``RateLimited`` with ``retry_after`` (seconds); the caller
decides when to retry (queue visibility, scheduler). This module never sleeps.

``transport(url, data, headers, timeout) -> (status, body_bytes)`` is injectable
so tests run offline; the default uses urllib.request.
"""

import html
import json
import logging
import re
import secrets
import urllib.error
import urllib.request

API_BASE = "https://api.telegram.org"
MAX_TEXT = 4096
MAX_CAPTION = 1024
MAX_CALLBACK_ANSWER = 200
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
SECRET_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,256}")
COMMAND_NAME = re.compile(r"[a-z0-9_]{1,32}")

log = logging.getLogger(__name__)


def escape(text):
    """Escape external text (listing titles, user input) for parse_mode=HTML."""
    return html.escape(str(text), quote=True)


class TelegramError(Exception):
    """Bot API call failed. Never holds the token or the request URL."""

    def __init__(self, method, status, description, retry_after=None):
        self.method, self.status, self.description, self.retry_after = method, status, description, retry_after
        super().__init__(f"telegram {method} failed: status={status} {description}")


class RateLimited(TelegramError):
    """HTTP 429 / flood control; wait ``retry_after`` seconds before retrying."""


def urllib_transport(url, data, headers, timeout):
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https base
            return response.status, response.read()
    except urllib.error.HTTPError as exc:  # Telegram answers 4xx/5xx with a JSON body
        with exc:
            return exc.code, exc.read()


class BotApi:
    def __init__(self, token, transport=urllib_transport, base_url=API_BASE, timeout=10):
        if not token or "/" in token:
            raise ValueError("invalid bot token")
        self._token = token
        self._transport = transport
        self._base_url = base_url
        self._timeout = timeout

    def __repr__(self):
        return "BotApi(token=<redacted>)"

    def _redact(self, text):
        return str(text).replace(self._token, "<redacted>")

    def call(self, method, params=None, files=None):
        """POST a Bot API method; return ``result`` or raise TelegramError/RateLimited."""
        params = {key: value for key, value in (params or {}).items() if value is not None}
        if files:
            data, content_type = _multipart(params, files)
        else:
            data, content_type = json.dumps(params, ensure_ascii=False).encode("utf-8"), "application/json"
        url = f"{self._base_url}/bot{self._token}/{method}"
        try:
            status, body = self._transport(url, data, {"Content-Type": content_type}, self._timeout)
        except Exception as exc:  # network/TLS/timeout: drop the chained exception, it may embed the URL
            log.warning("telegram %s transport error: %s", method, type(exc).__name__)
            raise TelegramError(method, None, self._redact(f"transport error: {type(exc).__name__}: {exc}")) from None
        try:
            reply = json.loads(body)
        except ValueError:
            reply = None
        if not isinstance(reply, dict):
            log.warning("telegram %s status=%s non-json reply", method, status)
            raise TelegramError(method, status, "non-JSON reply")
        if reply.get("ok") is True and status == 200:
            log.info("telegram %s status=200", method)
            return reply.get("result")
        description = self._redact(reply.get("description", ""))[:200]
        retry_after = (reply.get("parameters") or {}).get("retry_after")
        log.warning("telegram %s status=%s retry_after=%s", method, status, retry_after)
        if status == 429 or reply.get("error_code") == 429 or retry_after is not None:
            raise RateLimited(method, status, description, retry_after=int(retry_after or 1))
        raise TelegramError(method, status, description)

    def download_file(self, file_id, *, max_bytes=20 * 1024 * 1024):
        """getFile + GET of the file (Bot API cap: 20 MB). Token never leaves this object or its errors."""
        info = self.call("getFile", {"file_id": file_id})
        path = info.get("file_path") if isinstance(info, dict) else None
        if not isinstance(path, str) or ".." in path or (info.get("file_size") or 0) > max_bytes:
            raise TelegramError("getFile", None, "file unavailable or too large")
        try:
            with urllib.request.urlopen(f"{self._base_url}/file/bot{self._token}/{path}", timeout=60) as response:  # noqa: S310
                data = response.read(max_bytes + 1)
        except Exception as exc:
            raise TelegramError("downloadFile", None, self._redact(f"transport error: {type(exc).__name__}")) from None
        if len(data) > max_bytes:
            raise TelegramError("downloadFile", None, "file too large")
        return data, path

    def send_message(self, chat_id, text, *, parse_mode="HTML", reply_markup=None,
                     protect_content=False, disable_link_preview=True):
        """Send text (1-4096 chars). Escape every external fragment with escape()."""
        _check_text(text, MAX_TEXT)
        return self.call("sendMessage", {
            "chat_id": chat_id, "text": text, "parse_mode": parse_mode, "reply_markup": reply_markup,
            "protect_content": protect_content or None,
            "link_preview_options": {"is_disabled": True} if disable_link_preview else None,
        })

    def edit_message_text(self, chat_id, message_id, text, *, parse_mode="HTML", reply_markup=None,
                          disable_link_preview=True):
        _check_text(text, MAX_TEXT)
        return self.call("editMessageText", {
            "chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": parse_mode,
            "reply_markup": reply_markup,
            "link_preview_options": {"is_disabled": True} if disable_link_preview else None,
        })

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        if text is not None and len(text) > MAX_CALLBACK_ANSWER:
            raise ValueError(f"callback answer exceeds {MAX_CALLBACK_ANSWER} characters")
        return self.call("answerCallbackQuery", {
            "callback_query_id": callback_query_id, "text": text, "show_alert": show_alert or None,
        })

    def send_document(self, chat_id, filename, content, *, caption=None, parse_mode="HTML", protect_content=False):
        """Upload bytes as a document (<= 50 MB) via multipart/form-data."""
        if not content or len(content) > MAX_DOCUMENT_BYTES:
            raise ValueError(f"document must be 1..{MAX_DOCUMENT_BYTES} bytes")
        if caption is not None:
            _check_text(caption, MAX_CAPTION)
        return self.call("sendDocument", {
            "chat_id": chat_id, "caption": caption, "parse_mode": parse_mode if caption else None,
            "protect_content": protect_content or None,
        }, files={"document": (filename, content)})

    def set_webhook(self, url, *, secret_token, allowed_updates, max_connections, drop_pending_updates):
        """Every safety-relevant option is a required keyword: nothing left to API defaults."""
        if not url.startswith("https://"):
            raise ValueError("webhook url must be https")
        if not SECRET_TOKEN.fullmatch(secret_token or ""):
            raise ValueError("secret_token must be 1-256 chars of A-Z a-z 0-9 _ -")
        if not 1 <= max_connections <= 100:
            raise ValueError("max_connections must be 1..100")
        return self.call("setWebhook", {
            "url": url, "secret_token": secret_token, "allowed_updates": list(allowed_updates),
            "max_connections": max_connections, "drop_pending_updates": bool(drop_pending_updates),
        })

    def set_my_commands(self, commands, scope=None):
        """commands: iterable of (name, description); scope selects the per-role menu."""
        items = [{"command": name, "description": description} for name, description in commands]
        for item in items:
            if not COMMAND_NAME.fullmatch(item["command"]) or not 1 <= len(item["description"]) <= 256:
                raise ValueError(f"invalid bot command: {item['command']!r}")
        return self.call("setMyCommands", {"commands": items, "scope": scope})


def _check_text(text, limit):
    if not isinstance(text, str) or not 1 <= len(text) <= limit:
        raise ValueError(f"text must be 1..{limit} characters")


def _multipart(params, files):
    boundary = f"radar-{secrets.token_hex(16)}"
    parts = []
    for name, value in params.items():
        if not isinstance(value, str):
            value = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, (filename, content) in files.items():
        safe = re.sub(r'[^A-Za-z0-9._-]', "_", filename)[:128] or "file"
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{safe}"\r\n'
                     f'Content-Type: application/octet-stream\r\n\r\n'.encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
