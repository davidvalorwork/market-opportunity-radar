"""Real SQLite/A6/A8/A9/A10/age + explicitly synthetic reasoner/HTTP, offline."""
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from radar import contracts
from radar.adapters.local.contextual import (SQLiteContextualStore, EnabledConversationContext,
    CapturedResearch, ContextualHandoff, build_reasoner, compose_handler)
from radar.adapters.local.conversations import SQLiteConversations
from radar.adapters.local.codec import loads
from radar.adapters.local.general_runtime import GeneralRuntime, GeneralError
from radar.adapters.local.general_ingress import PrivateIngress
from radar.adapters.local.private_vault import PrivateVault, create_private_directory
from radar.adapters.local.sqlite import SQLiteStore, stamp
from radar.adapters.local.tasks import SQLiteTaskStore
from radar.adapters.local.telegram import Directory
from radar.adapters.openrouter.client import OpenRouterLLM, InvalidRequest, LLMDisabled, PrivacyRefused
from radar.adapters.sources.generic import Reader, Registry, SourceSpec, ReadOperation, ReadRequest, RawItem
from radar.adapters.sources.generic.fixtures import FixtureTransport, FixtureCapabilities, FixtureAccess, fixture_page
from radar.adapters.telegram.consent import CONSENT_VERSION
from radar.application.contextual.model import ReplyRequest, ReplyBudget, ContextualError
from radar.application.contextual.composer import ContextualComposer, SCHEMA_NAME, PROMPT_VERSION
from radar.application.conversations import Account, Channel, Conversations, Incoming, IncomingPage
from radar.application.research.models import SourceMaterial, SourceUnavailable
from radar.application.tasks import Authority, TaskRouter, Budget
from radar.ports.types import Capability, SecretValue, StructuredRequest, StructuredResult, ConditionalConflict

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026,10,4,12,tzinfo=timezone.utc)
OWNER, ACTOR = 'owner:synthetic', 'user:synthetic'
MODEL = 'synthetic/context-model'
PRIVATE = 'PRIVATE_SYNTHETIC Elena +58-000-123-4567'


@pytest.fixture(scope='session')
def contextual_binaries():
    output = ROOT/'.local'/'contextual-vault-build'
    output.mkdir(parents=True,exist_ok=True)
    env = {**os.environ,'GOPROXY':'off','GOSUMDB':'off','GOTOOLCHAIN':'local','GOWORK':'off'}
    suffix = '.exe' if os.name == 'nt' else ''
    helper, keygen = output/('vault'+suffix), output/('synthetic-keys'+suffix)
    for target,package in ((helper,'./cmd/vault'),(keygen,'./cmd/synthetic-keys')):
        result = subprocess.run(['go','build','-mod=readonly','-trimpath','-buildvcs=false','-o',str(target),package],
            cwd=ROOT/'helpers'/'private-vault',env=env,capture_output=True,timeout=60)
        assert result.returncode == 0, 'offline crypto helper failed'
    return helper,keygen


class Clock:
    current = NOW
    def now(self): return self.current


class Policy:
    live, model_allowed, query_allowed = True, True, False
    stages = []
    def check(self, request, *, stage, now):
        if not self.live:
            raise ConditionalConflict('runtime_cancelled_or_lease_lost')
    def approve_reasoner(self, request, *, now):
        if not self.model_allowed or request.model_ref.endswith(':free'):
            raise PermissionError('denied')
    def approve_public_query(self, request, *, query, now):
        if not self.query_allowed or query != 'SYNTHETIC astronomy question':
            raise PermissionError('denied')


class Caps:
    def get(self, *, owner_ref, platform, backend, operation, session_ref=None):
        return Capability(platform,backend,operation,'probado_local','synthetic',NOW.date(),True)


class FixtureReasoner:
    def __init__(self):
        self.requests, self.mutate, self.fail = [], None, False
    def generate(self, *, owner_ref, request):
        self.requests.append(request)
        if self.fail: raise RuntimeError('PRIVATE_BODY lost response')
        data = json.loads(request.public_input)
        source = data['evidence'][0]
        document = dict(schema_version=1,scope=data['scope'],claims=[dict(kind='inference',
            statement='SYNTHETIC grounded response about '+source['content'].split()[1],
            citations=[dict(source_ref=source['source_ref'],start=0,end=len(source['content']))])],unknowns=[],contradictions=[])
        if self.mutate: document = self.mutate(document)
        return StructuredResult(document,request.model_ref,100,50,Decimal('0.001'))


