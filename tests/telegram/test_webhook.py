"""Webhook: secret, limits, dedupe, authorization, invites, persistence before ack."""

import base64
from datetime import timedelta
import hashlib
import json

import pytest

from radar.contracts import validate
from radar.entrypoints import lambda_bot
from telegram_fakes import COLLAB_ID, NOW, OWNER_ID, SECRET, STRANGER_ID, Bot, callback, message


def body(response):
    return json.loads(response["body"]) if response["body"] else None


@pytest.fixture
def bot():
    return Bot()


@pytest.mark.parametrize("secret", [None, "", "wrong-secret", SECRET + "x", SECRET[:-1]])
def test_bad_or_missing_secret_is_401_with_zero_effects(bot, secret):
    before = bot.effects()
    response = bot.post(message(1, OWNER_ID, "/salud"), secret=secret)
    assert response["statusCode"] == 401 and response["body"] == ""
    assert bot.effects() == before and bot.uow.calls == []


def test_secret_checked_before_size_and_json(bot):
    assert bot.post(b"x" * 100_000, secret="nope")["statusCode"] == 401
    assert bot.post(b"{not json", secret="nope")["statusCode"] == 401


def test_oversize_body_is_413(bot):
    update = message(1, OWNER_ID, "/salud")
    update["pad"] = "x" * (64 * 1024)
    assert bot.post(update)["statusCode"] == 413
    assert bot.uow.calls == [] and bot.idempotency.keys == set()


@pytest.mark.parametrize("raw", [b"{not json", b"", b"[1,2]", b'"text"', b'{"update_id":"5"}',
                                 b'{"update_id":-1}', b'{"update_id":true}', b"[" * 5000 + b"]" * 5000])
def test_malformed_json_is_400(bot, raw):
    assert bot.post(raw)["statusCode"] == 400
    assert bot.uow.calls == [] and bot.idempotency.keys == set()


def test_valid_owner_command_validates_persists_once_and_acks(bot):
    response = bot.post(message(100000001, OWNER_ID, "/buscar perfume 100 ml"))
    assert response["statusCode"] == 200
    assert body(response) == {"method": "sendMessage", "chat_id": OWNER_ID,
                              "text": "Recibido: /buscar. Te aviso cuando esté listo."}
    assert len(bot.uow.calls) == 1
    receipt, command, outbox = bot.uow.calls[0]
    assert command == {"schema_version": 1, "update_id": 100000001, "telegram_user_ref": "tguser:owner-a",
                       "command": "buscar", "args": {"query": "perfume 100 ml"}}
    validate("telegram.command.v1", command)
    validate("envelope.v1", outbox)
    assert outbox["kind"] == "telegram.command" and outbox["payload"] == command
    assert outbox["owner_ref"] == "owner:radar-pilot" and outbox["deadline"] == "2026-10-03T12:05:00Z"
    assert receipt["idempotency_keys"] == ["tg:update:100000001"]
    assert str(OWNER_ID) not in json.dumps([receipt, command, outbox])  # raw numeric ID never persisted


def test_bot_suffix_and_case_are_normalized(bot):
    bot.post(message(2, OWNER_ID, "/Salud@RadarPilotBot"))
    assert bot.uow.commands[0]["command"] == "salud" and bot.uow.commands[0]["args"] == {}


def test_duplicate_update_id_has_single_effect(bot):
    first = bot.post(message(5, OWNER_ID, "/resumen"))
    second = bot.post(message(5, OWNER_ID, "/resumen"))
    assert first["statusCode"] == second["statusCode"] == 200
    assert body(second) is None
    assert len(bot.uow.calls) == 1 and len(bot.uow.outbox) == 1


def test_concurrent_duplicate_lost_race_is_not_acknowledged(bot):
    bot.idempotency.seen = lambda key: False  # both deliveries passed the early check
    bot.post(message(6, OWNER_ID, "/resumen"))
    second = bot.post(message(6, OWNER_ID, "/resumen"))
    assert second["statusCode"] == 200 and body(second) is None
    assert len(bot.uow.commands) == 1


def test_unauthorized_user_gets_neutral_reply_and_only_dedupe_key(bot):
    response = bot.post(message(7, STRANGER_ID, "/oportunidades"))
    assert response["statusCode"] == 200
    assert body(response) == {"method": "sendMessage", "chat_id": STRANGER_ID, "text": "Este bot es de uso privado."}
    assert bot.uow.calls == [] and bot.idempotency.keys == {"tg:update:7"}
    assert STRANGER_ID not in bot.users.records


