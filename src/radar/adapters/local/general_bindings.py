"""Explicit host composition; real vault required, no default fake providers."""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib
import json
import os
from pathlib import Path
from uuid import uuid4

from radar import contracts
from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.tasks import SQLiteTaskStore
from radar.adapters.local.telegram import Directory, NoInvites
from radar.adapters.local.polling import PollingWiring
from radar.adapters.local.conversations import SQLiteConversations
from radar.adapters.local.private_vault import PrivateVault, _permissions
from radar.adapters.local.scheduler import A6TemplateResolver, LocalScheduler
from radar.adapters.sources.generic import Reader, Registry, ReadOperation, SourceSpec
from radar.adapters.sources.generic.model import Grant, reference
from radar.adapters.telegram import consent
from radar.adapters.telegram.bot_api import escape
from radar.application.tasks import TaskRouter
from radar.application.tasks.models import Authority, Budget, TaskError
from radar.application.conversations import Channel, Conversations, DraftInput
from radar.application.schedules.model import Quota, Recurrence
from radar.ports.types import Capability, ConditionalConflict
from .general_ingress import GeneralWebhook, GeneralIdempotency, PrivateIngress
from .general_runtime import GeneralRuntime, GeneralError, source_report


class Clock:
    def now(self):
        return datetime.now(timezone.utc)


class GeneralDirectory(Directory):
    """Durable owner binding selected by operator, not a synthetic enrollment."""
    def __init__(self,store,owner_ref):
        super().__init__(store)
        if not reference(owner_ref):
            raise GeneralError('owner_configuration')
        self.owner_ref = owner_ref
    def enroll(self,telegram_user_id,role,enrolled_at,idempotency_keys):
        if type(telegram_user_id) is not int or role not in ('owner','collaborator'):
            raise GeneralError('enrollment_configuration')
        with self.store.transaction():
            if self.get(telegram_user_id) is not None or any(GeneralIdempotency(self.store).seen(k) for k in idempotency_keys):
                return False
            for key in idempotency_keys:
                self.store.db.execute('INSERT INTO ignored VALUES(?)',(key,))
            actor = 'actor:a'+uuid4().hex
            self.store.db.execute('INSERT OR IGNORE INTO owners(owner) VALUES(?)',(self.owner_ref,))
            self.store.db.execute('INSERT INTO actors VALUES(?,?,1)',(self.owner_ref,actor))
            self.store.db.execute('INSERT INTO directory(id,owner,actor,role) VALUES(?,?,?,?)',(telegram_user_id,self.owner_ref,actor,role))
        return True


class SourceAccess:
    def __init__(self,store,host_authority,session_authorizer=None,clock=lambda:datetime.now(timezone.utc)):
        self.store,self.host,self.session_authorizer,self.clock = store,host_authority,session_authorizer,clock
    def authorize(self,request):
        now=self.clock()
        authority = self.host(request.owner_ref,request.actor_ref,now)
        if not self.store.owner_actor(request.owner_ref,request.actor_ref) or not self.store.current_consent(request.owner_ref,request.actor_ref):
            return None
        capability = dict(authority.capabilities).get(request.operation)
        if not capability or not self.store.source_allowed(owner_ref=request.owner_ref,source_ref=capability) or not self.store.source_allowed(owner_ref=request.owner_ref,source_ref=request.source_ref):
            return None
        if request.account_ref is not None or request.session_ref is not None:
            # An SQLite version is not proof of a live authenticated account.
            # Host proof checks exact owner/account/session/expiry for this read.
            if not callable(self.session_authorizer):
                return None
            grant=self.session_authorizer(request=request,authority=authority,now=now)
            return grant if isinstance(grant,Grant) else None
        return Grant(request.owner_ref,request.actor_ref,request.source_ref,request.operation,min(authority.expires_at,request.deadline),True,True,
                     None,None,None,True)


class ConfigCapabilities:
    """Dated operator observations; configuration is not fresh real verification."""
    def __init__(self,rows):
        self.rows = tuple(rows)
    def get(self,*,owner_ref,platform,backend,operation,session_ref=None):
        matches = [row for row in self.rows if (row['owner_ref'],row['platform'],row['backend'],row['operation'],row.get('session_ref')) == (owner_ref,platform,backend,operation,session_ref)]
        if len(matches)!=1:
            return None
        row = matches[0]
        status='documentado' if row['status']=='probado_real' else row['status']
        return Capability(platform,backend,operation,status,row.get('proof_ref','configuration_unverified'),datetime.fromisoformat(row['checked_on']).date(),row['authorized'] is True)


