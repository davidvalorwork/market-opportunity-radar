"""Offline transport + real B webhook + A3 SQLite; no accounts or network."""
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from threading import Barrier, Event
from uuid import uuid4

import pytest

from radar.adapters.local.polling import PollingError, PollingRunner, PollingWiring
from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.telegram import Directory, Idempotency, NoInvites, TelegramBridge
from radar.adapters.telegram import consent
from radar.adapters.telegram.allowlist import PhoneAllowlist
from radar.adapters.telegram.bot_api import BotApi
from radar.adapters.telegram.webhook import Webhook

OWNER,ACTOR = 'owner:local','user:local'
SECRET,TOKEN,PHONE = 'synthetic-secret','synthetic-token','+10000000000'
NOW = datetime(2026,10,3,tzinfo=timezone.utc)


class FakePrivateVault:
    """Owner-bound fake only; does not implement or claim real encryption."""
    def __init__(self):
        self.values = {}
        self.available = True
    def preflight(self, *, owner_ref):
        return self.available and owner_ref==OWNER
    def put(self, *, owner_ref, content):
        ref = 'private:'+str(uuid4())
        self.values[(owner_ref,ref)] = content
        return ref
    def get(self, *, owner_ref, ref):
        return self.values[(owner_ref,ref)]


class BotTransport:
    def __init__(self,updates=(),*,webhook=''):
        self.updates,self.webhook = list(updates),webhook
        self.calls,self.sent = [],[]
        self.poll_failures = []
        self.send_failure = None
    def __call__(self,url,data,headers,timeout):
        method = url.rsplit('/',1)[-1]
        params = json.loads(data)
        self.calls.append((method,params,timeout))
        if method=='getWebhookInfo':
            result = {'url':self.webhook}
        elif method=='deleteWebhook':
            assert params=={'drop_pending_updates':False}
            self.webhook = ''
            result = True
        elif method=='getUpdates':
            if self.poll_failures:
                failure = self.poll_failures.pop(0)
                if isinstance(failure,Exception):
                    raise failure
                return 429,json.dumps({'ok':False,'parameters':{'retry_after':failure}}).encode()
            result = [u for u in self.updates if u.get('update_id',0)>=params['offset']][:params['limit']]
        elif method in ('sendMessage','answerCallbackQuery'):
            self.sent.append((method,params))
            if self.send_failure:
                raise self.send_failure
            result = {'message_id':100} if method=='sendMessage' else True
        else:
            raise AssertionError('unexpected_bot_method')
        return 200,json.dumps({'ok':True,'result':result}).encode()


def message(update_id=1, *, text='/start',numeric=101):
    return {'update_id':update_id,'message':{'chat':{'id':numeric,'type':'private'},'from':{'id':numeric},'text':text}}


def build(path, vault, transport, *, enroll=True, secret=SECRET, diagnostic=lambda code: None, tick=lambda: None, clock=lambda:NOW):
    store = SQLiteStore(path)
    store.enroll(owner_ref=OWNER,actor_ref='user:bootstrap')
    directory = Directory(store,synthetic_contact_owner_ref=OWNER)
    if enroll and directory.get(101) is None:
        directory.enroll_synthetic(101,owner_ref=OWNER,actor_ref=ACTOR)
    phones = PhoneAllowlist.from_json(json.dumps({'schema_version':1,'entries':[{'phone_e164':PHONE,'role':'owner'}]}))
    webhook = Webhook(secret=SECRET,unit_of_work=TelegramBridge(store,directory),idempotency=Idempotency(store),
        users=directory,invites=NoInvites(),phone_allowlist=phones,clock=lambda:NOW)
    wiring = PollingWiring(store,webhook,secret,vault,OWNER,tick)
    runner = PollingRunner(BotApi(TOKEN,transport=transport,timeout=25),wiring,bot_ref='bot:local',diagnostic=diagnostic,clock=clock)
    return runner,directory


