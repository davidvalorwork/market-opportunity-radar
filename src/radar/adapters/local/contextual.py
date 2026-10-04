"""Contextual SQLite checkpoint + explicit A10/A9/B boundaries, not a runtime."""
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal
import json
from uuid import uuid4

from radar.application.contextual.model import (Composition, ContextMessage, ContextualError,
    EvidenceBundle, Invocation, ReplyBudget, canonical, fingerprint)
from radar.application.contextual.composer import PROMPT_VERSION, SCHEMA_NAME, SYSTEM_PROMPT
from radar.application.conversations import DraftInput
from radar.application.research.models import Coverage, SourceUnavailable
from radar.domain.core import utc
from radar.ports.types import BlobPointer
from .sqlite import parse, stamp

DDL = """
CREATE TABLE IF NOT EXISTS contextual_limits(owner TEXT,task TEXT,config TEXT,calls INTEGER,tokens INTEGER,cost TEXT,PRIMARY KEY(owner,task));
CREATE TABLE IF NOT EXISTS contextual_invocations(owner TEXT,event TEXT,ref TEXT,actor TEXT,run TEXT,task TEXT,task_version INTEGER,task_hash TEXT,binding TEXT,state TEXT,reason TEXT,pointer TEXT,stage TEXT,calls INTEGER,reserved_calls INTEGER,reserved_tokens INTEGER,reserved_cost TEXT,input_tokens INTEGER,output_tokens INTEGER,actual_cost TEXT,PRIMARY KEY(owner,event));
CREATE UNIQUE INDEX IF NOT EXISTS contextual_refs ON contextual_invocations(owner,ref);
"""


def request_binding(request):
    value = asdict(request)
    value['deadline'] = stamp(request.deadline)
    value['budget']['max_usd'] = str(request.budget.max_usd)
    return fingerprint(value)


def budget_doc(value):
    return canonical(asdict(value) | {'max_usd': str(value.max_usd)})


