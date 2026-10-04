from dataclasses import asdict,replace
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
from threading import Event,Thread

import pytest

from radar.adapters.local.general_fixture import build,message,request,OWNER,ACTOR,SECRET
from radar.adapters.local.general_runtime import GeneralError
from radar.adapters.local.general_bindings import ContactBridge
from radar.adapters.local.contact import LocalContacts
from radar.adapters.local.polling import PollingRunner
from radar.adapters.sources.generic import RawItem
from radar.adapters.sources.generic.fixtures import fixture_page
from radar.adapters.telegram.bot_api import BotApi
from radar.adapters.telegram import consent
from radar.application.contact.model import ResolvedContact
from radar.application.conversations import Incoming,IncomingPage
from radar.ports.types import ConditionalConflict,LedgerState,BlobPointer
from radar.entrypoints.general_local import main


MARKER = 'PRIVATE_MESSAGE_CONTACT_CUSTOMER_916z'


@pytest.fixture
def assembly(tmp_path):
    value = build(tmp_path/'control.sqlite')
    yield value
    value['store'].close()


def accept(value,document,*,update=1,free=False):
    body = json.dumps(message(update,('' if free else '/pedir ')+json.dumps(document))).encode()
    assert value['wiring'].webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},body)['statusCode']==200
    value['runtime'].ingest(OWNER)
    task = value['store'].db.execute('SELECT task FROM general_inputs WHERE task IS NOT NULL ORDER BY rowid DESC LIMIT 1').fetchone()[0]
    proposal = value['runtime'].tasks._row(OWNER,task)
    value['runtime'].deliver(OWNER,ACTOR)
    return proposal


def confirm(value,proposal):
    callback = next(button.callback_ref for button in proposal.buttons if button.text=='Confirmar')
    return value['runtime'].callback(OWNER,ACTOR,callback,event_ref='event:a'+str(proposal.version)+proposal.task_ref.split(':')[1])


@pytest.mark.parametrize('topic,source',[('Perfumes','perfumes'),('Empleos','jobs'),('Música','events'),('Ciencia','articles')])
def test_general_private_ingress_plan_reader_report_without_products(assembly,topic,source):
    doc = request(topic)
    doc['steps'][0]['arguments']['source_refs']=['source:'+source]
    proposal = accept(assembly,doc,free=True)
    confirmed = confirm(assembly,proposal)
    assert confirmed.confirmation_hash==confirmed.content_hash
    runtime = assembly['runtime']
    assert runtime.pump(OWNER,ACTOR)==2
    statuses = runtime.status(OWNER,ACTOR)
    assert len(statuses)==1 and statuses[0][2]=='partial'  # Real source cost unknown.
    assert len(assembly['transport'].calls)==1
    assert assembly['store'].db.execute('SELECT count(*) FROM ledger').fetchone()[0]==0
    assert assembly['store'].db.execute('SELECT count(*) FROM commands').fetchone()[0]==0
    assert assembly['store'].db.execute('SELECT count(*) FROM outbox').fetchone()[0]==0
    reports = [notice['body'] for notice in assembly['notices'] if 'document_ref' in notice['body']]
    assert len(reports)==1
    private = json.loads(assembly['views']['worker:research'].open(owner_ref=OWNER,pointer=BlobPointer(**reports[0]['document_ref'])))
    assert private['topic']==topic and private['notes'] and private['notes'][0]['citations']


