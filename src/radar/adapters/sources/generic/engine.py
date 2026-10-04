"""Bounded read orchestration. No network implementation or contact action here."""

from concurrent.futures import ThreadPoolExecutor
from base64 import b64encode
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
import re
from threading import Lock
from typing import Callable
from urllib.parse import urljoin
from uuid import uuid4

from radar.ports.interfaces import Capabilities

from .extraction import contact_candidates
from .model import (
    AccessPolicy, Code, PrivateSink, Provenance, ReadRequest, ReadTransport,
    Record, Report, SourceFailure, SourceSpec, TransportCall, name, opaque, reference, utc,
)
from .network import URLGuard


class Registry:
    """Register administrator-reviewed READ handlers, not LLM-selected commands.
    A handler_ref is an opaque lookup key, NEVER a command or arbitrary argv.
    Semantic read classification must be reviewed for each backend (e.g. Threads
    `post` is reading; X `reply-dm` writes and must never be registered here).
    """

    def __init__(self, specs: tuple[SourceSpec, ...]):
        self._specs: dict[str, SourceSpec] = {}
        for spec in specs:
            if (not opaque(spec.source_ref) or not name(spec.platform) or not name(spec.backend)
                    or spec.source_ref in self._specs
                    or spec.route not in ('fixture', 'http', 'browser', 'cli')
                    or not spec.operations or type(spec.requires_session) is not bool
                    or type(spec.max_concurrency) is not int or not 1 <= spec.max_concurrency <= 4
                    or type(spec.min_interval_seconds) is not int or not 0 <= spec.min_interval_seconds <= 300):
                raise SourceFailure(Code.INVALID)
            names = set()
            for op in spec.operations:
                if (not name(op.name) or not name(op.handler_ref) or op.effect != 'read'
                        or (spec.platform in ('x', 'twitter') and op.name == 'reply-dm')
                        or op.name in names):
                    raise SourceFailure(Code.INVALID)
                names.add(op.name)
            if spec.route != 'fixture' and not spec.allowed_hosts:
                raise SourceFailure(Code.INVALID)
            if any(not isinstance(h, str) or not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)+', h)
                   for h in spec.allowed_hosts):
                raise SourceFailure(Code.INVALID)
            self._specs[spec.source_ref] = spec

    def get(self, source_ref: str) -> SourceSpec | None:
        return self._specs.get(source_ref)


