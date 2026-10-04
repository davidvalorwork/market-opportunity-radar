"""Telegram webhook receiver: authenticate, dedupe, authorize, persist, acknowledge.

``Webhook.handle_update(headers, body)`` returns a Lambda Function URL response
dict. Order (docs/research/agent-b/telegram-lambda-best-practices.md §2,
architecture-final-review.md §4):

  a. constant-time secret check            -> 401, no side effects, empty body
  b. body size limit                       -> 413
  c. safe JSON parse, update_id >= 0       -> 400
  d. dedupe by update_id                   -> 200, no new effect
  e. authorize numeric user_id (owner/collaborator); /start <code> invites
  f. map to telegram.command.v1, validate, persist receipt + command + outbox in
     ONE unit-of-work call, and only then return 200 with the acknowledgement as
     a Bot API method in the body. Persistence failure -> 500 (Telegram retries);
     a command that was not stored is never acknowledged.

Injected dependencies (src/radar/ports/ does not exist yet; agent A owns it).
Each maps to a future port:

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
- ``users.get(telegram_user_id) -> {"role", "user_ref", "owner_ref"} | None``:
  user directory / allowlist. ``user_ref`` and ``owner_ref`` are opaque refs
  (common.v1 opaque_ref); the raw numeric ID never enters a payload.
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

log = logging.getLogger(__name__)


def hash_invite_code(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _response(status, method=None):
    if method is None:
        return {"statusCode": status, "headers": {"content-type": "text/plain"}, "body": ""}
    return {"statusCode": status, "headers": {"content-type": "application/json"},
            "body": json.dumps(method, ensure_ascii=False)}


def _reply(chat_id, text):
    return {"method": "sendMessage", "chat_id": chat_id, "text": text}


def _answer(callback_query_id, text):
    return {"method": "answerCallbackQuery", "callback_query_id": callback_query_id, "text": text}


def _int(value):
    return value if type(value) is int else None


class Webhook:
    def __init__(self, *, secret, unit_of_work, idempotency, users, invites, clock, max_body=MAX_BODY):
        if not secret:
            raise ValueError("webhook secret is required")
        self._secret = secret.encode("utf-8")
        self._uow, self._seen, self._users, self._invites, self._clock = unit_of_work, idempotency, users, invites, clock
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
        text = message.get("text") if isinstance(message.get("text"), str) else ""
        match = COMMAND_TEXT.fullmatch(text.strip())
        name, rest = (match.group(1).lower(), (match.group(2) or "").strip()) if match else (None, "")
        user = self._users.get(user_id)
        if user is None and name == "start" and INVITE_CODE.fullmatch(rest):
            if self._invites.redeem(hash_invite_code(rest), user_id, self._clock()):
                log.info("telegram update %s invite redeemed", update_id)
                user = self._users.get(user_id)
        role = user.get("role") if isinstance(user, dict) else None
        if role not in ("owner", "collaborator"):
            return self._reject(key, _reply(chat_id, NEUTRAL))
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
        stored = self._persist(update_id, [key], user, command)
        return _response(200, _reply(chat_id, f"Recibido: /{name}. Te aviso cuando esté listo.") if stored else None)

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
        command = {"schema_version": 1, "update_id": update_id, "telegram_user_ref": user["user_ref"],
                   "command": "callback", "args": {}, "callback_ref": data}
        # ponytail: each callback_ref acts once per user; the app mints a fresh ref for repeatable buttons.
        keys = [key, f"tg:callback:{user['user_ref']}:{data}"]
        stored = self._persist(update_id, keys, user, command)
        return _response(200, _answer(query_id, "Recibido." if stored else DONE_CALLBACK_TEXT))

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