class SQLiteContextualStore:
    """Reserve before I/O. A claimed invocation is NEVER automatically repeated.

    Fresh access callbacks include authenticated run/lease checks owned by A16;
    no caller or model can obtain authority by supplying run_ref or owner_ref.
    """
    def __init__(self, tasks, *, authority, access, limits, clock):
        if any(not callable(port) for port in (authority, limits)) or access is None:
            raise ContextualError('trusted_checkpoint_wiring_required')
        self.tasks, self.store, self.authority = tasks, tasks.store, authority
        self.access, self.limits, self.clock = access, limits, clock
        self.store.db.executescript(DDL)

    def failpoint(self, stage):
        self.store.failpoint(stage)

    def _check(self, request, stage):
        now = utc(self.clock.now())
        if now >= request.deadline:
            raise ContextualError('composition_deadline')
        authority = self.authority(request.owner_ref, request.actor_ref, now)
        self.tasks._authority(authority, now)
        proposal = self.tasks._row(request.owner_ref, request.task_ref)
        if proposal.actor_ref != request.actor_ref or proposal.status != 'confirmed' or proposal.confirmation_hash != proposal.content_hash:
            raise ContextualError('contextual_intent_changed')
        self.tasks._plan(authority, proposal.snapshot)
        self.access.check(request, stage=stage, now=now)
        return proposal

    def _row(self, request):
        row = self.store.db.execute('SELECT ref,binding,state,reason,pointer,task_version,task_hash FROM contextual_invocations WHERE owner=? AND event=?', (request.owner_ref,request.event_ref)).fetchone()
        if row is not None and row[1] != request_binding(request):
            raise ContextualError('contextual_replay_mismatch')
        return row

    def _reserve_runtime(self, request, proposal):
        """Optional same-SQLite bridge: allocate subcalls from A16's run ceiling.

        Fresh access has already validated the host-owned lease. This does not
        pretend to account for other providers' unknown token/USD usage.
        """
        if self.store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='general_runs'").fetchone() is None:
            return  # Standalone component: only its explicitly allocated cap.
        row=self.store.db.execute('SELECT actor,task,intent,hash,deadline,status,calls,budget,version FROM general_runs WHERE owner=? AND ref=?',
            (request.owner_ref,request.run_ref)).fetchone()
        if (row is None or row[:4] != (request.actor_ref,request.task_ref,proposal.version,proposal.confirmation_hash)
                or parse(row[4]) <= utc(self.clock.now()) or row[5] not in ('pending','running','awaiting_approval')):
            raise ContextualError('runtime_contextual_binding')
        if row[6]+request.budget.calls > row[7]:
            raise ContextualError('contextual_budget_exhausted')
        changed=self.store.db.execute('UPDATE general_runs SET calls=calls+?,version=version+1 WHERE owner=? AND ref=? AND actor=? AND task=? AND intent=? AND hash=? AND deadline=? AND status=? AND version=? AND calls=?',
            (request.budget.calls,request.owner_ref,request.run_ref,*row[:6],row[8],row[6])).rowcount
        if changed != 1:
            raise ContextualError('runtime_reservation_conflict')

    def begin(self, request, *, now):
        with self.store.transaction():
            proposal = self._check(request, 'reserve')
            row = self._row(request)
            if row:
                if row[5:] != (proposal.version, proposal.confirmation_hash):
                    raise ContextualError('contextual_intent_changed')
                state, reason = (('uncertain', 'composition_checkpoint_incomplete') if row[2] == 'claimed' else row[2:4])
                return Invocation(row[0], False, Composition(row[0], state, reason,
                    None if row[4] is None else BlobPointer(**json.loads(row[4])), True))
            ceiling = self.limits(request, utc(self.clock.now()))
            if not isinstance(ceiling, ReplyBudget):
                raise ContextualError('contextual_host_budget_required')
            # A plugin's ceiling cannot expand the confirmed intention budget.
            planned = proposal.document['request']['budget']
            if planned['calls'] < 1:
                raise ContextualError('contextual_budget_exhausted')
            ceiling = replace(ceiling,calls=min(ceiling.calls,planned['calls']),
                              max_usd=min(ceiling.max_usd,Decimal(planned['max_usd'])))
            old = self.store.db.execute('SELECT config,calls,tokens,cost FROM contextual_limits WHERE owner=? AND task=?', (request.owner_ref, request.task_ref)).fetchone()
            config = budget_doc(ceiling)
            if old is None:
                self.store.db.execute('INSERT INTO contextual_limits VALUES(?,?,?,0,0,?)', (request.owner_ref, request.task_ref, config, '0'))
                old = (config, 0, 0, '0')
            if old[0] != config:
                raise ContextualError('contextual_budget_policy_changed')
            # Literal responses use no LLM/query API. Private I/O remains
            # bounded, but neither tokens nor API dollars are fabricated.
            tokens=request.budget.tokens if request.mode == 'reasoned' else 0
            dollars=request.budget.max_usd if request.mode == 'reasoned' or request.query_ref is not None else Decimal(0)
            if (old[1]+request.budget.calls > ceiling.calls or old[2]+tokens > ceiling.tokens
                    or Decimal(old[3])+dollars > ceiling.max_usd
                    or request.budget.input_bytes > ceiling.input_bytes or request.budget.output_tokens > ceiling.output_tokens
                    or request.budget.sources > ceiling.sources):
                raise ContextualError('contextual_budget_exhausted')
            self._reserve_runtime(request,proposal)
            self.store.db.execute('UPDATE contextual_limits SET calls=?,tokens=?,cost=? WHERE owner=? AND task=?',
                (old[1]+request.budget.calls, old[2]+tokens, str(Decimal(old[3])+dollars), request.owner_ref, request.task_ref))
            ref = 'composition:'+uuid4().hex
            self.store.db.execute('INSERT INTO contextual_invocations VALUES(?,?,?,?,?,?,?,?,?,\'claimed\',NULL,NULL,\'reserved\',0,?,?,?,NULL,NULL,NULL)',
                (request.owner_ref, request.event_ref, ref, request.actor_ref, request.run_ref, request.task_ref,
                 proposal.version, proposal.confirmation_hash, request_binding(request), request.budget.calls, tokens, str(dollars)))
            self.failpoint('after_contextual_reservation')
        self.failpoint('after_contextual_reservation_commit')
        return Invocation(ref, True, Composition(ref, 'claimed'))

    def stage(self, request, invocation, stage):
        with self.store.transaction():
            proposal = self._check(request, stage)
            row = self._row(request)
            if row is None or (invocation is not None and row[0] != invocation.reference) or row[2] != 'claimed':
                raise ContextualError('contextual_claim_changed')
            if row[5:] != (proposal.version,proposal.confirmation_hash):
                raise ContextualError('contextual_intent_changed')
            updated = self.store.db.execute('UPDATE contextual_invocations SET stage=?,calls=calls+1 WHERE owner=? AND event=? AND calls<reserved_calls AND state=\'claimed\'', (stage,request.owner_ref,request.event_ref)).rowcount
            if updated != 1:
                raise ContextualError('contextual_call_budget_exhausted')

    def finish(self, request, invocation, *, state, reason=None, pointer=None, usage=None):
        if state not in ('prepared', 'partial', 'blocked', 'uncertain'):
            raise ContextualError('invalid_checkpoint_state')
        with self.store.transaction():
            proposal = self._check(request, 'checkpoint')
            row = self._row(request)
            if row is None or row[0] != invocation.reference or row[2] != 'claimed':
                raise ContextualError('contextual_claim_changed')
            if row[5:] != (proposal.version,proposal.confirmation_hash):
                raise ContextualError('contextual_intent_changed')
            self.store.db.execute('UPDATE contextual_invocations SET state=?,reason=?,pointer=?,input_tokens=?,output_tokens=?,actual_cost=? WHERE owner=? AND event=?',
                (state, reason, None if pointer is None else canonical(asdict(pointer)),
                 None if usage is None else usage.input_tokens, None if usage is None else usage.output_tokens,
                 None if usage is None or usage.cost is None else str(usage.cost), request.owner_ref, request.event_ref))
            self.failpoint('after_contextual_result')
        return Composition(invocation.reference, state, reason, pointer)

    def read_stage(self, request, stage):
        """Bounded private handoff reads against the original reservation."""
        with self.store.transaction():
            proposal = self._check(request,stage)
            row = self._row(request)
            if row is None or row[2] not in ('prepared','partial') or row[5:] != (proposal.version,proposal.confirmation_hash):
                raise ContextualError('contextual_draft_not_ready')
            if self.store.db.execute('UPDATE contextual_invocations SET stage=?,calls=calls+1 WHERE owner=? AND event=? AND calls<reserved_calls',
                    (stage,request.owner_ref,request.event_ref)).rowcount != 1:
                raise ContextualError('contextual_call_budget_exhausted')