@pytest.fixture
def env(tmp_path, contextual_binaries):
    helper,keygen = contextual_binaries
    store = SQLiteStore(tmp_path/'control.sqlite')
    directory = Directory(store)
    directory.enroll_synthetic(1,owner_ref=OWNER,actor_ref=ACTOR)
    directory.accept_consent(ACTOR,CONSENT_VERSION,NOW,('consent:synthetic',))
    caps = tuple((name,'cap:'+name) for name in ('read','compose','inform','search'))
    for _,cap in caps: store.allow_source(owner_ref=OWNER,source_ref=cap,authorized=True)
    store.set_session(owner_ref=OWNER,session_ref='session:synthetic',version=1)
    clock,policy = Clock(),Policy()
    def authority(owner,actor,now):
        return Authority(owner,actor,caps,NOW+timedelta(days=2),Budget(100,20,Decimal('1.00')),(('session:synthetic',1),))
    private_root = create_private_directory(tmp_path/'private-vault')
    keys = create_private_directory(tmp_path/'synthetic-keys')
    result = subprocess.run([str(keygen),'--synthetic-only',str(keys)],capture_output=True,timeout=10)
    assert result.returncode == 0 and result.stdout == b'' and result.stderr == b''
    config = dict(root=private_root,helper=helper,helper_sha256=sha256(helper.read_bytes()).hexdigest(),
        identity_file=keys/'identity.agekey',recipient=(keys/'recipient.txt').read_text().strip(),owner_ref=OWNER,
        audiences=('worker:task-router','worker:conversations','worker:contextual','worker:sources','worker:research'),
        authorize=lambda **kw: policy.live,store=store)
    backend = PrivateVault(**config)
    tasks = SQLiteTaskStore(store,vault=backend.view('worker:task-router'))
    router = TaskRouter(tasks,validate_document=contracts.validate)
    document = {'schema_version':1,'goal':PRIVATE+' contextual response','privacy_scope':'personal',
        'steps':[{'step_id':'respond','operation':'compose','arguments':{}}],
        'missing_fields':[],'confidence':1,'budget':{'calls':30,'message_limit':1,'max_usd':'0.50'}}
    proposed = router.propose(authority=authority(OWNER,ACTOR,NOW),event_ref='event:task',document=document,now=NOW)
    token = next(b.callback_ref for b in proposed.buttons if b.text=='Confirmar')
    proposal = router.callback(authority=authority(OWNER,ACTOR,NOW),callback_ref=token,event_ref='event:confirm',now=NOW)
    repo = SQLiteConversations(store,vault=backend.view('worker:conversations'),capabilities=Caps(),clock=clock,
        channels=(Channel('synthetic','fixture',True),),synthetic_authorized=True)
    account = Account('account:synthetic','synthetic','fixture','recipient:self','session:synthetic',1,NOW+timedelta(days=2))
    repo.register_account(authority=authority(OWNER,ACTOR,NOW),account=account,now=NOW)
    identity = repo.vault.seal(owner_ref=OWNER,plaintext=json.dumps(dict(account_ref=account.account_ref,
        chat_ref='chat:synthetic',recipient_ref='recipient:synthetic',display=PRIVATE,address='synthetic-address')).encode())
    repo.enable_chat(authority=authority(OWNER,ACTOR,NOW),account_ref=account.account_ref,chat_ref='chat:synthetic',
        recipient_ref='recipient:synthetic',identity_ref=identity,now=NOW)
    def incoming(message='message:first',topic='astronomy'):
        pointer = repo.vault.seal(owner_ref=OWNER,plaintext=json.dumps(dict(chat_ref='chat:synthetic',recipient_ref='recipient:synthetic',
            provider_message_ref=message,text=PRIVATE+' question about '+topic,observed_at=stamp(clock.now()))).encode())
        class Worker:
            fixture_only=True
            def sync(self, **kwargs): return IncomingPage((Incoming('chat:synthetic','recipient:synthetic',message,pointer),))
        repo.sync(authority=authority(OWNER,ACTOR,clock.now()),account_ref=account.account_ref,now=clock.now(),worker=Worker())
        return pointer
    incoming()
    sink = backend.view('worker:sources').source_sink()
    captures = {}
    def capture(topic='astronomy',count=1):
        spec = SourceSpec('source:synthetic','fixture','memory','fixture',(ReadOperation('search','fixture_search'),),min_interval_seconds=0)
        body = ('SYNTHETIC '+topic+' observed information.').encode()
        reader = Reader(registry=Registry((spec,)),capabilities=FixtureCapabilities(clock.now()),access=FixtureAccess(),
            private_sink=sink,transports={'fixture':FixtureTransport({'source:synthetic':(fixture_page((RawItem(body,'https://example.org/synthetic'),)),)})},now=clock.now)
        report = reader.read(ReadRequest(OWNER,ACTOR,'request:synthetic','source:synthetic','search',clock.now()+timedelta(minutes=1)))
        record = report.records[0]
        raw = json.loads(sink.read(owner_ref=OWNER,pointer=record.private_ref))
        content = b64decode(raw['content_b64'],validate=True)
        material = SourceMaterial(OWNER,record.record_ref,'cap:read',clock.now(),content,sha256(content).hexdigest(),
            'fixture',Decimal(0),record.private_ref,evidence_format='content_b64_json')
        captures[record.record_ref]=material
        return record.record_ref
    ref = capture()
    class Resolver:
        def resolve(self, **kw):
            value = captures[kw['source_ref']]
            sink.read(owner_ref=kw['owner_ref'],pointer=value.private_ref,max_bytes=65536)
            return value
    state = SQLiteContextualStore(tasks,authority=authority,access=policy,
        limits=lambda req,now:ReplyBudget(100,1000000,Decimal(1),65536,8192,32),clock=clock)
    context = EnabledConversationContext(repo,authority=authority)
    evidence = CapturedResearch(Resolver(),state)
    reasoner = FixtureReasoner()
    vault = backend.view('worker:contextual')
    composer = ContextualComposer(state=state,access=policy,context=context,evidence=evidence,vault=vault,clock=clock,reasoner=reasoner)
    request = ReplyRequest(OWNER,ACTOR,'run:synthetic',proposal.task_ref,'event:reply',account.account_ref,'chat:synthetic',
        'message:first','purpose:answer',vault.seal(owner_ref=OWNER,plaintext=b'SYNTHETIC answer with evidence'),(ref,),NOW+timedelta(hours=1),model_ref=MODEL)
    handoff = ContextualHandoff(composer,service=Conversations(repo),context=context,vault=vault,authority=authority)
    yield dict(store=store,backend=backend,config=config,tasks=tasks,router=router,proposal=proposal,authority=authority,
        repo=repo,clock=clock,policy=policy,state=state,context=context,evidence=evidence,reasoner=reasoner,vault=vault,
        composer=composer,request=request,handoff=handoff,captures=captures,capture=capture,incoming=incoming)
    store.close()


