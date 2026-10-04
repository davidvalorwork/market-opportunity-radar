"""Versioned consent text and the Accept/Decline prompt the webhook gates on.

Changing anything in the text requires a new ``CONSENT_VERSION``: every user
(owner included) must then accept again before using gated commands. The text
describes the intended behaviour of /mis_datos, /borrar and /stop; the app side
of those commands is not implemented yet and a Venezuelan lawyer must review the
text before any real collaborator sees it (docs/research/agent-b-sessions.md
B-F026, docs/research/agent-b/threat-model.md).

Callback data are fixed ASCII strings (<= 64 bytes) carrying only the version,
never user data. Anything else under ``consent:`` is rejected by the webhook.
"""

from radar.adapters.telegram.bot_api import MAX_TEXT, escape

CONSENT_VERSION = "2026-10-03.1"
CALLBACK_PREFIX = "consent:"
ACCEPT = f"{CALLBACK_PREFIX}accept:{CONSENT_VERSION}"
DECLINE = f"{CALLBACK_PREFIX}decline:{CONSENT_VERSION}"
UNGATED = {"start", "mis_datos", "borrar", "stop"}  # habeas data must work without consent

_PARAGRAPHS = (
    "Antes de usar el radar, lee esto. Solo seguimos si aceptas.",
    "1. Chats: solo se procesan los chats de WhatsApp que tú habilites. Los mensajes "
    "de otras personas en chats no habilitados se descartan en memoria y no se guardan.",
    "2. Dónde: tus datos y la sesión de tu WhatsApp vinculado se guardan cifrados en "
    "servidores de AWS fuera de Venezuela.",
    "3. Riesgo: el radar usa un cliente de WhatsApp no oficial. WhatsApp puede "
    "bloquear el número que vincules. Si no aceptas ese riesgo, no vincules tu número.",
    "4. Envíos: ningún mensaje sale desde tu WhatsApp sin que el propietario del radar "
    "apruebe antes ese mensaje exacto.",
    "5. Tus datos: /mis_datos te muestra qué guardamos de ti. /borrar borra tus datos. "
    "/stop desvincula tu dispositivo de WhatsApp y borra tus datos. Estos tres "
    "comandos funcionan aunque no aceptes.",
    "6. Puedes retirar tu aceptación cuando quieras con /stop. Si estas condiciones "
    "cambian, te pediremos aceptarlas de nuevo.",
)
CONSENT_TEXT = (f"<b>Condiciones del radar (versión {escape(CONSENT_VERSION)})</b>\n\n"
                + "\n\n".join(escape(p) for p in _PARAGRAPHS))

ACCEPTED_TEXT = "Condiciones aceptadas. Ya puedes usar el bot."
ALREADY_TEXT = "Ya aceptaste estas condiciones."
DECLINED_TEXT = "No aceptaste: no guardamos nada. Si cambias de opinión, envía /start."


def has_consent(user):
    """True only if the user directory record holds the CURRENT version."""
    return user.get("consent_version") == CONSENT_VERSION


def prompt(chat_id, intro=None):
    """sendMessage (as webhook reply) with the consent text and Accept/Decline buttons."""
    text = CONSENT_TEXT if intro is None else f"{escape(intro)}\n\n{CONSENT_TEXT}"
    if len(text) > MAX_TEXT:
        raise ValueError("consent prompt exceeds Telegram's 4096 characters")
    return {"method": "sendMessage", "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": [[{"text": "Acepto", "callback_data": ACCEPT},
                                                  {"text": "No acepto", "callback_data": DECLINE}]]}}
