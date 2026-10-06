"""Opt-in local pilot: Telegram commands plus free text, real Exa and WhatsApp.

Free text goes to pilot_outreach (AI plan, owner-approved sends via the local
bridge). No synthetic approval, model-chosen recipients or generic shell tool.
Research retains one investigation identity across explicit /mas invocations.
"""
from dataclasses import asdict, replace
from datetime import timedelta
from hashlib import sha256
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from radar.application.conversations.models import Account
from radar.application.research.cache import CacheBinding, CachePolicy, CacheRecord, CacheError
from radar.adapters.local.research_cache import SQLiteResearchCache
from radar.adapters.local.pilot_sources import AgentReachExa, OpenCliGoogle, PilotCapabilities
from radar.adapters.local.whatsapp_bridge import create_bridge, BridgeError
from radar.adapters.sources.generic.model import SourceFailure
from radar.ports.types import BlobPointer, ConditionalConflict, LedgerState
from .general_runtime import GeneralError, canonical
from .sqlite import parse, stamp
from .conversations import SELF_TEST_PURPOSE, SELF_TEST_TEXT
from . import pilot_outreach as outreach
from . import gmail
from . import research
from .social import Social, SocialError, LABELS
from email.utils import parseaddr
import subprocess
import tempfile
import sys
import time