@pytest.mark.parametrize('topic',['astronomy','garden','literature','music','services'])
def test_general_context_evidence_draft_a10_exact_screen_without_approval(env,topic):
    request=replace(env['request'],evidence_refs=(env['capture'](topic),))
    result=env['composer'].compose(request)
    assert result.state=='prepared'
    body=json.loads(env['vault'].open(owner_ref=OWNER,pointer=result.private_ref))
    assert topic in body['text'] and body['citations'][0]['citations'][0]['evidence_ref']['recipient_scope']=='worker:sources'
    batch=env['handoff'].draft(request,result)
    assert batch.status=='proposed'
    screen=env['handoff'].service.screen(authority=env['authority'](OWNER,ACTOR,NOW),batch_ref=batch.batch_ref,now=NOW)
    assert screen.rows[0]['text']==body['text'] and screen.rows[0]['chat_ref']==request.chat_ref
    assert env['store'].get(owner_ref=OWNER,operation_id=batch.operation_ids[0]).approval is None
    assert env['store'].db.execute('SELECT COUNT(*) FROM provider_proofs').fetchone()[0]==0
    assert env['handoff'].draft(request,result).batch_ref==batch.batch_ref


def test_new_response_in_same_task_opens_research_then_draft(env):
    first=env['composer'].compose(env['request'])
    env['incoming']('message:second','garden')
    query_ref=env['vault'].seal(owner_ref=OWNER,plaintext=b'SYNTHETIC astronomy question')
    env['policy'].query_allowed=True
    calls=[]
    def query_runner(*,request,query,reserve):
        reserve()
        calls.append(query)
        return (env['capture']('garden'),),Decimal('0.002')
    env['evidence'].query_runner=query_runner
    request=replace(env['request'],event_ref='event:reply_second',provider_message_ref='message:second',
        query_ref=query_ref,evidence_refs=(),budget=ReplyBudget(calls=12))
    second=env['composer'].compose(request)
    assert second.state=='prepared' and first.invocation_ref!=second.invocation_ref
    assert calls==['SYNTHETIC astronomy question']
    assert env['tasks']._row(OWNER,request.task_ref).task_ref==env['proposal'].task_ref
    body=json.loads(env['vault'].open(owner_ref=OWNER,pointer=second.private_ref))
    assert 'garden' in body['text'] and body['scope']['provider_message_ref']=='message:second'


def test_literal_reply_has_no_research_or_reasoner_and_reuses_exact_context(env):
    request=replace(env['request'],mode='literal',evidence_refs=())
    env['evidence'].collect=lambda *a,**kw:pytest.fail('literal called research')
    result=env['composer'].compose(request)
    body=json.loads(env['vault'].open(owner_ref=OWNER,pointer=result.private_ref))
    assert body['text']=='SYNTHETIC answer with evidence' and body['mode']=='literal' and body['citations']==[]
    assert env['reasoner'].requests==[]


def test_missing_reasoner_explicitly_blocked_not_extract_synthesis(env):
    env['composer'].reasoner=None
    assert env['composer'].compose(env['request']).reason=='reasoner_not_configured'
    request=replace(env['request'],event_ref='event:extracts',mode='extracts')
    result=env['composer'].compose(request)
    assert result.state=='partial'
    assert 'no síntesis' in json.loads(env['vault'].open(owner_ref=OWNER,pointer=result.private_ref))['text']


@pytest.mark.parametrize('stage',['before_reasoner','after_reasoner','after_private_result','after_contextual_result'])
def test_crash_restart_never_repeats_reasoner_without_durable_result(env,stage):
    def fail(point):
        if point==stage:raise RuntimeError('synthetic crash')
    env['store'].failpoint=fail
    with pytest.raises(RuntimeError):env['composer'].compose(env['request'])
    previous=len(env['reasoner'].requests)
    env['store'].failpoint=lambda point:None
    path=env['store'].path
    env['store'].close()
    second=SQLiteStore(path)
    try:
        restarted=PrivateVault(**{**env['config'],'store':second})
        tasks=SQLiteTaskStore(second,vault=restarted.view('worker:task-router'))
        state=SQLiteContextualStore(tasks,authority=env['authority'],access=env['policy'],limits=env['state'].limits,clock=env['clock'])
        env['composer'].state=state
        result=env['composer'].compose(env['request'])
        assert result.state=='uncertain' and result.reason=='composition_checkpoint_incomplete'
        assert len(env['reasoner'].requests)==previous
    finally:second.close()