@pytest.fixture
def setup(tmp_path):
    vault,transport = FakePrivateVault(),BotTransport([message()])
    runner,directory = build(tmp_path/'.local/control.sqlite',vault,transport)
    yield runner,directory,vault,transport
    runner.wiring.store.close()


def receipt(runner):
    return runner.wiring.store.db.execute('SELECT state,private_ref,code FROM polling_receipts ORDER BY update_id').fetchall()


def crash_at(expected):
    def crash(stage):
        if stage==expected:
            raise RuntimeError('injected_crash')
    return crash


def restart(runner,vault,transport,**kwargs):
    path=runner.wiring.store.path
    runner.wiring.store.close()
    # Simulate abrupt process death: the old durable lease must expire before
    # a new worker can acquire a higher token/version. No force-unlock.
    resumed,_=build(path,vault,transport,enroll=False,clock=lambda:NOW+timedelta(seconds=61),**kwargs)
    resumed.prepare()
    return resumed


def test_same_webhook_persists_command_response_and_offset_then_replay_converges(setup):
    runner,directory,vault,transport=setup
    runner.prepare()
    assert runner.poll_once()==1
    assert runner.offset==2 and receipt(runner)[0][0]=='sent'
    assert len(transport.sent)==1
    assert runner.wiring.store.db.execute('SELECT count(*) FROM commands').fetchone()[0]==1
    assert transport.calls[1][1]['allowed_updates']==['message','callback_query']
    assert transport.calls[1][1]['timeout']==20
    resumed=restart(runner,vault,transport)
    try:
        resumed.poll_once()
        assert len(transport.sent)==1
        assert resumed.wiring.store.db.execute('SELECT count(*) FROM commands').fetchone()[0]==1
    finally:
        resumed.wiring.store.close()


def test_contact_enrollment_consent_then_search_uses_real_gates(setup):
    runner,directory,vault,transport=setup
    transport.updates=[message(numeric=202),
        {'update_id':2,'message':{'chat':{'id':202,'type':'private'},'from':{'id':202},
                               'contact':{'user_id':202,'phone_number':PHONE}}},
        message(3,text='/buscar tema general',numeric=202),
        {'update_id':4,'callback_query':{'id':'synthetic-query','from':{'id':202},'data':consent.ACCEPT}},
        message(5,text='/buscar tema general',numeric=202)]
    runner.prepare()
    runner.poll_once()
    assert runner.offset==6 and len(transport.sent)==5
    assert directory.get(202)['consent_version']==consent.CONSENT_VERSION
    assert runner.wiring.store.db.execute('SELECT count(*) FROM commands').fetchone()[0]==1
    assert runner.wiring.store.pending(owner_ref=OWNER,limit=10).entries[0].envelope['payload']['args']['query']=='tema general'
    with sqlite3.connect(runner.wiring.store.path) as db:
        assert PHONE not in ''.join(db.iterdump())


def test_unauthorized_revoked_and_group_updates_never_create_command(setup):
    runner,directory,vault,transport=setup
    runner.wiring.store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
    group=message(3)
    group['message']['chat']['type']='group'
    transport.updates=[message(text='/buscar privado'),message(2,numeric=303,text='/buscar privado'),group]
    runner.prepare()
    runner.poll_once()
    assert runner.offset==4
    assert runner.wiring.store.db.execute('SELECT count(*) FROM commands').fetchone()[0]==0
    assert len(transport.sent)==2


@pytest.mark.parametrize('takeover',[False,True])
def test_active_webhook_requires_explicit_takeover_without_dropping_updates(setup,takeover):
    runner,directory,vault,transport=setup
    transport.webhook='https://example.invalid/private-hook'
    if not takeover:
        with pytest.raises(PollingError,match='active_webhook_requires_takeover'):
            runner.prepare()
        assert [c[0] for c in transport.calls]==['getWebhookInfo']
    else:
        runner.prepare(take_over_bot=True)
        assert [c[0] for c in transport.calls]==['getWebhookInfo','deleteWebhook']
        runner.poll_once()
        assert len(transport.sent)==1


