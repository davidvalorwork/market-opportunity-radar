"""Local Postgres archive (docker/local-db.compose.yml) for analysis and quick queries.

Plain-text copy of the owner's own activity: requests, AI plans, searches with
results, outbound sends and every WhatsApp message/chat/contact synced from each
local bridge's SQLite store. Loopback only; credentials in .local/pilot-state/db.env.
The control store (SQLite, encrypted) stays the source of truth: archive failures
never block the pilot.
"""
from pathlib import Path
from datetime import timedelta
from hashlib import sha1
import sqlite3

import psycopg
from psycopg.types.json import Jsonb

DDL = """
CREATE TABLE IF NOT EXISTS requests(ref TEXT PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), text TEXT, plan JSONB);
CREATE TABLE IF NOT EXISTS searches(id BIGSERIAL PRIMARY KEY, request_ref TEXT, query TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), results INT);
CREATE TABLE IF NOT EXISTS search_results(search_id BIGINT REFERENCES searches(id), position INT, url TEXT, title TEXT, snippet TEXT, phones TEXT[],
    PRIMARY KEY(search_id, position));
CREATE TABLE IF NOT EXISTS outbound(id BIGSERIAL PRIMARY KEY, request_ref TEXT, session TEXT, phone TEXT, label TEXT, message TEXT,
    state TEXT, detail TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS wa_chats(session TEXT, jid TEXT, name TEXT, last_message_time TIMESTAMPTZ, PRIMARY KEY(session, jid));
CREATE TABLE IF NOT EXISTS wa_messages(session TEXT, id TEXT, chat_jid TEXT, sender TEXT, content TEXT, ts TIMESTAMPTZ,
    is_from_me BOOLEAN, media_type TEXT, filename TEXT, PRIMARY KEY(session, id, chat_jid));
CREATE INDEX IF NOT EXISTS wa_messages_ts ON wa_messages(session, ts);
CREATE INDEX IF NOT EXISTS wa_messages_chat ON wa_messages(session, chat_jid, ts);
CREATE INDEX IF NOT EXISTS wa_messages_fts ON wa_messages USING gin(to_tsvector('spanish', coalesce(content, '')));
CREATE TABLE IF NOT EXISTS wa_contacts(session TEXT, jid TEXT, full_name TEXT, push_name TEXT, business_name TEXT, PRIMARY KEY(session, jid));
CREATE TABLE IF NOT EXISTS wa_lid(session TEXT, lid TEXT, pn TEXT, PRIMARY KEY(session, lid));
CREATE TABLE IF NOT EXISTS reply_summaries(id BIGSERIAL PRIMARY KEY, request_ref TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    covered_until TIMESTAMPTZ, messages INT, summary TEXT);
CREATE TABLE IF NOT EXISTS gmail_messages(id TEXT PRIMARY KEY, thread_id TEXT, ts TIMESTAMPTZ, sender TEXT, recipients TEXT,
    subject TEXT, snippet TEXT, body TEXT, labels TEXT[]);
CREATE INDEX IF NOT EXISTS gmail_ts ON gmail_messages(ts);
CREATE INDEX IF NOT EXISTS gmail_fts ON gmail_messages USING gin(to_tsvector('spanish', coalesce(subject, '') || ' ' || coalesce(body, '')));
CREATE TABLE IF NOT EXISTS gmail_attachments(message_id TEXT, filename TEXT, mime TEXT, size INT, PRIMARY KEY(message_id, filename));
ALTER TABLE outbound ADD COLUMN IF NOT EXISTS thread_id TEXT;
CREATE TABLE IF NOT EXISTS social_threads(platform TEXT, thread_key TEXT, thread_id TEXT, name TEXT, listing TEXT, last_text TEXT,
    last_time TEXT, unread BOOLEAN, seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(platform, thread_key));
CREATE TABLE IF NOT EXISTS social_messages(platform TEXT, thread_key TEXT, msg_key TEXT, author TEXT, is_me BOOLEAN, text TEXT,
    ts TIMESTAMPTZ, media TEXT, seen_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(platform, thread_key, msg_key));
CREATE INDEX IF NOT EXISTS social_fts ON social_messages USING gin(to_tsvector('spanish', coalesce(text, '')));
CREATE TABLE IF NOT EXISTS sync_state(session TEXT PRIMARY KEY, last_ts TIMESTAMPTZ);
-- Incoming messages after each successful send, matched by phone or its WhatsApp LID.
CREATE OR REPLACE VIEW outreach_replies AS
SELECT o.request_ref, o.session, o.phone, o.label, o.created_at AS sent_at, m.ts, m.content, m.media_type
FROM outbound o
JOIN wa_messages m ON m.session = o.session AND NOT m.is_from_me AND m.ts > o.created_at
 AND (m.chat_jid = o.phone OR m.chat_jid = substr(o.phone, 2) || '@s.whatsapp.net'
      OR m.chat_jid IN (SELECT l.lid || '@lid' FROM wa_lid l WHERE l.session = o.session AND l.pn = substr(o.phone, 2)))
WHERE o.state = 'sent' AND o.session <> 'gmail'
UNION ALL
SELECT o.request_ref, o.session, o.phone, o.label, o.created_at, g.ts,
       'Correo «' || coalesce(g.subject, '') || '» de ' || coalesce(g.sender, '') || ': ' || left(coalesce(g.body, g.snippet, ''), 3000), NULL
FROM outbound o
JOIN gmail_messages g ON g.thread_id = o.thread_id AND g.ts > o.created_at AND NOT ('SENT' = ANY(coalesce(g.labels, '{}')))
WHERE o.state = 'sent' AND o.session = 'gmail'
UNION ALL
SELECT o.request_ref, o.session, o.phone, o.label, o.created_at, coalesce(s.ts, s.seen_at), s.text, s.media
FROM outbound o
JOIN social_messages s ON s.platform = o.session AND s.thread_key = o.phone AND NOT s.is_me AND coalesce(s.ts, s.seen_at) > o.created_at
WHERE o.state = 'sent' AND o.session = 'x';

"""


