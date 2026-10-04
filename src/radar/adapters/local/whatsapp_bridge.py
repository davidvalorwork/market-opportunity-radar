"""Private local IPC over the real wameow client, never the ephemeral B runner.

Local A3/A10 authority/approvals remain authoritative. This is deliberately not
an implementation of the published Go Worker wire/blob protocol.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import timedelta
from hashlib import sha256
import json
import os
import re
from queue import Empty, Queue
import subprocess
from threading import Thread
from time import monotonic
from uuid import uuid4

from radar.application.conversations.models import Account, canonical, ref
from radar.domain.core import utc
from radar.ports.types import BlobPointer, ConditionalConflict, LedgerState
from .private_vault import _absolute, _permissions
from .sqlite import parse, stamp


MAX_FRAME = 256 * 1024
DDL = """
CREATE TABLE IF NOT EXISTS whatsapp_bridge_accounts(owner TEXT,account TEXT,actor TEXT,session TEXT,version INTEGER,expires TEXT,paired INTEGER,PRIMARY KEY(owner,account),UNIQUE(owner,session));
CREATE TABLE IF NOT EXISTS whatsapp_bridge_calls(owner TEXT,account TEXT,event TEXT,hash TEXT,method TEXT,state TEXT,pointer TEXT,PRIMARY KEY(owner,account,event));
CREATE TABLE IF NOT EXISTS whatsapp_bridge_sends(owner TEXT,op TEXT,account TEXT,approval TEXT,pointer TEXT,hash TEXT,PRIMARY KEY(owner,op));
CREATE TABLE IF NOT EXISTS whatsapp_bridge_usage(owner TEXT,day TEXT,calls INTEGER,PRIMARY KEY(owner,day));
"""


class BridgeError(ValueError):
    """Static diagnostics only; private protocol responses never enter exceptions."""


@dataclass(frozen=True)
class BridgeResult:
    state: str
    private_ref: BlobPointer | None = None
    replayed: bool = False


class GoBridgeRPC:
    fixture_only = False

    def __init__(self, *, helper_path, helper_sha256):
        self.path = _absolute(helper_path)
        if not self.path.is_file() or sha256(self.path.read_bytes()).hexdigest() != helper_sha256:
            raise BridgeError('helper_integrity')
        self.expected_hash = helper_sha256

    def __repr__(self):
        return 'GoBridgeRPC(<private>)'

    def call(self, *, session_dir, request, gate, authorize_live):
        if sha256(self.path.read_bytes()).hexdigest() != self.expected_hash:
            raise BridgeError('helper_integrity')
        encoded = canonical(request).encode()
        if len(encoded) > MAX_FRAME:
            raise BridgeError('private_frame_limit')
        deadline = monotonic() + max(0, (parse(request['deadline']) - utc_now()).total_seconds())
        command = [str(self.path), '--session-dir', str(session_dir)]
        if authorize_live:
            command.append('--authorize-network')
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, shell=False,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        events = Queue(maxsize=9)

        def read():
            try:
                for _ in range(9):
                    line = process.stdout.readline(MAX_FRAME + 1)
                    if not line or len(line) > MAX_FRAME:
                        events.put(None)
                        return
                    events.put(json.loads(line))
            except Exception:
                events.put(None)

        thread = Thread(target=read, daemon=True)
        thread.start()
        try:
            process.stdin.write(encoded + b'\n')
            process.stdin.flush()
            for _ in range(8):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise BridgeError('helper_timeout')
                try:
                    frame = events.get(timeout=remaining)
                except Empty:
                    raise BridgeError('helper_timeout') from None
                if (not isinstance(frame, dict) or frame.get('v') != 1
                        or frame.get('id') != request['id']
                        or set(frame) - {'v', 'id', 'event', 'body', 'code'}):
                    raise BridgeError('helper_response_unavailable')
                if frame['event'] == 'result':
                    if not isinstance(frame.get('body'), dict):
                        raise BridgeError('helper_response_unavailable')
                    return frame['body']
                if frame['event'] == 'error':
                    raise BridgeError('protocol_operation_uncertain')
                if frame['event'] not in ('preflight', 'effect_gate', 'pair_code'):
                    raise BridgeError('helper_response_unavailable')
                gate(frame['event'], frame.get('body'))
                process.stdin.write(canonical({'v': 1, 'id': request['id'], 'event': 'continue', 'allowed': True}).encode() + b'\n')
                process.stdin.flush()
            raise BridgeError('helper_frame_limit')
        finally:
            process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
            process.wait(timeout=5)
            process.stdout.close()
            thread.join(timeout=1)


def utc_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


class LocalWhatsAppBridge:
    def __init__(self, *, store, conversations, vault, authority_provider, clock,
                 transport, session_root, pair_authorizer=None, authorize_live=False,
                 synthetic_authorized=False, daily_calls=100, timeout_seconds=120):
        if (not callable(authority_provider) or vault is None or conversations.store is not store
                or type(authorize_live) is not bool or type(synthetic_authorized) is not bool
                or type(daily_calls) is not int or not 1 <= daily_calls <= 1000
                or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 150):
            raise BridgeError('bridge_configuration')
        if getattr(transport, 'fixture_only', None) is True:
            if not synthetic_authorized or authorize_live:
                raise BridgeError('explicit_fixture_authorization_required')
        elif getattr(transport, 'fixture_only', None) is not False or synthetic_authorized:
            raise BridgeError('bridge_configuration')
        self.root = _absolute(session_root)
        _permissions(self.root, directory=True)
        self.store, self.conversations, self.vault = store, conversations, vault
        self.authority_provider, self.clock, self.transport = authority_provider, clock, transport
        self.pair_authorizer, self.authorize_live = pair_authorizer, authorize_live
        self.synthetic_authorized, self.daily_calls, self.timeout = synthetic_authorized, daily_calls, timeout_seconds
        self.store.db.executescript(DDL)

    def __repr__(self):
        return 'LocalWhatsAppBridge(<private>)'

    def _fresh(self, authority):
        fresh = self.authority_provider(owner_ref=authority.owner_ref, actor_ref=authority.actor_ref)
        if (fresh.owner_ref, fresh.actor_ref) != (authority.owner_ref, authority.actor_ref):
            raise ConditionalConflict('bridge_authority_binding')
        self.conversations._authority(fresh, utc(self.clock.now()))
        return fresh

    def bind_account(self, *, authority, account):
        """Trusted host registration, not proof of login or capability verification."""
        with self.store.transaction():
            fresh = self._fresh(authority)
            if (not isinstance(account, Account) or account.channel != 'whatsapp'
                    or not ref(account.account_ref) or account.session_version < 1
                    or account.expires_at <= utc(self.clock.now())
                    or (account.session_ref, account.session_version) not in fresh.sessions):
                raise BridgeError('bridge_account_binding')
            expected = (fresh.actor_ref, account.session_ref, account.session_version, stamp(account.expires_at))
            row = self.store.db.execute('SELECT actor,session,version,expires FROM whatsapp_bridge_accounts WHERE owner=? AND account=?', (fresh.owner_ref, account.account_ref)).fetchone()
            if row and row != expected:
                raise ConditionalConflict('bridge_account_binding')
            self.store.db.execute('INSERT OR IGNORE INTO whatsapp_bridge_accounts VALUES(?,?,?,?,?,?,0)',
                                  (fresh.owner_ref, account.account_ref, *expected))

    def _account(self, authority, account_ref, *, paired=True, operation='read'):
        fresh = self._fresh(authority)
        row = self.store.db.execute('SELECT actor,session,version,expires,paired FROM whatsapp_bridge_accounts WHERE owner=? AND account=?', (fresh.owner_ref, account_ref)).fetchone()
        if (not row or row[0] != fresh.actor_ref or parse(row[3]) <= utc(self.clock.now())
                or (row[1], row[2]) not in fresh.sessions
                or self.store.db.execute('SELECT version FROM sessions WHERE owner=? AND ref=?', (fresh.owner_ref, row[1])).fetchone() != (row[2],)):
            raise ConditionalConflict('bridge_account_binding')
        if paired:
            if not row[4]:
                raise BridgeError('account_needs_pairing')
            account = self.conversations._check(fresh, account_ref, operation, self.clock.now())
            if (account.session_ref, account.session_version) != (row[1], row[2]):
                raise ConditionalConflict('bridge_account_binding')
        return fresh, row

    def _reserve(self, owner, ceiling):
        day = utc(self.clock.now()).date().isoformat()
        self.store.db.execute('INSERT OR IGNORE INTO whatsapp_bridge_usage VALUES(?,?,0)', (owner, day))
        if not self.store.db.execute('UPDATE whatsapp_bridge_usage SET calls=calls+1 WHERE owner=? AND day=? AND calls<?', (owner, day, min(self.daily_calls, ceiling))).rowcount:
            raise BridgeError('bridge_call_budget_exhausted')

    def _directory(self, owner, account):
        name = sha256(canonical((owner, account)).encode()).hexdigest()
        directory = self.root / name
        directory.mkdir(mode=0o700, exist_ok=True)
        _permissions(directory, directory=True)
        return directory

    @contextmanager
    def _session(self, authority, account_ref, *, paired=True, operation='read'):
        fresh, row = self._account(authority, account_ref, paired=paired, operation=operation)
        directory = self._directory(fresh.owner_ref, account_ref)
        # OS exclusion remains held until the helper exits; lease expiry cannot let
        # another cooperating host share a writable protocol DB with that process.
        path = directory / '.bridge.lock'
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        locked, lease = False, None
        try:
            _permissions(path)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise BridgeError('bridge_session_busy_or_storage') from None
            locked = True
            lease = self.store.acquire(owner_ref=fresh.owner_ref, session_ref=row[1],
                                       worker_ref='worker:whatsapp-local', now=self.clock.now(),
                                       ttl=timedelta(seconds=self.timeout + 10))
            if lease is None:
                raise ConditionalConflict('bridge_session_busy')
            yield fresh, row, directory, lease
        finally:
            if lease is not None:
                self.store.release(owner_ref=authority.owner_ref, lease=lease)
            if locked:
                if os.name == 'nt':
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _gate(self, authority, account, lease, *, paired=True, operation='read'):
        self._account(authority, account, paired=paired, operation=operation)
        if not self.store.is_current(owner_ref=authority.owner_ref, lease=lease, now=self.clock.now()):
            raise ConditionalConflict('bridge_lease_changed')

    def _request(self, authority, account, row, event, method, body):
        return {'v': 1, 'id': event, 'owner_ref': authority.owner_ref, 'account_ref': account,
                'session_ref': row[1], 'session_version': row[2],
                'deadline': stamp(min(utc(self.clock.now()) + timedelta(seconds=self.timeout), parse(row[3]), utc(authority.expires_at))),
                'method': method, 'body': body}

    def _call(self, *, authority, account, event, method, body, paired=True, notifier=None, pair_ref=None):
        if not self.authorize_live and not self.synthetic_authorized:
            raise BridgeError('live_bridge_disabled')
        if not ref(event):
            raise BridgeError('bridge_event_reference')
        digest = sha256(canonical((method, authority.actor_ref, body)).encode()).hexdigest()
        with self._session(authority, account, paired=paired) as (fresh, row, directory, lease):
            with self.store.transaction():
                self._gate(fresh, account, lease, paired=paired)
                prior = self.store.db.execute('SELECT hash,state,pointer FROM whatsapp_bridge_calls WHERE owner=? AND account=? AND event=?', (fresh.owner_ref, account, event)).fetchone()
                if prior:
                    if prior[0] != digest:
                        raise ConditionalConflict('bridge_event_binding')
                    return BridgeResult(prior[1], BlobPointer(**json.loads(prior[2])) if prior[2] else None, True)
                self._reserve(fresh.owner_ref, fresh.ceiling.calls)
                self.store.db.execute("INSERT INTO whatsapp_bridge_calls VALUES(?,?,?,?,?,'uncertain',NULL)", (fresh.owner_ref, account, event, digest, method))

            def gate(kind, private):
                self._gate(fresh, account, lease, paired=paired)
                if method == 'pair':
                    if self.pair_authorizer is None or self.pair_authorizer(authority=self._fresh(fresh), account_ref=account, event_ref=event, phone_ref=pair_ref) is not True:
                        raise BridgeError('pair_human_confirmation_required')
                if kind == 'pair_code':
                    if not isinstance(private, dict) or set(private) != {'code'} or not isinstance(private['code'], str) or not 1 <= len(private['code']) <= 64:
                        raise BridgeError('pair_code_unavailable')
                    if notifier(owner_ref=fresh.owner_ref, actor_ref=fresh.actor_ref, event_ref=event, code=private['code']) is not True:
                        raise BridgeError('pair_notice_uncertain')
                    self._gate(fresh, account, lease, paired=False)

            # Crash/failpoint leaves an admitted call uncertain; never retries I/O.
            self.store.failpoint('whatsapp_bridge_reserved')
            gate('preflight', None)
            result = self.transport.call(session_dir=directory,
                                         request=self._request(fresh, account, row, event, method, body),
                                         gate=gate, authorize_live=self.authorize_live)
            self.store.failpoint('whatsapp_bridge_after_io')
            result = self._result(method, result, body, fresh, account, row)
            pointer = self.vault.seal(owner_ref=fresh.owner_ref, plaintext=canonical(result).encode())
            projections = self._prepare_projection(fresh, account, result) if method == 'sync' else ()
            with self.store.transaction():
                self._gate(fresh, account, lease, paired=paired)
                if method == 'pair':
                    if result != {'paired': True}:
                        raise BridgeError('pair_result_unavailable')
                    self.store.db.execute('UPDATE whatsapp_bridge_accounts SET paired=1 WHERE owner=? AND account=? AND version=?', (fresh.owner_ref, account, row[2]))
                if method == 'sync':
                    self._project(fresh, account, projections)
                self.store.db.execute("UPDATE whatsapp_bridge_calls SET state='completed',pointer=? WHERE owner=? AND account=? AND event=? AND hash=? AND state='uncertain'", (canonical(asdict(pointer)), fresh.owner_ref, account, event, digest))
            return BridgeResult('completed', pointer)

    def _result(self, method, result, body, authority, account, row):
        if not isinstance(result, dict):
            raise BridgeError('bridge_result_shape')
        if method == 'pair':
            if result != {'paired': True}:
                raise BridgeError('pair_result_unavailable')
            return result
        key = 'chats' if method == 'list_chats' else 'messages'
        if (set(result) != {key, 'has_more'} or type(result['has_more']) is not bool
                or not isinstance(result[key], list) or len(result[key]) > body['limit']):
            raise BridgeError('bridge_result_shape')
        seen, cursor, total = set(), '', 0
        for item in result[key]:
            if not isinstance(item, dict) or not ref(item.get('chat_ref')):
                raise BridgeError('bridge_result_binding')
            if method == 'list_chats':
                if (set(item) - {'chat_ref', 'display_name', 'last_message_at', 'is_self'}
                        or type(item.get('is_self')) is not bool
                        or not isinstance(item.get('display_name', ''), str)
                        or len(item.get('display_name', '').encode()) > 512
                        or item['chat_ref'] in seen):
                    raise BridgeError('bridge_result_shape')
                seen.add(item['chat_ref'])
                if 'last_message_at' in item:
                    try:
                        parse(item['last_message_at'])
                    except (TypeError, ValueError, AttributeError):
                        raise BridgeError('bridge_result_shape') from None
                continue
            if (set(item) != {'chat_ref', 'provider_message_id', 'cursor', 'text', 'observed_at'}
                    or item['chat_ref'] not in body['enabled']
                    or not isinstance(item['provider_message_id'], str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', item['provider_message_id'])
                    or not isinstance(item['cursor'], str) or not re.fullmatch(r'p[1-9][0-9]{0,18}', item['cursor'])
                    or not isinstance(item['text'], str) or not 1 <= len(item['text'].encode()) <= 4096):
                raise BridgeError('bridge_result_binding')
            try:
                parse(item['observed_at'])
            except (TypeError, ValueError, AttributeError):
                raise BridgeError('bridge_result_shape') from None
            if cursor and int(item['cursor'][1:]) <= int(cursor[1:]):
                raise BridgeError('bridge_result_binding')
            cursor = item['cursor']
            identity = (authority.owner_ref, account, item['chat_ref'], item['provider_message_id'])
            message_ref = 'provider:p' + sha256(canonical(identity).encode()).hexdigest()
            if message_ref in seen:
                raise BridgeError('bridge_result_binding')
            seen.add(message_ref)
            item['provider_message_ref'] = message_ref
            total += len(item['text'].encode())
        if total > 65536:
            raise BridgeError('bridge_result_limit')
        if method == 'sync':
            return {**result, 'owner_ref': authority.owner_ref, 'account_ref': account,
                    'session_ref': row[1], 'session_version': row[2], 'cursor': cursor}
        return result

    def _prepare_projection(self, authority, account, result):
        prepared = []
        for item in result['messages']:
            recipient, _, _ = self.conversations._chat(authority.owner_ref, account, item['chat_ref'])
            body = {k: item[k] for k in ('chat_ref', 'provider_message_ref', 'text', 'observed_at')}
            body['recipient_ref'] = recipient
            pointer = self.conversations._seal(authority.owner_ref, body)
            prepared.append((body, pointer))
        return prepared

    def _project(self, authority, account, prepared):
        """Metadata checkpoint is atomic. Vault may retain orphan ciphertext on failure."""
        for body, pointer in prepared:
            item = body
            recipient, _, _ = self.conversations._chat(authority.owner_ref, account, item['chat_ref'])
            if recipient != body['recipient_ref']:
                raise ConditionalConflict('incoming_recipient_changed')
            previous = self.store.db.execute('SELECT chat,pointer FROM conversation_messages WHERE owner=? AND account=? AND message=?', (authority.owner_ref, account, item['provider_message_ref'])).fetchone()
            if previous:
                if previous[0] != item['chat_ref'] or self.conversations._open(authority.owner_ref, BlobPointer(**json.loads(previous[1]))) != body:
                    raise ConditionalConflict('incoming_replay_mismatch')
                continue
            self.store.db.execute('INSERT INTO conversation_messages VALUES(?,?,?,?,?)', (authority.owner_ref, account, item['chat_ref'], item['provider_message_ref'], canonical(asdict(pointer))))

    def pair(self, *, authority, account_ref, event_ref, phone_ref, notifier):
        if not callable(notifier) or self.pair_authorizer is None:
            raise BridgeError('pair_private_notifier_required')
        private = json.loads(self.vault.open(owner_ref=authority.owner_ref, pointer=phone_ref))
        if (not isinstance(private, dict) or set(private) != {'declared_phone'}
                or not isinstance(private['declared_phone'], str)
                or not re.fullmatch(r'\+[1-9][0-9]{6,14}', private['declared_phone'])):
            raise BridgeError('pair_private_shape')
        return self._call(authority=authority, account=account_ref, event=event_ref, method='pair',
                          body={'phone': private['declared_phone']}, paired=False, notifier=notifier, pair_ref=phone_ref)

    def list_chats(self, *, authority, account_ref, event_ref, after_ref='', limit=20):
        if type(limit) is not int or not 1 <= limit <= 100 or (after_ref and not ref(after_ref)):
            raise BridgeError('bridge_page_limit')
        return self._call(authority=authority, account=account_ref, event=event_ref, method='list_chats', body={'after': after_ref, 'limit': limit})

    def sync(self, *, authority, account_ref, event_ref, enabled_chat_refs, limit=20, ack_ref=None):
        if type(limit) is not int or not 1 <= limit <= 100 or not isinstance(enabled_chat_refs, tuple) or not 1 <= len(enabled_chat_refs) <= 100 or len(set(enabled_chat_refs)) != len(enabled_chat_refs):
            raise BridgeError('bridge_page_limit')
        for chat in enabled_chat_refs:
            self.conversations._chat(authority.owner_ref, account_ref, chat)
        ack = ''
        if ack_ref is not None:
            private = json.loads(self.vault.open(owner_ref=authority.owner_ref, pointer=ack_ref))
            row = self._account(authority, account_ref)[1]
            registered = self.store.db.execute("SELECT 1 FROM whatsapp_bridge_calls WHERE owner=? AND account=? AND method='sync' AND state='completed' AND pointer=?", (authority.owner_ref, account_ref, canonical(asdict(ack_ref)))).fetchone()
            if (not registered or (private.get('owner_ref'), private.get('account_ref'), private.get('session_ref'), private.get('session_version')) != (authority.owner_ref, account_ref, row[1], row[2])):
                raise ConditionalConflict('bridge_cursor_binding')
            ack = private['cursor']
        return self._call(authority=authority, account=account_ref, event=event_ref, method='sync', body={'enabled': list(enabled_chat_refs), 'limit': limit, 'ack': ack})

    def send(self, *, authority, operation_id, approval_ref):
        if not self.authorize_live and not self.synthetic_authorized:
            raise BridgeError('live_bridge_disabled')
        fresh = self._fresh(authority)
        record = self.store.get(owner_ref=fresh.owner_ref, operation_id=operation_id)
        if record is None:
            raise ConditionalConflict('unknown_owner_action')
        # Authorization precedes replay; a claimed operation never re-enters IPC.
        if record.state in (LedgerState.DISPATCH_COMMITTED, LedgerState.SEND_UNCERTAIN, LedgerState.PROVIDER_CONFIRMED):
            previous = self.store.db.execute('SELECT account,approval,pointer,hash FROM whatsapp_bridge_sends WHERE owner=? AND op=?', (fresh.owner_ref, operation_id)).fetchone()
            if not previous or approval_ref != previous[1]:
                raise ConditionalConflict('bridge_approval_binding')
            self._account(fresh, previous[0], operation='contact')
            action = self.store.db.execute('SELECT batch FROM conversation_actions WHERE owner=? AND op=?', (fresh.owner_ref, operation_id)).fetchone()
            if not action:
                raise ConditionalConflict('bridge_approval_binding')
            self.conversations._bound(fresh, self.conversations._batch(fresh.owner_ref, action[0]), self.clock.now(), 'contact')
            private = json.loads(self.vault.open(owner_ref=fresh.owner_ref, pointer=BlobPointer(**json.loads(previous[2]))))
            if (sha256(canonical(private).encode()).hexdigest() != previous[3]
                    or sha256(private['text'].encode()).hexdigest() != record.content_hash
                    or record.approval is None or record.approval.expires_at <= self.clock.now()):
                raise ConditionalConflict('bridge_approval_binding')
            if record.state == LedgerState.DISPATCH_COMMITTED:
                return self.store.finish_local(owner_ref=fresh.owner_ref, operation_id=operation_id,
                                               expected_version=record.version, target=LedgerState.SEND_UNCERTAIN,
                                               provider_message_ref=record.provider_message_ref, now=self.clock.now())
            return record
        with self.store.transaction():
            batch, account, item, record, _, token = self.conversations._wire_approval(fresh, operation_id, self.clock.now(), approval_ref)
            body = {'chat_ref': item['chat_ref'], 'text': item['text'], 'content_hash': record.content_hash, 'purpose_ref': record.purpose}
            if sha256(item['text'].encode()).hexdigest() != record.content_hash:
                raise ConditionalConflict('bridge_content_binding')
            latest = self.store.db.execute('SELECT next_allowed FROM conversation_usage WHERE owner=? AND account=? ORDER BY next_allowed DESC LIMIT 1', (fresh.owner_ref, account.account_ref)).fetchone()
            if latest and parse(latest[0]) > self.clock.now():
                raise BridgeError('bridge_rate_limited')
        with self._session(fresh, account.account_ref, operation='contact') as (_, row, directory, lease):
            pointer = self.vault.seal(owner_ref=fresh.owner_ref, plaintext=canonical(body).encode())
            with self.store.transaction():
                self._gate(fresh, account.account_ref, lease, operation='contact')
                self.conversations._wire_approval(self._fresh(fresh), operation_id, self.clock.now(), token)
                self._reserve(fresh.owner_ref, fresh.ceiling.calls)
                self.store.db.execute('UPDATE conversation_usage SET next_allowed=? WHERE owner=? AND account=?', (stamp(self.clock.now() + timedelta(seconds=self.conversations.min_interval_seconds)), fresh.owner_ref, account.account_ref))
                binding = (account.account_ref, token, sha256(canonical(body).encode()).hexdigest())
                previous = self.store.db.execute('SELECT account,approval,hash FROM whatsapp_bridge_sends WHERE owner=? AND op=?', (fresh.owner_ref, operation_id)).fetchone()
                if previous is not None and previous != binding:
                    raise ConditionalConflict('bridge_send_binding')
                self.store.db.execute('INSERT OR IGNORE INTO whatsapp_bridge_sends VALUES(?,?,?,?,?,?)', (fresh.owner_ref, operation_id, account.account_ref, token, canonical(asdict(pointer)), binding[2]))
            # A3 claim owns its transaction. Preparation survives a crash before it;
            # no I/O has happened, so the same exact approval can resume that stage.
            with self.store.db.lock:
                self._gate(fresh, account.account_ref, lease, operation='contact')
                self.conversations._wire_approval(self._fresh(fresh), operation_id, self.clock.now(), token)
                claimed = self.store.claim_dispatch(owner_ref=fresh.owner_ref, operation_id=operation_id,
                                                    expected_version=record.version, lease=lease,
                                                    provider_message_ref='protocol:' + uuid4().hex, now=self.clock.now())
            self.store.failpoint('whatsapp_bridge_after_claim')

            def gate(kind, private):
                if kind not in ('preflight', 'effect_gate') or private is not None:
                    raise BridgeError('send_private_gate_shape')
                with self.store.transaction():
                    self._gate(fresh, account.account_ref, lease, operation='contact')
                    self.conversations._bound(self._fresh(fresh), self.conversations._batch(fresh.owner_ref, batch.batch_ref), self.clock.now(), 'contact')
                    current = self.store.get(owner_ref=fresh.owner_ref, operation_id=operation_id)
                    cancelled = self.store.db.execute('SELECT 1 FROM cancelled_actions WHERE owner=? AND op=?', (fresh.owner_ref, operation_id)).fetchone()
                    if current != claimed or cancelled or claimed.approval.expires_at <= self.clock.now():
                        raise ConditionalConflict('bridge_approved_effect_binding')

            try:
                gate('preflight', None)
                result = self.transport.call(session_dir=directory,
                                             request=self._request(fresh, account.account_ref, row, operation_id, 'send', body),
                                             gate=gate, authorize_live=self.authorize_live)
                if (set(result) != {'state', 'provider_message_id'} or result['state'] != 'provider_confirmed'
                        or not isinstance(result['provider_message_id'], str) or not 1 <= len(result['provider_message_id']) <= 256):
                    raise BridgeError('provider_response_unavailable')
                # Result is an independent helper observation, never caller proof.
                provider = self.vault.seal(owner_ref=fresh.owner_ref, plaintext=canonical(result).encode())
            except (BridgeError, ConditionalConflict):
                return self.store.finish_local(owner_ref=fresh.owner_ref, operation_id=operation_id,
                                               expected_version=claimed.version, target=LedgerState.SEND_UNCERTAIN,
                                               provider_message_ref=claimed.provider_message_ref, now=self.clock.now())
            self.store.failpoint('whatsapp_bridge_after_send')
            with self.store.transaction():
                self._gate(fresh, account.account_ref, lease, operation='contact')
                finished = self.store._finish(fresh.owner_ref, operation_id, claimed.version,
                                              LedgerState.PROVIDER_CONFIRMED, claimed.provider_message_ref)
                self.store.db.execute('UPDATE conversation_outbox SET status=? WHERE owner=? AND op=?', (finished.state.value, fresh.owner_ref, operation_id))
                self.store.db.execute("INSERT INTO whatsapp_bridge_calls VALUES(?,?,?,?,?,'completed',?)", (fresh.owner_ref, account.account_ref, operation_id, claimed.content_hash, 'send', canonical(asdict(provider))))
            return finished


def create_bridge(*, store, conversations_repository, vault_view, authority_provider,
                  clock, helper_path, helper_sha256, session_root, authorize_live=False,
                  pair_authorizer=None, timeout_seconds=120, daily_calls=100):
    """Trusted installed host factory. No synthetic directory or automatic permission."""
    return LocalWhatsAppBridge(store=store, conversations=conversations_repository, vault=vault_view,
                              authority_provider=authority_provider, clock=clock,
                              transport=GoBridgeRPC(helper_path=helper_path, helper_sha256=helper_sha256),
                              session_root=session_root, authorize_live=authorize_live,
                              pair_authorizer=pair_authorizer, timeout_seconds=timeout_seconds,
                              daily_calls=daily_calls)
