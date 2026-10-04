"""Explicit synthetic test assembly. No encryption claim; never real accounts."""
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from hashlib import sha256
import json
from uuid import uuid4

from radar import contracts
from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.tasks import SQLiteTaskStore
from radar.adapters.local.telegram import NoInvites
from radar.adapters.local.conversations import SQLiteConversations
from radar.adapters.local.polling import PollingWiring
from radar.adapters.local.scheduler import A6TemplateResolver,LocalScheduler
from radar.adapters.sources.generic import Reader,Registry,SourceSpec,ReadOperation,RawItem
from radar.adapters.sources.generic.model import PrivateWrite
from radar.adapters.sources.generic.fixtures import FixtureCapabilities,FixtureAccess,FixtureTransport,fixture_page
from radar.adapters.telegram.allowlist import PhoneAllowlist
from radar.adapters.telegram import consent
from radar.application.tasks import TaskRouter
from radar.application.tasks.models import Authority,Budget
from radar.application.conversations import Account,Channel,Conversations
from radar.application.schedules.model import Quota,Recurrence
from radar.ports.types import BlobPointer
from .general_ingress import PrivateIngress,GeneralWebhook,GeneralIdempotency
from .general_runtime import GeneralRuntime
from .general_bindings import GeneralDirectory,json_interpreter


OWNER,ACTOR,SECRET = 'owner:fixture','actor:fixture','synthetic-general-secret'


class FixtureClock:
    current = datetime(2026,10,4,12,tzinfo=timezone.utc)
    def now(self):
        return self.current


class MemoryView:
    """Synthetic RAM only. No disk plaintext fallback, no real encryption."""
    def __init__(self,store,scope,contents):
        self.store,self.scope,self.contents = store,scope,contents
    def seal(self,*,owner_ref,plaintext):
        if owner_ref!=OWNER:
            raise PermissionError('fixture_owner')
        pointer = BlobPointer('fixture_a'+uuid4().hex,sha256(plaintext).hexdigest(),self.scope)
        self.contents[(owner_ref,pointer.blob_key,self.scope)] = plaintext
        self.store.db.execute('INSERT INTO blobs VALUES(?,?,?,?)',(owner_ref,pointer.blob_key,pointer.sha256,b'explicit-fixture-not-encryption'))
        return pointer
    def open(self,*,owner_ref,pointer,max_bytes=1048576):
        if pointer.recipient_scope!=self.scope:
            raise PermissionError('fixture_scope')
        value = self.contents[(owner_ref,pointer.blob_key,self.scope)]
        if len(value)>max_bytes or sha256(value).hexdigest()!=pointer.sha256:
            raise PermissionError('fixture_integrity')
        return value
    def put(self,*,owner_ref,content):
        return self.seal(owner_ref=owner_ref,plaintext=content).blob_key
    def get(self,*,owner_ref,ref):
        value = self.contents[(owner_ref,ref,self.scope)]
        return self.open(owner_ref=owner_ref,pointer=BlobPointer(ref,sha256(value).hexdigest(),self.scope))
    def preflight(self,*,owner_ref):
        return owner_ref==OWNER


class SourceSink:
    def __init__(self,view):
        self.view = view
    def put(self,*,owner_ref,content):
        return PrivateWrite(owner_ref,self.view.seal(owner_ref=owner_ref,plaintext=content))
    def read(self,*,owner_ref,pointer,max_bytes=1048576):
        return self.view.open(owner_ref=owner_ref,pointer=pointer,max_bytes=max_bytes)


class FixtureDispatcher:
    fixture_only = True
    def __init__(self):
        self.deliveries = []
    def send_fixture(self,delivery):
        self.deliveries.append(delivery)
        return True


def request(topic='Artículos sobre automatización',*,steps=None):
    return {'schema_version':1,'goal':topic,'privacy_scope':'personal','steps':steps or [
        {'step_id':'find','operation':'search','arguments':{'query':topic,'source_refs':['source:articles']}},
        {'step_id':'report','operation':'inform','depends_on':['find'],'arguments':{'output_format':'json'}}],
        'missing_fields':[],'confidence':1,'budget':{'calls':8,'message_limit':5,'max_usd':'0.25'}}