class TelegramDelivery:
    """Host UI only, opaque actor resolved by current directory; no third parties."""
    def __init__(self,api,directory,report_vault):
        self.api,self.directory,self.report_vault = api,directory,report_vault
    def __call__(self,*,owner_ref,actor_ref,notice_ref,body):
        user = self.directory.by_actor(actor_ref)
        if not user or user['owner_ref']!=owner_ref or not consent.has_consent(user):
            raise GeneralError('ui_authority')
        ids = self.directory.store.db.execute('SELECT id FROM directory WHERE owner=? AND actor=?',(owner_ref,actor_ref)).fetchall()
        if len(ids)!=1:
            raise GeneralError('ui_actor_binding')
        chat = ids[0][0]
        if 'document_ref' in body:
            from radar.ports.types import BlobPointer
            content = self.report_vault.open(owner_ref=owner_ref,pointer=BlobPointer(**body['document_ref']))
            result = self.api.send_document(chat,'research.json',content,protect_content=True)
        else:
            text = body.get('text') or json.dumps(body.get('exact_row',body),ensure_ascii=False,sort_keys=True)
            escaped = escape(text)
            buttons = body.get('buttons',())
            markup = {'inline_keyboard':[[{'text':button['text'],'callback_data':button['callback_ref']}] for button in buttons]} if buttons else None
            if len(escaped)>4096:
                # Exact long private screen, not a silently truncated approval.
                if buttons:
                    raise GeneralError('ui_screen_budget')
                result = self.api.send_document(chat,'private-screen.json',text.encode(),protect_content=True)
            else:
                result = self.api.send_message(chat,escaped,reply_markup=markup,protect_content=True)
        return isinstance(result,dict) and type(result.get('message_id')) is int


class ContactBridge:
    """A7 preparation/resolution → A10 exact draft. No resolver/send default."""
    def __init__(self,contacts,conversations):
        self.contacts,self.conversations = contacts,conversations
    def prepare_step(self,*,runtime,authority,run_ref,task_ref,step,outputs):
        args = step['arguments']
        if 'private_ref' not in args:
            return {},'blocked','private_contact_configuration_required'
        packet = runtime.open(authority.owner_ref,json.dumps(args['private_ref']))
        reports = tuple(source_report(report) for output in outputs.values() for report in output.get('reports',()))
        campaign = self.contacts.start(owner_ref=authority.owner_ref,actor_ref=authority.actor_ref,event_ref='event:a'+run_ref.split(':')[1]+step['step_id'],
            task_ref=task_ref,reports=reports,purpose_ref=packet['purpose_ref'],template=packet['template'],request_facts=packet.get('facts',{}),maximum=packet.get('maximum',10))
        prepared = tuple(self.contacts.prepare(owner_ref=authority.owner_ref,actor_ref=authority.actor_ref,campaign_ref=campaign.campaign_ref,
            candidate_ref=candidate,event_ref='event:a'+candidate.split(':')[1]) for candidate in campaign.candidate_refs)
        runtime.store.db.execute('INSERT OR IGNORE INTO general_contact_runs VALUES(?,?,?)',(authority.owner_ref,campaign.campaign_ref,run_ref))
        runtime.notify(authority.owner_ref,authority.actor_ref,{'text':'Destinatarios publicados NO verificados; requieren resolver independiente aprobado.',
            'campaign_ref':campaign.campaign_ref,'private_ref':asdict(campaign.private_ref),'operations':[asdict(row) for row in prepared]},key=run_ref+step['step_id'])
        return {'campaign_ref':campaign.campaign_ref,'operations':[row.operation_ref for row in prepared]},'awaiting_approval','independent_resolution_required'
    def resolve(self,*,runtime,owner_ref,actor_ref,operation_ref,proof_ref):
        authority = runtime.authority(owner_ref,actor_ref)
        self.contacts.resolve(owner_ref=owner_ref,actor_ref=actor_ref,operation_ref=operation_ref,proof_ref=proof_ref)
        prepared = self.contacts.export(owner_ref=owner_ref,actor_ref=actor_ref,operation_ref=operation_ref)
        target = prepared.resolved
        account = self.conversations.repository._check(authority,target.account_ref,'read',runtime.clock.now())
        if (account.channel,account.session_ref,account.session_version)!=(target.channel,target.session_ref,target.session_version):
            raise GeneralError('contact_account_binding')
        # Resolver proof is independent. This administrative enablement is NOT proof.
        self.conversations.repository.enable_chat(authority=authority,account_ref=target.account_ref,chat_ref=target.chat_ref,
            recipient_ref=target.recipient_ref,identity_ref=target.identity_ref,now=runtime.clock.now())
        batch = self.conversations.compose(authority=authority,event_ref='event:a'+operation_ref.split(':')[1],account_ref=target.account_ref,
            drafts=(DraftInput((target.chat_ref,),prepared.text,prepared.purpose_ref),),now=runtime.clock.now())
        runtime.store.db.execute('INSERT OR IGNORE INTO general_contact_links VALUES(?,?,?,?)',(owner_ref,batch.batch_ref,operation_ref,batch.operation_ids[0]))
        screen = self.conversations.screen(authority=authority,batch_ref=batch.batch_ref,now=runtime.clock.now())
        campaign=self.contacts.store.db.execute('SELECT campaign FROM contact_operations WHERE owner=? AND ref=?',(owner_ref,operation_ref)).fetchone()
        binding=runtime.store.db.execute('SELECT run FROM general_contact_runs WHERE owner=? AND campaign=?',(owner_ref,campaign[0])).fetchone() if campaign else None
        if binding is None:
            raise GeneralError('contact_run_binding')
        callback = runtime.approval_notice(authority,screen,run_ref=binding[0])
        return batch,callback


