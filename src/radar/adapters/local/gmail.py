"""Owner's Gmail through the local Google Workspace CLI (gws), already authorized by the owner.

Read: Gmail search syntax -> messages with plain-text bodies. Send: one RFC 5322 message,
optionally threaded as a reply. Credentials stay in gws's encrypted store/OS keyring; this
module never reads or prints them. Sends happen only after the owner's Telegram approval.
"""
import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

EMAIL = re.compile(r'(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+')


class GmailError(Exception):
    pass


def gws_path():
    # The Startup launcher may run with a shorter PATH than an interactive shell: also try the npm install next to node.
    for shim in (shutil.which('gws'), shutil.which('node')):
        native = Path(shim).parent / 'node_modules' / '@googleworkspace' / 'cli' / 'bin' / 'gws.exe' if shim else None
        if native and native.is_file():
            return str(native)
    raise GmailError('gws_missing')


def call(args, params, body=None, *, timeout=60, runner=subprocess.run):
    argv = [gws_path(), *args, '--params', json.dumps(params)] + (['--json', json.dumps(body)] if body is not None else [])
    result = runner(argv, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        document = json.loads(result.stdout.decode('utf-8'))
    except ValueError:
        raise GmailError('gws_output_invalid') from None
    if result.returncode or (isinstance(document, dict) and 'error' in document):
        raise GmailError('gws_failed')
    return document


def _decode(data):
    return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)).decode('utf-8', 'replace')


def _text(payload, mime='text/plain'):
    if payload.get('mimeType') == mime and payload.get('body', {}).get('data'):
        return _decode(payload['body']['data'])
    for part in payload.get('parts', ()):
        found = _text(part, mime)
        if found:
            return found
    return ''


def _attachments(payload):
    found = [{'id': payload['body']['attachmentId'], 'filename': payload['filename'], 'mime': payload.get('mimeType', ''),
        'size': payload['body'].get('size', 0)}] if payload.get('filename') and payload.get('body', {}).get('attachmentId') else []
    for part in payload.get('parts', ()):
        found += _attachments(part)
    return found


def parse(message):
    payload = message.get('payload', {})
    headers = {h['name'].lower(): h['value'] for h in payload.get('headers', ())}
    body = _text(payload) or re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html.unescape(_text(payload, 'text/html'))))
    return {'id': message['id'], 'thread_id': message.get('threadId'), 'labels': message.get('labelIds', []),
        'ts': datetime.fromtimestamp(int(message.get('internalDate', 0)) / 1000, timezone.utc),
        'from': headers.get('from', ''), 'reply_to': headers.get('reply-to', ''), 'to': headers.get('to', ''), 'subject': headers.get('subject', ''),
        'message_id': headers.get('message-id', ''), 'snippet': html.unescape(message.get('snippet', '')),
        'body': body.strip()[:20000], 'attachments': _attachments(payload)}


def list_ids(query, limit=100, **kw):
    document = call(['gmail', 'users', 'messages', 'list'], {'userId': 'me', 'q': query, 'maxResults': max(1, min(limit, 500))}, **kw)
    return [row['id'] for row in document.get('messages', ())]


def get(message_id, **kw):
    return parse(call(['gmail', 'users', 'messages', 'get'], {'userId': 'me', 'id': message_id, 'format': 'full'}, **kw))


def attachment(message_id, attachment_id, **kw):
    document = call(['gmail', 'users', 'messages', 'attachments', 'get'],
        {'userId': 'me', 'messageId': message_id, 'id': attachment_id}, timeout=120, **kw)
    return base64.urlsafe_b64decode(document['data'] + '=' * (-len(document['data']) % 4))


READABLE = ('.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.txt', '.csv', '.md', '.json', '.html', '.xml')
OFFICE = ('.docx', '.xlsx', '.pptx')


