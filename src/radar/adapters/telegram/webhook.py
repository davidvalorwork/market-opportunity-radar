"""Telegram webhook receiver: authenticate, dedupe, authorize, persist, acknowledge.

``Webhook.handle_update(headers, body)`` returns a Lambda Function URL response
dict. Order (docs/research/agent-b/telegram-lambda-best-practices.md §2,
architecture-final-review.md §4):

  a. constant-time secret check            -> 401, no side effects, empty body
  b. body size limit                       -> 413
  c. safe JSON parse, update_id >= 0       -> 400
  d. dedupe by update_id                   -> 200, no new effect
  e. authorize numeric user_id (owner/collaborator); /start <code> invites.
     Unknown user + bare /start -> request_contact keyboard (only the dedupe
     key is stored).
  e1. contact enrollment (private chat ``message.contact``): only the sender's
     OWN contact (``contact.user_id == from.id``) counts; the number is
     normalized to E.164 and looked up in ``phone_allowlist``. Match ->
     ``users.enroll`` (persist before the answer; failure -> 500, no answer),
     then the consent prompt. Forwarded card, no match or already enrolled ->
     neutral/friendly reply and only the dedupe key. The phone number is never
     stored, logged or returned: only user_id and role reach ``users.enroll``.
  e2. consent gate (consent.py): until the user (owner included) accepts the
     CURRENT consent version only /start, /mis_datos, /borrar, /stop and the
     consent buttons work; anything else gets the consent prompt and is NOT
     stored (only the dedupe key). Accept persists through
     ``users.accept_consent`` under the same rules as commands (persist before
     the answer; failure -> 500, no answer); Decline stores only the dedupe key.
  f. map to telegram.command.v1, validate, persist receipt + command + outbox in
     ONE unit-of-work call, and only then return 200 with the acknowledgement as
     a Bot API method in the body. Persistence failure -> 500 (Telegram retries);
     a command that was not stored is never acknowledged.

Injected dependencies (src/radar/ports/ has no user-directory, invite or
idempotency port yet; agent A owns it). Each maps to a future port:

- ``secret`` (str): webhook secret_token, loaded by the entrypoint factory from
  SSM (config/secret port).
- ``unit_of_work.commit(receipt, command, outbox) -> bool``: receipt/command/
  outbox unit of work. ONE transaction that conditionally creates every key in
  ``receipt["idempotency_keys"]`` (so ``idempotency.seen`` becomes true) and
  stores the command and the outbox envelope. Returns False, writing nothing,
  if any key already exists; raises on any other failure.
- ``idempotency.seen(key) -> bool`` / ``idempotency.remember(key)``: idempotency
  store (DynamoDB conditional put + TTL). ``remember`` is used for updates that
  produce no command (unauthorized users, rejected commands): only the dedupe
  key is persisted, nothing else.
- ``users.get(telegram_user_id) -> {"role", "user_ref", "owner_ref",
  "consent_version"?, "consent_accepted_at"?} | None``: user directory /
  allowlist. ``user_ref`` and ``owner_ref`` are opaque refs (common.v1
  opaque_ref); the raw numeric ID never enters a payload. No
  ``consent_version`` = never accepted.
- ``users.accept_consent(user_ref, version, accepted_at, idempotency_keys) -> bool``:
  user directory port, consent record. ONE transaction (e.g. DynamoDB
  TransactWriteItems) that conditionally creates every key in
  ``idempotency_keys`` and sets ``consent_version``/``consent_accepted_at`` on
  the user, plus an append-only consent history item kept as proof. Returns
  False, writing nothing, if any key already exists; raises on any other failure.
- ``users.enroll(telegram_user_id, role, enrolled_at, idempotency_keys) -> bool``:
  user directory port, conditional create. ONE transaction that creates the
  user record (role, fresh opaque ``user_ref``, ``owner_ref``, no consent) only
  if the user does not exist yet, plus every key in ``idempotency_keys``.
  Returns False, writing nothing, if the user is already enrolled or any key
  exists; raises on any other failure. Never receives the phone number.
- ``phone_allowlist.lookup(e164) -> "owner" | "collaborator" | None``: allowlist
  config (allowlist.PhoneAllowlist). Local wiring: ``PhoneAllowlist.from_file``
  on ``.local/allowlist.json``; production: ``PhoneAllowlist.from_json`` on the
  SSM SecureString ``/market-radar/allowlist_phones`` (config/secret port).
- ``invites.redeem(code_sha256, telegram_user_id, now) -> bool``: invite store.
  Atomically consumes an unused, unexpired (now < expires_at) invite stored by
  hash and enrolls the user as collaborator; False otherwise.
- ``clock() -> aware UTC datetime``: clock port.
"""

