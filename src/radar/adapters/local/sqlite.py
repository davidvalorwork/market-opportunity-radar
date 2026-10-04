"""Disk-backed local control plane with indexed, owner-scoped transactions.

SQLite is a conformance target for local behavior, not an AWS emulator. Public
synthetic blobs remain plaintext; this adapter does not implement age encryption.
"""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from radar.adapters.telegram import consent
from radar.domain.core import utc
from radar.ports.types import (AcceptedCommand, Approval, BlobPointer, ConditionalConflict,
                              Lease, LedgerRecord, LedgerState, OutboxEntry, PendingOutbox)
from radar.ports.workflow import LocalAlertIntent, RunState
from .codec import dumps, loads
from .wire import validate_envelope


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def digest(value):
    return sha256(canonical(value)).hexdigest()


def stamp(value):
    return utc(value).isoformat().replace('+00:00', 'Z')


def parse(value):
    return utc(datetime.fromisoformat(value.replace('Z', '+00:00')))


DDL = """
CREATE TABLE IF NOT EXISTS actors(owner TEXT, actor TEXT, enabled INTEGER NOT NULL, PRIMARY KEY(owner,actor));
CREATE TABLE IF NOT EXISTS owners(owner TEXT PRIMARY KEY, stopped INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS receipts(owner TEXT, channel TEXT, ref TEXT, hash TEXT, op TEXT, msg TEXT, PRIMARY KEY(owner,channel,ref));
CREATE INDEX IF NOT EXISTS telegram_receipt_lookup ON receipts(channel,ref);
CREATE TABLE IF NOT EXISTS ignored(ref TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS commands(owner TEXT, op TEXT, msg TEXT, doc TEXT, PRIMARY KEY(owner,op), UNIQUE(owner,msg));
CREATE TABLE IF NOT EXISTS outbox(seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, msg TEXT, entry TEXT, version INTEGER NOT NULL DEFAULT 0, published TEXT, UNIQUE(owner,msg));
CREATE INDEX IF NOT EXISTS pending_outbox ON outbox(owner,published,seq);
CREATE TABLE IF NOT EXISTS searches(owner TEXT, ref TEXT, doc TEXT, PRIMARY KEY(owner,ref));
CREATE TABLE IF NOT EXISTS runs(owner TEXT, op TEXT, doc TEXT, PRIMARY KEY(owner,op));
CREATE TABLE IF NOT EXISTS run_searches(owner TEXT, op TEXT, doc TEXT, PRIMARY KEY(owner,op));
CREATE TABLE IF NOT EXISTS capabilities(owner TEXT, source TEXT, enabled INTEGER NOT NULL, PRIMARY KEY(owner,source));
CREATE TABLE IF NOT EXISTS tasks(owner TEXT, msg TEXT, op TEXT, doc TEXT, cursor INTEGER, next_cursor INTEGER, PRIMARY KEY(owner,msg));
CREATE INDEX IF NOT EXISTS tasks_by_operation ON tasks(owner,op);
CREATE TABLE IF NOT EXISTS worker_results(owner TEXT, msg TEXT, op TEXT, hash TEXT, doc TEXT, cause TEXT, received TEXT, PRIMARY KEY(owner,msg), UNIQUE(owner,cause));
CREATE INDEX IF NOT EXISTS worker_result_cause ON worker_results(owner,cause);
CREATE TABLE IF NOT EXISTS projected_signals(owner TEXT, msg TEXT, op TEXT, doc TEXT, PRIMARY KEY(owner,msg));
CREATE INDEX IF NOT EXISTS signals_by_operation ON projected_signals(owner,op);
CREATE TABLE IF NOT EXISTS projections(owner TEXT, msg TEXT, op TEXT, hash TEXT, doc TEXT, page INTEGER, PRIMARY KEY(owner,msg));
CREATE INDEX IF NOT EXISTS projections_by_run ON projections(owner,op,page);
CREATE TABLE IF NOT EXISTS alerts(seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, ref TEXT, doc TEXT, delivered INTEGER NOT NULL DEFAULT 0, UNIQUE(owner,ref));
CREATE INDEX IF NOT EXISTS pending_alerts ON alerts(owner,delivered,seq);
CREATE TABLE IF NOT EXISTS ledger(owner TEXT, op TEXT, doc TEXT, PRIMARY KEY(owner,op));
CREATE TABLE IF NOT EXISTS cancelled_actions(owner TEXT,op TEXT,PRIMARY KEY(owner,op));
CREATE TABLE IF NOT EXISTS sessions(owner TEXT, ref TEXT, version INTEGER, PRIMARY KEY(owner,ref));
CREATE TABLE IF NOT EXISTS leases(owner TEXT, ref TEXT, doc TEXT, PRIMARY KEY(owner,ref));
CREATE TABLE IF NOT EXISTS blobs(owner TEXT, key TEXT, hash TEXT, content BLOB, PRIMARY KEY(owner,key));
CREATE TABLE IF NOT EXISTS provider_proofs(owner TEXT, ref TEXT, op TEXT, recipient TEXT, session TEXT, session_version INTEGER, hash TEXT, purpose TEXT, protocol TEXT, PRIMARY KEY(owner,ref));
CREATE TABLE IF NOT EXISTS directory(id INTEGER PRIMARY KEY, owner TEXT, actor TEXT, role TEXT, consent_version TEXT, consent_at TEXT, UNIQUE(owner,actor));
CREATE TABLE IF NOT EXISTS consent_history(seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, actor TEXT, version TEXT, accepted TEXT);
CREATE TABLE IF NOT EXISTS queue(seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, msg TEXT, destination TEXT, entry TEXT, acked INTEGER DEFAULT 0, token TEXT, visible TEXT, UNIQUE(owner,msg));
CREATE INDEX IF NOT EXISTS queue_pending ON queue(owner,destination,acked,seq);
"""