def test_lost_response_accounting_unknown_and_no_retry(env):
    env['reasoner'].fail=True
    result=env['composer'].compose(env['request'])
    assert result.state=='uncertain' and env['composer'].compose(env['request']).replayed
    assert len(env['reasoner'].requests)==1
    assert env['store'].db.execute('SELECT actual_cost FROM contextual_invocations').fetchone()==(None,)


def test_concurrent_event_single_reasoner(env):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:env['composer'].compose(env['request']),range(2)))
    assert len(env['reasoner'].requests)==1
    assert len({r.invocation_ref for r in results})==1
    assert env['composer'].compose(env['request']).state=='prepared'


@pytest.mark.parametrize('field',['owner_ref','account_ref','chat_ref','provider_message_ref'])
def test_reasoner_scope_drift_never_creates_a10_draft(env,field):
    env['reasoner'].mutate=lambda doc:{**doc,'scope':{**doc['scope'],field:'other:synthetic'}}
    result=env['composer'].compose(env['request'])
    assert result.state=='blocked' and result.reason=='reply_validation_failed'
    assert env['store'].db.execute('SELECT COUNT(*) FROM conversation_batches').fetchone()[0]==0


@pytest.mark.parametrize('case',['unknown_source','span','claim','send','interlocutor'])
def test_citation_or_injection_invalid_output_blocked(env,case):
    def mutate(doc):
        claim=doc['claims'][0]
        if case=='unknown_source':claim['citations'][0]['source_ref']='source:other'
        elif case=='span':claim['citations'][0]['end']=999999
        elif case=='claim':claim['kind']='extracted'
        elif case=='send':doc['send']=True
        else:claim.update(kind='interlocutor',statement='Fake statement',citations=[])
        return doc
    env['reasoner'].mutate=mutate
    assert env['composer'].compose(env['request']).state=='blocked'


@pytest.mark.parametrize('query',[PRIVATE,'user@example.org','+580001234567','https://example.org/private','123456789'])
def test_pii_query_not_sent_publicly(env,query):
    env['policy'].query_allowed=True
    env['evidence'].query_runner=lambda **kw:pytest.fail('PII query reached source')
    request=replace(env['request'],query_ref=env['vault'].seal(owner_ref=OWNER,plaintext=query.encode()))
    with pytest.raises(ContextualError,match='public_query_not_minimal'):env['composer'].compose(request)
    assert env['reasoner'].requests==[]


def test_owner_budget_before_io_survives_restart_and_no_cost_refund(env):
    env['state'].limits=lambda req,now:ReplyBudget(8,20000,Decimal('0.10'),12000,1024,8)
    request=replace(env['request'],budget=ReplyBudget(calls=8))
    env['composer'].compose(request)
    with pytest.raises(ContextualError,match='contextual_budget_exhausted'):
        env['composer'].compose(replace(request,event_ref='event:new_question'))
    assert len(env['reasoner'].requests)==1


@pytest.mark.parametrize('change',['stop','revoke','consent','chat','session','run','deadline'])
def test_fresh_permission_denies_replay_and_handoff(env,change):
    result=env['composer'].compose(env['request'])
    if change=='stop':env['store'].stop(owner_ref=OWNER)
    elif change=='revoke':env['store'].revoke(owner_ref=OWNER,actor_ref=ACTOR)
    elif change=='consent':env['store'].db.execute('UPDATE directory SET consent_version=NULL')
    elif change=='chat':env['store'].db.execute('UPDATE conversation_chats SET enabled=0')
    elif change=='session':env['store'].set_session(owner_ref=OWNER,session_ref='session:synthetic',version=2)
    elif change=='run':env['policy'].live=False
    else:env['clock'].current=env['request'].deadline
    with pytest.raises((ContextualError,ConditionalConflict,ValueError)):
        env['handoff'].draft(env['request'],result)


def test_stale_or_modified_capture_and_missing_message_fail_closed(env):
    ref=env['request'].evidence_refs[0]
    env['captures'][ref]=replace(env['captures'][ref],observed_at=NOW-timedelta(days=2))
    with pytest.raises(ContextualError,match='evidence_stale_or_modified'):env['composer'].compose(env['request'])
    with pytest.raises(ContextualError,match='context_message_ambiguous_or_missing'):
        env['composer'].compose(replace(env['request'],event_ref='event:other',provider_message_ref='message:unknown'))
    assert env['reasoner'].requests==[]


def test_private_sqlite_wal_ciphertext_and_diagnostics(env,capsys):
    result=env['composer'].compose(env['request'])
    env['handoff'].draft(env['request'],result)
    connection=sqlite3.connect(env['store'].path)
    try:dump='\n'.join(connection.iterdump())
    finally:connection.close()
    for marker in (PRIVATE,'SYNTHETIC answer with evidence','astronomy observed information'):
        assert marker not in dump+repr(result)+str(capsys.readouterr())
        for path in (env['store'].path,Path(str(env['store'].path)+'-wal')):
            if path.exists():assert marker.encode() not in path.read_bytes()
    assert all(row[0].startswith(b'age-encryption.org/v1\n') for row in env['store'].db.execute('SELECT content FROM blobs').fetchall())