import hashlib
import hmac
import json
import logging
import re
import uuid
from datetime import timedelta

from jsonschema import ValidationError

from radar.adapters.telegram import consent
from radar.adapters.telegram.allowlist import normalize_phone
from radar.contracts import validate

MAX_BODY = 64 * 1024
DEADLINE = timedelta(minutes=5)
COMMANDS = {"start", "vincular", "buscar", "busquedas", "oportunidades", "pendientes",
            "resumen", "salud", "pedir", "mis_datos", "borrar", "stop"}
OWNER_ONLY = {"buscar", "busquedas", "pendientes", "resumen", "salud"}
TEXT_ARG = {"buscar": "query", "pedir": "text"}  # other commands ignore trailing text
MAX_ARG = 256
CALLBACK_DATA = re.compile(r"[A-Za-z0-9_:.-]{1,64}")
INVITE_CODE = re.compile(r"[A-Za-z0-9_-]{1,64}")
COMMAND_TEXT = re.compile(r"/([A-Za-z0-9_]{1,32})(?:@[A-Za-z0-9_]{1,64})?(?:\s+(.*))?", re.DOTALL)

NEUTRAL = "Este bot es de uso privado."
OWNER_ONLY_TEXT = "Ese comando es solo para el propietario."
UNKNOWN_TEXT = "Comando no reconocido."
TOO_LONG_TEXT = f"Texto demasiado largo (máximo {MAX_ARG} caracteres)."
BAD_CALLBACK_TEXT = "Acción no válida."
DONE_CALLBACK_TEXT = "Esta acción ya se procesó."
SHARE_CONTACT_TEXT = ("Para usar este bot, comparte tu contacto con el botón de abajo. "
                      "El radar solo comprueba si tu número está autorizado y no lo guarda.")
OWN_CONTACT_TEXT = "Solo se acepta tu propio contacto, enviado con el botón."
NOT_ALLOWED_TEXT = "No autorizado. Este bot es de uso privado."
ENROLLED_TEXT = "Número verificado."
ALREADY_ENROLLED_TEXT = "Ya estás autorizado."
CONTACT_KEYBOARD = {"keyboard": [[{"text": "Compartir mi contacto", "request_contact": True}]],
                    "one_time_keyboard": True, "resize_keyboard": True}
REMOVE_KEYBOARD = {"remove_keyboard": True}

log = logging.getLogger(__name__)


