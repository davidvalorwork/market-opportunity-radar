"""Opt-in A8 adapter: preserve Reader state; isolate guard context per request."""
from contextvars import ContextVar

from radar.adapters.sources.generic.engine import Reader
from radar.adapters.sources.generic.model import Code, Report, SourceFailure, opaque
from radar.adapters.sources.generic.network import URLGuard
from .public_dns import PublicDNSResolver


class _ContextGuard(URLGuard):
    def __init__(self,context):
        super().__init__(lambda _:())
        self.context=context

    def pin(self,url,*,allowed_hosts):
        prepared=self.context.get()
        if prepared is None:
            raise SourceFailure(Code.INVALID)
        return prepared.guard.pin(url,allowed_hosts=allowed_hosts)


class BoundPublicReader(Reader):
    def __init__(self,*,resolver,**kwargs):
        if not isinstance(resolver,PublicDNSResolver) or 'url_guard' in kwargs:
            raise SourceFailure(Code.INVALID)
        self._resolver_factory=resolver
        self._request_dns=ContextVar('request_dns',default=None)  # Instance-owned, not module/global mutable.
        super().__init__(url_guard=_ContextGuard(self._request_dns),**kwargs)

    def read(self,request,*,cancelled=lambda:False):
        try:
            prepared=self._resolver_factory.prepare(request,cancelled=cancelled)
        except SourceFailure as exc:
            return Report(request.request_ref if opaque(getattr(request,'request_ref',None)) else 'request:invalid',
                          request.source_ref if opaque(getattr(request,'source_ref',None)) else 'source:invalid',exc.code,False)
        token=self._request_dns.set(prepared)
        try:
            # Same A8 instance/locks/throttle, and same clamped request through DNS/HTTP.
            return super().read(prepared.request,cancelled=cancelled)
        finally:
            self._request_dns.reset(token)
