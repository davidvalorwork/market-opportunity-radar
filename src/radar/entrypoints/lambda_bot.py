"""Lambda Function URL handler for the ``bot`` function (Telegram webhook).

No AWS SDK here. Configuration comes only from environment variables that hold
NAMES, never secret values:

- ``RADAR_BOT_WIRING``: ``"package.module:function"``. The function receives
  ``os.environ`` and returns a wired ``radar.adapters.telegram.webhook.Webhook``.
  This is the factory hook agent A fills with real adapters (DynamoDB unit of
  work / idempotency / users / invites, clock) and the secret it loads from SSM.
- ``RADAR_TELEGRAM_SECRET_PARAM``: SSM parameter NAME of the webhook secret_token
  (read by the factory, not here).
- ``RADAR_TELEGRAM_TOKEN_PARAM``: SSM parameter NAME of the bot token (used by
  outbound senders; the webhook itself never needs the token).

Fail closed: no wiring -> 503, wiring error -> 500. Updates are never accepted
without a configured webhook.
"""

import base64
import binascii
import importlib
import logging
import os

WIRING_ENV = "RADAR_BOT_WIRING"
SECRET_PARAM_ENV = "RADAR_TELEGRAM_SECRET_PARAM"
TOKEN_PARAM_ENV = "RADAR_TELEGRAM_TOKEN_PARAM"

log = logging.getLogger(__name__)
_webhook = None  # wired clients are reused across warm invocations; no request state lives here


def build_webhook(environ):
    """Factory hook: import and call the function named by RADAR_BOT_WIRING; None if unset."""
    spec = environ.get(WIRING_ENV, "")
    if not spec:
        return None
    module, _, function = spec.partition(":")
    return getattr(importlib.import_module(module), function)(environ)


def handler(event, context):
    global _webhook
    if _webhook is None:
        try:
            _webhook = build_webhook(os.environ)
        except Exception as exc:
            log.error("bot wiring failed: %s", type(exc).__name__)
            return {"statusCode": 500, "body": ""}
        if _webhook is None:
            log.error("bot not wired: %s unset", WIRING_ENV)
            return {"statusCode": 503, "body": ""}
    body = event.get("body") or ""
    try:
        raw = base64.b64decode(body, validate=True) if event.get("isBase64Encoded") else body.encode("utf-8")
    except (binascii.Error, ValueError):
        raw = b""  # still goes through the secret check first; then 400 as invalid JSON
    return _webhook.handle_update(event.get("headers") or {}, raw)
