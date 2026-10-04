"""Real loopback socket/TLS and ephemeral SYNTHETIC certificates, never a source."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import socket
import ssl
import subprocess
from threading import Event, Lock, Thread
from time import monotonic
from urllib.parse import urlsplit

import pytest

from radar.adapters.local.private_vault import create_private_directory
from radar.adapters.sources.generic.model import PinnedTarget, ReadOperation, ReadRequest, SourceSpec, TransportCall
from radar.adapters.sources.transports import PublicHTTPTransport

ROOT = Path(__file__).resolve().parents[2]
HOST = 'fixture.invalid'
SPEC = SourceSpec('source:synthetic','web','stdlib','http', (ReadOperation('read','public_get'),),
                  allowed_hosts=(HOST,),min_interval_seconds=0)


@pytest.fixture(scope='session')
def tls_material(tmp_path_factory):
    output = ROOT / '.local' / 'a17-build'
    output.mkdir(parents=True,exist_ok=True)
    binary = output / ('tls-cert-fixture.exe' if os.name == 'nt' else 'tls-cert-fixture')
    environment = {**os.environ,'GOPROXY':'off','GOSUMDB':'off','GOWORK':'off','GOTOOLCHAIN':'local','GO111MODULE':'off'}
    result = subprocess.run(['go','build','-trimpath','-buildvcs=false','-o',str(binary),
                             str(Path(__file__).with_name('tls_cert_fixture.go'))],
                            env=environment,capture_output=True,timeout=60)
    assert result.returncode == 0, 'offline stdlib TLS fixture build failed'
    parent = tmp_path_factory.mktemp('a17-synthetic-tls')
    directory = create_private_directory(parent / 'new-private-certificates')
    result = subprocess.run([str(binary),'--synthetic-only',str(directory)],capture_output=True,timeout=10)
    assert result.returncode == 0 and result.stdout == b'' and result.stderr == b''
    return directory


class TLSServer:
    def __init__(self, material, chunks, *, plaintext=False, alpn='http/1.1'):
        self.chunks, self.plaintext = chunks, plaintext
        self.stop, self.accepted, self.request_seen = Event(), Event(), Event()
        self.sni, self.requests, self.sockets, self.workers = [], [], [], []
        self.lock = Lock()
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(material/'server.pem',material/'server-key.pem')
        self.context.set_alpn_protocols([alpn])
        self.context.set_servername_callback(lambda sock,name,context:self.sni.append(name))
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1',0)); self.listener.listen(8); self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.thread = Thread(target=self._accept,daemon=True); self.thread.start()

    def _accept(self):
        while not self.stop.is_set():
            try:
                sock,_ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self.lock: self.sockets.append(sock)
            self.accepted.set()
            worker = Thread(target=self._serve,args=(sock,),daemon=True)
            self.workers.append(worker); worker.start()

    def _serve(self,sock):
        try:
            sock.settimeout(2)
            if self.plaintext:
                self.stop.wait(2)
                return
            sock = self.context.wrap_socket(sock,server_side=True)
            with self.lock: self.sockets.append(sock)
            request = bytearray()
            while b'\r\n\r\n' not in request and len(request)<8192:
                chunk=sock.recv(1024)
                if not chunk: return
                request.extend(chunk)
            self.requests.append(bytes(request)); self.request_seen.set()
            for data, delay in self.chunks:
                if self.stop.wait(delay): return
                sock.sendall(data)
        except (OSError,ssl.SSLError):
            pass  # Expected client aborts/cert negatives, not a production logger.
        finally:
            sock.close()

    def close(self):
        self.stop.set(); self.listener.close()
        with self.lock:
            for sock in self.sockets:
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                sock.close()
        self.thread.join(2)
        for thread in self.workers: thread.join(2)
        assert not self.thread.is_alive() and not any(t.is_alive() for t in self.workers)
        assert self.listener.fileno() == -1 and all(s.fileno()==-1 for s in self.sockets)


class LoopbackFixtureTransport(PublicHTTPTransport):
    """TEST ONLY endpoint. Production has no allow_private/port/context bypass."""
    fixture_only = True
    guarded_live = False

    def __init__(self, server, material, **kwargs):
        super().__init__((SPEC,),**kwargs)
        self.server, self.material, self.tls_sockets = server,material,[]

    def _endpoint(self,target,spec):
        assert target.hostname == HOST and target.ips == ('127.0.0.1',)
        return HOST,'127.0.0.1',self.server.port,urlsplit(target.url)

    def _context(self):
        sockets = self.tls_sockets
        class TrackedContext(ssl.SSLContext):
            def wrap_socket(self,*args,**kwargs):
                sock = super().wrap_socket(*args,**kwargs)
                sockets.append(sock)
                return sock
        context = TrackedContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(self.material/'ca.pem')
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(['http/1.1'])
        return context


@pytest.fixture
def tls_server_factory(tls_material):
    servers=[]
    def factory(chunks,**kwargs):
        server=TLSServer(tls_material,chunks,**kwargs); servers.append(server)
        return server,LoopbackFixtureTransport(server,tls_material)
    yield factory
    for server in servers: server.close()


def transport_call(*, url='https://fixture.invalid/note?synthetic=marker',timeout=1,max_bytes=100000,deadline=None):
    request = ReadRequest('owner:synthetic','user:synthetic','request:synthetic',SPEC.source_ref,'read',
                          deadline or datetime.now(timezone.utc)+timedelta(seconds=5),target_url=url)
    return TransportCall(request,SPEC.operations[0],PinnedTarget(HOST,('127.0.0.1',),url),None,timeout,max_bytes)