@pytest.mark.parametrize('broken',['missing_vault','vault_unavailable','second_owner','owner_binding'])
def test_local_preflight_failures_precede_every_bot_call(setup,broken):
    runner,directory,vault,transport=setup
    if broken=='missing_vault':
        runner.wiring.vault=None
    elif broken=='vault_unavailable':
        vault.available=False
    elif broken=='second_owner':
        runner.wiring.store.enroll(owner_ref='owner:foreign',actor_ref='user:foreign')
    else:
        runner.wiring.owner_ref='owner:foreign'
    with pytest.raises(PollingError):
        runner.prepare(take_over_bot=True)
    assert transport.calls==[]


@pytest.mark.parametrize('stage',['after_webhook','after_private_response','after_response_checkpoint'])
def test_crash_after_webhook_preserves_command_and_reports_reply_gap_or_recovers_private_reply(setup,stage):
    runner,directory,vault,transport=setup
    diagnostics=[]
    runner.prepare()
    with pytest.raises(RuntimeError,match='injected_crash'):
        runner.poll_once(failpoint=crash_at(stage))
    expected_offset=2 if stage=='after_response_checkpoint' else 0
    assert runner.offset==expected_offset and not transport.sent
    resumed=restart(runner,vault,transport,diagnostic=diagnostics.append)
    try:
        resumed.poll_once()
        assert resumed.offset==2
        assert resumed.wiring.store.db.execute('SELECT count(*) FROM commands').fetchone()[0]==1
        if stage=='after_response_checkpoint':
            assert receipt(resumed)[0][0]=='sent' and len(transport.sent)==1
        else:
            assert receipt(resumed)[0][0]=='response_unavailable' and not transport.sent
            assert diagnostics==['response_unavailable']
    finally:
        resumed.wiring.store.close()


@pytest.mark.parametrize('stage',['after_reply_claim','after_reply_send'])
def test_claim_or_send_crash_becomes_uncertain_without_blind_resend(setup,stage):
    runner,directory,vault,transport=setup
    runner.prepare()
    with pytest.raises(RuntimeError):
        runner.poll_once(failpoint=crash_at(stage))
    assert runner.offset==2 and receipt(runner)[0][0]=='dispatch_committed'
    effects=0 if stage=='after_reply_claim' else 1
    assert len(transport.sent)==effects
    resumed=restart(runner,vault,transport)
    try:
        resumed.poll_once()
        assert receipt(resumed)[0][0]=='send_uncertain' and len(transport.sent)==effects
    finally:
        resumed.wiring.store.close()


def test_transport_send_failure_is_uncertain_and_never_retried(setup,caplog):
    runner,directory,vault,transport=setup
    transport.send_failure=TimeoutError(f'{TOKEN} {PHONE} private text')
    runner.prepare()
    runner.poll_once()
    runner.poll_once()
    assert receipt(runner)[0][0]=='send_uncertain' and len(transport.sent)==1
    assert TOKEN not in caplog.text and PHONE not in caplog.text and 'private text' not in caplog.text


def test_webhook_commit_failure_does_not_advance_offset(setup):
    runner,directory,vault,transport=setup
    runner.prepare()
    runner.wiring.store.failpoint=crash_at('after_command')
    with pytest.raises(PollingError,match='webhook_not_committed'):
        runner.poll_once()
    assert runner.offset==0 and receipt(runner)[0][0]=='handling' and not transport.sent
    assert runner.wiring.store.db.execute('SELECT count(*) FROM commands').fetchone()[0]==0
    runner.wiring.store.failpoint=lambda stage:None
    runner.poll_once()
    assert runner.offset==2 and receipt(runner)[0][0]=='sent'


def test_wrong_webhook_secret_does_not_bypass_authentication(setup):
    runner,directory,vault,transport=setup
    runner.wiring.secret='wrong-secret'
    runner.prepare()
    with pytest.raises(PollingError,match='webhook_not_committed'):
        runner.poll_once()
    assert runner.offset==0 and not transport.sent
    assert runner.wiring.store.db.execute('SELECT count(*) FROM commands').fetchone()[0]==0


