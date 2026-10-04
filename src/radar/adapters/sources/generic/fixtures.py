"""Explicitly synthetic offline demonstration; NOT encryption or real source proof."""

from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

from radar.ports.types import BlobPointer, Capability

from .engine import Reader, Registry
from .model import Grant, PrivateWrite, RawItem, RawPage, ReadOperation, ReadRequest, SourceSpec


class FixtureCapabilities:
    def __init__(self, current: datetime):
        self.current = current

    def get(self, *, owner_ref, platform, backend, operation, session_ref=None):
        return Capability(platform, backend, operation, 'probado_local',
                          'synthetic-fixture', self.current.date(), True)


class FixtureAccess:
    def authorize(self, request):
        return Grant(request.owner_ref, request.actor_ref, request.source_ref,
                     request.operation, request.deadline, True, True,
                     request.account_ref, request.session_ref,
                     request.deadline if request.session_ref else None,
                     bool(request.session_ref))


class MemoryPrivateSink:
    """Memory-only synthetic fixtures. Not age encryption; NEVER wire to real data."""

    def __init__(self):
        self.contents: dict[tuple[str, str], bytes] = {}

    def put(self, *, owner_ref, content):
        key = uuid4().hex
        self.contents[(owner_ref, key)] = content
        return PrivateWrite(owner_ref, BlobPointer(key, hashlib.sha256(content).hexdigest(), 'worker:sources'))

    def read(self, *, owner_ref, pointer, max_bytes=1_000_000):
        if (owner_ref, pointer.blob_key) not in self.contents:
            raise PermissionError('access_denied')
        result = self.contents[(owner_ref, pointer.blob_key)]
        if len(result) > max_bytes or hashlib.sha256(result).hexdigest() != pointer.sha256:
            raise PermissionError('access_denied')
        return result


class FixtureTransport:
    fixture_only = True
    guarded_live = False

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def read(self, call, *, cancelled):
        self.calls.append(call)
        index = int(call.cursor or '0')
        return self.pages[call.request.source_ref][index]


def fixture_page(items, *, next_cursor=None):
    return RawPage(tuple(items), next_cursor,
                   sum(len(i.content) + len(i.url.encode('utf-8')) for i in items))


def demo():
    current = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
    samples = {
        'perfumes': b'Fictional perfume listing: 45 USD; phone +1 202 555 0148',
        'jobs': b'Fictional remote Python engineer role, 3000 USD monthly',
        'events': b'Fictional free community music event, Saturday',
        'articles': b'Fictional public article about safe automation',
    }
    specs = tuple(SourceSpec('source:' + name, 'fixture', 'memory', 'fixture',
                             (ReadOperation('search', 'fixture_search'),),
                             min_interval_seconds=0) for name in samples)
    pages = {'source:' + name: (fixture_page((RawItem(text, f'https://example.org/{name}?private=fixture'),)),)
             for name, text in samples.items()}
    sink = MemoryPrivateSink()
    reader = Reader(registry=Registry(specs), capabilities=FixtureCapabilities(current),
                    access=FixtureAccess(), private_sink=sink,
                    transports={'fixture': FixtureTransport(pages)}, now=lambda: current)
    requests = tuple(ReadRequest('owner:fixture', 'actor:fixture', f'request:{name}',
                                 'source:' + name, 'search', current + timedelta(minutes=1)) for name in samples)
    return reader.read_many(requests)