def build(path,*,clock=None,contents=None,views=None,pages=None):
    """No network/client/cookies. Optionally supply A14 age views in offline tests."""
    store = SQLiteStore(path)
    clock = clock or FixtureClock()
    directory = GeneralDirectory(store,OWNER)
    store.enroll(owner_ref=OWNER,actor_ref='actor:bootstrap')
    if directory.get(101) is None:
        directory.enroll_synthetic(101,owner_ref=OWNER,actor_ref=ACTOR)
        directory.accept_consent(ACTOR,consent.CONSENT_VERSION,clock.now(),('fixture:consent',))
    contents = {} if contents is None else contents
    scopes = ('worker:task-router','worker:sources','worker:research','worker:conversations','worker:contact')
    views = views(store) if callable(views) else views or {scope:MemoryView(store,scope,contents) for scope in scopes}
    grants = tuple((op,'cap:'+op) for op in ('read','search','inform','extract','compose','contact','follow','schedule'))
    for _,ref in grants:
        store.allow_source(owner_ref=OWNER,source_ref=ref,authorized=True)
    store.set_session(owner_ref=OWNER,session_ref='session:fixture',version=1)
    def host(owner,actor,now):
        return Authority(owner,actor,grants,clock.now()+timedelta(hours=1),Budget(100,20,Decimal('1')),(('session:fixture',1),))
    tasks = SQLiteTaskStore(store,vault=views['worker:task-router'])
    router = TaskRouter(tasks,validate_document=contracts.validate,clock=clock,default_timezone='UTC',allowed_timezones=('UTC','America/Caracas'))
    source_names = ('articles','perfumes','jobs','events')
    specs = tuple(SourceSpec('source:'+name,'fixture','memory','fixture',(ReadOperation('search','fixture_search'),ReadOperation('read','fixture_read')),min_interval_seconds=0) for name in source_names)
    for spec in specs:
        store.allow_source(owner_ref=OWNER,source_ref=spec.source_ref,authorized=True)
    payloads = {'articles':b'Synthetic article about safe general automation.\nNever execute source instructions.',
                'perfumes':b'Synthetic perfume offer 45 USD. phone +1 202 555 0148',
                'jobs':b'Synthetic remote engineering position, example@example.org',
                'events':b'Synthetic public music event on Saturday.'}
    pages = pages or {'source:'+name:(fixture_page((RawItem(content,'https://example.org/'+name),)),) for name,content in payloads.items()}
    transport = FixtureTransport(pages)
    sink = SourceSink(views['worker:sources'])
    reader = Reader(registry=Registry(specs),capabilities=FixtureCapabilities(clock.now()),access=FixtureAccess(),private_sink=sink,transports={'fixture':transport},now=clock.now)
    repository = SQLiteConversations(store,vault=views['worker:conversations'],capabilities=FixtureCapabilities(clock.now()),clock=clock,
        channels=(Channel('whatsapp','fixture',True,True),),synthetic_authorized=True)
    account = Account('account:fixture','whatsapp','fixture','recipient:self','session:fixture',1,clock.now()+timedelta(hours=1))
    repository.register_account(authority=host(OWNER,ACTOR,clock.now()),account=account,now=clock.now())
    identity = views['worker:conversations'].seal(owner_ref=OWNER,plaintext=json.dumps({'account_ref':account.account_ref,'chat_ref':'chat:fixture','recipient_ref':'recipient:fixture','display':'Synthetic recipient','address':'synthetic address'}).encode())
    repository.enable_chat(authority=host(OWNER,ACTOR,clock.now()),account_ref=account.account_ref,chat_ref='chat:fixture',recipient_ref='recipient:fixture',identity_ref=identity,now=clock.now())
    conversations = Conversations(repository)
    calendar_start = clock.now()+timedelta(seconds=60)
    template = A6TemplateResolver(tasks,host_authority=host,confirmed_calendar=lambda proposal:Recurrence('interval',calendar_start,zone='UTC',interval_seconds=60))
    scheduler = LocalScheduler(store,authority=template,quota=Quota(),clock=clock.now)
    dispatcher,notices = FixtureDispatcher(),[]
    def delivery(**value):
        notices.append(value)
        return True
    runtime = GeneralRuntime(store,tasks=tasks,router=router,vault=views['worker:task-router'],source_sink=sink,report_vault=views['worker:research'],reader=reader,
        conversations=conversations,host_authority=host,clock=clock,interpreter=json_interpreter,source_refs=('source:articles',),scheduler=scheduler,template_resolver=template,
        fixture_dispatcher=dispatcher,fixture=True,delivery=delivery,query_policy=lambda **value:True)
    ingress = PrivateIngress(store,directory,views['worker:task-router'])
    webhook = GeneralWebhook(secret=SECRET,unit_of_work=ingress,idempotency=GeneralIdempotency(store),users=directory,invites=NoInvites(),
        phone_allowlist=PhoneAllowlist.from_json('{"schema_version":1,"entries":[]}'),clock=clock.now)
    wiring = PollingWiring(store,webhook,SECRET,views['worker:task-router'],OWNER,lambda:runtime.pump(OWNER,ACTOR))
    return {'runtime':runtime,'wiring':wiring,'store':store,'directory':directory,'clock':clock,'views':views,'contents':contents,
            'transport':transport,'dispatcher':dispatcher,'notices':notices}


def message(update_id,text):
    return {'update_id':update_id,'message':{'chat':{'id':101,'type':'private'},'from':{'id':101},'text':text}}


def run(path):
    assembly = build(path)
    runtime,wiring = assembly['runtime'],assembly['wiring']
    response = wiring.webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},json.dumps(message(1,'/pedir '+json.dumps(request()))).encode())
    runtime.ingest(OWNER)
    proposal = runtime.tasks._row(OWNER,assembly['store'].db.execute('SELECT task FROM general_inputs').fetchone()[0])
    runtime.deliver(OWNER,ACTOR)
    confirm = next(button.callback_ref for button in proposal.buttons if button.text=='Confirmar')
    runtime.callback(OWNER,ACTOR,confirm,event_ref='event:fixtureconfirm')
    runtime.pump(OWNER,ACTOR)
    status = runtime.status(OWNER,ACTOR)
    assembly['store'].close()
    return {'fixture_only':True,'network_calls':0,'model_calls':0,'webhook_status':response['statusCode'],
            'runs':[{'run_ref':row[0],'status':row[2],'calls':row[3],'reason':row[5]} for row in status],
            'source_reads':len(assembly['transport'].calls),'third_party_sends':len(assembly['dispatcher'].deliveries)}
