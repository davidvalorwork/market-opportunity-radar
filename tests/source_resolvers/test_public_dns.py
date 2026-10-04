"""Real child/process and loopback DNS protocol, synthetic authority/data only."""
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
import subprocess
from threading import Event, Timer
from time import monotonic, sleep
import tracemalloc

import pytest

from radar.adapters.sources.generic.model import Code, SourceFailure
from radar.adapters.sources.resolvers import PublicDNSResolver
from radar.adapters.sources.resolvers import process as module
from conftest import HOST, SPEC, request


@pytest.fixture
def own_processes(monkeypatch):
    actual=module.subprocess.Popen
    children=[]
    def observed(*args,**kwargs):
        child=actual(*args,**kwargs);children.append(child);return child
    monkeypatch.setattr(module.subprocess,'Popen',observed)
    yield children
    for child in children:
        assert child.poll() is not None
        assert child.wait(timeout=0.2) is not None
        assert all(pipe.closed for pipe in (child.stdin,child.stdout,child.stderr))


def test_real_loopback_dns_private_pipe_public_ips_metrics(resolver_factory,own_processes,capsys,caplog):
    factory=resolver_factory()
    prepared=factory.prepare(request(),cancelled=lambda:False)
    ips=prepared.resolver(HOST)
    assert set(ips)=={'1.1.1.1','2606:4700:4700::1111'}
    target=prepared.guard.pin('https://fixture.invalid/note',allowed_hosts=SPEC.allowed_hosts)
    assert target.ips and target.hostname==HOST
    stats=prepared.resolver.metrics
    assert stats.calls==2 and 0<stats.dns_bytes<32768 and stats.queries==4
    assert stats.heap_sys>=stats.heap_alloc>0 and stats.pipe_bytes>0
    assert all(child.args==[str(factory.helper)] for child in own_processes)
    assert HOST not in repr(prepared)+repr(prepared.resolver)+repr(factory)+repr(stats)+caplog.text
    assert capsys.readouterr().out==''
    with pytest.raises(FrozenInstanceError):prepared.resolver.deadline=request().deadline


@pytest.mark.parametrize('mode',['host','empty','duplicate','many','private','mapped','invalid','unknown','bytes','queries','bool','duplicate-field','malformed'])
def test_forged_or_incomplete_output_rejected(resolver_factory,own_processes,mode):
    bound=resolver_factory('forged:'+mode).prepare(request(),cancelled=lambda:False).resolver
    with pytest.raises(SourceFailure) as error:bound(HOST)
    assert error.value.code==Code.NETWORK and 'SYNTHETIC' not in str(error.value)


@pytest.mark.parametrize('mode',['stdout-flood','stderr-flood'])
def test_pipe_overflow_kills_awaits_real_child(resolver_factory,own_processes,mode):
    bound=resolver_factory(mode).prepare(request(),cancelled=lambda:False).resolver
    start=monotonic()
    with pytest.raises(SourceFailure) as error:bound(HOST)
    assert error.value.code==Code.LIMIT and monotonic()-start<2


@pytest.mark.parametrize('use_guard',[False,True])
def test_timeout_of_slow_real_child_preserved_and_closed(resolver_factory,own_processes,use_guard):
    prepared=resolver_factory('slow',max_timeout_seconds=0.15).prepare(request(),cancelled=lambda:False)
    start=monotonic()
    with pytest.raises(SourceFailure) as error:
        if use_guard:prepared.guard.pin('https://fixture.invalid/',allowed_hosts=SPEC.allowed_hosts)
        else:prepared.resolver(HOST)
    assert error.value.code==Code.TIMEOUT and monotonic()-start<1.2 and len(own_processes)==1


@pytest.mark.parametrize('mode',['slow','delayed-dns'])
def test_cancel_during_dns_io_kills_and_reaps(resolver_factory,own_processes,mode):
    stop=Event();timer=Timer(0.12,stop.set)
    prepared=resolver_factory(mode).prepare(request(),cancelled=stop.is_set)
    timer.start();start=monotonic()
    try:
        with pytest.raises(SourceFailure) as error:prepared.guard.pin('https://fixture.invalid/',allowed_hosts=SPEC.allowed_hosts)
        assert error.value.code==Code.CANCELLED and monotonic()-start<1.2
    finally:timer.cancel();timer.join()
    assert len(own_processes)==1
    assert prepared.resolver.metrics.usage_unknown is True


def test_late_process_creation_after_expiry_is_owned_and_closed(resolver_factory,own_processes,monkeypatch):
    prepared=resolver_factory('slow',max_timeout_seconds=0.12).prepare(request(),cancelled=lambda:False)
    actual=module.subprocess.Popen
    def delayed(*args,**kwargs):
        child=actual(*args,**kwargs);sleep(0.18);return child
    monkeypatch.setattr(module.subprocess,'Popen',delayed)
    with pytest.raises(SourceFailure) as error:prepared.resolver(HOST)
    assert error.value.code==Code.TIMEOUT and len(own_processes)==1


@pytest.mark.parametrize('maxima',[{'max_dns_bytes':8},{'max_queries':1},{'max_ips':1}])
def test_real_dns_wire_result_budgets(resolver_factory,own_processes,maxima):
    bound=resolver_factory(**maxima).prepare(request(),cancelled=lambda:False).resolver
    with pytest.raises(SourceFailure) as error:bound(HOST)
    assert error.value.code==Code.LIMIT