def test_ingress_preserves_secret_and_current_consent_no_raw_text_sqlite_wal(assembly):
    wiring,store,directory = assembly['wiring'],assembly['store'],assembly['directory']
    body = json.dumps(message(1,'/pedir '+MARKER)).encode()
    assert wiring.webhook.handle_update({},body)['statusCode']==401
    assert not store.db.execute('SELECT * FROM general_inputs').fetchall()
    directory.withdraw_consent(owner_ref=OWNER,actor_ref=ACTOR)
    assert wiring.webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},body)['statusCode']==200
    assert not store.db.execute('SELECT * FROM general_inputs').fetchall()
    directory.accept_consent(ACTOR,consent.CONSENT_VERSION,assembly['clock'].now(),('event:consentagain',))
    body = json.dumps(message(2,'/pedir '+json.dumps(request(MARKER)))).encode()
    assert wiring.webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},body)['statusCode']==200
    assert wiring.webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},body)['statusCode']==200
    assembly['runtime'].ingest(OWNER)
    assert store.db.execute('SELECT count(*) FROM general_inputs').fetchone()[0]==1
    for table in ('general_inputs','general_receipts','general_notifications','task_router_proposals','commands','outbox','blobs'):
        assert MARKER not in repr(store.db.execute('SELECT * FROM '+table).fetchall())
    for path in (store.path,store.path.with_name(store.path.name+'-wal')):
        if path.exists():
            assert MARKER.encode() not in path.read_bytes()


def test_callback_external_private_data_is_encrypted_not_receipt_key(assembly):
    value={'update_id':10,'callback_query':{'id':'synthetic','from':{'id':101},'data':MARKER}}
    assert assembly['wiring'].webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},json.dumps(value).encode())['statusCode']==200
    assert MARKER not in repr(assembly['store'].db.execute('SELECT * FROM general_receipts').fetchall())


@pytest.mark.parametrize('chat',[{'type':'private'},{'type':'private','id':True},{'type':'private','id':'101'}])
def test_malformed_oversize_private_chat_keeps_b_early_ignore(assembly,chat):
    previous=assembly['store'].db.execute('SELECT * FROM ignored').fetchall()
    body=message(20,'x'*4097)
    body['message']['chat']=chat
    response=assembly['wiring'].webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},json.dumps(body).encode())
    assert response['statusCode']==200 and not response.get('body')
    assert not assembly['store'].db.execute('SELECT * FROM general_inputs').fetchall()
    assert not assembly['store'].db.execute('SELECT * FROM general_receipts').fetchall()
    assert assembly['store'].db.execute('SELECT * FROM ignored').fetchall()==previous


def test_crash_before_run_commit_replays_confirmed_callback_no_task_loss(assembly):
    proposal = accept(assembly,request())
    def crash(stage):
        if stage=='general_run_created':
            raise RuntimeError('synthetic crash')
    assembly['store'].failpoint=crash
    with pytest.raises(RuntimeError):
        confirm(assembly,proposal)
    assert not assembly['store'].db.execute('SELECT * FROM general_runs').fetchall()
    assembly['store'].failpoint=lambda stage:None
    confirm(assembly,proposal)
    assembly['runtime'].pump(OWNER,ACTOR)
    assert len(assembly['runtime'].status(OWNER,ACTOR))==1


def test_interpreter_once_crash_recovery_uses_existing_a6_proposal(assembly):
    body = json.dumps(message(1,'/pedir '+json.dumps(request()))).encode()
    assembly['wiring'].webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},body)
    calls=[]
    previous = assembly['runtime'].interpreter
    def parser(**values):
        calls.append(1)
        return previous(**values)
    parser.deterministic=True
    assembly['runtime'].interpreter=parser
    assembly['runtime'].proposal_notice=lambda *values:(_ for _ in ()).throw(RuntimeError('synthetic storage'))
    with pytest.raises(GeneralError):
        assembly['runtime'].ingest(OWNER)
    assert len(calls)==1
    from radar.adapters.local.general_runtime import GeneralRuntime
    assembly['runtime'].proposal_notice=GeneralRuntime.proposal_notice.__get__(assembly['runtime'])
    assembly['runtime'].ingest(OWNER)
    assert len(calls)==1 and assembly['store'].db.execute('SELECT state FROM general_inputs').fetchone()[0]=='done'


