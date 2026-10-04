"""Contact enrollment: /start asks for the user's own contact, allowlisted phones enroll, the number is never kept.

Synthetic numbers only (telegram_fakes). The phone must not reach any persisted object, response body or log.
"""

from datetime import timedelta
import json
import logging

import pytest

from radar.adapters.telegram import webhook
from radar.adapters.telegram.consent import ACCEPT, CONSENT_TEXT, DECLINE
from telegram_fakes import (COLLAB_PHONE, NOW, OWNER_ID, OWNER_PHONE, STRANGER_ID, UNLISTED_PHONE, Bot, callback,
                            contact, message)

OTHER_ID = 900000004
REMOVE = {"remove_keyboard": True}


def body(response):
    return json.loads(response["body"]) if response["body"] else None


def stored(bot):
    """Everything the webhook made the fakes persist, serialized."""
    return json.dumps([sorted(bot.idempotency.keys), bot.uow.calls, bot.users.records, bot.users.enroll_calls,
                       bot.users.consent_calls, bot.users.consent_log, bot.invites.invites], default=str)


def digits(phone):
    return phone.lstrip("+")


@pytest.fixture
def bot():
    return Bot()


def test_unknown_user_start_gets_request_contact_keyboard(bot):
    response = bot.post(message(200, STRANGER_ID, "/start"))
    assert response["statusCode"] == 200
    assert body(response) == {"method": "sendMessage", "chat_id": STRANGER_ID, "text": webhook.SHARE_CONTACT_TEXT,
                              "reply_markup": {"keyboard": [[{"text": "Compartir mi contacto",
                                                              "request_contact": True}]],
                                               "one_time_keyboard": True, "resize_keyboard": True}}
    assert bot.idempotency.keys == {"tg:update:200"}
    assert bot.uow.calls == [] and bot.users.enroll_calls == [] and STRANGER_ID not in bot.users.records


def test_invite_flow_still_wins_over_contact_request(bot):
    bot.invites.add("synthetic-invite_code-7", NOW + timedelta(hours=1))
    response = bot.post(message(201, STRANGER_ID, "/start synthetic-invite_code-7"))
    assert body(response)["text"].startswith("Recibido: /start")
    assert bot.users.records[STRANGER_ID]["role"] == "collaborator"


@pytest.mark.parametrize("phone, role", [(OWNER_PHONE, "owner"), (COLLAB_PHONE, "collaborator")])
def test_own_allowlisted_contact_enrolls_with_role_then_consent_prompt(bot, phone, role):
    response = bot.post(contact(202, STRANGER_ID, phone))
    assert response["statusCode"] == 200
    method = body(response)
    assert method["method"] == "sendMessage" and method["chat_id"] == STRANGER_ID
    assert method["text"] == f"{webhook.ENROLLED_TEXT}\n\n{CONSENT_TEXT}"
    assert [b["callback_data"] for row in method["reply_markup"]["inline_keyboard"] for b in row] == [ACCEPT, DECLINE]
    assert bot.users.enroll_calls == [(STRANGER_ID, role, NOW, ["tg:update:202"])]
    record = bot.users.records[STRANGER_ID]
    assert record["role"] == role and "consent_version" not in record
    assert bot.idempotency.keys == {"tg:update:202"} and bot.uow.calls == []
    # the consent gate applies as usual: gated command -> prompt, Accept -> commands work
    gated = body(bot.post(message(203, STRANGER_ID, "/vincular")))
    assert gated["text"] == CONSENT_TEXT and bot.uow.calls == []
    bot.post(callback(204, STRANGER_ID, ACCEPT))
    assert body(bot.post(message(205, STRANGER_ID, "/vincular")))["text"].startswith("Recibido: /vincular")
    assert bot.uow.commands[-1]["telegram_user_ref"] == record["user_ref"]


def test_own_contact_not_on_allowlist_rejected_and_nothing_enrolled(bot):
    response = bot.post(contact(206, STRANGER_ID, UNLISTED_PHONE))
    assert body(response) == {"method": "sendMessage", "chat_id": STRANGER_ID, "text": webhook.NOT_ALLOWED_TEXT,
                              "reply_markup": REMOVE}
    assert bot.users.enroll_calls == [] and STRANGER_ID not in bot.users.records
    assert bot.idempotency.keys == {"tg:update:206"} and bot.uow.calls == []
    assert body(bot.post(message(207, STRANGER_ID, "/vincular")))["text"] == webhook.NEUTRAL