class Reader:
    """Single-process scheduler, owner isolation and per-account serialization.
    Cross-worker leases and encrypted storage require deployment adapters.
    """

    def __init__(self, *, registry: Registry, capabilities: Capabilities,
                 access: AccessPolicy, private_sink: PrivateSink | None,
                 transports: dict[str, ReadTransport], now: Callable[[], datetime],
                 url_guard: URLGuard | None = None, max_parallel: int = 4,
                 capability_max_age_days: int = 7, private_scope: str = 'worker:sources'):
        if (type(max_parallel) is not int or not 1 <= max_parallel <= 16
                or type(capability_max_age_days) is not int or not 0 <= capability_max_age_days <= 30
                or not reference(private_scope, 'recipient_scope')):
            raise SourceFailure(Code.INVALID)
        self.registry, self.capabilities, self.access = registry, capabilities, access
        self.private_sink, self.transports = private_sink, dict(transports)
        self.now, self.url_guard, self.max_parallel = now, url_guard, max_parallel
        self.capability_max_age_days = capability_max_age_days
        self.private_scope = private_scope
        self._lock = Lock()
        self._active: dict[tuple, int] = {}
        self._next: dict[tuple, datetime] = {}

    def _keys(self, request: ReadRequest, spec: SourceSpec) -> tuple[tuple, ...]:
        # Global source throttle protects public sources across owners. A private
        # account key serializes different source operations for the same owner.
        keys = [('source', spec.source_ref)]
        if request.account_ref is not None:
            keys.append(('account', request.owner_ref, request.account_ref))
        return tuple(keys)

    def _preflight(self, request: ReadRequest, spec: SourceSpec, transport: ReadTransport) -> None:
        current = self.now()
        if not utc(current) or current >= request.deadline:
            raise SourceFailure(Code.TIMEOUT)
        grant = self.access.authorize(request)
        binding = ('owner_ref', 'actor_ref', 'source_ref', 'operation', 'account_ref', 'session_ref')
        if (grant is None or any(getattr(grant, k) != getattr(request, k) for k in binding)
                or grant.authorized is not True or grant.consent_current is not True
                or not utc(grant.expires_at) or grant.expires_at <= current):
            raise SourceFailure(Code.DENIED)
        if spec.requires_session or request.session_ref is not None:
            if (request.account_ref is None or request.session_ref is None
                    or grant.session_verified is not True or not utc(grant.session_expires_at)
                    or grant.session_expires_at <= current):
                raise SourceFailure(Code.REAUTH)
        cap = self.capabilities.get(owner_ref=request.owner_ref, platform=spec.platform,
                                    backend=spec.backend, operation=request.operation,
                                    session_ref=request.session_ref)
        permitted = ('probado_local', 'probado_real') if transport.fixture_only is True else ('probado_real',)
        if (cap is None or (cap.platform, cap.backend, cap.operation) != (spec.platform, spec.backend, request.operation)
                or cap.authorized is not True or cap.status not in permitted
                or not 0 <= (current.date() - cap.checked_on).days <= self.capability_max_age_days):
            raise SourceFailure(Code.CAPABILITY)
        if self.private_sink is None:
            raise SourceFailure(Code.PRIVATE)
        if spec.route == 'fixture':
            if transport.fixture_only is not True:
                raise SourceFailure(Code.UNSUPPORTED)
        elif (transport.fixture_only is not False or transport.guarded_live is not True
              or self.url_guard is None or request.target_url is None):
            raise SourceFailure(Code.UNSUPPORTED)

    def _pace(self, keys: tuple[tuple, ...], spec: SourceSpec) -> None:
        with self._lock:
            current = self.now()
            if any(current < self._next.get(k, current) for k in keys):
                # A local check does not renew the provider's cooldown.
                raise SourceFailure(Code.RATE, cooldown_seconds=0)
            for k in keys:
                self._next[k] = current + timedelta(seconds=spec.min_interval_seconds)

    def _cooldown(self, keys: tuple[tuple, ...], seconds: int) -> None:
        seconds = min(300, max(1, seconds)) if type(seconds) is int else 30
        with self._lock:
            for k in keys:
                until = self.now() + timedelta(seconds=seconds)
                self._next[k] = max(self._next.get(k, until), until)

    def _pointer(self, pointer) -> bool:
        return (reference(pointer.blob_key, 'blob_key')
                and pointer.recipient_scope == self.private_scope
                and isinstance(pointer.sha256, str)
                and re.fullmatch(r'[a-f0-9]{64}', pointer.sha256) is not None)

    def _put(self, owner_ref: str, content: bytes):
        stored = self.private_sink.put(owner_ref=owner_ref, content=content)
        if stored.owner_ref != owner_ref or not self._pointer(stored.pointer):
            raise SourceFailure(Code.PRIVATE)
        return stored.pointer

    @staticmethod
    def _resume_binding(request: ReadRequest) -> dict:
        return {key: getattr(request, key) for key in
                ('owner_ref', 'actor_ref', 'source_ref', 'operation', 'account_ref',
                 'session_ref', 'query', 'target_url')}

    def read(self, request: ReadRequest, *, cancelled: Callable[[], bool] = lambda: False) -> Report:
        # Return safe refs even for malformed requests; never echo untrusted IDs.
        report = Report(request.request_ref if opaque(request.request_ref) else 'request:invalid',
                        request.source_ref if opaque(request.source_ref) else 'source:invalid', Code.INVALID, False)
        spec, keys, acquired = None, (), False
        records: list[Record] = []
        seen: dict[bytes, int] = {}
        pages = calls = received = duplicates = 0
        cursor = resume_ref = None
        page_offset = 0
        checkpoint_pages = 0
        target_url = request.target_url
        code = Code.FAILURE
        try:
            if (not all(opaque(v) for v in (request.owner_ref, request.actor_ref, request.request_ref, request.source_ref))
                    or not name(request.operation)
                    or (request.account_ref is not None and not opaque(request.account_ref))
                    or (request.session_ref is not None and not reference(request.session_ref, 'session_ref'))
                    or not utc(request.deadline) or not request.limits.valid()
                    or not isinstance(request.query, str) or len(request.query.encode('utf-8')) > 8192
                    or (request.account_ref is not None and request.session_ref is None)):
                raise SourceFailure(Code.INVALID)
            spec = self.registry.get(request.source_ref)
            if spec is None:
                raise SourceFailure(Code.UNSUPPORTED)
            operation = next((op for op in spec.operations if op.name == request.operation), None)
            transport = self.transports.get(spec.route)
            if operation is None or transport is None:
                raise SourceFailure(Code.UNSUPPORTED)
            self._preflight(request, spec, transport)
            keys = self._keys(request, spec)
            with self._lock:
                if (self._active.get(('global',), 0) >= self.max_parallel
                        or any(self._active.get(k, 0) >= (spec.max_concurrency if k[0] == 'source' else 1) for k in keys)):
                    raise SourceFailure(Code.RATE, cooldown_seconds=0)
                for k in keys + (('global',),):
                    self._active[k] = self._active.get(k, 0) + 1
                acquired = True
            if request.resume_ref is not None:
                if not self._pointer(request.resume_ref):
                    raise SourceFailure(Code.PRIVATE)
                try:
                    raw = self.private_sink.read(owner_ref=request.owner_ref, pointer=request.resume_ref, max_bytes=32_768)
                except PermissionError:
                    raise SourceFailure(Code.DENIED) from None
                if not isinstance(raw, bytes) or len(raw) > 32_768:
                    raise SourceFailure(Code.PRIVATE)
                saved = json.loads(raw)
                if (saved.get('kind') != 'source_resume_v1' or saved.get('binding') != self._resume_binding(request)
                        or not isinstance(saved.get('cursor'), str) or len(saved['cursor'].encode()) > 4096
                        or type(saved.get('page_offset')) is not int or not 0 <= saved['page_offset'] <= 1_000_000
                        or not isinstance(saved.get('target_url'), (str, type(None)))):
                    raise SourceFailure(Code.DENIED)
                cursor, target_url = saved['cursor'], saved['target_url']
                page_offset = saved['page_offset']
            while True:
                if cancelled():
                    raise SourceFailure(Code.CANCELLED)
                if pages >= request.limits.pages:
                    raise SourceFailure(Code.LIMIT)
                redirects = 0
                while True:
                    self._preflight(request, spec, transport)  # Fresh consent/session/capability every I/O.
                    if cancelled():
                        raise SourceFailure(Code.CANCELLED)
                    if calls >= request.limits.requests or received >= request.limits.bytes:
                        raise SourceFailure(Code.LIMIT)
                    self._pace(keys, spec)
                    target = (self.url_guard.pin(target_url, allowed_hosts=spec.allowed_hosts)
                              if spec.route != 'fixture' else None)
                    remaining = (request.deadline - self.now()).total_seconds()
                    if remaining <= 0:
                        raise SourceFailure(Code.TIMEOUT)
                    timeout = min(request.limits.timeout_seconds, remaining)
                    started = self.now()
                    calls += 1  # Even a timeout consumes a request.
                    page = transport.read(TransportCall(request, operation, target, cursor,
                                                        timeout, request.limits.bytes - received), cancelled=cancelled)
                    if cancelled():
                        raise SourceFailure(Code.CANCELLED)
                    if self.now() >= request.deadline or (self.now() - started).total_seconds() > timeout:
                        raise SourceFailure(Code.TIMEOUT)
                    if type(page.bytes_received) is not int or page.bytes_received < 0:
                        raise SourceFailure(Code.FAILURE)
                    received += page.bytes_received
                    if received > request.limits.bytes:
                        raise SourceFailure(Code.LIMIT)
                    if target is not None:
                        self.url_guard.check_peer(target, page.peer_ip)
                    if page.status == 429:
                        raise SourceFailure(Code.RATE, cooldown_seconds=page.retry_after_seconds)
                    if page.status in (401, 403):
                        raise SourceFailure(Code.REAUTH)
                    if page.status in (301, 302, 303, 307, 308):
                        if target is None or not page.redirect_url or redirects >= request.limits.redirects:
                            raise SourceFailure(Code.NETWORK)
                        target_url = urljoin(target.url, page.redirect_url)
                        redirects += 1
                        continue
                    if page.status != 200 or page.redirect_url is not None:
                        raise SourceFailure(Code.FAILURE)
                    break
                pages += 1
                if (not isinstance(page.items, tuple) or len(page.items) > request.limits.items
                        or any(not isinstance(i.content, bytes) or not isinstance(i.url, str) for i in page.items)):
                    raise SourceFailure(Code.FAILURE)
                # Header/decoding accounting cannot substitute a streaming cap in
                # a live adapter; also bound decoded item bytes before persistence.
                if sum(len(i.content) + len(i.url.encode('utf-8')) for i in page.items) > page.bytes_received:
                    raise SourceFailure(Code.LIMIT)
                for item in page.items:
                    if cancelled():
                        raise SourceFailure(Code.CANCELLED)
                    self._preflight(request, spec, transport)
                    # Length-framed hashing avoids hex/JSON copies and ambiguous
                    # concatenation (URL='ab', body='c' != URL='a', body='bc').
                    url_bytes = item.url.encode('utf-8')
                    hasher = hashlib.sha256()
                    hasher.update(len(url_bytes).to_bytes(8, 'big'))
                    hasher.update(url_bytes)
                    hasher.update(len(item.content).to_bytes(8, 'big'))
                    hasher.update(item.content)
                    fingerprint = hasher.digest()
                    provenance = Provenance(spec.source_ref, page_offset + pages, self.now())
                    if fingerprint in seen:
                        index = seen[fingerprint]
                        records[index] = replace(records[index], provenance=records[index].provenance + (provenance,))
                        duplicates += 1
                        continue
                    if len(records) >= request.limits.items:
                        raise SourceFailure(Code.LIMIT)
                    contacts = [{'kind': c.kind, 'value': c.value, 'verified': c.verified}
                                for c in contact_candidates(item.content)]
                    payload = json.dumps({'url': item.url, 'content_b64': b64encode(item.content).decode('ascii'),
                                          'contact_candidates': contacts},
                                         separators=(',', ':')).encode()
                    pointer = self._put(request.owner_ref, payload)
                    seen[fingerprint] = len(records)
                    records.append(Record('record:' + uuid4().hex + 'r', request.owner_ref, pointer, (provenance,)))
                checkpoint_pages = pages
                if page.next_cursor is None:
                    code = Code.COMPLETE
                    break
                if not isinstance(page.next_cursor, str) or len(page.next_cursor.encode('utf-8')) > 4096:
                    raise SourceFailure(Code.FAILURE)
                cursor = page.next_cursor
        except SourceFailure as exc:
            code = exc.code
            if code == Code.RATE and keys and acquired and exc.cooldown_seconds != 0:
                self._cooldown(keys, exc.cooldown_seconds)
        except Exception:
            # External exceptions often contain source URLs or authentication.
            code = Code.FAILURE
        finally:
            # Pagination tokens stay in owner-encrypted storage, not control/queue.
            # Save only a page boundary: never skip a partially processed page.
            if code in (Code.RATE, Code.LIMIT) and cursor is not None and acquired:
                try:
                    self._preflight(request, spec, transport)
                    resume_ref = self._put(request.owner_ref, json.dumps({
                        'kind': 'source_resume_v1', 'binding': self._resume_binding(request),
                        'cursor': cursor, 'target_url': target_url, 'page_offset': page_offset + checkpoint_pages,
                    }, separators=(',', ':')).encode())
                except Exception:
                    code = Code.PRIVATE
            if acquired:
                with self._lock:
                    for k in keys + (('global',),):
                        self._active[k] -= 1
        return replace(report, code=code, complete=code == Code.COMPLETE,
                       pages=pages, requests=calls, bytes_received=received,
                       duplicates=duplicates, records=tuple(records), resume_ref=resume_ref)

    def read_many(self, requests: tuple[ReadRequest, ...], *, cancelled: Callable[[], bool] = lambda: False) -> tuple[Report, ...]:
        if not isinstance(requests, tuple) or len(requests) > 100:
            raise SourceFailure(Code.LIMIT)
        with ThreadPoolExecutor(max_workers=self.max_parallel) as pool:
            return tuple(pool.map(lambda request: self.read(request, cancelled=cancelled), requests))
