"""Opt-in local pilot: deterministic Telegram commands, real Exa and WhatsApp.

No free-text model, synthetic approval, raw phone routing or generic shell tool.
Research retains one investigation identity across explicit /mas invocations.
"""
from dataclasses import asdict, replace
from datetime import timedelta
from hashlib import sha256
import json
import re
from uuid import uuid4

from radar.application.conversations.models import Account
from radar.application.research.cache import CacheBinding, CachePolicy, CacheRecord, CacheError
from radar.adapters.local.research_cache import SQLiteResearchCache
from radar.adapters.local.pilot_sources import AgentReachExa, PilotCapabilities
from radar.adapters.local.whatsapp_bridge import create_bridge, BridgeError
from radar.adapters.sources.generic.model import SourceFailure
from radar.ports.types import BlobPointer, ConditionalConflict, LedgerState
from .general_runtime import GeneralError, canonical
from .sqlite import parse, stamp
from .conversations import SELF_TEST_PURPOSE, SELF_TEST_TEXT


SOURCE = 'source:exaweb'
HELP = ('/vincular +numero: vincular TU WhatsApp por código\n'
    '/chats: listar chats; /leer alias: sincronizar sólo ese chat\n'
    '/responder alias texto: preparar respuesta para aprobar\n'
    '/prueba_whatsapp: mensaje de prueba sólo a ti mismo\n'
    '/investigar tema: búsqueda pública (no pongas datos privados)\n'
    '/mas 20: hasta 20 resultados NUEVOS de la última investigación\n'
    '/refrescar: volver a consultar fuentes conocidas\n'
    '/limites resultados=20 consultas=3 paginas=3 tiempo=60 bytes=524288\n'
    '/estado; /stop. Los límites son techos, no garantías de resultados.')
CAPS = {'max_items': 100, 'max_requests': 10, 'max_pages': 10,
    'max_bytes': 2097152, 'seconds': 300, 'retention_seconds': 2592000}
LIMIT_NAMES = {'resultados':'max_items','consultas':'max_requests','paginas':'max_pages',
    'tiempo':'seconds','bytes':'max_bytes'}
DDL = '''CREATE TABLE IF NOT EXISTS pilot_private(owner TEXT,actor TEXT,kind TEXT,ref TEXT,pointer TEXT,
PRIMARY KEY(owner,actor,kind,ref));'''


def limits(text, defaults):
    updated = dict(defaults)
    seen = set()
    for token in text.split():
        match = re.fullmatch(r'([a-z]+)=([1-9][0-9]{0,8})', token)
        if not match or match[1] not in LIMIT_NAMES or match[1] in seen:
            raise GeneralError('pilot_limits_invalid')
        seen.add(match[1])
        key, value = LIMIT_NAMES[match[1]], int(match[2])
        if value > CAPS[key]:
            raise GeneralError('pilot_limit_exceeded')
        updated[key] = value
    return updated