def test_crash_read_checkpoint_restart_keeps_task_and_budget(assembly,tmp_path):
    proposal = accept(assembly,request())
    confirm(assembly,proposal)
    def crash(stage):
        if stage=='general_step_checkpoint':
            raise RuntimeError('synthetic crash')
    assembly['store'].failpoint=crash
    with pytest.raises(RuntimeError):
        assembly['runtime'].pump(OWNER,ACTOR)
    assert len(assembly['transport'].calls)==1
    assert assembly['store'].db.execute('SELECT calls FROM general_runs').fetchone()[0]==1
    assembly['store'].failpoint=lambda stage:None
    # In-memory fixture vault retained explicitly; real durable age tested separately.
    path=assembly['store'].path
    assembly['store'].close()
    resumed=build(path,contents=assembly['contents'],clock=assembly['clock'])
    try:
        resumed['runtime'].pump(OWNER,ACTOR)
        assert len(resumed['transport'].calls)==1
        assert resumed['store'].db.execute('SELECT calls FROM general_runs').fetchone()[0]==3
        assert resumed['runtime'].status(OWNER,ACTOR)[0][2]=='partial'
    finally:
        resumed['store'].close()


@pytest.mark.parametrize('change',['consent','revoke','capability','cancel','expiry'])
def test_current_authority_before_read_or_replay(assembly,change):
    proposal=accept(assembly,request())
    confirmed=confirm(assembly,proposal)
    store=assembly['store']
    if change=='consent':
        assembly['directory'].withdraw_consent(owner_ref=OWNER,actor_ref=ACTOR)
    elif change=='revoke':
        store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
    elif change=='capability':
        store.allow_source(owner_ref=OWNER,source_ref='cap:search',authorized=False)
    elif change=='cancel':
        button=next(b.callback_ref for b in confirmed.buttons if b.text=='Cancelar')
        assembly['runtime'].callback(OWNER,ACTOR,button,event_ref='event:cancel')
    else:
        assembly['clock'].current+=timedelta(hours=2)
    try:
        assembly['runtime'].pump(OWNER,ACTOR)
    except (ConditionalConflict,GeneralError):
        pass
    assert not assembly['transport'].calls and not assembly['dispatcher'].deliveries


def test_correction_invalidates_run_and_requires_new_confirmation(assembly):
    initial=accept(assembly,request())
    confirmed=confirm(assembly,initial)
    correction=next(b.callback_ref for b in confirmed.buttons if b.text=='Corregir')
    changed=assembly['runtime'].callback(OWNER,ACTOR,correction,event_ref='event:correct')
    authority=assembly['runtime'].authority(OWNER,ACTOR)
    fixed=assembly['runtime'].router.correct(authority=authority,task_ref=changed.task_ref,expected_version=changed.version,document=request('New topic'),now=assembly['clock'].now())
    confirm(assembly,fixed)
    # Run identity needs proposal version, not only task_ref; no stale run reused.
    assert assembly['store'].db.execute("SELECT count(*) FROM general_runs WHERE status='cancelled'").fetchone()[0]==1
    assert assembly['store'].db.execute('SELECT count(*) FROM general_runs').fetchone()[0]==2
    assembly['runtime'].pump(OWNER,ACTOR)
    assert len(assembly['transport'].calls)==1


