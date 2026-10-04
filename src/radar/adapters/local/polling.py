"""Bounded local polling over B's webhook and A3 SQLite control plane.

No default bot/directory/vault wiring. ResponseVault is a trusted encrypted
private backend: only opaque refs and hashes enter the polling control tables.
"""
from dataclasses import dataclass, field
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from threading import Event, RLock
from uuid import uuid4

from radar.adapters.telegram.bot_api import BotApi, RateLimited, TelegramError
from radar.adapters.telegram.webhook import Webhook
from .sqlite import SQLiteStore, canonical


ERROR_CODES = frozenset({
    'invalid_wiring','invalid_configuration','invalid_limits','single_owner_store_required',
    'private_backend_required','private_backend_unavailable','poller_already_active',
    'poller_lease_lost','bot_owner_binding','invalid_webhook_info','active_webhook_requires_takeover',
    'takeover_failed','bot_preflight_failed','invalid_reply','reply_recipient_binding',
    'update_replay_binding','webhook_not_committed','private_response_write_failed','invalid_private_ref',
    'private_response_read_failed','private_response_integrity','runner_not_prepared',
    'poll_rate_limited','poll_failed','invalid_updates','retry_budget_exhausted',
    'invalid_factory','private_path_required','bot_io_not_authorized','secret_configuration_required',
    'factory_binding','local_polling_failed',
})


class PollingError(Exception):
    def __init__(self, code, *, retry_after=None):
        code = code if isinstance(code,str) and code in ERROR_CODES else 'local_polling_failed'
        self.code, self.retry_after = code, retry_after
        super().__init__(code)


@dataclass(repr=False)
class PollingWiring:
    store: SQLiteStore
    webhook: Webhook
    secret: str
    vault: object
    owner_ref: str
    tick: object = field(default=lambda: None)

    def __repr__(self):
        return 'PollingWiring(<private>)'


DDL = """
CREATE TABLE IF NOT EXISTS polling_offsets(bot TEXT PRIMARY KEY, owner TEXT, next_update INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS polling_receipts(bot TEXT, update_id INTEGER, owner TEXT, hash TEXT,
 state TEXT, private_ref TEXT, response_hash TEXT, code TEXT, PRIMARY KEY(bot,update_id));
CREATE INDEX IF NOT EXISTS polling_pending ON polling_receipts(bot,owner,state,update_id);
"""

OPAQUE = re.compile(r'[A-Za-z0-9_:.-]{1,200}')
REPLY_FIELDS = {
    'sendMessage': {'method', 'chat_id', 'text', 'reply_markup', 'parse_mode'},
    'answerCallbackQuery': {'method', 'callback_query_id', 'text'},
}