def test_unauthorized_callback_gets_neutral_answer(bot):
    response = bot.post(callback(8, STRANGER_ID, "approve:01J9ZX3Q4R5S6T7V8W9XAYBZC2"))
    assert body(response)["method"] == "answerCallbackQuery"
    assert body(response)["text"] == "Este bot es de uso privado."
    assert bot.uow.calls == []


@pytest.mark.parametrize("command", ["buscar", "busquedas", "pendientes", "resumen", "salud"])
def test_collaborator_owner_only_command_rejected(bot, command):
    response = bot.post(message(9, COLLAB_ID, f"/{command} algo"))
    assert body(response)["text"] == "Ese comando es solo para el propietario."
    assert bot.uow.calls == []


@pytest.mark.parametrize("command", ["start", "vincular", "oportunidades", "pedir", "mis_datos", "borrar", "stop"])
def test_collaborator_shared_commands_persist(bot, command):
    assert bot.post(message(10, COLLAB_ID, f"/{command}"))["statusCode"] == 200
    assert [c["command"] for c in bot.uow.commands] == [command]


def test_unknown_command_and_plain_text_rejected_without_storing(bot):
    assert body(bot.post(message(11, OWNER_ID, "/sudo rm")))["text"] == "Comando no reconocido."
    assert body(bot.post(message(12, OWNER_ID, "hola")))["text"] == "Comando no reconocido."
    assert bot.uow.calls == []


def test_oversize_argument_rejected(bot):
    assert body(bot.post(message(13, OWNER_ID, "/buscar " + "a" * 257)))["text"].startswith("Texto demasiado largo")
    assert bot.uow.calls == []


def test_group_chat_is_ignored(bot):
    response = bot.post(message(14, OWNER_ID, "/salud", chat_type="group"))
    assert response["statusCode"] == 200 and body(response) is None and bot.uow.calls == []


def test_persistence_failure_returns_500_without_ack():
    bot = Bot(fail=True)
    response = bot.post(message(15, OWNER_ID, "/salud"))
    assert response["statusCode"] == 500 and response["body"] == ""
    assert bot.idempotency.keys == set()  # retry will be processed, not dropped as duplicate


def test_numeric_user_ref_fails_schema_and_is_not_stored(bot):
    bot.users.records[OWNER_ID]["user_ref"] = f"tguser:{OWNER_ID}"  # raw numeric ID: schema forbids it
    response = bot.post(message(17, OWNER_ID, "/salud"))
    assert response["statusCode"] == 500 and response["body"] == ""
    assert bot.uow.calls == [] and bot.idempotency.keys == set()


def test_dependency_failure_fails_closed(bot):
    def boom(user_id):
        raise ConnectionError("synthetic directory outage")
    bot.users.get = boom
    assert bot.post(message(16, OWNER_ID, "/salud"))["statusCode"] == 500


def test_invite_code_enrolls_collaborator_once(bot):
    bot.invites.add("synthetic-invite_code-1", NOW + timedelta(hours=1))
    response = bot.post(message(20, STRANGER_ID, "/start synthetic-invite_code-1"))
    assert body(response)["text"].startswith("Recibido: /start")
    assert bot.users.records[STRANGER_ID]["role"] == "collaborator"
    assert bot.uow.commands[-1]["args"] == {}  # the code is never persisted in the command
    assert "synthetic-invite_code-1" not in json.dumps(bot.uow.calls)
    assert bot.invites.calls == [hashlib.sha256(b"synthetic-invite_code-1").hexdigest()]


def test_reused_invite_code_rejected(bot):
    bot.invites.add("synthetic-invite_code-2", NOW + timedelta(hours=1))
    bot.post(message(21, STRANGER_ID, "/start synthetic-invite_code-2"))
    other = 900000004
    response = bot.post(message(22, other, "/start synthetic-invite_code-2"))
    assert body(response)["text"] == "Este bot es de uso privado."
    assert other not in bot.users.records


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(seconds=-1), timedelta(days=-1)])
def test_expired_invite_code_rejected(bot, delta):
    bot.invites.add("synthetic-invite_code-3", NOW + delta)
    response = bot.post(message(23, STRANGER_ID, "/start synthetic-invite_code-3"))
    assert body(response)["text"] == "Este bot es de uso privado."
    assert STRANGER_ID not in bot.users.records and bot.uow.calls == []