class Secrets:
    def get(self,**kwargs):return SecretValue(b'sk-SYNTHETIC-not-real-key')


class Cache:
    def __init__(self):self.items={}
    def get(self,key):return self.items.get(key)
    def put(self,key,value):self.items[key]=value


def fake_http_reasoner(clock, *, document=None, enabled=True, body_mutation=None):
    calls=[]
    def transport(url,data,headers,timeout):
        request=json.loads(data)
        calls.append(request)
        private_input=request['messages'][1]['content']
        start=private_input.index('\n')
        # Parse between B's immutable hash markers; never modify B's client.
        user=json.loads(private_input.split('\n',2)[2].rsplit('\n',1)[0])
        source=user['evidence'][0]
        answer=document or dict(schema_version=1,scope=user['scope'],claims=[dict(kind='inference',statement='SYNTHETIC contextual response',
            citations=[dict(source_ref=source['source_ref'],start=0,end=len(source['content']))])],unknowns=[],contradictions=[])
        reply={'choices':[{'message':{'content':json.dumps(answer)}}],'usage':{'prompt_tokens':100,'completion_tokens':50}}
        if body_mutation:reply=body_mutation(reply)
        return 200,{},json.dumps(reply).encode()
    backend=build_reasoner(secrets=Secrets(),cache=Cache(),clock=clock,
        prices={MODEL:(Decimal('0.02'),Decimal('0.10'))},transport=transport,enabled=enabled,allowed_models=(MODEL,))
    return backend,calls


def test_existing_b_client_candidate_schema_prompt_personal_zdr_and_golden_output(env):
    backend,calls=fake_http_reasoner(env['clock'])
    env['composer'].reasoner=backend
    result=env['composer'].compose(env['request'])
    assert result.state=='prepared' and len(calls)==1
    assert calls[0]['provider']['zdr'] is True and calls[0]['provider']['data_collection']=='deny'
    assert calls[0]['response_format']['json_schema']['strict'] is True and 'tools' not in calls[0]
    body=json.loads(env['vault'].open(owner_ref=OWNER,pointer=result.private_ref))
    assert body['accounting']['model_api_cost']=='0.000007' and body['accounting']['total_execution_cost'] is None
    contracts.validate('llm.contextual_reply.v1',body['reply'])


def test_default_b_prompt_registry_refuses_contextual_request_without_factory():
    backend=OpenRouterLLM(secrets=Secrets(),cache=Cache(),clock=Clock(),prices={MODEL:(Decimal('.02'),Decimal('.10'))},
        enabled=True,transport=lambda *a:pytest.fail('default registry made HTTP'))
    payload='{}'
    request=StructuredRequest(SCHEMA_NAME,1,MODEL,PROMPT_VERSION,sha256(payload.encode()).hexdigest(),'es','personal',payload,100,Decimal('.1'))
    with pytest.raises(InvalidRequest,match='prompt_version'):backend.generate(owner_ref=OWNER,request=request)
    disabled,calls=fake_http_reasoner(Clock(),enabled=False)
    with pytest.raises(LLMDisabled):disabled.generate(owner_ref=OWNER,request=request)
    assert calls==[]


def test_b_rejected_output_preserves_spent_cost_and_never_retries(env):
    backend,calls=fake_http_reasoner(env['clock'],body_mutation=lambda body:{**body,'choices':[{'message':{'content':'{"send":true}'}}]})
    env['composer'].reasoner=backend
    result=env['composer'].compose(env['request'])
    assert result.state=='uncertain'
    assert env['store'].db.execute('SELECT input_tokens,output_tokens,actual_cost FROM contextual_invocations').fetchone()==(100,50,'0.000007')
    env['composer'].compose(env['request'])
    assert len(calls)==1


def test_unknown_research_cost_blocks_next_reasoner_call(env):
    env['policy'].query_allowed=True
    query=env['vault'].seal(owner_ref=OWNER,plaintext=b'SYNTHETIC astronomy question')
    env['evidence'].query_runner=lambda **kw:(env['request'].evidence_refs,None)
    result=env['composer'].compose(replace(env['request'],query_ref=query,budget=ReplyBudget(calls=12)))
    assert result.reason=='research_cost_unknown' and env['reasoner'].requests==[]


def test_handler_request_factory_cannot_replace_owner_or_context_scope(env):
    bad=replace(env['request'],run_ref='run:other')
    handler=compose_handler(env['handoff'],request_factory=lambda **kw:bad)
    with pytest.raises(ContextualError,match='runtime_contextual_binding'):
        handler(runtime=object(),authority=env['authority'](OWNER,ACTOR,NOW),run_ref='run:synthetic',
            task_ref=bad.task_ref,step={},outputs={})
    handler=compose_handler(env['handoff'],request_factory=lambda **kw:env['request'])
    document,state,reason=handler(runtime=object(),authority=env['authority'](OWNER,ACTOR,NOW),run_ref='run:synthetic',
        task_ref=bad.task_ref,step={},outputs={})
    assert state=='awaiting_approval' and reason=='exact_message_approval_required'
    assert 'batch_ref' in document and 'text' not in document


