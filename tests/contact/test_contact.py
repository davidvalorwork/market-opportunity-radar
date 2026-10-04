"""Real A6/A8/SQLite with synthetic private backends; no network or sends."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3
from uuid import uuid4

import pytest

from radar import contracts
from radar.adapters.local.contact import LocalContacts
from radar.adapters.local.conversations import SQLiteConversations
from radar.adapters.local.actions import SimulatedSender
from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.tasks import SQLiteTaskStore
from radar.adapters.local.telegram import Directory
from radar.adapters.sources.generic.engine import Reader, Registry
from radar.adapters.sources.generic.fixtures import (FixtureAccess, FixtureCapabilities,
    FixtureTransport, MemoryPrivateSink, fixture_page)
from radar.adapters.sources.generic.model import RawItem, ReadOperation, ReadRequest, SourceSpec
from radar.adapters.telegram.consent import CONSENT_VERSION
from radar.application.contact.model import (ContactError, ResolvedContact,
    ResolutionBinding, ResponseObservation, compare_quotes, declared_quote, render)
from radar.application.tasks import Authority, OperationRegistry, TaskRouter
from radar.application.conversations import Account, Channel, Conversations, DraftInput
from radar.domain.core import FxRate, Money
from radar.ports.types import Approval, BlobPointer, Capability, ConditionalConflict, LedgerState


NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


class SyntheticVault:
    """Memory-only private fixture; pointers are not proof of actual encryption."""
    def __init__(self, scope):
        self.scope, self.content = scope, {}
        self.fail = False

    def seal(self, *, owner_ref, plaintext):
        if self.fail:
            raise RuntimeError('synthetic_vault_failure')
        pointer = BlobPointer('fixture/'+uuid4().hex+'.age', sha256(b'fake:'+plaintext).hexdigest(), self.scope)
        self.content[(owner_ref, pointer)] = plaintext
        return pointer

    def open(self, *, owner_ref, pointer):
        return self.content[(owner_ref, pointer)]


class IndependentResolution:
    def __init__(self):
        self.proofs = {}

    def resolve(self, *, binding, proof_ref, now):
        proof = self.proofs.get(proof_ref)
        if proof is None:
            raise ContactError('resolution_approval_required')
        return proof


class IndependentObservations:
    def __init__(self):
        self.proofs = {}

    def resolve(self, *, owner_ref, operation_ref, proof_ref, now):
        return self.proofs[proof_ref]


def source_reports(store, sink, *, owner='owner:alpha', actor='user:alpha', topic='astronomy', count=2):
    spec = SourceSpec('source:one', 'fixture', 'memory', 'fixture',
                      (ReadOperation('search', 'fixture_search'),), min_interval_seconds=0)
    items = tuple(RawItem(json.dumps({'facts': {'topic': topic, 'name': 'Synthetic '+str(index)},
            'phone': '+1 202 555 014'+str(index)}).encode(), 'https://example.org/'+topic+'/'+str(index)) for index in range(count))
    reader = Reader(registry=Registry((spec,)), capabilities=FixtureCapabilities(NOW),
        access=FixtureAccess(), private_sink=sink,
        transports={'fixture': FixtureTransport({'source:one': (fixture_page(items),)})}, now=lambda: NOW)
    return (reader.read(ReadRequest(owner, actor, 'request:read', 'source:one', 'search', NOW+timedelta(minutes=1))),)


@pytest.fixture
def env(tmp_path):
    store = SQLiteStore(tmp_path/'contact.sqlite')
    directory = Directory(store)
    caps = tuple((name, 'cap:'+name) for name in OperationRegistry().names)
    for index, owner in enumerate(('alpha', 'beta'), 1):
        directory.enroll_synthetic(index, owner_ref='owner:'+owner, actor_ref='user:'+owner)
        directory.accept_consent('user:'+owner, CONSENT_VERSION, NOW, ('consent:'+owner,))
        for _, capability in caps:
            store.allow_source(owner_ref='owner:'+owner, source_ref=capability, authorized=True)
        store.allow_source(owner_ref='owner:'+owner, source_ref='source:one', authorized=True)
        store.db.execute('INSERT INTO sessions VALUES(?,?,1)', ('owner:'+owner, 'fixture:local'))
    state = {'now': NOW, 'expires': NOW+timedelta(days=90)}
    def host(owner, actor, now):
        return Authority(owner, actor, caps, state['expires'], sessions=(('fixture:local', 1),))
    tasks = SQLiteTaskStore(store, vault=SyntheticVault('worker:task-router'))
    router = TaskRouter(tasks, validate_document=contracts.validate)
    source_sink = MemoryPrivateSink()
    vault = SyntheticVault('worker:contact')
    resolver, observations = IndependentResolution(), IndependentObservations()
    contacts = LocalContacts(tasks, source_sink=source_sink, vault=vault, host_authority=host,
                             resolver=resolver, observations=observations, clock=lambda: state['now'])
    yield store, tasks, router, host, state, source_sink, vault, resolver, observations, contacts
    store.close()


def confirmed(env, owner='alpha', event='event:task'):
    _, _, router, host, state, _, _, _, _, _ = env
    authority = host('owner:'+owner, 'user:'+owner, state['now'])
    document = {'schema_version': 1, 'goal': 'Find public contacts for any topic', 'privacy_scope': 'public',
        'steps': [{'step_id': 'find', 'operation': 'search', 'arguments': {'query': 'topic fixture'}},
                  {'step_id': 'compose', 'operation': 'compose', 'depends_on': ['find'], 'arguments': {}}],
        'missing_fields': [], 'confidence': 1, 'budget': {'calls': 2, 'message_limit': 0, 'max_usd': '0.10'}}
    proposed = router.propose(authority=authority, event_ref=event, document=document, now=state['now'])
    callback = next(button.callback_ref for button in proposed.buttons if button.text == 'Confirmar')
    return router.callback(authority=authority, callback_ref=callback, event_ref=event+':confirm', now=state['now'])


def start(env, *, owner='alpha', event='event:start', topic='astronomy', count=2, **kwargs):
    proposal = confirmed(env, owner, 'event:task:'+event.split(':')[-1])
    reports = source_reports(env[0], env[5], owner='owner:'+owner, actor='user:'+owner, topic=topic, count=count)
    kwargs.setdefault('followup_purpose_ref', 'purpose:information_followup')
    view = env[9].start(owner_ref='owner:'+owner, actor_ref='user:'+owner, event_ref=event,
        task_ref=proposal.task_ref, reports=reports, purpose_ref='purpose:information',
        template='Hola {name}, ¿puede compartir información sobre {topic}? {question}',
        request_facts={'question': 'Consulta sintética, no enviar.'}, **kwargs)
    return proposal, reports, view


def prepare(env, view, index=0, event='event:prepare', **kwargs):
    return env[9].prepare(owner_ref='owner:alpha', actor_ref='user:alpha', campaign_ref=view.campaign_ref,
                         candidate_ref=view.candidate_refs[index], event_ref=event, **kwargs)


def approved_resolution(env, prepared, *, proof='proof:resolve', recipient='recipient:synthetic'):
    identity = BlobPointer('fixture/identity.age', 'a'*64, 'worker:conversations')
    bind = ResolutionBinding('owner:alpha', 'user:alpha', prepared.operation_ref,
         prepared.candidate_ref, prepared.purpose_ref, prepared.private_ref, prepared.content_hash)
    result = ResolvedContact(bind, 'synthetic', 'account:synthetic', 'chat:synthetic', recipient,
                            'fixture:local', 1, identity, NOW+timedelta(days=60))
    env[7].proofs[proof] = result
    return proof, result


@pytest.mark.parametrize('topic', ['astronomy', 'garden', 'books', 'jobs', 'services'])
def test_real_a6_a8_multitopic_shortlist_and_private_preparation(env, topic):
    _, reports, view = start(env, topic=topic, maximum=1)
    assert reports[0].complete and len(view.candidate_refs) == 1 and view.truncated
    draft = prepare(env, view)
    assert draft.state == 'pending_resolution_approval'
    packet = json.loads(env[6].open(owner_ref='owner:alpha', pointer=draft.private_ref))
    assert topic in packet['text'] and packet['candidate']['verified'] is False
    assert 'whatsapp' not in packet and packet['candidate']['evidence'][0]['source_ref'] == 'source:one'
    for table in ('ledger', 'outbox', 'queue', 'provider_proofs'):
        assert env[0].db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0


def test_resolution_approval_not_a6_confirmation_and_export_has_no_effect(env):
    _, _, view = start(env)
    draft = prepare(env, view)
    with pytest.raises(ContactError, match='resolution_approval_required'):
        env[9].export(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref)
    proof, _ = approved_resolution(env, draft)
    env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=proof)
    intent = env[9].export(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref)
    assert intent.purpose_ref == 'purpose:information'
    assert sha256(intent.text.encode()).hexdigest() == draft.content_hash
    assert env[0].db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0] == 0


def test_replay_restart_and_owner_bound_events(env):
    store, tasks, _, host, state, sink, vault, resolver, observations, contacts = env
    proposal, reports, view = start(env)
    replay = contacts.start(owner_ref='owner:alpha', actor_ref='user:alpha', event_ref='event:start',
        task_ref=proposal.task_ref, reports=reports, purpose_ref='purpose:information',
        template='Hola {name}, ¿puede compartir información sobre {topic}? {question}',
        request_facts={'question': 'Consulta sintética, no enviar.'}, followup_purpose_ref='purpose:information_followup')
    assert replay == view
    draft = prepare(env, view)
    assert prepare(env, view) == draft
    second = SQLiteStore(store.path)
    try:
        restarted_tasks = SQLiteTaskStore(second, vault=tasks.vault)
        restarted = LocalContacts(restarted_tasks, source_sink=sink, vault=vault, host_authority=host,
            resolver=resolver, observations=observations, clock=lambda: state['now'])
        assert restarted.prepare(owner_ref='owner:alpha', actor_ref='user:alpha', campaign_ref=view.campaign_ref,
            candidate_ref=view.candidate_refs[0], event_ref='event:prepare') == draft
        with pytest.raises(ContactError, match='campaign_unknown'):
            restarted.prepare(owner_ref='owner:beta', actor_ref='user:beta', campaign_ref=view.campaign_ref,
                candidate_ref=view.candidate_refs[0], event_ref='event:prepare')
    finally:
        second.close()


@pytest.mark.parametrize('field', ['owner_ref', 'actor_ref', 'operation_ref', 'candidate_ref', 'purpose_ref', 'content_hash'])
def test_resolution_negative_binding_fields(env, field):
    _, _, view = start(env)
    draft = prepare(env, view)
    proof, response = approved_resolution(env, draft)
    wrong = 'b'*64 if field == 'content_hash' else 'other:opaque'
    env[7].proofs[proof] = replace(response, binding=replace(response.binding, **{field: wrong}))
    with pytest.raises(ContactError, match='contact_resolution_binding'):
        env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=proof)
    assert env[0].db.execute('SELECT state,version FROM contact_operations').fetchone() == ('pending_resolution_approval', 1)


def test_resolution_replay_cannot_change_recipient(env):
    _, _, view = start(env)
    draft = prepare(env, view)
    proof, response = approved_resolution(env, draft)
    env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=proof)
    env[7].proofs[proof] = replace(response, recipient_ref='recipient:other')
    with pytest.raises(ContactError, match='contact_resolution_replay'):
        env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=proof)
    with pytest.raises(ContactError, match='contact_resolution_replay'):
        env[9].export(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref)


@pytest.mark.parametrize('change', ['revoke', 'consent', 'role', 'stop', 'source', 'capability', 'expiry', 'cancel'])
def test_fresh_gates_after_prepare(env, change):
    store, _, _, _, state, _, _, _, _, contacts = env
    _, _, view = start(env)
    draft = prepare(env, view)
    proof, _ = approved_resolution(env, draft)
    if change == 'revoke':
        store.revoke(owner_ref='owner:alpha', actor_ref='user:alpha')
    elif change == 'consent':
        store.db.execute('UPDATE directory SET consent_version=NULL WHERE owner=\'owner:alpha\'')
    elif change == 'role':
        store.db.execute('UPDATE directory SET role=\'collaborator\' WHERE owner=\'owner:alpha\'')
    elif change == 'stop':
        store.stop(owner_ref='owner:alpha')
    elif change == 'source':
        store.allow_source(owner_ref='owner:alpha', source_ref='source:one', authorized=False)
    elif change == 'capability':
        store.allow_source(owner_ref='owner:alpha', source_ref='cap:contact', authorized=False)
    elif change == 'expiry':
        state['expires'] = NOW
    else:
        contacts.cancel(owner_ref='owner:alpha', actor_ref='user:alpha', campaign_ref=view.campaign_ref)
    with pytest.raises((ContactError, ConditionalConflict)):
        contacts.resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=proof)
    assert store.db.execute('SELECT state FROM contact_operations').fetchone()[0] == 'pending_resolution_approval'


@pytest.mark.parametrize('action', ['Corregir', 'Cancelar'])
def test_a6_change_invalidates_campaign(env, action):
    _, _, router, host, _, _, _, _, _, _ = env
    proposal, _, view = start(env)
    token = next(button.callback_ref for button in proposal.buttons if button.text == action)
    router.callback(authority=host('owner:alpha', 'user:alpha', NOW), callback_ref=token, event_ref='event:change', now=NOW)
    with pytest.raises(ContactError, match='campaign_intent_changed'):
        prepare(env, view)


def test_dedupe_configurable_per_purpose_and_budget_reservation_not_send(env):
    _, _, view = start(env)
    first, second = prepare(env, view), prepare(env, view, 1, 'event:second')
    for draft, proof in ((first, 'proof:first'), (second, 'proof:second')):
        approved_resolution(env, draft, proof=proof)
    env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=first.operation_ref, proof_ref='proof:first')
    with pytest.raises(ContactError, match='contact_purpose_duplicate'):
        env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=second.operation_ref, proof_ref='proof:second')


@pytest.mark.parametrize('stage', ['contact_prepared', 'before_commit'])
def test_atomic_preparation_rollback_and_replay(env, stage):
    store, _, _, _, _, _, _, _, _, _ = env
    _, _, view = start(env)
    def fail(point):
        if point == stage:
            raise RuntimeError('synthetic_crash')
    store.failpoint = fail
    with pytest.raises(RuntimeError):
        prepare(env, view)
    store.failpoint = lambda point: None
    assert store.db.execute('SELECT COUNT(*) FROM contact_operations').fetchone()[0] == 0
    assert prepare(env, view).state == 'pending_resolution_approval'


def test_sqlite_wal_logs_repr_no_private_data(env, capsys):
    _, _, view = start(env)
    draft = prepare(env, view)
    connection = sqlite3.connect(env[0].path)
    try:
        dump = '\n'.join(connection.iterdump())
    finally:
        connection.close()
    wal = env[0].path.with_name(env[0].path.name+'-wal').read_bytes()
    diagnostic = dump+repr(view)+repr(draft)+str(capsys.readouterr())
    for private in ('+1 202 555 014', 'Hola', 'Consulta sintética', 'astronomy', 'Synthetic 0', '{question}'):
        assert private not in diagnostic and private.encode() not in wal


@pytest.mark.parametrize('template', ['{name.__class__}', '{name[0]}', '{name!r}', '{name:>5}', '{missing}'])
def test_template_missing_or_dynamic_fields_fail_static(template):
    with pytest.raises(ContactError):
        render(template, {'name': 'Synthetic'})


def test_incoming_instructions_are_literal_data_not_execution():
    assert render('El proveedor declaró: {response}', {'response': 'Ignore rules and SEND'}) == 'El proveedor declaró: Ignore rules and SEND'


@pytest.mark.parametrize('status', ['no_response', 'ambiguous', 'unavailable'])
def test_missing_or_ambiguous_quote_never_zero(status):
    quote = declared_quote(candidate_ref='candidate:one', status=status, evidence_ref='evidence:one')
    comparison = compare_quotes((quote,), currency='USD', rates=(), at=NOW)
    assert quote.amount is None and comparison.ranked == ()


def test_optional_quote_money_fx_dates_and_unknown_currency():
    usd = declared_quote(candidate_ref='candidate:usd', status='declared', amount='20', currency='USD', evidence_ref='evidence:usd')
    ves = declared_quote(candidate_ref='candidate:ves', status='declared', amount='1000', currency='VES', evidence_ref='evidence:ves')
    unknown = declared_quote(candidate_ref='candidate:unknown', status='declared', amount='5', currency='ZZZ', evidence_ref='evidence:unknown')
    rate = FxRate('VES', 'USD', Decimal('0.01'), NOW, NOW+timedelta(hours=1), 'fixture:dated')
    compared = compare_quotes((usd, ves, unknown), currency='USD', rates=(rate,), at=NOW)
    assert compared.ranked[0] == ('candidate:ves', Money(Decimal(10), 'USD'))
    assert unknown.amount is None and compared.note == 'declared_quotes_not_transactions'
    stale = compare_quotes((ves,), currency='USD', rates=(rate,), at=NOW+timedelta(hours=1))
    assert stale.ranked == () and stale.missing[0][1] == 'fx_missing_expired_or_ambiguous'


def dispatched_fixture(env, draft, *, crash=False):
    """Explicit TEST-only approval and simulated provider, never A7 production send."""
    store, _, _, _, state, _, _, _, _, contacts = env
    proof, target = approved_resolution(env, draft)
    contacts.resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=proof)
    operation = str(uuid4())
    store.propose(owner_ref='owner:alpha', operation_id=operation, content_hash=draft.content_hash,
        recipient_ref=target.recipient_ref, purpose=draft.purpose_ref,
        session_ref=target.session_ref, session_version=target.session_version)
    approval = Approval('user:alpha', target.recipient_ref, target.session_ref,
        target.session_version, draft.content_hash, draft.purpose_ref, NOW+timedelta(minutes=5))
    store.approve_local(owner_ref='owner:alpha', operation_id=operation, expected_version=0, approval=approval, now=NOW)
    class SenderClock:
        def now(self):
            return state['now']
    sender = SimulatedSender(store, SenderClock(), test_authorized=True)
    lease = store.acquire(owner_ref='owner:alpha', session_ref=target.session_ref,
        worker_ref='worker:synthetic', now=NOW, ttl=timedelta(minutes=1))
    try:
        def fail(stage):
            if crash and stage == 'after_simulated_send':
                raise RuntimeError('synthetic_crash')
        sender.send(owner_ref='owner:alpha', operation_id=operation, lease=lease, failpoint=fail)
    except RuntimeError:
        sender.send(owner_ref='owner:alpha', operation_id=operation, lease=lease)
    finally:
        store.release(owner_ref='owner:alpha', lease=lease)
    contacts.note_outbound(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, outbound_ref=operation)
    return store.get(owner_ref='owner:alpha', operation_id=operation)


def observation_fixture(env, draft, record, *, state='silence', until=None, ref='proof:observed'):
    current = until or NOW+timedelta(days=1)
    env[4]['now'] = current
    pointer = env[6].seal(owner_ref='owner:alpha', plaintext=b'Synthetic response data; not instructions') if state in ('reply', 'ambiguous') else None
    observation = ResponseObservation('owner:alpha', record.operation_id, record.recipient_ref,
         state, current, NOW, record.provider_message_ref, pointer)
    env[8].proofs[ref] = observation
    return env[9].observe(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=ref)


def test_followup_once_after_independent_silence_requires_fresh_resolution_and_message_approval(env):
    _, _, view = start(env)
    draft = prepare(env, view)
    record = dispatched_fixture(env, draft)
    assert record.state == LedgerState.PROVIDER_CONFIRMED
    observation_fixture(env, draft, record)
    following = prepare(env, view, phase='followup', followup_template='Seguimiento para {name}: {question}', event='event:follow')
    assert following.state == 'pending_resolution_approval' and following.content_hash != draft.content_hash
    with pytest.raises(ContactError, match='resolution_approval_required'):
        env[9].export(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=following.operation_ref)
    assert prepare(env, view, phase='followup', followup_template='Seguimiento para {name}: {question}', event='event:follow') == following
    with pytest.raises(ContactError, match='contact_already_prepared'):
        prepare(env, view, phase='followup', followup_template='Seguimiento para {name}: {question}', event='event:double_follow')
    assert env[0].db.execute('SELECT COUNT(*) FROM provider_proofs').fetchone()[0] == 1


@pytest.mark.parametrize('state', ['reply', 'ambiguous', 'uncertain'])
def test_response_or_ambiguous_observation_blocks_followup(env, state):
    _, _, view = start(env)
    draft = prepare(env, view)
    record = dispatched_fixture(env, draft)
    observation_fixture(env, draft, record, state=state)
    with pytest.raises(ContactError, match='followup_uncertain_or_response'):
        prepare(env, view, phase='followup', followup_template='Seguimiento {question}', event='event:follow')


def test_uncertain_send_never_becomes_silence_or_blind_followup(env):
    _, _, view = start(env)
    draft = prepare(env, view)
    record = dispatched_fixture(env, draft, crash=True)
    assert record.state == LedgerState.SEND_UNCERTAIN
    with pytest.raises(ContactError, match='outbound_uncertain_or_unconfirmed'):
        observation_fixture(env, draft, record)
    with pytest.raises(ContactError, match='followup_silence_proof_required'):
        prepare(env, view, phase='followup', followup_template='Seguimiento {question}', event='event:follow')
    assert env[0].db.execute('SELECT COUNT(*) FROM provider_proofs').fetchone()[0] == 1


def test_followup_wait_period_explicit_and_old_silence_cannot_replace_reply(env):
    _, _, view = start(env, silence_seconds=3600)
    draft = prepare(env, view)
    record = dispatched_fixture(env, draft)
    observation_fixture(env, draft, record, until=NOW+timedelta(minutes=10))
    with pytest.raises(ContactError, match='followup_silence_too_short'):
        prepare(env, view, phase='followup', followup_template='Seguimiento {question}', event='event:follow')
    observation_fixture(env, draft, record, state='reply', ref='proof:reply', until=NOW+timedelta(hours=2))
    with pytest.raises(ContactError, match='response_observation_stale'):
        env[9].observe(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref='proof:observed')


@pytest.mark.parametrize('field', ['owner_ref', 'operation_ref', 'recipient_ref', 'provider_message_ref'])
def test_independent_response_negative_binding(env, field):
    _, _, view = start(env)
    draft = prepare(env, view)
    record = dispatched_fixture(env, draft)
    until = NOW+timedelta(days=1)
    env[4]['now'] = until
    observation = ResponseObservation('owner:alpha', record.operation_id, record.recipient_ref,
                                     'silence', until, NOW, record.provider_message_ref)
    env[8].proofs['proof:bad'] = replace(observation, **{field: 'other:opaque'})
    with pytest.raises(ContactError, match='response_observation_binding'):
        env[9].observe(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref='proof:bad')


def test_missing_resolver_cannot_invent_whatsapp_or_unreadable_vault_success(env):
    _, _, view = start(env)
    draft = prepare(env, view)
    with pytest.raises(ContactError, match='resolution_approval_required'):
        env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref='proof:missing')
    env[6].fail = True
    with pytest.raises(ContactError, match='private_vault_failed'):
        prepare(env, view, index=1, event='event:second')
    assert env[0].db.execute('SELECT COUNT(*) FROM contact_operations').fetchone()[0] == 1


def conversation_fixture(env):
    class Clock:
        def now(self):
            return env[4]['now']
    class Capabilities:
        def get(self, *, owner_ref, platform, backend, operation, session_ref=None):
            return Capability(platform, backend, operation, 'probado_local', 'synthetic', NOW.date(), True)
    class Dispatcher:
        fixture_only = True
        def __init__(self):
            self.deliveries = []
        def send_fixture(self, delivery):
            self.deliveries.append(delivery)
            return True
    vault = SyntheticVault('worker:conversations')
    repo = SQLiteConversations(env[0], vault=vault, capabilities=Capabilities(), clock=Clock(),
        channels=(Channel('synthetic', 'fixture', enabled=True),), synthetic_authorized=True)
    authority = env[3]('owner:alpha', 'user:alpha', env[4]['now'])
    repo.register_account(authority=authority, account=Account('account:synthetic', 'synthetic',
        'fixture', 'recipient:self', 'fixture:local', 1, NOW+timedelta(days=60)), now=env[4]['now'])
    return repo, Conversations(repo), vault, Dispatcher()


def conversation_approve_fixture(env, draft, repo, service, vault, event):
    identity = vault.seal(owner_ref='owner:alpha', plaintext=json.dumps({
        'account_ref': 'account:synthetic', 'chat_ref': 'chat:synthetic',
        'recipient_ref': 'recipient:synthetic', 'display': 'Synthetic contact',
        'address': '+1 202 555 0140'}).encode())
    proof, resolved = approved_resolution(env, draft, proof='proof:'+event)
    env[7].proofs[proof] = replace(resolved, identity_ref=identity)
    env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=proof)
    prepared = env[9].export(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref)
    authority = env[3]('owner:alpha', 'user:alpha', env[4]['now'])
    # Administrative fixture enrollment is NOT the independent resolution proof.
    repo.enable_chat(authority=authority, account_ref=prepared.resolved.account_ref,
        chat_ref=prepared.resolved.chat_ref, recipient_ref=prepared.resolved.recipient_ref,
        identity_ref=prepared.resolved.identity_ref, now=env[4]['now'])
    batch = service.compose(authority=authority, event_ref='event:'+event,
        account_ref=prepared.resolved.account_ref,
        drafts=(DraftInput((prepared.resolved.chat_ref,), prepared.text, prepared.purpose_ref),), now=env[4]['now'])
    assert env[0].get(owner_ref='owner:alpha', operation_id=batch.operation_ids[0]).approval is None
    screen = service.screen(authority=authority, batch_ref=batch.batch_ref, now=env[4]['now'])
    assert screen.rows[0]['text'] == prepared.text and screen.rows[0]['purpose_ref'] == prepared.purpose_ref
    approved = service.approve(authority=authority, event_ref='event:approve_'+event, screen=screen, now=env[4]['now'])
    env[9].note_outbound(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref,
                         outbound_ref=approved.operation_ids[0])
    return approved, screen


def test_real_a6_a8_a10_e2e_fresh_followup_approval_and_no_blind_double_send(env):
    _, _, view = start(env)
    draft = prepare(env, view)
    repo, service, vault, dispatcher = conversation_fixture(env)
    batch, screen = conversation_approve_fixture(env, draft, repo, service, vault, 'initial')
    now = env[4]['now']
    authority = env[3]('owner:alpha', 'user:alpha', now)
    result = service.dispatch_fixture(authority=authority, operation_id=batch.operation_ids[0], now=now, dispatcher=dispatcher)
    assert result.state == LedgerState.PROVIDER_CONFIRMED
    record = env[0].get(owner_ref='owner:alpha', operation_id=batch.operation_ids[0])
    # Timestamp comes from the independent synthetic provider journal, not from
    # a nonexistent observed_at field in LedgerRecord.
    observation_fixture(env, draft, record)
    following = prepare(env, view, phase='followup', followup_template='¿Alguna novedad sobre {topic}?', event='event:follow')
    follow_batch, follow_screen = conversation_approve_fixture(env, following, repo, service, vault, 'followup')
    assert follow_screen.callback_ref != screen.callback_ref
    assert follow_screen.rows[0]['text'].startswith('SEGUIMIENTO: ')
    assert follow_screen.rows[0]['purpose_ref'] == 'purpose:information_followup'
    now = env[4]['now']
    authority = env[3]('owner:alpha', 'user:alpha', now)
    service.dispatch_fixture(authority=authority, operation_id=follow_batch.operation_ids[0], now=now, dispatcher=dispatcher)
    service.dispatch_fixture(authority=authority, operation_id=follow_batch.operation_ids[0], now=now, dispatcher=dispatcher)
    assert len(dispatcher.deliveries) == 2
    assert len(env[9].status(owner_ref='owner:alpha', actor_ref='user:alpha', campaign_ref=view.campaign_ref)) == 2


def test_followup_requires_explicit_stable_opt_in(env):
    _, _, view = start(env, followup_purpose_ref=None)
    draft = prepare(env, view)
    record = dispatched_fixture(env, draft)
    observation_fixture(env, draft, record)
    with pytest.raises(ContactError, match='followup_opt_in_required'):
        prepare(env, view, phase='followup', followup_template='Seguimiento {question}', event='event:follow')
    with pytest.raises(ContactError, match='followup_purpose_must_be_explicit'):
        start(env, event='event:other', followup_purpose_ref='purpose:information')


def test_concurrent_same_event_prepare_and_resolution_converge(env):
    _, _, view = start(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        drafts = list(pool.map(lambda _: prepare(env, view), range(2)))
    assert drafts[0] == drafts[1]
    proof, _ = approved_resolution(env, drafts[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha',
            operation_ref=drafts[0].operation_ref, proof_ref=proof), range(2)))
    assert results[0] == results[1]
    assert env[0].db.execute('SELECT COUNT(*) FROM contact_operations').fetchone()[0] == 1


@pytest.mark.parametrize('change', ['session', 'proof_expired', 'proof_deleted'])
def test_resolved_export_revalidates_independent_proof_and_session(env, change):
    _, _, view = start(env)
    draft = prepare(env, view)
    proof, resolved = approved_resolution(env, draft)
    env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=proof)
    if change == 'session':
        env[0].set_session(owner_ref='owner:alpha', session_ref='fixture:local', version=2)
    elif change == 'proof_expired':
        env[4]['now'] = resolved.expires_at
    else:
        del env[7].proofs[proof]
    with pytest.raises(ContactError):
        env[9].export(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref)


def test_restart_followup_replay_once_and_new_reply_blocks_export(env):
    store, tasks, _, host, state, sink, vault, resolver, observations, contacts = env
    _, _, view = start(env)
    draft = prepare(env, view)
    record = dispatched_fixture(env, draft)
    observation_fixture(env, draft, record)
    following = prepare(env, view, phase='followup', followup_template='¿Novedades {question}?', event='event:follow')
    proof, _ = approved_resolution(env, following, proof='proof:follow')
    contacts.resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=following.operation_ref, proof_ref=proof)
    second = SQLiteStore(store.path)
    try:
        restarted = LocalContacts(SQLiteTaskStore(second, vault=tasks.vault), source_sink=sink, vault=vault,
            host_authority=host, resolver=resolver, observations=observations, clock=lambda: state['now'])
        replay = restarted.prepare(owner_ref='owner:alpha', actor_ref='user:alpha', campaign_ref=view.campaign_ref,
            candidate_ref=draft.candidate_ref, event_ref='event:follow', phase='followup', followup_template='¿Novedades {question}?')
        assert replay.operation_ref == following.operation_ref
        with pytest.raises(ContactError, match='contact_already_prepared'):
            restarted.prepare(owner_ref='owner:alpha', actor_ref='user:alpha', campaign_ref=view.campaign_ref,
                candidate_ref=draft.candidate_ref, event_ref='event:repeat', phase='followup', followup_template='¿Novedades {question}?')
        observation_fixture(env, draft, record, state='reply', ref='proof:new_reply', until=NOW+timedelta(days=2))
        with pytest.raises(ContactError, match='followup_uncertain_or_response'):
            restarted.export(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=following.operation_ref)
    finally:
        second.close()


def test_resolution_crash_rolls_back_reservation_and_replay(env):
    _, _, view = start(env)
    draft = prepare(env, view)
    proof, _ = approved_resolution(env, draft)
    def fail(stage):
        if stage == 'contact_resolved':
            raise RuntimeError('synthetic_crash')
    env[0].failpoint = fail
    with pytest.raises(RuntimeError, match='synthetic_crash'):
        env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=proof)
    assert env[0].db.execute('SELECT COUNT(*) FROM contact_purpose_reservations').fetchone()[0] == 0
    env[0].failpoint = lambda stage: None
    assert env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref=proof).state == 'resolved'


def test_source_owner_and_bounded_explicit_filters(env):
    proposal = confirmed(env)
    reports = source_reports(env[0], env[5], topic='garden')
    wrong = replace(reports[0], records=(replace(reports[0].records[0], owner_ref='owner:beta'),))
    with pytest.raises(ContactError, match='finding_owner_or_evidence'):
        env[9].start(owner_ref='owner:alpha', actor_ref='user:alpha', event_ref='event:start',
            task_ref=proposal.task_ref, reports=(wrong,), purpose_ref='purpose:information',
            template='Hola {name}', request_facts={})
    view = env[9].start(owner_ref='owner:alpha', actor_ref='user:alpha', event_ref='event:start',
        task_ref=proposal.task_ref, reports=reports, purpose_ref='purpose:information',
        template='Hola {name}', request_facts={}, filters={'topic': 'astronomy'})
    assert view.candidate_refs == () and view.discarded == 2


def test_private_backend_failure_uses_static_diagnostic_without_writes(env):
    _, _, view = start(env)
    draft = prepare(env, view)
    def private_failure(**kwargs):
        raise RuntimeError('+1 202 555 0140 PRIVATE_BODY')
    env[7].resolve = private_failure
    with pytest.raises(ContactError, match='^resolution_lookup_failed$') as failure:
        env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=draft.operation_ref, proof_ref='proof:any')
    assert 'PRIVATE_BODY' not in str(failure.value)
    assert env[0].db.execute('SELECT COUNT(*) FROM contact_purpose_reservations').fetchone()[0] == 0


def test_followup_resolution_cannot_switch_original_target(env):
    _, _, view = start(env)
    draft = prepare(env, view)
    record = dispatched_fixture(env, draft)
    observation_fixture(env, draft, record)
    following = prepare(env, view, phase='followup', followup_template='¿Novedades {question}?', event='event:follow')
    proof, _ = approved_resolution(env, following, proof='proof:follow', recipient='recipient:other')
    with pytest.raises(ContactError, match='followup_original_target_required'):
        env[9].resolve(owner_ref='owner:alpha', actor_ref='user:alpha', operation_ref=following.operation_ref, proof_ref=proof)


def test_cancel_linked_approved_a10_outbox_survives_but_claim_is_blocked(env):
    _, _, view = start(env)
    draft = prepare(env, view)
    repo, service, vault, dispatcher = conversation_fixture(env)
    batch, _ = conversation_approve_fixture(env, draft, repo, service, vault, 'initial')
    env[9].cancel(owner_ref='owner:alpha', actor_ref='user:alpha', campaign_ref=view.campaign_ref)
    authority = env[3]('owner:alpha', 'user:alpha', NOW)
    with pytest.raises(ConditionalConflict, match='action_cancelled'):
        service.dispatch_fixture(authority=authority, operation_id=batch.operation_ids[0], now=NOW, dispatcher=dispatcher)
    assert dispatcher.deliveries == []
    assert env[0].db.execute('SELECT COUNT(*) FROM conversation_outbox').fetchone()[0] == 1
    assert env[0].get(owner_ref='owner:alpha', operation_id=batch.operation_ids[0]).state == LedgerState.APPROVED
