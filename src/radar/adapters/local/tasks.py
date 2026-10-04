"""Owner-scoped local proposal control. No queue, provider I/O or cryptography."""
from dataclasses import replace
from decimal import Decimal
import json
import re
from uuid import uuid4

from radar import contracts
from radar.application.tasks.models import Budget, Proposal, TaskError, canonical, content_hash
from radar.domain.core import utc
from radar.ports.types import BlobPointer, ConditionalConflict, InlineButton
from .sqlite import stamp, parse

DDL = """
CREATE TABLE IF NOT EXISTS task_router_proposals(owner TEXT,actor TEXT,task TEXT,version INTEGER,status TEXT,snapshot TEXT,hash TEXT,expires TEXT,confirmation TEXT,event TEXT,event_hash TEXT,PRIMARY KEY(owner,task),UNIQUE(owner,actor,event));
CREATE TABLE IF NOT EXISTS task_router_callbacks(token TEXT PRIMARY KEY,owner TEXT,actor TEXT,task TEXT,version INTEGER,action TEXT,used INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS task_router_callbacks_version ON task_router_callbacks(owner,task,version);
CREATE TABLE IF NOT EXISTS task_router_callback_receipts(owner TEXT,actor TEXT,event TEXT,token TEXT,result TEXT,PRIMARY KEY(owner,actor,event));
CREATE TABLE IF NOT EXISTS task_router_reservations(owner TEXT,key TEXT,calls INTEGER,messages INTEGER,usd TEXT,hash TEXT,PRIMARY KEY(owner,key));
CREATE TABLE IF NOT EXISTS task_router_parses(owner TEXT,actor TEXT,event TEXT,hash TEXT,result TEXT,PRIMARY KEY(owner,actor,event));
"""


