"""Observed span checks. Citation validity is not truth/independent corroboration."""
from .models import CitationSpan, Note, ReportError, digest, opaque


def verify(note, materials):
    if not isinstance(note, Note) or not isinstance(note.statement, str) or not note.statement.strip() or len(note.statement.encode('utf-8')) > 8192 or note.kind not in ('extracted', 'inference', 'unverified') or not isinstance(note.citations, tuple) or len(note.citations) > 16:
        raise ReportError('invalid_note')
    opaque(note.note_ref)
    if not note.citations:
        raise ReportError('missing_citation')
    citations = []
    for span in note.citations:
        if not isinstance(span, CitationSpan) or span.source_ref not in materials:
            raise ReportError('unknown_citation')
        material = materials[span.source_ref]
        text = material.content.decode('utf-8')
        if type(span.start) is not int or type(span.end) is not int or not 0 <= span.start < span.end <= len(text):
            raise ReportError('citation_out_of_bounds')
        quote = text[span.start:span.end]
        if note.kind == 'extracted' and note.statement != quote:
            raise ReportError('extracted_quote_mismatch')
        pointer = material.private_ref
        citations.append({'source_ref': span.source_ref, 'start': span.start, 'end': span.end,
                          'observed_at': material.observed_at.isoformat(), 'content_hash': material.content_hash,
                          'quote_hash': digest(quote.encode('utf-8')), 'provenance': material.provenance,
                          'public_url': material.public_url,
                          'evidence_format': material.evidence_format,
                          'origins': [{'source_ref': ref, 'page': page, 'observed_at': observed.isoformat()} for ref, page, observed in material.origins],
                          'evidence_ref': None if pointer is None else {'blob_key': pointer.blob_key, 'sha256': pointer.sha256, 'recipient_scope': pointer.recipient_scope}})
    return {'note_ref': note.note_ref, 'statement': note.statement, 'kind': note.kind, 'citations': citations}
