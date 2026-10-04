"""Literal bounded notes, not semantic synthesis or instructions from sources."""
from .models import CitationSpan, Note


def extract_notes(materials, *, fields=(), limit=64):
    notes = []
    for material in materials:
        text = material.content.decode('utf-8')
        offset = 0
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if stripped and (not fields or any(stripped.casefold().startswith(name.casefold() + ':') for name in fields)):
                start = offset + line.index(stripped)
                # Clip the observed span itself, never assert omitted content was extracted.
                statement = stripped[:2048]
                notes.append(Note('note:literal_' + str(len(notes)), statement, 'extracted',
                                  (CitationSpan(material.source_ref, start, start + len(statement)),)))
                if len(notes) >= limit:
                    return tuple(notes)
            offset += len(line)
    return tuple(notes)
