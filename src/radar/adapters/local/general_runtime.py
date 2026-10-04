"""Durable general task executor using A6/A8/A9/A10/A11 APIs, not Products.

All application content goes through an owner-bound private vault. Control data
are IDs, states, hashes, counts and pointers only. No default live effects/LLM.
"""
from base64 import b64decode, b64encode
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
import re
from uuid import uuid4

from radar.adapters.sources.generic import ReadRequest
from radar.adapters.sources.generic.model import Limits, Code, Record, Report as SourceReport, Provenance
from radar.application.tasks.models import Authority, TaskError, canonical, content_hash
from radar.application.research import ReportBuilder, ResearchRequest, ResearchBudget, SourceMaterial
from radar.application.research.models import digest, SourceUnavailable
from radar.application.conversations import ApprovalScreen, DraftInput
from radar.application.conversations.models import ConversationError
from radar.application.contact.model import ContactError
from radar.application.research.models import ReportError
from radar.application.schedules.model import Schedule, TemplateBinding, ScheduleDenied
from radar.ports.types import BlobPointer, ConditionalConflict
from .sqlite import stamp, parse


class GeneralError(ValueError):
    """Static diagnostics only, never external exception strings."""


RESULT_STATES=frozenset({'succeeded','partial','pending','awaiting_approval','blocked'})
CONTROL_REASON=re.compile(r'[a-z][a-z0-9_]{0,63}')


def control_reason(value):
    return value if type(value) is str and CONTROL_REASON.fullmatch(value) else 'handler_reason_invalid'


def checked_step_result(value):
    """Host results cannot introduce a new lifecycle or raw diagnostic text.

    Token syntax is not a content classifier: trusted plugins still must return
    predefined static reasons, never private text disguised as a valid token.
    """
    if not isinstance(value,(tuple,list)) or len(value)!=3:
        return {},'blocked','handler_result_invalid'
    result,state,reason=value
    if type(state) is not str or state not in RESULT_STATES:
        return {},'blocked','handler_state_invalid'
    if reason is not None and (type(reason) is not str or not CONTROL_REASON.fullmatch(reason)):
        return {},'blocked','handler_reason_invalid'
    if type(result) is not dict:
        return {},'blocked','handler_result_invalid'
    return result,state,reason


DDL = """
CREATE TABLE IF NOT EXISTS general_runs(owner TEXT,actor TEXT,ref TEXT,task TEXT,intent INTEGER,version INTEGER,status TEXT,hash TEXT,deadline TEXT,calls INTEGER,budget INTEGER,pointer TEXT,reason TEXT,occurrence TEXT,PRIMARY KEY(owner,ref),UNIQUE(owner,task,intent,occurrence));
CREATE TABLE IF NOT EXISTS general_steps(owner TEXT,run TEXT,id TEXT,position INTEGER,operation TEXT,state TEXT,version INTEGER,attempt INTEGER,pointer TEXT,reason TEXT,next_due TEXT,PRIMARY KEY(owner,run,id));
CREATE TABLE IF NOT EXISTS general_callbacks(owner TEXT,actor TEXT,token TEXT,kind TEXT,pointer TEXT,used INTEGER DEFAULT 0,PRIMARY KEY(owner,token));
CREATE TABLE IF NOT EXISTS general_notifications(owner TEXT,actor TEXT,ref TEXT,state TEXT,pointer TEXT,screen TEXT,reason TEXT,PRIMARY KEY(owner,ref));
CREATE INDEX IF NOT EXISTS general_notifications_pending ON general_notifications(owner,state,ref);
CREATE TABLE IF NOT EXISTS general_firings(owner TEXT,run TEXT,occurrence TEXT,binding TEXT,PRIMARY KEY(owner,run),UNIQUE(owner,occurrence));
CREATE TABLE IF NOT EXISTS general_contact_links(owner TEXT,batch TEXT,contact TEXT,outbound TEXT,PRIMARY KEY(owner,batch,contact));
CREATE TABLE IF NOT EXISTS general_approval_runs(owner TEXT,batch TEXT,run TEXT,PRIMARY KEY(owner,batch));
CREATE TABLE IF NOT EXISTS general_contact_runs(owner TEXT,campaign TEXT,run TEXT,PRIMARY KEY(owner,campaign));
"""


def pointer_doc(pointer):
    return asdict(pointer)


def source_report(document):
    records = tuple(Record(row['record_ref'], row['owner_ref'], BlobPointer(**row['private_ref']),
                           tuple(Provenance(p['source_ref'], p['page'], parse(p['observed_at'])) for p in row['provenance']))
                    for row in document['records'])
    return SourceReport(document['request_ref'], document['source_ref'], Code(document['code']), document['complete'],
                        document['pages'], document['requests'], document['bytes_received'], document['duplicates'], records,
                        BlobPointer(**document['resume_ref']) if 'resume_ref' in document else None)


