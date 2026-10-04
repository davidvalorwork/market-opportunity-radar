"""CPU-only synthetic authority/zone fixtures against the real SQLite adapter."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone, tzinfo
import sqlite3

import pytest

from radar.adapters.local.scheduler import LocalScheduler
from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.telegram.consent import CONSENT_VERSION
from radar.application.schedules.model import (Quota, Recurrence, Schedule,
    ScheduleDenied, TemplateAuthority, TemplateBinding)


UTC = timezone.utc
NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class IndependentAuthority:
    """Independent test fixture, NOT production approval storage or a vault."""
    def __init__(self):
        self.confirmed = {}
        self.denials = {}
        self.failure = None

    def resolve(self, binding, *, now):
        if self.failure:
            raise self.failure
        if binding.owner_ref in self.denials:
            raise ScheduleDenied(self.denials[binding.owner_ref])
        proof = self.confirmed.get((binding.owner_ref, binding.private_ref))
        if proof is None or proof.binding != binding:
            raise ScheduleDenied('template_changed')
        return proof


def zones(name):
    return {'America/Caracas': timezone(timedelta(hours=-4)),
            'Asia/Tokyo': timezone(timedelta(hours=9)),
            'Fixture/Eastern': EasternFixture()}[name]


class EasternFixture(tzinfo):
    """2026 US DST fixture; no dependency on platform timezone data."""
    def utcoffset(self, dt):
        wall = dt.replace(tzinfo=None)
        spring = datetime(2026, 3, 8, 2)
        fall = datetime(2026, 11, 1, 1)
        if spring <= wall < spring+timedelta(hours=1):
            daylight = dt.fold == 1
        elif fall <= wall < fall+timedelta(hours=1):
            daylight = dt.fold == 0
        else:
            daylight = spring+timedelta(hours=1) <= wall < fall
        return timedelta(hours=-4 if daylight else -5)

    def dst(self, dt):
        return self.utcoffset(dt)-timedelta(hours=-5)

    def fromutc(self, dt):
        wall = dt.replace(tzinfo=None)
        daylight = datetime(2026, 3, 8, 7) <= wall < datetime(2026, 11, 1, 6)
        fold = int(datetime(2026, 11, 1, 6) <= wall < datetime(2026, 11, 1, 7))
        return (dt+timedelta(hours=-4 if daylight else -5)).replace(fold=fold)


def enroll(store, owner='owner:one', actor='actor:one'):
    store.enroll(owner_ref=owner, actor_ref=actor)
    identity = 1 if owner == 'owner:one' else 2
    store.db.execute('INSERT INTO directory VALUES(?,?,?,\'owner\',?,?)',
                     (identity, owner, actor, CONSENT_VERSION, 'synthetic'))


def binding(owner='owner:one', actor='actor:one'):
    return TemplateBinding(owner, actor, 'private:template:one', 'a'*64, 1, 'session:one', 1)


@pytest.fixture
def env(tmp_path):
    store = SQLiteStore(tmp_path/'control.sqlite')
    enroll(store)
    authority = IndependentAuthority()
    bind = binding()
    authority.confirmed[(bind.owner_ref, bind.private_ref)] = TemplateAuthority(bind, NOW+timedelta(days=365))
    clock = Clock()
    scheduler = LocalScheduler(store, authority=authority, quota=Quota(), clock=clock, zones=zones)
    yield store, authority, clock, scheduler
    store.close()


def schedule(ref='schedule:one', bind=None, start=NOW, **kwargs):
    return Schedule(ref, bind or binding(), Recurrence('interval', start, interval_seconds=60), **kwargs)


def tick(scheduler, **kwargs):
    return scheduler.tick(owner_ref='owner:one', actor_ref='actor:one', **kwargs)


def status(scheduler, **kwargs):
    return scheduler.status(owner_ref='owner:one', actor_ref='actor:one', **kwargs)


def prepare(scheduler, occurrence):
    return scheduler.prepare(owner_ref='owner:one', actor_ref='actor:one', occurrence_ref=occurrence)


def test_restart_duplicate_tick_and_worker_replay(env):
    store, authority, clock, scheduler = env
    spec = schedule()
    scheduler.create(spec)
    assert scheduler.create(spec) == spec.schedule_ref
    assert tick(scheduler).created == 1
    occurrence = status(scheduler)[0][1]
    intended = prepare(scheduler, occurrence)
    assert intended.state == 'pending_approval'
    assert prepare(scheduler, occurrence) == intended
    second = SQLiteStore(store.path)
    try:
        restarted = LocalScheduler(second, authority=authority, quota=Quota(), clock=clock, zones=zones)
        assert tick(restarted).created == 0
        assert prepare(restarted, occurrence) == intended
        assert second.db.execute('SELECT occurrences,units FROM schedule_windows').fetchone() == (1, 1)
    finally:
        second.close()


@pytest.mark.parametrize('stage', ['schedule_reserved', 'schedule_outbox', 'schedule_advanced', 'before_commit'])
def test_atomic_reservation_occurrence_outbox_advance_rollback(env, stage):
    store, _, _, scheduler = env
    scheduler.create(schedule())
    def fail(point):
        if point == stage:
            raise RuntimeError('synthetic_crash')
    store.failpoint = fail
    with pytest.raises(RuntimeError, match='synthetic_crash'):
        tick(scheduler)
    store.failpoint = lambda point: None
    assert not status(scheduler)
    assert store.db.execute('SELECT COUNT(*) FROM schedule_occurrences').fetchone()[0] == 0
    assert store.db.execute('SELECT SUM(units) FROM schedule_windows').fetchone()[0] is None
    assert tick(scheduler).created == 1


def test_partial_commit_before_next_schedule_crash_survives(env):
    store, _, _, scheduler = env
    scheduler.create(schedule('schedule:a'))
    scheduler.create(schedule('schedule:b'))
    calls = 0
    def fail(point):
        nonlocal calls
        if point == 'schedule_outbox':
            calls += 1
            if calls == 2:
                raise RuntimeError('synthetic_crash')
    store.failpoint = fail
    with pytest.raises(RuntimeError):
        tick(scheduler)
    store.failpoint = lambda point: None
    assert len(status(scheduler)) == 1
    assert tick(scheduler).created == 1
    assert len(status(scheduler)) == 2


def test_two_connections_concurrent_ticks_reserve_once(env):
    store, authority, clock, scheduler = env
    scheduler.create(schedule())
    second = SQLiteStore(store.path)
    other = LocalScheduler(second, authority=authority, quota=Quota(), clock=clock, zones=zones)
    try:
        with ThreadPoolExecutor(2) as pool:
            reports = list(pool.map(tick, (scheduler, other)))
        assert sum(report.created for report in reports) == 1
        assert len(status(scheduler)) == 1
        assert store.db.execute('SELECT occurrences,units FROM schedule_windows').fetchone() == (1, 1)
    finally:
        second.close()


@pytest.mark.parametrize('denial', ['template_cancelled', 'template_changed', 'session_changed', 'authority_expired'])
def test_independent_authority_denied_before_tick_and_worker(env, denial):
    _, authority, _, scheduler = env
    scheduler.create(schedule())
    assert tick(scheduler).created == 1
    occurrence = status(scheduler)[0][1]
    authority.denials['owner:one'] = denial
    assert prepare(scheduler, occurrence) is None
    assert status(scheduler)[0][2:] == ('blocked', denial)
    assert prepare(scheduler, occurrence) is None


@pytest.mark.parametrize('denial', ['template_cancelled', 'template_changed', 'session_changed', 'authority_expired'])
def test_tick_preserves_backlog_and_exposes_denial(env, denial):
    _, authority, _, scheduler = env
    scheduler.create(schedule())
    authority.denials['owner:one'] = denial
    report = tick(scheduler)
    assert report.created == 0 and report.backlog
    assert report.blocked == (('schedule:one', denial),)
    assert not status(scheduler)


@pytest.mark.parametrize('change', ['revoke', 'stop', 'consent', 'role'])
def test_current_host_gates_block_ticks_and_queued_work(env, change):
    store, _, clock, scheduler = env
    scheduler.create(schedule())
    tick(scheduler)
    occurrence = status(scheduler)[0][1]
    if change == 'revoke':
        store.revoke(owner_ref='owner:one', actor_ref='actor:one')
    elif change == 'stop':
        store.stop(owner_ref='owner:one')
    elif change == 'consent':
        store.db.execute('UPDATE directory SET consent_version=NULL')
    else:
        store.db.execute('UPDATE directory SET role=\'collaborator\'')
    clock.now += timedelta(minutes=1)
    with pytest.raises(ScheduleDenied):
        tick(scheduler)
    with pytest.raises(ScheduleDenied):
        prepare(scheduler, occurrence)
    assert store.db.execute('SELECT state FROM schedule_outbox').fetchone()[0] == 'pending'


@pytest.mark.parametrize('state', ['paused', 'deleted'])
def test_pause_delete_cancel_queued_and_restart(env, state):
    store, authority, clock, scheduler = env
    scheduler.create(schedule())
    tick(scheduler)
    occurrence = status(scheduler)[0][1]
    scheduler.set_state(owner_ref='owner:one', actor_ref='actor:one', schedule_ref='schedule:one', state=state)
    clock.now += timedelta(days=1)
    other_store = SQLiteStore(store.path)
    try:
        other = LocalScheduler(other_store, authority=authority, quota=Quota(), clock=clock, zones=zones)
        assert tick(other).created == 0
        assert prepare(other, occurrence) is None
        assert status(other)[0][2:] == ('blocked', 'schedule_inactive')
    finally:
        other_store.close()


def test_owner_bound_ids_and_lists(env):
    store, authority, clock, scheduler = env
    enroll(store, 'owner:two', 'actor:two')
    other_binding = binding('owner:two', 'actor:two')
    authority.confirmed[(other_binding.owner_ref, other_binding.private_ref)] = TemplateAuthority(other_binding, NOW+timedelta(days=1))
    scheduler.create(schedule())
    scheduler.create(schedule(bind=other_binding))
    tick(scheduler)
    scheduler.tick(owner_ref='owner:two', actor_ref='actor:two')
    first = status(scheduler)[0][1]
    second = scheduler.status(owner_ref='owner:two', actor_ref='actor:two')[0][1]
    assert first != second
    with pytest.raises(ValueError, match='occurrence_unknown'):
        scheduler.prepare(owner_ref='owner:two', actor_ref='actor:two', occurrence_ref=first)
    for operation in (lambda: scheduler.list(owner_ref='owner:one', actor_ref='actor:two'),
                      lambda: scheduler.set_state(owner_ref='owner:one', actor_ref='actor:two', schedule_ref='schedule:one', state='deleted')):
        with pytest.raises(ScheduleDenied):
            operation()


def test_backlog_bounds_and_current_utc_window_quota(env):
    store, authority, clock, _ = env
    scheduler = LocalScheduler(store, authority=authority, quota=Quota(3600, 3, 5), clock=clock, zones=zones)
    scheduler.create(schedule(start=NOW-timedelta(days=100), units_per_occurrence=2))
    report = tick(scheduler, max_occurrences=1)
    assert report.created == 1 and report.backlog and report.cost_usd is None
    report = tick(scheduler)
    assert report.created == 1 and report.backlog
    assert report.blocked == (('schedule:one', 'budget_exhausted'),)
    assert store.db.execute('SELECT window_start,occurrences,units FROM schedule_windows').fetchone() == ('2026-10-04T12:00:00.000000Z', 2, 4)
    clock.now = NOW+timedelta(hours=1)
    assert tick(scheduler).created == 2
    assert len(status(scheduler)) == 4


def test_quota_shared_across_schedules_but_not_owners(env):
    store, authority, clock, _ = env
    scheduler = LocalScheduler(store, authority=authority, quota=Quota(3600, 1, 10), clock=clock, zones=zones)
    scheduler.create(schedule('schedule:a'))
    scheduler.create(schedule('schedule:b'))
    assert tick(scheduler).created == 1
    assert len(status(scheduler)) == 1
    enroll(store, 'owner:two', 'actor:two')
    other = binding('owner:two', 'actor:two')
    authority.confirmed[(other.owner_ref, other.private_ref)] = TemplateAuthority(other, NOW+timedelta(days=1))
    scheduler.create(schedule(bind=other))
    assert scheduler.tick(owner_ref='owner:two', actor_ref='actor:two').created == 1
    conflicting = LocalScheduler(store, authority=authority, quota=Quota(3600, 100, 100), clock=clock, zones=zones)
    with pytest.raises(ValueError, match='owner_quota_policy_conflict'):
        tick(conflicting)


def test_caller_hash_is_not_authority_and_template_is_immutable(env):
    _, _, _, scheduler = env
    changed = replace(binding(), confirmation_hash='b'*64)
    with pytest.raises(ScheduleDenied, match='template_changed'):
        scheduler.create(schedule(bind=changed))
    scheduler.create(schedule())
    with pytest.raises(ValueError, match='immutable_schedule_conflict'):
        scheduler.create(schedule(start=NOW+timedelta(minutes=1)))


def test_transient_resolver_or_storage_failure_not_swallowed(env):
    store, authority, _, scheduler = env
    scheduler.create(schedule())
    authority.failure = RuntimeError('synthetic_transient')
    with pytest.raises(RuntimeError):
        tick(scheduler)
    assert not status(scheduler)
    authority.failure = None
    tick(scheduler)
    occurrence = status(scheduler)[0][1]
    store.failpoint = lambda stage: (_ for _ in ()).throw(RuntimeError('synthetic_crash')) if stage == 'schedule_prepared' else None
    with pytest.raises(RuntimeError):
        prepare(scheduler, occurrence)
    assert status(scheduler)[0][2] == 'pending'


def test_control_tables_have_only_refs_hashes_and_temporal_config(env):
    store, _, _, scheduler = env
    scheduler.create(schedule())
    tick(scheduler)
    prepare(scheduler, status(scheduler)[0][1])
    connection = sqlite3.connect(store.path)
    try:
        rows = '\n'.join(line for line in connection.iterdump() if 'schedule' in line)
    finally:
        connection.close()
    for private in ('A confidential synthetic message', '+584121234567', 'secret-token-fixture', 'chat_id', 'snapshot', 'text'):
        assert private not in rows
    assert 'private:template:one' in rows and 'pending_approval' in rows


@pytest.mark.parametrize('ambiguous,expected', [('earlier', 5), ('later', 6)])
def test_dst_fold_fires_one_occurrence(ambiguous, expected):
    recurrence = Recurrence('daily', datetime(2026, 11, 1, tzinfo=UTC), zone='Fixture/Eastern', hour=1, minute=30, ambiguous=ambiguous)
    first = recurrence.next_after(datetime(2026, 11, 1, tzinfo=UTC), zones)
    assert first == datetime(2026, 11, 1, expected, 30, tzinfo=UTC)
    assert recurrence.next_after(first, zones) == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


def test_dst_nonexistent_skips_instead_of_shift_or_doublefire():
    recurrence = Recurrence('daily', datetime(2026, 3, 8, tzinfo=UTC), zone='Fixture/Eastern', hour=2, minute=30)
    assert recurrence.next_after(datetime(2026, 3, 8, tzinfo=UTC), zones) == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)


def test_weekly_zone_configurable_and_interval_is_anchor_utc():
    weekly = Recurrence('weekly', NOW, zone='Asia/Tokyo', hour=9, weekdays=(0, 4))
    assert weekly.next_after(NOW, zones) == datetime(2026, 10, 5, tzinfo=UTC)
    interval = Recurrence('interval', NOW, interval_seconds=60)
    assert interval.next_after(NOW+timedelta(seconds=90), zones) == NOW+timedelta(seconds=120)


@pytest.mark.parametrize('kwargs,code', [({'kind': 'cron'}, 'unsupported_recurrence'),
    ({'interval_seconds': 0}, 'schedule_limit_invalid'), ({'interval_seconds': True}, 'schedule_limit_invalid'),
    ({'hour': 24}, 'schedule_limit_invalid'), ({'weekdays': (7,)}, 'schedule_limit_invalid'),
    ({'weekdays': (0, 0)}, 'weekdays_invalid'), ({'nonexistent': 'shift'}, 'unsupported_dst_policy'),
    ({'ambiguous': 'both'}, 'unsupported_dst_policy'), ({'start_at': NOW.replace(tzinfo=None)}, 'aware_time_required')])
def test_recurrence_limits_and_unsupported_explicit(kwargs, code):
    options = dict(kind='daily', start_at=NOW)
    options.update(kwargs)
    with pytest.raises(ValueError, match=code):
        Recurrence(**options)


def test_timezone_resolver_unavailable_fails_before_acceptance(env):
    store, authority, clock, _ = env
    def unavailable(name):
        raise ValueError('timezone_unavailable')
    scheduler = LocalScheduler(store, authority=authority, quota=Quota(), clock=clock, zones=unavailable)
    with pytest.raises(ValueError, match='timezone_unavailable'):
        scheduler.create(Schedule('schedule:one', binding(), Recurrence('daily', NOW)))
    assert not scheduler.list(owner_ref='owner:one', actor_ref='actor:one')
