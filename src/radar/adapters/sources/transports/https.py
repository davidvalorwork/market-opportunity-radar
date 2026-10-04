"""Public GET only. Fixed IP/443, real TLS hostname verification, no resolver/proxy."""
from datetime import datetime, timezone
from ipaddress import ip_address
import math
import socket
import ssl
from urllib.parse import quote, urlsplit

from radar.adapters.sources.generic.engine import Registry
from radar.adapters.sources.generic.network import URLGuard
from radar.adapters.sources.generic.model import Code, PinnedTarget, SourceFailure, SourceSpec, TransportCall, opaque, utc
from .response import response_page
from .stream import Budget, GuardedStream, connect, send


class PublicHTTPTransport:
    fixture_only = False
    # Technical isolation contract, NOT evidence of a tested source/capability.
    guarded_live = True

    def __init__(self, specs: tuple[SourceSpec, ...], *, max_body_bytes=512*1024,
                 max_header_bytes=32768, max_line_bytes=8192):
        if (not isinstance(specs, tuple) or not specs
            or any(not isinstance(s, SourceSpec) or s.route != 'http' or s.requires_session for s in specs)
            or type(max_body_bytes) is not int or not 1 <= max_body_bytes <= 512*1024
            or type(max_header_bytes) is not int or not 1024 <= max_header_bytes <= 32768
            or type(max_line_bytes) is not int or not 128 <= max_line_bytes <= 8192):
            raise SourceFailure(Code.INVALID)
        self.registry = Registry(specs)
        self.max_body, self.max_headers, self.max_line = max_body_bytes, max_header_bytes, max_line_bytes

    def __repr__(self):
        return 'PublicHTTPTransport(<private>)'

    def _endpoint(self, target, spec):
        if (not isinstance(target, PinnedTarget) or not isinstance(target.url, str)
            or len(target.url.encode('utf-8')) > 4096 or any(ord(c) < 33 for c in target.url)
            or '\\' in target.url or not isinstance(target.ips, tuple) or not 1 <= len(target.ips) <= 16):
            raise SourceFailure(Code.NETWORK)
        # Reuse A8's frozen public guard with the ALREADY pinned tuple as resolver.
        # No socket/getaddrinfo/DNS invocation happens here.
        pinned = URLGuard(lambda _: target.ips).pin(target.url,allowed_hosts=spec.allowed_hosts)
        if pinned.hostname != target.hostname:
            raise SourceFailure(Code.NETWORK)
        addresses = tuple(ip_address(i) for i in pinned.ips)
        if any('%' in str(p) for p in addresses):
            raise SourceFailure(Code.NETWORK)
        # Exactly one IP attempt per TransportCall. No hidden retries/failover.
        return target.hostname, str(addresses[0]), 443, urlsplit(pinned.url)

    def _context(self):
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(['http/1.1'])
        return context

    def read(self, call: TransportCall, *, cancelled):
        raw = secured = stream = None
        try:
            if (not isinstance(call, TransportCall) or not callable(cancelled)
                or type(call.timeout_seconds) not in (float,int) or not math.isfinite(call.timeout_seconds)
                or not 0 < call.timeout_seconds <= 60 or type(call.max_bytes) is not int or not 1 <= call.max_bytes <= 20_000_000
                or not utc(call.request.deadline) or not call.request.limits.valid()
                or not all(opaque(v) for v in (call.request.owner_ref,call.request.actor_ref,call.request.request_ref,call.request.source_ref))):
                raise SourceFailure(Code.INVALID)
            spec = self.registry.get(call.request.source_ref)
            if spec is None or call.operation not in spec.operations or call.operation.name != call.request.operation:
                raise SourceFailure(Code.UNSUPPORTED)
            if call.request.session_ref is not None or call.request.account_ref is not None:
                raise SourceFailure(Code.REAUTH)
            if call.cursor is not None:
                raise SourceFailure(Code.UNSUPPORTED)
            seconds = min(call.timeout_seconds, call.request.limits.timeout_seconds,
                          (call.request.deadline-datetime.now(timezone.utc)).total_seconds())
            if seconds <= 0:
                raise SourceFailure(Code.TIMEOUT)
            budget = Budget(seconds,cancelled)
            budget.check()
            host, ip, port, parts = self._endpoint(call.target,spec)
            retained = len(call.target.url.encode('utf-8'))
            max_wire = min(min(call.max_bytes,call.request.limits.bytes)-retained,
                           self.max_body+self.max_headers)
            if max_wire <= 0:
                raise SourceFailure(Code.LIMIT)
            context = self._context()
            if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                raise SourceFailure(Code.NETWORK)
            raw = socket.socket(socket.AF_INET6 if ip_address(ip).version == 6 else socket.AF_INET,socket.SOCK_STREAM)
            address = (ip,port,0,0) if raw.family == socket.AF_INET6 else (ip,port)
            connect(raw,address,budget)
            if ip_address(raw.getpeername()[0]) != ip_address(ip):
                raise SourceFailure(Code.NETWORK)
            secured = context.wrap_socket(raw,server_hostname=host,do_handshake_on_connect=False)
            secured.setblocking(False)
            budget.ssl_call(secured,secured.do_handshake)
            peer = secured.getpeername()[0]
            if ip_address(peer) != ip_address(ip) or secured.selected_alpn_protocol() not in (None,'http/1.1'):
                raise SourceFailure(Code.NETWORK)
            path = quote(parts.path or '/',safe='/%:@!$&\'()*+,;=-._~')
            if parts.query:
                path += '?' + quote(parts.query,safe='/%?:@!$&\'()*+,;=-._~')
            request = (f'GET {path} HTTP/1.1\r\nHost: {host}\r\nAccept: */*\r\n'
                       'Accept-Encoding: identity\r\nUser-Agent: RadarPublicReader/1\r\nConnection: close\r\n\r\n').encode('ascii')
            send(secured,request,budget)
            stream = GuardedStream(secured,budget,max_wire=max_wire,max_headers=self.max_headers,
                                   max_line=self.max_line,read_ahead=min(4096,self.max_body+1))
            return response_page(stream,url=call.target.url,peer=peer,max_body=min(self.max_body,max_wire))
        except SourceFailure:
            raise
        except ssl.SSLCertVerificationError:
            raise SourceFailure(Code.NETWORK) from None
        except (ValueError,TypeError,AttributeError,UnicodeError):
            raise SourceFailure(Code.INVALID) from None
        except Exception:
            raise SourceFailure(Code.FAILURE) from None
        finally:
            if stream is not None:
                stream.close()
            for sock in (secured,raw):
                if sock is not None:
                    sock.close()
