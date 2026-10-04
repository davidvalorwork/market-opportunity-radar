"""Explicit host resolver factory; DNS results never grant source authority."""
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from ipaddress import ip_address
import json
import math
from pathlib import Path
import re
from threading import Lock
from time import monotonic

from radar.adapters.local.private_vault import VaultError, _absolute, _permissions
from radar.adapters.sources.generic.engine import Registry
from radar.adapters.sources.generic.model import Code, ReadRequest, SourceFailure, SourceSpec, opaque, utc
from radar.adapters.sources.generic.network import URLGuard
from .clock import Deadline
from .process import invoke


HOST=re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+')


def valid_host(host):
    return (isinstance(host,str) and len(host)<=253 and HOST.fullmatch(host) is not None
            and not any(label.startswith('xn--') for label in host.split('.')))


def strict_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError('duplicate_field')
        result[key]=value
    return result


@dataclass(frozen=True)
class DNSMetrics:
    calls: int
    dns_bytes: int
    queries: int
    pipe_bytes: int
    heap_alloc: int
    heap_sys: int
    usage_unknown: bool


class _Quota:
    def __init__(self,max_calls,max_bytes):
        self.lock=Lock()
        self.calls=self.dns_bytes=self.queries=self.pipe_bytes=self.heap_alloc=self.heap_sys=0
        self.usage_unknown=False
        self.max_calls,self.max_bytes=max_calls,max_bytes

    def stats(self):
        with self.lock:
            return DNSMetrics(self.calls,self.dns_bytes,self.queries,self.pipe_bytes,self.heap_alloc,self.heap_sys,self.usage_unknown)


@dataclass(frozen=True,repr=False)
class BoundResolver:
    factory: object=field(repr=False)
    request: ReadRequest=field(repr=False)
    hosts: tuple[str,...]=field(repr=False)
    deadline: Deadline=field(repr=False)
    quota: _Quota=field(repr=False)

    def __repr__(self):
        return 'BoundResolver(<private>)'

    @property
    def metrics(self):
        return self.quota.stats()

    def __call__(self,hostname):
        attempted=False
        try:
            self.deadline.remaining()
            if not valid_host(hostname) or hostname not in self.hosts:
                raise SourceFailure(Code.NETWORK)
            # Reuse A8 for domain policy, before even starting a child.
            URLGuard(lambda _:('1.1.1.1',)).pin('https://'+hostname+'/',allowed_hosts=self.hosts)
            with self.quota.lock:
                if self.quota.calls>=self.quota.max_calls:
                    raise SourceFailure(Code.LIMIT)
                self.quota.calls+=1
                # Reserve a fixed per-lookup byte ceiling: parallel calls cannot
                # reuse quota. Unused bytes are NOT recredited after errors.
                if self.quota.max_bytes<self.factory.max_dns_bytes:
                    raise SourceFailure(Code.LIMIT)
                self.quota.max_bytes-=self.factory.max_dns_bytes
            self.factory._check_helper()
            seconds=self.deadline.remaining()
            payload=json.dumps({'v':1,'hostname':hostname,'timeout_ms':min(60000,max(1,math.ceil(seconds*1000))),
                                'max_ips':self.factory.max_ips,'max_dns_bytes':self.factory.max_dns_bytes,
                                'max_queries':self.factory.max_queries},separators=(',',':')).encode('ascii')
            attempted=True
            data=invoke(self.factory.helper,payload,self.deadline,max_output=self.factory.max_output)
            result=json.loads(data,object_pairs_hook=strict_object)
            if (not isinstance(result,dict) or set(result)!={'v','hostname','ips','dns_bytes','queries','heap_alloc','heap_sys'}
                or type(result['v']) is not int or result['v']!=1 or result['hostname']!=hostname
                or not isinstance(result['ips'],list) or not 1<=len(result['ips'])<=self.factory.max_ips
                or any(not isinstance(i,str) or len(i)>45 for i in result['ips'])
                or type(result['dns_bytes']) is not int or not 0<=result['dns_bytes']<=self.factory.max_dns_bytes
                or type(result['queries']) is not int or not 0<=result['queries']<=self.factory.max_queries
                or any(type(result[k]) is not int or not 0<=result[k]<=256*1024*1024 for k in ('heap_alloc','heap_sys'))):
                raise SourceFailure(Code.NETWORK)
            addresses=tuple(ip_address(i) for i in result['ips'])
            if len(set(addresses))!=len(addresses) or any(str(ip)!=text for ip,text in zip(addresses,result['ips'])):
                raise SourceFailure(Code.NETWORK)
            ips=tuple(result['ips'])
            # Canonical frozen A8 public-IP guard remains the policy authority.
            URLGuard(lambda _:ips).pin('https://'+hostname+'/',allowed_hosts=self.hosts)
            with self.quota.lock:
                self.quota.dns_bytes+=result['dns_bytes'];self.quota.queries+=result['queries']
                self.quota.pipe_bytes+=len(data)
                self.quota.heap_alloc=max(self.quota.heap_alloc,result['heap_alloc'])
                self.quota.heap_sys=max(self.quota.heap_sys,result['heap_sys'])
            self.deadline.remaining()
            return ips
        except SourceFailure:
            if attempted:
                with self.quota.lock:self.quota.usage_unknown=True
            raise
        except Exception:
            if attempted:
                with self.quota.lock:self.quota.usage_unknown=True
            raise SourceFailure(Code.NETWORK) from None


