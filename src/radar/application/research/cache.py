"""Private research-cache policy/DTOs; these never authorize a read or a send."""
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import re

from radar.domain.core import utc
from radar.ports.types import BlobPointer


MAX_PAGE_BYTES = 524288


class CacheError(Exception):
    """Static diagnostics only; never URLs, query text or external exception text."""


def opaque(value):
    if not isinstance(value, str) or len(value) > 129 or not re.fullmatch(
            r'[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*', value):
        raise CacheError('invalid_cache_reference')


def fingerprint(owner_ref, value):
    opaque(owner_ref)
    if not isinstance(value, bytes):
        raise CacheError('cache_bytes_required')
    digest = sha256(b'research-cache-v1\0')
    for part in (owner_ref.encode(), value):
        digest.update(len(part).to_bytes(8, 'big'))
        digest.update(part)
    return digest.hexdigest()


@dataclass(frozen=True)
class CacheBinding:
    owner_ref: str
    actor_ref: str
    task_ref: str
    source_ref: str
    query_sha256: str
    account_ref: str | None = None
    session_ref: str | None = None
    session_version: int | None = None

    def __post_init__(self):
        for value in (self.owner_ref, self.actor_ref, self.task_ref, self.source_ref):
            opaque(value)
        if not isinstance(self.query_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.query_sha256):
            raise CacheError('invalid_query_digest')
        values = (self.account_ref, self.session_ref, self.session_version)
        if any(v is not None for v in values):
            opaque(self.account_ref)
            if not isinstance(self.session_ref, str) or not re.fullmatch(
                    r'[a-z][a-z0-9-]{0,31}:[a-z0-9][a-z0-9-]{0,62}', self.session_ref):
                raise CacheError('invalid_session_reference')
            if type(self.session_version) is not int or self.session_version < 1:
                raise CacheError('invalid_session_version')


@dataclass(frozen=True)
class CachePolicy:
    deadline: datetime
    max_items: int = 100
    max_pages: int = 10
    max_requests: int = 10
    max_bytes: int = 8388608
    retention_seconds: int = 86400

    def __post_init__(self):
        utc(self.deadline)
        limits = ((self.max_items, 5000), (self.max_pages, 1000),
                  (self.max_requests, 10000), (self.max_bytes, 67108864),
                  (self.retention_seconds, 2592000))
        if any(type(value) is not int or not 1 <= value <= maximum for value, maximum in limits):
            raise CacheError('invalid_cache_budget')


@dataclass(frozen=True)
class CacheRecord:
    url: str = field(repr=False)
    content: bytes = field(repr=False)
    observed_at: datetime

    def __post_init__(self):
        try:
            if not isinstance(self.url, str) or not 1 <= len(self.url.encode()) <= 8192 or any(ord(c) <= 32 for c in self.url):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise CacheError('invalid_cache_url') from None
        utc(self.observed_at)
        if not isinstance(self.content, bytes) or len(self.content) > MAX_PAGE_BYTES:
            raise CacheError('cache_record_limit')


@dataclass(frozen=True)
class CachedRecord:
    record_ref: str
    url_sha256: str
    content_sha256: str
    private_ref: BlobPointer
    ordinal: int
    observed_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class CacheView:
    cache_ref: str
    pass_ref: str
    version: int
    continuation_ref: BlobPointer | None
    report_ref: BlobPointer | None
    pending_items: int
    inspected: int
    skipped: int
    new: int
    pages: int
    requests: int
    bytes_used: int
    expires_at: datetime
    state: str


@dataclass(frozen=True)
class Reservation:
    view: CacheView
    request_ref: str
    max_bytes: int
    replayed: bool
    state: str


@dataclass(frozen=True)
class PageResult:
    records: tuple[CachedRecord, ...]
    view: CacheView
    replayed: bool = False