def hash_invite_code(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _response(status, method=None):
    if method is None:
        return {"statusCode": status, "headers": {"content-type": "text/plain"}, "body": ""}
    return {"statusCode": status, "headers": {"content-type": "application/json"},
            "body": json.dumps(method, ensure_ascii=False)}


def _reply(chat_id, text, reply_markup=None):
    method = {"method": "sendMessage", "chat_id": chat_id, "text": text}
    if reply_markup is not None:
        method["reply_markup"] = reply_markup
    return method


def _answer(callback_query_id, text):
    return {"method": "answerCallbackQuery", "callback_query_id": callback_query_id, "text": text}


def _int(value):
    return value if type(value) is int else None


class Webhook:
    def __init__(self, *, secret, unit_of_work, idempotency, users, invites, phone_allowlist, clock,
                 max_body=MAX_BODY):
        if not secret:
            raise ValueError("webhook secret is required")
        self._secret = secret.encode("utf-8")
        self._uow, self._seen, self._users, self._invites, self._clock = unit_of_work, idempotency, users, invites, clock
        self._phones = phone_allowlist
        self.max_body = max_body

    def handle_update(self, headers, body):
        headers = {str(key).lower(): value for key, value in (headers or {}).items()}
        given = str(headers.get("x-telegram-bot-api-secret-token", "")).encode("utf-8")
        if not hmac.compare_digest(given, self._secret):
            return _response(401)
        if len(body) > self.max_body:
            return _response(413)
        try:
            update = json.loads(body)
        except (ValueError, RecursionError):
            return _response(400)
        update_id = _int(update.get("update_id")) if isinstance(update, dict) else None
        if update_id is None or update_id < 0:
            return _response(400)
        try:
            return self._dispatch(update_id, update)
        except Exception as exc:  # fail closed: Telegram retries, nothing acknowledged
            log.error("telegram update %s failed: %s", update_id, type(exc).__name__)
            return _response(500)

    def _dispatch(self, update_id, update):
        key = f"tg:update:{update_id}"
        if self._seen.seen(key):
            log.info("telegram update %s duplicate", update_id)
            return _response(200)
        if isinstance(update.get("message"), dict):
            return self._message(update_id, key, update["message"])
        if isinstance(update.get("callback_query"), dict):
            return self._callback(update_id, key, update["callback_query"])
        return _response(200)  # update type outside allowed_updates: ignore

    def _reject(self, key, method):
        self._seen.remember(key)
        return _response(200, method)

    def _message(self, update_id, key, message):
        chat, sender = message.get("chat"), message.get("from")
        if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(sender, dict):
            return _response(200)  # private chats only; never answer into groups
        chat_id, user_id = _int(chat.get("id")), _int(sender.get("id"))
        if chat_id is None or user_id is None:
            return _response(200)
        if isinstance(message.get("contact"), dict):
            return self._contact(update_id, key, chat_id, user_id, message["contact"])
        text = message.get("text") if isinstance(message.get("text"), str) else ""
        match = COMMAND_TEXT.fullmatch(text.strip())
        name, rest = (match.group(1).lower(), (match.group(2) or "").strip()) if match else (None, "")
        user = self._users.get(user_id)
        if user is None and name == "start" and INVITE_CODE.fullmatch(rest):
            if self._invites.redeem(hash_invite_code(rest), user_id, self._clock()):
                log.info("telegram update %s invite redeemed", update_id)
                user = self._users.get(user_id)
        if user is None and name == "start" and not rest:
            return self._reject(key, _reply(chat_id, SHARE_CONTACT_TEXT, CONTACT_KEYBOARD))
        role = user.get("role") if isinstance(user, dict) else None
        if role not in ("owner", "collaborator"):
            return self._reject(key, _reply(chat_id, NEUTRAL))
        consented = consent.has_consent(user)
        if not consented and name not in consent.UNGATED:
            return self._reject(key, consent.prompt(chat_id))
        if name not in COMMANDS:
            return self._reject(key, _reply(chat_id, UNKNOWN_TEXT))
        if name in OWNER_ONLY and role != "owner":
            return self._reject(key, _reply(chat_id, OWNER_ONLY_TEXT))
        args = {}
        if name in TEXT_ARG and rest:
            if len(rest) > MAX_ARG:
                return self._reject(key, _reply(chat_id, TOO_LONG_TEXT))
            args[TEXT_ARG[name]] = rest
        command = {"schema_version": 1, "update_id": update_id, "telegram_user_ref": user["user_ref"],
                   "command": name, "args": args}
        if not self._persist(update_id, [key], user, command):
            return _response(200)
        ack = f"Recibido: /{name}. Te aviso cuando esté listo."
        return _response(200, consent.prompt(chat_id, ack) if name == "start" and not consented else _reply(chat_id, ack))

    def _contact(self, update_id, key, chat_id, user_id, contact):
        """Enroll by allowlisted phone. The number lives only in this frame: never stored, logged or returned."""
        if _int(contact.get("user_id")) != user_id:  # someone else's card (forwarded or attached)
            return self._reject(key, _reply(chat_id, OWN_CONTACT_TEXT))
        if self._users.get(user_id) is not None:
            return self._reject(key, _reply(chat_id, ALREADY_ENROLLED_TEXT, REMOVE_KEYBOARD))
        role = self._phones.lookup(normalize_phone(contact.get("phone_number")))
        if role not in ("owner", "collaborator"):
            log.info("telegram update %s contact not allowlisted", update_id)
            return self._reject(key, _reply(chat_id, NOT_ALLOWED_TEXT, REMOVE_KEYBOARD))
        enrolled = self._users.enroll(user_id, role, self._clock(), [key]) is True
        log.info("telegram update %s contact enrolled=%s role=%s", update_id, enrolled, role)
        if not enrolled:  # lost a race with a concurrent enrollment or redelivery
            return _response(200, _reply(chat_id, ALREADY_ENROLLED_TEXT, REMOVE_KEYBOARD))
        # ponytail: one webhook reply = one message = one reply_markup; the inline consent buttons win and the
        # one_time contact keyboard is already hidden by the client. Remove it via BotApi if it ever matters.
        return _response(200, consent.prompt(chat_id, ENROLLED_TEXT))

    def _callback(self, update_id, key, query):
        query_id, sender = query.get("id"), query.get("from")
        if not isinstance(query_id, str) or not query_id or not isinstance(sender, dict) or _int(sender.get("id")) is None:
            return _response(200)
        user = self._users.get(sender["id"])
        if not isinstance(user, dict) or user.get("role") not in ("owner", "collaborator"):
            return self._reject(key, _answer(query_id, NEUTRAL))
        data = query.get("data")
        if not isinstance(data, str) or not CALLBACK_DATA.fullmatch(data):
            return self._reject(key, _answer(query_id, BAD_CALLBACK_TEXT))
        if data.startswith(consent.CALLBACK_PREFIX):
            return self._consent(update_id, key, query_id, user, data)
        if not consent.has_consent(user):  # private chat id == user id
            return self._reject(key, consent.prompt(sender["id"]))
        command = {"schema_version": 1, "update_id": update_id, "telegram_user_ref": user["user_ref"],
                   "command": "callback", "args": {}, "callback_ref": data}
        # ponytail: each callback_ref acts once per user; the app mints a fresh ref for repeatable buttons.
        keys = [key, f"tg:callback:{user['user_ref']}:{data}"]
        stored = self._persist(update_id, keys, user, command)
        return _response(200, _answer(query_id, "Recibido." if stored else DONE_CALLBACK_TEXT))

    def _consent(self, update_id, key, query_id, user, data):
        if data == consent.DECLINE:
            return self._reject(key, _answer(query_id, consent.DECLINED_TEXT))
        if data != consent.ACCEPT:  # stale version or forged: record nothing
            return self._reject(key, _answer(query_id, BAD_CALLBACK_TEXT))
        if consent.has_consent(user):
            return self._reject(key, _answer(query_id, consent.ALREADY_TEXT))
        keys = [key, f"tg:consent:{user['user_ref']}:{consent.CONSENT_VERSION}"]
        stored = self._users.accept_consent(user["user_ref"], consent.CONSENT_VERSION, self._clock(), keys) is True
        log.info("telegram update %s consent=%s stored=%s", update_id, consent.CONSENT_VERSION, stored)
        return _response(200, _answer(query_id, consent.ACCEPTED_TEXT if stored else consent.ALREADY_TEXT))

    def _persist(self, update_id, keys, user, command):
        """Validate, then commit receipt + command + outbox atomically. False = duplicate."""
        now = self._clock()
        outbox = {
            "schema_version": 1, "message_id": str(uuid.uuid4()), "operation_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()), "owner_ref": user["owner_ref"], "kind": "telegram.command",
            "deadline": (now + DEADLINE).strftime("%Y-%m-%dT%H:%M:%SZ"), "attempt": 1, "payload": command,
        }
        try:
            validate("telegram.command.v1", command)
            validate("envelope.v1", outbox)
        except ValidationError:
            log.error("telegram update %s produced an invalid command", update_id)
            raise
        # No chat_id/user_id stored: the app reaches the user through user_ref (private chat id == user id).
        receipt = {"update_id": update_id, "idempotency_keys": keys, "received_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
        stored = self._uow.commit(receipt, command, outbox)
        log.info("telegram update %s command=%s stored=%s", update_id, command["command"], stored)
        return stored is True
