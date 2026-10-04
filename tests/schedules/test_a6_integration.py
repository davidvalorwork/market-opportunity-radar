"""Frozen A6 router/storage + scheduler, fake private vault/host only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import sqlite3

import pytest

from radar import contracts
from radar.adapters.local.scheduler import A6TemplateResolver, LocalScheduler
from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.tasks import SQLiteTaskStore
from radar.adapters.local.telegram import Directory
from radar.adapters.telegram.consent import CONSENT_VERSION
from radar.application.schedules.model import Quota, Recurrence, Schedule, ScheduleDenied
from radar.application.tasks import Authority, OperationRegistry, TaskRouter
from radar.ports.types import BlobPointer


NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


class SyntheticPrivateVault:
    """Test-only memory vault, not encryption or a production backend."""
    def __init__(self):
        self.documents = {}

    def seal(self, *, owner_ref, plaintext):
        pointer = BlobPointer('fixture/opaque_' + str(len(self.documents)) + '.age',
                              sha256(b'fake-pointer'+plaintext).hexdigest(), 'worker:task-router')
        self.documents[(owner_ref, pointer)] = plaintext
        return pointer

    def open(self, *, owner_ref, pointer):
        return self.documents[(owner_ref, pointer)]


@pytest.fixture
def integrated(tmp_path):
    store = SQLiteStore(tmp_path/'control.sqlite')
    directory = Directory(store)
    directory.enroll_synthetic(1, owner_ref='owner:alpha', actor_ref='user:alpha')
    directory.accept_consent('user:alpha', CONSENT_VERSION, NOW, ('consent:alpha',))
    caps = tuple((name, 'cap:'+name) for name in OperationRegistry().names)
    for _, capability in caps:
        store.allow_source(owner_ref='owner:alpha', source_ref=capability, authorized=True)
    store.db.execute('INSERT INTO sessions VALUES(?,?,1)', ('owner:alpha', 'fixture:local'))
    state = {'now': NOW, 'expiry': NOW+timedelta(days=2), 'caps': caps,
             'sessions': (('fixture:local', 1),)}
    def host(owner, actor, now):
        return Authority('owner:alpha', 'user:alpha', state['caps'], state['expiry'], sessions=state['sessions'])
    vault = SyntheticPrivateVault()
    tasks = SQLiteTaskStore(store, vault=vault)
    router = TaskRouter(tasks, validate_document=contracts.validate)
    calendar = Recurrence('interval', NOW, interval_seconds=60)
    def approved_calendar(proposal):
        schedule = next(step for step in proposal.document['request']['steps'] if step['operation'] == 'schedule')
        if schedule['arguments']['schedule']['expression'] != 'interval:60':
            raise ValueError('unsupported_calendar_expression')
        return calendar
    resolver = A6TemplateResolver(tasks, host_authority=host, confirmed_calendar=approved_calendar)
    scheduler = LocalScheduler(store, authority=resolver, quota=Quota(), clock=lambda: state['now'], zones=lambda name: timezone(timedelta(hours=-4)))
    yield store, tasks, router, host, state, resolver, scheduler, vault
    store.close()


def document(contact=False):
    doc = {'schema_version': 1, 'goal': 'Private fixture Natalia +584121234567',
           'privacy_scope': 'personal', 'steps': [{'step_id': 'timer', 'operation': 'schedule',
           'arguments': {'schedule': {'expression': 'interval:60', 'timezone': 'America/Caracas'}}}],
           'missing_fields': [], 'confidence': 1,
           'budget': {'calls': 2 if contact else 1, 'message_limit': 1 if contact else 0, 'max_usd': '0.10'}}
    if contact:
        doc['steps'].append({'step_id': 'contact', 'operation': 'contact', 'depends_on': ['timer'],
            'arguments': {'recipient_refs': ['recipient:synthetic'], 'purpose': 'Synthetic test only',
                'session_ref': 'fixture:local', 'session_version': 1,
                'private_ref': {'blob_key': 'fixture/content.age', 'sha256': sha256(b'fake').hexdigest(),
                                'recipient_scope': 'worker:task-router'}}})
    return doc


def confirmed(integrated, contact=False):
    store, _, router, host, _, _, _, _ = integrated
    if contact:
        store.db.execute('INSERT INTO blobs VALUES(?,?,?,?)', ('owner:alpha', 'fixture/content.age', sha256(b'fake').hexdigest(), b'fake'))
    authority = host('owner:alpha', 'user:alpha', NOW)
    proposal = router.propose(authority=authority, event_ref='event:propose', document=document(contact), now=NOW)
    callback = next(button.callback_ref for button in proposal.buttons if button.text == 'Confirmar')
    return router.callback(authority=authority, callback_ref=callback, event_ref='event:confirm', now=NOW)


def captured(integrated, contact=False):
    proposal = confirmed(integrated, contact)
    resolver, scheduler = integrated[5:7]
    binding, calendar = resolver.capture_confirmed(owner_ref='owner:alpha', actor_ref='user:alpha', task_ref=proposal.task_ref, now=NOW)
    scheduler.create(Schedule('schedule:alpha', binding, calendar, binding.minimum_units))
    return proposal, binding, calendar


def tick(scheduler):
    return scheduler.tick(owner_ref='owner:alpha', actor_ref='user:alpha')


def pending(scheduler):
    return scheduler.pending(owner_ref='owner:alpha', actor_ref='user:alpha')


def test_a6_confirmed_template_survives_router_expiry_with_fresh_host_authority(integrated):
    store, tasks, _, host, state, resolver, scheduler, vault = integrated
    proposal, bind, calendar = captured(integrated)
    state['now'] += timedelta(hours=1)
    assert tasks.get(authority=host('owner:alpha', 'user:alpha', state['now']), task_ref=proposal.task_ref, now=state['now']).status == 'expired'
    assert tick(scheduler).created == 5
    second = SQLiteStore(store.path)
    try:
        restarted_tasks = SQLiteTaskStore(second, vault=vault)
        restarted_resolver = A6TemplateResolver(restarted_tasks, host_authority=host, confirmed_calendar=resolver.calendar)
        restarted = LocalScheduler(second, authority=restarted_resolver, quota=Quota(), clock=lambda: state['now'])
        assert tick(restarted).created == 5
        assert restarted_resolver.capture_confirmed(owner_ref='owner:alpha', actor_ref='user:alpha', task_ref=proposal.task_ref, now=state['now']) == (bind, calendar)
    finally:
        second.close()


def test_a6_expired_confirmation_cannot_register_new_template(integrated):
    proposal = confirmed(integrated)
    with pytest.raises(ScheduleDenied, match='authority_expired'):
        integrated[5].capture_confirmed(owner_ref='owner:alpha', actor_ref='user:alpha', task_ref=proposal.task_ref, now=NOW+timedelta(hours=1))


@pytest.mark.parametrize('action', ['Corregir', 'Cancelar'])
def test_a6_correction_or_cancellation_invalidates_new_and_queued_work(integrated, action):
    _, _, router, host, _, _, scheduler, _ = integrated
    proposal, _, _ = captured(integrated)
    assert tick(scheduler).created == 1
    occurrence = pending(scheduler)[0][1]
    token = next(button.callback_ref for button in proposal.buttons if button.text == action)
    router.callback(authority=host('owner:alpha', 'user:alpha', NOW), callback_ref=token, event_ref='event:change', now=NOW)
    integrated[4]['now'] += timedelta(minutes=1)
    report = tick(scheduler)
    assert report.created == 0 and report.backlog
    assert report.blocked[0][1] == ('template_cancelled' if action == 'Cancelar' else 'template_changed')
    assert scheduler.prepare(owner_ref='owner:alpha', actor_ref='user:alpha', occurrence_ref=occurrence) is None


@pytest.mark.parametrize('change', ['expired', 'capability', 'session'])
def test_a6_current_host_authority_is_rechecked(integrated, change):
    store, _, _, _, state, _, scheduler, _ = integrated
    captured(integrated, contact=change == 'session')
    if change == 'expired':
        state['expiry'] = NOW
    elif change == 'capability':
        store.allow_source(owner_ref='owner:alpha', source_ref='cap:schedule', authorized=False)
    else:
        store.db.execute('UPDATE sessions SET version=2')
    report = tick(scheduler)
    assert report.created == 0 and report.backlog
    assert not pending(scheduler)


def test_a6_no_ledger_approval_and_each_contact_occurrence_pending(integrated):
    store, _, _, _, state, _, scheduler, _ = integrated
    _, binding, _ = captured(integrated, contact=True)
    assert binding.minimum_units == 2
    assert tick(scheduler).created == 1
    state['now'] += timedelta(minutes=1)
    assert tick(scheduler).created == 1
    rows = pending(scheduler)
    assert len(rows) == 2
    for _, occurrence, _, _ in rows:
        intent = scheduler.prepare(owner_ref='owner:alpha', actor_ref='user:alpha', occurrence_ref=occurrence)
        assert intent.state == 'pending_approval'
    for table in ('ledger', 'provider_proofs', 'outbox', 'queue'):
        assert store.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0


def test_a6_binding_cannot_change_owner_version_calendar_or_budget(integrated):
    _, binding, calendar = captured(integrated)
    resolver = integrated[5]
    for changed in (replace(binding, owner_ref='owner:other'), replace(binding, template_version=2),
                    replace(binding, confirmation_hash='f'*64), replace(binding, minimum_units=2)):
        with pytest.raises(ScheduleDenied, match='template_changed'):
            resolver.resolve(changed, now=NOW)
    with pytest.raises(ValueError, match='confirmed_calendar_mismatch'):
        Schedule('schedule:changed', binding, replace(calendar, interval_seconds=120))
    _, contact, contact_calendar = captured_contact_independent(integrated)
    with pytest.raises(ValueError, match='schedule_budget_below_template'):
        Schedule('schedule:changed', contact, contact_calendar, 1)


def captured_contact_independent(integrated):
    # Independent owner-bound confirmed fixture in this test, not fabricated Approval.
    store, _, router, host, _, resolver, _, _ = integrated
    store.db.execute('INSERT INTO blobs VALUES(?,?,?,?)', ('owner:alpha', 'fixture/content.age', sha256(b'fake').hexdigest(), b'fake'))
    proposal = router.propose(authority=host('owner:alpha', 'user:alpha', NOW), event_ref='event:contact', document=document(True), now=NOW)
    token = next(button.callback_ref for button in proposal.buttons if button.text == 'Confirmar')
    result = router.callback(authority=host('owner:alpha', 'user:alpha', NOW), callback_ref=token, event_ref='event:contact_confirm', now=NOW)
    bind, calendar = resolver.capture_confirmed(owner_ref='owner:alpha', actor_ref='user:alpha', task_ref=result.task_ref, now=NOW)
    return result, bind, calendar


def test_a6_capture_rejects_foreign_owner_and_unconfirmed_proposal(integrated):
    _, _, router, host, _, resolver, _, _ = integrated
    proposal = router.propose(authority=host('owner:alpha', 'user:alpha', NOW), event_ref='event:unconfirmed', document=document(), now=NOW)
    with pytest.raises(ScheduleDenied, match='template_changed'):
        resolver.capture_confirmed(owner_ref='owner:alpha', actor_ref='user:alpha', task_ref=proposal.task_ref, now=NOW)
    with pytest.raises(ScheduleDenied, match='host_authority'):
        resolver.capture_confirmed(owner_ref='owner:other', actor_ref='user:alpha', task_ref=proposal.task_ref, now=NOW)


def test_a6_private_documents_absent_from_scheduler_tables_logs_repr(integrated, capsys):
    captured(integrated)
    scheduler = integrated[6]
    tick(scheduler)
    result = scheduler.prepare(owner_ref='owner:alpha', actor_ref='user:alpha', occurrence_ref=pending(scheduler)[0][1])
    connection = sqlite3.connect(integrated[0].path)
    try:
        dump = '\n'.join(connection.iterdump())
    finally:
        connection.close()
    diagnostic = repr(result)+repr(tick(scheduler))+str(capsys.readouterr())
    for secret in ('Natalia', '+584121234567', 'Private fixture', 'interval:60'):
        assert secret not in dump and secret not in diagnostic


def test_a6_vault_failure_remains_visible_not_acked(integrated):
    captured(integrated)
    integrated[7].documents.clear()
    from radar.application.tasks import TaskError
    with pytest.raises(TaskError, match='private_vault_failed'):
        tick(integrated[6])
    assert not pending(integrated[6])