class SQLiteStore:
    def __init__(self, path, *, failpoint=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript(DDL)
        self.failpoint = failpoint or (lambda stage: None)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.failpoint('before_commit')
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def enroll(self, *, owner_ref, actor_ref):
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO owners(owner) VALUES(?)', (owner_ref,))
            self.db.execute('INSERT OR REPLACE INTO actors VALUES(?,?,1)', (owner_ref, actor_ref))

    def authorize_actor(self, *, owner_ref, actor_ref):
        return self.db.execute('SELECT 1 FROM actors WHERE owner=? AND actor=? AND enabled=1', (owner_ref, actor_ref)).fetchone() is not None

    def current_consent(self, owner, actor):
        return self.db.execute('SELECT 1 FROM directory WHERE owner=? AND actor=? AND consent_version=?',
                               (owner,actor,consent.CONSENT_VERSION)).fetchone() is not None

    def owner_actor(self, owner, actor):
        return self.authorize_actor(owner_ref=owner,actor_ref=actor) and self.db.execute(
            'SELECT 1 FROM directory WHERE owner=? AND actor=? AND role=\'owner\'',(owner,actor)).fetchone() is not None

    def revoke(self, *, owner_ref, actor_ref):
        self.db.execute('UPDATE actors SET enabled=0 WHERE owner=? AND actor=?', (owner_ref, actor_ref))

    def _active(self, owner):
        row = self.db.execute('SELECT stopped FROM owners WHERE owner=?', (owner,)).fetchone()
        if row is None or row[0]:
            raise ConditionalConflict('owner_stopped_or_unknown')

    def stop(self, *, owner_ref):
        with self.transaction():
            self.db.execute('UPDATE owners SET stopped=1 WHERE owner=?', (owner_ref,))
            self.db.execute('UPDATE directory SET consent_version=NULL,consent_at=NULL WHERE owner=?',(owner_ref,))
            for op, doc in self.db.execute('SELECT op,doc FROM runs WHERE owner=?', (owner_ref,)).fetchall():
                run = loads(doc)
                if run.status in ('pending', 'running'):
                    self._write_run(owner_ref, replace(run, status='cancelled', version=run.version+1))

    def allow_source(self, *, owner_ref, source_ref, authorized):
        self.db.execute('INSERT OR REPLACE INTO capabilities VALUES(?,?,?)', (owner_ref, source_ref, int(authorized is True)))

    def source_allowed(self, *, owner_ref, source_ref):
        return self.db.execute('SELECT 1 FROM capabilities WHERE owner=? AND source=? AND enabled=1', (owner_ref, source_ref)).fetchone() is not None

    def _insert_outbox(self, owner, entry):
        validate_envelope(entry.envelope)
        if entry.envelope['owner_ref'] != owner:
            raise ConditionalConflict('envelope_owner')
        msg = entry.envelope['message_id']
        row = self.db.execute('SELECT entry FROM outbox WHERE owner=? AND msg=?', (owner, msg)).fetchone()
        if row:
            if row[0] != dumps(entry):
                raise ConditionalConflict('outbox_identity_conflict')
            return
        self.db.execute('INSERT INTO outbox(owner,msg,entry) VALUES(?,?,?)', (owner,msg,dumps(entry)))
        self.failpoint('after_outbox')

    def accept_command(self, *, owner_ref, receipt, command, outbox):
        validate_envelope(command)
        if command['kind'] != 'telegram.command' or command['owner_ref'] != owner_ref or command != outbox.envelope:
            raise ConditionalConflict('command_owner_or_outbox')
        actor = command['payload']['telegram_user_ref']
        if not self.authorize_actor(owner_ref=owner_ref, actor_ref=actor):
            raise ConditionalConflict('command_actor')
        if command['payload']['command'] not in consent.UNGATED and not self.current_consent(owner_ref,actor):
            raise ConditionalConflict('command_consent')
        refs = tuple(dict.fromkeys((receipt.event_ref,) + receipt.idempotency_refs))
        with self.transaction():
            if not self.authorize_actor(owner_ref=owner_ref,actor_ref=actor) or (command['payload']['command'] not in consent.UNGATED and not self.current_consent(owner_ref,actor)):
                raise ConditionalConflict('command_authority_changed')
            found = [self.db.execute('SELECT hash,op,msg FROM receipts WHERE owner=? AND channel=? AND ref=?',
                                     (owner_ref,receipt.channel,ref)).fetchone() for ref in refs]
            present = [r for r in found if r]
            if present:
                original = present[0]
                if any(r != original for r in present) or original[0] != receipt.command_hash:
                    raise ConditionalConflict('receipt_replay_mismatch')
                # A callback repeated under a new update ref also gets that
                # ref committed; no new command, original IDs preserved.
                for ref in refs:
                    self.db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?,?)',
                                    (owner_ref,receipt.channel,ref,*original))
                return AcceptedCommand(original[1], original[2], True)
            op,msg = command['operation_id'],command['message_id']
            for ref in refs:
                self.db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?)', (owner_ref,receipt.channel,ref,receipt.command_hash,op,msg))
            self.failpoint('after_receipt')
            self.db.execute('INSERT INTO commands VALUES(?,?,?,?)', (owner_ref,op,msg,dumps(dict(command))))
            self.failpoint('after_command')
            self._insert_outbox(owner_ref,outbox)
            return AcceptedCommand(op,msg,False)

    def command(self, *, owner_ref, operation_id):
        row = self.db.execute('SELECT doc FROM commands WHERE owner=? AND op=?', (owner_ref,operation_id)).fetchone()
        if not row:
            raise ConditionalConflict('unknown_command')
        return loads(row[0])

    def pending(self, *, owner_ref, limit, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('bounded_limit_required')
        rows = self.db.execute('SELECT seq,entry,version FROM outbox WHERE owner=? AND published IS NULL AND seq>? ORDER BY seq LIMIT ?',
                               (owner_ref,int(cursor or 0),limit+1)).fetchall()
        page = rows[:limit]
        return PendingOutbox(tuple(replace(loads(r[1]), version=r[2]) for r in page), str(page[-1][0]) if len(rows)>limit else None)

    def mark_published(self, *, owner_ref, message_id, expected_version, published_at):
        return self.db.execute('UPDATE outbox SET published=?,version=version+1 WHERE owner=? AND msg=? AND version=? AND published IS NULL',
                               (stamp(published_at),owner_ref,message_id,expected_version)).rowcount == 1

    def save_search(self, *, owner_ref, search):
        if not self.authorize_actor(owner_ref=owner_ref,actor_ref=search.actor_ref):
            raise ConditionalConflict('search_actor')
        if not 1 <= search.page_size <= 10 or not 1 <= search.max_jobs <= 100 or not 1 <= search.max_pages <= 100 or not 1 <= search.max_comparisons <= 10000:
            raise ValueError('bounded_search_required')
        self.db.execute('INSERT OR REPLACE INTO searches VALUES(?,?,?)', (owner_ref,search.search_ref,dumps(search)))

    def search(self, *, owner_ref, search_ref):
        row = self.db.execute('SELECT doc FROM searches WHERE owner=? AND ref=?', (owner_ref,search_ref)).fetchone()
        if not row:
            raise ConditionalConflict('unknown_search')
        return loads(row[0])

    def run(self, *, owner_ref, operation_id):
        row = self.db.execute('SELECT doc FROM runs WHERE owner=? AND op=?', (owner_ref,operation_id)).fetchone()
        if not row:
            raise ConditionalConflict('unknown_run')
        return loads(row[0])

    def search_for_run(self, *, owner_ref, operation_id):
        row = self.db.execute('SELECT doc FROM run_searches WHERE owner=? AND op=?',(owner_ref,operation_id)).fetchone()
        if not row:
            raise ConditionalConflict('unknown_run_snapshot')
        return loads(row[0])

    def _write_run(self, owner, run):
        self.db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?)', (owner,run.operation_id,dumps(run)))

    def _schedule(self, owner, run, search, command, now):
        if parse(command['deadline']) <= now:
            self._write_run(owner,replace(run,status='expired',version=run.version+1))
            self._alert(owner,LocalAlertIntent('expired:'+run.operation_id,run.actor_ref,run.operation_id,'deadline_expired'))
            return
        if run.jobs_used >= search.max_jobs or run.pages_used >= search.max_pages:
            self._write_run(owner,replace(run,status='budget_exhausted',version=run.version+1))
            self._alert(owner,LocalAlertIntent('budget:'+run.operation_id,run.actor_ref,run.operation_id,'budget_exhausted'))
            return
        updated = replace(run,status='running',jobs_used=run.jobs_used+1,pages_used=run.pages_used+1,version=run.version+1)
        task = {'schema_version':command['schema_version'],'message_id':str(uuid4()),'operation_id':run.operation_id,
                'correlation_id':command['correlation_id'],'causation_id':command['message_id'],
                'owner_ref':owner,'kind':'browser.read','deadline':command['deadline'],'attempt':1,
                'expected_version':updated.version,
                'payload':{'schema_version':1,'capability_id':'fixture.local.read',
                           'target':{'origin':'http://localhost','path':'/public-fixture'},'fixture_only':True,
                           'mode':'direct','batch':search.page_size,'deadline_ms':1000}}
        validate_envelope(task)
        self.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,NULL)', (owner,task['message_id'],run.operation_id,dumps(task),run.cursor))
        self._insert_outbox(owner,OutboxEntry(task,'browser.fifo',owner+':'+search.source_ref))
        self._write_run(owner,updated)

    def start_run(self, *, owner_ref, search_ref, command, now):
        validate_envelope(command)
        with self.transaction():
            self._active(owner_ref)
            actor = command['payload']['telegram_user_ref']
            if not self.current_consent(owner_ref,actor) or not self.authorize_actor(owner_ref=owner_ref,actor_ref=actor):
                raise ConditionalConflict('run_consent')
            saved = self.command(owner_ref=owner_ref,operation_id=command['operation_id'])
            if saved != command:
                raise ConditionalConflict('run_command_binding_or_expiry')
            row = self.db.execute('SELECT doc FROM runs WHERE owner=? AND op=?',(owner_ref,command['operation_id'])).fetchone()
            if row:
                existing = loads(row[0])
                frozen = self.search_for_run(owner_ref=owner_ref,operation_id=existing.operation_id)
                if frozen.actor_ref != actor or existing.search_ref != search_ref:
                    raise ConditionalConflict('original_run_binding')
                return existing
            search = self.search(owner_ref=owner_ref,search_ref=search_ref)
            if search.actor_ref != actor:
                raise ConditionalConflict('search_actor_binding')
            if command['payload']['command'] != 'buscar' or command['payload'].get('args',{}).get('query') != search.query:
                raise ConditionalConflict('saved_search_query_binding')
            if parse(command['deadline']) <= now:
                raise ConditionalConflict('command_expired')
            run = RunState(command['operation_id'],search_ref,search.actor_ref,'pending',0,0,0,0,command['message_id'],now)
            self._write_run(owner_ref,run)
            self.db.execute('INSERT INTO run_searches VALUES(?,?,?)',(owner_ref,run.operation_id,dumps(search)))
            if not self.source_allowed(owner_ref=owner_ref,source_ref=search.source_ref):
                self._write_run(owner_ref,replace(run,status='blocked',version=1))
                self._alert(owner_ref,LocalAlertIntent('blocked:'+run.operation_id,run.actor_ref,run.operation_id,'source_not_authorized'))
            else:
                self._schedule(owner_ref,run,search,command,now)
        return self.run(owner_ref=owner_ref,operation_id=run.operation_id)

    def cancel(self, *, owner_ref, operation_id):
        with self.transaction():
            run = self.run(owner_ref=owner_ref,operation_id=operation_id)
            self._write_run(owner_ref,replace(run,status='cancelled',version=run.version+1))

    def task(self, *, owner_ref, message_id):
        row = self.db.execute('SELECT doc,cursor,next_cursor FROM tasks WHERE owner=? AND msg=?', (owner_ref,message_id)).fetchone()
        if not row:
            raise ConditionalConflict('unknown_task')
        return loads(row[0]),row[1],row[2]

    def worker_result(self, *, owner_ref, task_message_id):
        row = self.db.execute('SELECT doc FROM worker_results WHERE owner=? AND cause=?',
                              (owner_ref,task_message_id)).fetchone()
        # json storage has no wrapper around plain dict values.
        return loads(row[0]) if row else None

    def complete_worker(self, *, owner_ref, task_message_id, envelope, next_cursor, now):
        validate_envelope(envelope)
        with self.transaction():
            task,_,_ = self.task(owner_ref=owner_ref,message_id=task_message_id)
            self._validate_result_binding(owner_ref,task,envelope,now,require_live=False)
            existing = self.worker_result(owner_ref=owner_ref,task_message_id=task_message_id)
            if existing:
                if existing != envelope:
                    raise ConditionalConflict('worker_replay_content')
                return existing
            self._validate_result_binding(owner_ref,task,envelope,now)
            self.db.execute('INSERT INTO worker_results VALUES(?,?,?,?,?,?,?)', (owner_ref,envelope['message_id'],envelope['operation_id'],digest(envelope),dumps(envelope),task_message_id,stamp(now)))
            self.db.execute('UPDATE tasks SET next_cursor=? WHERE owner=? AND msg=?', (next_cursor,owner_ref,task_message_id))
            self._insert_outbox(owner_ref,OutboxEntry(envelope,'results',owner_ref+':'+envelope['operation_id']))
        return envelope

    def _validate_result_binding(self, owner, task, envelope, now, *, require_live=True):
        if envelope['kind'] != 'browser.result' or envelope['owner_ref'] != owner or any(envelope.get(k) != task.get(k) for k in ('schema_version','operation_id','correlation_id','deadline','expected_version')) or envelope.get('causation_id') != task['message_id'] or (require_live and parse(envelope['deadline']) <= now):
            raise ConditionalConflict('result_binding_or_expiry')

    def _alert(self, owner, intent):
        self.db.execute('INSERT OR IGNORE INTO alerts(owner,ref,doc) VALUES(?,?,?)',(owner,intent.intent_ref,dumps(intent)))
        self.failpoint('after_alert')

    def commit_projection(self, *, owner_ref, result, report, now):
        from .worker import decode_result
        env = result.envelope
        validate_envelope(env)
        with self.transaction():
            if decode_result(self,owner_ref=owner_ref,envelope=env) != result:
                raise ConditionalConflict('result_projection_must_derive_from_evidence')
            task,_,next_cursor = self.task(owner_ref=owner_ref,message_id=env.get('causation_id'))
            # Durable replays may arrive after transport deadline; new work may
            # not. Identity/hash checks still precede returning the old report.
            previous = self.projected_report(owner_ref=owner_ref,result=result)
            if previous is not None:
                if previous != report:
                    raise ConditionalConflict('projection_replay_conflict')
                return False
            persisted = self.db.execute('SELECT hash,doc,received FROM worker_results WHERE owner=? AND msg=? AND op=?', (owner_ref,env['message_id'],env['operation_id'])).fetchone()
            if not persisted or persisted[0] != result.content_hash or result.content_hash != digest(env) or loads(persisted[1]) != env:
                raise ConditionalConflict('result_hash_or_receipt')
            self._validate_result_binding(owner_ref,task,env,parse(persisted[2]))
            previous = self.db.execute('SELECT hash,doc FROM projections WHERE owner=? AND msg=?',(owner_ref,env['message_id'])).fetchone()
            if previous:
                if previous != (result.content_hash,dumps(report)):
                    raise ConditionalConflict('projection_replay_conflict')
                return False
            run = self.run(owner_ref=owner_ref,operation_id=env['operation_id'])
            if run.status != 'running' or run.version != task['expected_version']:
                raise ConditionalConflict('cancelled_or_stale_run')
            self.db.execute('INSERT INTO projections VALUES(?,?,?,?,?,?)',(owner_ref,env['message_id'],env['operation_id'],result.content_hash,dumps(report),run.pages_used))
            self.db.execute('INSERT INTO projected_signals VALUES(?,?,?,?)',(owner_ref,env['message_id'],env['operation_id'],dumps(result.signals)))
            self.failpoint('after_projection')
            self._alert(owner_ref,LocalAlertIntent('result:'+env['message_id'],run.actor_ref,run.operation_id,'report',report))
            search = self.search_for_run(owner_ref=owner_ref,operation_id=run.operation_id)
            if next_cursor is None or result.status == 'failed':
                self._write_run(owner_ref,replace(run,status='succeeded' if result.status == 'succeeded' else result.status,version=run.version+1))
            else:
                command = self.command(owner_ref=owner_ref,operation_id=run.operation_id)
                self._schedule(owner_ref,replace(run,cursor=next_cursor),search,command,now)
        return True

    def projected_report(self, *, owner_ref, result):
        env = result.envelope
        validate_envelope(env)
        row = self.db.execute('SELECT hash,op,doc FROM projections WHERE owner=? AND msg=?',(owner_ref,env['message_id'])).fetchone()
        if env['owner_ref'] != owner_ref or result.content_hash != digest(env):
            raise ConditionalConflict('result_owner_or_hash')
        if row:
            stored = self.db.execute('SELECT doc FROM worker_results WHERE owner=? AND msg=?',(owner_ref,env['message_id'])).fetchone()
            if row[0] != result.content_hash or row[1] != env['operation_id'] or not stored or loads(stored[0]) != env:
                raise ConditionalConflict('result_replay_binding')
            return loads(row[2])
        return None

    def signals_for_run(self, *, owner_ref, operation_id):
        return tuple(s for row in self.db.execute('SELECT doc FROM projected_signals WHERE owner=? AND op=? ORDER BY msg',(owner_ref,operation_id)) for s in loads(row[0]))

    def latest_report(self, *, owner_ref, operation_id):
        row = self.db.execute('SELECT doc FROM projections WHERE owner=? AND op=? ORDER BY page DESC LIMIT 1',(owner_ref,operation_id)).fetchone()
        return loads(row[0]) if row else None

    def reports(self, *, owner_ref, operation_id):
        return tuple(loads(r[0]) for r in self.db.execute('SELECT doc FROM projections WHERE owner=? AND op=? ORDER BY msg',(owner_ref,operation_id)))

    def pending_alerts(self, *, owner_ref, limit=10, cursor=0):
        if not 1 <= limit <= 100:
            raise ValueError('bounded_limit_required')
        return tuple((seq,loads(doc)) for seq,doc in self.db.execute('SELECT seq,doc FROM alerts WHERE owner=? AND delivered=0 AND seq>? ORDER BY seq LIMIT ?', (owner_ref,cursor,limit)))

    def mark_alert(self, *, owner_ref, intent_ref):
        self.db.execute('UPDATE alerts SET delivered=1 WHERE owner=? AND ref=?',(owner_ref,intent_ref))

    def put_if_absent(self, *, owner_ref, blob, content):
        if sha256(content).hexdigest() != blob.sha256 or blob.recipient_scope is not None:
            raise ConditionalConflict('digest_or_encryption_not_supported')
        with self.transaction():
            self._active(owner_ref)
            row = self.db.execute('SELECT hash,content FROM blobs WHERE owner=? AND key=?',(owner_ref,blob.blob_key)).fetchone()
            if row and row != (blob.sha256,content):
                raise ConditionalConflict('immutable_blob')
            self.db.execute('INSERT OR IGNORE INTO blobs VALUES(?,?,?,?)',(owner_ref,blob.blob_key,blob.sha256,content))
        return blob

    def read(self, *, owner_ref, blob, max_bytes):
        row = self.db.execute('SELECT hash,content FROM blobs WHERE owner=? AND key=?',(owner_ref,blob.blob_key)).fetchone()
        if not row or row[0] != blob.sha256 or sha256(row[1]).hexdigest() != blob.sha256 or len(row[1]) > max_bytes or blob.recipient_scope is not None:
            raise ConditionalConflict('blob_access_digest_or_size')
        return row[1]

    def set_session(self, *, owner_ref, session_ref, version):
        if type(version) is not int or version < 1:
            raise ValueError('positive_session_version')
        self.db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?)',(owner_ref,session_ref,version))

    def acquire(self, *, owner_ref, session_ref, worker_ref, now, ttl):
        if ttl <= timedelta(0):
            raise ValueError('positive_ttl')
        with self.transaction():
            self._active(owner_ref)
            row = self.db.execute('SELECT doc FROM leases WHERE owner=? AND ref=?',(owner_ref,session_ref)).fetchone()
            old = loads(row[0]) if row else None
            if old and old.expires_at > now:
                return None
            lease = Lease(session_ref,worker_ref,str(uuid4()),now+ttl,old.version+1 if old else 1)
            self.db.execute('INSERT OR REPLACE INTO leases VALUES(?,?,?)',(owner_ref,session_ref,dumps(lease)))
            return lease

    def is_current(self, *, owner_ref, lease, now):
        row = self.db.execute('SELECT doc FROM leases WHERE owner=? AND ref=?',(owner_ref,lease.session_ref)).fetchone()
        return bool(row and loads(row[0]) == lease and lease.expires_at > now)

    def renew(self, *, owner_ref, lease, now, ttl):
        if ttl <= timedelta(0):
            raise ValueError('positive_ttl')
        with self.transaction():
            if not self.is_current(owner_ref=owner_ref,lease=lease,now=now):
                return None
            renewed = replace(lease,expires_at=now+ttl,version=lease.version+1)
            self.db.execute('UPDATE leases SET doc=? WHERE owner=? AND ref=?',(dumps(renewed),owner_ref,lease.session_ref))
            return renewed

    def release(self, *, owner_ref, lease):
        # Keep a tombstone version: release must not reset the fencing epoch.
        with self.transaction():
            row = self.db.execute('SELECT doc FROM leases WHERE owner=? AND ref=?',(owner_ref,lease.session_ref)).fetchone()
            if not row or loads(row[0]) != lease:
                return False
            self.db.execute('UPDATE leases SET doc=? WHERE owner=? AND ref=?',(dumps(replace(lease,expires_at=parse('1970-01-01T00:00:00Z'))),owner_ref,lease.session_ref))
            return True

    def get(self, *, owner_ref, operation_id):
        row = self.db.execute('SELECT doc FROM ledger WHERE owner=? AND op=?',(owner_ref,operation_id)).fetchone()
        return loads(row[0]) if row else None

    def _write_ledger(self, owner, record):
        self.db.execute('INSERT OR REPLACE INTO ledger VALUES(?,?,?)',(owner,record.operation_id,dumps(record)))
        self.failpoint('after_ledger')

    def propose(self, *, owner_ref, operation_id, content_hash, recipient_ref, purpose, session_ref, session_version):
        record = LedgerRecord(operation_id,LedgerState.PROPOSED,0,content_hash,recipient_ref,purpose,session_ref,session_version)
        with self.transaction():
            self._active(owner_ref)
            old = self.get(owner_ref=owner_ref,operation_id=operation_id)
            if old:
                if any(getattr(old,k) != getattr(record,k) for k in ('content_hash','recipient_ref','purpose','session_ref','session_version')):
                    raise ConditionalConflict('action_binding')
                return old
            self._write_ledger(owner_ref,record)
        return record

    def _approved(self, owner, op, expected, approval, now):
        self._active(owner)
        if self.db.execute('SELECT 1 FROM cancelled_actions WHERE owner=? AND op=?',(owner,op)).fetchone():
            raise ConditionalConflict('action_cancelled')
        record = self.get(owner_ref=owner,operation_id=op)
        session = self.db.execute('SELECT version FROM sessions WHERE owner=? AND ref=?',(owner,approval.session_ref)).fetchone()
        if not record or record.state != LedgerState.PROPOSED or record.version != expected or approval.expires_at <= now or not self.owner_actor(owner,approval.actor_ref) or not self.current_consent(owner,approval.actor_ref) or not session or session[0] != approval.session_version or any(getattr(record,k) != getattr(approval,k) for k in ('recipient_ref','session_ref','session_version','content_hash','purpose')):
            raise ConditionalConflict('approval_binding_expiry_version')
        approved = replace(record,state=LedgerState.APPROVED,version=record.version+1,approval=approval)
        self._write_ledger(owner,approved)
        return approved

    def approve_local(self, *, owner_ref, operation_id, expected_version, approval, now):
        with self.transaction():
            return self._approved(owner_ref,operation_id,expected_version,approval,now)

    def approve_action(self, *, owner_ref, operation_id, expected_version, approval, outbox, now):
        raise NotImplementedError('wire_action_requires_age_and_verified_private_payload; use explicit local intent')

    def claim_dispatch(self, *, owner_ref, operation_id, expected_version, lease, provider_message_ref, now):
        with self.transaction():
            self._active(owner_ref)
            if self.db.execute('SELECT 1 FROM cancelled_actions WHERE owner=? AND op=?',(owner_ref,operation_id)).fetchone():
                raise ConditionalConflict('action_cancelled')
            record = self.get(owner_ref=owner_ref,operation_id=operation_id)
            session = self.db.execute('SELECT version FROM sessions WHERE owner=? AND ref=?',(owner_ref,lease.session_ref)).fetchone()
            if not record or record.state != LedgerState.APPROVED or record.version != expected_version or not record.approval or record.approval.expires_at <= now or not self.owner_actor(owner_ref,record.approval.actor_ref) or not self.current_consent(owner_ref,record.approval.actor_ref) or not session or session[0] != record.session_version or record.session_ref != lease.session_ref or not self.is_current(owner_ref=owner_ref,lease=lease,now=now) or not provider_message_ref:
                raise ConditionalConflict('dispatch_binding_or_current_lease')
            claimed = replace(record,state=LedgerState.DISPATCH_COMMITTED,version=record.version+1,provider_message_ref=provider_message_ref)
            self._write_ledger(owner_ref,claimed)
            return claimed

    def cancel_action(self, *, owner_ref, operation_id):
        with self.transaction():
            record = self.get(owner_ref=owner_ref,operation_id=operation_id)
            if not record or record.state not in (LedgerState.PROPOSED,LedgerState.APPROVED,LedgerState.DISPATCH_COMMITTED):
                raise ConditionalConflict('cannot_cancel_dispatch_started')
            self.db.execute('INSERT OR IGNORE INTO cancelled_actions VALUES(?,?)',(owner_ref,operation_id))

    def _finish(self, owner, op, expected, target, provider_ref):
        record = self.get(owner_ref=owner,operation_id=op)
        if record and record.state == target and provider_ref == record.provider_message_ref:
            return record
        if not record or record.state != LedgerState.DISPATCH_COMMITTED or record.version != expected or target not in (LedgerState.PROVIDER_CONFIRMED,LedgerState.SEND_UNCERTAIN) or provider_ref != record.provider_message_ref:
            raise ConditionalConflict('result_transition')
        completed = replace(record,state=target,version=record.version+1)
        self._write_ledger(owner,completed)
        self._alert(owner,LocalAlertIntent('action:'+op,record.approval.actor_ref,op,target.value))
        return completed

    def finish_local(self, *, owner_ref, operation_id, expected_version, target, provider_message_ref, now):
        with self.transaction():
            return self._finish(owner_ref,operation_id,expected_version,target,provider_message_ref)

    def record_result(self, *, owner_ref, operation_id, expected_version, target, outbox, provider_message_ref, now):
        raise NotImplementedError('wire_result_requires_verified_provider_boundary; use explicit local intent')

    def transition(self, *, owner_ref, operation_id, expected_version, expected_state, target, outbox, now, provider_message_ref=None):
        raise NotImplementedError('wire_transition_not_implemented; reconciliation_requires_independent_proof')

    def reconcile_proof(self, *, owner_ref, operation_id, proof_ref, now):
        with self.transaction():
            record = self.get(owner_ref=owner_ref,operation_id=operation_id)
            proof = self.db.execute('SELECT op,recipient,session,session_version,hash,purpose,protocol FROM provider_proofs WHERE owner=? AND ref=?',(owner_ref,proof_ref)).fetchone()
            binding = (operation_id,record.recipient_ref,record.session_ref,record.session_version,record.content_hash,record.purpose,record.provider_message_ref) if record else None
            if not record or record.state != LedgerState.SEND_UNCERTAIN or proof != binding:
                raise ConditionalConflict('independent_provider_proof_required')
            settled = replace(record,state=LedgerState.PROVIDER_CONFIRMED,version=record.version+1)
            self._write_ledger(owner_ref,settled)
            self._alert(owner_ref,LocalAlertIntent('reconciled:'+operation_id,record.approval.actor_ref,operation_id,'provider_confirmed'))
            return settled
