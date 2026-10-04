"""Consent gate: versioned text, Accept/Decline, persistence before answer, re-prompt on version bump."""

from datetime import timedelta
import json
import re

import pytest

from radar.adapters.telegram import consent
from radar.adapters.telegram.consent import ACCEPT, CONSENT_TEXT, CONSENT_VERSION, DECLINE
from radar.adapters.telegram.webhook import CALLBACK_DATA
from telegram_fakes import COLLAB_ID, NOW, OWNER_ID, STRANGER_ID, Bot, callback, message

APPROVE = "approve:01J9ZX3Q4R5S6T7V8W9XAYBZC2"


def body(response):
    return json.loads(response["body"]) if response["body"] else None


def unconsent(bot, user_id, version=None):
    record = bot.users.records[user_id]
    record.pop("consent_accepted_at", None)
    record.pop("consent_version", None)
    if version is not None:
        record["consent_version"] = version


def assert_prompt(method, chat_id, intro=None):
    assert method["method"] == "sendMessage" and method["chat_id"] == chat_id
    assert method["parse_mode"] == "HTML"
    assert method["text"] == (CONSENT_TEXT if intro is None else f"{intro}\n\n{CONSENT_TEXT}")
    buttons = method["reply_markup"]["inline_keyboard"]
    assert [b["callback_data"] for row in buttons for b in row] == [ACCEPT, DECLINE]


@pytest.fixture
def bot():
    return Bot()


@pytest.mark.parametrize("user_id, text", [
    (OWNER_ID, "/buscar perfume 100 ml"),  # owner is gated too
    (OWNER_ID, "/vincular"),
    (COLLAB_ID, "/vincular"),
    (COLLAB_ID, "/buscar algo"),           # consent comes before the owner-only check
    (COLLAB_ID, "/pedir zapatos 42"),
    (COLLAB_ID, "/oportunidades"),
    (COLLAB_ID, "hola"),
])
def test_unconsented_user_gets_prompt_and_command_is_not_stored(bot, user_id, text):
    unconsent(bot, user_id)
    response = bot.post(message(60, user_id, text))
    assert response["statusCode"] == 200
    assert_prompt(body(response), user_id)
    assert bot.uow.calls == [] and bot.users.consent_calls == []
    assert bot.idempotency.keys == {"tg:update:60"}


@pytest.mark.parametrize("command", ["mis_datos", "borrar", "stop"])
def test_habeas_data_commands_work_without_consent(bot, command):
    unconsent(bot, COLLAB_ID)
    response = bot.post(message(61, COLLAB_ID, f"/{command}"))
    assert body(response) == {"method": "sendMessage", "chat_id": COLLAB_ID,
                              "text": f"Recibido: /{command}. Te aviso cuando esté listo."}
    assert [c["command"] for c in bot.uow.commands] == [command]


def test_unconsented_start_is_stored_and_answered_with_ack_plus_prompt(bot):
    unconsent(bot, COLLAB_ID)
    response = bot.post(message(62, COLLAB_ID, "/start"))
    assert_prompt(body(response), COLLAB_ID, intro="Recibido: /start. Te aviso cuando esté listo.")
    assert [c["command"] for c in bot.uow.commands] == ["start"]


def test_invited_user_starts_unconsented_and_must_accept(bot):
    bot.invites.add("synthetic-invite_code-9", NOW + timedelta(hours=1))
    response = bot.post(message(63, STRANGER_ID, "/start synthetic-invite_code-9"))
    assert_prompt(body(response), STRANGER_ID, intro="Recibido: /start. Te aviso cuando esté listo.")
    assert "consent_version" not in bot.users.records[STRANGER_ID]
    assert_prompt(body(bot.post(message(64, STRANGER_ID, "/vincular"))), STRANGER_ID)
    assert [c["command"] for c in bot.uow.commands] == ["start"]


def test_accept_records_version_and_timestamp_then_commands_work(bot):
    unconsent(bot, COLLAB_ID)
    response = bot.post(callback(65, COLLAB_ID, ACCEPT))
    assert body(response) == {"method": "answerCallbackQuery", "callback_query_id": "cbq-synthetic-1",
                              "text": consent.ACCEPTED_TEXT}
    assert bot.users.consent_log == [("tguser:collab-b", CONSENT_VERSION, NOW)]
    assert bot.users.records[COLLAB_ID]["consent_version"] == CONSENT_VERSION
    assert bot.users.records[COLLAB_ID]["consent_accepted_at"] == NOW
    assert bot.idempotency.keys == {"tg:update:65", f"tg:consent:tguser:collab-b:{CONSENT_VERSION}"}
    assert bot.uow.calls == []  # consent is not a telegram.command
    assert str(COLLAB_ID) not in json.dumps(bot.users.consent_calls, default=str)
    assert body(bot.post(message(66, COLLAB_ID, "/vincular")))["text"].startswith("Recibido: /vincular")
    assert [c["command"] for c in bot.uow.commands] == ["vincular"]


def test_accept_persistence_failure_is_500_without_answer_and_retry_succeeds():
    bot = Bot(fail_consent=True)
    unconsent(bot, COLLAB_ID)
    response = bot.post(callback(67, COLLAB_ID, ACCEPT))
    assert response["statusCode"] == 500 and response["body"] == ""
    assert bot.idempotency.keys == set() and "consent_version" not in bot.users.records[COLLAB_ID]
    bot.users.fail_consent = False  # Telegram retries the same update: processed, not dropped as duplicate
    assert body(bot.post(callback(67, COLLAB_ID, ACCEPT)))["text"] == consent.ACCEPTED_TEXT
    assert len(bot.users.consent_log) == 1


