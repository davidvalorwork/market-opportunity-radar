"""Bot API client: offline transport, token redaction, limits, 429, HTML escaping."""

import io
import json
import logging
import traceback
import urllib.error
import urllib.request

import pytest

from radar.adapters.telegram import bot_api
from radar.adapters.telegram.bot_api import BotApi, RateLimited, TelegramError, escape

TOKEN = "123456:TEST-TOKEN-NOT-REAL"


class Transport:
    """Records calls; replies with queued (status, body) pairs."""

    def __init__(self, *replies):
        self.replies = list(replies) or [(200, {"ok": True, "result": {"message_id": 1}})]
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        status, reply = self.replies.pop(0)
        return status, reply if isinstance(reply, bytes) else json.dumps(reply).encode()

    def json(self, index=-1):
        return json.loads(self.calls[index]["data"])


def test_send_message_posts_json_with_token_only_in_path():
    transport = Transport()
    api = BotApi(TOKEN, transport=transport)
    assert api.send_message(10, "hola", protect_content=True) == {"message_id": 1}
    call = transport.calls[0]
    assert call["url"] == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert TOKEN not in call["data"].decode() and TOKEN not in json.dumps(call["headers"])
    assert transport.json() == {"chat_id": 10, "text": "hola", "parse_mode": "HTML", "protect_content": True,
                                "link_preview_options": {"is_disabled": True}}


@pytest.mark.parametrize("text", ["", "x" * 4097, None])
def test_send_message_rejects_bad_length_without_calling(text):
    transport = Transport()
    with pytest.raises(ValueError):
        BotApi(TOKEN, transport=transport).send_message(10, text)
    assert transport.calls == []


def test_send_message_accepts_4096_chars():
    transport = Transport()
    BotApi(TOKEN, transport=transport).send_message(10, "x" * 4096)
    assert len(transport.json()["text"]) == 4096


def test_escape_neutralizes_link_injection_from_listing_title():
    title = 'Perfume <a href="https://evil.example/">gratis</a> & <b>oferta</b>'
    transport = Transport()
    BotApi(TOKEN, transport=transport).send_message(10, f"<b>Oportunidad:</b> {escape(title)}")
    sent = transport.json()["text"]
    assert "<a" not in sent and "href=\"" not in sent and "<b>oferta" not in sent
    assert sent == ("<b>Oportunidad:</b> Perfume &lt;a href=&quot;https://evil.example/&quot;&gt;gratis&lt;/a&gt;"
                    " &amp; &lt;b&gt;oferta&lt;/b&gt;")


def test_429_raises_typed_error_with_retry_after_and_no_retry():
    transport = Transport((429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 17",
                                 "parameters": {"retry_after": 17}}))
    with pytest.raises(RateLimited) as caught:
        BotApi(TOKEN, transport=transport).send_message(10, "hola")
    assert caught.value.retry_after == 17 and caught.value.status == 429 and caught.value.method == "sendMessage"
    assert isinstance(caught.value, TelegramError) and len(transport.calls) == 1


def test_api_error_is_typed_and_non_json_is_handled():
    with pytest.raises(TelegramError) as caught:
        BotApi(TOKEN, transport=Transport((400, {"ok": False, "error_code": 400, "description": "Bad Request"}))).send_message(1, "x")
    assert caught.value.status == 400 and not isinstance(caught.value, RateLimited)
    with pytest.raises(TelegramError, match="non-JSON"):
        BotApi(TOKEN, transport=Transport((502, b"<html>bad gateway</html>"))).send_message(1, "x")