class Pilot:
    def __init__(self, *, store, vault, host_authority, clock):
        self.store, self.vault, self.host, self.clock = store, vault, host_authority, clock
        self.runtime = None
        owner = store.db.execute('SELECT owner FROM owners').fetchone()[0]
        self.capabilities = PilotCapabilities(owner=owner, clock=clock)
        store.db.executescript(DDL)

    def configure(self, *, runtime, directory, vault, config, api):
        self.runtime, self.directory, self.api = runtime, directory, api
        runtime.conversations.repository.allow_self_test = True
        settings = config['pilot']
        self.defaults = settings['research']
        if set(self.defaults) != set(CAPS) or any(type(v) is not int or not 1 <= v <= CAPS[k] for k,v in self.defaults.items()):
            raise GeneralError('pilot_configuration')
        self.search = AgentReachExa(clock=self.clock, **settings['agent_reach'])
        self.capabilities.search = self.search
        self.cache = SQLiteResearchCache(store=self.store,vault=vault.view('worker:research'),authorize=self._cache_authorize)
        self.wa_vault = vault.view('worker:whatsapp')
        self.bridge = create_bridge(store=self.store, conversations_repository=runtime.conversations.repository,
            vault_view=self.wa_vault, authority_provider=self.fresh, clock=self.clock,
            helper_path=settings['whatsapp_helper'], helper_sha256=settings['whatsapp_helper_sha256'],
            session_root=settings['session_root'], authorize_live=settings['authorize_whatsapp_live'],
            pair_authorizer=self._pair_allowed)
        self.capabilities.whatsapp_verified = self._verified_session
        self.capabilities.whatsapp_operation_verified = self._verified_operation
        runtime.conversations.repository.session_trial_authorizer = self._trial_access
        self.store.allow_source(owner_ref=self.capabilities.owner,source_ref=SOURCE,authorized=True)

    def fresh(self, *, owner_ref, actor_ref):
        return self.runtime.authority(owner_ref,actor_ref)

    def _cache_authorize(self, binding, now):
        authority = self.fresh(owner_ref=binding.owner_ref,actor_ref=binding.actor_ref)
        capability = dict(authority.capabilities).get('search')
        return binding.source_ref == SOURCE and bool(capability) and self.store.source_allowed(owner_ref=binding.owner_ref,source_ref=capability) and self.store.source_allowed(owner_ref=binding.owner_ref,source_ref=SOURCE)

    def _verified_session(self, session):
        return bool(self.store.db.execute('SELECT 1 FROM whatsapp_bridge_accounts WHERE owner=? AND session=? AND paired=1 AND expires>?',
            (self.capabilities.owner,session,stamp(self.clock.now()))).fetchone())

    def _trial_access(self, *, authority, account, operation):
        fresh = self.fresh(owner_ref=authority.owner_ref,actor_ref=authority.actor_ref)
        return operation in ('read','sync','compose','contact') and (account.session_ref,account.session_version) in fresh.sessions and self._verified_session(account.session_ref)

    def _verified_operation(self, session, operation):
        row = self.store.db.execute('SELECT account,actor FROM whatsapp_bridge_accounts WHERE owner=? AND session=? AND paired=1',
            (self.capabilities.owner,session)).fetchone()
        if row is None: return False
        if operation in ('read','sync'):
            methods = ('sync',) if operation=='sync' else ('list_chats','sync')
            return any(self.store.db.execute("SELECT 1 FROM whatsapp_bridge_calls WHERE owner=? AND account=? AND method=? AND state='completed' LIMIT 1",
                (self.capabilities.owner,row[0],method)).fetchone() for method in methods)
        # Local composition is not a provider operation. Both compose/contact
        # graduate only after the bounded self trial is provider-confirmed.
        return self._self_test_confirmed(self.fresh(owner_ref=self.capabilities.owner,actor_ref=row[1]))

    def save(self, authority, kind, ref, body):
        self.fresh(owner_ref=authority.owner_ref,actor_ref=authority.actor_ref)
        pointer = self.runtime.seal(authority.owner_ref,body)
        self.store.db.execute('INSERT OR REPLACE INTO pilot_private VALUES(?,?,?,?,?)',
            (authority.owner_ref,authority.actor_ref,kind,ref,pointer))

    def load(self, authority, kind, ref):
        self.fresh(owner_ref=authority.owner_ref,actor_ref=authority.actor_ref)
        row = self.store.db.execute('SELECT pointer FROM pilot_private WHERE owner=? AND actor=? AND kind=? AND ref=?',
            (authority.owner_ref,authority.actor_ref,kind,ref)).fetchone()
        return self.runtime.open(authority.owner_ref,row[0]) if row else None

    def tell(self, authority, event, text):
        return self.runtime.notify(authority.owner_ref,authority.actor_ref,{'text':text},key=event)

    def _pair_allowed(self, *, authority, account_ref, event_ref, phone_ref):
        intent = self.load(authority,'pair',event_ref)
        return intent == {'account_ref':account_ref,'phone_ref':asdict(phone_ref)}

    def account(self, authority):
        info = self.load(authority,'account','active')
        if info is None:
            raise GeneralError('account_needs_pairing')
        return info['account_ref']

    def _pair(self, authority, event, text):
        if not re.fullmatch(r'\+[1-9][0-9]{6,14}',text.strip()):
            raise GeneralError('own_phone_required')
        info = self.load(authority,'account','active')
        if info is None:
            session = 'session:p'+uuid4().hex
            self.store.db.execute('INSERT INTO sessions VALUES(?,?,1)',(authority.owner_ref,session))
            info = {'account_ref':'account:a'+uuid4().hex,'session_ref':session}
            self.save(authority,'account','active',info)
        authority = self.fresh(owner_ref=authority.owner_ref,actor_ref=authority.actor_ref)
        # Expiry is immutable for repeated registration; do not rotate sessions on reads.
        row = self.store.db.execute('SELECT doc FROM conversation_accounts WHERE owner=? AND account=?',(authority.owner_ref,info['account_ref'])).fetchone()
        expires = parse(json.loads(row[0])['expires_at']) if row else self.clock.now()+timedelta(days=7)
        account = Account(info['account_ref'],'whatsapp','local_wameow','recipient:selfpilot',info['session_ref'],1,expires)
        self.runtime.conversations.repository.register_account(authority=authority,account=account,now=self.clock.now())
        self.bridge.bind_account(authority=authority,account=account)
        phone = self.wa_vault.seal(owner_ref=authority.owner_ref,plaintext=canonical({'declared_phone':text.strip()}).encode())
        self.save(authority,'pair',event,{'account_ref':account.account_ref,'phone_ref':asdict(phone)})
        def notifier(*,owner_ref,actor_ref,event_ref,code):
            current = self.fresh(owner_ref=owner_ref,actor_ref=actor_ref)
            if event_ref != event or self.api is None:
                return False
            ids = self.store.db.execute('SELECT id FROM directory WHERE owner=? AND actor=?',(owner_ref,actor_ref)).fetchall()
            if len(ids)!=1:
                return False
            # Pair code only in RAM/private Telegram, not vault, DB or logs.
            result = self.api.send_message(ids[0][0],'WhatsApp → Dispositivos vinculados → Vincular con número. Código: '+code,protect_content=True)
            return isinstance(result,dict) and type(result.get('message_id')) is int
        self.bridge.pair(authority=authority,account_ref=account.account_ref,event_ref=event,phone_ref=phone,notifier=notifier)
        self.tell(authority,event+':done','WhatsApp vinculado. Usa /chats; todavía no se han leído cuerpos de mensajes.')

    def _chats(self, authority, event):
        account = self.account(authority)
        result = self.bridge.list_chats(authority=authority,account_ref=account,event_ref=event,limit=20)
        body = json.loads(self.wa_vault.open(owner_ref=authority.owner_ref,pointer=result.private_ref))
        own = [item for item in body['chats'] if item.get('is_self') is True]
        if len(own)!=1:
            raise GeneralError('self_chat_unverified')
        repo = self.runtime.conversations.repository
        registered = repo._check(authority,account,'read',self.clock.now())
        self_recipient = 'recipient:r'+sha256((account+own[0]['chat_ref']).encode()).hexdigest()
        repo.register_account(authority=authority,account=replace(registered,self_recipient_ref=self_recipient),now=self.clock.now())
        previous = self.load(authority,'chats','active') or {}
        chats = dict(previous)
        for item in body['chats']:
            alias = 'c'+sha256(item['chat_ref'].encode()).hexdigest()[:16]
            chats[alias] = item
        self.save(authority,'chats','active',chats)
        self.tell(authority,event+':done','\n'.join(alias+' · '+item.get('display_name','Chat')+(' · yo' if item.get('is_self') else '') for alias,item in chats.items()) or 'No hay chats disponibles todavía.')
        return chats

    def enable(self, authority, alias):
        chats = self.load(authority,'chats','active') or {}
        item = chats.get(alias)
        if item is None:
            raise GeneralError('chat_alias_unknown')
        account, chat = self.account(authority),item['chat_ref']
        recipient = 'recipient:r'+sha256((account+chat).encode()).hexdigest()
        body = {'account_ref':account,'chat_ref':chat,'recipient_ref':recipient,
            'display':item.get('display_name') or 'Chat','address':chat}
        repo = self.runtime.conversations.repository
        pointer = repo.vault.seal(owner_ref=authority.owner_ref,plaintext=canonical(body).encode())
        repo.enable_chat(authority=authority,account_ref=account,chat_ref=chat,recipient_ref=recipient,identity_ref=pointer,now=self.clock.now())
        return chat

    def _read(self, authority, event, alias):
        chat = self.enable(authority,alias.strip())
        account = self.account(authority)
        last = self.load(authority,'cursor',chat)
        result = self.bridge.sync(authority=authority,account_ref=account,event_ref=event,enabled_chat_refs=(chat,),limit=20,
            ack_ref=BlobPointer(**last) if last else None)
        body = json.loads(self.wa_vault.open(owner_ref=authority.owner_ref,pointer=result.private_ref))
        self.save(authority,'cursor',chat,asdict(result.private_ref))
        self.tell(authority,event+':done','\n\n'.join(row['text'] for row in body['messages']) or 'Sin mensajes nuevos capturados. La sincronización no garantiza todo el historial.')

    def propose(self, authority, event, goal, steps):
        document = {'schema_version':1,'goal':goal,'privacy_scope':'personal','steps':steps,
            'missing_fields':[],'confidence':1,'budget':{'calls':len(steps),'message_limit':1,'max_usd':'0'}}
        proposal = self.runtime.router.propose(authority=authority,event_ref=event,document=document,now=self.clock.now())
        self.runtime.proposal_notice(authority,proposal)
        return proposal.task_ref

    def _draft(self, authority, event, alias, text, *, self_test=False):
        if self_test and text != SELF_TEST_TEXT:
            raise GeneralError('self_test_text_binding')
        if not self_test and not self._self_test_confirmed(authority):
            raise GeneralError('pilot_self_test_required')
        chat = self.enable(authority,alias)
        pointer = self.runtime.seal(authority.owner_ref,{'account_ref':self.account(authority),'drafts':[
            {'chat_ref':chat,'text':text,'purpose_ref':SELF_TEST_PURPOSE if self_test else 'purpose:p'+uuid4().hex}]})
        return self.propose(authority,event,'Preparar respuesta WhatsApp',[{'step_id':'draft','operation':'compose','arguments':{'private_ref':json.loads(pointer)}}])

    def _self_test_confirmed(self, authority):
        account = self.account(authority)
        rows = self.store.db.execute('SELECT op FROM conversation_actions WHERE owner=? AND account=? AND purpose=?',
            (authority.owner_ref,account,SELF_TEST_PURPOSE)).fetchall()
        return any(self.store.get(owner_ref=authority.owner_ref,operation_id=row[0]).state == LedgerState.PROVIDER_CONFIRMED for row in rows)

    def research_proposal(self, authority, event, text, mode):
        settings = self.load(authority,'limits','active') or self.defaults
        if mode == 'new':
            if not text.strip() or len(text.encode())>2000:
                raise GeneralError('research_topic_required')
            investigation = 'investigation:i'+uuid4().hex
            topic = text.strip()
            self.save(authority,'investigation',investigation,{'topic':topic})
            self.save(authority,'last','active',{'investigation_ref':investigation})
            mode = 'continue'
        else:
            last = self.load(authority,'last','active')
            if last is None:
                raise GeneralError('prior_investigation_required')
            investigation = last['investigation_ref']
            topic = self.load(authority,'investigation',investigation)['topic']
            if text.strip():
                if not re.fullmatch(r'[1-9][0-9]{0,2}',text.strip()) or int(text)>100:
                    raise GeneralError('pilot_limits_invalid')
                settings = {**settings,'max_items':int(text)}
        pass_ref = 'pass:p'+sha256(event.encode()).hexdigest()
        metadata = {'investigation_ref':investigation,'topic':topic,'settings':settings,'mode':mode,'pass_ref':pass_ref}
        pointer = json.loads(self.runtime.seal(authority.owner_ref,metadata))
        steps = [{'step_id':'search'+str(i),'operation':'search','depends_on':['search'+str(i-1)] if i else [],
            'arguments':{'private_ref':pointer,'source_refs':[SOURCE],'query':topic}} for i in range(min(settings['max_requests'],settings['max_pages']))]
        steps.append({'step_id':'report','operation':'inform','depends_on':[steps[-1]['step_id']], 'arguments':{'private_ref':pointer}})
        return self.propose(authority,event,'Investigar: '+topic,steps)

    def input(self, route, *, runtime, authority, event_ref, text, lease):
        try:
            if route == 'pilot_help': self.tell(authority,event_ref,HELP)
            elif route == 'pilot_status': self.tell(authority,event_ref,canonical(runtime.status(authority.owner_ref,authority.actor_ref)))
            elif route == 'research_limits':
                settings = limits(text,self.load(authority,'limits','active') or self.defaults)
                self.save(authority,'limits','active',settings)
                self.tell(authority,event_ref,canonical(settings))
            elif route in ('research','research_continue','research_refresh'):
                return self.research_proposal(authority,event_ref,text,{'research':'new','research_continue':'continue','research_refresh':'refresh'}[route])
            elif route == 'whatsapp_pair': self._pair(authority,event_ref,text)
            elif route == 'whatsapp_chats': self._chats(authority,event_ref)
            elif route == 'whatsapp_read': self._read(authority,event_ref,text)
            elif route == 'whatsapp_draft':
                parts = text.split(maxsplit=1)
                if len(parts)!=2: raise GeneralError('reply_alias_and_text_required')
                return self._draft(authority,event_ref,*parts)
            elif route == 'whatsapp_self_test':
                chats = self._chats(authority,event_ref)
                own = [alias for alias,item in chats.items() if item.get('is_self') is True]
                if len(own)!=1: raise GeneralError('self_chat_unverified')
                return self._draft(authority,event_ref,own[0],SELF_TEST_TEXT,self_test=True)
            else: raise GeneralError('pilot_command_unsupported')
        except (BridgeError,CacheError) as error:
            raise GeneralError(str(error)) from None

    def binding(self, authority, metadata):
        return CacheBinding(authority.owner_ref,authority.actor_ref,metadata['investigation_ref'],SOURCE,sha256(metadata['topic'].encode()).hexdigest())

    def search_step(self, *, runtime, authority, run_ref, task_ref, step, outputs):
        metadata = runtime.open(authority.owner_ref,canonical(step['arguments']['private_ref']))
        binding, now = self.binding(authority,metadata),self.clock.now()
        pass_ref = metadata['pass_ref']
        records = ()
        try:
            saved = self.load(authority,'pass',pass_ref)
            if saved is None:
                settings = metadata['settings']
                run_deadline = parse(self.store.db.execute('SELECT deadline FROM general_runs WHERE owner=? AND ref=?',(authority.owner_ref,run_ref)).fetchone()[0])
                policy = CachePolicy(deadline=min(run_deadline,now+timedelta(seconds=settings['seconds'])),**{k:v for k,v in settings.items() if k!='seconds'})
                saved = {**asdict(policy),'deadline':stamp(policy.deadline)}
                self.save(authority,'pass',pass_ref,saved)
            policy = CachePolicy(**{**saved,'deadline':parse(saved['deadline'])})
            try: view = self.cache.current(binding,now=now)
            except CacheError as error:
                if str(error)!='unknown_cache_binding': raise
                view = None
            if view is None or (view.pass_ref!=pass_ref and step['step_id']=='search0'):
                view = self.cache.open(binding,pass_ref=pass_ref,mode=metadata['mode'],policy=policy,now=now,expected_version=view.version if view else None)
            if view.pass_ref!=pass_ref: raise GeneralError('research_pass_superseded')
            request_ref = 'request:r'+sha256((run_ref+step['step_id']).encode()).hexdigest()
            drain_ref = 'drain:d'+sha256((run_ref+step['step_id']).encode()).hexdigest()
            drained = self.cache.recover_drain(binding,pass_ref=pass_ref,request_ref=drain_ref,now=now)
            if drained is not None:
                records = drained.records
            recovered = self.cache.recover_page(binding,pass_ref=pass_ref,request_ref=request_ref,now=now)
            if recovered is not None:
                records += recovered.records
                return {'cached_records':[r.record_ref for r in records],'state':recovered.view.state},'partial',None
            if drained is None and view.state == 'ready':
                page = self.cache.drain(binding,pass_ref=pass_ref,expected_version=view.version,now=now,request_ref=drain_ref)
                records,view = page.records,page.view
            if view.state == 'ready' and not view.pending_items:
                remaining = (policy.deadline-self.clock.now()).total_seconds()
                if remaining < 1:
                    return {'cached_records':[r.record_ref for r in records],'state':'deadline'},'partial',None
                reservation = self.cache.reserve(binding,pass_ref=pass_ref,request_ref=request_ref,expected_version=view.version,now=now,max_bytes=131072)
                if reservation.replayed:
                    raise GeneralError('research_request_uncertain')
                try:
                    cursor = self.cache.continuation(binding,now=now)
                    round_no = int(cursor.decode()) if cursor else 0
                    query = metadata['topic'] + ('\nFind additional independent sources and alternative sites. Discovery round '+str(round_no+1)+'.' if round_no else '')
                    rows,size = self.search.search(query,10,timeout=min(15,remaining),max_bytes=reservation.max_bytes)
                    self.search.verified_at = self.clock.now()
                    self.fresh(owner_ref=authority.owner_ref,actor_ref=authority.actor_ref)
                    observed = self.clock.now()
                    page = self.cache.commit_page(binding,pass_ref=pass_ref,request_ref=request_ref,expected_version=reservation.view.version,
                        records=tuple(CacheRecord(url,content,observed) for url,content in rows),continuation=str(round_no+1).encode(),
                        received_bytes=size,now=observed)
                    records += page.records
                    view = page.view
                except Exception:
                    if self.cache.current(binding,now=self.clock.now()).version == reservation.view.version:
                        self.cache.abandon(binding,request_ref=request_ref,expected_version=reservation.view.version,now=self.clock.now())
                    raise
            return {'cached_records':[r.record_ref for r in records],'state':view.state},'partial',None
        except (CacheError,SourceFailure):
            return {'cached_records':[r.record_ref for r in records],'state':'read_incomplete'},'partial','research_read_incomplete'

    def inform_step(self, *, runtime, authority, run_ref, task_ref, step, outputs):
        if 'private_ref' not in step['arguments']:
            return {},'blocked','pilot_report_binding_required'
        metadata = runtime.open(authority.owner_ref,canonical(step['arguments']['private_ref']))
        binding = self.binding(authority,metadata)
        records = [r for out in outputs.values() for r in out.get('cached_records',())]
        lines = []
        for ref in records:
            record = self.cache.read_record(binding,record_ref=ref,now=self.clock.now())
            title = record.content.decode('utf-8').splitlines()[0][:240]
            lines.append(title+'\n'+record.url)
        view = self.cache.current(binding,now=self.clock.now())
        self.tell(authority,run_ref+':report','Resultados nuevos: '+str(len(records))+'; consultas: '+str(view.requests)+'; repetidos: '+str(view.skipped)+
            '; parada: '+view.state+'\n\n'+'\n\n'.join(lines)+'\n\nFuentes sin verificar; no se garantiza cobertura de toda la web. /mas N continúa; /refrescar vuelve a consultar.')
        return {'new':len(records),'requests':view.requests,'state':view.state},'succeeded' if records else 'partial',None

    def dispatch(self, owner, actor):
        authority = self.fresh(owner_ref=owner,actor_ref=actor)
        rows = self.store.db.execute("SELECT pointer FROM general_callbacks WHERE owner=? AND actor=? AND kind='approve' AND used=1",(owner,actor)).fetchall()
        for (pointer,) in rows:
            screen = self.runtime.open(owner,pointer)
            run = self.store.db.execute('SELECT run FROM general_approval_runs WHERE owner=? AND batch=?',(owner,screen['batch_ref'])).fetchone()
            if run is None: continue
            status = self.store.db.execute('SELECT status FROM general_runs WHERE owner=? AND ref=?',(owner,run[0])).fetchone()
            if status != ('awaiting_approval',): continue
            self.runtime.check_batch_run(owner,actor,screen['batch_ref'])
            states = []
            for item in screen['rows']:
                try:
                    record = self.bridge.send(authority=authority,operation_id=item['operation_id'],approval_ref=screen['callback_ref'])
                except (BridgeError,ConditionalConflict):
                    self.tell(authority,run[0]+':dispatchblocked','Envío detenido: revisar estado, sesión y aprobación. No se reintentará automáticamente.')
                    self.store.db.execute("UPDATE general_runs SET status='blocked',reason='pilot_dispatch_denied',version=version+1 WHERE owner=? AND ref=?",(owner,run[0]))
                    break
                states.append(record.state)
                self.tell(authority,record.operation_id+':result',record.state.value)
            else:
                terminal = 'succeeded' if states and all(s==LedgerState.PROVIDER_CONFIRMED for s in states) else 'blocked'
                self.store.db.execute('UPDATE general_steps SET state=? WHERE owner=? AND run=? AND state=?',(terminal,owner,run[0],'awaiting_approval'))
                self.store.db.execute('UPDATE general_runs SET status=?,version=version+1 WHERE owner=? AND ref=?',(terminal,owner,run[0]))


def build(*,store,vault,host_authority,clock):
    controller = Pilot(store=store,vault=vault,host_authority=host_authority,clock=clock)
    from .general_ingress import PILOT_ROUTES
    def handler(route):
        return lambda **values: controller.input(route,**values)
    return {'pilot_controller':controller,'capabilities':controller.capabilities,
        'input_handlers':{route:handler(route) for route in PILOT_ROUTES.values()},
        'handlers':{'search':controller.search_step,'inform':controller.inform_step}}