def runtime_contextual(env, *, request=None):
    """Trusted test host binds A16's actual run and acquired lease; no fake permission."""
    PrivateIngress(env['store'],Directory(env['store']),env['backend'].view('worker:task-router'))
    runtime=GeneralRuntime(env['store'],tasks=env['tasks'],router=env['router'],
        vault=env['backend'].view('worker:task-router'),source_sink=env['backend'].view('worker:sources').source_sink(),
        report_vault=env['backend'].view('worker:research'),reader=object(),conversations=env['handoff'].service,
        host_authority=env['authority'],clock=env['clock'],fixture=True)
    run=runtime.start(env['authority'](OWNER,ACTOR,NOW),env['proposal'])
    request=replace(request or env['request'],run_ref=run,event_ref='event:runtime_contextual')
    original=env['policy'].check
    pinned=[]
    def fresh(request,*,stage,now):
        original(request,stage=stage,now=now)
        runtime._live_run(request.owner_ref,request.actor_ref,request.run_ref)
        row=env['store'].db.execute('SELECT doc FROM leases WHERE owner=? AND ref=?',
            (request.owner_ref,'general:'+request.run_ref.split(':',1)[1])).fetchone()
        if row is None:raise ConditionalConflict('run_lease_lost')
        lease=loads(row[0])
        if not pinned:pinned.append(lease)
        if pinned[0]!=lease or not env['store'].is_current(owner_ref=request.owner_ref,lease=lease,now=now):
            raise ConditionalConflict('run_lease_lost')
    env['policy'].check=fresh
    screens,callbacks=[],[]
    def notice(*,runtime,authority,batch,run_ref):
        screen=env['handoff'].service.screen(authority=authority,batch_ref=batch.batch_ref,now=env['clock'].now())
        screens.append(screen)
        callbacks.append(runtime.approval_notice(authority,screen,run_ref=run_ref))
    runtime.handlers['compose']=compose_handler(env['handoff'],request_factory=lambda **kw:request,notice=notice)
    return runtime,run,request,screens,callbacks


def test_runtime_new_message_mid_task_research_exact_scope_pending_screen(env):
    # New provider response arrives after the A6 intention was confirmed.
    env['incoming']('message:mid_task','garden')
    env['policy'].query_allowed=True
    query=env['vault'].seal(owner_ref=OWNER,plaintext=b'SYNTHETIC astronomy question')
    acquired=[]
    def research(*,request,query,reserve):
        reserve()
        acquired.append(query)
        return (env['capture']('garden'),),Decimal('0.002')
    env['evidence'].query_runner=research
    request=replace(env['request'],provider_message_ref='message:mid_task',query_ref=query,evidence_refs=())
    runtime,run,request,screens,callbacks=runtime_contextual(env,request=request)
    assert runtime.pump(OWNER,ACTOR)==1
    assert runtime.status(OWNER,ACTOR)[0][2]=='awaiting_approval'
    output=runtime.outputs(OWNER,run)['respond']
    result=env['composer'].state.store.db.execute('SELECT run,calls,state FROM contextual_invocations').fetchone()
    assert result==(run,12,'prepared') and acquired==['SYNTHETIC astronomy question']
    screen=screens[0]
    assert 'garden' in screen.rows[0]['text'] and screen.rows[0]['chat_ref']==request.chat_ref
    assert screen.rows[0]['account_ref']==request.account_ref and screen.rows[0]['purpose_ref']==request.purpose_ref
    assert output['batch_ref']==screen.batch_ref and len(env['reasoner'].requests)==1
    assert env['store'].db.execute('SELECT state FROM general_notifications').fetchall()==[('pending',),('pending',)]
    with pytest.raises(GeneralError,match='approval_screen_not_delivered'):
        runtime.callback(OWNER,ACTOR,callbacks[0],event_ref='event:unseen_reply')
    assert env['store'].get(owner_ref=OWNER,operation_id=env['handoff'].service.repository._batch(OWNER,screen.batch_ref).operation_ids[0]).approval is None


def test_runtime_cancel_blocks_draft_approval_and_new_contextual_io(env):
    runtime,run,request,screens,callbacks=runtime_contextual(env)
    runtime.pump(OWNER,ACTOR)
    cancel=next(b.callback_ref for b in env['proposal'].buttons if b.text=='Cancelar')
    runtime.callback(OWNER,ACTOR,cancel,event_ref='event:cancel_contextual')
    with pytest.raises(GeneralError,match='run_inactive_or_expired'):
        runtime.callback(OWNER,ACTOR,callbacks[0],event_ref='event:cancelled_reply_approval')
    with pytest.raises(GeneralError,match='run_inactive_or_expired'):
        env['composer'].compose(replace(request,event_ref='event:reply_after_cancel'))
    assert len(env['reasoner'].requests)==1 and env['store'].db.execute('SELECT COUNT(*) FROM provider_proofs').fetchone()==(0,)


def test_runtime_original_plan_budget_blocks_before_context_or_reasoner(env):
    request=replace(env['request'],budget=ReplyBudget(calls=31))
    runtime,run,request,screens,callbacks=runtime_contextual(env,request=request)
    assert runtime.pump(OWNER,ACTOR)==1 and runtime.status(OWNER,ACTOR)[0][2]=='blocked'
    assert runtime.outputs(OWNER,run)=={} and env['reasoner'].requests==[]
    assert env['store'].db.execute('SELECT COUNT(*) FROM contextual_invocations').fetchone()==(0,)
    assert env['store'].db.execute('SELECT reason FROM general_steps').fetchone()==('contextual_budget_exhausted',)