def json_interpreter(*,authority,input_ref,text,now):
    """Exact structured /pedir JSON, zero model calls; ordinary text is not guessed."""
    try:
        document = json.loads(text)
        contracts.validate('llm.task_request.v1',document)
        return document
    except Exception:
        raise GeneralError('structured_request_or_interpreter_required') from None


json_interpreter.deterministic = True


def private_operator_path(value, *, new_file=False):
    """A14 actual owner/ACL/reparse checks, before reads or SQLite creation.

    The .local convention is organizational, not a security boundary. All
    operator files and their immediate directory must be private. SQLite may
    create a new file only under an already validated private directory.
    """
    try:
        path = Path(value).absolute()
        _permissions(path.parent, directory=True)
        if not new_file or path.exists() or path.is_symlink():
            _permissions(path)
        # Validate existing SQLite sidecars too; never follow a hostile link.
        if new_file:
            for suffix in ('-wal', '-shm', '-journal'):
                sidecar = path.with_name(path.name + suffix)
                if sidecar.exists() or sidecar.is_symlink():
                    _permissions(sidecar)
        return path
    except Exception:
        raise GeneralError('private_operator_path_required') from None


def from_config(*,state_db,phone_allowlist,secret,config_path,api=None):
    """Working local assembly with real age, authenticated B webhook and A12.

    Missing real transports produce blocked tasks, never fixture substitution.
    config_path is trusted operator data, not a Telegram/task-supplied filename.
    """
    path = private_operator_path(config_path)
    state_db = private_operator_path(state_db, new_file=True)
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
        owner = config['owner_ref']
        if not state_db.exists():
            # Exclusive creation under the checked private parent. SQLite then
            # opens the owner-only file; do not loosen existing file ACLs.
            descriptor=os.open(state_db,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            os.close(descriptor)
        store = SQLiteStore(state_db)
        if os.name!='nt':
            for suffix in ('','-wal','-shm'):
                target=state_db.with_name(state_db.name+suffix)
                if target.exists():
                    os.chmod(target,0o600)
        private_operator_path(state_db,new_file=True)
        store.enroll(owner_ref=owner,actor_ref='actor:bootstrap')
        directory = GeneralDirectory(store,owner)
        clock = Clock()
        capabilities = ConfigCapabilities(config.get('capability_observations',()))
        grants = tuple((op,value) for op,value in config['grants'].items())
        def host(owner_ref,actor_ref,now):
            if owner_ref!=owner or not store.owner_actor(owner_ref,actor_ref) or not store.current_consent(owner_ref,actor_ref):
                raise GeneralError('general_authority')
            return Authority(owner_ref,actor_ref,grants,now+timedelta(minutes=60),Budget(config.get('max_calls',100),config.get('max_messages',20),Decimal(config.get('max_usd','1'))),
                tuple((r[0],r[1]) for r in store.db.execute('SELECT ref,version FROM sessions WHERE owner=?',(owner,)).fetchall()))
        for _,value in grants:
            store.allow_source(owner_ref=owner,source_ref=value,authorized=True)
        def authorize(owner_ref,audience):
            return owner_ref==owner and not store.db.execute('SELECT stopped FROM owners WHERE owner=?',(owner,)).fetchone()[0]
        vault = PrivateVault(store=store,owner_ref=owner,authorize=authorize,audiences=('worker:task-router','worker:sources','worker:research','worker:conversations','worker:whatsapp','worker:contact'),**config['vault'])
        task_view, source_view, report_view, conversation_view = (vault.view(scope) for scope in ('worker:task-router','worker:sources','worker:research','worker:conversations'))
        tasks = SQLiteTaskStore(store,vault=task_view)
        router = TaskRouter(tasks,validate_document=contracts.validate,clock=clock,
            default_timezone=config.get('timezone','America/Caracas'),allowed_timezones=tuple(config.get('allowed_timezones',('America/Caracas',))))
        specs = []
        for row in config.get('sources',()):
            row = dict(row)
            if row.get('route') == 'fixture':
                raise GeneralError('fixture_source_requires_fixture_mode')
            row['operations'] = tuple(ReadOperation(**operation) for operation in row['operations'])
            row['allowed_hosts'] = tuple(row.get('allowed_hosts',()))
            specs.append(SourceSpec(**row))
            store.allow_source(owner_ref=owner,source_ref=row['source_ref'],authorized=row['source_ref'] in config.get('authorized_sources',()))
        # An explicit operator module supplies verified guards/backends if present.
        extensions = {}
        if config.get('backend_factory'):
            module,name = config['backend_factory'].split(':',1)
            extensions = getattr(importlib.import_module(module),name)(store=store,vault=vault,host_authority=host,clock=clock)
        if (not isinstance(extensions,dict) or any(getattr(transport,'fixture_only',False)
                for transport in extensions.get('transports',{}).values())):
            raise GeneralError('fixture_backend_requires_fixture_mode')
        if config.get('parser_enabled') is True:
            if extensions.get('parser') is None or not extensions.get('model_ref'):
                raise GeneralError('parser_backend_required')
            router=TaskRouter(tasks,validate_document=contracts.validate,clock=clock,
                parser=extensions['parser'],parser_enabled=True,model_ref=extensions['model_ref'],
                default_timezone=config.get('timezone','America/Caracas'),
                allowed_timezones=tuple(config.get('allowed_timezones',('America/Caracas',))))
        sink = source_view.source_sink()
        capabilities=extensions.get('capabilities',capabilities)
        reader = Reader(registry=Registry(tuple(specs)),capabilities=capabilities,access=SourceAccess(store,host,extensions.get('session_authorizer'),clock.now),private_sink=sink,
            transports=extensions.get('transports',{}),now=clock.now)
        channels = tuple(Channel(**row) for row in config.get('channels',()))
        conversations = Conversations(SQLiteConversations(store,vault=conversation_view,capabilities=capabilities,clock=clock,channels=channels))
        template = extensions.get('template_resolver')
        scheduler = extensions.get('scheduler')
        runtime = GeneralRuntime(store,tasks=tasks,router=router,vault=task_view,source_sink=sink,report_vault=report_view,reader=reader,
            conversations=conversations,host_authority=host,clock=clock,interpreter=None if router.parser_enabled else extensions.get('interpreter',json_interpreter),
            source_refs=tuple(row.source_ref for row in specs),scheduler=scheduler,template_resolver=template,contacts=extensions.get('contacts'),
            query_policy=extensions.get('query_policy'),inbox_sources=extensions.get('inbox_sources'),handlers=extensions.get('handlers'))
        runtime.input_handlers = dict(extensions.get('input_handlers', {}))
        seconds = config.get('input_lease_seconds', 60)
        if type(seconds) is not int or not 60 <= seconds <= 300:
            raise GeneralError('general_configuration')
        runtime.input_lease_seconds = seconds
        reader_factory = extensions.get('reader_factory')
        if reader_factory is not None:
            runtime.reader = reader_factory(reader=reader, access=reader.access, sink=sink)
        if api is not None:
            runtime.delivery = TelegramDelivery(api,directory,report_view)
        if extensions.get('configure'):
            extensions['configure'](runtime=runtime,directory=directory,vault=vault)
        controller = extensions.get('pilot_controller')
        if controller is not None:
            controller.configure(runtime=runtime,directory=directory,vault=vault,config=config,api=api)
        ingress = PrivateIngress(store,directory,task_view)
        webhook = GeneralWebhook(secret=secret,unit_of_work=ingress,idempotency=GeneralIdempotency(store),users=directory,
            invites=NoInvites(),phone_allowlist=phone_allowlist,clock=clock.now)
        def tick():
            actors = store.db.execute("SELECT actor FROM directory WHERE owner=? AND role='owner'",(owner,)).fetchall()
            for (actor,) in actors:
                try:
                    runtime.tick_schedules(owner,actor)
                    runtime.pump(owner,actor,max_steps=1 if controller is not None else 20)
                    if controller is not None:
                        controller.dispatch(owner,actor)
                        runtime.deliver(owner,actor)
                except (GeneralError,ConditionalConflict,TaskError):
                    continue  # Status/ingress retained; no authority bypass.
        wiring = PollingWiring(store,webhook,secret,task_view,owner,tick)
        return wiring,runtime
    except Exception:
        if 'store' in locals():
            store.close()
        raise GeneralError('general_factory_failed') from None