def test_unknown_or_malformed_invite_code_rejected(bot):
    bot.post(message(24, STRANGER_ID, "/start nope"))
    bot.post(message(25, STRANGER_ID, "/start bad code!"))
    bot.post(message(26, STRANGER_ID, "/start " + "a" * 65))
    assert STRANGER_ID not in bot.users.records
    assert len(bot.invites.calls) == 1  # only the well-formed code reached the store


def test_valid_callback_persisted_and_answered(bot):
    response = bot.post(callback(30, OWNER_ID, "approve:01J9ZX3Q4R5S6T7V8W9XAYBZC2"))
    assert body(response) == {"method": "answerCallbackQuery", "callback_query_id": "cbq-synthetic-1",
                              "text": "Recibido."}
    command = bot.uow.commands[0]
    assert command["command"] == "callback" and command["callback_ref"] == "approve:01J9ZX3Q4R5S6T7V8W9XAYBZC2"
    validate("telegram.command.v1", command)


@pytest.mark.parametrize("data", ["a" * 65, "", "approve:<script>", "approve id", "ñ", None, 42, {"x": 1}])
def test_malformed_callback_data_rejected(bot, data):
    response = bot.post(callback(31, OWNER_ID, data))
    assert body(response) == {"method": "answerCallbackQuery", "callback_query_id": "cbq-synthetic-1",
                              "text": "Acción no válida."}
    assert bot.uow.calls == [] and bot.idempotency.keys == {"tg:update:31"}


def test_double_approve_has_single_effect(bot):
    data = "approve:01J9ZX3Q4R5S6T7V8W9XAYBZC2"
    first = bot.post(callback(40, OWNER_ID, data, query_id="q1"))
    second = bot.post(callback(41, OWNER_ID, data, query_id="q2"))  # new update_id, same button
    assert body(first)["text"] == "Recibido."
    assert body(second) == {"method": "answerCallbackQuery", "callback_query_id": "q2",
                            "text": "Esta acción ya se procesó."}
    assert len(bot.uow.commands) == 1 and len(bot.uow.outbox) == 1


def test_lambda_handler_fails_closed_without_wiring(monkeypatch):
    monkeypatch.delenv(lambda_bot.WIRING_ENV, raising=False)
    monkeypatch.setattr(lambda_bot, "_webhook", None)
    event = {"headers": {"x-telegram-bot-api-secret-token": SECRET}, "body": json.dumps(message(1, OWNER_ID, "/salud"))}
    assert lambda_bot.handler(event, None)["statusCode"] == 503


def test_lambda_handler_wiring_error_is_500(monkeypatch):
    monkeypatch.setenv(lambda_bot.WIRING_ENV, "telegram_fakes:missing_factory")
    monkeypatch.setattr(lambda_bot, "_webhook", None)
    assert lambda_bot.handler({"body": "{}"}, None)["statusCode"] == 500
    assert lambda_bot._webhook is None


def test_lambda_handler_function_url_event(monkeypatch):
    monkeypatch.setenv(lambda_bot.WIRING_ENV, "telegram_fakes:build")
    monkeypatch.setattr(lambda_bot, "_webhook", None)
    raw = json.dumps(message(50, OWNER_ID, "/salud")).encode()
    event = {"headers": {"x-telegram-bot-api-secret-token": SECRET, "content-type": "application/json"},
             "isBase64Encoded": True, "body": base64.b64encode(raw).decode(),
             "requestContext": {"http": {"method": "POST"}}}
    response = lambda_bot.handler(event, None)
    assert response["statusCode"] == 200 and body(response)["method"] == "sendMessage"
    assert lambda_bot.handler({**event, "headers": {}}, None)["statusCode"] == 401
    assert lambda_bot.handler({**event, "body": "%%%not-base64"}, None)["statusCode"] == 400
    plain = {"headers": event["headers"], "isBase64Encoded": False, "body": json.dumps(message(51, OWNER_ID, "/salud"))}
    assert lambda_bot.handler(plain, None)["statusCode"] == 200
