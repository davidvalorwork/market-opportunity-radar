"""Real socket/TLS tests on loopback ONLY. No test contacts the public Internet."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import errno
import socket
import ssl
from threading import Event, Timer
from time import monotonic
from urllib.parse import urlsplit

import pytest

from radar.adapters.sources.generic.model import Code, PinnedTarget, ReadOperation, SourceFailure
from radar.adapters.sources.transports import PublicHTTPTransport
from radar.adapters.sources.transports.response import retry_after
from radar.adapters.sources.transports import https as module
from conftest import HOST, SPEC, LoopbackFixtureTransport, transport_call


def response(body=b'SYNTHETIC astronomy and music\x00\xff',status=200,headers=b''):
    return f'HTTP/1.1 {status} Fixture\r\nContent-Length: {len(body)}\r\n'.encode()+headers+b'Connection: close\r\n\r\n'+body


def assert_closed(transport):
    assert all(sock.fileno()==-1 for sock in transport.tls_sockets)


def test_real_tls_ip_pin_host_sni_certificate_binary_peer_no_dns(tls_server_factory,monkeypatch,capsys,caplog):
    body=b'SYNTHETIC astronomy and music\x00\xff'
    payload=response(body)
    server,client=tls_server_factory(((payload,0),))
    monkeypatch.setattr(socket,'getaddrinfo',lambda *args,**kw: (_ for _ in ()).throw(AssertionError('DNS forbidden')))
    page=client.read(transport_call(),cancelled=lambda:False)
    assert page.items[0].content==body and page.peer_ip=='127.0.0.1' and page.status==200
    assert page.bytes_received==len(payload)+len(page.items[0].url.encode())
    assert server.sni==[HOST] and b'Host: fixture.invalid\r\n' in server.requests[0]
    assert b'GET /note?synthetic=marker HTTP/1.1' in server.requests[0]
    assert b'Cookie:' not in server.requests[0] and b'Authorization:' not in server.requests[0]
    assert 'synthetic=marker' not in repr(page)+repr(client)+caplog.text
    assert capsys.readouterr().out==''
    assert_closed(client)


@pytest.mark.parametrize('status',[301,302,303,307,308])
def test_redirect_returned_never_followed(tls_server_factory,status):
    server,client=tls_server_factory(((response(b'',status,b'Location: https://foreign.invalid/private?SYNTHETIC_SECRET\r\n'),0),))
    page=client.read(transport_call(),cancelled=lambda:False)
    assert page.status==status and not page.items and page.redirect_url.startswith('https://foreign.invalid/')
    assert len(server.requests)==1 and 'SYNTHETIC_SECRET' not in repr(page)
    assert_closed(client)


@pytest.mark.parametrize('status',[401,403,429,404,500,101])
def test_status_static_private_projection(tls_server_factory,status):
    server,client=tls_server_factory(((response(b'SYNTHETIC provider secret',status,b'Retry-After: 9000\r\n'),0),))
    page=client.read(transport_call(),cancelled=lambda:False)
    assert page.status==status and not page.items and page.retry_after_seconds==300
    assert 'provider secret' not in repr(page) and len(server.requests)==1
    assert_closed(client)


@pytest.mark.parametrize('value,expected',[('17',17),('0',1),('999999',300),('-1',30),('SYNTHETIC_SECRET',30),('x'*129,30)])
def test_retry_after_bounded_static(value,expected):
    assert retry_after(value)==expected


def test_retry_after_http_date():
    now=datetime(2026,10,4,tzinfo=timezone.utc)
    assert retry_after('Sun, 04 Oct 2026 00:01:00 GMT',now)==60
    assert retry_after('Sun, 04 Oct 2026 00:00:00 GMT',now)==1


@pytest.mark.parametrize('phase',['handshake','headers','body'])
def test_absolute_deadline_beats_continuous_trickle(tls_server_factory,phase):
    if phase=='handshake':
        server,client=tls_server_factory((),plaintext=True)
    elif phase=='headers':
        server,client=tls_server_factory(tuple((bytes([b]),0.02) for b in response()))
    else:
        header=b'HTTP/1.1 200 Fixture\r\nContent-Length: 30\r\n\r\n'
        server,client=tls_server_factory(((header,0),)+tuple((b'x',0.03) for _ in range(30)))
    start=monotonic()
    with pytest.raises(SourceFailure) as error:
        client.read(transport_call(timeout=0.20),cancelled=lambda:False)
    assert error.value.code==Code.TIMEOUT and monotonic()-start<1.2
    assert_closed(client)


@pytest.mark.parametrize('phase',['handshake','headers','body'])
def test_cancel_during_real_io_closes_fds(tls_server_factory,phase):
    if phase=='handshake': server,client=tls_server_factory((),plaintext=True)
    elif phase=='headers': server,client=tls_server_factory(((b'HTTP/1.1 200 F',0),(b'ixture\r\n\r\n',1)))
    else: server,client=tls_server_factory(((b'HTTP/1.1 200 Fixture\r\nContent-Length: 8\r\n\r\nx',0),(b'xxxxxxx',1)))
    stop=Event(); timer=Timer(0.12,stop.set); timer.start()
    start=monotonic()
    try:
        with pytest.raises(SourceFailure) as error:
            client.read(transport_call(timeout=2),cancelled=stop.is_set)
        assert error.value.code==Code.CANCELLED and monotonic()-start<0.9
        assert server.accepted.is_set()
    finally:
        timer.cancel(); timer.join()
    assert_closed(client)


@pytest.mark.parametrize('kind',['length','stream','header','header_count','chunked','encoding','ambiguous','incomplete'])
def test_streaming_size_framing_header_bounds(tls_server_factory,kind):
    if kind=='length': payload=b'HTTP/1.1 200 Fixture\r\nContent-Length: 900000000\r\n\r\n'
    elif kind=='stream': payload=b'HTTP/1.1 200 Fixture\r\n\r\n'+b'x'*5000
    elif kind=='header': payload=b'HTTP/1.1 200 Fixture\r\nX-Huge: '+b'x'*10000+b'\r\n\r\n'
    elif kind=='header_count': payload=b'HTTP/1.1 200 Fixture\r\n'+b'X: y\r\n'*65+b'\r\n'
    elif kind=='chunked': payload=b'HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\n1388\r\n'+b'x'*5000+b'\r\n0\r\n\r\n'
    elif kind=='encoding': payload=response(b'gzipbomb',headers=b'Content-Encoding: gzip\r\n')
    elif kind=='ambiguous': payload=response(b'x',headers=b'Transfer-Encoding: chunked\r\n')
    else: payload=b'HTTP/1.1 200 Fixture\r\nContent-Length: 20\r\n\r\nshort'
    server,client=tls_server_factory(((payload,0),))
    client.max_body=1024
    with pytest.raises(SourceFailure) as error:
        client.read(transport_call(max_bytes=12000),cancelled=lambda:False)
    expected=Code.UNSUPPORTED if kind=='encoding' else Code.NETWORK if kind=='ambiguous' else Code.FAILURE if kind=='incomplete' else Code.LIMIT
    assert error.value.code==expected
    assert_closed(client)


def test_chunked_binary_success(tls_server_factory):
    payload=b'HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\n3\r\na\x00b\r\n2\r\n\xffc\r\n0\r\nX-Trailer: fixture\r\n\r\n'
    _,client=tls_server_factory(((payload,0),))
    page=client.read(transport_call(),cancelled=lambda:False)
    assert page.items[0].content==b'a\x00b\xffc'
    assert page.bytes_received==len(payload)+len(page.items[0].url.encode())
    assert_closed(client)


@pytest.mark.parametrize('change',['untrusted','hostname','insecure'])
def test_real_certificate_and_hostname_validation(tls_server_factory,change):
    server,client=tls_server_factory(((response(),0),))
    if change=='untrusted': client._context=lambda:ssl.create_default_context()
    elif change=='hostname': client._endpoint=lambda target,spec:('wrong.fixture.invalid','127.0.0.1',server.port,urlsplit(target.url))
    else:
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT); context.check_hostname=False; context.verify_mode=ssl.CERT_NONE
        client._context=lambda:context
    with pytest.raises(SourceFailure) as error:
        client.read(transport_call(),cancelled=lambda:False)
    assert error.value.code==Code.NETWORK
    assert not server.requests and (server.sni or change=='insecure')
    assert_closed(client)


@pytest.mark.parametrize('ips',[('127.0.0.1',),('10.0.0.1',),('169.254.169.254',),('::1',),('::ffff:8.8.8.8',),('1.1.1.1','192.168.0.1'),(),('SYNTHETIC_BAD_IP',)])
def test_product_rejects_forged_private_pins_before_io(ips,monkeypatch):
    client=PublicHTTPTransport((SPEC,))
    monkeypatch.setattr(module.socket,'socket',lambda *args:(_ for _ in ()).throw(AssertionError('unexpected I/O')))
    call=replace(transport_call(),target=PinnedTarget(HOST,ips,'https://fixture.invalid/note'))
    with pytest.raises(SourceFailure): client.read(call,cancelled=lambda:False)


@pytest.mark.parametrize('url',['http://fixture.invalid/','https://fixture.invalid:8443/','https://user:secret@fixture.invalid/',
                                'https://foreign.invalid/','https://fixture.invalid/#secret','https://fixture.invalid/\r\nCookie:secret'])
def test_product_rejects_bad_target_urls_before_io(url,monkeypatch):
    client=PublicHTTPTransport((SPEC,))
    monkeypatch.setattr(module.socket,'socket',lambda *args:(_ for _ in ()).throw(AssertionError('unexpected I/O')))
    call=replace(transport_call(),target=PinnedTarget(HOST,('1.1.1.1',),url))
    with pytest.raises(SourceFailure): client.read(call,cancelled=lambda:False)


def test_literal_ip_443_one_attempt_no_dns_hidden_fallback(monkeypatch):
    attempts=[]
    class RejectedSocket:
        family=socket.AF_INET
        closed=False
        def setblocking(self,flag): pass
        def connect_ex(self,address): attempts.append(address); return errno.ECONNREFUSED
        def close(self): self.closed=True
    sock=RejectedSocket()
    monkeypatch.setattr(module.socket,'socket',lambda *args:sock)
    monkeypatch.setattr(module.socket,'getaddrinfo',lambda *args:(_ for _ in ()).throw(AssertionError('DNS forbidden')))
    call=replace(transport_call(),target=PinnedTarget(HOST,('1.1.1.1','8.8.8.8'),'https://fixture.invalid/note'))
    with pytest.raises(SourceFailure) as error: PublicHTTPTransport((SPEC,)).read(call,cancelled=lambda:False)
    assert error.value.code==Code.FAILURE and attempts==[('1.1.1.1',443)] and sock.closed


def test_invalid_call_private_sessions_and_expiry_no_io(monkeypatch):
    client=PublicHTTPTransport((SPEC,))
    monkeypatch.setattr(module.socket,'socket',lambda *args:(_ for _ in ()).throw(AssertionError('unexpected I/O')))
    call=transport_call()
    for malformed in (replace(call,timeout_seconds=float('nan')),replace(call,max_bytes=-1),
                      replace(call,operation=ReadOperation('send','send','write')),
                      replace(call,request=replace(call.request,session_ref='web:synthetic')),
                      replace(call,cursor='SYNTHETIC_PRIVATE_CURSOR')):
        with pytest.raises(SourceFailure): client.read(malformed,cancelled=lambda:False)
    with pytest.raises(SourceFailure) as error:
        client.read(transport_call(deadline=datetime.now(timezone.utc)-timedelta(seconds=1)),cancelled=lambda:False)
    assert error.value.code==Code.TIMEOUT
    with pytest.raises(SourceFailure) as error: client.read(call,cancelled=lambda:True)
    assert error.value.code==Code.CANCELLED


def test_budget_counts_received_headers_and_retained_url(tls_server_factory):
    payload=response(b'x'*40)
    _,client=tls_server_factory(((payload,0),))
    call=transport_call(max_bytes=65)
    with pytest.raises(SourceFailure) as error: client.read(call,cancelled=lambda:False)
    assert error.value.code==Code.LIMIT
    assert_closed(client)


def test_forged_call_cannot_raise_request_byte_ceiling(tls_server_factory):
    _,client=tls_server_factory(((response(b'x'*40),0),))
    call=transport_call(max_bytes=100000)
    call=replace(call,request=replace(call.request,limits=replace(call.request.limits,bytes=65)))
    with pytest.raises(SourceFailure) as error: client.read(call,cancelled=lambda:False)
    assert error.value.code==Code.LIMIT
    assert_closed(client)


def test_forged_call_cannot_raise_request_timeout(tls_server_factory):
    _,client=tls_server_factory(((b'HTTP/1.1 200 Fixture\r\nContent-Length: 2\r\n\r\nx',0),(b'y',2)))
    call=transport_call(timeout=5)
    call=replace(call,request=replace(call.request,limits=replace(call.request.limits,timeout_seconds=1)))
    start=monotonic()
    with pytest.raises(SourceFailure) as error: client.read(call,cancelled=lambda:False)
    assert error.value.code==Code.TIMEOUT and monotonic()-start<1.7
    assert_closed(client)


def test_total_header_budget_and_repeated_interim_responses(tls_server_factory):
    payload=b'HTTP/1.1 100 Continue\r\n\r\n'*50+response()
    _,client=tls_server_factory(((payload,0),))
    client.max_headers=1024
    with pytest.raises(SourceFailure) as error: client.read(transport_call(),cancelled=lambda:False)
    assert error.value.code==Code.LIMIT
    assert_closed(client)


def test_bad_chunk_framing_errors_are_static(tls_server_factory,caplog):
    payload=b'HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\nSYNTHETIC_SECRET\r\n'
    _,client=tls_server_factory(((payload,0),))
    with pytest.raises(SourceFailure) as error: client.read(transport_call(),cancelled=lambda:False)
    assert error.value.code==Code.FAILURE and str(error.value)=='source_failure'
    assert 'SYNTHETIC_SECRET' not in str(error.value)+caplog.text
    assert_closed(client)


def test_real_fixture_peer_cannot_pass_public_target_check(tls_server_factory):
    from radar.adapters.sources.generic.network import URLGuard
    _,client=tls_server_factory(((response(),0),))
    page=client.read(transport_call(),cancelled=lambda:False)
    assert page.peer_ip=='127.0.0.1'
    with pytest.raises(SourceFailure) as error:
        URLGuard.check_peer(PinnedTarget(HOST,('1.1.1.1',),'https://fixture.invalid/note'),page.peer_ip)
    assert error.value.code==Code.NETWORK
    assert_closed(client)


def test_request_absolute_deadline_not_only_call_timeout(tls_server_factory):
    server,client=tls_server_factory(((b'HTTP/1.1 200 F',0),(response(),1)))
    start=monotonic()
    call=transport_call(timeout=5,deadline=datetime.now(timezone.utc)+timedelta(seconds=0.15))
    with pytest.raises(SourceFailure) as error: client.read(call,cancelled=lambda:False)
    assert error.value.code==Code.TIMEOUT and monotonic()-start<0.9
    assert_closed(client)


def test_production_has_no_private_endpoint_or_context_parameters():
    for kwargs in ({'allow_private':True},{'port':8443},{'context':ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)}):
        with pytest.raises(TypeError): PublicHTTPTransport((SPEC,),**kwargs)


@pytest.mark.parametrize('payload',[
    b'HTTP/1.1 200 Fixture\r\n',
    b'HTTP/1.1 200 Fixture\r\nX: incomplete',
    b'HTTP/1.1 200 Fixture\n\n',
    b'HTTP/1.1 200 Fixture\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n',
    b'HTTP/1.1 200 Fixture\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx',
])
def test_incomplete_or_ambiguous_headers_and_trailers_rejected(tls_server_factory,payload):
    _,client=tls_server_factory(((payload,0),))
    with pytest.raises(SourceFailure): client.read(transport_call(),cancelled=lambda:False)
    assert_closed(client)
