"""Private, in-process read boundary. Not a second public/wire schema.

Trusted deployment code registers read handlers; untrusted tasks cannot register
commands. Sensitive fields are excluded from repr and never appear in reports.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from importlib.resources import files
import json
import re
from typing import Callable, Protocol

from radar.ports.types import BlobPointer


class Code(str, Enum):
    COMPLETE = 'complete'
    INVALID = 'invalid_request'
    UNSUPPORTED = 'unsupported'
    DENIED = 'access_denied'
    REAUTH = 'needs_reauth'
    CAPABILITY = 'capability_unverified'
    PRIVATE = 'private_backend_required'
    NETWORK = 'network_boundary_rejected'
    LIMIT = 'budget_exhausted'
    RATE = 'rate_limited'
    TIMEOUT = 'timeout'
    CANCELLED = 'cancelled'
    FAILURE = 'source_failure'


class SourceFailure(Exception):
    def __init__(self, code: Code, *, cooldown_seconds: int = 30):
        self.code = code
        self.cooldown_seconds = cooldown_seconds
        super().__init__(code.value)  # Never reflect provider text/URLs/credentials.


_DEFINITIONS = json.loads(files('radar').joinpath('schemas/common.v1.json').read_text(encoding='utf-8'))['$defs']


def reference(value: str, kind: str = 'opaque_ref') -> bool:
    """Use canonical published definitions, not a competing ref convention."""
    definition = (_DEFINITIONS['private_ref']['properties']['recipient_scope']
                  if kind == 'recipient_scope' else _DEFINITIONS[kind])
    return (isinstance(value, str) and len(value) <= definition.get('maxLength', 256)
            and re.fullmatch(definition['pattern'], value) is not None)


def opaque(value: str) -> bool:
    return reference(value)


def name(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', value) is not None


def utc(value: datetime) -> bool:
    return isinstance(value, datetime) and value.utcoffset() is not None and value.utcoffset().total_seconds() == 0


@dataclass(frozen=True)
class Limits:
    pages: int = 5
    requests: int = 10
    bytes: int = 2_000_000
    items: int = 200
    timeout_seconds: int = 10
    redirects: int = 3

    def valid(self) -> bool:
        caps = (100, 300, 20_000_000, 2_000, 60, 10)
        values = (self.pages, self.requests, self.bytes, self.items, self.timeout_seconds, self.redirects)
        return all(type(v) is int and 0 < v <= cap for v, cap in zip(values, caps))


@dataclass(frozen=True)
class ReadOperation:
    name: str
    handler_ref: str
    effect: str = 'read'


@dataclass(frozen=True)
class SourceSpec:
    source_ref: str
    platform: str
    backend: str
    route: str
    operations: tuple[ReadOperation, ...]
    allowed_hosts: tuple[str, ...] = ()
    requires_session: bool = False
    min_interval_seconds: int = 1
    max_concurrency: int = 1


@dataclass(frozen=True)
class ReadRequest:
    owner_ref: str
    actor_ref: str
    request_ref: str
    source_ref: str
    operation: str
    deadline: datetime
    limits: Limits = field(default_factory=Limits)
    account_ref: str | None = None
    session_ref: str | None = None  # Opaque vault identifier ONLY; never cookies.
    target_url: str | None = field(default=None, repr=False)
    query: str = field(default='', repr=False)
    resume_ref: BlobPointer | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Grant:
    owner_ref: str
    actor_ref: str
    source_ref: str
    operation: str
    expires_at: datetime
    authorized: bool
    consent_current: bool
    account_ref: str | None = None
    session_ref: str | None = None
    session_expires_at: datetime | None = None
    session_verified: bool = False


class AccessPolicy(Protocol):
    def authorize(self, request: ReadRequest) -> Grant | None: ...


@dataclass(frozen=True)
class PrivateWrite:
    owner_ref: str
    pointer: BlobPointer


class PrivateSink(Protocol):
    """Production implementation must encrypt BEFORE storage, authorize owner,
    enforce quota and immutable refs, and return an owner-bound PrivateWrite.
    recipient_scope identifies the authorized age recipients, not the owner.
    No plaintext disk/object-store fallback is permitted by this boundary.
    """

    def put(self, *, owner_ref: str, content: bytes) -> PrivateWrite: ...

    def read(self, *, owner_ref: str, pointer: BlobPointer, max_bytes: int) -> bytes: ...


@dataclass(frozen=True)
class PinnedTarget:
    hostname: str
    ips: tuple[str, ...]
    url: str = field(repr=False)


@dataclass(frozen=True)
class TransportCall:
    request: ReadRequest = field(repr=False)
    operation: ReadOperation
    target: PinnedTarget | None = field(repr=False)
    cursor: str | None = field(repr=False)
    timeout_seconds: float
    max_bytes: int


@dataclass(frozen=True)
class RawItem:
    content: bytes = field(repr=False)
    url: str = field(default='', repr=False)


@dataclass(frozen=True)
class RawPage:
    items: tuple[RawItem, ...] = field(default=(), repr=False)
    next_cursor: str | None = field(default=None, repr=False)
    bytes_received: int = 0
    status: int = 200
    peer_ip: str | None = None
    redirect_url: str | None = field(default=None, repr=False)
    retry_after_seconds: int = 30


class ReadTransport(Protocol):
    """A trusted handler, never shell/argv from a task. Enforce max_bytes DURING
    acquisition and deadline/cancel at the I/O boundary, not after buffering.
    HTTP: pin supplied IPs; verify TLS for hostname; disable automatic redirects.
    Browser/CLI: guard every subrequest (incl. redirects/downloads/WS), no login,
    posts/messages or arbitrary command strings. Verification is separate.
    """

    fixture_only: bool
    guarded_live: bool

    def read(self, call: TransportCall, *, cancelled: Callable[[], bool]) -> RawPage: ...


@dataclass(frozen=True)
class Provenance:
    source_ref: str
    page: int
    observed_at: datetime


@dataclass(frozen=True)
class Record:
    record_ref: str
    owner_ref: str
    private_ref: BlobPointer
    provenance: tuple[Provenance, ...]


@dataclass(frozen=True)
class Report:
    request_ref: str
    source_ref: str
    code: Code
    complete: bool
    pages: int = 0
    requests: int = 0
    bytes_received: int = 0
    duplicates: int = 0
    records: tuple[Record, ...] = ()
    resume_ref: BlobPointer | None = None

    def control(self) -> dict:
        """Explicit allowlist: no content, query, cursor, phone or source URL."""
        result = {
            'request_ref': self.request_ref, 'source_ref': self.source_ref,
            'code': self.code.value, 'complete': self.complete,
            'pages': self.pages, 'requests': self.requests,
            'bytes_received': self.bytes_received, 'duplicates': self.duplicates,
            'records': [{
                'record_ref': r.record_ref, 'owner_ref': r.owner_ref,
                'private_ref': {'blob_key': r.private_ref.blob_key,
                                'sha256': r.private_ref.sha256,
                                'recipient_scope': r.private_ref.recipient_scope},
                'provenance': [{'source_ref': p.source_ref, 'page': p.page,
                                'observed_at': p.observed_at.isoformat()}
                               for p in r.provenance],
            } for r in self.records],
        }
        if self.resume_ref is not None:
            result['resume_ref'] = {'blob_key': self.resume_ref.blob_key,
                                    'sha256': self.resume_ref.sha256,
                                    'recipient_scope': self.resume_ref.recipient_scope}
        return result