def test_two_ingestors_reserve_once_and_do_not_mark_active_parser_uncertain(assembly):
    body=json.dumps(message(1,'/pedir '+json.dumps(request()))).encode()
    assembly['wiring'].webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},body)
    entered,release=Event(),Event()
    original=assembly['runtime'].interpreter
    calls,errors=[],[]
    def interpreter(**kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return original(**kwargs)
    interpreter.deterministic=True
    assembly['runtime'].interpreter=interpreter
    def first():
        try:
            assembly['runtime'].ingest(OWNER)
        except Exception as error:
            errors.append(error)
    worker=Thread(target=first)
    worker.start()
    assert entered.wait(5)
    assembly['runtime'].ingest(OWNER)
    assert assembly['store'].db.execute('SELECT state FROM general_inputs').fetchone()[0]=='interpreting'
    release.set()
    worker.join(5)
    assert not worker.is_alive() and not errors and calls==[1]
    assert assembly['store'].db.execute('SELECT state FROM general_inputs').fetchone()[0]=='done'


def test_expired_worker_neither_checkpoints_nor_clobbers_successor(assembly):
    proposal=accept(assembly,request(steps=[{'step_id':'read','operation':'read','arguments':{'source_refs':['source:articles']}}]))
    confirm(assembly,proposal)
    runtime=assembly['runtime']
    def successor(**kwargs):
        return {'winner':'successor'},'succeeded',None
    def stale(**kwargs):
        # Another runner cannot reclaim a live lease.
        assert runtime.pump(OWNER,ACTOR)==0
        assembly['clock'].current+=timedelta(seconds=61)
        runtime.handlers['read']=successor
        assert runtime.pump(OWNER,ACTOR)==1
        return {'winner':'stale'},'succeeded',None
    runtime.handlers['read']=stale
    runtime.pump(OWNER,ACTOR)
    status=runtime.status(OWNER,ACTOR)[0]
    assert status[2]=='succeeded' and status[3]==2
    assert runtime.outputs(OWNER,status[0])['read']=={'winner':'successor'}


@pytest.mark.parametrize('returned_state',['uncertain','send_uncertain','completed','',None,{'state':'succeeded'}])
def test_handler_unknown_state_is_blocked_never_success_and_not_replayed(assembly,returned_state):
    runtime=assembly['runtime']
    calls=[]
    def handler(**kwargs):
        calls.append(1)
        return {'must_not_checkpoint':MARKER},returned_state,None
    runtime.handlers['compose']=handler
    proposal=accept(assembly,request(steps=[{'step_id':'draft','operation':'compose','arguments':{}}]))
    confirm(assembly,proposal)
    assert runtime.pump(OWNER,ACTOR)==1
    status=runtime.status(OWNER,ACTOR)[0]
    assert status[2]=='blocked'
    state,reason=assembly['store'].db.execute('SELECT state,reason FROM general_steps').fetchone()
    assert (state,reason)==('blocked','handler_state_invalid')
    assert runtime.pump(OWNER,ACTOR)==0 and calls==[1]
    assert not assembly['dispatcher'].deliveries


@pytest.mark.parametrize('state',['succeeded','partial','pending','awaiting_approval','blocked'])
def test_documented_handler_states_preserve_reservation_and_private_checkpoint(assembly,state):
    runtime=assembly['runtime']
    runtime.handlers['inform']=lambda **kwargs:({'fixture_ref':'fixture:allowed'},state,'fixture_result')
    proposal=accept(assembly,request(steps=[{'step_id':'report','operation':'inform','arguments':{}}]))
    confirm(assembly,proposal)
    assert runtime.pump(OWNER,ACTOR)==1
    actual,pointer,reason=assembly['store'].db.execute('SELECT state,pointer,reason FROM general_steps').fetchone()
    assert (actual,reason)==(state,'fixture_result')
    assert runtime.open(OWNER,pointer)=={'fixture_ref':'fixture:allowed'}
    assert assembly['store'].db.execute('SELECT calls FROM general_runs').fetchone()[0]==1


@pytest.mark.parametrize('diagnostic',[MARKER,'email@example.org','PRIVATE '+MARKER,'x'*65,{'message':MARKER}])
def test_external_reason_cannot_enter_control_rows(assembly,diagnostic):
    runtime=assembly['runtime']
    runtime.handlers['read']=lambda **kwargs:({},'succeeded',diagnostic)
    proposal=accept(assembly,request(steps=[{'step_id':'read','operation':'read','arguments':{'source_refs':['source:articles']}}]))
    confirm(assembly,proposal)
    runtime.pump(OWNER,ACTOR)
    assert runtime.status(OWNER,ACTOR)[0][2]=='blocked'
    assert assembly['store'].db.execute('SELECT reason FROM general_steps').fetchone()[0]=='handler_reason_invalid'
    assert MARKER not in repr(assembly['store'].db.execute('SELECT * FROM general_steps').fetchall())


@pytest.mark.parametrize('where',['interpreter','handler'])
def test_external_general_error_is_not_persisted_as_control_reason(assembly,where):
    runtime=assembly['runtime']
    def poisoned(**kwargs):
        raise GeneralError(MARKER)
    poisoned.deterministic=True
    if where=='interpreter':
        runtime.interpreter=poisoned
        body=json.dumps(message(1,'/pedir '+json.dumps(request()))).encode()
        assembly['wiring'].webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':SECRET},body)
        runtime.ingest(OWNER)
        assert assembly['store'].db.execute('SELECT reason FROM general_inputs').fetchone()[0]=='handler_reason_invalid'
    else:
        runtime.handlers['inform']=poisoned
        proposal=accept(assembly,request(steps=[{'step_id':'report','operation':'inform','arguments':{}}]))
        confirm(assembly,proposal)
        runtime.pump(OWNER,ACTOR)
        assert runtime.status(OWNER,ACTOR)[0][5]=='handler_reason_invalid'
    assert MARKER not in repr(assembly['store'].db.execute('SELECT * FROM general_runs').fetchall())
    assert MARKER not in repr(assembly['store'].db.execute('SELECT * FROM general_inputs').fetchall())


def test_unknown_effect_state_checkpoint_crash_recovers_uncertain_without_retry(assembly):
    runtime=assembly['runtime']
    calls=[]
    def handler(**kwargs):
        calls.append(1)
        return {'contextual_ref':'contextual:fixture'},'uncertain','model_outcome_uncertain'
    runtime.handlers['compose']=handler
    proposal=accept(assembly,request(steps=[{'step_id':'draft','operation':'compose','arguments':{}}]))
    confirm(assembly,proposal)
    def crash(stage):
        if stage=='general_step_checkpoint':
            raise RuntimeError('synthetic crash after result validation')
    assembly['store'].failpoint=crash
    with pytest.raises(RuntimeError):
        runtime.pump(OWNER,ACTOR)
    assembly['store'].failpoint=lambda stage:None
    runtime.pump(OWNER,ACTOR)
    assert runtime.status(OWNER,ACTOR)[0][2]=='blocked'
    assert assembly['store'].db.execute('SELECT reason FROM general_steps').fetchone()[0]=='step_outcome_uncertain'
    assert calls==[1] and not assembly['dispatcher'].deliveries


def test_existing_unknown_checkpoint_never_becomes_success_on_replay(assembly):
    runtime=assembly['runtime']
    proposal=accept(assembly,request(steps=[{'step_id':'draft','operation':'compose','arguments':{}}]))
    confirm(assembly,proposal)
    assembly['store'].db.execute("UPDATE general_steps SET state='uncertain'")
    assert runtime.pump(OWNER,ACTOR)==0
    assert runtime.status(OWNER,ACTOR)[0][2]=='blocked'
    assert assembly['store'].db.execute('SELECT reason FROM general_steps').fetchone()[0]=='step_state_invalid'


def test_invalid_state_rolls_back_checkpoint_and_cannot_authorize_draft_effect(assembly):
    runtime=assembly['runtime']
    pointer=runtime.vault.seal(owner_ref=OWNER,plaintext=json.dumps({'account_ref':'account:fixture','drafts':[{'chat_ref':'chat:fixture','text':MARKER,'purpose_ref':'purpose:invalidstate'}]}).encode())
    proposal=accept(assembly,request(steps=[{'step_id':'draft','operation':'compose','arguments':{'private_ref':asdict(pointer)}}]))
    confirm(assembly,proposal)
    calls=[]
    original=runtime.execute
    def wrapped(authority,run,task,step,request,outputs,attempt):
        result,state,reason=original(authority,run,task,step,request,outputs,attempt)
        calls.append(result)
        return result,'uncertain','model_outcome_uncertain'
    runtime.execute=wrapped
    runtime.pump(OWNER,ACTOR)
    assert runtime.status(OWNER,ACTOR)[0][2]=='blocked'
    assert assembly['store'].db.execute('SELECT calls FROM general_runs').fetchone()[0]==1
    assert runtime.outputs(OWNER,runtime.status(OWNER,ACTOR)[0][0])=={}
    with pytest.raises(GeneralError,match='run_inactive_or_expired'):
        runtime.callback(OWNER,ACTOR,calls[0]['approval_ref'],event_ref='event:invalidapprove')
    with pytest.raises(GeneralError,match='run_inactive_or_expired'):
        runtime.dispatch_fixture(OWNER,ACTOR,calls[0]['batch_ref'])
    assert not assembly['dispatcher'].deliveries


def test_approval_crash_after_a10_commit_replays_without_second_message(assembly):
    runtime=assembly['runtime']
    pointer=runtime.vault.seal(owner_ref=OWNER,plaintext=json.dumps({'account_ref':'account:fixture','drafts':[{'chat_ref':'chat:fixture','text':MARKER,'purpose_ref':'purpose:reply'}]}).encode())
    proposal=accept(assembly,request(steps=[{'step_id':'draft','operation':'compose','arguments':{'private_ref':asdict(pointer)}}]))
    confirm(assembly,proposal)
    runtime.pump(OWNER,ACTOR)
    output=runtime.outputs(OWNER,runtime.status(OWNER,ACTOR)[0][0])['draft']
    class CrashAfterApproval:
        def __init__(self):
            self.contacts=self
            self.crash=True
        def note_outbound(self,**kwargs):
            if self.crash:
                raise RuntimeError('synthetic crash after A10 approval')
        def export(self,**kwargs):
            return None
    contact=CrashAfterApproval()
    runtime.contacts=contact
    assembly['store'].db.execute('INSERT INTO general_contact_links VALUES(?,?,?,?)',(OWNER,output['batch_ref'],'contact:fixture',output['operation_ids'][0]))
    with pytest.raises(RuntimeError):
        runtime.callback(OWNER,ACTOR,output['approval_ref'],event_ref='event:approvalcrash')
    assert not assembly['dispatcher'].deliveries
    contact.crash=False
    runtime.callback(OWNER,ACTOR,output['approval_ref'],event_ref='event:approvalcrash')
    runtime.dispatch_fixture(OWNER,ACTOR,output['batch_ref'])
    runtime.dispatch_fixture(OWNER,ACTOR,output['batch_ref'])
    assert len(assembly['dispatcher'].deliveries)==1


def test_notice_claim_crash_never_blindly_resends(assembly):
    runtime=assembly['runtime']
    runtime.notify(OWNER,ACTOR,{'text':MARKER},key='event:noticecrash')
    def crash(stage):
        if stage=='general_notice_claimed':
            raise RuntimeError('synthetic crash')
    assembly['store'].failpoint=crash
    with pytest.raises(RuntimeError):
        runtime.deliver(OWNER,ACTOR)
    assembly['store'].failpoint=lambda stage:None
    assert runtime.deliver(OWNER,ACTOR)==0
    assert not assembly['notices']
    assert assembly['store'].db.execute('SELECT state FROM general_notifications').fetchone()[0]=='dispatch_committed'


def test_wrong_owner_never_opens_or_lists_private_task(assembly):
    proposal=accept(assembly,request())
    with pytest.raises((GeneralError,ConditionalConflict)):
        assembly['runtime'].callback('owner:foreign',ACTOR,proposal.buttons[0].callback_ref,event_ref='event:wrong')
    assert not assembly['store'].db.execute('SELECT * FROM general_runs').fetchall()


def test_missing_query_classification_blocks_before_source_io(assembly):
    proposal=accept(assembly,request(MARKER))
    confirm(assembly,proposal)
    assembly['runtime'].query_policy=None
    assembly['runtime'].pump(OWNER,ACTOR)
    assert not assembly['transport'].calls
    assert assembly['runtime'].status(OWNER,ACTOR)[0][2]=='blocked'
    assert assembly['store'].db.execute("SELECT reason FROM general_steps WHERE id='find'").fetchone()[0]=='query_privacy_unclassified'


def test_no_infinite_retries_or_hidden_source_fanout(assembly):
    doc=request()
    doc['steps'][0]['arguments']['source_refs']=['source:articles','source:events']
    proposal=accept(assembly,doc)
    confirm(assembly,proposal)
    assembly['runtime'].pump(OWNER,ACTOR)
    assert not assembly['transport'].calls and assembly['runtime'].status(OWNER,ACTOR)[0][2]=='blocked'


def test_exact_contact_draft_approval_fixture_dispatch_not_a6_confirmation(assembly):
    view=assembly['views']['worker:task-router']
    pointer=view.seal(owner_ref=OWNER,plaintext=json.dumps({'account_ref':'account:fixture','drafts':[{'chat_ref':'chat:fixture','text':MARKER,'purpose_ref':'purpose:reply'}]}).encode())
    doc=request('Reply explicitly',steps=[{'step_id':'draft','operation':'compose','arguments':{'private_ref':asdict(pointer)}}])
    proposal=accept(assembly,doc)
    confirm(assembly,proposal)
    assembly['runtime'].pump(OWNER,ACTOR)
    output=assembly['runtime'].outputs(OWNER,assembly['runtime'].status(OWNER,ACTOR)[0][0])['draft']
    assert assembly['store'].get(owner_ref=OWNER,operation_id=output['operation_ids'][0]).state==LedgerState.PROPOSED
    assert not assembly['dispatcher'].deliveries
    assembly['runtime'].callback(OWNER,ACTOR,output['approval_ref'],event_ref='event:exactapprove')
    assembly['runtime'].dispatch_fixture(OWNER,ACTOR,output['batch_ref'])
    assert [d.text for d in assembly['dispatcher'].deliveries]==[MARKER]
    assembly['runtime'].dispatch_fixture(OWNER,ACTOR,output['batch_ref'])
    assert len(assembly['dispatcher'].deliveries)==1


def test_unseen_or_partial_screen_cannot_approve(assembly):
    assembly['runtime'].delivery=None
    view=assembly['views']['worker:task-router']
    pointer=view.seal(owner_ref=OWNER,plaintext=json.dumps({'account_ref':'account:fixture','drafts':[{'chat_ref':'chat:fixture','text':MARKER,'purpose_ref':'purpose:reply'}]}).encode())
    proposal=accept(assembly,request(steps=[{'step_id':'draft','operation':'compose','arguments':{'private_ref':asdict(pointer)}}]))
    confirm(assembly,proposal)
    assembly['runtime'].pump(OWNER,ACTOR)
    output=assembly['runtime'].outputs(OWNER,assembly['runtime'].status(OWNER,ACTOR)[0][0])['draft']
    with pytest.raises(GeneralError,match='approval_screen_not_delivered'):
        assembly['runtime'].callback(OWNER,ACTOR,output['approval_ref'],event_ref='event:unseen')
    assert not assembly['dispatcher'].deliveries


def test_cancelled_intent_blocks_previously_exact_approved_fixture_batch(assembly):
    runtime=assembly['runtime']
    pointer=runtime.vault.seal(owner_ref=OWNER,plaintext=json.dumps({'account_ref':'account:fixture','drafts':[{'chat_ref':'chat:fixture','text':MARKER,'purpose_ref':'purpose:cancelguard'}]}).encode())
    proposal=accept(assembly,request(steps=[{'step_id':'draft','operation':'compose','arguments':{'private_ref':asdict(pointer)}}]))
    confirmed=confirm(assembly,proposal)
    runtime.pump(OWNER,ACTOR)
    output=runtime.outputs(OWNER,runtime.status(OWNER,ACTOR)[0][0])['draft']
    runtime.callback(OWNER,ACTOR,output['approval_ref'],event_ref='event:approvebeforecancel')
    cancel=next(b.callback_ref for b in confirmed.buttons if b.text=='Cancelar')
    runtime.callback(OWNER,ACTOR,cancel,event_ref='event:cancelafterapprove')
    with pytest.raises(GeneralError,match='run_inactive_or_expired'):
        runtime.dispatch_fixture(OWNER,ACTOR,output['batch_ref'])
    assert not assembly['dispatcher'].deliveries


def test_cli_help_fixture_and_no_real_flags(capsys):
    assert main(['--fixture'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['fixture_only'] and result['source_reads']==1 and result['third_party_sends']==0
    assert main(['--fixture','--authorize-bot-io'])==2
    result=subprocess.run([sys.executable,'-m','radar.entrypoints.general_local','--help'],capture_output=True,text=True)
    assert result.returncode==0 and '--fixture' in result.stdout