class GeneralRuntime:
    def __init__(self, store, *, tasks, router, vault, source_sink, report_vault, reader,
                 conversations, host_authority, clock, interpreter=None, source_refs=(),
                 scheduler=None, template_resolver=None, contacts=None, fixture_dispatcher=None,
                 delivery=None, fixture=False, max_runs=100, max_attempts=3, query_policy=None,
                 inbox_sources=None, handlers=None):
        if (not callable(host_authority) or any(value is None for value in (tasks, router, vault, source_sink, report_vault, reader, conversations, clock))
                or type(fixture) is not bool or type(max_runs) is not int or not 1 <= max_runs <= 1000
                or type(max_attempts) is not int or not 1 <= max_attempts <= 5):
            raise GeneralError('general_configuration')
        if fixture_dispatcher is not None and (not fixture or fixture_dispatcher.fixture_only is not True):
            raise GeneralError('fixture_dispatcher_not_authorized')
        self.store, self.tasks, self.router = store, tasks, router
        self.vault, self.source_sink, self.report_vault, self.reader = vault, source_sink, report_vault, reader
        self.conversations, self.host_authority, self.clock = conversations, host_authority, clock
        self.interpreter, self.source_refs = interpreter, tuple(source_refs)
        self.scheduler, self.template_resolver, self.contacts = scheduler, template_resolver, contacts
        self.fixture_dispatcher, self.fixture, self.delivery = fixture_dispatcher, fixture, delivery
        self.max_runs, self.max_attempts = max_runs, max_attempts
        self.query_policy, self.inbox_sources = query_policy, dict(inbox_sources or {})
        self.handlers = dict(handlers or {})  # Trusted host handlers, not LLM code.
        store.db.executescript(DDL)

    def authority(self, owner, actor):
        authority = self.host_authority(owner, actor, self.clock.now())
        if not isinstance(authority, Authority) or (authority.owner_ref, authority.actor_ref) != (owner, actor):
            raise GeneralError('general_authority')
        self.tasks._authority(authority, self.clock.now())
        return authority

    def seal(self, owner, body):
        try:
            pointer = self.vault.seal(owner_ref=owner, plaintext=canonical(body).encode())
            self.tasks._reference(pointer_doc(pointer))
            return canonical(pointer_doc(pointer))
        except Exception:
            raise GeneralError('general_private_write') from None

    def open(self, owner, reference):
        try:
            pointer = BlobPointer(**json.loads(reference))
            self.tasks._reference(pointer_doc(pointer))
            content = self.vault.open(owner_ref=owner, pointer=pointer)
            if len(content) > 524288:
                raise ValueError
            return json.loads(content)
        except Exception:
            raise GeneralError('general_private_read') from None

    def notify(self, owner, actor, body, *, key, screen=None):
        self.authority(owner, actor)
        ref = 'notice:a' + sha256(key.encode()).hexdigest()
        with self.store.transaction():
            existing = self.store.db.execute('SELECT pointer FROM general_notifications WHERE owner=? AND ref=?', (owner, ref)).fetchone()
            if existing:
                if self.open(owner, existing[0]) != body:
                    raise ConditionalConflict('notification_replay')
                return ref
            pointer = self.seal(owner, body)
            self.store.db.execute("INSERT INTO general_notifications VALUES(?,?,?,'pending',?,?,NULL)", (owner, actor, ref, pointer, screen))
        return ref

    def proposal_notice(self, authority, proposal):
        request = proposal.document['request']
        # Private response; rendering has no active source URLs/instructions.
        return self.notify(authority.owner_ref, authority.actor_ref, {
            'text': canonical({'goal': request['goal'], 'steps': request['steps'], 'missing_fields': request['missing_fields'],
                               'budget': request['budget'], 'status': proposal.status}),
            'buttons': [{'text': b.text, 'callback_ref': b.callback_ref} for b in proposal.buttons],
        }, key=proposal.task_ref + ':' + str(proposal.version))

    def ingest(self, owner, *, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise GeneralError('general_limit')
        rows = self.store.db.execute("SELECT actor,ref,pointer,deadline,state FROM general_inputs WHERE owner=? AND state IN ('accepted','interpreting','parsed') ORDER BY ref LIMIT ?", (owner, limit)).fetchall()
        for actor, input_ref, pointer, deadline, state in rows:
            lease = self.store.acquire(owner_ref=owner,session_ref='ingress:'+input_ref.split(':',1)[1],
                worker_ref='worker:ingress-a'+uuid4().hex,now=self.clock.now(),ttl=timedelta(seconds=60))
            if lease is None:
                continue
            try:
                authority = self.authority(owner, actor)
                if parse(deadline) <= self.clock.now():
                    raise GeneralError('input_expired')
                command = self.open(owner, pointer)
                if state in ('interpreting','parsed'):
                    existing = self.store.db.execute('SELECT task FROM task_router_proposals WHERE owner=? AND actor=? AND event=?',(owner,actor,input_ref)).fetchone()
                    if existing:
                        proposal = self.tasks.get(authority=authority,task_ref=existing[0],now=self.clock.now())
                        self.proposal_notice(authority,proposal)
                        self.store.db.execute("UPDATE general_inputs SET state='done',task=? WHERE owner=? AND ref=?",(proposal.task_ref,owner,input_ref))
                        continue
                    # Parser may have run; never blindly repeat a model call.
                    raise GeneralError('interpretation_uncertain')
                name, args = command['command'], command['args']
                if name == 'callback':
                    result = self.callback(owner, actor, command['callback_ref'], event_ref=input_ref)
                    task_ref = getattr(result, 'task_ref', None)
                elif name == 'stop':
                    self.store.stop(owner_ref=owner)
                    task_ref = None
                elif name in ('start', 'mis_datos', 'borrar'):
                    # B's authenticated control route is kept, not guessed.
                    raise GeneralError('control_handler_pending')
                elif args.get('route') == 'tasks':
                    self.list_schedules(owner, actor)
                    task_ref = None
                elif name == 'pedir':
                    if self.interpreter is None and self.router.parser_enabled is not True:
                        raise GeneralError('interpreter_not_configured')
                    with self.store.transaction():
                        self.authority(owner, actor)
                        claimed = self.store.db.execute("UPDATE general_inputs SET state='interpreting' WHERE owner=? AND ref=? AND state='accepted'", (owner, input_ref)).rowcount
                        if not claimed:
                            continue
                    # Host interpreter is deterministic OR explicitly budgeted.
                    # It receives owner-bound input, never acquires Authority.
                    if self.interpreter is not None:
                        if getattr(self.interpreter,'deterministic',False) is not True:
                            raise GeneralError('interpreter_budget_adapter_required')
                        document = self.interpreter(authority=authority, input_ref=BlobPointer(**json.loads(pointer)),
                                                    text=args.get('text', ''), now=self.clock.now())
                        self.authority(owner,actor)
                        if not self.store.is_current(owner_ref=owner,lease=lease,now=self.clock.now()):
                            raise GeneralError('input_lease_lost')
                        proposal = self.router.propose(authority=authority, event_ref=input_ref, document=document, now=self.clock.now())
                    else:
                        proposal = self.router.interpret(authority=authority,event_ref=input_ref,context_refs=(json.loads(pointer),),now=self.clock.now())
                    task_ref = proposal.task_ref
                    with self.store.transaction():
                        self.authority(owner,actor)
                        if not self.store.is_current(owner_ref=owner,lease=lease,now=self.clock.now()):
                            raise GeneralError('input_lease_lost')
                        self.store.db.execute("UPDATE general_inputs SET state='parsed',task=? WHERE owner=? AND ref=?", (task_ref, owner, input_ref))
                    self.proposal_notice(authority, proposal)
                else:
                    raise GeneralError('command_unsupported')
                with self.store.transaction():
                    self.store.db.execute("UPDATE general_inputs SET state='done',task=?,reason=NULL WHERE owner=? AND ref=?", (task_ref, owner, input_ref))
            except (ConditionalConflict, GeneralError,TaskError) as error:
                code = control_reason(str(error)) if isinstance(error,(GeneralError,TaskError)) else 'authority_or_state_denied'
                if self.store.is_current(owner_ref=owner,lease=lease,now=self.clock.now()):
                    self.store.db.execute("UPDATE general_inputs SET state='blocked',reason=? WHERE owner=? AND ref=?", (code, owner, input_ref))
            except Exception:
                # Unknown storage/failpoint errors stay visible; no ACK/success fiction.
                raise GeneralError('general_ingress_failed') from None
            finally:
                self.store.release(owner_ref=owner,lease=lease)

    def callback(self, owner, actor, token, *, event_ref):
        authority = self.authority(owner, actor)
        control = self.store.db.execute('SELECT kind,pointer,used FROM general_callbacks WHERE owner=? AND actor=? AND token=?', (owner, actor, token)).fetchone()
        if control:
            if control[2]:
                raise ConditionalConflict('general_callback_used')
            value = self.open(owner, control[1])
            if control[0] == 'approve':
                self.check_batch_run(owner,actor,value['batch_ref'])
                if self.store.db.execute("SELECT 1 FROM general_notifications WHERE owner=? AND screen=? AND state!='sent' LIMIT 1", (owner, token)).fetchone():
                    raise GeneralError('approval_screen_not_delivered')
                screen = ApprovalScreen(value['batch_ref'], value['version'], value['content_hash'], value['callback_ref'], tuple(value['rows']))
                result = self.conversations.approve(authority=authority, event_ref=event_ref, screen=screen, now=self.clock.now())
                if self.contacts is not None:
                    for contact,outbound in self.store.db.execute('SELECT contact,outbound FROM general_contact_links WHERE owner=? AND batch=?',(owner,result.batch_ref)).fetchall():
                        self.contacts.contacts.note_outbound(owner_ref=owner,actor_ref=actor,operation_ref=contact,outbound_ref=outbound)
            elif control[0] == 'schedule':
                if self.scheduler is None:
                    raise GeneralError('scheduler_not_configured')
                self.scheduler.set_state(owner_ref=owner, actor_ref=actor, schedule_ref=value['schedule_ref'], state=value['state'])
                result = None
            else:
                raise GeneralError('general_callback_unsupported')
            self.store.db.execute('UPDATE general_callbacks SET used=1 WHERE owner=? AND token=?', (owner, token))
            return result
        proposal = self.router.callback(authority=authority, callback_ref=token, event_ref=event_ref, now=self.clock.now())
        if proposal.status == 'confirmed':
            self.start(authority, proposal)
        elif proposal.status in ('cancelled', 'correcting'):
            self.store.db.execute("UPDATE general_runs SET status='cancelled',reason='intent_changed',version=version+1 WHERE owner=? AND task=? AND status IN ('pending','running','awaiting_approval')", (owner, proposal.task_ref))
        self.proposal_notice(authority, proposal)
        return proposal

    def start(self, authority, proposal, *, occurrence=None, binding=None):
        self.authority(authority.owner_ref, authority.actor_ref)
        if proposal.status != 'confirmed' or proposal.confirmation_hash != proposal.content_hash:
            raise GeneralError('intent_not_confirmed')
        owner, actor = authority.owner_ref, authority.actor_ref
        occurrence = occurrence or ''
        with self.store.transaction():
            previous = self.store.db.execute('SELECT ref FROM general_runs WHERE owner=? AND task=? AND intent=? AND occurrence=?', (owner, proposal.task_ref, proposal.version, occurrence)).fetchone()
            if previous:
                return previous[0]
            if self.store.db.execute('SELECT count(*) FROM general_runs WHERE owner=?', (owner,)).fetchone()[0] >= self.max_runs:
                raise GeneralError('run_quota')
            request = proposal.document['request']
            if occurrence:
                if self.template_resolver is None or binding is None:
                    raise GeneralError('schedule_binding_required')
                self.template_resolver.resolve(binding,now=self.clock.now())
                removed = {step['step_id'] for step in request['steps'] if step['operation']=='schedule'}
                request = {**request,'steps':[{**step,'depends_on':[parent for parent in step.get('depends_on',()) if parent not in removed]} for step in request['steps'] if step['operation']!='schedule']}
                if not request['steps']:
                    raise GeneralError('scheduled_template_empty')
            run = 'run:a' + uuid4().hex
            pointer = self.seal(owner, {'request': request, 'proposal_version': proposal.version, 'proposal_hash': proposal.content_hash})
            deadline = min(authority.expires_at,self.clock.now()+timedelta(minutes=15)) if occurrence else min(authority.expires_at,proposal.expires_at)
            self.store.db.execute("INSERT INTO general_runs VALUES(?,?,?,?,?,1,'pending',?,?,0,?,?,NULL,?)", (owner, actor, run, proposal.task_ref, proposal.version, proposal.content_hash, stamp(deadline), request['budget']['calls'], pointer, occurrence))
            if occurrence:
                self.store.db.execute('INSERT INTO general_firings VALUES(?,?,?,?)',(owner,run,occurrence,canonical(asdict(binding))))
            for index, step in enumerate(request['steps']):
                self.store.db.execute("INSERT INTO general_steps VALUES(?,?,?,?,?,'pending',0,0,NULL,NULL,?)", (owner, run, step['step_id'], index, step['operation'], stamp(self.clock.now())))
            self.store.failpoint('general_run_created')
            return run

    def _live_run(self, owner, actor, run):
        authority = self.authority(owner, actor)
        row = self.store.db.execute('SELECT task,status,hash,deadline,calls,budget,pointer,occurrence,intent FROM general_runs WHERE owner=? AND actor=? AND ref=?', (owner, actor, run)).fetchone()
        if row is None or row[1] in ('cancelled','blocked') or parse(row[3]) <= self.clock.now():
            raise GeneralError('run_inactive_or_expired')
        if row[7]:
            firing = self.store.db.execute('SELECT occurrence,binding FROM general_firings WHERE owner=? AND run=?',(owner,run)).fetchone()
            if firing is None or firing[0]!=row[7] or self.scheduler is None or self.template_resolver is None:
                raise GeneralError('scheduled_run_binding')
            binding = TemplateBinding(**json.loads(firing[1]))
            self.template_resolver.resolve(binding,now=self.clock.now())
            current = self.store.db.execute("SELECT s.state,s.version,o.schedule_version,b.state FROM schedule_occurrences o JOIN schedules s ON s.owner=o.owner AND s.ref=o.schedule JOIN schedule_outbox b ON b.owner=o.owner AND b.ref=o.ref WHERE o.owner=? AND o.ref=?",(owner,row[7])).fetchone()
            if current is None or current[0]!='active' or current[1]!=current[2] or current[3]!='pending_approval':
                raise GeneralError('scheduled_run_inactive')
            proposal = self.tasks._row(owner,row[0])
        else:
            # Real A6 checks, composable inside A3's existing transaction.
            self.tasks._authority(authority,self.clock.now())
            proposal = self.tasks._view(authority,self.tasks._row(owner,row[0]),self.clock.now())
        if proposal.status != 'confirmed' or proposal.content_hash != row[2] or proposal.confirmation_hash != row[2] or proposal.version!=row[8]:
            raise GeneralError('intent_changed')
        return authority, row, self.open(owner, row[6])['request']

    def tick_schedules(self,owner,actor):
        authority = self.authority(owner,actor)
        if self.scheduler is None:
            return 0
        self.scheduler.tick(owner_ref=owner,actor_ref=actor)
        count = 0
        for _,occurrence,state,_ in self.scheduler.status(owner_ref=owner,actor_ref=actor,limit=100):
            if state not in ('pending','pending_approval'):
                continue
            intent = self.scheduler.prepare(owner_ref=owner,actor_ref=actor,occurrence_ref=occurrence)
            if intent is not None:
                proposal = self.tasks._row(owner,intent.binding.template_ref)
                self.start(authority,proposal,occurrence=occurrence,binding=intent.binding)
                count += 1
        return count

    def outputs(self, owner, run):
        rows = self.store.db.execute("SELECT id,pointer FROM general_steps WHERE owner=? AND run=? AND state IN ('succeeded','partial','awaiting_approval') AND pointer IS NOT NULL ORDER BY position", (owner, run)).fetchall()
        return {step: self.open(owner, pointer) for step, pointer in rows}

    def pump(self, owner, actor, *, max_steps=20):
        if type(max_steps) is not int or not 1 <= max_steps <= 100:
            raise GeneralError('general_limit')
        self.authority(owner, actor)
        self.ingest(owner)
        runs = self.store.db.execute("SELECT ref FROM general_runs WHERE owner=? AND actor=? AND status IN ('pending','running') ORDER BY ref LIMIT 20", (owner, actor)).fetchall()
        handled = 0
        for (run,) in runs:
            lease = self.store.acquire(owner_ref=owner, session_ref='general:' + run.split(':', 1)[1], worker_ref='worker:general-a'+uuid4().hex, now=self.clock.now(), ttl=timedelta(seconds=60))
            if lease is None:
                continue
            try:
                while handled < max_steps:
                    with self.store.transaction():
                        authority, row, request = self._live_run(owner, actor, run)
                        if not self.store.is_current(owner_ref=owner,lease=lease,now=self.clock.now()):
                            raise GeneralError('run_lease_lost')
                        steps = self.store.db.execute('SELECT id,state,version,attempt,next_due FROM general_steps WHERE owner=? AND run=? ORDER BY position', (owner, run)).fetchall()
                        states = {s[0]: s[1] for s in steps}
                        # Interrupted read-only work may retry; effects must not.
                        for step_id, state, version, attempt, due in steps:
                            if state == 'running':
                                operation = next(s['operation'] for s in request['steps'] if s['step_id'] == step_id)
                                if operation in ('contact','follow','schedule','compose'):
                                    self.store.db.execute("UPDATE general_steps SET state='blocked',reason='step_outcome_uncertain' WHERE owner=? AND run=? AND id=?", (owner, run, step_id))
                                else:
                                    self.store.db.execute("UPDATE general_steps SET state='pending',reason='recovered_read' WHERE owner=? AND run=? AND id=?", (owner, run, step_id))
                                states[step_id] = 'blocked' if operation in ('contact','follow','schedule','compose') else 'pending'
                        # Old/imported checkpoints are not allowed to turn an
                        # unknown state such as uncertain into terminal success.
                        for step_id,state in tuple(states.items()):
                            if state not in RESULT_STATES:
                                self.store.db.execute("UPDATE general_steps SET state='blocked',reason='step_state_invalid' WHERE owner=? AND run=? AND id=?",(owner,run,step_id))
                                states[step_id]='blocked'
                        selected = next((step for step in request['steps'] if states[step['step_id']] == 'pending' and all(states[p] in ('succeeded','partial') for p in step.get('depends_on', ()))
                                         and parse(next(s[4] for s in steps if s[0] == step['step_id'])) <= self.clock.now()), None)
                        if selected is None:
                            terminal = 'blocked' if any(v == 'blocked' for v in states.values()) else 'awaiting_approval' if 'awaiting_approval' in states.values() else 'running' if 'pending' in states.values() else 'partial' if 'partial' in states.values() else 'succeeded'
                            self.store.db.execute('UPDATE general_runs SET status=?,version=version+1 WHERE owner=? AND ref=?', (terminal, owner, run))
                            break
                        step_id = selected['step_id']
                        previous = next(s for s in steps if s[0] == step_id)
                        if row[4] >= row[5] or previous[3] >= self.max_attempts:
                            self.store.db.execute("UPDATE general_steps SET state='blocked',reason='budget_exhausted' WHERE owner=? AND run=? AND id=?", (owner, run, step_id))
                            continue
                        if not self.store.is_current(owner_ref=owner, lease=lease, now=self.clock.now()):
                            raise GeneralError('run_lease_lost')
                        self.store.db.execute("UPDATE general_steps SET state='running',version=version+1,attempt=attempt+1 WHERE owner=? AND run=? AND id=? AND version=?", (owner, run, step_id, previous[2]))
                        self.store.db.execute("UPDATE general_runs SET status='running',calls=calls+1,version=version+1 WHERE owner=? AND ref=?", (owner, run))
                        attempt = previous[3] + 1
                    self.store.failpoint('general_step_claimed')
                    result, state, reason = checked_step_result(self.execute(authority, run, row[0], selected, request, self.outputs(owner, run), attempt))
                    with self.store.transaction():
                        self._live_run(owner, actor, run)
                        if not self.store.is_current(owner_ref=owner, lease=lease, now=self.clock.now()):
                            raise GeneralError('run_lease_lost')
                        pointer = self.seal(owner, result)
                        self.store.db.execute('UPDATE general_steps SET state=?,pointer=?,reason=?,next_due=? WHERE owner=? AND run=? AND id=? AND state=\'running\'', (state, pointer, reason, stamp(self.clock.now() + timedelta(seconds=30) if state == 'pending' else self.clock.now()), owner, run, step_id))
                        self.store.failpoint('general_step_checkpoint')
                    handled += 1
            except (GeneralError,ConditionalConflict,TaskError,ConversationError,ContactError,ReportError,ScheduleDenied) as error:
                # A stale worker must not clobber a successor's committed run.
                if self.store.is_current(owner_ref=owner,lease=lease,now=self.clock.now()):
                    self.store.db.execute("UPDATE general_runs SET status='blocked',reason=? WHERE owner=? AND ref=?", (control_reason(str(error)) if isinstance(error,GeneralError) else 'authority_or_state_denied', owner, run))
            finally:
                self.store.release(owner_ref=owner, lease=lease)
        self.deliver(owner, actor)
        return handled

    def execute(self, authority, run, task, step, request, outputs, attempt):
        owner, actor, args, operation = authority.owner_ref, authority.actor_ref, step['arguments'], step['operation']
        self.authority(owner, actor)
        if operation in self.handlers:
            return self.handlers[operation](runtime=self, authority=authority, run_ref=run, task_ref=task, step=step, outputs=outputs)
        if operation in ('search', 'read'):
            refs = tuple(args.get('source_refs', self.source_refs))
            if not refs:
                return {'reports': []}, 'blocked', 'source_not_configured'
            # A step consumes one reserved call, therefore only one source/page.
            # Multiple sources require explicit steps/budget; never hidden fanout.
            if len(refs) != 1:
                return {'reports': []}, 'blocked', 'one_source_per_step_required'
            if operation == 'read' and refs[0] in self.inbox_sources:
                account,chat = self.inbox_sources[refs[0]]
                messages, cursor = self.conversations.list_messages(authority=authority,account_ref=account,chat_ref=chat,now=self.clock.now(),limit=20)
                records = []
                captured_bytes=0
                code=Code.UNSUPPORTED
                for message in messages:
                    self._live_run(owner,actor,run)
                    private = self.conversations.repository.vault.open(owner_ref=owner,pointer=message.private_ref)
                    if captured_bytes+len(private)>131072:
                        code=Code.LIMIT
                        break
                    data = json.loads(private)
                    if not isinstance(data.get('text'),str):
                        raise GeneralError('inbox_private_shape')
                    # Cached private incoming text remains DATA, not new steps.
                    content = data['text'].encode()
                    projection = canonical({'content_b64':b64encode(content).decode(),'contact_candidates':[],
                        'context_kind':'private_inbox','original_private_ref':pointer_doc(message.private_ref)}).encode()
                    write = self.source_sink.put(owner_ref=owner,content=projection)
                    identity=canonical((owner,account,chat,message.provider_message_ref)).encode()
                    records.append(Record('record:a'+sha256(identity).hexdigest(),owner,write.pointer,(Provenance(refs[0],1,self.clock.now()),)))
                    captured_bytes+=len(private)
                report = SourceReport('request:a'+sha256((run+step['step_id']).encode()).hexdigest(),refs[0],code,False,1,0,captured_bytes,0,tuple(records))
                return {'reports':[report.control()]}, 'partial' if records else 'blocked', 'inbox_budget_exhausted' if code==Code.LIMIT else 'live_inbox_sync_not_connected'
            query = args.get('query', '')
            if query and (self.query_policy is None or self.query_policy(owner_ref=owner,source_ref=refs[0],query=query) is not True):
                return {'reports': []}, 'blocked', 'query_privacy_unclassified'
            previous = self.store.db.execute('SELECT pointer FROM general_steps WHERE owner=? AND run=? AND id=?', (owner, run, step['step_id'])).fetchone()[0]
            checkpoint = self.open(owner, previous) if previous else {}
            resume = BlobPointer(**checkpoint['resume_ref']) if checkpoint.get('resume_ref') else None
            report = self.reader.read(ReadRequest(owner, actor, 'request:a'+sha256((run+step['step_id']).encode()).hexdigest(), refs[0], operation,
                parse(self.store.db.execute('SELECT deadline FROM general_runs WHERE owner=? AND ref=?', (owner,run)).fetchone()[0]),
                limits=Limits(pages=1, requests=1, bytes=131072, items=50), query=query,
                session_ref=args.get('session_ref'), resume_ref=resume), cancelled=lambda: self.cancelled(owner, actor, run))
            documents = checkpoint.get('reports', []) + [report.control()]
            result = {'reports': documents, 'resume_ref': pointer_doc(report.resume_ref) if report.resume_ref else None}
            if not report.complete and report.resume_ref is not None and attempt < self.max_attempts:
                return result, 'pending', report.code.value
            return result, 'succeeded' if report.complete else 'partial' if report.records or checkpoint.get('reports') else 'blocked', None if report.complete else report.code.value
        if operation == 'extract':
            return {'upstream': [key for key in outputs], 'fields': args.get('field_names', [])}, 'partial', 'deterministic_report_extraction'
        if operation == 'inform':
            records = tuple(record for output in outputs.values() for report in output.get('reports', ()) for record in source_report(report).records)
            if not records:
                return {}, 'blocked', 'no_source_material'
            builder = ReportBuilder(resolver=MaterialResolver(self, authority, run, records), access=ReportAccess(self, authority, run, records), vault=self.report_vault, clock=self.clock)
            report = builder.build(ResearchRequest(owner, actor, task, request['goal'], tuple(record.record_ref for record in records),
                (args.get('output_format','telegram'),), (), ResearchBudget(max_sources=min(64,len(records)), max_cost=Decimal(request['budget']['max_usd'])),
                parse(self.store.db.execute('SELECT deadline FROM general_runs WHERE owner=? AND ref=?',(owner,run)).fetchone()[0])))
            result = {'document_ref': pointer_doc(report.document_ref), 'status': report.status, 'reasons': list(report.reasons),
                      'artifacts': [{'format': a.format, 'private_ref': pointer_doc(a.private_ref)} for a in report.artifacts]}
            self.notify(owner, actor, result, key=run+step['step_id'])
            return result, report.status, None
        if operation == 'compose':
            if 'private_ref' not in args:
                return {}, 'blocked', 'private_drafts_required'
            private = self.open(owner, canonical(args['private_ref']))
            drafts = tuple(DraftInput((row['chat_ref'],), row['text'], row['purpose_ref']) for row in private['drafts'])
            batch = self.conversations.compose(authority=authority, event_ref='event:a'+sha256((run+step['step_id']).encode()).hexdigest(), account_ref=private['account_ref'], drafts=drafts, now=self.clock.now())
            screen = self.conversations.screen(authority=authority, batch_ref=batch.batch_ref, now=self.clock.now())
            token = self.approval_notice(authority, screen,run_ref=run)
            return {'batch_ref': batch.batch_ref, 'operation_ids': list(batch.operation_ids), 'approval_ref': token}, 'awaiting_approval', 'message_approval_required'
        if operation in ('contact', 'follow'):
            # Generic external-contact workflow is bound via A7, never raw send.
            if self.contacts is None:
                return {}, 'blocked', 'contact_resolver_not_configured'
            return self.contacts.prepare_step(runtime=self, authority=authority, run_ref=run, task_ref=task, step=step, outputs=outputs)
        if operation == 'schedule':
            if self.scheduler is None or self.template_resolver is None:
                return {}, 'blocked', 'scheduler_not_configured'
            binding, calendar = self.template_resolver.capture_confirmed(owner_ref=owner, actor_ref=actor, task_ref=task, now=self.clock.now())
            schedule = Schedule('schedule:a'+sha256((run+step['step_id']).encode()).hexdigest(), binding, calendar, binding.minimum_units)
            self.scheduler.create(schedule)
            return {'schedule_ref': schedule.schedule_ref}, 'succeeded', None
        return {}, 'blocked', 'handler_not_configured'

    def cancelled(self, owner, actor, run):
        try:
            self._live_run(owner, actor, run)
            return False
        except Exception:
            return True

    def approval_notice(self, authority, screen,*,run_ref):
        owner, actor = authority.owner_ref, authority.actor_ref
        self._live_run(owner,actor,run_ref)
        token = 'gcb:a'+uuid4().hex
        with self.store.transaction():
            previous=self.store.db.execute('SELECT run FROM general_approval_runs WHERE owner=? AND batch=?',(owner,screen.batch_ref)).fetchone()
            if previous and previous!=(run_ref,):
                raise GeneralError('approval_run_binding')
            self.store.db.execute('INSERT OR IGNORE INTO general_approval_runs VALUES(?,?,?)',(owner,screen.batch_ref,run_ref))
            value = asdict(screen)
            pointer = self.seal(owner, value)
            self.store.db.execute("INSERT INTO general_callbacks VALUES(?,?,?,'approve',?,0)", (owner, actor, token, pointer))
        # All rows/text appear in private messages; no subset-only approval.
        for index,row in enumerate(screen.rows):
            self.notify(owner, actor, {'exact_row': row}, key=token+':'+str(index), screen=token)
        self.notify(owner, actor, {'text': 'Aprobar exactamente todos los destinatarios y textos anteriores.', 'buttons': [{'text':'Aprobar lote','callback_ref':token}]}, key=token+':approve', screen=token)
        return token

    def check_batch_run(self,owner,actor,batch):
        binding=self.store.db.execute('SELECT run FROM general_approval_runs WHERE owner=? AND batch=?',(owner,batch)).fetchone()
        if binding is None:
            raise GeneralError('approval_run_binding')
        self._live_run(owner,actor,binding[0])

    def dispatch_fixture(self, owner, actor, batch_ref):
        if not self.fixture or self.fixture_dispatcher is None:
            raise GeneralError('real_dispatch_disabled')
        authority = self.authority(owner, actor)
        self.check_batch_run(owner,actor,batch_ref)
        if self.contacts is not None:
            for (contact,) in self.store.db.execute('SELECT contact FROM general_contact_links WHERE owner=? AND batch=?',(owner,batch_ref)).fetchall():
                self.contacts.contacts.export(owner_ref=owner,actor_ref=actor,operation_ref=contact)
        states = self.conversations.results(authority=authority,batch_ref=batch_ref,now=self.clock.now())
        return tuple(self.conversations.dispatch_fixture(authority=authority,operation_id=s.operation_id,now=self.clock.now(),dispatcher=self.fixture_dispatcher) for s in states)

    def list_schedules(self, owner, actor):
        if self.scheduler is None:
            raise GeneralError('scheduler_not_configured')
        rows = self.scheduler.list(owner_ref=owner,actor_ref=actor)
        buttons = []
        with self.store.transaction():
            for schedule,state,due,version,reason in rows:
                for target,label in (('paused','Pausar'),('deleted','Eliminar')):
                    token = 'gcb:a'+uuid4().hex
                    self.store.db.execute("INSERT INTO general_callbacks VALUES(?,?,?,'schedule',?,0)", (owner,actor,token,self.seal(owner,{'schedule_ref':schedule,'state':target})))
                    buttons.append({'text':label+' '+schedule,'callback_ref':token})
        self.notify(owner,actor,{'text':canonical(rows),'buttons':buttons},key='schedules:a'+uuid4().hex)
        return rows

    def deliver(self, owner, actor, *, limit=20):
        if self.delivery is None:
            return 0  # Pending delivery is observable, never claimed as sent.
        self.authority(owner,actor)
        rows = self.store.db.execute("SELECT ref,pointer FROM general_notifications WHERE owner=? AND actor=? AND state='pending' ORDER BY rowid LIMIT ?",(owner,actor,limit)).fetchall()
        for ref,pointer in rows:
            with self.store.transaction():
                self.authority(owner,actor)
                claimed = self.store.db.execute("UPDATE general_notifications SET state='dispatch_committed' WHERE owner=? AND ref=? AND state='pending'",(owner,ref)).rowcount
                if not claimed:
                    continue
                body = self.open(owner,pointer)
            self.store.failpoint('general_notice_claimed')
            try:
                self.authority(owner,actor)
                confirmed = self.delivery(owner_ref=owner,actor_ref=actor,notice_ref=ref,body=body) is True
            except Exception:
                confirmed = False
            self.store.db.execute('UPDATE general_notifications SET state=?,reason=? WHERE owner=? AND ref=?',('sent' if confirmed else 'send_uncertain',None if confirmed else 'send_uncertain',owner,ref))
        return len(rows)

    def status(self, owner, actor):
        self.authority(owner,actor)
        return self.store.db.execute('SELECT ref,task,status,calls,budget,reason FROM general_runs WHERE owner=? AND actor=? ORDER BY ref LIMIT 100',(owner,actor)).fetchall()


class MaterialResolver:
    def __init__(self,runtime,authority,run,records):
        self.runtime,self.authority,self.run,self.records = runtime,authority,run,{r.record_ref:r for r in records}
    def resolve(self,*,owner_ref,source_ref,max_bytes,max_cost):
        record = self.records.get(source_ref)
        if record is None or record.owner_ref != owner_ref or self.authority.owner_ref != owner_ref:
            raise SourceUnavailable('access_denied')
        self.runtime._live_run(owner_ref,self.authority.actor_ref,self.run)
        private = json.loads(self.runtime.source_sink.read(owner_ref=owner_ref,pointer=record.private_ref,max_bytes=max_bytes*2+4096))
        content = b64decode(private['content_b64'],validate=True)
        origin = record.provenance[0]
        return SourceMaterial(owner_ref,source_ref,dict(self.authority.capabilities).get('search','cap:read'),origin.observed_at,content,digest(content),
            'fixture' if self.runtime.fixture else 'unverified',None,record.private_ref,origins=tuple((p.source_ref,p.page,p.observed_at) for p in record.provenance),evidence_format='content_b64_json')


class ReportAccess:
    def __init__(self,runtime,authority,run,records):
        self.runtime,self.authority,self.run,self.allowed = runtime,authority,run,{r.record_ref:r for r in records}
    def check_report(self,*,owner_ref,actor_ref,task_ref,now):
        authority,row,_ = self.runtime._live_run(owner_ref,actor_ref,self.run)
        if authority.actor_ref != self.authority.actor_ref or row[0] != task_ref:
            raise GeneralError('report_binding')
    def check_source(self,*,owner_ref,actor_ref,source_ref,capability_ref,private_ref,now):
        self.runtime._live_run(owner_ref,actor_ref,self.run)
        if owner_ref != self.authority.owner_ref or actor_ref != self.authority.actor_ref or source_ref not in self.allowed:
            raise GeneralError('report_binding')
        if private_ref is not None and private_ref != self.allowed[source_ref].private_ref:
            raise GeneralError('report_binding')
    def reserve_read(self,**values):
        self.runtime._live_run(values['owner_ref'],self.authority.actor_ref,self.run)
    def check_public_url(self,**values):
        raise GeneralError('public_url_unclassified')