def test_poll_backoff_is_bounded_and_retry_budget_exhausts(setup):
    runner,directory,vault,transport=setup
    transport.poll_failures=[9999]*5
    waits=[]
    runner.prepare()
    with pytest.raises(PollingError,match='retry_budget_exhausted'):
        runner.run(wait=waits.append)
    assert waits==[30]*4 and runner.offset==0


def test_poll_transient_error_then_success_resets_budget(setup):
    runner,directory,vault,transport=setup
    transport.poll_failures=[TimeoutError('private transport error')]
    waits=[]
    runner.prepare()
    runner.run(max_polls=2,wait=waits.append)
    assert waits==[1] and runner.offset==2 and len(transport.sent)==1


def test_stop_before_poll_and_after_checkpoint_is_clean(setup):
    runner,directory,vault,transport=setup
    runner.prepare()
    stop=Event()
    stop.set()
    assert runner.poll_once(stop=stop)==0 and len(transport.calls)==1
    stop.clear()
    def stopping(stage):
        if stage=='after_response_checkpoint':
            stop.set()
    runner.poll_once(stop=stop,failpoint=stopping)
    assert runner.offset==2 and not transport.sent and receipt(runner)[0][0]=='ready'
    stop.clear()
    runner.poll_once(stop=stop)
    assert len(transport.sent)==1


def test_private_response_digest_and_owner_gate_before_send(setup):
    runner,directory,vault,transport=setup
    runner.prepare()
    with pytest.raises(RuntimeError):
        runner.poll_once(failpoint=crash_at('after_response_checkpoint'))
    ref=receipt(runner)[0][1]
    vault.values[(OWNER,ref)]=b'private tamper'
    with pytest.raises(PollingError,match='private_response_integrity'):
        runner.poll_once()
    assert not transport.sent and receipt(runner)[0][0]=='ready'


def test_control_tables_and_repr_do_not_contain_update_or_reply_content(setup,caplog):
    runner,directory,vault,transport=setup
    transport.updates=[message(text='sensitive synthetic topic')]
    runner.prepare()
    runner.poll_once()
    rows=runner.wiring.store.db.execute('SELECT * FROM polling_receipts').fetchall()
    data=str(rows)
    assert all(value not in (101,'101') for row in rows for value in row)
    for private in (TOKEN,SECRET,PHONE,'sensitive synthetic topic','Comando no reconocido','chat_id'):
        assert private not in data and private not in repr(runner)+repr(runner.wiring)+caplog.text
    assert vault.values  # private response exists only in the injected test backend


def test_changed_update_same_id_is_rejected_even_after_offset(setup):
    runner,directory,vault,transport=setup
    runner.prepare()
    runner.poll_once()
    # Simulate malicious/stale transport returning an already acknowledged ID.
    original=transport.__class__.__call__
    class Replay(BotTransport):
        def __call__(self,url,data,headers,timeout):
            if url.endswith('/getUpdates'):
                return 200,json.dumps({'ok':True,'result':[message(text='/stop')]}).encode()
            return original(self,url,data,headers,timeout)
    transport.__class__=Replay
    with pytest.raises(PollingError,match='update_replay_binding'):
        runner.poll_once()
    assert runner.offset==2 and len(transport.sent)==1


@pytest.mark.parametrize('reply',[{'method':'deleteWebhook'},
    {'method':'sendMessage','chat_id':999,'text':'forged'},
    {'method':'sendMessage','chat_id':101,'text':'forged','other':'bad'}])
def test_only_bound_allowlisted_webhook_replies_can_be_dispatched(setup,monkeypatch,reply):
    runner,directory,vault,transport=setup
    monkeypatch.setattr(runner.wiring.webhook,'handle_update',lambda headers,body:{'statusCode':200,'body':json.dumps(reply)})
    runner.prepare()
    with pytest.raises(PollingError):
        runner.poll_once()
    assert runner.offset==0 and not transport.sent and not vault.values


