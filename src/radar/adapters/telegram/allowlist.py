"""Phone-number allowlist for Telegram contact enrollment (stdlib only).

Document (JSON, strict)::

    {"schema_version": 1, "entries": [{"phone_e164": "+10000000000", "role": "owner"}]}

Unknown keys, malformed E.164 numbers, unknown roles and duplicate numbers are
rejected. Validation errors name the entry index, never the number.

Sources: ``from_file`` for local wiring (the real list lives in
``.local/allowlist.json``, outside Git) and ``from_json`` for the SSM SecureString
``/market-radar/allowlist_phones`` that the entrypoint factory reads (agent A).

Phone numbers are personal data: ``repr``/``str`` never reveal entries and this
module never logs. The webhook only uses ``lookup`` and discards the number.
"""

import json
import re
from pathlib import Path

E164 = re.compile(r"\+[1-9][0-9]{6,14}")
ROLES = ("owner", "collaborator")


def normalize_phone(raw):
    """Telegram's ``contact.phone_number`` (with or without ``+``, maybe spaced) -> E.164 or None."""
    if not isinstance(raw, str):
        return None
    phone = "".join(raw.split())
    if not phone.startswith("+"):
        phone = "+" + phone
    return phone if E164.fullmatch(phone) else None


class PhoneAllowlist:
    def __init__(self, document):
        if not isinstance(document, dict) or set(document) != {"schema_version", "entries"}:
            raise ValueError("allowlist must have exactly schema_version and entries")
        if type(document["schema_version"]) is not int or document["schema_version"] != 1:
            raise ValueError("unsupported allowlist schema_version")
        if not isinstance(document["entries"], list):
            raise ValueError("allowlist entries must be a list")
        roles = {}
        for index, entry in enumerate(document["entries"]):
            if not isinstance(entry, dict) or set(entry) != {"phone_e164", "role"}:
                raise ValueError(f"allowlist entry {index}: must have exactly phone_e164 and role")
            phone, role = entry["phone_e164"], entry["role"]
            if not isinstance(phone, str) or not E164.fullmatch(phone):
                raise ValueError(f"allowlist entry {index}: invalid phone_e164")
            if role not in ROLES:
                raise ValueError(f"allowlist entry {index}: invalid role")
            if phone in roles:
                raise ValueError(f"allowlist entry {index}: duplicate phone_e164")
            roles[phone] = role
        self._roles = roles

    @classmethod
    def from_json(cls, text):
        try:
            document = json.loads(text)
        except ValueError:
            raise ValueError("allowlist is not valid JSON") from None  # the parser message may quote the text
        return cls(document)

    @classmethod
    def from_file(cls, path):
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def lookup(self, e164):
        """Role for an E.164 number, or None."""
        return self._roles.get(e164) if isinstance(e164, str) else None

    def __repr__(self):
        return "PhoneAllowlist(entries=<redacted>)"  # str() falls back to this too