def test_token_never_in_repr_logs_or_exceptions(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    api = BotApi(TOKEN, transport=Transport(
        (200, {"ok": True, "result": True}),
        (401, {"ok": False, "error_code": 401, "description": f"Unauthorized for /bot{TOKEN}/getMe"}),
        (429, {"ok": False, "error_code": 429, "description": "flood", "parameters": {"retry_after": 3}})))
    errors = []
    api.answer_callback_query("q1")
    for _ in range(2):
        try:
            api.send_message(1, "x")
        except TelegramError as exc:
            errors.append(exc)

    def failing_transport(url, data, headers, timeout):
        raise urllib.error.URLError(f"cannot reach {url}")
    try:
        BotApi(TOKEN, transport=failing_transport).send_message(1, "x")
    except TelegramError as exc:
        errors.append(exc)

    def raise_http_error(request, timeout):  # real urllib transport; HTTPError carries the full URL
        reply = json.dumps({"ok": False, "error_code": 500, "description": f"boom {request.full_url}"}).encode()
        raise urllib.error.HTTPError(request.full_url, 500, f"boom {request.full_url}", {}, io.BytesIO(reply))
    monkeypatch.setattr(urllib.request, "urlopen", raise_http_error)
    try:
        BotApi(TOKEN).send_message(1, "x")
    except TelegramError as exc:
        errors.append(exc)

    assert len(errors) == 4
    rendered = [repr(api), caplog.text] + [str(e) for e in errors] + [repr(e) for e in errors]
    rendered += ["".join(traceback.format_exception(e)) for e in errors]
    rendered += [str(vars(e)) for e in errors]
    for text in rendered:
        assert TOKEN not in text and "TEST-TOKEN-NOT-REAL" not in text
    assert all(e.__cause__ is None and (e.__context__ is None or e.__suppress_context__) for e in errors)
    assert errors[3].status == 500  # HTTPError body parsed by the real urllib transport
    assert "<redacted>" in str(errors[0])


def test_send_document_multipart_and_size_limit(monkeypatch):
    transport = Transport()
    api = BotApi(TOKEN, transport=transport)
    api.send_document(10, "informe.html", b"<html>ok</html>", caption="Resumen", protect_content=True)
    call = transport.calls[0]
    assert call["url"].endswith("/sendDocument")
    assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=radar-")
    assert b'name="document"; filename="informe.html"' in call["data"] and b"<html>ok</html>" in call["data"]
    assert b'name="protect_content"\r\n\r\ntrue' in call["data"]
    monkeypatch.setattr(bot_api, "MAX_DOCUMENT_BYTES", 10)
    with pytest.raises(ValueError):
        api.send_document(10, "big.bin", b"x" * 11)
    with pytest.raises(ValueError):
        api.send_document(10, "empty.bin", b"")
    assert len(transport.calls) == 1


def test_document_limit_is_50_mb():
    assert bot_api.MAX_DOCUMENT_BYTES == 50 * 1024 * 1024


def test_set_webhook_requires_explicit_options():
    transport = Transport((200, {"ok": True, "result": True}))
    api = BotApi(TOKEN, transport=transport)
    with pytest.raises(TypeError):
        api.set_webhook("https://example.com/hook", secret_token="s")  # noqa - missing explicit options
    api.set_webhook("https://example.com/hook", secret_token="synthetic_secret-1",
                    allowed_updates=["message", "callback_query"], max_connections=10, drop_pending_updates=False)
    assert transport.json() == {"url": "https://example.com/hook", "secret_token": "synthetic_secret-1",
                                "allowed_updates": ["message", "callback_query"], "max_connections": 10,
                                "drop_pending_updates": False}
    for kwargs in ({"secret_token": "bad secret!"}, {"max_connections": 0}, {"max_connections": 101}):
        options = {"secret_token": "ok", "allowed_updates": [], "max_connections": 10,
                   "drop_pending_updates": False, **kwargs}
        with pytest.raises(ValueError):
            api.set_webhook("https://example.com/hook", **options)
    with pytest.raises(ValueError):
        api.set_webhook("http://example.com/hook", secret_token="ok", allowed_updates=[], max_connections=10,
                        drop_pending_updates=False)


def test_edit_answer_and_commands():
    transport = Transport(*[(200, {"ok": True, "result": True})] * 3)
    api = BotApi(TOKEN, transport=transport)
    api.edit_message_text(10, 7, "Aprobado")
    assert transport.json() == {"chat_id": 10, "message_id": 7, "text": "Aprobado", "parse_mode": "HTML",
                                "link_preview_options": {"is_disabled": True}}
    api.answer_callback_query("q1", "Listo")
    assert transport.json() == {"callback_query_id": "q1", "text": "Listo"}
    api.set_my_commands([("buscar", "Crear búsqueda"), ("salud", "Estado")], scope={"type": "chat", "chat_id": 10})
    assert transport.json()["commands"][0] == {"command": "buscar", "description": "Crear búsqueda"}
    with pytest.raises(ValueError):
        api.set_my_commands([("Bad-Name", "x")])
    with pytest.raises(ValueError):
        api.answer_callback_query("q1", "x" * 201)
    with pytest.raises(ValueError):
        api.edit_message_text(10, 7, "")