def test_decline_stores_only_the_dedupe_key(bot):
    unconsent(bot, COLLAB_ID)
    response = bot.post(callback(68, COLLAB_ID, DECLINE))
    assert body(response) == {"method": "answerCallbackQuery", "callback_query_id": "cbq-synthetic-1",
                              "text": consent.DECLINED_TEXT}
    assert "/start" in consent.DECLINED_TEXT and len(consent.DECLINED_TEXT) <= 200
    assert bot.idempotency.keys == {"tg:update:68"}
    assert bot.users.consent_calls == [] and bot.uow.calls == []
    assert "consent_version" not in bot.users.records[COLLAB_ID]


def test_version_bump_forces_reacceptance(bot):
    unconsent(bot, OWNER_ID, version="2026-01-01.1")  # accepted an older text
    assert_prompt(body(bot.post(message(69, OWNER_ID, "/salud"))), OWNER_ID)
    stale = bot.post(callback(70, OWNER_ID, "consent:accept:2026-01-01.1"))  # old button
    assert body(stale)["text"] == "Acción no válida."
    assert bot.users.consent_calls == []
    assert body(bot.post(callback(71, OWNER_ID, ACCEPT)))["text"] == consent.ACCEPTED_TEXT
    assert bot.users.records[OWNER_ID]["consent_version"] == CONSENT_VERSION
    assert body(bot.post(message(72, OWNER_ID, "/salud")))["text"].startswith("Recibido: /salud")


@pytest.mark.parametrize("data", [
    "consent:accept:9999-12-31.9", "consent:accept", "consent:", "consent:accept:" + CONSENT_VERSION + "x",
    "consent:revoke:" + CONSENT_VERSION, "consent:accept:" + "9" * 60, "consent:accept:<b>",
])
@pytest.mark.parametrize("consented", [False, True])
def test_forged_or_oversized_consent_callback_rejected(bot, data, consented):
    if not consented:
        unconsent(bot, COLLAB_ID)
    response = bot.post(callback(73, COLLAB_ID, data))
    assert body(response) == {"method": "answerCallbackQuery", "callback_query_id": "cbq-synthetic-1",
                              "text": "Acción no válida."}
    assert bot.users.consent_calls == [] and bot.uow.calls == []  # never forwarded as a callback command
    assert bot.idempotency.keys == {"tg:update:73"}
    assert ("consent_version" in bot.users.records[COLLAB_ID]) is consented


def test_unconsented_user_other_callback_gets_prompt_and_is_not_stored(bot):
    unconsent(bot, OWNER_ID)
    response = bot.post(callback(74, OWNER_ID, APPROVE))
    assert_prompt(body(response), OWNER_ID)
    assert bot.uow.calls == [] and bot.idempotency.keys == {"tg:update:74"}


def test_stranger_cannot_accept_consent(bot):
    response = bot.post(callback(75, STRANGER_ID, ACCEPT))
    assert body(response)["text"] == "Este bot es de uso privado."
    assert bot.users.consent_calls == [] and STRANGER_ID not in bot.users.records


def test_double_tap_accept_has_single_effect(bot):
    unconsent(bot, COLLAB_ID)
    first = bot.post(callback(76, COLLAB_ID, ACCEPT, query_id="q1"))
    again = bot.post(callback(76, COLLAB_ID, ACCEPT, query_id="q1"))  # Telegram redelivery
    second = bot.post(callback(77, COLLAB_ID, ACCEPT, query_id="q2"))  # second tap, new update_id
    assert body(first)["text"] == consent.ACCEPTED_TEXT and body(again) is None
    assert body(second) == {"method": "answerCallbackQuery", "callback_query_id": "q2", "text": consent.ALREADY_TEXT}
    assert len(bot.users.consent_log) == 1 and len(bot.users.consent_calls) == 1


def test_concurrent_double_tap_lost_race_has_single_effect(bot):
    unconsent(bot, COLLAB_ID)
    snapshot = dict(bot.users.records[COLLAB_ID])
    bot.users.get = lambda user_id: dict(snapshot)  # both taps read the record before either commit
    bot.post(callback(78, COLLAB_ID, ACCEPT, query_id="q1"))
    second = bot.post(callback(79, COLLAB_ID, ACCEPT, query_id="q2"))
    assert body(second)["text"] == consent.ALREADY_TEXT
    assert len(bot.users.consent_calls) == 2 and len(bot.users.consent_log) == 1


def test_consent_text_fits_is_html_safe_and_covers_required_points():
    longest = consent.prompt(1, "Recibido: /oportunidades. Te aviso cuando esté listo.")["text"]
    assert len(longest) <= 4096
    bare = CONSENT_TEXT.replace("<b>", "").replace("</b>", "")
    assert "<" not in bare and ">" not in bare
    assert re.fullmatch(r"(?:[^&]|&(?:amp|lt|gt|quot|#x27);)*", bare, re.DOTALL)
    for needle in (CONSENT_VERSION, "habilites", "descartan en memoria", "AWS", "fuera de Venezuela",
                   "no oficial", "bloquear", "apruebe", "/mis_datos", "/borrar", "/stop", "desvincula"):
        assert needle in CONSENT_TEXT, needle
    for data in (ACCEPT, DECLINE):
        assert len(data.encode()) <= 64 and CALLBACK_DATA.fullmatch(data)
    with pytest.raises(ValueError):
        consent.prompt(1, "x" * 4096)