def test_separate_cumulative_dns_quota_no_recredit(resolver_factory,own_processes):
    prepared=resolver_factory(max_calls=2,max_total_dns_bytes=32768).prepare(request(),cancelled=lambda:False)
    assert prepared.resolver(HOST) and prepared.resolver(HOST)
    with pytest.raises(SourceFailure) as error:prepared.guard.pin('https://fixture.invalid/',allowed_hosts=SPEC.allowed_hosts)
    assert error.value.code==Code.LIMIT and len(own_processes)==2
    prepared=resolver_factory(max_total_dns_bytes=16384).prepare(request(),cancelled=lambda:False)
    assert prepared.resolver(HOST)
    with pytest.raises(SourceFailure) as error:prepared.resolver(HOST)
    assert error.value.code==Code.LIMIT and len(own_processes)==3


@pytest.mark.parametrize('host',['foreign.invalid','é.invalid','xn--abc.invalid','fixture.invalid.','fixture.invalid\nsecret',
                                 '127.0.0.1','-bad.invalid','bad-.invalid','x'*64+'.invalid'])
def test_ambiguous_or_unallowed_host_no_process(resolver_factory,own_processes,host):
    bound=resolver_factory().prepare(request(),cancelled=lambda:False).resolver
    with pytest.raises(SourceFailure):bound(host)
    assert not own_processes


def test_private_dns_answers_remain_rejected(resolver_factory,own_processes):
    bound=resolver_factory('private-dns').prepare(request(),cancelled=lambda:False).resolver
    with pytest.raises(SourceFailure) as error:bound(HOST)
    assert error.value.code==Code.NETWORK


def test_secret_stderr_is_static_not_logged(resolver_factory,own_processes,caplog,capsys):
    bound=resolver_factory('stderr-secret').prepare(request(),cancelled=lambda:False).resolver
    with pytest.raises(SourceFailure) as error:bound(HOST)
    assert str(error.value)=='network_boundary_rejected'
    assert '555123456' not in str(error.value)+repr(bound)+caplog.text+capsys.readouterr().out


def test_minimal_environment_does_not_inherit_tokens_or_proxy(monkeypatch):
    monkeypatch.setenv('SYNTHETIC_PRIVATE_TOKEN','SYNTHETIC_SECRET')
    monkeypatch.setenv('HTTPS_PROXY','http://synthetic-secret.invalid')
    environment=module.helper_environment()
    assert set(environment)==({'SystemRoot'} if os.name=='nt' else set())
    assert 'SYNTHETIC' not in repr(environment) and 'PROXY' not in repr(environment)


def test_expired_cancelled_invalid_prepare_no_process(resolver_factory,own_processes):
    factory=resolver_factory()
    with pytest.raises(SourceFailure) as error:factory.prepare(request(deadline=datetime.now(timezone.utc)-timedelta(seconds=1)),cancelled=lambda:False)
    assert error.value.code==Code.TIMEOUT
    with pytest.raises(SourceFailure) as error:factory.prepare(request(),cancelled=lambda:True)
    assert error.value.code==Code.CANCELLED
    for bad in (replace(request(),owner_ref='SYNTHETIC_PRIVATE_NAME'),replace(request(),session_ref='web:synthetic'),
                replace(request(),operation='send'),replace(request(),deadline=datetime.now()),replace(request(),limits=None)):
        with pytest.raises(SourceFailure):factory.prepare(bad,cancelled=lambda:False)
    assert not own_processes


def test_invalid_cancel_callback_does_not_leak_or_start_process(resolver_factory,own_processes):
    factory=resolver_factory()
    def broken():raise RuntimeError('SYNTHETIC_SECRET_PHONE_555123456')
    for callback in (broken,lambda:None,lambda:'false'):
        with pytest.raises(SourceFailure) as error:factory.prepare(request(),cancelled=callback)
        assert 'SYNTHETIC' not in str(error.value)
    assert not own_processes


def test_hash_and_private_acl_preflight_no_process(dns_helpers,resolver_factory,own_processes,tmp_path):
    factory=resolver_factory()
    with pytest.raises(SourceFailure):PublicDNSResolver(helper=factory.helper,helper_sha256='0'*64,specs=(SPEC,))
    factory.helper.write_bytes(b'REPLACED_SYNTHETIC_BINARY')
    with pytest.raises(SourceFailure):factory.prepare(request(),cancelled=lambda:False).resolver(HOST)
    assert not own_processes
    public=tmp_path/'public-binary';public.write_bytes(dns_helpers['synthetic'].read_bytes())
    if os.name!='nt':public.chmod(0o755)
    with pytest.raises(SourceFailure):PublicDNSResolver(helper=public,helper_sha256=sha256(public.read_bytes()).hexdigest(),specs=(SPEC,))


def test_synthetic_latency_and_runtime_memory(resolver_factory,own_processes):
    bound=resolver_factory().prepare(request(),cancelled=lambda:False).resolver
    tracemalloc.start();start=monotonic()
    try:
        for _ in range(3):assert bound(HOST)
        _,peak=tracemalloc.get_traced_memory()
    finally:tracemalloc.stop()
    stats=bound.metrics
    print(f'synthetic_dns calls={stats.calls} dns_application_bytes={stats.dns_bytes} queries={stats.queries} '
          f'wall_seconds={monotonic()-start:.6f} python_traced_peak_bytes={peak} '
          f'helper_heap_alloc_max={stats.heap_alloc} helper_heap_sys_max={stats.heap_sys}')
    assert stats.calls==3 and peak<2*1024*1024
