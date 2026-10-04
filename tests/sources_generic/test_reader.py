from dataclasses import replace
from base64 import b64decode
from datetime import datetime, timedelta, timezone
import json
from threading import Event, Thread

import pytest

from radar.adapters.sources.generic import (
    Code, Grant, Limits, RawItem, RawPage, Reader, ReadOperation, ReadRequest,
    Registry, SourceFailure, SourceSpec, URLGuard,
)
from radar.adapters.sources.generic.fixtures import (
    FixtureAccess, FixtureCapabilities, FixtureTransport, MemoryPrivateSink,
    demo, fixture_page,
)
from radar.adapters.sources.generic.extraction import contact_candidates
from radar.adapters.sources.generic.model import PrivateWrite
from radar.ports.types import BlobPointer


NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
ITEM = RawItem(b'Private listing with phone +1 202 555 0148', 'https://example.org/item?token=secret')


def setup(*, pages=None, spec=None, access=None, caps=None, sink='default', transport=None,
          now=None, guard=None):
    spec = spec or SourceSpec('source:fixture', 'social', 'backend', 'fixture',
                              (ReadOperation('search', 'safe_search'),), min_interval_seconds=0)
    pages = pages or (fixture_page((ITEM,)),)
    transport = transport or FixtureTransport({'source:fixture': pages})
    actual_sink = MemoryPrivateSink() if sink == 'default' else sink
    reader = Reader(registry=Registry((spec,)), capabilities=caps or FixtureCapabilities(NOW),
                    access=access or FixtureAccess(), private_sink=actual_sink,
                    transports={spec.route: transport}, now=now or (lambda: NOW), url_guard=guard)
    request = ReadRequest('owner:alpha', 'actor:alpha', 'request:alpha', 'source:fixture', 'search', NOW + timedelta(minutes=1))
    return reader, request, transport, actual_sink


def test_multi_topic_offline_demo():
    reports = demo()
    assert {r.source_ref for r in reports} == {'source:perfumes', 'source:jobs', 'source:events', 'source:articles'}
    assert all(r.complete and len(r.records) == 1 for r in reports)
    output = json.dumps([r.control() for r in reports])
    assert 'https:' not in output and 'phone' not in output and 'USD' not in output


def test_private_only_output_and_exact_dedupe_provenance():
    reader, request, _, sink = setup(pages=(fixture_page((ITEM,), next_cursor='1'),
                                          fixture_page((ITEM, replace(ITEM, url='https://example.org/other')))))
    report = reader.read(request)
    assert report.complete and len(report.records) == 2 and report.duplicates == 1
    assert [p.page for p in report.records[0].provenance] == [1, 2]
    assert 'secret' not in repr(report) + repr(request) + json.dumps(report.control())
    private = sink.read(owner_ref='owner:alpha', pointer=report.records[0].private_ref)
    assert 'secret' in private.decode() and b64decode(json.loads(private)['content_b64']) == ITEM.content
    assert json.loads(private)['contact_candidates'] == [
        {'kind': 'phone', 'value': '+1 202 555 0148', 'verified': False}]
    with pytest.raises(PermissionError):
        sink.read(owner_ref='other', pointer=report.records[0].private_ref)


def test_contact_candidates_bounded_private_and_not_platform_proof():
    candidates = contact_candidates(b'Contact: person@example.org +1 202 555 0148; salary 3000 USD')
    assert {c.kind for c in candidates} == {'email', 'phone'}
    assert all(not c.verified for c in candidates)
    assert 'person@' not in repr(candidates) and '+1 202' not in repr(candidates)
    assert len(contact_candidates((' '.join(f'person{i}@example.org' for i in range(100))).encode())) == 64


def test_missing_private_backend_cannot_fetch():
    reader, request, transport, _ = setup(sink=None)
    assert reader.read(request).code == Code.PRIVATE
    assert transport.calls == []


@pytest.mark.parametrize('field,value', [
    ('owner_ref', 'other'), ('actor_ref', 'other'), ('source_ref', 'other'),
    ('operation', 'other'), ('account_ref', 'other'), ('session_ref', 'other'),
    ('authorized', False), ('consent_current', False), ('expires_at', NOW),
    ('expires_at', NOW.replace(tzinfo=None)),
])
def test_grant_binding_and_expiry(field, value):
    class Access:
        def authorize(self, request):
            return replace(FixtureAccess().authorize(request), **{field: value})
    reader, request, transport, _ = setup(access=Access())
    assert reader.read(request).code == Code.DENIED
    assert not transport.calls