def test_tick_is_explicit_local_factory_work_not_a_fake_default(setup):
    runner,directory,vault,transport=setup
    ticks=[]
    runner.wiring.tick=lambda:ticks.append(runner.wiring.store.pending(owner_ref=OWNER,limit=10))
    runner.prepare()
    runner.poll_once()
    assert len(ticks)==1 and len(ticks[0].entries)==1


def test_checkpoint_rollback_does_not_advance_offset_or_send(setup):
    runner,directory,vault,transport=setup
    runner.prepare()
    def arm(stage):
        if stage=='after_webhook':
            runner.wiring.store.failpoint=crash_at('before_commit')
    with pytest.raises(RuntimeError):
        runner.poll_once(failpoint=arm)
    assert runner.offset==0 and receipt(runner)[0][0]=='handling' and not transport.sent
    assert runner.wiring.store.db.execute('SELECT count(*) FROM commands').fetchone()[0]==1


def test_confirmation_storage_failure_retains_uncertainty_without_resend(setup,monkeypatch):
    runner,directory,vault,transport=setup
    runner.prepare()
    original=runner.wiring.store.db.execute
    def fail_confirmation(sql,parameters=()):
        if sql.startswith("UPDATE polling_receipts SET state='sent'"):
            raise sqlite3.OperationalError('injected storage failure')
        return original(sql,parameters)
    monkeypatch.setattr(runner.wiring.store.db,'execute',fail_confirmation)
    with pytest.raises(sqlite3.OperationalError):
        runner.poll_once()
    assert runner.offset==2 and receipt(runner)[0][0]=='dispatch_committed'
    assert len(transport.sent)==1
    resumed=restart(runner,vault,transport)
    try:
        resumed.poll_once()
        assert receipt(resumed)[0][0]=='send_uncertain' and len(transport.sent)==1
    finally:
        resumed.wiring.store.close()


def test_unreadable_private_backend_never_dispatches(setup,monkeypatch):
    runner,directory,vault,transport=setup
    runner.prepare()
    with pytest.raises(RuntimeError):
        runner.poll_once(failpoint=crash_at('after_response_checkpoint'))
    def fail(**kwargs):
        raise OSError(f'{TOKEN} {PHONE}')
    monkeypatch.setattr(vault,'get',fail)
    with pytest.raises(PollingError,match='private_response_read_failed'):
        runner.poll_once()
    assert not transport.sent and receipt(runner)[0][0]=='ready'


def test_reply_write_failure_keeps_offset_and_marks_gap_on_redelivery(setup,monkeypatch):
    runner,directory,vault,transport=setup
    runner.prepare()
    def fail(**kwargs):
        raise OSError('private backend failed')
    monkeypatch.setattr(vault,'put',fail)
    with pytest.raises(PollingError,match='private_response_write_failed'):
        runner.poll_once()
    assert runner.offset==0 and not transport.sent
    runner.poll_once()
    assert runner.offset==2 and receipt(runner)[0][0]=='response_unavailable'
    assert runner.wiring.store.db.execute('SELECT count(*) FROM commands').fetchone()[0]==1


@pytest.mark.parametrize('limits',[{'timeout':0},{'timeout':31},{'batch_size':101},{'max_errors':0},{'backoff_cap':31}])
def test_bad_limits_fail_without_api_call(setup,limits):
    runner,directory,vault,transport=setup
    with pytest.raises(PollingError,match='invalid_limits'):
        PollingRunner(runner.api,runner.wiring,bot_ref='bot:local',**limits)
    assert transport.calls==[]


def test_pending_response_cannot_be_claimed_under_another_bot_owner(setup):
    runner,directory,vault,transport=setup
    runner.prepare()
    with pytest.raises(RuntimeError):
        runner.poll_once(failpoint=crash_at('after_response_checkpoint'))
    ref=receipt(runner)[0][1]
    with pytest.raises(KeyError):
        vault.get(owner_ref='owner:foreign',ref=ref)
    assert not transport.sent