class EnabledConversationContext:
    """Exact stable provider ref in enabled A10 chat; no automatic chat enrollment."""
    def __init__(self, conversations, *, authority):
        self.repository, self.authority = conversations, authority

    def resolve(self, request, *, now):
        repo = self.repository
        authority = self.authority(request.owner_ref, request.actor_ref, now)
        with repo.store.transaction():
            account = repo._check(authority, request.account_ref, 'read', now)
            recipient, _, _ = repo._chat(request.owner_ref, request.account_ref, request.chat_ref)
            row = repo.store.db.execute('SELECT pointer FROM conversation_messages WHERE owner=? AND account=? AND chat=? AND message=?',
                (request.owner_ref,request.account_ref,request.chat_ref,request.provider_message_ref)).fetchone()
            if row is None:
                raise ContextualError('context_message_ambiguous_or_missing')
            pointer = BlobPointer(**json.loads(row[0]))
            body = repo._open(request.owner_ref, pointer)
            if (body.get('chat_ref'), body.get('recipient_ref'), body.get('provider_message_ref')) != (request.chat_ref, recipient, request.provider_message_ref):
                raise ContextualError('context_message_binding')
            if not isinstance(body.get('text'), str) or not isinstance(body.get('observed_at'), str):
                raise ContextualError('context_text_or_timestamp_missing')
            repo._check(self.authority(request.owner_ref, request.actor_ref, now), request.account_ref, 'read', repo.clock.now())
            return ContextMessage(request.owner_ref,request.account_ref,request.chat_ref,request.provider_message_ref,
                recipient,account.session_ref,account.session_version,pointer,body['text'],parse(body['observed_at']))


class CapturedResearch:
    """Explicit A9 resolver, optionally an authorized A8 query→capture boundary.

    Query runner receives a minimal reviewed string, never chat context. It must
    obey the supplied budget/deadline, charge each external I/O before execution,
    and return (new source refs, Decimal actual API cost or None).
    """
    def __init__(self, resolver, state, *, query_runner=None):
        self.resolver, self.state, self.query_runner = resolver, state, query_runner

    def collect(self, request, *, query, now):
        refs, cost = request.evidence_refs, Decimal(0)  # No new external API call.
        if query is not None:
            if self.query_runner is None:
                raise ContextualError('research_backend_not_configured')
            self.state.stage(request, None, 'public_query')
            refs, cost = self.query_runner(request=request, query=query,
                reserve=lambda: self.state.stage(request, None, 'source_io'))
            if type(refs) is not tuple or len(refs) > request.budget.sources or len(set(refs)) != len(refs):
                raise ContextualError('research_capture_limit')
        materials, coverage, remaining = [], [], request.budget.input_bytes
        for ref in refs:
            self.state.stage(request, None, 'capture_read')
            try:
                material = self.resolver.resolve(owner_ref=request.owner_ref, source_ref=ref,
                    max_bytes=remaining, max_cost=request.budget.max_usd)
            except SourceUnavailable as error:
                coverage.append(Coverage(ref,'failed',error.args[0]))
                continue
            if len(material.content) > remaining:
                coverage.append(Coverage(ref,'skipped','input_quota'))
                break
            remaining -= len(material.content)
            materials.append(material)
            coverage.append(Coverage(ref,'read',observed_at=material.observed_at,content_hash=material.content_hash,provenance=material.provenance))
        return EvidenceBundle(tuple(materials),tuple(coverage),len(materials)==len(refs),cost)


