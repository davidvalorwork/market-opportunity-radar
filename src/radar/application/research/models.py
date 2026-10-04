"""Private in-memory report inputs and opaque control outputs; no I/O."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Protocol

from radar.domain.core import utc
from radar.ports.types import BlobPointer


class ReportError(ValueError):
    """Static class only, never provider/user/corpus details."""


class SourceUnavailable(Exception):
    def __init__(self, code='reader_failed'):
        allowed = {'reader_failed', 'unsupported', 'blocked', 'needs_reauth', 'timeout', 'rate_limited', 'empty_verified', 'budget_exhausted'}
        super().__init__(code if code in allowed else 'reader_failed')


def opaque(value):
    if not isinstance(value, str) or len(value) > 129 or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*', value):
        raise ReportError('invalid_reference')


def digest(value):
    return sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def private_pointer(pointer, *, scope=None):
    if not isinstance(pointer, BlobPointer) or not isinstance(pointer.blob_key, str) or len(pointer.blob_key) > 256 or not re.fullmatch(r'[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*(/[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*)*', pointer.blob_key) or not isinstance(pointer.sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', pointer.sha256) or not isinstance(pointer.recipient_scope, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}:[a-z][a-z0-9_-]{0,63}', pointer.recipient_scope) or (scope is not None and pointer.recipient_scope != scope):
        raise ReportError('invalid_private_reference')


@dataclass(frozen=True)
class ResearchBudget:
    max_sources: int = 8
    max_notes: int = 64
    max_input_bytes: int = 131072
    max_output_bytes: int = 131072
    max_cost: Decimal = Decimal('0.25')
    max_read_cost: Decimal = Decimal('0.01')

    def __post_init__(self):
        limits = ((self.max_sources, 64), (self.max_notes, 512), (self.max_input_bytes, 1048576), (self.max_output_bytes, 1048576))
        if any(type(value) is not int or not 1 <= value <= limit for value, limit in limits) or any(not isinstance(value, Decimal) or not value.is_finite() or value < 0 for value in (self.max_cost, self.max_read_cost)):
            raise ReportError('invalid_budget')


@dataclass(frozen=True)
class ResearchRequest:
    owner_ref: str
    actor_ref: str
    task_ref: str
    topic: str = field(repr=False)
    source_refs: tuple[str, ...]
    formats: tuple[str, ...] = ('telegram',)
    fields: tuple[str, ...] = ()
    budget: ResearchBudget = ResearchBudget()
    deadline: datetime | None = None

    def __post_init__(self):
        for ref in (self.owner_ref, self.actor_ref, self.task_ref):
            opaque(ref)
        if not isinstance(self.topic, str) or not self.topic.strip() or len(self.topic.encode('utf-8')) > 4096:
            raise ReportError('invalid_topic')
        if not isinstance(self.source_refs, tuple) or not 1 <= len(self.source_refs) <= 64 or any(not isinstance(ref, str) for ref in self.source_refs) or len(set(self.source_refs)) != len(self.source_refs):
            raise ReportError('invalid_sources')
        for ref in self.source_refs:
            opaque(ref)
        if not isinstance(self.formats, tuple) or not self.formats or any(not isinstance(value, str) for value in self.formats) or len(set(self.formats)) != len(self.formats) or any(value not in ('telegram', 'json', 'document', 'pdf') for value in self.formats):
            raise ReportError('unsupported_format')
        if not isinstance(self.fields, tuple) or len(self.fields) > 32 or any(not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', value) for value in self.fields):
            raise ReportError('invalid_fields')
        if not isinstance(self.budget, ResearchBudget) or self.deadline is None:
            raise ReportError('invalid_request')
        utc(self.deadline)


@dataclass(frozen=True)
class SourceMaterial:
    """Resolver attests provenance/access; observed UTF-8 bytes, not model claims."""
    owner_ref: str
    source_ref: str
    capability_ref: str
    observed_at: datetime
    content: bytes = field(repr=False)
    content_hash: str
    provenance: str = 'unverified'
    cost: Decimal | None = None
    private_ref: BlobPointer | None = field(default=None, repr=False)
    public_url: str | None = field(default=None, repr=False)
    origins: tuple[tuple[str, int, datetime], ...] = ()
    evidence_format: str = 'raw_utf8'


@dataclass(frozen=True)
class CitationSpan:
    source_ref: str
    start: int
    end: int


@dataclass(frozen=True)
class Note:
    note_ref: str
    statement: str = field(repr=False)
    kind: str = 'extracted'
    citations: tuple[CitationSpan, ...] = ()


@dataclass(frozen=True)
class Coverage:
    source_ref: str
    status: str
    code: str | None = None
    observed_at: datetime | None = None
    content_hash: str | None = None
    provenance: str | None = None


@dataclass(frozen=True)
class Artifact:
    format: str
    private_ref: BlobPointer = field(repr=False)
    content_hash: str
    size_bytes: int


@dataclass(frozen=True)
class Report:
    owner_ref: str
    task_ref: str
    status: str
    document_ref: BlobPointer = field(repr=False)
    document_hash: str
    artifacts: tuple[Artifact, ...]
    coverage: tuple[Coverage, ...]
    reasons: tuple[str, ...]
    note_count: int
    generated_at: datetime


class SourceResolver(Protocol):
    def resolve(self, *, owner_ref: str, source_ref: str, max_bytes: int, max_cost: Decimal) -> SourceMaterial: ...


class ResearchAccess(Protocol):
    """Trusted current owner/actor/consent/task state and capability boundary.

    Recheck cancellation, expiry and revocation on every call. reserve_read is a
    conditional durable quota reservation; caller text cannot authorize it.
    """
    def check_report(self, *, owner_ref: str, actor_ref: str, task_ref: str, now: datetime) -> None: ...
    def check_source(self, *, owner_ref: str, actor_ref: str, source_ref: str, capability_ref: str | None, private_ref: BlobPointer | None, now: datetime) -> None: ...
    def check_public_url(self, *, owner_ref: str, source_ref: str, url: str) -> None: ...
    def reserve_read(self, *, owner_ref: str, task_ref: str, source_ref: str, max_cost: Decimal, now: datetime) -> None: ...


class ReportVault(Protocol):
    """Real encryption/durability is supplied by an authorized boundary, never fallback raw."""
    def seal(self, *, owner_ref: str, plaintext: bytes) -> BlobPointer: ...