@pytest.mark.parametrize('status,age,authorized', [
    ('documentado', 0, True), ('doctor_ok', 0, True), ('probado_local', 8, True),
    ('probado_real', -1, True), ('probado_real', 0, False),
])
def test_unverified_stale_future_or_unauthorized_capability(status, age, authorized):
    class Caps(FixtureCapabilities):
        def get(self, **kwargs):
            return replace(super().get(**kwargs), status=status, authorized=authorized,
                           checked_on=(NOW - timedelta(days=age)).date())
    reader, request, transport, _ = setup(caps=Caps(NOW))
    assert reader.read(request).code == Code.CAPABILITY
    assert not transport.calls


def test_wrong_capability_operation():
    class Caps(FixtureCapabilities):
        def get(self, **kwargs):
            return replace(super().get(**kwargs), operation='send')
    reader, request, _, _ = setup(caps=Caps(NOW))
    assert reader.read(request).code == Code.CAPABILITY


@pytest.mark.parametrize('changes', [
    {'session_verified': False}, {'session_expires_at': NOW}, {'session_expires_at': None},
])
def test_session_vault_ref_and_verified_freshness(changes):
    class Access:
        def authorize(self, request):
            return replace(FixtureAccess().authorize(request), **changes)
    reader, request, transport, _ = setup(access=Access())
    request = replace(request, account_ref='account:alpha', session_ref='session:alpha')
    assert reader.read(request).code == Code.REAUTH
    assert not transport.calls


@pytest.mark.parametrize('changes', [
    {'session_ref': 'cookie=value'}, {'owner_ref': 'someone@example.com'},
    {'request_ref': 'https://private?q=secret'}, {'deadline': NOW.replace(tzinfo=None)},
    {'account_ref': 'account:alpha'}, {'limits': Limits(pages=True)},
    {'query': 'x' * 8193},
])
def test_invalid_request_safe_errors(changes):
    reader, request, transport, _ = setup()
    report = reader.read(replace(request, **changes))
    assert report.code == Code.INVALID and not transport.calls
    assert 'secret' not in json.dumps(report.control())


def test_write_registration_unknown_source_and_operation():
    with pytest.raises(SourceFailure, match='invalid_request'):
        Registry((SourceSpec('source:fixture', 'social', 'backend', 'fixture',
                             (ReadOperation('reply-dm', 'write', effect='write'),)),))
    with pytest.raises(SourceFailure, match='invalid_request'):
        Registry((SourceSpec('source:twitter', 'twitter', 'opencli', 'cli',
                             (ReadOperation('reply-dm', 'incorrectly_labelled_read'),), ('x.com',)),))
    reader, request, transport, _ = setup()
    assert reader.read(replace(request, operation='send')).code == Code.UNSUPPORTED
    assert reader.read(replace(request, source_ref='source:unknown')).code == Code.UNSUPPORTED
    assert not transport.calls


def test_limits_preserve_partial_coverage():
    reader, request, transport, _ = setup(pages=(fixture_page((ITEM,), next_cursor='1'), fixture_page((ITEM,))))
    report = reader.read(replace(request, limits=Limits(pages=1)))
    assert report.code == Code.LIMIT and not report.complete and len(report.records) == 1
    assert report.requests == 1 and len(transport.calls) == 1
    assert 'next_cursor' not in report.control()
    assert report.resume_ref is not None


def test_positive_rate_interval_resume_does_not_restart_page_one():
    current = [NOW]
    spec = SourceSpec('source:fixture', 'social', 'backend', 'fixture',
                      (ReadOperation('search', 'safe_search'),), min_interval_seconds=5)
    reader, request, transport, _ = setup(
        spec=spec, pages=(fixture_page((ITEM,), next_cursor='1'),
                          fixture_page((replace(ITEM, content=b'second page'),))), now=lambda: current[0])
    report = reader.read(request)
    assert report.code == Code.RATE and report.pages == 1 and report.resume_ref is not None
    assert len(transport.calls) == 1 and 'cursor' not in json.dumps(report.control())
    current[0] += timedelta(seconds=6)
    resumed = reader.read(replace(request, request_ref='request:next', resume_ref=report.resume_ref))
    assert resumed.complete and len(transport.calls) == 2
    assert transport.calls[1].cursor == '1'
    assert resumed.records[0].provenance[0].page == 2


def test_resume_cross_owner_query_and_scope_denied():
    reader, request, transport, _ = setup(pages=(fixture_page((ITEM,), next_cursor='1'), fixture_page((ITEM,))))
    report = reader.read(replace(request, limits=Limits(pages=1)))
    assert report.resume_ref
    assert reader.read(replace(request, owner_ref='owner:other', resume_ref=report.resume_ref)).code == Code.DENIED
    assert reader.read(replace(request, query='different', resume_ref=report.resume_ref)).code == Code.DENIED
    assert reader.read(replace(request, resume_ref=replace(report.resume_ref, recipient_scope='worker:other'))).code == Code.PRIVATE
    assert len(transport.calls) == 1


