"""Reader adapter wiring with explicit scripted HTTP/capabilities, NOT live proof."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock
from time import monotonic, sleep
from concurrent.futures import ThreadPoolExecutor

import pytest

from radar.adapters.sources.generic.engine import Registry
from radar.adapters.sources.generic.fixtures import MemoryPrivateSink
from radar.adapters.sources.generic.model import Code, Grant, RawItem, RawPage, SourceFailure
from radar.adapters.sources.resolvers import BoundPublicReader
from radar.adapters.sources.transports import PublicHTTPTransport
from radar.adapters.sources.transports.stream import Budget
from radar.ports.types import Capability
from conftest import SPEC, request


class SyntheticAccess:
    def authorize(self,req):
        return Grant(req.owner_ref,req.actor_ref,req.source_ref,req.operation,
                     datetime.now(timezone.utc)+timedelta(minutes=1),True,True)


class SyntheticCapabilities:
    """TEST ONLY fake asserted capability, never registered as real source evidence."""
    def get(self,**kwargs):
        return Capability(platform=SPEC.platform,backend=SPEC.backend,operation='read',status='probado_real',
                          source='SYNTHETIC TEST ONLY asserted capability',checked_on=datetime.now(timezone.utc).date(),authorized=True)


class ScriptedHTTP:
    fixture_only=False
    guarded_live=True
    def __init__(self,*,redirect=False,slow=False,barrier=None):
        self.redirect,self.slow,self.barrier=redirect,slow,barrier
        self.calls=[];self.context_owners=[];self.lock=Lock();self.reader=None
    def read(self,call,*,cancelled):
        with self.lock:
            self.calls.append(call)
            if self.reader:
                prepared=self.reader._request_dns.get()
                self.context_owners.append((call.request.owner_ref,prepared.request.owner_ref,prepared.request.request_ref))
        if self.barrier:self.barrier.wait(timeout=3)
        if self.slow:
            # Exercise the very Budget shipped in frozen A17; no HTTP socket is
            # claimed here. A17's separate TLS tests prove its real I/O integration.
            budget=Budget(call.timeout_seconds,cancelled)
            while True:
                budget.check();sleep(0.01)
        if self.redirect and call.target.hostname=='fixture.invalid':
            return RawPage(status=302,peer_ip=call.target.ips[0],bytes_received=len(call.target.url),
                           redirect_url='https://second.invalid/next')
        body=b'SYNTHETIC noncommercial music and astronomy'
        return RawPage(items=(RawItem(body,call.target.url),),peer_ip=call.target.ips[0],
                       bytes_received=len(body)+len(call.target.url))


def reader_for(factory,transport,*,spec=SPEC,capabilities=None,access=None):
    reader=BoundPublicReader(resolver=factory,registry=Registry((spec,)),capabilities=capabilities or SyntheticCapabilities(),
                             access=access or SyntheticAccess(),private_sink=MemoryPrivateSink(),transports={'http':transport},
                             now=lambda:datetime.now(timezone.utc),max_parallel=4)
    if isinstance(transport,ScriptedHTTP):transport.reader=reader
    return reader


def test_dns_delay_consumes_same_http_deadline_not_fresh_timeout(resolver_factory):
    factory=resolver_factory('delayed-dns',max_timeout_seconds=0.65)
    transport=ScriptedHTTP(slow=True);reader=reader_for(factory,transport)
    start=monotonic();original=request(timeout=10)
    report=reader.read(original)
    assert report.code==Code.TIMEOUT and report.requests==1 and monotonic()-start<1.4
    call=transport.calls[0]
    assert 0<call.timeout_seconds<0.5 and call.request.deadline<original.deadline
    assert reader._request_dns.get() is None


def test_redirect_resolves_fresh_same_request_budget(resolver_factory):
    factory=resolver_factory();transport=ScriptedHTTP(redirect=True);reader=reader_for(factory,transport)
    report=reader.read(request())
    assert report.complete and report.requests==2
    assert tuple(c.target.hostname for c in transport.calls)==('fixture.invalid','second.invalid')
    assert transport.calls[0].request.deadline==transport.calls[1].request.deadline
    assert reader._request_dns.get() is None
    limited=reader_for(resolver_factory(max_calls=1),ScriptedHTTP(redirect=True)).read(request())
    assert limited.code==Code.LIMIT and limited.requests==1


def test_read_many_owners_have_distinct_immutable_request_context(resolver_factory):
    factory=resolver_factory();transport=ScriptedHTTP(barrier=Barrier(2));reader=reader_for(factory,transport)
    requests=(request(owner='owner:one',request_ref='request:one'),request(owner='owner:two',request_ref='request:two'))
    reports=reader.read_many(requests)
    assert all(r.complete for r in reports)
    assert sorted(transport.context_owners)==[('owner:one','owner:one','request:one'),('owner:two','owner:two','request:two')]
    assert all(record.owner_ref==req.owner_ref for req,report in zip(requests,reports) for record in report.records)
    assert reader._request_dns.get() is None


def test_concurrent_read_resets_worker_context_after_return(resolver_factory):
    transport=ScriptedHTTP(barrier=Barrier(2));reader=reader_for(resolver_factory(),transport)
    def run(index):
        report=reader.read(request(owner=f'owner:test{index}',request_ref=f'request:test{index}'))
        assert reader._request_dns.get() is None
        return report
    with ThreadPoolExecutor(max_workers=2) as pool:reports=tuple(pool.map(run,(1,2)))
    assert all(r.complete for r in reports) and len(transport.context_owners)==2


def test_existing_source_throttle_is_not_recreated(resolver_factory):
    transport=ScriptedHTTP();reader=reader_for(resolver_factory(),transport,spec=replace(SPEC,min_interval_seconds=1))
    assert reader.read(request(owner='owner:one')).complete
    second=reader.read(request(owner='owner:two',request_ref='request:second'))
    assert second.code==Code.RATE and second.requests==0 and len(transport.calls)==1


def test_base_capability_denies_before_dns_and_http(resolver_factory,monkeypatch):
    class MissingCapabilities:
        def get(self,**kwargs):return None
    factory=resolver_factory()
    monkeypatch.setattr('radar.adapters.sources.resolvers.public_dns.invoke',lambda *a,**kw:pytest.fail('unexpected DNS helper'))
    import socket
    monkeypatch.setattr(socket,'socket',lambda *a,**kw:pytest.fail('unexpected HTTP socket'))
    reader=reader_for(factory,PublicHTTPTransport((SPEC,)),capabilities=MissingCapabilities())
    report=reader.read(request())
    assert report.code==Code.CAPABILITY and report.requests==0 and reader._request_dns.get() is None


def test_guard_without_context_and_foreign_redirect_fail_closed(resolver_factory):
    transport=ScriptedHTTP();reader=reader_for(resolver_factory(),transport)
    with pytest.raises(SourceFailure):reader.url_guard.pin('https://fixture.invalid/',allowed_hosts=SPEC.allowed_hosts)
    def malicious(call,*,cancelled):
        return RawPage(status=302,redirect_url='https://127.0.0.1/secret',peer_ip=call.target.ips[0],bytes_received=1)
    transport.read=malicious
    assert reader.read(request()).code==Code.NETWORK
    assert reader._request_dns.get() is None


def test_timeout_cancel_failure_then_next_request_has_new_binding(resolver_factory):
    factory=resolver_factory();transport=ScriptedHTTP();reader=reader_for(factory,transport)
    assert reader.read(request(),cancelled=lambda:True).code==Code.CANCELLED
    assert reader._request_dns.get() is None
    assert reader.read(request(request_ref='request:next')).complete
    assert reader._request_dns.get() is None