def build_reasoner(*, secrets, cache, clock, prices, transport, enabled=False, allowed_models=()):
    """Existing B client, candidate prompt injection; default remains disabled.

    Candidate schema/prompt and personal ZDR/model endpoint require B review and
    explicit host authorization before live. No client/catalog duplication.
    """
    from radar.adapters.openrouter.client import OpenRouterLLM, Prompt
    return OpenRouterLLM(secrets=secrets,cache=cache,clock=clock,prices=prices,
        transport=transport,enabled=enabled,allowed_models=allowed_models,
        prompts={PROMPT_VERSION:Prompt(SYSTEM_PROMPT,SCHEMA_NAME,1)}, price_cap=True)


class ContextualHandoff:
    def __init__(self, composer, *, service, context, vault, authority):
        self.composer, self.service, self.context, self.vault, self.authority = composer, service, context, vault, authority

    def draft(self, request, result):
        if result.state not in ('prepared','partial') or result.private_ref is None:
            raise ContextualError('contextual_draft_not_ready')
        self.composer._check(request, 'handoff')
        # Reload durable result: an arbitrary Composition supplied by caller is
        # never accepted as evidence or an event binding.
        persisted = self.composer.state.begin(request, now=utc(self.composer.clock.now())).result
        if (persisted.invocation_ref,persisted.private_ref,persisted.state) != (result.invocation_ref,result.private_ref,result.state):
            raise ContextualError('contextual_result_binding')
        self.composer.state.read_stage(request,'handoff_result_read')
        body = json.loads(self.vault.open(owner_ref=request.owner_ref,pointer=result.private_ref))
        self.composer.state.read_stage(request,'handoff_context_read')
        current = self.context.resolve(request,now=utc(self.composer.clock.now()))
        expected = body['context']
        if body['scope'] != request.scope or (body['run_ref'],body['task_ref']) != (request.run_ref,request.task_ref) or any(expected[k] != getattr(current,k) for k in ('recipient_ref','session_ref','session_version')) or expected['private_ref'] != asdict(current.private_ref):
            raise ContextualError('contextual_context_changed')
        self.composer._check(request,'handoff_private_read')
        # Stable host-owned invocation identifier, not fake user message/approval.
        event = 'event:contextual_'+result.invocation_ref.split(':',1)[1]
        authority = self.authority(request.owner_ref,request.actor_ref,self.composer.clock.now())
        self.composer.state.read_stage(request,'handoff_draft')
        return self.service.compose(authority=authority,event_ref=event,account_ref=request.account_ref,
            drafts=(DraftInput((request.chat_ref,),body['text'],request.purpose_ref),),now=self.composer.clock.now())


def compose_handler(handoff, *, request_factory, notice=None):
    """Small A16 handler hook. No runtime import, scheduler or autoapproval."""
    def handle(*, runtime, authority, run_ref, task_ref, step, outputs):
        request = request_factory(authority=authority,run_ref=run_ref,task_ref=task_ref,step=step,outputs=outputs)
        if (request.owner_ref,request.actor_ref,request.run_ref,request.task_ref) != (authority.owner_ref,authority.actor_ref,run_ref,task_ref):
            raise ContextualError('runtime_contextual_binding')
        try:
            result = handoff.composer.compose(request)
        except ContextualError as error:
            # Only this admission denial is known to precede all I/O. Other
            # conflicts, bugs, crashes and mid-call failures remain visible.
            if error.args != ('contextual_budget_exhausted',):
                raise
            return {'state':'blocked','reason':'contextual_budget_exhausted'},'blocked','contextual_budget_exhausted'
        document = {'invocation_ref':result.invocation_ref,'state':result.state,'reason':result.reason,
                    'private_ref':None if result.private_ref is None else asdict(result.private_ref)}
        if result.state in ('prepared','partial'):
            batch = handoff.draft(request,result)
            document['batch_ref'] = batch.batch_ref
            if notice is not None:
                notice(runtime=runtime,authority=authority,batch=batch,run_ref=run_ref)
            return document,'awaiting_approval','exact_message_approval_required'
        if result.state == 'uncertain':
            # A16's step state machine has no uncertain terminal: returning it
            # verbatim would misclassify the run as succeeded. Keep uncertainty
            # in the encrypted document and block further automatic work.
            return document,'blocked','contextual_outcome_uncertain'
        return document,result.state,result.reason
    return handle
