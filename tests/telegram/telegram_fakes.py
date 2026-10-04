"""In-memory fakes for the webhook's injected dependencies and synthetic updates.

All IDs, refs and secrets are synthetic. The fakes implement the contracts
documented in radar.adapters.telegram.webhook so future adapters can be held to
the same behaviour.
"""

from datetime import datetime, timezone
import json

from radar.adapters.telegram.webhook import Webhook, hash_invite_code

SECRET = "synthetic-webhook-secret_0123456789"
OWNER_ID, COLLAB_ID, STRANGER_ID = 900000001, 900000002, 900000003
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


class Idempotency:
    def __init__(self):
        self.keys = set()

    def seen(self, key):
        return key in self.keys

    def remember(self, key):
        self.keys.add(key)


class UnitOfWork:
    """One atomic commit: all idempotency keys + command + outbox, or nothing."""

    def __init__(self, idempotency, fail=False):
        self.idempotency, self.fail = idempotency, fail
        self.calls, self.commands, self.outbox = [], [], []

    def commit(self, receipt, command, outbox):
        self.calls.append((receipt, command, outbox))
        if self.fail:
            raise RuntimeError("synthetic persistence failure")
        if any(key in self.idempotency.keys for key in receipt["idempotency_keys"]):
            return False
        self.idempotency.keys.update(receipt["idempotency_keys"])
        self.commands.append(command)
        self.outbox.append(outbox)
        return True


class Users:
    def __init__(self):
        self.records = {
            OWNER_ID: {"role": "owner", "user_ref": "tguser:owner-a", "owner_ref": "owner:radar-pilot"},
            COLLAB_ID: {"role": "collaborator", "user_ref": "tguser:collab-b", "owner_ref": "owner:radar-pilot"},
        }

    def get(self, user_id):
        return self.records.get(user_id)


class Invites:
    """Reference semantics: stored by hash, one use, expires_at exclusive."""

    def __init__(self, users):
        self.users, self.invites, self.calls = users, {}, []

    def add(self, code, expires_at):
        self.invites[hash_invite_code(code)] = {"expires_at": expires_at, "used_by": None}

    def redeem(self, code_sha256, user_id, now):
        self.calls.append(code_sha256)
        invite = self.invites.get(code_sha256)
        if invite is None or invite["used_by"] is not None or now >= invite["expires_at"]:
            return False
        invite["used_by"] = user_id
        self.users.records[user_id] = {"role": "collaborator", "user_ref": f"tguser:invited-{len(self.users.records)}",
                                       "owner_ref": "owner:radar-pilot"}
        return True


class Bot:
    """A webhook plus handles on every fake, for assertions."""

    def __init__(self, fail=False):
        self.idempotency = Idempotency()
        self.uow = UnitOfWork(self.idempotency, fail=fail)
        self.users = Users()
        self.invites = Invites(self.users)
        self.now = NOW
        self.webhook = Webhook(secret=SECRET, unit_of_work=self.uow, idempotency=self.idempotency,
                               users=self.users, invites=self.invites, clock=lambda: self.now)

    def post(self, update, secret=SECRET):
        body = update if isinstance(update, bytes) else json.dumps(update).encode()
        headers = {} if secret is None else {"X-Telegram-Bot-Api-Secret-Token": secret}
        return self.webhook.handle_update(headers, body)

    def effects(self):
        return {"keys": set(self.idempotency.keys), "uow_calls": len(self.uow.calls),
                "users": set(self.users.records), "invite_calls": len(self.invites.calls)}


def message(update_id, user_id, text, chat_type="private"):
    return {"update_id": update_id, "message": {
        "message_id": 1, "date": 1790000000, "text": text,
        "from": {"id": user_id, "is_bot": False, "first_name": "Synthetic"},
        "chat": {"id": user_id, "type": chat_type}}}


def callback(update_id, user_id, data, query_id="cbq-synthetic-1"):
    return {"update_id": update_id, "callback_query": {
        "id": query_id, "data": data, "chat_instance": "synthetic",
        "from": {"id": user_id, "is_bot": False, "first_name": "Synthetic"},
        "message": {"message_id": 7, "chat": {"id": user_id, "type": "private"}}}}


def build(environ):
    """Factory for RADAR_BOT_WIRING tests: 'telegram_fakes:build'."""
    return Bot().webhook
