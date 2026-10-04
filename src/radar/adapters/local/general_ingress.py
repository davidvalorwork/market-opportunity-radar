"""B webhook authorization followed by an encrypted, local-only input boundary.

No B schema is changed. B envelopes are validated in memory, then their whole
command is encrypted rather than inserted into A3's public commands/outbox.
"""
from contextvars import ContextVar
from dataclasses import asdict
from hashlib import sha256
import json
from uuid import uuid4

from radar.adapters.telegram import consent
from radar.adapters.telegram.webhook import Webhook, COMMAND_TEXT
from radar.application.tasks.models import canonical
from radar.ports.types import BlobPointer, ConditionalConflict
from .sqlite import stamp
from .telegram import Idempotency


INPUT = ContextVar('general_private_input', default=None)
DDL = """
CREATE TABLE IF NOT EXISTS general_inputs(owner TEXT,actor TEXT,ref TEXT,hash TEXT,pointer TEXT,deadline TEXT,state TEXT,task TEXT,reason TEXT,PRIMARY KEY(owner,ref));
CREATE TABLE IF NOT EXISTS general_receipts(key TEXT PRIMARY KEY,owner TEXT,actor TEXT,hash TEXT,input TEXT);
CREATE INDEX IF NOT EXISTS general_inputs_state ON general_inputs(owner,state,ref);
"""


class GeneralIdempotency(Idempotency):
    def seen(self, key):
        return (super().seen(key) or bool(self.store.db.execute('SELECT 1 FROM general_receipts WHERE key=?', (sha256(key.encode()).hexdigest(),)).fetchone()))


class PrivateIngress:
    def __init__(self, store, directory, vault, *, max_pending=50):
        if vault is None or type(max_pending) is not int or not 1 <= max_pending <= 1000:
            raise ValueError('private_ingress_configuration')
        self.store, self.directory, self.vault, self.max_pending = store, directory, vault, max_pending
        store.db.executescript(DDL)

    def commit(self, receipt, command, outbox):
        user = self.directory.by_actor(command['telegram_user_ref'])
        if (user is None or user['owner_ref'] != outbox['owner_ref'] or user['role'] != 'owner'
                or (command['command'] not in consent.UNGATED and not self.directory.accepted(user))):
            raise ConditionalConflict('general_ingress_authority')
        original = INPUT.get()
        private = {**command, 'args': dict(command['args'])}
        if original is not None:
            private['args'] = {'text': original['text'], 'route': original['route']}
        semantic = {key: value for key, value in private.items() if key != 'update_id'}
        digest = sha256(canonical(semantic).encode()).hexdigest()
        owner, actor = user['owner_ref'], user['user_ref']
        keys = tuple(sha256(key.encode()).hexdigest() for key in receipt['idempotency_keys'])
        with self.store.transaction():
            current = self.directory.by_actor(actor)
            if current != user:
                raise ConditionalConflict('general_ingress_authority')
            previous = [self.store.db.execute('SELECT owner,actor,hash,input FROM general_receipts WHERE key=?', (key,)).fetchone() for key in keys]
            existing = [row for row in previous if row]
            if existing:
                if any(row[:3] != (owner, actor, digest) or row[3] != existing[0][3] for row in existing):
                    raise ConditionalConflict('general_ingress_replay')
                input_ref = existing[0][3]
                for key in keys:
                    self.store.db.execute('INSERT OR IGNORE INTO general_receipts VALUES(?,?,?,?,?)', (key, owner, actor, digest, input_ref))
                return False
            if self.store.db.execute("SELECT count(*) FROM general_inputs WHERE owner=? AND state IN ('accepted','interpreting')", (owner,)).fetchone()[0] >= self.max_pending:
                raise ConditionalConflict('general_ingress_quota')
            pointer = self.vault.seal(owner_ref=owner, plaintext=canonical(private).encode())
            if not isinstance(pointer, BlobPointer) or pointer.recipient_scope != 'worker:task-router':
                raise ConditionalConflict('general_private_scope')
            input_ref = 'input:a' + uuid4().hex
            self.store.db.execute('INSERT INTO general_inputs VALUES(?,?,?,?,?,?,\'accepted\',NULL,NULL)',
                                  (owner, actor, input_ref, digest, canonical(asdict(pointer)), outbox['deadline']))
            for key in keys:
                self.store.db.execute('INSERT INTO general_receipts VALUES(?,?,?,?,?)', (key, owner, actor, digest, input_ref))
            self.store.failpoint('general_ingress_committed')
        return True


class GeneralWebhook(Webhook):
    """Normalize authorized private free text/routes without bypassing B gates.

    B handles secret, malformed body, contact, consent, /start, callback dedupe.
    Free text is accepted ONLY from an already authorized/consented owner.
    Original input stays thread-local and is sealed by PrivateIngress.
    """
    def _message(self, update_id, key, message):
        sender, chat = message.get('from', {}), message.get('chat', {})
        text = message.get('text')
        user = self._users.get(sender.get('id')) if isinstance(sender, dict) and type(sender.get('id')) is int else None
        if (isinstance(chat, dict) and type(chat.get('id')) is int and chat.get('type') == 'private' and user
                and user.get('role') == 'owner' and consent.has_consent(user)
                and 'contact' not in message and isinstance(text, str) and text.strip()):
            matched = COMMAND_TEXT.fullmatch(text.strip())
            name, rest = (matched.group(1).lower(), matched.group(2) or '') if matched else (None, text)
            route = 'tasks' if name == 'tareas' else 'request'
            if name in ('pedir', 'buscar', 'tareas') or (name is None and not text.lstrip().startswith('/')):
                if len(text.encode('utf-8')) > 4096:
                    return self._reject(key, {'method': 'sendMessage', 'chat_id': chat['id'], 'text': 'Pedido demasiado largo.'})
                token = INPUT.set({'text': rest, 'route': route})
                try:
                    # Dummy argument has no private data and only exists in RAM.
                    return super()._message(update_id, key, {**message, 'text': '/pedir private-input'})
                finally:
                    INPUT.reset(token)
        return super()._message(update_id, key, message)
