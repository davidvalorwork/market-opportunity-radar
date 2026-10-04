"""Private artifact bytes only: plain Telegram, JSON, inert self-contained HTML."""
from .models import canonical


def escape(value):
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#x27;')


def citation_text(citation):
    suffix = '' if citation['public_url'] is None else ' ' + citation['public_url']
    return f"[{citation['source_ref']} {citation['observed_at']} {citation['provenance']} sha256:{citation['content_hash']} span:{citation['start']}-{citation['end']}]" + suffix


def render_telegram(document):
    paragraphs = [f"Informe: {document['topic']}\nEstado: {document['status']}\nCitas no demuestran verdad de la afirmación."]
    for note in document['notes']:
        paragraphs.append(f"({note['kind']}) {note['statement']}\n" + '\n'.join(citation_text(cite) for cite in note['citations']))
    paragraphs.append('Cobertura: ' + '; '.join(f"{row['source_ref']}={row['status']}({row['code'] or row['provenance'] or 'sin_datos'})" for row in document['coverage']))
    if document['reasons']:
        paragraphs.append('Límites: ' + ', '.join(document['reasons']))
    # UTF-16 units conservatively bounded for a future Bot API boundary, no parse_mode.
    chunks, current = [], ''
    for paragraph in paragraphs:
        remaining = paragraph
        while remaining:
            piece, units = '', 0
            for char in remaining:
                cost = 2 if ord(char) > 0xffff else 1
                if units + cost > 3800:
                    break
                piece += char
                units += cost
            if current:
                chunks.append(current)
            current, remaining = piece, remaining[len(piece):]
    if current:
        chunks.append(current)
    return canonical({'parse_mode': None, 'link_preview_options': {'is_disabled': True}, 'messages': chunks}).encode('utf-8')


def render_html(document):
    parts = ['<!doctype html><html lang="es"><head><meta charset="utf-8">',
             '<meta http-equiv="Content-Security-Policy" content="default-src &#x27;none&#x27;; base-uri &#x27;none&#x27;; form-action &#x27;none&#x27;">',
             '<title>Informe privado</title></head><body>',
             '<h1>' + escape(document['topic']) + '</h1>',
             '<p>Estado: ' + escape(document['status']) + '</p>',
             '<p>Extractos, inferencias y notas no verificadas; una cita no demuestra verdad.</p><ol>']
    for note in document['notes']:
        parts.append('<li><p>' + escape(note['kind']) + '</p><blockquote>' + escape(note['statement']) + '</blockquote>')
        for citation in note['citations']:
            parts.append('<p>' + escape(citation_text(citation)) + '</p>')
        parts.append('</li>')
    parts.append('</ol><h2>Fuentes y cobertura</h2><ul>')
    for row in document['coverage']:
        parts.append('<li>' + escape(row['source_ref']) + ': ' + escape(row['status']) + ' (' + escape(row['code'] or row['provenance'] or 'sin_datos') + ')</li>')
    parts.append('</ul><h2>Límites</h2><p>' + escape(', '.join(document['reasons'])) + '</p></body></html>')
    return ''.join(parts).encode('utf-8')