def test_two_concurrent_pollers_exclude_second_before_takeover_or_poll(setup):
    first,directory,vault,transport=setup
    other_transport=BotTransport([message()],webhook='https://example.invalid/active')
    second,_=build(first.wiring.store.path,vault,other_transport,enroll=False)
    transport.webhook='https://example.invalid/active'
    barrier=Barrier(2)
    def prepare(runner):
        barrier.wait(timeout=2)
        try:
            runner.prepare(take_over_bot=True)
        except PollingError as error:
            return error.code
        return 'prepared'
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(prepare,(first,second)))
        assert sorted(results)==['poller_already_active','prepared']
        winner,loser=(first,second) if results[0]=='prepared' else (second,first)
        winning_transport,losing_transport=(transport,other_transport) if winner is first else (other_transport,transport)
        assert losing_transport.calls==[]
        assert [c[0] for c in winning_transport.calls]==['getWebhookInfo','deleteWebhook']
        winner.poll_once()
        assert len(transport.sent)+len(other_transport.sent)==1
        with pytest.raises(PollingError,match='runner_not_prepared'):
            loser.poll_once()
    finally:
        first.close()
        second.close()
        second.wiring.store.close()


@pytest.mark.parametrize('boundary',['after_webhook','after_response_checkpoint','after_reply_claim','after_reply_send'])
def test_replaced_lease_fences_old_checkpoint_or_reply_without_duplicate(setup,boundary):
    first,directory,vault,transport=setup
    times=[NOW]
    first.clock=lambda:times[0]
    first.prepare()
    next_transport=BotTransport([message()])
    second,_=build(first.wiring.store.path,vault,next_transport,enroll=False,clock=lambda:times[0])
    old_lease=[]
    def takeover(stage):
        if stage==boundary:
            old_lease.append(first.lease)
            times[0]=NOW+timedelta(seconds=61)
            second.prepare()
    try:
        with pytest.raises(PollingError,match='poller_lease_lost'):
            first.poll_once(failpoint=takeover)
        assert second.lease.version>old_lease[0].version and second.lease.token!=old_lease[0].token
        expected_offset=0 if boundary=='after_webhook' else 2
        assert first.offset==second.offset==expected_offset
        count_before=0 if boundary!='after_reply_send' else 1
        assert len(transport.sent)==count_before
        with pytest.raises(PollingError,match='poller_lease_lost'):
            first.poll_once()
        # Closing an obsolete poller cannot release the replacement lease.
        first.close()
        second._assert_lease()
        second.poll_once()
        expected_effects=1 if boundary in ('after_response_checkpoint','after_reply_send') else 0
        assert len(transport.sent)+len(next_transport.sent)==expected_effects
        assert second.offset==2
        if boundary in ('after_reply_claim','after_reply_send'):
            assert receipt(second)[0][0]=='send_uncertain'
        elif boundary=='after_webhook':
            assert receipt(second)[0][0]=='response_unavailable'
        else:
            assert receipt(second)[0][0]=='sent'
    finally:
        second.close()
        second.wiring.store.close()


def test_expired_lease_blocks_network_and_graceful_close_releases_only_current_epoch(setup):
    runner,directory,vault,transport=setup
    times=[NOW]
    runner.clock=lambda:times[0]
    runner.prepare()
    times[0]=NOW+timedelta(seconds=60)
    with pytest.raises(PollingError,match='poller_lease_lost'):
        runner.poll_once()
    assert [c[0] for c in transport.calls]==['getWebhookInfo'] and runner.offset==0
    runner.close()
    fresh,_=build(runner.wiring.store.path,vault,transport,enroll=False,clock=lambda:times[0])
    try:
        fresh.prepare()
        fresh.poll_once()
        assert len(transport.sent)==1
    finally:
        fresh.close()
        fresh.wiring.store.close()