def test_runtime_expired_lease_before_model_never_requests_or_checkpoints(env):
    runtime,run,request,screens,callbacks=runtime_contextual(env)
    def lose_lease(stage):
        if stage=='before_reasoner':env['clock'].current+=timedelta(seconds=61)
    env['store'].failpoint=lose_lease
    runtime.pump(OWNER,ACTOR)
    assert env['reasoner'].requests==[] and screens==[]
    assert env['store'].db.execute('SELECT state FROM contextual_invocations').fetchone()==('claimed',)
    assert env['store'].db.execute('SELECT pointer FROM general_steps').fetchone()==(None,)


def test_runtime_crash_after_draft_keeps_uncertainty_no_duplicate_model_or_batch(env):
    runtime,run,request,screens,callbacks=runtime_contextual(env)
    def crash(stage):
        if stage=='general_step_checkpoint':raise RuntimeError('synthetic checkpoint crash')
    env['store'].failpoint=crash
    with pytest.raises(RuntimeError):runtime.pump(OWNER,ACTOR)
    env['store'].failpoint=lambda stage:None
    assert len(env['reasoner'].requests)==1 and len(screens)==1
    runtime.pump(OWNER,ACTOR)
    assert len(env['reasoner'].requests)==1 and len(screens)==1
    assert runtime.status(OWNER,ACTOR)[0][2]=='blocked'
    assert env['store'].db.execute('SELECT reason FROM general_steps').fetchone()==('step_outcome_uncertain',)
    assert env['store'].db.execute('SELECT COUNT(*) FROM conversation_batches').fetchone()==(1,)


def test_unknowns_and_contradictions_remain_partial_visible_not_zero(env):
    second=env['capture']('garden')
    def limited(document):
        return {**document,'unknowns':['Precio y moneda desconocidos.'],
            'contradictions':[{'description':'Fuentes observadas discrepan; no resuelto.',
                'source_refs':[env['request'].evidence_refs[0],second]}]}
    env['reasoner'].mutate=limited
    result=env['composer'].compose(replace(env['request'],evidence_refs=(*env['request'].evidence_refs,second)))
    assert result.state=='partial'
    body=json.loads(env['vault'].open(owner_ref=OWNER,pointer=result.private_ref))
    assert 'moneda desconocidos' in body['text'] and 'Contradicciones declaradas' in body['text']
    assert body['accounting']['total_execution_cost'] is None


@pytest.mark.parametrize('known',[True,False])
def test_source_denial_is_explicit_but_unexpected_crash_is_not_swallowed(env,known):
    def failed(**kw):
        if known:raise SourceUnavailable('needs_reauth')
        raise RuntimeError('synthetic source crash')
    env['evidence'].resolver.resolve=failed
    if known:
        assert env['composer'].compose(env['request']).reason=='evidence_missing'
    else:
        with pytest.raises(RuntimeError):env['composer'].compose(env['request'])
        assert env['composer'].compose(env['request']).state=='uncertain'
    assert env['reasoner'].requests==[]


def test_runtime_lost_reasoner_response_and_explicit_replay_never_succeed(env):
    runtime,run,request,screens,callbacks=runtime_contextual(env)
    env['reasoner'].fail=True
    original=runtime.handlers['compose']
    replay=[]
    def twice(**kwargs):
        first=original(**kwargs)
        second=original(**kwargs)
        replay.append((first,second))
        return second
    runtime.handlers['compose']=twice
    assert runtime.pump(OWNER,ACTOR)==1
    assert runtime.status(OWNER,ACTOR)[0][2]=='blocked'
    pointer,reason=env['store'].db.execute('SELECT pointer,reason FROM general_steps').fetchone()
    assert reason=='contextual_outcome_uncertain'
    private=runtime.open(OWNER,pointer)
    assert private['state']=='uncertain' and private['reason']=='reasoner_response_unavailable'
    assert runtime.pump(OWNER,ACTOR)==0 and len(env['reasoner'].requests)==1
    assert replay[0][0]==replay[0][1] and replay[0][1][1]=='blocked' and screens==[]


def expanded_intent(env, *, calls=30):
    document={**env['proposal'].document['request'],'steps':[
        {'step_id':'before','operation':'read','arguments':{'source_refs':['source:synthetic']}},
        {'step_id':'respond','operation':'compose','depends_on':['before'],'arguments':{}},
        {'step_id':'after','operation':'read','depends_on':['before'],'arguments':{'source_refs':['source:synthetic']}}],
        'budget':{'calls':calls,'message_limit':1,'max_usd':'0.50'}}
    proposal=env['router'].propose(authority=env['authority'](OWNER,ACTOR,NOW),event_ref='event:extended',document=document,now=NOW)
    callback=next(b.callback_ref for b in proposal.buttons if b.text=='Confirmar')
    confirmed=env['router'].callback(authority=env['authority'](OWNER,ACTOR,NOW),callback_ref=callback,event_ref='event:extended_confirm',now=NOW)
    env['proposal']=confirmed
    env['request']=replace(env['request'],task_ref=confirmed.task_ref)


