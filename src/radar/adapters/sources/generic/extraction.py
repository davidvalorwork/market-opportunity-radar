"""Conservative, bounded contact candidates; never permission or WhatsApp proof.

No fetching, URL following, geocoding or account checks. Results belong only in
the owner's encrypted content blob, never a queue/control report or log.
"""

from dataclasses import dataclass, field
import re


@dataclass(frozen=True)
class ContactCandidate:
    kind: str
    value: str = field(repr=False)
    verified: bool = False


def contact_candidates(content: bytes) -> tuple[ContactCandidate, ...]:
    # A worker/parser can later supply structured extraction. Here scan only a
    # bounded UTF-8 prefix; binary content is left untouched as private evidence.
    text = content[:262_144].decode('utf-8', errors='replace')
    candidates = []
    seen = set()
    patterns = (
        ('email', r'(?<![\w.+-])[a-zA-Z0-9.!#$%&\'*+/=?^_`{|}~-]{1,64}@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+'),
        ('phone', r'(?<!\w)\+?\d[\d ()-]{5,30}\d(?!\w)'),
    )
    for kind, pattern in patterns:
        for match in re.finditer(pattern, text):
            value = match.group().strip()
            if kind == 'email' and len(value) > 254:
                continue
            if kind == 'phone' and not 7 <= len(re.sub(r'\D', '', value)) <= 15:
                continue
            key = (kind, value)
            if key not in seen:
                seen.add(key)
                candidates.append(ContactCandidate(kind, value))
            if len(candidates) >= 64:
                return tuple(candidates)
    return tuple(candidates)