class SQLiteTaskStore:
    def __init__(self, store, *, vault):
        self.store = store
        if vault is None:
            raise TaskError('private_vault_required')
        self.vault = vault
        store.db.executescript(DDL)

    def _seal(self, owner, snapshot):
        try:
            pointer = self.vault.seal(owner_ref=owner, plaintext=snapshot.encode('utf-8'))
            ref = {'blob_key': pointer.blob_key, 'sha256': pointer.sha256,
                   'recipient_scope': pointer.recipient_scope}
            self._reference(ref)
            return canonical({'private_ref': ref, 'document_hash': content_hash(json.loads(snapshot))})
        except Exception:
            raise TaskError('private_vault_failed') from None

    def _open(self, owner, sealed):
        try:
            record = json.loads(sealed)
            ref = record['private_ref']
            self._reference(ref)
            snapshot = self.vault.open(owner_ref=owner, pointer=BlobPointer(**ref)).decode('utf-8')
            if content_hash(json.loads(snapshot)) != record['document_hash']:
                raise ValueError('integrity')
            return snapshot
        except Exception:
            raise TaskError('private_vault_failed') from None

    def _reference(self, ref):
        probe = {'schema_version': 1, 'goal': 'Reference validation', 'privacy_scope': 'personal',
                 'steps': [{'step_id': 'check', 'operation': 'inform', 'arguments': {}}],
                 'missing_fields': [], 'confidence': 1, 'context_refs': [ref]}
        contracts.validate('llm.task_request.v1', probe)
        if ref['recipient_scope'] != 'worker:task-router':
            raise TaskError('invalid_private_reference')

    def _authority(self, authority, now):
        self.store._active(authority.owner_ref)
        if utc(authority.expires_at) <= utc(now) or not self.store.owner_actor(authority.owner_ref, authority.actor_ref) or not self.store.current_consent(authority.owner_ref, authority.actor_ref):
            raise ConditionalConflict('task_authority')

    def _event(self, event_ref):
        if not isinstance(event_ref, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,256}', event_ref):
            raise TaskError('invalid_event_reference')

    def _private(self, owner, refs):
        for ref in refs:
            # References are validated separately even before the parser runs.
            try:
                self._reference(ref)
            except Exception:
                raise TaskError('invalid_private_reference') from None
            row = self.store.db.execute('SELECT hash FROM blobs WHERE owner=? AND key=?', (owner, ref['blob_key'])).fetchone()
            if row is None or row[0] != ref['sha256'] or ref['recipient_scope'] != 'worker:task-router':
                raise ConditionalConflict('private_reference_scope')

    def _plan(self, authority, snapshot):
        doc = json.loads(snapshot)
        request = doc['request']
        try:
            contracts.validate('llm.task_request.v1', request)
        except Exception:
            raise TaskError('invalid_request') from None
        if doc['actor_ref'] != authority.actor_ref or doc['owner_ref'] != authority.owner_ref:
            raise ConditionalConflict('task_scope')
        bindings = dict(authority.capabilities)
        expected = []
        for step in request['steps']:
            operation = step['operation']
            if operation not in bindings:
                raise ConditionalConflict('task_capability')
            expected.append(bindings[operation])
            args = step['arguments']
            if 'session_ref' in args:
                pair = (args['session_ref'], args.get('session_version'))
                row = self.store.db.execute('SELECT version FROM sessions WHERE owner=? AND ref=?', (authority.owner_ref, pair[0])).fetchone()
                if pair not in authority.sessions or row is None or row[0] != pair[1]:
                    raise ConditionalConflict('task_session')
        if sorted(set(expected)) != doc['capability_refs'] or any(not self.store.source_allowed(owner_ref=authority.owner_ref, source_ref=ref) for ref in expected):
            raise ConditionalConflict('task_capability')
        refs = list(request.get('context_refs', []))
        refs += [step['arguments']['private_ref'] for step in request['steps'] if 'private_ref' in step['arguments']]
        self._private(authority.owner_ref, refs)
        budget = Budget(request['budget']['calls'], request['budget']['message_limit'], Decimal(request['budget']['max_usd']))
        if not budget.fits(authority.ceiling):
            raise TaskError('budget_exhausted')
        return doc, budget

    def _reserve(self, authority, key, budget, fingerprint):
        row = self.store.db.execute('SELECT calls,messages,usd,hash FROM task_router_reservations WHERE owner=? AND key=?', (authority.owner_ref, key)).fetchone()
        values = (budget.calls, budget.messages, str(budget.max_usd), fingerprint)
        if row:
            if row != values:
                raise ConditionalConflict('reservation_replay')
            return
        totals = self.store.db.execute('SELECT calls,messages,usd FROM task_router_reservations WHERE owner=?', (authority.owner_ref,)).fetchall()
        reserved = Budget(budget.calls + sum(r[0] for r in totals), budget.messages + sum(r[1] for r in totals), budget.max_usd + sum((Decimal(r[2]) for r in totals), Decimal(0)))
        if not reserved.fits(authority.ceiling):
            raise TaskError('budget_exhausted')
        self.store.db.execute('INSERT INTO task_router_reservations VALUES(?,?,?,?,?,?)', (authority.owner_ref, key, *values))

    def _row(self, owner, task):
        row = self.store.db.execute('SELECT task,owner,actor,version,status,snapshot,hash,expires,confirmation FROM task_router_proposals WHERE owner=? AND task=?', (owner, task)).fetchone()
        if row is None:
            raise ConditionalConflict('unknown_task')
        buttons = tuple(InlineButton({'confirm': 'Confirmar', 'correct': 'Corregir', 'cancel': 'Cancelar'}[action], token) for token, action in self.store.db.execute('SELECT token,action FROM task_router_callbacks WHERE owner=? AND task=? AND version=? AND used=0 ORDER BY rowid', (owner, task, row[3])).fetchall())
        snapshot = self._open(owner, row[5])
        if content_hash(json.loads(snapshot)) != row[6]:
            raise ConditionalConflict('task_snapshot_hash')
        return Proposal(*row[:5], snapshot, row[6], parse(row[7]), row[8], buttons)

    def _buttons(self, owner, actor, task, version, status):
        actions = ('confirm', 'correct', 'cancel') if status == 'proposed' else ('correct', 'cancel') if status == 'needs_clarification' or status == 'confirmed' else ('cancel',) if status == 'correcting' else ()
        for action in actions:
            self.store.db.execute('INSERT INTO task_router_callbacks VALUES(?,?,?,?,?,?,0)', ('callback:' + uuid4().hex, owner, actor, task, version, action))

    def _view(self, authority, row, now):
        if row.actor_ref != authority.actor_ref:
            raise ConditionalConflict('task_scope')
        if row.expires_at <= utc(now):
            return replace(row, status='expired', confirmation_hash=None, buttons=())
        if row.status in ('proposed', 'needs_clarification', 'confirmed'):
            self._plan(authority, row.snapshot)
        return row

    def create(self, *, authority, event_ref, plan, expires_at, now):
        with self.store.transaction():
            self._event(event_ref)
            self._authority(authority, now)
            self._plan(authority, plan.snapshot)
            if content_hash(json.loads(plan.snapshot)) != plan.content_hash:
                raise ConditionalConflict('task_snapshot_hash')
            existing = self.store.db.execute('SELECT task,event_hash FROM task_router_proposals WHERE owner=? AND actor=? AND event=?', (authority.owner_ref, authority.actor_ref, event_ref)).fetchone()
            if existing:
                if existing[1] != plan.content_hash:
                    raise ConditionalConflict('task_event_replay')
                current = self._view(authority, self._row(authority.owner_ref, existing[0]), now)
                return replace(current, replayed=True)
            if not utc(now) < utc(expires_at) <= utc(authority.expires_at):
                raise ConditionalConflict('task_expiry')
            task, status = 'task:' + uuid4().hex, 'proposed' if plan.ready else 'needs_clarification'
            sealed = self._seal(authority.owner_ref, plan.snapshot)
            self.store.db.execute('INSERT INTO task_router_proposals VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                  (authority.owner_ref, authority.actor_ref, task, 0, status, sealed, plan.content_hash, stamp(expires_at), None, event_ref, plan.content_hash))
            self._buttons(authority.owner_ref, authority.actor_ref, task, 0, status)
            return self._row(authority.owner_ref, task)

    def get(self, *, authority, task_ref, now):
        with self.store.transaction():
            self._authority(authority, now)
            row = self._row(authority.owner_ref, task_ref)
            return self._view(authority, row, now)

    def act(self, *, authority, callback_ref, event_ref, now):
        with self.store.transaction():
            self._event(event_ref)
            self._authority(authority, now)
            receipt = self.store.db.execute('SELECT token,result FROM task_router_callback_receipts WHERE owner=? AND actor=? AND event=?', (authority.owner_ref, authority.actor_ref, event_ref)).fetchone()
            if receipt:
                if receipt[0] != callback_ref:
                    raise ConditionalConflict('callback_receipt_replay')
                data = json.loads(receipt[1])
                current = self._row(authority.owner_ref, data['task_ref'])
                if current.actor_ref != authority.actor_ref or current.expires_at <= utc(now):
                    raise ConditionalConflict('callback_stale_or_expired')
                # Replays return current intent, never a previously revoked confirmation.
                return replace(self._view(authority, current, now), replayed=True)
            callback = self.store.db.execute('SELECT task,version,action,used FROM task_router_callbacks WHERE token=? AND owner=? AND actor=?', (callback_ref, authority.owner_ref, authority.actor_ref)).fetchone()
            if callback is None or callback[3]:
                raise ConditionalConflict('callback_unknown_or_used')
            row = self._row(authority.owner_ref, callback[0])
            if row.version != callback[1] or row.expires_at <= utc(now) or row.status == 'cancelled':
                raise ConditionalConflict('callback_stale_or_expired')
            action = callback[2]
            status = {'confirm': 'confirmed', 'correct': 'correcting', 'cancel': 'cancelled'}[action]
            if action == 'confirm':
                doc, budget = self._plan(authority, row.snapshot)
                if row.status != 'proposed' or doc['request']['missing_fields']:
                    raise ConditionalConflict('task_not_ready')
                self._reserve(authority, row.task_ref + ':' + str(row.version), budget, row.content_hash)
            confirmation = row.content_hash if action == 'confirm' else None
            self.store.db.execute('UPDATE task_router_proposals SET version=version+1,status=?,confirmation=? WHERE owner=? AND task=? AND version=?', (status, confirmation, authority.owner_ref, row.task_ref, row.version))
            self.store.db.execute('UPDATE task_router_callbacks SET used=1 WHERE owner=? AND task=? AND version=?', (authority.owner_ref, row.task_ref, row.version))
            self._buttons(authority.owner_ref, authority.actor_ref, row.task_ref, row.version + 1, status)
            result = self._row(authority.owner_ref, row.task_ref)
            data = {'task_ref': result.task_ref}
            self.store.db.execute('INSERT INTO task_router_callback_receipts VALUES(?,?,?,?,?)', (authority.owner_ref, authority.actor_ref, event_ref, callback_ref, canonical(data)))
            return result

    def correct(self, *, authority, task_ref, expected_version, plan, now):
        with self.store.transaction():
            self._authority(authority, now)
            self._plan(authority, plan.snapshot)
            if content_hash(json.loads(plan.snapshot)) != plan.content_hash:
                raise ConditionalConflict('task_snapshot_hash')
            row = self._row(authority.owner_ref, task_ref)
            if row.actor_ref != authority.actor_ref or row.version != expected_version or row.status != 'correcting' or row.expires_at <= utc(now):
                raise ConditionalConflict('task_correction_conflict')
            status = 'proposed' if plan.ready else 'needs_clarification'
            sealed = self._seal(authority.owner_ref, plan.snapshot)
            self.store.db.execute('UPDATE task_router_proposals SET version=version+1,status=?,snapshot=?,hash=?,confirmation=NULL WHERE owner=? AND task=?', (status, sealed, plan.content_hash, authority.owner_ref, task_ref))
            self.store.db.execute('UPDATE task_router_callbacks SET used=1 WHERE owner=? AND task=?', (authority.owner_ref, task_ref))
            self._buttons(authority.owner_ref, authority.actor_ref, task_ref, row.version + 1, status)
            return self._row(authority.owner_ref, task_ref)

    def claim_parse(self, *, authority, event_ref, input_hash, context_refs, max_cost, now):
        with self.store.transaction():
            self._event(event_ref)
            self._authority(authority, now)
            self._private(authority.owner_ref, context_refs)
            row = self.store.db.execute('SELECT hash,result FROM task_router_parses WHERE owner=? AND actor=? AND event=?', (authority.owner_ref, authority.actor_ref, event_ref)).fetchone()
            if row:
                if row[0] != input_hash:
                    raise ConditionalConflict('parser_replay_conflict')
                if row[1] is None:
                    raise ConditionalConflict('parser_inflight_or_uncertain')
                return json.loads(self._open(authority.owner_ref, row[1]))
            self._reserve(authority, 'parse:' + authority.actor_ref + ':' + event_ref, Budget(1, 0, max_cost), input_hash)
            self.store.db.execute('INSERT INTO task_router_parses VALUES(?,?,?,?,NULL)', (authority.owner_ref, authority.actor_ref, event_ref, input_hash))
            return None

    def finish_parse(self, *, authority, event_ref, input_hash, document, now):
        with self.store.transaction():
            self._authority(authority, now)
            contracts.validate('llm.task_request.v1', document)
            row = self.store.db.execute('SELECT hash,result FROM task_router_parses WHERE owner=? AND actor=? AND event=?', (authority.owner_ref, authority.actor_ref, event_ref)).fetchone()
            if row is None or row[0] != input_hash or row[1] is not None:
                raise ConditionalConflict('parser_commit_conflict')
            sealed = self._seal(authority.owner_ref, canonical(document))
            self.store.db.execute('UPDATE task_router_parses SET result=? WHERE owner=? AND actor=? AND event=?', (sealed, authority.owner_ref, authority.actor_ref, event_ref))