def dsn_from_env_file(path, host='127.0.0.1', port=5434):
    values = dict(line.split('=', 1) for line in Path(path).read_text().splitlines() if '=' in line)
    return psycopg.conninfo.make_conninfo(host=host, port=port, dbname=values['POSTGRES_DB'],
        user=values['POSTGRES_USER'], password=values['POSTGRES_PASSWORD'], connect_timeout=5)


class Archive:
    def __init__(self, dsn):
        self.conn = psycopg.connect(dsn, autocommit=True)
        self.conn.execute(DDL)

    def request(self, ref, text, plan):
        self.conn.execute('INSERT INTO requests(ref, text, plan) VALUES (%s, %s, %s) ON CONFLICT (ref) DO UPDATE SET plan = EXCLUDED.plan',
            (ref, text, Jsonb(plan)))

    def search(self, request_ref, query, rows, phones):
        """rows: [(url, content bytes)]; phones: callable(text) -> numbers found in that result."""
        with self.conn.transaction():
            search_id = self.conn.execute('INSERT INTO searches(request_ref, query, results) VALUES (%s, %s, %s) RETURNING id',
                (request_ref, query, len(rows))).fetchone()[0]
            with self.conn.cursor() as cursor:
                cursor.executemany('INSERT INTO search_results VALUES (%s, %s, %s, %s, %s, %s)', [
                    (search_id, i, url, text.split('\n', 1)[0].removeprefix('Title: '), text.split('\n', 1)[-1], phones(text))
                    for i, (url, text) in enumerate((url, content.decode('utf-8', 'replace')) for url, content in rows)])

    def outbound(self, request_ref, session, phone, label, message, state, detail, thread_id=None):
        self.conn.execute('INSERT INTO outbound(request_ref, session, phone, label, message, state, detail, thread_id) '
            'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)', (request_ref, session, phone, label, message, state, detail, thread_id))

    def sync_whatsapp(self, session, store_dir):
        """Incremental copy from a bridge store (read-only). Returns new message rows seen."""
        store = Path(store_dir)
        last = self.conn.execute('SELECT last_ts FROM sync_state WHERE session = %s', (session,)).fetchone()
        # Bridge stores local-offset text timestamps: compare in the same offset, with a margin; the PK dedupes.
        since = (last[0].astimezone() - timedelta(hours=1)).isoformat(sep=' ', timespec='seconds') if last and last[0] else '0000'
        source = sqlite3.connect('file:' + (store / 'messages.db').as_posix() + '?mode=ro', uri=True, timeout=5)
        try:
            messages = source.execute('SELECT id, chat_jid, sender, content, timestamp, is_from_me, media_type, filename FROM messages '
                'WHERE timestamp >= ? ORDER BY timestamp', (since,)).fetchall()
            chats = source.execute('SELECT jid, name, last_message_time FROM chats').fetchall()
        finally:
            source.close()
        identity = sqlite3.connect('file:' + (store / 'whatsapp.db').as_posix() + '?mode=ro', uri=True, timeout=5)
        try:
            contacts = identity.execute('SELECT their_jid, full_name, push_name, business_name FROM whatsmeow_contacts').fetchall()
            lids = identity.execute('SELECT lid, pn FROM whatsmeow_lid_map').fetchall()
        finally:
            identity.close()
        with self.conn.transaction(), self.conn.cursor() as cursor:
            cursor.executemany('INSERT INTO wa_messages VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                [(session, *row[:5], bool(row[5]), *row[6:]) for row in messages])
            cursor.executemany('INSERT INTO wa_chats VALUES (%s, %s, %s, %s) ON CONFLICT (session, jid) DO UPDATE SET name = EXCLUDED.name, '
                'last_message_time = EXCLUDED.last_message_time', [(session, *row) for row in chats])
            cursor.executemany('INSERT INTO wa_contacts VALUES (%s, %s, %s, %s, %s) ON CONFLICT (session, jid) DO UPDATE SET '
                'full_name = EXCLUDED.full_name, push_name = EXCLUDED.push_name, business_name = EXCLUDED.business_name',
                [(session, *row) for row in contacts])
            cursor.executemany('INSERT INTO wa_lid VALUES (%s, %s, %s) ON CONFLICT (session, lid) DO UPDATE SET pn = EXCLUDED.pn',
                [(session, *row) for row in lids])
            if messages:
                cursor.execute('INSERT INTO sync_state VALUES (%s, %s) ON CONFLICT (session) DO UPDATE SET last_ts = EXCLUDED.last_ts',
                    (session, messages[-1][4]))
        return len(messages)

    def pending_replies(self, quiet_seconds=90):
        """{request_ref: [(phone, label, ts, content)]} with replies newer than the last summary, once quiet."""
        rows = self.conn.execute('''
            SELECT r.request_ref, r.phone, r.label, r.ts, coalesce(r.content, '[' || r.media_type || ']')
            FROM outreach_replies r
            WHERE r.ts > coalesce((SELECT max(covered_until) FROM reply_summaries s WHERE s.request_ref = r.request_ref), '-infinity')
              AND r.request_ref IN (SELECT request_ref FROM outreach_replies GROUP BY request_ref
                                    HAVING max(ts) < now() - make_interval(secs => %s))
            ORDER BY r.request_ref, r.phone, r.ts''', (quiet_seconds,)).fetchall()
        grouped = {}
        for ref, *rest in rows:
            grouped.setdefault(ref, []).append(tuple(rest))
        return grouped

    def read_messages(self, names, keywords, hours, limit=300, phones=()):
        """Newest-first [(ts, chat, author, content)] by chat/contact name or phone, Spanish keywords and time window."""
        hours = max(1, min(int(hours or 24), 24 * 365))
        digits = [p.lstrip('+') for p in phones]
        return self.conn.execute('''
            SELECT m.ts, coalesce(CASE WHEN c.name !~ '^[0-9+ ]*$' THEN c.name END, nullif(cc.full_name, ''), nullif(cc.push_name, ''),
                   nullif(cc.business_name, ''), '+' || l.pn, nullif(c.name, ''), m.chat_jid),
                   CASE WHEN m.is_from_me THEN 'yo' ELSE coalesce(nullif(ct.full_name, ''), nullif(ct.push_name, ''), m.sender) END,
                   coalesce(nullif(m.content, ''), '[' || nullif(m.media_type, '') || ']', '')
            FROM wa_messages m
            LEFT JOIN wa_chats c ON c.session = m.session AND c.jid = m.chat_jid
            LEFT JOIN wa_contacts cc ON cc.session = m.session AND cc.jid = m.chat_jid
            LEFT JOIN wa_lid l ON l.session = m.session AND m.chat_jid = l.lid || '@lid'
            LEFT JOIN LATERAL (SELECT full_name, push_name FROM wa_contacts x WHERE x.session = m.session
                AND x.jid IN (m.sender || '@s.whatsapp.net', m.sender || '@lid') LIMIT 1) ct ON true
            WHERE m.ts > now() - make_interval(hours => %s)
              AND ((cardinality(%s::text[]) = 0 AND cardinality(%s::text[]) = 0)
                   OR concat_ws(' ', c.name, cc.full_name, cc.push_name, cc.business_name) ILIKE ANY (SELECT '%%' || n || '%%' FROM unnest(%s::text[]) n)
                   OR split_part(m.chat_jid, '@', 1) = ANY(%s) OR l.pn = ANY(%s))
              AND (%s = '' OR to_tsvector('spanish', coalesce(m.content, '')) @@ websearch_to_tsquery('spanish', %s))
            ORDER BY m.ts DESC LIMIT %s''', (hours, names, digits, names, digits, digits, keywords, keywords, limit)).fetchall()

    def resolve_chats(self, names, per_name=5):
        """[(session, jid, display name)] of existing chats whose chat, saved-contact, profile or business name matches."""
        found = []
        for name in names:
            found += self.conn.execute('''SELECT c.session, c.jid, coalesce(CASE WHEN c.name !~ '^[0-9+ ]*$' THEN c.name END, nullif(cc.full_name, ''), nullif(cc.push_name, ''),
                   nullif(cc.business_name, ''), '+' || l.pn, nullif(c.name, ''), c.jid)
                FROM wa_chats c
                LEFT JOIN wa_contacts cc ON cc.session = c.session AND cc.jid = c.jid
                LEFT JOIN wa_lid l ON l.session = c.session AND c.jid = l.lid || '@lid'
                WHERE concat_ws(' ', c.name, cc.full_name, cc.push_name, cc.business_name) ILIKE %s
                ORDER BY c.last_message_time DESC NULLS LAST LIMIT %s''', ('%' + name + '%', per_name)).fetchall()
        return list(dict.fromkeys(found))

    def store_emails(self, mails):
        with self.conn.transaction(), self.conn.cursor() as cursor:
            cursor.executemany('INSERT INTO gmail_messages VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO UPDATE SET labels = EXCLUDED.labels',
                [(m['id'], m['thread_id'], m['ts'], m['from'], m['to'], m['subject'], m['snippet'], m['body'], m['labels']) for m in mails])
            cursor.executemany('INSERT INTO gmail_attachments VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING',
                [(m['id'], a['filename'], a['mime'], a['size']) for m in mails for a in m.get('attachments', ())])

    def missing_emails(self, ids):
        known = {row[0] for row in self.conn.execute('SELECT id FROM gmail_messages WHERE id = ANY(%s)', (ids,)).fetchall()}
        return [i for i in ids if i not in known]

    @staticmethod
    def _thread_key(row):
        return row.get('thread_id') or sha1((row.get('name', '') + '|' + row.get('listing', '')).encode()).hexdigest()

    def store_social(self, threads, messages=()):
        """Upsert thread rows; messages carry their thread_id. Marketplace snippets are kept as messages too.
        Returns the threads whose last_text changed since the previous sync."""
        changed = []
        with self.conn.transaction(), self.conn.cursor() as cursor:
            for row in threads:
                key = self._thread_key(row)
                before = cursor.execute('SELECT last_text FROM social_threads WHERE platform = %s AND thread_key = %s', (row['platform'], key)).fetchone()
                if before is None or before[0] != row.get('last_text'):
                    changed.append(row)
                cursor.execute('''INSERT INTO social_threads(platform, thread_key, thread_id, name, listing, last_text, last_time, unread)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (platform, thread_key) DO UPDATE SET name = EXCLUDED.name,
                    listing = EXCLUDED.listing, last_text = EXCLUDED.last_text, last_time = EXCLUDED.last_time, unread = EXCLUDED.unread, seen_at = now()''',
                    (row['platform'], key, row.get('thread_id'), row.get('name'), row.get('listing'), row.get('last_text'), row.get('last_time'), row.get('unread')))
                if row['platform'] == 'marketplace' and row.get('last_text'):
                    messages = list(messages) + [{'platform': 'marketplace', 'thread_id': key, 'author': row.get('name'), 'is_me': False,
                        'text': row['last_text'], 'ts': None, 'media': None, 'message_id': None}]
            cursor.executemany('INSERT INTO social_messages(platform, thread_key, msg_key, author, is_me, text, ts, media) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING',
                [(m['platform'], m['thread_id'], m.get('message_id') or sha1('|'.join(str(m.get(k) or '') for k in ('author', 'time', 'text')).encode()).hexdigest(),
                  m.get('author'), bool(m.get('is_me')), m.get('text'), m.get('ts'), m.get('media')) for m in messages])
        return changed

    def resolve_social(self, platform, names, per_name=5):
        found = []
        for name in names:
            found += self.conn.execute('''SELECT thread_id, name FROM social_threads WHERE platform = %s AND thread_id IS NOT NULL
                AND name ILIKE %s ORDER BY seen_at DESC LIMIT %s''', (platform, '%' + name + '%', per_name)).fetchall()
        return list(dict.fromkeys(found))

    def request_text(self, ref):
        row = self.conn.execute('SELECT text FROM requests WHERE ref = %s', (ref,)).fetchone()
        return row[0] if row else ''

    def summarized(self, ref, covered_until, messages, summary):
        self.conn.execute('INSERT INTO reply_summaries(request_ref, covered_until, messages, summary) VALUES (%s, %s, %s, %s)',
            (ref, covered_until, messages, summary))


if __name__ == '__main__':
    # Live check against the local container in a throwaway schema; bridge stores faked with SQLite.
    import tempfile
    from datetime import datetime
    dsn = dsn_from_env_file(Path(__file__).resolve().parents[4] / '.local' / 'pilot-state' / 'db.env')
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute('DROP SCHEMA IF EXISTS selfcheck CASCADE; CREATE SCHEMA selfcheck')
    archive = Archive(dsn + ' options=-csearch_path=selfcheck')
    with tempfile.TemporaryDirectory() as folder:
        sent = datetime.now().astimezone().replace(microsecond=0) - timedelta(minutes=10)
        db = sqlite3.connect(Path(folder) / 'messages.db')
        db.executescript("CREATE TABLE chats(jid,name,last_message_time); CREATE TABLE messages(id,chat_jid,sender,content,timestamp,is_from_me,media_type,filename);"
            "INSERT INTO chats VALUES ('111@lid','Taller Uno',NULL);")
        reply = (sent + timedelta(minutes=5)).isoformat(sep=' ')
        db.executemany('INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)', [('m1', '111@lid', '111', 'Son 40$', reply, 0, '', ''),
            ('m2', '584140000000@s.whatsapp.net', '', 'hola', (sent - timedelta(minutes=1)).isoformat(sep=' '), 1, '', '')])
        db.commit(); db.close()
        db = sqlite3.connect(Path(folder) / 'whatsapp.db')
        db.executescript("CREATE TABLE whatsmeow_contacts(their_jid,full_name,push_name,business_name); CREATE TABLE whatsmeow_lid_map(lid,pn);"
            "INSERT INTO whatsmeow_lid_map VALUES ('111','584140000000');")
        db.commit(); db.close()
        assert archive.sync_whatsapp('principal', folder) == 2
        archive.sync_whatsapp('principal', folder)  # overlap window re-reads; PK dedupes
        assert archive.conn.execute('SELECT count(*) FROM wa_messages').fetchone()[0] == 2
        archive.request('r1', 'pedido', {'message': 'm'})
        archive.search('r1', 'q', [('https://a', b'Title: T\nLlama 0414-000.00.00')], lambda text: ['+584140000000'])
        archive.conn.execute("INSERT INTO outbound(request_ref, session, phone, label, message, state, detail, created_at) "
            "VALUES ('r1','principal','+584140000000','T','m','sent','',%s)", (sent,))
        pending = archive.pending_replies(quiet_seconds=60)
        assert list(pending) == ['r1'] and pending['r1'][0][3] == 'Son 40$', pending
        archive.summarized('r1', pending['r1'][-1][2], 1, 'ok')
        read = archive.read_messages(['taller'], 'precio', 24)
        assert archive.read_messages(['uno'], '', 24)[0][1:] == ('Taller Uno', '111', 'Son 40$'), read
        assert archive.read_messages(['otro'], '', 24) == []
        assert archive.resolve_chats(['taller', 'uno']) == [('principal', '111@lid', 'Taller Uno')]
        archive.conn.execute("INSERT INTO wa_contacts VALUES ('principal', '111@lid', 'Pedro Pérez', 'Pedrito', '')")
        assert archive.resolve_chats(['pedrito']) == [('principal', '111@lid', 'Taller Uno')]
        assert len(archive.read_messages([], '', 24, phones=['+584140000000'])) == 2  # phone chat + its LID chat
        assert archive.read_messages(['pérez'], '', 24)[0][3] == 'Son 40$'
        mail = {'id': 'g1', 'thread_id': 't1', 'ts': sent, 'from': 'a@x.com', 'to': 'yo@x.com', 'subject': 'Factura',
            'snippet': 's', 'body': 'Total 40$', 'labels': ['INBOX']}
        archive.store_emails([mail]); archive.store_emails([{**mail, 'labels': []}])
        assert archive.missing_emails(['g1', 'g2']) == ['g2']
        archive.outbound('r2', 'gmail', 'a@x.com', 'Factura', 'm', 'sent', '', 't1')
        archive.conn.execute("UPDATE outbound SET created_at = %s WHERE request_ref = 'r2'", (sent - timedelta(minutes=1),))
        archive.store_emails([{**mail, 'id': 'g3', 'body': 'Recibido, gracias', 'attachments': [{'filename': 'f.pdf', 'mime': 'x', 'size': 1}]},
            {**mail, 'id': 'g4', 'labels': ['SENT']}])
        replies = archive.pending_replies(quiet_seconds=60)['r2']
        assert len(replies) == 2 and any(r[3].endswith('Recibido, gracias') for r in replies), replies  # g4 is ours (SENT)
        archive.summarized('r2', max(r[2] for r in replies), 2, 'ok')
        thread = {'platform': 'x', 'thread_id': 'x1', 'name': 'Ana X', 'last_text': 'hola', 'last_time': '3 h', 'unread': True}
        assert archive.store_social([thread]) == [thread] and archive.store_social([thread]) == []
        archive.outbound('r3', 'x', 'x1', 'Ana X', 'm', 'sent', '')
        archive.store_social([], [{'platform': 'x', 'thread_id': 'x1', 'author': 'Ana', 'is_me': False, 'text': 'Vale, 30$',
            'ts': datetime.now().astimezone(), 'media': None, 'message_id': 'mx'}])
        archive.conn.execute("UPDATE outbound SET created_at = now() - interval '1 hour' WHERE request_ref = 'r3'")
        assert archive.conn.execute("SELECT content FROM outreach_replies WHERE request_ref = 'r3'").fetchone()[0] == 'Vale, 30$'
        assert archive.resolve_social('x', ['ana']) == [('x1', 'Ana X')]
        market = {'platform': 'marketplace', 'name': 'Luis', 'listing': 'Silla', 'last_text': '¿disponible?', 'last_time': '2 h', 'unread': True}
        archive.store_social([market])
        assert archive.conn.execute("SELECT count(*) FROM social_messages WHERE platform = 'marketplace'").fetchone()[0] == 1
        assert archive.pending_replies(quiet_seconds=60) == {}
    archive.conn.execute('DROP SCHEMA selfcheck CASCADE')
    print('ok')
