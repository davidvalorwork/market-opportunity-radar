"""Local conversation control extension sharing A3 SQLite transactions/ledger.

No live dispatcher. Canonical wire preparation is offline and never publishes;
the executable outbox below consists of local private intents.
"""
from dataclasses import asdict, replace
from datetime import timedelta
from hashlib import sha256
import json
import re
from uuid import UUID, uuid4

from radar.application.conversations.models import (
    Account, ApprovalScreen, Batch, Channel, ConversationError, Delivery,
    DispatchResult, Incoming, IncomingPage, canonical, fingerprint, ref, validate_drafts,
)
from radar.domain.core import utc
from radar.contracts import validate
from radar.ports.types import Approval, BlobPointer, ConditionalConflict, LedgerRecord, LedgerState
from .sqlite import parse, stamp


DDL = """
CREATE TABLE IF NOT EXISTS conversation_accounts(owner TEXT,account TEXT,doc TEXT,PRIMARY KEY(owner,account));
CREATE TABLE IF NOT EXISTS conversation_session_accounts(owner TEXT,session TEXT,account TEXT,PRIMARY KEY(owner,session));
CREATE TABLE IF NOT EXISTS conversation_chats(owner TEXT,account TEXT,chat TEXT,recipient TEXT,identity TEXT,enabled INTEGER,PRIMARY KEY(owner,account,chat));
CREATE TABLE IF NOT EXISTS conversation_messages(owner TEXT,account TEXT,chat TEXT,message TEXT,pointer TEXT,PRIMARY KEY(owner,account,message));
CREATE TABLE IF NOT EXISTS conversation_batches(owner TEXT,actor TEXT,batch TEXT,account TEXT,version INTEGER,status TEXT,pointer TEXT,hash TEXT,operations TEXT,expires TEXT,PRIMARY KEY(owner,batch));
CREATE INDEX IF NOT EXISTS conversation_batches_by_status ON conversation_batches(owner,status);
CREATE TABLE IF NOT EXISTS conversation_events(owner TEXT,actor TEXT,event TEXT,hash TEXT,batch TEXT,PRIMARY KEY(owner,actor,event));
CREATE TABLE IF NOT EXISTS conversation_dedupe(owner TEXT,account TEXT,recipient TEXT,purpose TEXT,op TEXT,PRIMARY KEY(owner,account,recipient,purpose));
CREATE TABLE IF NOT EXISTS conversation_actions(owner TEXT,op TEXT,batch TEXT,account TEXT,chat TEXT,recipient TEXT,purpose TEXT,hash TEXT,PRIMARY KEY(owner,op));
CREATE TABLE IF NOT EXISTS conversation_displays(owner TEXT,actor TEXT,token TEXT,batch TEXT,version INTEGER,hash TEXT,operations TEXT,display_hash TEXT,used INTEGER DEFAULT 0,PRIMARY KEY(owner,token));
CREATE TABLE IF NOT EXISTS conversation_approvals(owner TEXT,actor TEXT,event TEXT,token TEXT,batch TEXT,PRIMARY KEY(owner,actor,event));
CREATE TABLE IF NOT EXISTS conversation_usage(owner TEXT,account TEXT,day TEXT,reserved INTEGER,next_allowed TEXT,PRIMARY KEY(owner,account,day));
CREATE TABLE IF NOT EXISTS conversation_reads(owner TEXT,account TEXT,day TEXT,calls INTEGER,PRIMARY KEY(owner,account,day));
CREATE TABLE IF NOT EXISTS conversation_outbox(owner TEXT,op TEXT,entry TEXT,status TEXT,PRIMARY KEY(owner,op));
CREATE TABLE IF NOT EXISTS conversation_wire_preparations(owner TEXT,op TEXT,doc TEXT,PRIMARY KEY(owner,op));
"""


SELF_TEST_TEXT = 'Prueba local de Market Opportunity Radar: Telegram → WhatsApp.'
SELF_TEST_PURPOSE = 'purpose:selftest'