def test_runtime_budget_prior_read_query_compose_after_step_sum_and_known_usage(env):
    expanded_intent(env,calls=16)
    env['policy'].query_allowed=True
    query=env['vault'].seal(owner_ref=OWNER,plaintext=b'SYNTHETIC astronomy question')
    request=replace(env['request'],query_ref=query,evidence_refs=())
    def query_runner(*,request,query,reserve):
        reserve()
        return (env['capture']('astronomy'),),Decimal('0.002')
    env['evidence'].query_runner=query_runner
    runtime,run,request,screens,callbacks=runtime_contextual(env,request=request)
    reads=[]
    def read(**kw):
        reads.append(env['capture']('literature'))
        return {'source_ref':reads[-1]},'succeeded',None
    runtime.handlers['read']=read
    assert runtime.pump(OWNER,ACTOR)==3
    assert runtime.status(OWNER,ACTOR)[0][3:5]==(15,16)  # 3 attempts + 12 reserved subcalls.
    assert env['store'].db.execute('SELECT calls,tokens,cost FROM contextual_limits').fetchone()==(12,20000,'0.10')
    assert env['store'].db.execute('SELECT calls,input_tokens,output_tokens,actual_cost FROM contextual_invocations').fetchone()==(12,100,50,'0.001')
    private=runtime.outputs(OWNER,run)['respond']
    from radar.ports.types import BlobPointer
    body=json.loads(env['vault'].open(owner_ref=OWNER,pointer=BlobPointer(**private['private_ref'])))
    assert body['accounting']['new_research_api_cost']=='0.002' and body['accounting']['model_api_cost']=='0.001'
    assert body['accounting']['total_execution_cost'] is None and len(reads)==2


def test_runtime_concurrent_compositions_cannot_spend_other_step_budget(env):
    expanded_intent(env,calls=25)
    runtime,run,request,screens,callbacks=runtime_contextual(env)
    outcomes=[]
    def race(**kwargs):
        requests=(request,replace(request,event_ref='event:competing'))
        def one(req):
            try:return req,env['composer'].compose(req)
            except ContextualError as error:
                assert error.args==('contextual_budget_exhausted',)
                return req,None
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes.extend(pool.map(one,requests))
        winner,result=next((req,result) for req,result in outcomes if result is not None)
        batch=env['handoff'].draft(winner,result)
        return {'batch_ref':batch.batch_ref},'awaiting_approval','exact_message_approval_required'
    runtime.handlers['compose']=race
    runtime.handlers['read']=lambda **kw:({'source_ref':env['capture']('music')},'succeeded',None)
    assert runtime.pump(OWNER,ACTOR)==3
    assert sum(result is not None for _,result in outcomes)==1 and len(env['reasoner'].requests)==1
    assert runtime.status(OWNER,ACTOR)[0][3:5]==(15,25)
    assert env['store'].db.execute('SELECT calls FROM contextual_limits').fetchone()==(12,)
    assert env['store'].db.execute('SELECT COUNT(*) FROM contextual_invocations').fetchone()==(1,)


def test_two_literal_replies_same_task_reserve_no_model_tokens_or_api_dollars(env):
    request=replace(env['request'],mode='literal',evidence_refs=())
    runtime,run,request,screens,callbacks=runtime_contextual(env,request=request)
    original=runtime.handlers['compose']
    second=[]
    def two(**kwargs):
        first=original(**kwargs)
        second.append(env['composer'].compose(replace(request,event_ref='event:literal_second')))
        return first
    runtime.handlers['compose']=two
    assert runtime.pump(OWNER,ACTOR)==1 and second[0].state=='prepared'
    assert env['reasoner'].requests==[] and runtime.status(OWNER,ACTOR)[0][3:5]==(25,30)
    assert env['store'].db.execute('SELECT calls,tokens,cost FROM contextual_limits').fetchone()==(24,0,'0')
    assert env['store'].db.execute('SELECT reserved_tokens,reserved_cost,actual_cost FROM contextual_invocations').fetchall()==[(0,'0',None),(0,'0',None)]


@pytest.mark.parametrize('committed',[False,True])
def test_shared_runtime_and_contextual_reservation_atomic_on_crash(env,committed):
    runtime,run,request,screens,callbacks=runtime_contextual(env)
    def crash(stage):
        if stage==('after_contextual_reservation_commit' if committed else 'after_contextual_reservation'):
            raise RuntimeError('synthetic reservation crash')
    env['store'].failpoint=crash
    with pytest.raises(RuntimeError):runtime.pump(OWNER,ACTOR)
    assert runtime.status(OWNER,ACTOR)[0][3]==(13 if committed else 1)
    assert env['store'].db.execute('SELECT COUNT(*) FROM contextual_invocations').fetchone()==(1 if committed else 0,)
    assert env['store'].db.execute('SELECT COUNT(*) FROM contextual_limits').fetchone()==(1 if committed else 0,)
    assert env['reasoner'].requests==[]
    env['store'].failpoint=lambda stage:None
    runtime.pump(OWNER,ACTOR)
    assert runtime.status(OWNER,ACTOR)[0][2]=='blocked' and env['reasoner'].requests==[]