@pytest.mark.parametrize("contact_user_id", [OTHER_ID, None, "900000003", OWNER_ID])
def test_forwarded_or_foreign_contact_rejected(bot, contact_user_id):
    # someone else's allowlisted card, a card without user_id, a string id, the owner's card sent by a stranger
    response = bot.post(contact(208, STRANGER_ID, OWNER_PHONE, contact_user_id=contact_user_id))
    assert body(response) == {"method": "sendMessage", "chat_id": STRANGER_ID, "text": webhook.OWN_CONTACT_TEXT}
    assert bot.users.enroll_calls == [] and STRANGER_ID not in bot.users.records
    assert bot.idempotency.keys == {"tg:update:208"}


@pytest.mark.parametrize("raw", ["10000000001", "1 000 000 0001", " +1 000 0000001 "])
def test_phone_without_plus_and_with_spaces_is_normalized(bot, raw):
    bot.post(contact(209, STRANGER_ID, raw))
    assert bot.users.records[STRANGER_ID]["role"] == "collaborator"


@pytest.mark.parametrize("raw", ["", "abc", "+1-000-000-0001", None, 10000000001, {"x": 1}])
def test_malformed_phone_rejected(bot, raw):
    assert body(bot.post(contact(210, STRANGER_ID, raw)))["text"] == webhook.NOT_ALLOWED_TEXT
    assert bot.users.enroll_calls == []


def test_already_enrolled_user_sharing_again_has_no_second_effect(bot):
    bot.post(contact(211, STRANGER_ID, COLLAB_PHONE))
    record = dict(bot.users.records[STRANGER_ID])
    again = bot.post(contact(211, STRANGER_ID, COLLAB_PHONE))  # Telegram redelivery
    second = bot.post(contact(212, STRANGER_ID, COLLAB_PHONE))  # shares again
    owner = bot.post(contact(213, OWNER_ID, OWNER_PHONE))       # pre-existing user
    assert body(again) is None
    for response, chat_id in ((second, STRANGER_ID), (owner, OWNER_ID)):
        assert body(response) == {"method": "sendMessage", "chat_id": chat_id, "text": webhook.ALREADY_ENROLLED_TEXT,
                                  "reply_markup": REMOVE}
    assert len(bot.users.enroll_calls) == 1 and bot.users.records[STRANGER_ID] == record
    assert bot.users.records[OWNER_ID]["role"] == "owner"


def test_concurrent_enrollment_lost_race_is_friendly_no_op(bot):
    bot.users.get = lambda user_id: None  # both deliveries read "unknown" before either commit
    bot.post(contact(214, STRANGER_ID, COLLAB_PHONE))
    second = bot.post(contact(215, STRANGER_ID, COLLAB_PHONE))
    assert body(second)["text"] == webhook.ALREADY_ENROLLED_TEXT
    assert len(bot.users.enroll_calls) == 2 and len(bot.users.records) == 3


def test_enrollment_failure_is_500_without_ack_and_retry_succeeds():
    bot = Bot(fail_enroll=True)
    response = bot.post(contact(216, STRANGER_ID, COLLAB_PHONE))
    assert response["statusCode"] == 500 and response["body"] == ""
    assert bot.idempotency.keys == set() and STRANGER_ID not in bot.users.records
    bot.users.fail_enroll = False  # Telegram retries the same update: processed, not dropped as duplicate
    assert body(bot.post(contact(216, STRANGER_ID, COLLAB_PHONE)))["text"].startswith(webhook.ENROLLED_TEXT)


def test_contact_in_group_chat_is_ignored(bot):
    response = bot.post(contact(217, STRANGER_ID, COLLAB_PHONE, chat_type="group"))
    assert response["statusCode"] == 200 and body(response) is None
    assert bot.users.enroll_calls == [] and bot.idempotency.keys == set()


def test_phone_never_stored_returned_or_logged(caplog):
    caplog.set_level(logging.DEBUG)
    bot, bodies = Bot(fail_enroll=True), []
    updates = [contact(300, STRANGER_ID, "1 000 000 0001"),               # enrollment fails -> 500
               contact(301, OTHER_ID, OWNER_PHONE, contact_user_id=STRANGER_ID),  # forwarded
               contact(302, OTHER_ID, UNLISTED_PHONE)]                     # not allowlisted
    bodies += [bot.post(update)["body"] for update in updates]
    bot.users.fail_enroll = False
    updates = [contact(300, STRANGER_ID, "1 000 000 0001"),               # retry enrolls
               contact(303, STRANGER_ID, COLLAB_PHONE),                    # already enrolled
               callback(304, STRANGER_ID, ACCEPT), message(305, STRANGER_ID, "/start")]
    bodies += [bot.post(update)["body"] for update in updates]
    assert bot.users.records[STRANGER_ID]["role"] == "collaborator" and bot.uow.commands  # flows really ran
    haystack = "\n".join([stored(bot), *bodies, caplog.text, repr(bot.webhook.__dict__)])
    for phone in (OWNER_PHONE, COLLAB_PHONE, UNLISTED_PHONE):
        assert digits(phone) not in haystack
    assert "000 000" not in haystack