class PollingRunner:
    def __init__(self, api, wiring, *, bot_ref, timeout=20, batch_size=20,
                 max_errors=5, backoff_cap=30, diagnostic=lambda code: None,
                 clock=lambda: datetime.now(timezone.utc), lease_seconds=60):
        if not isinstance(api,BotApi) or not isinstance(wiring,PollingWiring):
            raise PollingError('invalid_wiring')
        if not isinstance(wiring.store,SQLiteStore) or not isinstance(wiring.webhook,Webhook):
            raise PollingError('invalid_wiring')
        if (not isinstance(wiring.secret,str) or not wiring.secret or not callable(wiring.tick)
            or not isinstance(wiring.owner_ref,str) or not OPAQUE.fullmatch(wiring.owner_ref)
            or not isinstance(bot_ref,str) or not OPAQUE.fullmatch(bot_ref)):
            raise PollingError('invalid_configuration')
        if (type(timeout) is not int or not 1<=timeout<=30 or type(batch_size) is not int
            or not 1<=batch_size<=100 or type(max_errors) is not int or not 1<=max_errors<=10
            or type(backoff_cap) is not int or not 1<=backoff_cap<=30
            or type(lease_seconds) is not int or not timeout+10<=lease_seconds<=300 or not callable(clock)):
            raise PollingError('invalid_limits')
        self.api,self.wiring,self.bot = api,wiring,bot_ref
        self.timeout,self.batch_size = timeout,batch_size
        self.max_errors,self.backoff_cap = max_errors,backoff_cap
        self.diagnostic,self._lock = diagnostic,RLock()
        self.clock,self.ttl = clock,timedelta(seconds=lease_seconds)
        self.lease = None
        self._prepared = False

    def __repr__(self):
        return 'PollingRunner(<private>)'

    @property
    def offset(self):
        return self.wiring.store.db.execute('SELECT next_update FROM polling_offsets WHERE bot=? AND owner=?',
            (self.bot,self.wiring.owner_ref)).fetchone()[0]

    def prepare(self, *, take_over_bot=False):
        """Check all local gates before any Bot API call, including takeover."""
        with self._lock:
            if self._prepared:
                self._assert_lease()
                return
            store,owner,vault = self.wiring.store,self.wiring.owner_ref,self.wiring.vault
            owners = store.db.execute('SELECT owner FROM owners').fetchall()
            if owners != [(owner,)]:
                raise PollingError('single_owner_store_required')
            if not all(callable(getattr(vault,name,None)) for name in ('preflight','put','get')):
                raise PollingError('private_backend_required')
            try:
                # Must assert durable encryption and owner-scoped access. This
                # is a trusted factory contract, not an encryption implementation.
                if vault.preflight(owner_ref=owner) is not True:
                    raise PollingError('private_backend_unavailable')
            except Exception:
                raise PollingError('private_backend_unavailable') from None
            store.db.executescript(DDL)
            self.lease = store.acquire(owner_ref=owner,session_ref='telegram-poll:'+self.bot,
                worker_ref='poller:'+str(uuid4()),now=self.clock(),ttl=self.ttl)
            if self.lease is None:
                raise PollingError('poller_already_active')
            try:
                self._prepare_bot(take_over_bot)
            except BaseException:
                self.close()
                raise

    def _prepare_bot(self, take_over_bot):
        store,owner = self.wiring.store,self.wiring.owner_ref
        with self._transaction():
            row = store.db.execute('SELECT owner FROM polling_offsets WHERE bot=?',(self.bot,)).fetchone()
            if row and row[0] != owner:
                raise PollingError('bot_owner_binding')
            store.db.execute('INSERT OR IGNORE INTO polling_offsets VALUES(?,?,0)',(self.bot,owner))
            # Any prior claimed reply may have reached Telegram. Never resend.
            store.db.execute("UPDATE polling_receipts SET state='send_uncertain',code='send_uncertain' WHERE bot=? AND owner=? AND state='dispatch_committed'",(self.bot,owner))
        try:
            self._assert_lease()
            info = self.api.call('getWebhookInfo')
            if not isinstance(info,dict) or not isinstance(info.get('url'),str):
                raise PollingError('invalid_webhook_info')
            if info['url']:
                if take_over_bot is not True:
                    raise PollingError('active_webhook_requires_takeover')
                self._assert_lease()
                if self.api.call('deleteWebhook',{'drop_pending_updates':False}) is not True:
                    raise PollingError('takeover_failed')
        except TelegramError:
            raise PollingError('bot_preflight_failed') from None
        self._assert_lease()
        self._prepared = True

    def _assert_lease(self):
        if self.lease is None or not self.wiring.store.is_current(owner_ref=self.wiring.owner_ref,
                                                               lease=self.lease,now=self.clock()):
            raise PollingError('poller_lease_lost')

    def _renew(self):
        self._assert_lease()
        renewed = self.wiring.store.renew(owner_ref=self.wiring.owner_ref,lease=self.lease,now=self.clock(),ttl=self.ttl)
        if renewed is None:
            raise PollingError('poller_lease_lost')
        self.lease = renewed

    @contextmanager
    def _transaction(self):
        with self.wiring.store.transaction():
            self._assert_lease()
            yield
            self._assert_lease()

    def close(self):
        with self._lock:
            if self.lease is not None:
                self.wiring.store.release(owner_ref=self.wiring.owner_ref,lease=self.lease)
                self.lease = None
            self._prepared = False

    def _receipt(self, update_id):
        return self.wiring.store.db.execute('SELECT hash,state,private_ref,response_hash,code FROM polling_receipts WHERE bot=? AND owner=? AND update_id=?',
            (self.bot,self.wiring.owner_ref,update_id)).fetchone()

    def _checkpoint(self, update_id, state, *, private_ref=None, response_hash=None, code=None):
        store = self.wiring.store
        with self._transaction():
            store.db.execute('UPDATE polling_receipts SET state=?,private_ref=?,response_hash=?,code=? WHERE bot=? AND owner=? AND update_id=?',
                (state,private_ref,response_hash,code,self.bot,self.wiring.owner_ref,update_id))
            store.db.execute('UPDATE polling_offsets SET next_update=max(next_update,?) WHERE bot=? AND owner=?',
                (update_id+1,self.bot,self.wiring.owner_ref))

    @staticmethod
    def _reply(body, update=None):
        if not isinstance(body,(str,bytes)) or len(body)>64*1024:
            raise PollingError('invalid_reply')
        try:
            reply = json.loads(body)
        except (ValueError,TypeError,RecursionError):
            raise PollingError('invalid_reply') from None
        method = reply.get('method') if isinstance(reply,dict) else None
        if method not in REPLY_FIELDS or not set(reply)<=REPLY_FIELDS[method]:
            raise PollingError('invalid_reply')
        if not isinstance(reply.get('text'),str) or not reply['text']:
            raise PollingError('invalid_reply')
        if method=='sendMessage':
            if len(reply['text'])>4096:
                raise PollingError('invalid_reply')
            if 'parse_mode' in reply and reply['parse_mode']!='HTML':
                raise PollingError('invalid_reply')
            if type(reply.get('chat_id')) is not int:
                raise PollingError('invalid_reply')
            if update is not None:
                target = (update.get('message') or {}).get('chat',{}).get('id')
                if target is None:
                    target = (update.get('callback_query') or {}).get('from',{}).get('id')
                if reply['chat_id'] != target:
                    raise PollingError('reply_recipient_binding')
        else:
            if len(reply['text'])>200:
                raise PollingError('invalid_reply')
            if not isinstance(reply.get('callback_query_id'),str) or not reply['callback_query_id']:
                raise PollingError('invalid_reply')
            if update is not None and reply['callback_query_id'] != (update.get('callback_query') or {}).get('id'):
                raise PollingError('reply_recipient_binding')
        return reply

    def _handle(self, update, *, failpoint):
        store,owner = self.wiring.store,self.wiring.owner_ref
        update_id,content_hash = update['update_id'],sha256(canonical(update)).hexdigest()
        with self._transaction():
            prior = self._receipt(update_id)
            if prior and prior[0] != content_hash:
                raise PollingError('update_replay_binding')
            if prior and prior[1] != 'handling':
                return
            store.db.execute("INSERT OR IGNORE INTO polling_receipts(bot,update_id,owner,hash,state) VALUES(?,?,?,?,'handling')",
                (self.bot,update_id,owner,content_hash))
        # No raw update/contact/text is persisted by this adapter.
        self._assert_lease()
        response = self.wiring.webhook.handle_update(
            {'X-Telegram-Bot-Api-Secret-Token':self.wiring.secret},canonical(update))
        failpoint('after_webhook')
        if response.get('statusCode') != 200:
            raise PollingError('webhook_not_committed')
        body = response.get('body','')
        if body:
            reply = self._reply(body,update)
            content = canonical(reply)
            self._assert_lease()
            try:
                ref = self.wiring.vault.put(owner_ref=owner,content=content)
            except Exception:
                raise PollingError('private_response_write_failed') from None
            if not isinstance(ref,str) or not OPAQUE.fullmatch(ref):
                raise PollingError('invalid_private_ref')
            failpoint('after_private_response')
            self._checkpoint(update_id,'ready',private_ref=ref,response_hash=sha256(content).hexdigest())
        else:
            # On replay B may have committed the command/consent but lost its
            # inline reply. Do not fabricate a new acknowledgement.
            code = 'response_unavailable' if prior else None
            self._checkpoint(update_id,'response_unavailable' if prior else 'no_reply',code=code)
            if code:
                self.diagnostic(code)
        failpoint('after_response_checkpoint')

    def _send_pending(self, *, failpoint, stop):
        store,owner = self.wiring.store,self.wiring.owner_ref
        self._assert_lease()
        rows = store.db.execute("SELECT update_id,private_ref,response_hash FROM polling_receipts WHERE bot=? AND owner=? AND state='ready' ORDER BY update_id LIMIT ?",
            (self.bot,owner,self.batch_size)).fetchall()
        for update_id,ref,expected_hash in rows:
            if stop.is_set():
                return
            self._renew()
            try:
                content = self.wiring.vault.get(owner_ref=owner,ref=ref)
            except Exception:
                raise PollingError('private_response_read_failed') from None
            if not isinstance(content,bytes) or sha256(content).hexdigest()!=expected_hash:
                raise PollingError('private_response_integrity')
            reply = self._reply(content)
            with self._transaction():
                claimed = store.db.execute("UPDATE polling_receipts SET state='dispatch_committed' WHERE bot=? AND owner=? AND update_id=? AND state='ready'",
                    (self.bot,owner,update_id)).rowcount
            if not claimed:
                continue
            failpoint('after_reply_claim')
            self._assert_lease()
            try:
                result = self.api.call(reply['method'],{key:value for key,value in reply.items() if key!='method'})
            except TelegramError:
                with self._transaction():
                    store.db.execute("UPDATE polling_receipts SET state='send_uncertain',code='send_uncertain' WHERE bot=? AND owner=? AND update_id=?",(self.bot,owner,update_id))
                self.diagnostic('send_uncertain')
                continue
            valid = (isinstance(result,dict) and type(result.get('message_id')) is int
                     if reply['method']=='sendMessage' else result is True)
            if not valid:
                with self._transaction():
                    store.db.execute("UPDATE polling_receipts SET state='send_uncertain',code='send_uncertain' WHERE bot=? AND owner=? AND update_id=?",(self.bot,owner,update_id))
                self.diagnostic('send_uncertain')
                continue
            failpoint('after_reply_send')
            with self._transaction():
                store.db.execute("UPDATE polling_receipts SET state='sent',code=NULL WHERE bot=? AND owner=? AND update_id=?",(self.bot,owner,update_id))

    def poll_once(self, *, stop=None, failpoint=lambda stage: None):
        with self._lock:
            if not self._prepared:
                raise PollingError('runner_not_prepared')
            stop = stop or Event()
            if stop.is_set():
                return 0
            self._renew()
            self._send_pending(failpoint=failpoint,stop=stop)
            if stop.is_set():
                return 0
            self._renew()
            try:
                updates = self.api.call('getUpdates',{'offset':self.offset,'timeout':self.timeout,
                    'limit':self.batch_size,'allowed_updates':['message','callback_query']})
            except RateLimited as error:
                raise PollingError('poll_rate_limited',retry_after=error.retry_after) from None
            except TelegramError:
                raise PollingError('poll_failed') from None
            if not isinstance(updates,list) or len(updates)>self.batch_size:
                raise PollingError('invalid_updates')
            if any(not isinstance(u,dict) or type(u.get('update_id')) is not int
                   or not 0<=u['update_id']<2**63-1 for u in updates):
                raise PollingError('invalid_updates')
            for update in sorted(updates,key=lambda item:item['update_id']):
                if stop.is_set():
                    break
                if update['update_id'] < self.offset:
                    prior = self._receipt(update['update_id'])
                    if prior and prior[0]!=sha256(canonical(update)).hexdigest():
                        raise PollingError('update_replay_binding')
                    continue
                self._handle(update,failpoint=failpoint)
                self._send_pending(failpoint=failpoint,stop=stop)
            if not stop.is_set():
                self._assert_lease()
                self.wiring.tick()
            return len(updates)

    def run(self, *, stop=None, max_polls=100, wait=None):
        if type(max_polls) is not int or not 1<=max_polls<=10000:
            raise PollingError('invalid_limits')
        stop = stop or Event()
        wait = wait or stop.wait
        errors = 0
        for _ in range(max_polls):
            if stop.is_set():
                return
            try:
                count = self.poll_once(stop=stop)
            except PollingError as error:
                if error.code not in {'poll_failed','poll_rate_limited','webhook_not_committed',
                                      'private_response_write_failed','private_response_read_failed'}:
                    raise
                errors += 1
                self.diagnostic(error.code)
                if errors>=self.max_errors:
                    raise PollingError('retry_budget_exhausted') from None
                wait(min(self.backoff_cap,max(1,error.retry_after or 2**(errors-1))))
            else:
                errors = 0
                if not count:
                    wait(1)
