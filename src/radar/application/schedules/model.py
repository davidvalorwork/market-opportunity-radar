"""Small scheduling boundary over immutable, privately stored confirmed templates."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
import re
from typing import Callable, Protocol


UTC = timezone.utc


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('aware_time_required')
    return value.astimezone(UTC)


def ref(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_:.-]{1,200}', value):
        raise ValueError('opaque_reference_required')
    return value


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('schedule_limit_invalid')
    return value


DENIAL_CODES = frozenset({'host_authority', 'template_cancelled', 'template_changed',
                         'authority_expired', 'session_changed', 'owner_stopped',
                         'schedule_inactive', 'schedule_changed'})


class ScheduleDenied(ValueError):
    """Only known permanent authority failures; other exceptions stay visible."""
    def __init__(self, code):
        if code not in DENIAL_CODES:
            raise ValueError('unknown_schedule_denial')
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TemplateBinding:
    owner_ref: str
    actor_ref: str
    private_ref: str
    confirmation_hash: str
    template_version: int
    session_ref: str | None
    session_version: int | None
    template_ref: str = 'template:one'
    minimum_units: int = 1

    def __post_init__(self):
        for value in (self.owner_ref, self.actor_ref, self.private_ref, self.template_ref):
            ref(value)
        if not re.fullmatch('[0-9a-f]{64}', self.confirmation_hash):
            raise ValueError('confirmation_hash_invalid')
        integer(self.template_version, 1, 2**31-1)
        if self.session_ref is None:
            if self.session_version is not None:
                raise ValueError('session_binding_invalid')
        else:
            ref(self.session_ref)
            integer(self.session_version, 1, 2**31-1)
        integer(self.minimum_units, 1, 1000)


@dataclass(frozen=True)
class TemplateAuthority:
    """Returned ONLY by a trusted resolver, not supplied as an approval by callers.

    expiry is current host authority, NOT the router's short-lived confirmation.
    """
    binding: TemplateBinding
    authority_expires_at: datetime


class TrustedTemplateResolver(Protocol):
    def resolve(self, binding: TemplateBinding, *, now: datetime) -> TemplateAuthority:
        """Load independent immutable confirmation, cancellation and live authority.

        Raise ScheduleDenied for known permanent failures. Never turn a caller's
        confirmation_hash alone into authority. Production A6 wiring is a gate.
        """
        ...


def resolve_authority(resolver: TrustedTemplateResolver, binding: TemplateBinding, now):
    proof = resolver.resolve(binding, now=utc(now))
    if not isinstance(proof, TemplateAuthority) or proof.binding != binding:
        raise ScheduleDenied('template_changed')
    if utc(proof.authority_expires_at) <= utc(now):
        raise ScheduleDenied('authority_expired')
    return proof


@dataclass(frozen=True)
class Recurrence:
    kind: str
    start_at: datetime
    zone: str = 'America/Caracas'
    hour: int = 9
    minute: int = 0
    weekdays: tuple[int, ...] = (0,)
    interval_seconds: int = 86400
    ambiguous: str = 'earlier'
    nonexistent: str = 'skip'

    def __post_init__(self):
        utc(self.start_at)
        if self.kind not in ('daily', 'weekly', 'interval'):
            raise ValueError('unsupported_recurrence')
        if not isinstance(self.zone, str) or not re.fullmatch(r'[A-Za-z0-9_+./-]{1,100}', self.zone):
            raise ValueError('timezone_invalid')
        integer(self.hour, 0, 23)
        integer(self.minute, 0, 59)
        integer(self.interval_seconds, 60, 31*86400)
        if type(self.weekdays) is not tuple or not self.weekdays or len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError('weekdays_invalid')
        for day in self.weekdays:
            integer(day, 0, 6)
        if self.ambiguous not in ('earlier', 'later') or self.nonexistent != 'skip':
            raise ValueError('unsupported_dst_policy')

    def next_after(self, after: datetime, zones: Callable[[str], tzinfo]) -> datetime:
        """Strictly increasing UTC occurrence. Gaps skip, folds fire only once.

        Interval arithmetic is UTC/anchor based. Calendar lookup is bounded to
        16 days; unusual resolvers with no valid time fail closed, never spin.
        """
        after, start = utc(after), utc(self.start_at)
        if self.kind == 'interval':
            if after < start:
                return start
            steps = (after-start)//timedelta(seconds=self.interval_seconds)+1
            return start + steps*timedelta(seconds=self.interval_seconds)
        zone = zones(self.zone)
        if not isinstance(zone, tzinfo):
            raise ValueError('timezone_unavailable')
        lower = max(after, start-timedelta(microseconds=1))
        local_day = lower.astimezone(zone).date()
        for offset in range(16):
            day = local_day+timedelta(days=offset)
            if self.kind == 'weekly' and day.weekday() not in self.weekdays:
                continue
            wall = datetime(day.year, day.month, day.day, self.hour, self.minute)
            candidates = set()
            for fold in (0, 1):
                instant = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
                if instant.astimezone(zone).replace(tzinfo=None) == wall:
                    candidates.add(instant)
            if not candidates:
                continue
            chosen = (min if self.ambiguous == 'earlier' else max)(candidates)
            if chosen >= start and chosen > after:
                return chosen
        raise ValueError('timezone_calendar_unavailable')


@dataclass(frozen=True)
class Quota:
    window_seconds: int = 3600
    max_occurrences: int = 10
    max_units: int = 100

    def __post_init__(self):
        integer(self.window_seconds, 60, 86400)
        integer(self.max_occurrences, 1, 1000)
        integer(self.max_units, 1, 100000)


@dataclass(frozen=True)
class Schedule:
    schedule_ref: str
    binding: TemplateBinding
    recurrence: Recurrence
    units_per_occurrence: int = 1

    def __post_init__(self):
        ref(self.schedule_ref)
        integer(self.units_per_occurrence, 1, 1000)
        if self.units_per_occurrence < self.binding.minimum_units:
            raise ValueError('schedule_budget_below_template')


@dataclass(frozen=True)
class OccurrenceIntent:
    """LOCAL intent, not a B transport envelope and never message approval."""
    occurrence_ref: str
    schedule_ref: str
    binding: TemplateBinding
    due_at: datetime
    state: str = 'pending_approval'


@dataclass(frozen=True)
class TickReport:
    created: int
    examined: int
    backlog: bool
    blocked: tuple[tuple[str, str], ...]
    cost_usd: None = None