class RequestURLGuard(URLGuard):
    """A8 policy unchanged; preserve typed resolver failures swallowed by A8."""
    def __init__(self,bound):
        super().__init__(bound)
        self.bound=bound

    def pin(self,url,*,allowed_hosts):
        failure=[]
        def resolve(host):
            try:
                return self.bound(host)
            except SourceFailure as exc:
                failure.append(exc)
                return ()
        try:
            self.bound.deadline.remaining()
            result=URLGuard(resolve).pin(url,allowed_hosts=allowed_hosts)
            self.bound.deadline.remaining()
            return result
        except SourceFailure:
            if failure:
                raise failure[0] from None
            raise


@dataclass(frozen=True,repr=False)
class PreparedRead:
    request: ReadRequest=field(repr=False)
    resolver: BoundResolver=field(repr=False)
    guard: RequestURLGuard=field(repr=False)


class PublicDNSResolver:
    def __init__(self,*,helper,helper_sha256,specs,max_timeout_seconds=10,
                 max_dns_bytes=16384,max_total_dns_bytes=65536,max_queries=8,max_calls=4,
                 max_ips=16,max_output_bytes=2048):
        if (not isinstance(helper_sha256,str) or re.fullmatch('[a-f0-9]{64}',helper_sha256) is None
            or not isinstance(specs,tuple) or not specs or any(not isinstance(s,SourceSpec) or s.route!='http' or s.requires_session for s in specs)
            or type(max_timeout_seconds) not in (int,float) or not math.isfinite(max_timeout_seconds) or not 0<max_timeout_seconds<=60
            or type(max_dns_bytes) is not int or not 1<=max_dns_bytes<=65536
            or type(max_total_dns_bytes) is not int or not max_dns_bytes<=max_total_dns_bytes<=1048576
            or type(max_queries) is not int or not 1<=max_queries<=32
            or type(max_calls) is not int or not 1<=max_calls<=32
            or type(max_ips) is not int or not 1<=max_ips<=16
            or type(max_output_bytes) is not int or not 128<=max_output_bytes<=2048):
            raise SourceFailure(Code.INVALID)
        self.registry=Registry(specs)
        if any(not valid_host(h) for spec in specs for h in spec.allowed_hosts):
            raise SourceFailure(Code.INVALID)
        try:
            self.helper=Path(helper)
        except (TypeError,ValueError):
            raise SourceFailure(Code.INVALID) from None
        self.helper_hash=helper_sha256
        self.max_timeout,self.max_dns_bytes,self.max_total_dns_bytes=max_timeout_seconds,max_dns_bytes,max_total_dns_bytes
        self.max_queries,self.max_calls,self.max_ips,self.max_output=max_queries,max_calls,max_ips,max_output_bytes
        self._check_helper()

    def __repr__(self):
        return 'PublicDNSResolver(<private>)'

    def _check_helper(self):
        try:
            path=_absolute(self.helper)
            _permissions(path.parent,directory=True)
            info=_permissions(path)
            if not 0<info.st_size<=16*1024*1024:
                raise SourceFailure(Code.INVALID)
            digest=sha256()
            with path.open('rb') as file:
                count=0
                while chunk:=file.read(65536):
                    count+=len(chunk)
                    if count>16*1024*1024:
                        raise SourceFailure(Code.INVALID)
                    digest.update(chunk)
            if digest.hexdigest()!=self.helper_hash:
                raise SourceFailure(Code.NETWORK)
        except SourceFailure:
            raise
        except (VaultError,OSError,ValueError,TypeError):
            raise SourceFailure(Code.NETWORK) from None

    def prepare(self,request,*,cancelled):
        try:
            valid=(isinstance(request,ReadRequest) and callable(cancelled) and utc(request.deadline)
                   and request.limits.valid() and all(opaque(v) for v in (request.owner_ref,request.actor_ref,request.request_ref,request.source_ref)))
        except Exception:
            valid=False
        if not valid:
            raise SourceFailure(Code.INVALID)
        spec=self.registry.get(request.source_ref)
        if spec is None or request.operation not in tuple(op.name for op in spec.operations):
            raise SourceFailure(Code.UNSUPPORTED)
        if request.account_ref is not None or request.session_ref is not None:
            raise SourceFailure(Code.REAUTH)
        now=datetime.now(timezone.utc)
        end=min(request.deadline,now+timedelta(seconds=min(self.max_timeout,request.limits.timeout_seconds)))
        seconds=(end-now).total_seconds()
        deadline=Deadline(end,monotonic()+seconds,cancelled)
        deadline.remaining()
        adjusted=replace(request,deadline=end)
        quota=_Quota(min(self.max_calls,request.limits.requests),self.max_total_dns_bytes)
        bound=BoundResolver(self,adjusted,spec.allowed_hosts,deadline,quota)
        return PreparedRead(adjusted,bound,RequestURLGuard(bound))
