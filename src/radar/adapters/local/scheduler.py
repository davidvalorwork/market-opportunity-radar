"""Bounded SQLite scheduler. No external transport, sends, or raw private data."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone, tzinfo
from hashlib import sha256
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from radar.application.tasks.models import Authority, TaskError
from radar.ports.types import ConditionalConflict
from radar.application.schedules.model import (OccurrenceIntent, Quota, Recurrence,
    Schedule, ScheduleDenied, TemplateAuthority, TemplateBinding, TickReport, integer, ref,
    resolve_authority, utc)
from .sqlite import parse, stamp


DDL = """
CREATE TABLE IF NOT EXISTS scheduler_policy(owner TEXT PRIMARY KEY,window_seconds INTEGER,max_occurrences INTEGER,max_units INTEGER);
CREATE TABLE IF NOT EXISTS schedules(owner TEXT,ref TEXT,config TEXT,state TEXT,next_due TEXT,version INTEGER,reason TEXT,PRIMARY KEY(owner,ref));
CREATE INDEX IF NOT EXISTS schedules_due ON schedules(owner,state,next_due,ref);
CREATE TABLE IF NOT EXISTS schedule_windows(owner TEXT,window_start TEXT,occurrences INTEGER,units INTEGER,PRIMARY KEY(owner,window_start));
CREATE TABLE IF NOT EXISTS schedule_occurrences(owner TEXT,ref TEXT,schedule TEXT,schedule_version INTEGER,due TEXT,window_start TEXT,units INTEGER,PRIMARY KEY(owner,ref),UNIQUE(owner,schedule,due));
CREATE TABLE IF NOT EXISTS schedule_outbox(seq INTEGER PRIMARY KEY AUTOINCREMENT,owner TEXT,ref TEXT,state TEXT,reason TEXT,UNIQUE(owner,ref));
CREATE INDEX IF NOT EXISTS schedule_pending ON schedule_outbox(owner,state,seq);
CREATE TABLE IF NOT EXISTS schedule_templates(owner TEXT,task TEXT,version INTEGER,binding TEXT,PRIMARY KEY(owner,task,version));
"""


def zoneinfo(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise ValueError('timezone_unavailable') from None


def encode(schedule):
    doc = asdict(schedule)
    doc['recurrence']['start_at'] = stamp(schedule.recurrence.start_at)
    return json.dumps(doc, sort_keys=True, separators=(',', ':'))


def decode(doc):
    data = json.loads(doc)
    recurrence = data['recurrence']
    recurrence['start_at'] = parse(recurrence['start_at'])
    recurrence['weekdays'] = tuple(recurrence['weekdays'])
    return Schedule(data['schedule_ref'], TemplateBinding(**data['binding']),
                    Recurrence(**recurrence), data['units_per_occurrence'])


def binding_document(binding):
    return json.dumps(asdict(binding), sort_keys=True, separators=(',', ':'))


class A6TemplateResolver:
    """Server-owned bridge to frozen A6. No user document can supply Authority.

    host_authority loads fresh host grants; confirmed_calendar deterministically
    resolves the privately confirmed schedule expression to the exact calendar
    the user accepted. Both are trusted injected gates, not Telegram handlers.
    Capture is possible only before original confirmation expires. After capture,
    the retained template is immutable while host authority is refreshed. Neither
    capture nor resolve approves any third-party message.
    """
    def __init__(self, task_store, *, host_authority, confirmed_calendar):
        if not callable(host_authority) or not callable(confirmed_calendar):
            raise ValueError('trusted_schedule_wiring_required')
        self.tasks, self.store = task_store, task_store.store
        self.host_authority, self.calendar = host_authority, confirmed_calendar
        self.store.db.executescript(DDL)

    def _host(self, owner, actor, now):
        authority = self.host_authority(owner, actor, now)
        if not isinstance(authority, Authority) or (authority.owner_ref, authority.actor_ref) != (owner, actor):
            raise ScheduleDenied('host_authority')
        if utc(authority.expires_at) <= utc(now):
            raise ScheduleDenied('authority_expired')
        self.tasks._authority(authority, now)
        return authority

    def _confirmed(self, authority, task_ref):
        proposal = self.tasks._row(authority.owner_ref, task_ref)
        if proposal.actor_ref != authority.actor_ref or proposal.status != 'confirmed' or proposal.confirmation_hash != proposal.content_hash:
            raise ScheduleDenied('template_cancelled' if proposal.status == 'cancelled' else 'template_changed')
        document, budget = self.tasks._plan(authority, proposal.snapshot)
        scheduled = [step for step in document['request']['steps'] if step['operation'] == 'schedule']
        if len(scheduled) != 1:
            raise ScheduleDenied('template_changed')
        calendar = self.calendar(proposal)
        if not isinstance(calendar, Recurrence) or calendar.zone != scheduled[0]['arguments']['schedule']['timezone']:
            raise ScheduleDenied('template_changed')
        sessions = {(step['arguments']['session_ref'], step['arguments']['session_version'])
                    for step in document['request']['steps'] if 'session_ref' in step['arguments']}
        if len(sessions) > 1:
            raise ValueError('multiple_schedule_sessions_unsupported')
        session, version = next(iter(sessions), (None, None))
        sealed = self.store.db.execute('SELECT snapshot FROM task_router_proposals WHERE owner=? AND task=?', (authority.owner_ref, task_ref)).fetchone()[0]
        private_ref = 'private:'+sha256(sealed.encode()).hexdigest()
        binding = TemplateBinding(authority.owner_ref, authority.actor_ref, private_ref,
                                  proposal.confirmation_hash, proposal.version, session,
                                  version, proposal.task_ref, max(1, budget.calls), calendar.fingerprint())
        return binding, calendar, proposal.expires_at

    def capture_confirmed(self, *, owner_ref, actor_ref, task_ref, now):
        """Trusted host registration of an explicitly accepted schedule template."""
        with self.store.transaction():
            authority = self._host(owner_ref, actor_ref, now)
            binding, calendar, expiry = self._confirmed(authority, task_ref)
            existing = self.store.db.execute('SELECT binding FROM schedule_templates WHERE owner=? AND task=? AND version=?', (owner_ref, task_ref, binding.template_version)).fetchone()
            if existing:
                if existing[0] != binding_document(binding):
                    raise ScheduleDenied('template_changed')
                return binding, calendar
            if utc(expiry) <= utc(now):
                raise ScheduleDenied('authority_expired')
            self.store.db.execute('INSERT INTO schedule_templates VALUES(?,?,?,?)', (owner_ref, task_ref, binding.template_version, binding_document(binding)))
            self.store.failpoint('schedule_template_captured')
            return binding, calendar

    def resolve(self, binding, *, now):
        row = self.store.db.execute('SELECT binding FROM schedule_templates WHERE owner=? AND task=? AND version=?', (binding.owner_ref, binding.template_ref, binding.template_version)).fetchone()
        if row is None or row[0] != binding_document(binding):
            raise ScheduleDenied('template_changed')
        try:
            authority = self._host(binding.owner_ref, binding.actor_ref, now)
            current, _, _ = self._confirmed(authority, binding.template_ref)
        except ConditionalConflict as error:
            codes = {'task_authority': 'host_authority', 'task_capability': 'host_authority',
                     'task_session': 'session_changed', 'unknown_task': 'template_changed',
                     'owner_stopped_or_unknown': 'owner_stopped'}
            if str(error) not in codes:
                raise
            raise ScheduleDenied(codes[str(error)]) from None
        except TaskError as error:
            if str(error) != 'budget_exhausted':
                raise
            raise ScheduleDenied('host_authority') from None
        if current != binding:
            raise ScheduleDenied('template_changed')
        return TemplateAuthority(binding, authority.expires_at)


class LocalScheduler:
    """Trusted resolver must verify independent confirmation, not caller hashes.

    All authority checks occur within the local SQLite transaction; a remote
    resolver cannot give remote atomicity/fencing. Executing workers must enforce
    their own live gates. This adapter only prepares pending-approval intents.
    """
    def __init__(self, store, *, authority, quota: Quota, clock=None, zones=zoneinfo):
        if not isinstance(quota, Quota) or not callable(getattr(authority, 'resolve', None)):
            raise ValueError('trusted_schedule_wiring_required')
        self.store, self.authority, self.quota = store, authority, quota
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.zones = zones
        self.store.db.executescript(DDL)

    def _host(self, owner, actor):
        row = self.store.db.execute('SELECT stopped FROM owners WHERE owner=?', (owner,)).fetchone()
        if row is None or row[0]:
            raise ScheduleDenied('owner_stopped')
        if not self.store.owner_actor(owner, actor) or not self.store.current_consent(owner, actor):
            raise ScheduleDenied('host_authority')

    def _live(self, binding, now):
        self._host(binding.owner_ref, binding.actor_ref)
        proof = resolve_authority(self.authority, binding, now)
        if utc(proof.authority_expires_at) <= utc(self.clock()):
            raise ScheduleDenied('authority_expired')
        self._host(binding.owner_ref, binding.actor_ref)

    def _policy(self, owner):
        expected = (self.quota.window_seconds, self.quota.max_occurrences, self.quota.max_units)
        self.store.db.execute('INSERT OR IGNORE INTO scheduler_policy VALUES(?,?,?,?)', (owner, *expected))
        row = self.store.db.execute('SELECT window_seconds,max_occurrences,max_units FROM scheduler_policy WHERE owner=?', (owner,)).fetchone()
        if row != expected:
            raise ValueError('owner_quota_policy_conflict')

    def create(self, schedule: Schedule):
        now = utc(self.clock())
        config = encode(schedule)
        owner = schedule.binding.owner_ref
        with self.store.transaction():
            self._live(schedule.binding, now)
            self._policy(owner)
            row = self.store.db.execute('SELECT config FROM schedules WHERE owner=? AND ref=?', (owner, schedule.schedule_ref)).fetchone()
            if row:
                if row[0] != config:
                    raise ValueError('immutable_schedule_conflict')
                return schedule.schedule_ref
            if not isinstance(self.zones(schedule.recurrence.zone), tzinfo):
                raise ValueError('timezone_unavailable')
            due = schedule.recurrence.next_after(utc(schedule.recurrence.start_at)-timedelta(microseconds=1), self.zones)
            self.store.db.execute('INSERT INTO schedules VALUES(?,?,?,?,?,1,NULL)',
                                  (owner, schedule.schedule_ref, config, 'active', stamp(due)))
            self.store.failpoint('schedule_created')
        return schedule.schedule_ref

    def set_state(self, *, owner_ref, actor_ref, schedule_ref, state):
        if state not in ('active', 'paused', 'deleted'):
            raise ValueError('schedule_state_invalid')
        with self.store.transaction():
            self._host(owner_ref, actor_ref)
            row = self.store.db.execute('SELECT state,config FROM schedules WHERE owner=? AND ref=?', (owner_ref, schedule_ref)).fetchone()
            if row is None:
                raise ValueError('schedule_unknown')
            if row[0] == 'deleted' and state != 'deleted':
                raise ValueError('schedule_deleted')
            if row[0] == state:
                return
            if state == 'active':
                self._live(decode(row[1]).binding, utc(self.clock()))
            self.store.db.execute('UPDATE schedules SET state=?,version=version+1,reason=NULL WHERE owner=? AND ref=?', (state, owner_ref, schedule_ref))

    def list(self, *, owner_ref, actor_ref, after='', limit=20):
        integer(limit, 1, 100)
        with self.store.transaction():
            self._host(owner_ref, actor_ref)
            return self.store.db.execute('SELECT ref,state,next_due,version,reason FROM schedules WHERE owner=? AND ref>? ORDER BY ref LIMIT ?', (owner_ref, after, limit)).fetchall()

    def tick(self, *, owner_ref, actor_ref, max_schedules=20, max_occurrences=50, per_schedule=5, after=None):
        integer(max_schedules, 1, 100)
        integer(max_occurrences, 1, 100)
        integer(per_schedule, 1, 10)
        now = utc(self.clock())
        created, examined, blocked = 0, 0, []
        if after is not None:
            if type(after) is not tuple or len(after) != 2:
                raise ValueError('schedule_cursor_invalid')
            after = (stamp(parse(after[0])), after[1])
            ref(after[1])
        # Each due item is its own bounded transaction: partial commits survive
        # failure on the next item. Concurrent ticks reread state under BEGIN.
        with self.store.transaction():
            self._host(owner_ref, actor_ref)
            self._policy(owner_ref)
            cursor_sql = ' AND (next_due>? OR (next_due=? AND ref>?))' if after else ''
            params = (owner_ref, stamp(now), *((after[0], after[0], after[1]) if after else ()), max_schedules)
            rows = self.store.db.execute('SELECT ref,next_due FROM schedules WHERE owner=? AND state=\'active\' AND next_due<=?'+cursor_sql+' ORDER BY next_due,ref LIMIT ?', params).fetchall()
        last_cursor = None
        for schedule_ref, due_key in rows:
            last_cursor = (due_key, schedule_ref)
            examined += 1
            for _ in range(per_schedule):
                if created == max_occurrences:
                    break
                with self.store.transaction():
                    row = self.store.db.execute('SELECT config,state,next_due,version FROM schedules WHERE owner=? AND ref=?', (owner_ref, schedule_ref)).fetchone()
                    if row[1] != 'active' or parse(row[2]) > now:
                        break
                    schedule = decode(row[0])
                    current = utc(self.clock())
                    try:
                        self._live(schedule.binding, current)
                    except ScheduleDenied as error:
                        self.store.db.execute('UPDATE schedules SET reason=? WHERE owner=? AND ref=?', (error.code, owner_ref, schedule_ref))
                        blocked.append((schedule_ref, error.code))
                        break
                    self._policy(owner_ref)
                    seconds = int(current.timestamp())
                    window = datetime.fromtimestamp(seconds-seconds % self.quota.window_seconds, timezone.utc)
                    self.store.db.execute('INSERT OR IGNORE INTO schedule_windows VALUES(?,?,0,0)', (owner_ref, stamp(window)))
                    count, units = self.store.db.execute('SELECT occurrences,units FROM schedule_windows WHERE owner=? AND window_start=?', (owner_ref, stamp(window))).fetchone()
                    if count+1 > self.quota.max_occurrences or units+schedule.units_per_occurrence > self.quota.max_units:
                        self.store.db.execute('UPDATE schedules SET reason=\'budget_exhausted\' WHERE owner=? AND ref=?', (owner_ref, schedule_ref))
                        blocked.append((schedule_ref, 'budget_exhausted'))
                        break
                    due = parse(row[2])
                    identity = sha256(json.dumps([owner_ref, schedule_ref, stamp(due)], separators=(',', ':')).encode()).hexdigest()
                    occurrence = 'occurrence:'+identity
                    self.store.db.execute('UPDATE schedule_windows SET occurrences=occurrences+1,units=units+? WHERE owner=? AND window_start=?', (schedule.units_per_occurrence, owner_ref, stamp(window)))
                    self.store.failpoint('schedule_reserved')
                    self._live(schedule.binding, utc(self.clock()))
                    self.store.db.execute('INSERT INTO schedule_occurrences VALUES(?,?,?,?,?,?,?)', (owner_ref, occurrence, schedule_ref, row[3], stamp(due), stamp(window), schedule.units_per_occurrence))
                    self.store.db.execute('INSERT INTO schedule_outbox(owner,ref,state) VALUES(?,?,\'pending\')', (owner_ref, occurrence))
                    self.store.failpoint('schedule_outbox')
                    next_due = schedule.recurrence.next_after(due, self.zones)
                    self.store.db.execute('UPDATE schedules SET next_due=?,reason=NULL WHERE owner=? AND ref=?', (stamp(next_due), owner_ref, schedule_ref))
                    self.store.failpoint('schedule_advanced')
                    self._live(schedule.binding, utc(self.clock()))
                created += 1
            if created == max_occurrences:
                break
        backlog = self.store.db.execute('SELECT 1 FROM schedules WHERE owner=? AND state=\'active\' AND next_due<=? LIMIT 1', (owner_ref, stamp(now))).fetchone() is not None
        next_cursor = last_cursor if examined < len(rows) or len(rows) == max_schedules else None
        return TickReport(created, examined, backlog, tuple(blocked), next_cursor)

    def pending(self, *, owner_ref, actor_ref, after=0, limit=20):
        integer(limit, 1, 100)
        with self.store.transaction():
            self._host(owner_ref, actor_ref)
            return self.store.db.execute('SELECT seq,ref,state,reason FROM schedule_outbox WHERE owner=? AND state=\'pending\' AND seq>? ORDER BY seq LIMIT ?', (owner_ref, after, limit)).fetchall()

    def prepare(self, *, owner_ref, actor_ref, occurrence_ref):
        """Durable preparation only. Replays do not execute or approve a send.

        Revalidate even after preparation/replay; denied work remains recorded
        as blocked. Downstream real executors must independently revalidate.
        """
        now = utc(self.clock())
        with self.store.transaction():
            self._host(owner_ref, actor_ref)
            row = self.store.db.execute('SELECT s.config,s.state,s.version,o.schedule_version,o.due,b.state,b.reason FROM schedule_occurrences o JOIN schedules s ON s.owner=o.owner AND s.ref=o.schedule JOIN schedule_outbox b ON b.owner=o.owner AND b.ref=o.ref WHERE o.owner=? AND o.ref=?', (owner_ref, occurrence_ref)).fetchone()
            if row is None:
                raise ValueError('occurrence_unknown')
            schedule = decode(row[0])
            if row[5] == 'blocked':
                return None
            try:
                if row[1] != 'active':
                    raise ScheduleDenied('schedule_inactive')
                if row[2] != row[3]:
                    raise ScheduleDenied('schedule_changed')
                self._live(schedule.binding, now)
            except ScheduleDenied as error:
                self.store.db.execute('UPDATE schedule_outbox SET state=\'blocked\',reason=? WHERE owner=? AND ref=?', (error.code, owner_ref, occurrence_ref))
                self.store.failpoint('schedule_blocked')
                return None
            self.store.db.execute('UPDATE schedule_outbox SET state=\'pending_approval\',reason=NULL WHERE owner=? AND ref=?', (owner_ref, occurrence_ref))
            self.store.failpoint('schedule_prepared')
            self._live(schedule.binding, utc(self.clock()))
            return OccurrenceIntent(occurrence_ref, schedule.schedule_ref, schedule.binding, parse(row[4]))

    def status(self, *, owner_ref, actor_ref, after=0, limit=20):
        integer(limit, 1, 100)
        with self.store.transaction():
            self._host(owner_ref, actor_ref)
            return self.store.db.execute('SELECT seq,ref,state,reason FROM schedule_outbox WHERE owner=? AND seq>? ORDER BY seq LIMIT ?', (owner_ref, after, limit)).fetchall()
