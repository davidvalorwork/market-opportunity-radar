"""A8's unchanged public gates; TLS fixture bridge is explicitly TEST ONLY."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from radar.adapters.sources.generic import Reader, Registry
from radar.adapters.sources.generic.fixtures import MemoryPrivateSink
from radar.adapters.sources.generic.model import Code, Grant, PinnedTarget, ReadRequest, SourceSpec
from radar.adapters.sources.generic.network import URLGuard
from radar.adapters.sources.transports import PublicHTTPTransport
from conftest import HOST, SPEC
from test_https_transport import response


class SyntheticAccess:
    def authorize(self,request):
        return Grant(request.owner_ref,request.actor_ref,request.source_ref,request.operation,
                     datetime.now(timezone.utc)+timedelta(minutes=1),True,True)


class NoVerifiedCapability:
    def get(self,**kwargs): return None


def test_real_transport_does_not_self_declare_a8_capability(monkeypatch):
    import socket
    monkeypatch.setattr(socket,'socket',lambda *args:(_ for _ in ()).throw(AssertionError('unexpected network')))
    reader=Reader(registry=Registry((SPEC,)),capabilities=NoVerifiedCapability(),access=SyntheticAccess(),
                  private_sink=MemoryPrivateSink(),transports={'http':PublicHTTPTransport((SPEC,))},
                  url_guard=URLGuard(lambda host:('1.1.1.1',)),now=lambda:datetime.now(timezone.utc))
    request=ReadRequest('owner:synthetic','user:synthetic','request:synthetic',SPEC.source_ref,'read',
                        datetime.now(timezone.utc)+timedelta(seconds=2),target_url='https://fixture.invalid/note')
    report=reader.read(request)
    assert report.code==Code.CAPABILITY and not report.complete and report.requests==0
    assert 'https:' not in repr(report.control())


@pytest.mark.parametrize('status,code',[(200,Code.COMPLETE),(401,Code.REAUTH),(403,Code.REAUTH),(429,Code.RATE),(500,Code.FAILURE)])
def test_fixture_bridge_actual_tls_results_obey_reader_status_contract(tls_server_factory,status,code):
    from radar.adapters.sources.generic.fixtures import FixtureCapabilities
    now=lambda:datetime.now(timezone.utc)
    server,transport=tls_server_factory(((response(b'SYNTHETIC astronomy',status),0),))
    class ExplicitTLSFixtureBridge:
        fixture_only=True
        guarded_live=False
        def read(self,call,*,cancelled):
            assert call.target is None
            target=PinnedTarget(HOST,('127.0.0.1',),'https://fixture.invalid/note')
            return transport.read(replace(call,target=target),cancelled=cancelled)
    fixture_spec=replace(SPEC,route='fixture')
    reader=Reader(registry=Registry((fixture_spec,)),capabilities=FixtureCapabilities(now()),access=SyntheticAccess(),
                  private_sink=MemoryPrivateSink(),transports={'fixture':ExplicitTLSFixtureBridge()},now=now)
    request=ReadRequest('owner:synthetic','user:synthetic','request:synthetic',SPEC.source_ref,'read',now()+timedelta(seconds=2))
    report=reader.read(request)
    assert report.code==code and report.requests==1 and report.complete==(status==200)
    assert len(server.requests)==1 and report.bytes_received>=len('https://fixture.invalid/note')
    assert all(s.fileno()==-1 for s in transport.tls_sockets)
    assert len(report.records)==(1 if status==200 else 0)