SOURCE = 'source:exaweb'
CONTEXT_LIMIT = 12000  # characters of recent conversation kept for the model; cleared (and announced) when exceeded
# Inboxes synced and read; Messenger/Instagram plugins are read-only (missing plugin = per-platform note, not a crash).
SOCIAL = ('x', 'messenger', 'instagram', 'marketplace')
HELP = ('Escribe tu pedido en texto libre: la IA arma búsqueda y mensaje; tú apruebas antes de enviar.\n'
    '/limpiar: borrar la memoria de la conversación (tus datos guardados no se tocan)\n'
    '/vincular +numero: vincular TU WhatsApp por código\n'
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
        # One local bridge per WhatsApp session; the first one is used until requests pick a session.
        bridges = settings.get('bridges', {'principal': {'port': 8080}})
        self.bridges = {name: 'http://127.0.0.1:%d/api/send' % row['port'] for name, row in bridges.items()}
        self.bridge_stores = {name: Path(row['dir'])/'store' for name, row in bridges.items() if row.get('dir')}
        self.database, self._archive, self._archive_at, self._synced_at = settings.get('database'), None, 0, 0
        self.gmail_enabled, self._mail_synced_at = bool(settings.get('gmail')), 0
        node = Path(settings['agent_reach']['node'])
        try: self.web = OpenCliGoogle(node=node, script=node.parent/'node_modules'/'@jackwener'/'opencli'/'dist'/'src'/'main.js')
        except ValueError: self.web = None  # OpenCLI absent: searches report it, commands still work
        try: self.social = Social(node=node, script=node.parent/'node_modules'/'@jackwener'/'opencli'/'dist'/'src'/'main.js')
        except ValueError: self.social = None
        self._social_synced_at, self._locked_told_at = 0, {}
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

    def tell_rich(self, authority, event, text):
        """Model-written prose: Telegram HTML, split into as many messages as needed."""
        for index, chunk in enumerate(outreach.telegram_html(text)):
            self.runtime.notify(authority.owner_ref,authority.actor_ref,{'text':chunk,'html':True},key=event+':'+str(index))

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

    def archive(self, action, *args):
        """Best effort: the analysis archive never blocks the pilot; reconnects at most once a minute."""
        if not self.database:
            return None
        try:
            if self._archive is None:
                if time.monotonic() - self._archive_at < 60:
                    return None
                self._archive_at = time.monotonic()
                from .archive import Archive, dsn_from_env_file  # psycopg only when the archive is configured
                self._archive = Archive(dsn_from_env_file(self.database['env_file'], port=self.database['port']))
            return getattr(self._archive, action)(*args)
        except Exception as error:
            print(json.dumps({'component': 'archive', 'action': action, 'error': type(error).__name__}), file=sys.stderr, flush=True)
            if self._archive is not None and self._archive.conn.closed:
                self._archive = None
            return None

    def replies_tick(self, authority):
        if time.monotonic() - self._synced_at < 60:
            return
        self._synced_at = time.monotonic()
        for name, store in self.bridge_stores.items():
            self.archive('sync_whatsapp', name, store)
        if self.social is not None and time.monotonic() - self._social_synced_at > 600:
            self._social_synced_at = time.monotonic()
            # Messenger only on demand: opening its inbox auto-opens the newest chat and may send a "seen" receipt.
            try: self.social_sync(authority, platforms=tuple(p for p in SOCIAL if p != 'messenger'), read_changed=3)
            except Exception as error:  # background sync must never take the bot down
                print(json.dumps({'component': 'social', 'error': type(error).__name__}), file=sys.stderr, flush=True)
        if self.gmail_enabled and time.monotonic() - self._mail_synced_at > 300:
            self._mail_synced_at = time.monotonic()
            try:
                missing = self.archive('missing_emails', gmail.list_ids('newer_than:30d', 500)) or []
                # Newest 10 first so replies to our emails show up quickly, then backfill oldest 15.
                self.archive('store_emails', [gmail.get(i) for i in dict.fromkeys(missing[:10] + missing[-15:])])
            except Exception as error:  # background sync must never take the bot down
                print(json.dumps({'component': 'gmail', 'error': type(error).__name__}), file=sys.stderr, flush=True)
        for ref, replies in (self.archive('pending_replies') or {}).items():
            try:
                summary = outreach.summarize(self.archive('request_text', ref) or '', replies)
            except outreach.OutreachError:
                return
            self.tell_rich(authority,'replies:'+uuid4().hex,'**Respuestas recibidas** ('+str(len(replies))+' mensajes)\n\n'+summary)
            self.archive('summarized', ref, max(r[2] for r in replies), len(replies), summary)

    def voice(self, authority, event, file_id):
        """Telegram voice note -> local Whisper transcript -> same flow as typed text."""
        from radar.adapters.telegram.bot_api import TelegramError
        from . import transcribe
        for attempt in range(3):  # transient Telegram/network timeouts are common; the file id stays valid
            try:
                data, path = self.api.download_file(file_id)
                break
            except TelegramError as error:
                print(json.dumps({'component':'voice','attempt':attempt,'error':str(error)[:120]}), file=sys.stderr, flush=True)
                if 'too large' in str(error):
                    return self.tell(authority,event+':voice','El audio supera el límite de Telegram para bots (20 MB).')
                time.sleep(2 * (attempt + 1))
        else:
            return self.tell(authority,event+':voice','No pude descargar el audio de Telegram (falla de conexión). Reenvíalo, por favor.')
        with tempfile.TemporaryDirectory(prefix='radar-voz-') as folder:
            audio = Path(folder)/('nota'+(Path(path).suffix or '.ogg'))
            audio.write_bytes(data)
            try: text = transcribe.transcribe(audio)
            except Exception: return self.tell(authority,event+':voice','No pude transcribir el audio.')
        if not text:
            return self.tell(authority,event+':voice','No entendí el audio; prueba de nuevo o escríbelo.')
        self.tell_rich(authority,event+':voice','🎤 *Entendí:* '+text)
        return self.request(authority,event,text)

    def social_sync(self, authority, *, platforms=SOCIAL, read_changed=3, names=()):
        """Refresh inboxes into the archive; read changed (or named) DM threads. -> [(ts, chat, author, text)]."""
        rows, notes = [], []
        for platform in platforms:
            label = LABELS[platform]
            try:
                threads = self.social.threads(platform)
            except SocialError as error:
                if str(error) == 'dm_locked' and time.monotonic() - self._locked_told_at.get(platform, 0) > 3600:
                    self._locked_told_at[platform] = time.monotonic()
                    self.tell(authority,'social:'+uuid4().hex,label+' pide desbloquear los mensajes: hazlo en Chrome.')
                notes.append(label+': '+str(error))
                continue
            changed = self.archive('store_social', threads) or []
            if platform == 'marketplace':
                rows += [(datetime.now(timezone.utc),'Marketplace · '+t['listing'],t['name'],t['last_text']+' ('+str(t['last_time'] or '')+')') for t in threads]
                continue
            wanted = [t for t in threads if any(n.lower() in (t.get('name') or '').lower() for n in names)] if names else changed[:read_changed]
            for thread in wanted[:5]:
                try: messages = self.social.read(platform, thread['thread_id'])
                except SocialError as error:
                    notes.append(label+': '+str(error)); continue
                self.archive('store_social', [], messages)
                rows += [(m['ts'] or datetime.now(timezone.utc),label+' · '+(thread.get('name') or ''),'yo' if m.get('is_me') else (m.get('author') or ''),
                    (m.get('text') or '')+(' ['+m['media']+']' if m.get('media') else '')) for m in messages]
            if not names:
                rows += [(datetime.now(timezone.utc),label+' · '+(t.get('name') or ''),t.get('name') or '',(t.get('last_text') or '')+' ('+str(t.get('last_time') or '')+')') for t in threads if t not in wanted]
        return rows, notes

    def context(self, authority):
        return '\n'.join(('Usuario: ' if turn['role'] == 'user' else 'Asistente: ')+turn['text'] for turn in self.load(authority,'context','active') or [])

    def remember(self, authority, role, text):
        """Rolling conversation memory (sealed). When it exceeds CONTEXT_LIMIT it is cleared and the owner is told."""
        turns = (self.load(authority,'context','active') or []) + [{'role':role,'text':text[:3000]}]
        if sum(len(turn['text']) for turn in turns) > CONTEXT_LIMIT:
            turns = [turns[-1]]
            self.tell(authority,'context:'+uuid4().hex,'🧹 Limpié el contexto de la conversación porque se llenó. '
                'Sigo con tu último pedido; tus chats, correos y datos guardados no se tocaron.')
        self.save(authority,'context','active',turns)

    def clear_context(self, authority, event):
        self.save(authority,'context','active',[])
        self.tell(authority,event+':context','🧹 Contexto limpio. Empezamos de cero; tus datos guardados no se tocaron.')

    def request(self, authority, event, text):
        current = self.load(authority,'outreach','active')
        if current and current.get('state') == 'researching':
            current['queued'] = current.get('queued', []) + [text]
            self.save(authority,'outreach','active',current)
            return self.tell(authority,event+':queued','Sigo con la investigación en curso; cuando termine aplico: «'+text[:200]+
                '» usando sus resultados. Escribe /cancelar si prefieres detenerla.')
        previous = current if current and (current['state'] in ('planned','found') or current.get('summary')) else None
        context = self.context(authority)
        self.remember(authority,'user',text)
        try: plan = outreach.plan(text,previous,context=context,last_research=self.load(authority,'research','last'))
        except outreach.OutreachError as error: raise GeneralError(str(error)) from None
        if plan['confirm'] and current and current['state'] in ('planned','found','choosing'):
            # "sí / dale" after a plan: run the search; sending always needs the button (exact recipients + text).
            if current['state'] == 'planned' and outreach.search_batch(current['plan']):
                return self.button(authority=authority,value={'action':'search','doc':current['id']})
            if current['state'] == 'found' and not current['found']:
                return self.tell(authority,event+':confirm','No hay destinatarios a quién enviar: no encontré números publicados. '
                    'Pídeme buscar en otra zona o con otras palabras, o dame tú los números.')
            return self.tell(authority,event+':confirm','Toca '+('una de las opciones' if current['state']=='choosing' else 'el botón Enviar')+
                ' del mensaje anterior para confirmar destinatarios y texto exactos.')
        direct = [{'phone':p,'url':'','title':'indicado por ti'} for p in outreach.phones(text)]
        last = self.load(authority,'research','last') if plan['use_last_research'] else None
        if last:
            plan = {**plan,'search_query':'','broad_query':'','searches':[]}
            if plan['message'] and not direct and not plan['reply_to_chats'] and last.get('doc'):
                corpus = [tuple(item) for item in self.load(authority,'corpus',last['doc']) or []]
                found = outreach.candidates([(url,text_.encode()) for url,text_ in corpus])
                if not found:
                    return self.tell(authority,event+':nophones','La última investigación no tiene teléfonos publicados. '
                        'Pídeme buscar contactos de esos negocios y los busco.')
                hint = (' (negocios mencionados: '+', '.join(plan['send_to'])+')') if plan['send_to'] else ''
                chosen, reason = outreach.pick(text+hint, found, last.get('findings', []))
                plan = {**plan,'send_to':[]}
                direct += chosen
                if reason:
                    plan = {**plan,'reply':plan['reply']+' '+reason}
        if plan['message'] and self.gmail_enabled and not plan['email_reply']:
            direct += [{'phone':a,'url':'','title':'correo indicado por ti','channel':'email',
                'subject':plan['email_subject'] or 'Consulta'} for a in dict.fromkeys(gmail.EMAIL.findall(text))]
        if plan['read_chats'] or (plan['read_email'] and self.gmail_enabled) or (plan['read_social'] and self.social is not None):
            messages, notes = [], []
            if plan['read_social'] and self.social is not None:
                found, social_notes = self.social_sync(authority, platforms=plan['social_platforms'] or SOCIAL, names=plan['chat_names'])
                messages += found; notes += social_notes
            if plan['read_chats']:
                chats = self.archive('read_messages', plan['chat_names'], plan['keywords'], plan['hours'], 300, outreach.phones(text))
                if chats is None: notes.append('WhatsApp no disponible (¿Docker apagado?)')
                messages += chats or []
            if plan['read_email'] and self.gmail_enabled:
                try:
                    mails = gmail.search(plan['email_query'] or 'newer_than:1d', 30)
                    self.archive('store_emails', mails)
                    messages += [(m['ts'],'correo: '+m['subject'],m['from'],(m['body'][:1500] or m['snippet'])+
                        (' [adjuntos: '+', '.join(a['filename'] for a in m['attachments'])+']' if m['attachments'] else '')) for m in mails]
                except (gmail.GmailError, subprocess.TimeoutExpired, OSError):
                    mails = []
                    notes.append('Gmail no respondió')
            messages.sort(key=lambda row: row[0], reverse=True)
            with tempfile.TemporaryDirectory(prefix='radar-adjuntos-') as folder:
                files = []
                if plan['read_email'] and self.gmail_enabled and mails:
                    try: files = gmail.save_attachments(sorted(mails,key=lambda m:m['ts'],reverse=True),folder)
                    except (gmail.GmailError, subprocess.TimeoutExpired, OSError, KeyError, ValueError): notes.append('adjuntos no disponibles')
                    if files: notes.append(str(len(files))+' adjuntos leídos')
                try: reply, composed = outreach.answer(text, messages, files=files, files_dir=folder, context=context)
                except outreach.OutreachError as error: raise GeneralError(str(error)) from None
            self.remember(authority,'assistant',reply)
            self.tell_rich(authority,event+':read',reply+'\n\n*'+str(len(messages))+' mensajes leídos'+('; '+'; '.join(notes) if notes else '')+'*')
            if composed and (plan['reply_to_chats'] or plan['email_reply'] or plan['reply_social'] or direct):
                plan = {**plan, 'message': composed}  # written from what was just read; still needs the owner's approval
            if not (plan['message'] and (plan['reply_to_chats'] or plan['email_reply'] or plan['reply_social'] or direct)):
                return None
        # Recipients resolve whenever sending is intended, even if the message is written later (after research).
        searching = bool(outreach.search_batch(plan))
        will_write = bool(plan['message'] or searching)
        if plan['reply_social'] and will_write and self.social is not None and 'x' in plan['social_platforms']:
            targets = plan['send_to'] or plan['chat_names']
            if not self.archive('resolve_social', 'x', targets):
                self.social_sync(authority, platforms=('x',), read_changed=0)
            direct += [{'phone':thread,'url':'','title':name+' (X)','channel':'x'} for thread,name in self.archive('resolve_social','x',targets) or ()]
        if plan['email_reply'] and plan['message'] and self.gmail_enabled and plan['email_query']:
            try: hit = gmail.search(plan['email_query'],1)
            except (gmail.GmailError, subprocess.TimeoutExpired, OSError): hit = []
            for m in hit:
                subject = m['subject'] if m['subject'].lower().startswith('re:') else 'Re: '+m['subject']
                direct.append({'phone':parseaddr(m['reply_to'] or m['from'])[1],'url':'','title':'responder «'+m['subject']+'»',
                    'channel':'email','subject':subject,'thread':m['thread_id'],'in_reply_to':m['message_id']})
        if plan['reply_to_chats'] and will_write:
            targets = plan['send_to'] or plan['chat_names']
            chats = self.archive('resolve_chats', targets) or []
            if not chats:
                options = []
                for name in targets:
                    options += [{'phone':jid,'url':'','title':display+' (chat existente)','label':display+(' · …'+pn[-4:] if pn else '')}
                        for _,jid,display,pn,_ in self.archive('similar_chats', name) or ()]
                options = list({o['phone']:o for o in options}.values())[:6]
                if not options:
                    return self.tell(authority,event+':nochat','No encontré un chat de WhatsApp parecido a «'+', '.join(targets)+
                        '». Escríbeme el nombre como lo tienes guardado o su número.')
                doc = {'id':uuid4().hex,'state':'choosing','plan':plan,'found':direct,'pages':[],'options':options,'request':text}
                if searching:
                    doc = {**doc,'found':[],'pending':direct}
                self.save(authority,'outreach','active',doc)
                self.archive('request', doc['id'], text, doc['plan'])
                return self.ask_choice(authority,event,doc,', '.join(targets))
            direct += [{'phone':jid,'url':'','title':name+' (chat existente)'} for _,jid,name in chats]
        if previous is None or plan['new_request']:
            doc = {'id':uuid4().hex,'state':'found' if direct else 'planned','plan':plan,'found':direct,'pages':[],'request':text}
            if searching and direct and (plan['reply_to_chats'] or plan['send_to'] or plan['reply_social']):
                # "investiga X y mándaselo a Y": recipients are known, the message must be written from the research.
                doc = {**doc,'state':'planned','found':[],'pending':direct}
        else:
            drop = set(plan['exclude'])
            found = [r for i,r in enumerate(previous['found'],1) if i not in drop]+direct
            doc = {**previous,'plan':plan,'found':found,'state':'found' if found else previous['state']}
        self.save(authority,'outreach','active',doc)
        self.archive('request', doc['id'], text, doc['plan'])
        self.remember(authority,'assistant',plan['reply']+(' Mensaje propuesto: '+plan['message'] if plan['message'] else ''))
        self.show(authority,event,doc)

    def show(self, authority, key, doc):
        plan, found = doc['plan'], doc['found']
        lines = [plan['reply']]
        batch = outreach.search_batch(plan)
        if doc['state'] == 'planned' and batch:
            lines.append('**Búsquedas ('+str(len(batch))+')**\n'+'\n'.join('• '+outreach.SOURCE_NAMES[s]+': '+q for s,q in batch))
        if doc['pages'] and not found:
            lines.append('**Fuentes**\n'+'\n'.join('• '+title+' — '+url for title,url in doc['pages'][:8]))
        if found:
            lines.append('**Destinatarios**\n'+'\n'.join(
                str(i)+'. '+(r['phone']+' · ' if r['phone'].startswith('+') or r.get('channel')=='email' else '')+r['title']
                +('\n   Asunto: '+r['subject'] if r.get('channel')=='email' else '')+('\n   '+r['url'] if r['url'] else '') for i,r in enumerate(found,1)))
        if plan['message']:
            lines.append('**Mensaje** (así se verá en el chat):\n'+plan['message'])
        buttons = []
        if plan['ready'] and found and plan['message']:
            buttons.append(('Enviar a '+str(len(found)),'send'))
        elif plan['ready'] and doc['state'] == 'planned' and batch:
            buttons.append(('Buscar','search'))
        chunks = outreach.telegram_html('\n\n'.join(lines+(['*Para corregir, escríbelo.*'] if buttons else [])))
        for index, chunk in enumerate(chunks[:-1]):
            self.runtime.notify(authority.owner_ref,authority.actor_ref,{'text':chunk,'html':True},key=key+':outreach:'+uuid4().hex+':'+str(index))
        body = {'text':chunks[-1],'html':True}
        if buttons:
            buttons.append(('Cancelar','cancel'))
            body['buttons'] = []
            for label,action in buttons:
                token = 'gcb:a'+uuid4().hex
                self.store.db.execute("INSERT INTO general_callbacks VALUES(?,?,?,'pilot',?,0)",(authority.owner_ref,authority.actor_ref,token,
                    self.runtime.seal(authority.owner_ref,{'action':action,'doc':doc['id']})))
                body['buttons'].append({'text':label,'callback_ref':token})
        self.runtime.notify(authority.owner_ref,authority.actor_ref,body,key=key+':outreach:'+uuid4().hex)

    def ask_choice(self, authority, event, doc, asked):
        """Owner picks the intended chat among the most similar names (one button each)."""
        buttons = []
        for index, option in enumerate(doc['options']):
            token = 'gcb:a'+uuid4().hex
            self.store.db.execute("INSERT INTO general_callbacks VALUES(?,?,?,'pilot',?,0)",(authority.owner_ref,authority.actor_ref,token,
                self.runtime.seal(authority.owner_ref,{'action':'pick','doc':doc['id'],'index':index})))
            buttons.append({'text':option['label'][:60],'callback_ref':token})
        token = 'gcb:a'+uuid4().hex
        self.store.db.execute("INSERT INTO general_callbacks VALUES(?,?,?,'pilot',?,0)",(authority.owner_ref,authority.actor_ref,token,
            self.runtime.seal(authority.owner_ref,{'action':'cancel','doc':doc['id']})))
        buttons.append({'text':'Ninguno / cancelar','callback_ref':token})
        self.runtime.notify(authority.owner_ref,authority.actor_ref,{'text':'No encontré exactamente «'+asked+
            '». ¿Es alguno de estos chats? (ordenados por parecido)','buttons':buttons},key=event+':choose:'+uuid4().hex)

    def button(self, *, authority, value):
        doc = self.load(authority,'outreach','active')
        key = 'outreach:'+uuid4().hex
        if doc is not None and doc['id'] == value['doc'] and doc['state'] == 'researching':
            return self.tell(authority,key,'Sigo investigando; te aviso cuando tenga el resumen.')
        if doc is None or doc['id'] != value['doc'] or doc['state'] not in ('planned','found','choosing'):
            return self.tell(authority,key,'Ese plan ya no está activo.')
        if value['action'] == 'pick':
            if doc['state'] != 'choosing' or not 0 <= value.get('index', -1) < len(doc['options']):
                return self.tell(authority,key,'Esa opción ya no está activa.')
            chosen = {k:v for k,v in doc['options'][value['index']].items() if k != 'label'}
            if 'pending' in doc:  # research still to do before writing the message
                doc = {**doc,'state':'planned','pending':doc['pending']+[chosen],'options':[]}
            else:
                doc = {**doc,'state':'found','found':doc['found']+[chosen],'options':[]}
            self.save(authority,'outreach','active',doc)
            return self.show(authority,key,doc)
        if value['action'] == 'cancel':
            self.save(authority,'outreach','active',{**doc,'state':'cancelled'})
            return self.tell(authority,key,'Cancelado.')
        if value['action'] == 'search':
            if self.web is None or self.social is None:
                return self.tell(authority,key,'OpenCLI no está instalado; no puedo buscar.')
            # Research runs as stages in the dispatch tick (research_tick), never inside this callback's lease.
            deep = 'pending' in doc or not doc['plan']['message']
            rounds = research.DEPTH_ROUNDS.get(doc['plan'].get('depth'), 1) if deep else 0
            batch = outreach.search_batch(doc['plan'])
            self.save(authority,'outreach','active',{**doc,'state':'researching','stage':'search','rounds':rounds,'batch':batch,'done':[],'findings':[]})
            return self.tell(authority,key,'🔎 Investigando: '+str(len(batch))+' búsquedas en '+', '.join(dict.fromkeys(outreach.SOURCE_NAMES[s] for s,_ in batch))
                +(' y '+str(rounds)+' ronda'+('s' if rounds>1 else '')+' de profundización' if rounds else '')+'. Te aviso el avance.')
        queue = self.load(authority,'queue','active') or {'items':[],'next':None,'waiting':False}
        bridge = next(iter(self.bridges))
        queue['items'] += [{'phone':r['phone'],'text':doc['plan']['message'],'bridge':bridge,'request':doc['id'],'label':r['title'],
            **{k:r[k] for k in ('channel','subject','thread','in_reply_to') if k in r}} for r in doc['found']]
        self.save(authority,'queue','active',queue)
        self.save(authority,'outreach','active',{**doc,'state':'queued'})
        self.tell(authority,key,'En cola: '+str(len(doc['found']))+'. El bridge envía cada 2 s; contactos nuevos: máx. 8 cada 20 min y 30 al día. Las respuestas llegan a tu WhatsApp.')

    def run_batch(self, doc, batch, budget=150):
        """Run (source, query) searches within a time budget -> (rows, failed source names)."""
        rows, failed, started = [], [], time.monotonic()
        for source, query in batch:
            if time.monotonic() - started > budget:
                failed.append(outreach.SOURCE_NAMES[source]+' (sin tiempo)'); continue
            for attempt in range(2):  # shared Chrome bridge: a concurrent automation can reject navigation; retry once
                try:
                    found = (self.web.search(query,20,timeout=90,max_bytes=2097152)[0] if source == 'google'
                        else self.social.search(source,query,10))
                    self.archive('search', doc['id'], '['+source+'] '+query, found, outreach.phones)
                    rows += [(url, content.decode('utf-8','replace')) for url,content in found]
                    break
                except (SourceFailure, SocialError, subprocess.TimeoutExpired, OSError, ValueError):
                    if attempt:
                        failed.append(outreach.SOURCE_NAMES[source])
                    else:
                        time.sleep(3)
        return rows, failed

    def research_tick(self, authority):
        doc = self.load(authority,'outreach','active')
        if not doc or doc.get('state') != 'researching':
            return
        try:
            self.research_step(authority, doc)
        except Exception as error:  # a failed stage must not take the bot down nor loop forever
            print(json.dumps({'component':'research','stage':doc.get('stage'),'error':type(error).__name__}), file=sys.stderr, flush=True)
            self.save(authority,'outreach','active',{**doc,'state':'failed'})
            self.tell(authority,'research:'+uuid4().hex,'La investigación se detuvo por un error ('+type(error).__name__+'). Pídela de nuevo.')

    def research_step(self, authority, doc):
        """One stage per tick: search -> [analyze -> deepen]*rounds -> synthesize."""
        key, request = 'research:'+uuid4().hex, doc.get('request') or doc['plan']['search_query']
        corpus = [tuple(item) for item in self.load(authority,'corpus',doc['id']) or []]
        def add(rows):
            known = {url for url,_ in corpus}
            corpus.extend((url,text) for url,text in rows if url not in known and not known.add(url))
            del corpus[research.MAX_CORPUS:]
            self.save(authority,'corpus',doc['id'],[list(item) for item in corpus])
        stage = doc['stage']
        if stage == 'search':
            rows, failed = self.run_batch(doc, doc['batch'])
            add(rows)
            doc['done'] = doc['done'] + ['['+s+'] '+q for s,q in doc['batch']]
            if failed:
                self.tell(authority,key+':failed','Sin resultados o con error en: '+', '.join(failed)+'.')
            if not corpus:
                self.save(authority,'outreach','active',{**doc,'state':'failed'})
                return self.tell(authority,key,'Ninguna búsqueda devolvió resultados. Revisa que Chrome con OpenCLI esté abierto y prueba de nuevo.')
            if not ('pending' in doc or not doc['plan']['message']):  # contacting businesses found: extract numbers
                if len(outreach.candidates([(u,x.encode()) for u,x in corpus])) < 3:
                    # Snippets rarely carry phones: read the businesses' own pages (contact info), skipping
                    # aggregators and social groups. Only URLs already found by the searches.
                    skip = ('facebook.com/groups','yelp.','google.','youtube.','reddit.','x.com','twitter.','instagram.','tiktok.')
                    sites = [url for url,_ in corpus if not any(s in url for s in skip)][:5]
                    self.tell(authority,key+':reading','Pocos teléfonos en los resultados; leo '+str(len(sites))+' páginas de los negocios…')
                    add([(url,'Title: '+url+'\n'+text) for url in sites for text in [research.read_page(self.social.node, self.social.script, url)] if text])
                pages = [(text.splitlines()[0].removeprefix('Title: ')[:120] if text else url,url) for url,text in corpus]
                doc = {**doc,'state':'found','found':outreach.candidates([(u,x.encode()) for u,x in corpus]),'pages':pages}
                if not doc['found']:
                    doc['plan'] = {**doc['plan'],'reply':'No encontré números publicados en '+str(len(corpus))+' páginas.'}
                self.save(authority,'outreach','active',doc)
                return self.show(authority,key,doc)
            doc['stage'] = 'analyze' if doc['rounds'] > 0 else 'synthesize'
            self.tell(authority,key,'Ronda 1 lista: '+str(len(corpus))+' resultados.'+(' Analizo qué falta…' if doc['rounds'] else ' Preparo el resumen…'))
        elif stage == 'analyze':
            result = research.analyze(request, corpus, doc['done'])
            doc['findings'] = (doc['findings'] + result['findings'])[-60:]
            doc['next'] = {'followups':result['followups'],'read':result['read_urls']}
            deepen = not result['enough'] and (result['followups'] or result['read_urls'])
            doc['stage'] = 'deepen' if deepen else 'synthesize'
            self.tell_rich(authority,key,'🧭 '+result['progress']+(
                '\n\n**Profundizo:** leo '+str(len(result['read_urls']))+' páginas completas'
                +''.join('\n• '+outreach.SOURCE_NAMES[s]+': '+q for s,q in result['followups']) if deepen else '\n\nPreparo el resumen final…'))
        elif stage == 'deepen':
            node = Path(self.social.node); script = self.social.script
            pages = [(url,'Title: '+url+'\n'+text) for url in doc['next']['read']
                for text in [research.read_page(str(node), script, url)] if text]
            add(pages)
            rows, _ = self.run_batch(doc, doc['next']['followups'], budget=90)
            add(rows)
            doc['done'] = doc['done'] + ['['+s+'] '+q for s,q in doc['next']['followups']]
            doc['rounds'] -= 1
            doc['stage'] = 'analyze' if doc['rounds'] > 0 else 'synthesize'
            self.tell(authority,key,'Leí '+str(len(pages))+' páginas y '+str(len(rows))+' resultados nuevos.'
                +(' Otra ronda de análisis…' if doc['rounds'] > 0 else ' Preparo el resumen final…'))
        else:
            return self.finish_research(authority, doc, corpus, key, request)
        self.save(authority,'outreach','active',doc)

    def finish_research(self, authority, doc, corpus, key, request):
        pages = list(dict.fromkeys((text.splitlines()[0].removeprefix('Title: ').removeprefix('# ')[:120] if text else url,url) for url,text in corpus))
        try: summary, composed = outreach.answer(research.synthesis_request(request, doc.get('findings')), research.corpus_messages(corpus))
        except outreach.OutreachError as error:
            self.save(authority,'outreach','active',{**doc,'state':'failed'})
            return self.tell(authority,key,'No pude resumir la investigación: '+str(error))
        sources = '\n'.join('• '+title+' — '+url for title,url in pages[:6])
        self.remember(authority,'assistant',summary)
        self.save(authority,'research','last',{'request':request,'summary':summary,'doc':doc['id'],'findings':doc.get('findings',[])[:40]})
        self.tell_rich(authority,key+':summary',summary+'\n\n**Fuentes principales**\n'+sources)
        if 'pending' not in doc:  # research only: keep the summary for "envíale el resumen a…"
            self.save(authority,'outreach','active',{**doc,'state':'done','pages':pages,'summary':summary,'found':[],'queued':[]})
            for queued in doc.get('queued', []):
                self.request(authority, key+':queued:'+uuid4().hex, queued)
            return None
        doc = {**doc,'state':'found','found':doc['pending'],'pages':pages,
            'plan':{**doc['plan'],'message':composed or doc['plan']['message'],'search_query':'','broad_query':'','searches':[]}}
        doc.pop('pending')
        self.save(authority,'outreach','active',doc)
        return self.show(authority,key,doc)

    def outreach_tick(self, authority):
        queue = self.load(authority,'queue','active')
        now = self.clock.now()
        if not queue or not queue['items'] or (queue['next'] and parse(queue['next']) > now):
            return
        while queue['items']:
            item = queue['items'].pop(0)
            self.save(authority,'queue','active',queue)  # at-most-once: popped before the POST
            if item.get('channel') == 'x':
                state, detail = self.social.x_send(item['phone'],outreach.channel_text(item['text'],'plain')) if self.social else ('failed','opencli_missing')
                item['bridge'] = 'x'
            elif item.get('channel') == 'email':
                state, detail, item['thread'] = gmail.send(item['phone'],item['subject'],outreach.channel_text(item['text'],'plain'),
                    thread_id=item.get('thread'),in_reply_to=item.get('in_reply_to',''))
                item['bridge'] = 'gmail'
            else:
                url = self.bridges.get(item.get('bridge',next(iter(self.bridges))))
                state, detail = outreach.send(item['phone'],outreach.channel_text(item['text'],'whatsapp'),url=url) if url else ('failed','bridge_not_configured')
            if state.startswith('limit'):
                queue['items'].insert(0,item)
                queue['next'] = stamp(now+timedelta(minutes=60 if state=='limit_day' else 3))
                if not queue['waiting']:
                    self.tell(authority,'outreach:'+uuid4().hex,'Límite del bridge: '+detail+'. Quedan '+str(len(queue['items']))+'; reanudo solo.')
                queue['waiting'] = True
                break
            queue['waiting'] = False
            self.archive('outbound', item.get('request'), item.get('bridge'), item['phone'], item.get('label'), item['text'], state, detail, item.get('thread'))
            self.tell(authority,'outreach:'+uuid4().hex,('Enviado a ' if state=='sent' else 'No enviado a ')+(item.get('label') if item.get('label') and not item['phone'].startswith('+') else item['phone'])+(': '+detail if detail else ''))
        else:
            queue['next'] = None
        self.save(authority,'queue','active',queue)

    def input(self, route, *, runtime, authority, event_ref, text, lease):
        try:
            if route == 'request': self.request(authority,event_ref,text)
            elif route == 'voice': self.voice(authority,event_ref,text)
            elif route == 'context_clear': self.clear_context(authority,event_ref)
            elif route == 'research_cancel':
                doc = self.load(authority,'outreach','active')
                if doc and doc.get('state') in ('researching','planned','found','choosing'):
                    self.save(authority,'outreach','active',{**doc,'state':'cancelled','queued':[]})
                    self.tell(authority,event_ref+':cancel','Cancelado. Puedes pedirme otra cosa.')
                else:
                    self.tell(authority,event_ref+':cancel','No hay nada en curso que cancelar.')
            elif route == 'pilot_help': self.tell(authority,event_ref,HELP)
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
        self.outreach_tick(authority)
        self.research_tick(authority)
        self.replies_tick(authority)
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
        'input_handlers':{**{route:handler(route) for route in (*PILOT_ROUTES.values(),'request','voice')},'pilot_callback':controller.button},
        'handlers':{'search':controller.search_step,'inform':controller.inform_step}}