@pytest.mark.parametrize('field', ['owner_ref', 'actor_ref', 'request_ref', 'source_ref'])
def test_numeric_phone_cannot_escape_as_opaque_ref(field):
    reader, request, transport, _ = setup()
    report = reader.read(replace(request, **{field: '584141171319'}))
    assert report.code == Code.INVALID and not transport.calls
    assert '584141171319' not in repr(report) + json.dumps(report.control())


def test_binary_exact_framing_not_concatenation_dedupe():
    items = (RawItem(b'c', 'ab'), RawItem(b'bc', 'a'), RawItem(b'\xff\x00\x80', 'a'))
    reader, request, _, sink = setup(pages=(fixture_page(items),))
    report = reader.read(request)
    assert report.complete and len(report.records) == 3 and report.duplicates == 0
    stored = json.loads(sink.read(owner_ref='owner:alpha', pointer=report.records[-1].private_ref))
    assert b64decode(stored['content_b64']) == items[-1].content


def test_oversize_no_persistence():
    reader, request, _, sink = setup()
    report = reader.read(replace(request, limits=Limits(bytes=5)))
    assert report.code == Code.LIMIT and not report.records and not sink.contents


def test_decoded_bytes_underreported_rejected():
    reader, request, _, sink = setup(pages=(RawPage((ITEM,), bytes_received=1),))
    assert reader.read(request).code == Code.LIMIT and not sink.contents


def test_429_cooldown_no_retry():
    current = [NOW]
    reader, request, transport, _ = setup(pages=(RawPage(status=429, retry_after_seconds=10),), now=lambda: current[0])
    assert reader.read(request).code == Code.RATE and len(transport.calls) == 1
    assert reader.read(request).code == Code.RATE and len(transport.calls) == 1
    current[0] += timedelta(seconds=11)
    assert reader.read(request).code == Code.RATE and len(transport.calls) == 2


@pytest.mark.parametrize('status', [401, 403])
def test_reauth_not_retried(status):
    reader, request, transport, _ = setup(pages=(RawPage(status=status),))
    assert reader.read(request).code == Code.REAUTH and len(transport.calls) == 1


def test_provider_exception_is_redacted():
    class Broken(FixtureTransport):
        def read(self, call, **kwargs):
            raise RuntimeError('https://secret.invalid/?cookie=credential')
    reader, request, _, _ = setup(transport=Broken({}))
    report = reader.read(request)
    assert report.code == Code.FAILURE and 'credential' not in repr(report)


def test_private_sink_owner_mismatch_no_control_pointer():
    class Broken:
        def put(self, **kwargs):
            return PrivateWrite('owner:other', BlobPointer('opaque', '0' * 64, 'worker:sources'))
    reader, request, _, _ = setup(sink=Broken())
    assert reader.read(request).code == Code.PRIVATE
    assert not reader.read(request).records


def test_cancellation_no_io():
    reader, request, transport, _ = setup()
    assert reader.read(request, cancelled=lambda: True).code == Code.CANCELLED
    assert not transport.calls


def test_access_revoked_between_pages():
    class Access(FixtureAccess):
        allowed = True
        def authorize(self, request):
            return replace(super().authorize(request), consent_current=self.allowed)
    access = Access()
    class Transport(FixtureTransport):
        def read(self, call, **kwargs):
            page = super().read(call, **kwargs)
            access.allowed = False
            return page
    transport = Transport({'source:fixture': (fixture_page((ITEM,), next_cursor='1'),)})
    reader, request, _, sink = setup(access=access, transport=transport)
    assert reader.read(request).code == Code.DENIED
    assert len(transport.calls) == 1 and not sink.contents


def test_transport_deadline_checked_before_storage():
    current = [NOW]
    class Transport(FixtureTransport):
        def read(self, call, **kwargs):
            page = super().read(call, **kwargs)
            current[0] += timedelta(seconds=11)
            return page
    reader, request, _, sink = setup(transport=Transport({'source:fixture': (fixture_page((ITEM,)),)}), now=lambda: current[0])
    assert reader.read(request).code == Code.TIMEOUT and not sink.contents


def test_same_account_serialized_across_sources():
    entered, release = Event(), Event()
    class Blocking(FixtureTransport):
        def read(self, call, **kwargs):
            entered.set()
            assert release.wait(2)
            return super().read(call, **kwargs)
    specs = tuple(SourceSpec(name, 'social', 'backend', 'fixture', (ReadOperation('search', 'safe'),),
                             min_interval_seconds=0, max_concurrency=2) for name in ('source:one', 'source:two'))
    transport = Blocking({name: (fixture_page((ITEM,)),) for name in ('source:one', 'source:two')})
    reader = Reader(registry=Registry(specs), capabilities=FixtureCapabilities(NOW), access=FixtureAccess(),
                    private_sink=MemoryPrivateSink(), transports={'fixture': transport}, now=lambda: NOW)
    request = ReadRequest('owner:alpha', 'actor:alpha', 'request:alpha', 'source:one', 'search', NOW + timedelta(minutes=1),
                          account_ref='account:alpha', session_ref='session:alpha')
    thread = Thread(target=reader.read, args=(request,))
    thread.start()
    try:
        assert entered.wait(2)
        assert reader.read(replace(request, source_ref='source:two')).code == Code.RATE
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()