class SQLiteConversations:
    def __init__(self, store, *, vault, capabilities, clock, channels=(), synthetic_authorized=False,
                 daily_messages=20, min_interval_seconds=10, private_scope='worker:conversations', allow_self_test=False,
                 session_trial_authorizer=None):
        if vault is None or capabilities is None:
            raise ConversationError('private_backend_and_capabilities_required')
        if (type(daily_messages) is not int or not 1 <= daily_messages <= 1000
                or type(min_interval_seconds) is not int or not 1 <= min_interval_seconds <= 3600
                or type(synthetic_authorized) is not bool or type(allow_self_test) is not bool
                or (session_trial_authorizer is not None and not callable(session_trial_authorizer))
                or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}:[a-z][a-z0-9_-]{0,63}', private_scope)):
            raise ConversationError('invalid_configuration')
        self.store, self.vault, self.capabilities = store, vault, capabilities
        self.clock = clock
        if not isinstance(channels, tuple) or any(not isinstance(c, Channel) for c in channels):
            raise ConversationError('invalid_registry')
        self.channels = {(c.name, c.backend): c for c in channels}
        if len(self.channels) != len(channels):
            raise ConversationError('invalid_registry')
        self.synthetic_authorized = synthetic_authorized
        self.daily_messages, self.min_interval_seconds = daily_messages, min_interval_seconds
        self.private_scope = private_scope
        self.allow_self_test = allow_self_test
        self.session_trial_authorizer = session_trial_authorizer
        self.store.db.executescript(DDL)

    def _now(self, supplied):
        return max(utc(supplied), utc(self.clock.now()))

    def _authority(self, authority, now):
        now = self._now(now)
        self.store._active(authority.owner_ref)
        if (utc(authority.expires_at) <= utc(now) or not self.store.owner_actor(authority.owner_ref, authority.actor_ref)
                or not self.store.current_consent(authority.owner_ref, authority.actor_ref)):
            raise ConditionalConflict('conversation_authority')

    def _check(self, authority, account_ref, operation, now):
        now = self._now(now)
        self._authority(authority, now)
        if not ref(account_ref):
            raise ConversationError('invalid_account_reference')
        row = self.store.db.execute('SELECT doc FROM conversation_accounts WHERE owner=? AND account=?',
                                    (authority.owner_ref, account_ref)).fetchone()
        if not row:
            raise ConditionalConflict('unknown_account')
        data = json.loads(row[0])
        data['expires_at'] = parse(data['expires_at'])
        account = Account(**data)
        mapping = self.store.db.execute('SELECT account FROM conversation_session_accounts WHERE owner=? AND session=?',
                                        (authority.owner_ref, account.session_ref)).fetchone()
        if mapping != (account_ref,):
            raise ConditionalConflict('session_account_binding')
        channel = self.channels.get((account.channel, account.backend))
        capability = dict(authority.capabilities).get(operation)
        version = self.store.db.execute('SELECT version FROM sessions WHERE owner=? AND ref=?',
                                        (authority.owner_ref, account.session_ref)).fetchone()
        if (channel is None or not channel.enabled or capability is None
                or not self.store.source_allowed(owner_ref=authority.owner_ref, source_ref=capability)
                or (account.session_ref, account.session_version) not in authority.sessions
                or version != (account.session_version,) or account.expires_at <= utc(now)):
            raise ConditionalConflict('conversation_permission_or_session')
        cap = self.capabilities.get(owner_ref=authority.owner_ref, platform=account.channel, backend=account.backend,
                                    operation=operation, session_ref=account.session_ref)
        allowed = ('probado_local', 'probado_real') if channel.fixture_only else ('probado_real',)
        trial = (not channel.fixture_only and cap is not None and cap.status == 'documentado'
            and self.session_trial_authorizer is not None
            and self.session_trial_authorizer(authority=authority,account=account,operation=operation) is True)
        if (cap is None or cap.authorized is not True or cap.status not in allowed
                or (cap.platform, cap.backend, cap.operation) != (account.channel, account.backend, operation)
                or not 0 <= (utc(now).date() - cap.checked_on).days <= 7):
            if not (trial and cap.authorized is True and
                    (cap.platform,cap.backend,cap.operation)==(account.channel,account.backend,operation)
                    and 0 <= (utc(now).date()-cap.checked_on).days <= 7):
                raise ConditionalConflict('conversation_capability_unverified')
        return account

    def _trial_self_only(self, authority, account, operation, recipient, purpose, text):
        """Session trial is not proof of messaging capability or a broad grant."""
        cap = self.capabilities.get(owner_ref=authority.owner_ref,platform=account.channel,
            backend=account.backend,operation=operation,session_ref=account.session_ref)
        if (not self.channels[(account.channel,account.backend)].fixture_only and
                cap is not None and cap.status != 'probado_real' and not (
                self.allow_self_test and recipient == account.self_recipient_ref and
                purpose == SELF_TEST_PURPOSE and text == SELF_TEST_TEXT)):
            raise ConversationError('trial_self_test_only')

    def register_account(self, *, authority, account, now):
        now = self._now(now)
        with self.store.transaction():
            self._authority(authority, now)
            if (not isinstance(account, Account) or not ref(account.account_ref) or not ref(account.self_recipient_ref)
                    or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}:[a-z0-9][a-z0-9-]{0,62}', account.session_ref)
                    or type(account.session_version) is not int or account.session_version < 1
                    or account.expires_at <= utc(now) or (account.session_ref, account.session_version) not in authority.sessions):
                raise ConversationError('invalid_account')
            if (account.channel, account.backend) not in self.channels:
                raise ConversationError('channel_not_registered')
            mapping = self.store.db.execute('SELECT account FROM conversation_session_accounts WHERE owner=? AND session=?',
                                            (authority.owner_ref, account.session_ref)).fetchone()
            if mapping is not None and mapping != (account.account_ref,):
                raise ConditionalConflict('session_account_binding')
            self.store.db.execute('INSERT OR IGNORE INTO conversation_session_accounts VALUES(?,?,?)',
                                  (authority.owner_ref, account.session_ref, account.account_ref))
            document = asdict(account)
            document['expires_at'] = stamp(account.expires_at)
            self.store.db.execute('INSERT OR REPLACE INTO conversation_accounts VALUES(?,?,?)',
                                  (authority.owner_ref, account.account_ref, canonical(document)))

    def _pointer(self, pointer):
        if (not isinstance(pointer, BlobPointer) or pointer.recipient_scope != self.private_scope
                or not isinstance(pointer.blob_key, str) or len(pointer.blob_key) > 256
                or not re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*(?:/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)*', pointer.blob_key)
                or not isinstance(pointer.sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', pointer.sha256)):
            raise ConversationError('invalid_private_reference')
        return asdict(pointer)

    def _open(self, owner, pointer):
        try:
            self._pointer(pointer)
            data = self.vault.open(owner_ref=owner, pointer=pointer)
            if not isinstance(data, bytes) or len(data) > 131_072:
                raise ValueError
            return json.loads(data)
        except Exception:
            raise ConversationError('private_access_failed') from None

    def _seal(self, owner, body):
        try:
            value = canonical(body).encode()
            if len(value) > 131_072:
                raise ValueError
            pointer = self.vault.seal(owner_ref=owner, plaintext=value)
            self._pointer(pointer)
            # Bind the immutable encrypted writer's round-trip to this owner.
            if self._open(owner, pointer) != body:
                raise ValueError
            return pointer
        except Exception:
            raise ConversationError('private_write_failed') from None

    def enable_chat(self, *, authority, account_ref, chat_ref, recipient_ref, identity_ref, now, enabled=True):
        with self.store.transaction():
            self._check(authority, account_ref, 'read', now)
            if not ref(chat_ref) or not ref(recipient_ref) or type(enabled) is not bool:
                raise ConversationError('invalid_chat')
            self._identity(authority.owner_ref, account_ref, chat_ref, recipient_ref, identity_ref)
            self.store.db.execute('INSERT OR REPLACE INTO conversation_chats VALUES(?,?,?,?,?,?)',
                                  (authority.owner_ref, account_ref, chat_ref, recipient_ref,
                                   canonical(self._pointer(identity_ref)), int(enabled)))

    def _identity(self, owner, account, chat, recipient, pointer):
        body = self._open(owner, pointer)
        if (set(body) != {'account_ref', 'chat_ref', 'recipient_ref', 'display', 'address'}
                or (body['account_ref'], body['chat_ref'], body['recipient_ref']) != (account, chat, recipient)
                or any(not isinstance(body[k], str) or not body[k] or len(body[k].encode()) > 512 for k in ('display', 'address'))):
            raise ConversationError('identity_binding')
        return body

    def _chat(self, owner, account, chat):
        row = self.store.db.execute('SELECT recipient,identity FROM conversation_chats WHERE owner=? AND account=? AND chat=? AND enabled=1',
                                    (owner, account, chat)).fetchone()
        if not row:
            raise ConversationError('chat_not_enabled')
        pointer = BlobPointer(**json.loads(row[1]))
        return row[0], pointer, self._identity(owner, account, chat, row[0], pointer)

    def list_chats(self, *, authority, account_ref, now, limit=20, after_ref=None):
        if type(limit) is not int or not 1 <= limit <= 100 or (after_ref is not None and not ref(after_ref)):
            raise ConversationError('invalid_limit_or_cursor')
        with self.store.transaction():
            self._check(authority, account_ref, 'read', now)
            rows = self.store.db.execute('SELECT chat,recipient,identity FROM conversation_chats WHERE owner=? AND account=? AND enabled=1 AND chat>? ORDER BY chat LIMIT ?',
                                        (authority.owner_ref, account_ref, after_ref or '', limit + 1)).fetchall()
            page = tuple({'chat_ref': r[0], 'recipient_ref': r[1], 'identity_ref': json.loads(r[2])} for r in rows[:limit])
            return page, rows[limit-1][0] if len(rows) > limit else None

    def sync(self, *, authority, account_ref, now, worker, cursor_ref=None, limit=20):
        now = self._now(now)
        if type(limit) is not int or not 1 <= limit <= 100 or authority.ceiling.calls < 1:
            raise ConversationError('sync_budget_exhausted')
        with self.store.transaction():
            account = self._check(authority, account_ref, 'read', now)
            channel = self.channels[(account.channel, account.backend)]
            if not self.synthetic_authorized or not channel.fixture_only or worker.fixture_only is not True:
                raise ConversationError('real_sync_disabled')
            chats = tuple(r['chat_ref'] for r in self.list_chats_unlocked(authority.owner_ref, account_ref))
            if cursor_ref is not None:
                self._sync_cursor(authority.owner_ref, account, cursor_ref)
            day = utc(now).date().isoformat()
            used = self.store.db.execute('SELECT COALESCE(SUM(calls),0) FROM conversation_reads WHERE owner=? AND day=?',
                                         (authority.owner_ref, day)).fetchone()[0]
            if used >= min(100, authority.ceiling.calls):
                raise ConversationError('sync_call_budget_exhausted')
            self.store.db.execute('INSERT INTO conversation_reads VALUES(?,?,?,1) ON CONFLICT(owner,account,day) DO UPDATE SET calls=calls+1',
                                  (authority.owner_ref, account_ref, day))
        try:
            page = worker.sync(owner_ref=authority.owner_ref, account=account, enabled_chat_refs=chats,
                               cursor_ref=cursor_ref, limit=limit)
        except Exception:
            raise ConversationError('inbox_failed') from None
        if not isinstance(page, IncomingPage) or not isinstance(page.messages, tuple) or len(page.messages) > limit:
            raise ConversationError('invalid_inbox_page')
        kept = []
        with self.store.transaction():
            current = self._check(authority, account_ref, 'read', now)
            if current != account:
                raise ConditionalConflict('sync_account_changed')
            for message in page.messages:
                if not isinstance(message, Incoming) or not ref(message.chat_ref):
                    raise ConversationError('invalid_incoming_reference')
                enabled = self.store.db.execute('SELECT recipient FROM conversation_chats WHERE owner=? AND account=? AND chat=? AND enabled=1',
                                               (authority.owner_ref, account_ref, message.chat_ref)).fetchone()
                if not enabled:
                    continue  # Do not decrypt/persist disabled chat data.
                if enabled != (message.recipient_ref,) or not ref(message.provider_message_ref):
                    raise ConversationError('incoming_binding')
                body = self._open(authority.owner_ref, message.private_ref)
                if (body.get('chat_ref'), body.get('recipient_ref'), body.get('provider_message_ref')) != (
                        message.chat_ref, message.recipient_ref, message.provider_message_ref):
                    raise ConversationError('incoming_private_binding')
                self._check(authority, account_ref, 'read', self._now(now))
                pointer = canonical(self._pointer(message.private_ref))
                previous = self.store.db.execute('SELECT chat,pointer FROM conversation_messages WHERE owner=? AND account=? AND message=?',
                                                (authority.owner_ref, account_ref, message.provider_message_ref)).fetchone()
                if previous:
                    existing = BlobPointer(**json.loads(previous[1]))
                    if previous[0] != message.chat_ref or self._open(authority.owner_ref, existing) != body:
                        raise ConditionalConflict('incoming_replay_mismatch')
                    # Legitimate re-encryption may change ciphertext/ref, not the
                    # stable provider message. Preserve original private pointer.
                    message = replace(message, private_ref=existing)
                self.store.db.execute('INSERT OR IGNORE INTO conversation_messages VALUES(?,?,?,?,?)',
                                      (authority.owner_ref, account_ref, message.chat_ref, message.provider_message_ref, pointer))
                kept.append(message)
            if page.next_ref is not None:
                self._sync_cursor(authority.owner_ref, account, page.next_ref)
        return IncomingPage(tuple(kept), page.next_ref)

    def _sync_cursor(self, owner, account, pointer):
        body = self._open(owner, pointer)
        if (body.get('owner_ref'), body.get('account_ref'), body.get('channel'),
                body.get('backend'), body.get('session_ref'), body.get('session_version')) != (
                owner, account.account_ref, account.channel, account.backend,
                account.session_ref, account.session_version):
            raise ConversationError('sync_cursor_binding')

    def list_messages(self, *, authority, account_ref, chat_ref, now, limit=20, after_ref=None):
        if type(limit) is not int or not 1 <= limit <= 100 or (after_ref is not None and not ref(after_ref)):
            raise ConversationError('invalid_limit_or_cursor')
        with self.store.transaction():
            self._check(authority, account_ref, 'read', self._now(now))
            recipient, _, _ = self._chat(authority.owner_ref, account_ref, chat_ref)
            rows = self.store.db.execute('SELECT message,pointer FROM conversation_messages WHERE owner=? AND account=? AND chat=? AND message>? ORDER BY message LIMIT ?',
                                        (authority.owner_ref, account_ref, chat_ref, after_ref or '', limit+1)).fetchall()
            page = tuple(Incoming(chat_ref, recipient, r[0], BlobPointer(**json.loads(r[1]))) for r in rows[:limit])
            return page, rows[limit-1][0] if len(rows) > limit else None

    def list_chats_unlocked(self, owner, account):
        # Used only under an A3 transaction. Explicit bounded sync allowlist.
        rows = self.store.db.execute('SELECT chat FROM conversation_chats WHERE owner=? AND account=? AND enabled=1 ORDER BY chat LIMIT 101',
                                    (owner, account)).fetchall()
        if len(rows) > 100:
            raise ConversationError('sync_chat_budget_exhausted')
        return tuple({'chat_ref': r[0]} for r in rows)

    def _draft_body(self, authority, account, drafts):
        validate_drafts(drafts)
        rows, identities = [], set()
        for draft in drafts:
            recipient, identity_ref, identity = self._chat(authority.owner_ref, account.account_ref, draft.chat_refs[0])
            self._trial_self_only(authority,account,'compose',recipient,draft.purpose_ref,draft.text)
            if recipient == account.self_recipient_ref and not (self.allow_self_test and
                    draft.purpose_ref == SELF_TEST_PURPOSE and draft.text == SELF_TEST_TEXT):
                raise ConversationError('self_contact_forbidden')
            key = (recipient, draft.purpose_ref)
            if key in identities:
                raise ConversationError('duplicate_recipient_purpose')
            identities.add(key)
            rows.append({'operation_id': str(uuid4()), 'chat_ref': draft.chat_refs[0], 'recipient_ref': recipient,
                         'text': draft.text, 'purpose_ref': draft.purpose_ref,
                         'identity_ref': self._pointer(identity_ref), 'identity': identity})
        return {'owner_ref': authority.owner_ref, 'actor_ref': authority.actor_ref,
                'account_ref': account.account_ref, 'channel': account.channel, 'backend': account.backend,
                'session_ref': account.session_ref, 'session_version': account.session_version, 'rows': rows}

    def _batch(self, owner, batch_ref):
        row = self.store.db.execute('SELECT batch,owner,actor,account,version,status,hash,pointer,operations,expires FROM conversation_batches WHERE owner=? AND batch=?',
                                    (owner, batch_ref)).fetchone()
        if row is None:
            raise ConditionalConflict('unknown_batch')
        return Batch(*row[:7], BlobPointer(**json.loads(row[7])), tuple(json.loads(row[8])), parse(row[9]))

    def _bound(self, authority, batch, now, operation='compose'):
        now = self._now(now)
        account = self._check(authority, batch.account_ref, operation, now)
        if batch.actor_ref != authority.actor_ref or batch.expires_at <= utc(now):
            raise ConditionalConflict('batch_actor_or_expiry')
        body = self._open(authority.owner_ref, batch.private_ref)
        if (fingerprint(body) != batch.content_hash or body['owner_ref'] != authority.owner_ref
                or body['actor_ref'] != authority.actor_ref or body['account_ref'] != account.account_ref
                or (body['channel'], body['backend'], body['session_ref'], body['session_version']) !=
                   (account.channel, account.backend, account.session_ref, account.session_version)
                or tuple(r['operation_id'] for r in body['rows']) != batch.operation_ids):
            raise ConditionalConflict('batch_binding')
        for row in body['rows']:
            recipient, identity_ref, identity = self._chat(authority.owner_ref, account.account_ref, row['chat_ref'])
            self._trial_self_only(authority,account,operation,recipient,row['purpose_ref'],row['text'])
            self_denied = recipient == account.self_recipient_ref and not (self.allow_self_test and
                row['purpose_ref'] == SELF_TEST_PURPOSE and row['text'] == SELF_TEST_TEXT)
            if (self_denied or recipient != row['recipient_ref']
                    or self._pointer(identity_ref) != row['identity_ref'] or identity != row['identity']):
                raise ConditionalConflict('recipient_binding_changed')
        return account, body

    def _install(self, authority, batch_ref, account, body, expires_at, *, version=0):
        owner = authority.owner_ref
        if self.store.db.execute('SELECT COUNT(*) FROM conversation_batches WHERE owner=?', (owner,)).fetchone()[0] >= 1000 and version == 0:
            raise ConversationError('stored_batch_budget_exhausted')
        if self.store.db.execute('SELECT COUNT(*) FROM conversation_batches WHERE owner=? AND status=\'proposed\'', (owner,)).fetchone()[0] >= 20 and version == 0:
            raise ConversationError('pending_batch_budget_exhausted')
        if len(body['rows']) > authority.ceiling.messages:
            raise ConversationError('message_budget_exhausted')
        for row in body['rows']:
            old = self.store.db.execute('SELECT op FROM conversation_dedupe WHERE owner=? AND account=? AND recipient=? AND purpose=?',
                                        (owner, account.account_ref, row['recipient_ref'], row['purpose_ref'])).fetchone()
            if old:
                raise ConversationError('duplicate_recipient_purpose')
        pointer = self._seal(owner, body)
        batch_hash = fingerprint(body)
        ids = tuple(r['operation_id'] for r in body['rows'])
        self.store.db.execute('INSERT OR REPLACE INTO conversation_batches VALUES(?,?,?,?,?,?,?,?,?,?)',
                              (owner, authority.actor_ref, batch_ref, account.account_ref, version, 'proposed',
                               canonical(self._pointer(pointer)), batch_hash, canonical(ids), stamp(expires_at)))
        for row in body['rows']:
            # Existing B/Go contract hashes UTF-8 text, not the private manifest.
            # Batch/screen hash separately binds account/channel/identity/purpose.
            content_hash = sha256(row['text'].encode('utf-8')).hexdigest()
            record = LedgerRecord(row['operation_id'], LedgerState.PROPOSED, 0, content_hash,
                                  row['recipient_ref'], row['purpose_ref'], account.session_ref, account.session_version)
            self.store._write_ledger(owner, record)
            self.store.db.execute('INSERT INTO conversation_actions VALUES(?,?,?,?,?,?,?,?)',
                                  (owner, row['operation_id'], batch_ref, account.account_ref, row['chat_ref'],
                                   row['recipient_ref'], row['purpose_ref'], content_hash))
            self.store.db.execute('INSERT INTO conversation_dedupe VALUES(?,?,?,?,?)',
                                  (owner, account.account_ref, row['recipient_ref'], row['purpose_ref'], row['operation_id']))
        return self._batch(owner, batch_ref)

    def compose(self, *, authority, event_ref, account_ref, drafts, now, expires_at):
        now = self._now(now)
        with self.store.transaction():
            account = self._check(authority, account_ref, 'compose', now)
            if not ref(event_ref) or not utc(now) < utc(expires_at) <= utc(authority.expires_at):
                raise ConversationError('invalid_event_or_expiry')
            body = self._draft_body(authority, account, drafts)
            stable = {**body, 'rows': [{k: v for k, v in r.items() if k != 'operation_id'} for r in body['rows']]}
            event_hash = fingerprint(stable)
            previous = self.store.db.execute('SELECT hash,batch FROM conversation_events WHERE owner=? AND actor=? AND event=?',
                                             (authority.owner_ref, authority.actor_ref, event_ref)).fetchone()
            if previous:
                if previous[0] != event_hash:
                    raise ConditionalConflict('compose_replay_mismatch')
                batch = self._batch(authority.owner_ref, previous[1])
                self._bound(authority, batch, now)
                return replace(batch, replayed=True)
            batch_ref = 'batch:' + uuid4().hex
            batch = self._install(authority, batch_ref, account, body, expires_at)
            self.store.db.execute('INSERT INTO conversation_events VALUES(?,?,?,?,?)',
                                  (authority.owner_ref, authority.actor_ref, event_ref, event_hash, batch_ref))
            return batch

    def screen(self, *, authority, batch_ref, now):
        now = self._now(now)
        with self.store.transaction():
            batch = self._batch(authority.owner_ref, batch_ref)
            account, body = self._bound(authority, batch, now)
            if batch.status != 'proposed':
                raise ConversationError('batch_not_proposed')
            token = 'approval:' + uuid4().hex
            rows = tuple({**row, 'account_ref': account.account_ref, 'channel': account.channel,
                          'backend': account.backend, 'session_ref': account.session_ref,
                          'session_version': account.session_version} for row in body['rows'])
            self.store.db.execute('INSERT INTO conversation_displays VALUES(?,?,?,?,?,?,?,?,0)',
                                  (authority.owner_ref, authority.actor_ref, token, batch_ref, batch.version,
                                   batch.content_hash, canonical(batch.operation_ids), fingerprint(rows)))
            return ApprovalScreen(batch_ref, batch.version, batch.content_hash, token, rows)

    def approve(self, *, authority, event_ref, callback_ref, content_hash, operation_ids, now, display_hash=None):
        now = self._now(now)
        with self.store.transaction():
            self._authority(authority, now)  # Never return a cached result before current permission.
            if not ref(event_ref) or not ref(callback_ref):
                raise ConversationError('invalid_approval_reference')
            display = self.store.db.execute('SELECT batch,version,hash,operations,used,display_hash FROM conversation_displays WHERE owner=? AND actor=? AND token=?',
                                            (authority.owner_ref, authority.actor_ref, callback_ref)).fetchone()
            if display is None:
                raise ConditionalConflict('unknown_approval')
            batch = self._batch(authority.owner_ref, display[0])
            account, body = self._bound(authority, batch, now, 'contact')
            if (content_hash != display[2] or tuple(operation_ids) != tuple(json.loads(display[3]))
                    or batch.content_hash != content_hash or display_hash != display[5]):
                raise ConditionalConflict('approval_not_exact')
            receipt = self.store.db.execute('SELECT token,batch FROM conversation_approvals WHERE owner=? AND actor=? AND event=?',
                                            (authority.owner_ref, authority.actor_ref, event_ref)).fetchone()
            if receipt:
                if receipt != (callback_ref, batch.batch_ref):
                    raise ConditionalConflict('approval_receipt_mismatch')
                return replace(batch, replayed=True)
            if display[4] or batch.status != 'proposed' or batch.version != display[1]:
                raise ConditionalConflict('approval_stale_or_used')
            usage = self.store.db.execute('SELECT reserved,next_allowed FROM conversation_usage WHERE owner=? AND account=? AND day=?',
                                          (authority.owner_ref, account.account_ref, utc(now).date().isoformat())).fetchone()
            reserved = usage[0] if usage else 0
            owner_reserved = self.store.db.execute('SELECT COALESCE(SUM(reserved),0) FROM conversation_usage WHERE owner=? AND day=?',
                                                   (authority.owner_ref, utc(now).date().isoformat())).fetchone()[0]
            if owner_reserved + len(body['rows']) > min(self.daily_messages, authority.ceiling.messages):
                raise ConversationError('daily_message_budget_exhausted')
            self.store.db.execute('INSERT OR REPLACE INTO conversation_usage VALUES(?,?,?,?,?)',
                                  (authority.owner_ref, account.account_ref, utc(now).date().isoformat(),
                                   reserved + len(body['rows']), usage[1] if usage else stamp(now)))
            for row in body['rows']:
                record = self.store.get(owner_ref=authority.owner_ref, operation_id=row['operation_id'])
                approval = Approval(authority.actor_ref, row['recipient_ref'], account.session_ref,
                                    account.session_version, record.content_hash, row['purpose_ref'], batch.expires_at)
                self.store._approved(authority.owner_ref, row['operation_id'], record.version, approval, utc(now))
                entry = {'kind': 'local.conversation_intent', 'owner_ref': authority.owner_ref,
                         'operation_id': row['operation_id'], 'batch_ref': batch.batch_ref,
                         'content_hash': record.content_hash, 'private_ref': self._pointer(batch.private_ref)}
                self.store.db.execute('INSERT INTO conversation_outbox VALUES(?,?,?,?)',
                                      (authority.owner_ref, row['operation_id'], canonical(entry), 'pending'))
            self.store.db.execute('UPDATE conversation_batches SET status=\'approved\',version=version+1 WHERE owner=? AND batch=?',
                                  (authority.owner_ref, batch.batch_ref))
            self.store.db.execute('UPDATE conversation_displays SET used=1 WHERE owner=? AND batch=?',
                                  (authority.owner_ref, batch.batch_ref))
            self.store.db.execute('INSERT INTO conversation_approvals VALUES(?,?,?,?,?)',
                                  (authority.owner_ref, authority.actor_ref, event_ref, callback_ref, batch.batch_ref))
            return self._batch(authority.owner_ref, batch.batch_ref)

    def correct(self, *, authority, batch_ref, expected_version, drafts, now):
        now = self._now(now)
        with self.store.transaction():
            batch = self._batch(authority.owner_ref, batch_ref)
            account, old = self._bound(authority, batch, now)
            if batch.version != expected_version or batch.status not in ('proposed', 'approved'):
                raise ConditionalConflict('correction_version_or_state')
            if any(self.store.get(owner_ref=authority.owner_ref, operation_id=op).state not in (LedgerState.PROPOSED, LedgerState.APPROVED)
                   for op in batch.operation_ids):
                raise ConditionalConflict('correction_dispatch_started')
            body = self._draft_body(authority, account, drafts)
            for op in batch.operation_ids:
                self.store.db.execute('INSERT OR IGNORE INTO cancelled_actions VALUES(?,?)', (authority.owner_ref, op))
                self.store.db.execute('DELETE FROM conversation_dedupe WHERE owner=? AND op=?', (authority.owner_ref, op))
                self.store.db.execute('UPDATE conversation_outbox SET status=\'cancelled\' WHERE owner=? AND op=?', (authority.owner_ref, op))
            self.store.db.execute('UPDATE conversation_displays SET used=1 WHERE owner=? AND batch=?', (authority.owner_ref, batch_ref))
            return self._install(authority, batch_ref, account, body, batch.expires_at, version=batch.version+1)

    def _wire_approval(self, authority, operation_id, now, approval_ref=None):
        self._authority(authority, now)
        action = self.store.db.execute('SELECT batch,account FROM conversation_actions WHERE owner=? AND op=?',
                                       (authority.owner_ref, operation_id)).fetchone()
        if action is None:
            raise ConditionalConflict('unknown_action')
        batch = self._batch(authority.owner_ref, action[0])
        account, body = self._bound(authority, batch, now, 'contact')
        record = self.store.get(owner_ref=authority.owner_ref, operation_id=operation_id)
        item = next((row for row in body['rows'] if row['operation_id'] == operation_id), None)
        if item is None:
            raise ConditionalConflict('worker_approval_binding')
        approved = self.store.db.execute('SELECT token FROM conversation_approvals WHERE owner=? AND actor=? AND batch=? ORDER BY rowid DESC LIMIT 1',
                                         (authority.owner_ref, authority.actor_ref, batch.batch_ref)).fetchone()
        if (batch.status != 'approved' or record.state != LedgerState.APPROVED or not approved
                or (approval_ref is not None and approval_ref != approved[0])
                or (record.recipient_ref, record.purpose, record.session_ref, record.session_version, record.content_hash)
                != (item['recipient_ref'], item['purpose_ref'], account.session_ref, account.session_version,
                    sha256(item['text'].encode('utf-8')).hexdigest())):
            raise ConditionalConflict('worker_approval_binding')
        approval = Approval(authority.actor_ref, record.recipient_ref, account.session_ref,
                            account.session_version, record.content_hash, record.purpose, batch.expires_at)
        if record.approval != approval:
            raise ConditionalConflict('worker_approval_binding')
        return batch, account, item, record, approval, approved[0]

    def worker_approval(self, *, authority, operation_id, approval_ref, now):
        """Resolve a real local callback to fresh ledger/Approval; no worker side effects.

        Host bridge must additionally enforce its worker identity and lease/claim.
        This is NOT an implementation of the Go ledger or A3 wire ports.
        """
        with self.store.transaction():
            _, _, _, record, approval, _ = self._wire_approval(authority, operation_id, self._now(now), approval_ref)
            return record, approval

    def prepare_whatsapp(self, *, authority, operation_id, now, worker_vault):
        """Persist an immutable offline B envelope projection, never queue/send it.

        worker_vault is explicitly injected and must seal owner-bound blobs for
        worker:whatsapp. Production requires age implementation and bridge review.
        """
        now = self._now(now)
        with self.store.transaction():
            batch, account, item, record, _, approval_ref = self._wire_approval(authority, operation_id, now)
            if account.channel != 'whatsapp':
                raise ConversationError('wire_channel_unsupported')
            previous = self.store.db.execute('SELECT doc FROM conversation_wire_preparations WHERE owner=? AND op=?',
                                             (authority.owner_ref, operation_id)).fetchone()
            if previous:
                envelope = json.loads(previous[0])
                if (parse(envelope['deadline']) <= now or envelope['payload']['approval_ref'] != approval_ref):
                    raise ConditionalConflict('wire_preparation_expired_or_changed')
                return envelope
            body = {'schema_version': 1, 'text': item['text']}
            try:
                validate('whatsapp.send.private.v1', body)
                pointer = worker_vault.seal(owner_ref=authority.owner_ref, plaintext=canonical(body).encode('utf-8'))
                if not isinstance(pointer, BlobPointer) or pointer.recipient_scope != 'worker:whatsapp':
                    raise ValueError
                if worker_vault.open(owner_ref=authority.owner_ref, pointer=pointer) != canonical(body).encode('utf-8'):
                    raise ValueError
                envelope = {
                    'schema_version': 2, 'message_id': str(uuid4()), 'operation_id': operation_id,
                    'correlation_id': str(UUID(batch.batch_ref.split(':', 1)[1])),
                    'owner_ref': authority.owner_ref, 'kind': 'whatsapp.send',
                    'deadline': stamp(min(batch.expires_at, account.expires_at, authority.expires_at)),
                    'attempt': 1, 'session_ref': account.session_ref, 'expected_version': account.session_version,
                    'payload': {'schema_version': 1, 'recipient_ref': record.recipient_ref,
                                'approval_ref': approval_ref, 'content_sha256': record.content_hash,
                                'private_ref': asdict(pointer)},
                }
                validate('envelope.v2', envelope)
            except Exception:
                raise ConversationError('worker_private_projection_failed') from None
            # Recheck after the injected writer; it cannot supply approval authority.
            self._wire_approval(authority, operation_id, self._now(now), approval_ref)
            self.store.db.execute('INSERT INTO conversation_wire_preparations VALUES(?,?,?)',
                                  (authority.owner_ref, operation_id, canonical(envelope)))
            return envelope

    def dispatch_fixture(self, *, authority, operation_id, now, dispatcher):
        now = self._now(now)
        # No generic send entrypoint: production is blocked until B/Go wire/replay
        # review, vault, session identity and provider isolation are proven.
        if not self.synthetic_authorized or dispatcher.fixture_only is not True:
            raise ConversationError('real_dispatch_disabled')
        with self.store.transaction():
            self._authority(authority, now)
            row = self.store.db.execute('SELECT batch,account FROM conversation_actions WHERE owner=? AND op=?',
                                        (authority.owner_ref, operation_id)).fetchone()
            if row is None:
                raise ConditionalConflict('unknown_action')
            batch = self._batch(authority.owner_ref, row[0])
            account, body = self._bound(authority, batch, now, 'contact')
            if not self.channels[(account.channel, account.backend)].fixture_only:
                raise ConversationError('real_dispatch_disabled')
            record = self.store.get(owner_ref=authority.owner_ref, operation_id=operation_id)
            if record.state in (LedgerState.DISPATCH_COMMITTED, LedgerState.SEND_UNCERTAIN, LedgerState.PROVIDER_CONFIRMED):
                return DispatchResult(operation_id, record.state, True)
            if record.state != LedgerState.APPROVED or batch.status != 'approved':
                raise ConditionalConflict('action_not_approved')
            # Day rollover does not bypass the last account-wide pace reservation.
            latest = self.store.db.execute('SELECT next_allowed FROM conversation_usage WHERE owner=? AND account=? ORDER BY next_allowed DESC LIMIT 1',
                                           (authority.owner_ref, account.account_ref)).fetchone()
            if latest and parse(latest[0]) > utc(now):
                raise ConversationError('rate_limited')
            self.store.db.execute('UPDATE conversation_usage SET next_allowed=? WHERE owner=? AND account=?',
                                  (stamp(utc(now)+timedelta(seconds=self.min_interval_seconds)), authority.owner_ref, account.account_ref))
        # Pace reservation can survive a pre-claim failure; conservative, no refund.
        lease = self.store.acquire(owner_ref=authority.owner_ref, session_ref=account.session_ref,
                                   worker_ref='worker:conversation-fixture', now=utc(now), ttl=timedelta(seconds=30))
        if lease is None:
            raise ConversationError('session_conflict')
        try:
            # Serialize local permission/chat checks with the existing A3 claim.
            # This does not fence a real provider; only fixtures are executable.
            with self.store.db.lock:
                claim_now = self._now(now)
                self._bound(authority, self._batch(authority.owner_ref, batch.batch_ref), claim_now, 'contact')
                claimed = self.store.claim_dispatch(owner_ref=authority.owner_ref, operation_id=operation_id,
                                                    expected_version=record.version, lease=lease,
                                                    provider_message_ref='provider:' + uuid4().hex, now=claim_now)
            try:
                with self.store.transaction():
                    current_now = self._now(now)
                    self._bound(authority, self._batch(authority.owner_ref, batch.batch_ref), current_now, 'contact')
                    if not self.store.is_current(owner_ref=authority.owner_ref, lease=lease, now=current_now):
                        raise ConditionalConflict('lease_changed')
                item = next(r for r in body['rows'] if r['operation_id'] == operation_id)
                delivery = Delivery(operation_id, authority.owner_ref, account, item['chat_ref'], item['recipient_ref'],
                                    item['purpose_ref'], claimed.content_hash, item['text'], item['identity'], claimed.provider_message_ref)
                confirmed = dispatcher.send_fixture(delivery) is True
            except Exception:
                confirmed = False
            target = LedgerState.PROVIDER_CONFIRMED if confirmed else LedgerState.SEND_UNCERTAIN
            result = self.store.finish_local(owner_ref=authority.owner_ref, operation_id=operation_id,
                                             expected_version=claimed.version, target=target,
                                             provider_message_ref=claimed.provider_message_ref, now=utc(now))
            self.store.db.execute('UPDATE conversation_outbox SET status=? WHERE owner=? AND op=?',
                                  (target.value, authority.owner_ref, operation_id))
            return DispatchResult(operation_id, result.state)
        finally:
            self.store.release(owner_ref=authority.owner_ref, lease=lease)

    def results(self, *, authority, batch_ref, now):
        with self.store.transaction():
            self._authority(authority, self._now(now))
            batch = self._batch(authority.owner_ref, batch_ref)
            self._check(authority, batch.account_ref, 'read', self._now(now))
            if batch.actor_ref != authority.actor_ref:
                raise ConditionalConflict('batch_actor')
            return tuple(DispatchResult(op, self.store.get(owner_ref=authority.owner_ref, operation_id=op).state)
                         for op in batch.operation_ids)

    def reconcile(self, *, authority, operation_id, proof_ref, now):
        if not ref(proof_ref):
            raise ConversationError('invalid_proof_reference')
        with self.store.db.lock:
            self._authority(authority, self._now(now))
            action = self.store.db.execute('SELECT account FROM conversation_actions WHERE owner=? AND op=?',
                                           (authority.owner_ref, operation_id)).fetchone()
            if action is None:
                raise ConditionalConflict('unknown_action')
            self._check(authority, action[0], 'read', self._now(now))
            # Only A3's independently persisted full provider correlation settles
            # uncertainty. A caller's bool/private message text is not proof.
            record = self.store.reconcile_proof(owner_ref=authority.owner_ref, operation_id=operation_id,
                                                proof_ref=proof_ref, now=self._now(now))
            return DispatchResult(operation_id, record.state)