def office_text(data):
    """Visible text of a .docx/.xlsx/.pptx (OOXML zip) using the stdlib only."""
    import io
    parts = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in sorted(archive.namelist()):
            if re.fullmatch(r'word/document\.xml|xl/sharedStrings\.xml|xl/worksheets/sheet\d+\.xml|ppt/slides/slide\d+\.xml', name):
                xml = archive.read(name).decode('utf-8', 'replace')
                parts.append(html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', xml))).strip())
    return '\n\n'.join(p for p in parts if p)


def save_attachments(mails, folder, *, max_files=5, max_bytes=10 * 1024 * 1024, **kw):
    """Download readable attachments of the given messages into folder -> [(filename, subject)]."""
    saved = []
    for mail in mails:
        for item in mail.get('attachments', ()):
            suffix = Path(item['filename']).suffix.lower()
            if len(saved) >= max_files or item['size'] > max_bytes or suffix not in READABLE + OFFICE:
                continue
            data = attachment(mail['id'], item['id'], **kw)
            name = str(len(saved) + 1) + '_' + re.sub(r'[^\w.-]', '_', item['filename'])[-80:]
            if suffix in OFFICE:
                name, data = name + '.txt', office_text(data).encode('utf-8')
            (Path(folder) / name).write_bytes(data)
            saved.append((name, mail['subject']))
    return saved


def search(query, limit=20, **kw):
    return [get(message_id, **kw) for message_id in list_ids(query, limit, **kw)]


def build_raw(to, subject, body, in_reply_to=''):
    message = EmailMessage()
    message['To'], message['Subject'] = to, subject
    if in_reply_to:
        message['In-Reply-To'] = message['References'] = in_reply_to
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def send(to, subject, body, *, thread_id=None, in_reply_to='', **kw):
    """-> ('sent'|'failed', detail, thread_id). One API call; never retried automatically."""
    if not EMAIL.fullmatch(parseaddr(to)[1] or ''):
        return 'failed', 'invalid_address', None
    try:
        document = call(['gmail', 'users', 'messages', 'send'], {'userId': 'me'},
            {'raw': build_raw(to, subject, body, in_reply_to), **({'threadId': thread_id} if thread_id else {})}, **kw)
        return 'sent', '', document.get('threadId', thread_id)
    except (GmailError, subprocess.TimeoutExpired, OSError) as error:
        return 'failed', str(error) or type(error).__name__, None


if __name__ == '__main__':
    encode = lambda text: base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')
    sample = {'id': 'm1', 'threadId': 't1', 'internalDate': '1759690000000', 'snippet': 'Hola &amp; chao',
        'payload': {'mimeType': 'multipart/alternative', 'headers': [{'name': 'From', 'value': 'Ana <ana@x.com>'},
            {'name': 'Subject', 'value': 'Precio'}, {'name': 'Message-ID', 'value': '<a@x>'}],
            'parts': [{'mimeType': 'text/html', 'body': {'data': encode('<p>Son <b>40$</b></p>')}}]}}
    parsed = parse(sample)
    assert parsed['body'] == 'Son 40$' and parsed['subject'] == 'Precio' and parsed['snippet'] == 'Hola & chao', parsed
    raw = base64.urlsafe_b64decode(build_raw('ana@x.com', 'Re: Precio', 'Gracias', '<a@x>')).decode()
    assert 'In-Reply-To: <a@x>' in raw and 'To: ana@x.com' in raw
    assert EMAIL.findall('escríbele a ana.p@x.com y a b+c@y.co.ve.') == ['ana.p@x.com', 'b+c@y.co.ve']
    fake = lambda argv, **kw: type('R', (), {'returncode': 0, 'stdout': b'{"id":"s1","threadId":"t9"}'})()
    assert send('ana@x.com', 's', 'b', runner=fake) == ('sent', '', 't9') and send('nope', 's', 'b', runner=fake)[0] == 'failed'
    nested = {'mimeType': 'multipart/mixed', 'parts': [{'mimeType': 'text/plain', 'body': {'data': encode('hola')}},
        {'mimeType': 'application/pdf', 'filename': 'f.pdf', 'body': {'attachmentId': 'A1', 'size': 10}}]}
    assert _attachments(nested) == [{'id': 'A1', 'filename': 'f.pdf', 'mime': 'application/pdf', 'size': 10}]
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as doc:
        doc.writestr('word/document.xml', '<w:document><w:p><w:t>Total: 40 &amp; IVA</w:t></w:p></w:document>')
    assert office_text(buffer.getvalue()) == 'Total: 40 & IVA'
    print('ok')