def test_batch_bound():
    reader, request, _, _ = setup()
    with pytest.raises(SourceFailure, match='budget_exhausted'):
        reader.read_many((request,) * 101)


def live_setup(pages, *, ips=('8.8.8.8',), status='probado_real', transport_type=None):
    class Caps(FixtureCapabilities):
        def get(self, **kwargs):
            return replace(super().get(**kwargs), status=status)
    class HTTP(FixtureTransport):
        fixture_only = False
        guarded_live = True
        def read(self, call, **kwargs):
            self.calls.append(call)
            return pages[len(self.calls) - 1]
    spec = SourceSpec('source:fixture', 'web', 'injected', 'http', (ReadOperation('read', 'http_read'),),
                      ('example.org',), min_interval_seconds=0)
    reader, request, transport, sink = setup(spec=spec, caps=Caps(NOW),
                                           transport=(transport_type or HTTP)({'source:fixture': ()}),
                                           guard=URLGuard(lambda host: ips))
    return reader, replace(request, operation='read', target_url='https://example.org/page?q=secret'), transport, sink


@pytest.mark.parametrize('url', [
    'http://example.org/', 'https://user:pass@example.org/', 'https://example.org:8080/',
    'https://127.0.0.1/', 'https://169.254.169.254/', 'https://example.org./',
    'https://example.org/#frag', 'https://example.org/ backslash',
    'https://example.org\\@evil.org/', 'https://localhost/',
])
def test_ssrf_url_guard(url):
    guard = URLGuard(lambda host: ('8.8.8.8',))
    with pytest.raises(SourceFailure, match='network_boundary_rejected'):
        guard.pin(url, allowed_hosts=('example.org', '127.0.0.1', '169.254.169.254', 'localhost'))


@pytest.mark.parametrize('ips', [
    ('127.0.0.1',), ('8.8.8.8', '10.0.0.1'), ('169.254.169.254',), ('100.64.0.1',),
    ('::ffff:8.8.8.8',), ('ff02::1',), (), ('bad',),
])
def test_ssrf_dns_guard(ips):
    with pytest.raises(SourceFailure, match='network_boundary_rejected'):
        URLGuard(lambda host: ips).pin('https://example.org/', allowed_hosts=('example.org',))


def test_peer_attestation_prevents_rebinding():
    reader, request, transport, sink = live_setup((RawPage((ITEM,), bytes_received=200, peer_ip='10.0.0.1'),))
    assert reader.read(request).code == Code.NETWORK and not sink.contents
    assert transport.calls[0].target.ips == ('8.8.8.8',)


def test_missing_peer_and_redirect_private_no_follow():
    reader, request, _, _ = live_setup((RawPage(peer_ip=None),))
    assert reader.read(request).code == Code.NETWORK
    reader, request, transport, _ = live_setup((RawPage(status=302, peer_ip='8.8.8.8',
                                                      redirect_url='https://169.254.169.254/'),))
    assert reader.read(request).code == Code.NETWORK and len(transport.calls) == 1


def test_live_local_capability_or_unguarded_transport_denied():
    reader, request, transport, _ = live_setup((), status='probado_local')
    assert reader.read(request).code == Code.CAPABILITY and not transport.calls
    class Unguarded(FixtureTransport):
        fixture_only = False
        guarded_live = False
    reader, request, transport, _ = live_setup((), transport_type=Unguarded)
    assert reader.read(request).code == Code.UNSUPPORTED and not transport.calls


def test_redirect_rechecks_dns_request_budget_and_private_result():
    page = replace(fixture_page((ITEM,)), peer_ip='8.8.8.8')
    reader, request, transport, _ = live_setup((RawPage(status=302, peer_ip='8.8.8.8',
                                                      redirect_url='/other?q=secret'), page))
    report = reader.read(request)
    assert report.complete and report.requests == 2 and report.pages == 1
    assert transport.calls[1].target.url == 'https://example.org/other?q=secret'
    assert 'secret' not in json.dumps(report.control())
    reader, request, transport, _ = live_setup((RawPage(status=302, peer_ip='8.8.8.8', redirect_url='/other'),))
    report = reader.read(replace(request, limits=Limits(requests=1)))
    assert report.code == Code.LIMIT and len(transport.calls) == 1
