from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest

from radar.adapters.local.actions import SimulatedSender
from radar.adapters.local.sqlite import SQLiteStore
from radar.application.local_flow import approve_action,reconcile_uncertain
from radar.ports.types import Approval,ConditionalConflict,LedgerState
from conftest import ACTOR,AT,OWNER,configure

OP = '00000000-0000-0000-0000-000000000001'
SESSION = 'whatsapp:synthetic'
HASH = 'a'*64


def action(runtime, *, owner=OWNER,actor=ACTOR,op=OP):
    runtime.store.set_session(owner_ref=owner,session_ref=SESSION,version=1)
    record = runtime.store.propose(owner_ref=owner,operation_id=op,content_hash=HASH,recipient_ref='recipient:fixture',purpose='synthetic_review',session_ref=SESSION,session_version=1)
    approval = Approval(actor,'recipient:fixture',SESSION,1,HASH,'synthetic_review',AT+timedelta(minutes=10))
    return record,approval


def approved(runtime, **kwargs):
    owner = kwargs.get('owner',OWNER)
    record,approval = action(runtime,**kwargs)
    return approve_action(runtime.store,owner_ref=owner,operation_id=record.operation_id,expected_version=record.version,approval=approval,now=AT)


def lease(runtime, *, owner=OWNER,worker='worker:one',ttl=timedelta(minutes=1)):
    return runtime.store.acquire(owner_ref=owner,session_ref=SESSION,worker_ref=worker,now=runtime.clock.now(),ttl=ttl)


def crash_at(boundary):
    def fail(stage):
        if boundary==stage:
            raise RuntimeError(stage)
    return fail


@pytest.mark.parametrize('field,value',[('actor_ref','user:foreign'),('recipient_ref','recipient:other'),('session_ref','whatsapp:other'),
                                     ('session_version',2),('content_hash','b'*64),('purpose','different'),('expires_at',AT)])
def test_approval_every_binding_rejected_without_transition(runtime,field,value):
    record,approval = action(runtime)
    with pytest.raises(ConditionalConflict):
        approve_action(runtime.store,owner_ref=OWNER,operation_id=OP,expected_version=0,approval=replace(approval,**{field:value}),now=AT)
    assert runtime.store.get(owner_ref=OWNER,operation_id=OP) == record


@pytest.mark.parametrize('change',['expired_lease','renewed_old_token','replacement','session_version','approval_expired','revoked_actor','withdrawn_consent','cancel','stop'])
def test_current_authority_checked_transactionally_before_claim(runtime,change):
    record = approved(runtime)
    token = lease(runtime)
    if change == 'expired_lease':
        runtime.clock.value += timedelta(minutes=1)
    elif change == 'renewed_old_token':
        assert runtime.store.renew(owner_ref=OWNER,lease=token,now=AT,ttl=timedelta(minutes=2))
    elif change == 'replacement':
        runtime.clock.value += timedelta(minutes=1)
        assert lease(runtime,worker='worker:two')
    elif change == 'session_version':
        runtime.store.set_session(owner_ref=OWNER,session_ref=SESSION,version=2)
    elif change == 'approval_expired':
        runtime.clock.value += timedelta(minutes=11)
        token = lease(runtime)
    elif change == 'revoked_actor':
        runtime.store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
    elif change == 'withdrawn_consent':
        runtime.directory.withdraw_consent(owner_ref=OWNER,actor_ref=ACTOR)
    elif change == 'cancel':
        runtime.store.cancel_action(owner_ref=OWNER,operation_id=OP)
    elif change == 'stop':
        runtime.store.stop(owner_ref=OWNER)
    with pytest.raises(ConditionalConflict):
        runtime.store.claim_dispatch(owner_ref=OWNER,operation_id=OP,expected_version=record.version,lease=token,provider_message_ref='fixture:protocol',now=runtime.clock.now())
    assert runtime.store.get(owner_ref=OWNER,operation_id=OP) == record


def test_concurrent_claims_only_one_succeeds(runtime):
    record = approved(runtime)
    token = lease(runtime)
    stores = [SQLiteStore(runtime.store.path),SQLiteStore(runtime.store.path)]
    def claim(index):
        try:
            stores[index].claim_dispatch(owner_ref=OWNER,operation_id=OP,expected_version=record.version,lease=token,provider_message_ref=f'fixture:protocol{index}',now=AT)
            return True
        except ConditionalConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(claim,range(2))) == [False,True]
    assert runtime.store.get(owner_ref=OWNER,operation_id=OP).state == LedgerState.DISPATCH_COMMITTED
    for store in stores:
        store.close()


@pytest.mark.parametrize('boundary',['after_claim','after_simulated_send','snapshot'])
def test_crash_never_blindly_resends_and_independent_proof_reconciles(runtime,boundary):
    approved(runtime)
    token = lease(runtime)
    sender = SimulatedSender(runtime.store,runtime.clock,test_authorized=True)
    with pytest.raises(RuntimeError):
        sender.send(owner_ref=OWNER,operation_id=OP,lease=token,failpoint=crash_at(boundary))
    assert runtime.store.get(owner_ref=OWNER,operation_id=OP).state == LedgerState.DISPATCH_COMMITTED
    expected_sends = 0 if boundary=='after_claim' else 1
    assert len(sender.proof_refs(owner_ref=OWNER,operation_id=OP)) == expected_sends
    path = runtime.store.path
    runtime.store.close()
    runtime.store = SQLiteStore(path)
    sender = SimulatedSender(runtime.store,runtime.clock,test_authorized=True)
    uncertain = sender.send(owner_ref=OWNER,operation_id=OP,lease=token)
    assert uncertain.state == LedgerState.SEND_UNCERTAIN
    assert sender.send(owner_ref=OWNER,operation_id=OP,lease=token) == uncertain
    assert len(sender.proof_refs(owner_ref=OWNER,operation_id=OP)) == expected_sends
    assert any(i.reason=='send_uncertain' for _,i in runtime.store.pending_alerts(owner_ref=OWNER))
    with pytest.raises(ConditionalConflict):
        reconcile_uncertain(runtime.store,owner_ref=OWNER,operation_id=OP,proof_ref='proof:callerBoolean',now=AT)
    if expected_sends:
        proof = sender.proof_refs(owner_ref=OWNER,operation_id=OP)[0]
        settled = reconcile_uncertain(runtime.store,owner_ref=OWNER,operation_id=OP,proof_ref=proof,now=AT)
        assert settled.state == LedgerState.PROVIDER_CONFIRMED
        assert len(sender.proof_refs(owner_ref=OWNER,operation_id=OP)) == 1


def test_cross_owner_same_operation_and_proof_are_isolated(runtime):
    configure(runtime,owner='owner:beta',actor='user:beta',numeric=202)
    approved(runtime)
    approved(runtime,owner='owner:beta',actor='user:beta')
    sender = SimulatedSender(runtime.store,runtime.clock,test_authorized=True)
    one = lease(runtime)
    with pytest.raises(RuntimeError):
        sender.send(owner_ref=OWNER,operation_id=OP,lease=one,failpoint=crash_at('snapshot'))
    sender.send(owner_ref=OWNER,operation_id=OP,lease=one)
    beta = lease(runtime,owner='owner:beta')
    with pytest.raises(RuntimeError):
        sender.send(owner_ref='owner:beta',operation_id=OP,lease=beta,failpoint=crash_at('after_claim'))
    sender.send(owner_ref='owner:beta',operation_id=OP,lease=beta)
    proof = sender.proof_refs(owner_ref=OWNER,operation_id=OP)[0]
    with pytest.raises(ConditionalConflict):
        reconcile_uncertain(runtime.store,owner_ref='owner:beta',operation_id=OP,proof_ref=proof,now=AT)
    assert runtime.store.get(owner_ref='owner:beta',operation_id=OP).state == LedgerState.SEND_UNCERTAIN
    assert runtime.store.get(owner_ref='owner:unknown',operation_id=OP) is None


def test_expired_lease_cannot_renew_and_release_keeps_epoch(runtime):
    token = lease(runtime)
    assert runtime.store.release(owner_ref=OWNER,lease=token)
    replacement = lease(runtime,worker='worker:two')
    assert replacement.version > token.version
    assert not runtime.store.release(owner_ref=OWNER,lease=token)
    runtime.clock.value += timedelta(minutes=1)
    assert runtime.store.renew(owner_ref=OWNER,lease=replacement,now=runtime.clock.now(),ttl=timedelta(minutes=1)) is None


def test_finish_rollback_and_same_result_converge(runtime):
    approved(runtime)
    token = lease(runtime)
    claimed = runtime.store.claim_dispatch(owner_ref=OWNER,operation_id=OP,expected_version=1,lease=token,provider_message_ref='fixture:protocol',now=AT)
    runtime.store.failpoint = crash_at('after_alert')
    with pytest.raises(RuntimeError):
        runtime.store.finish_local(owner_ref=OWNER,operation_id=OP,expected_version=claimed.version,target=LedgerState.SEND_UNCERTAIN,provider_message_ref=claimed.provider_message_ref,now=AT)
    assert runtime.store.get(owner_ref=OWNER,operation_id=OP) == claimed
    assert not runtime.store.pending_alerts(owner_ref=OWNER)
    runtime.store.failpoint = lambda stage:None
    kwargs = dict(owner_ref=OWNER,operation_id=OP,expected_version=claimed.version,target=LedgerState.SEND_UNCERTAIN,provider_message_ref=claimed.provider_message_ref,now=AT)
    completed = runtime.store.finish_local(**kwargs)
    assert runtime.store.finish_local(**kwargs) == completed


def test_stop_survives_restart_and_no_send_authorization_is_implicit(runtime):
    approved(runtime)
    token = lease(runtime)
    runtime.store.stop(owner_ref=OWNER)
    path=runtime.store.path
    runtime.store.close()
    runtime.store=SQLiteStore(path)
    with pytest.raises(ConditionalConflict):
        SimulatedSender(runtime.store,runtime.clock,test_authorized=True).send(owner_ref=OWNER,operation_id=OP,lease=token)
    with pytest.raises(ValueError):
        SimulatedSender(runtime.store,runtime.clock)


@pytest.mark.parametrize('method',['approve_action','record_result','transition'])
def test_unimplemented_wire_paths_fail_without_mutation(runtime,method):
    record,approval=action(runtime)
    common=dict(owner_ref=OWNER,operation_id=OP,expected_version=0,outbox=None,now=AT)
    if method=='approve_action':
        common['approval']=approval
    else:
        common.update(target=LedgerState.PROVIDER_CONFIRMED,provider_message_ref='fixture:fake')
    if method=='transition':
        common['expected_state']=LedgerState.DISPATCH_COMMITTED
    with pytest.raises(NotImplementedError):
        getattr(runtime.store,method)(**common)
    assert runtime.store.get(owner_ref=OWNER,operation_id=OP)==record
    assert not runtime.store.pending(owner_ref=OWNER,limit=10).entries


def test_collaborator_cannot_approve_and_role_demotion_blocks_claim(runtime):
    runtime.directory.enroll_synthetic(202,owner_ref=OWNER,actor_ref='user:collab',role='collaborator')
    from radar.adapters.local.telegram import consent
    runtime.directory.accept_consent('user:collab',consent.CONSENT_VERSION,AT,('collab-consent',))
    record,approval=action(runtime)
    with pytest.raises(ConditionalConflict):
        approve_action(runtime.store,owner_ref=OWNER,operation_id=OP,expected_version=0,approval=replace(approval,actor_ref='user:collab'),now=AT)
    record=approve_action(runtime.store,owner_ref=OWNER,operation_id=OP,expected_version=0,approval=approval,now=AT)
    token=lease(runtime)
    runtime.store.db.execute('UPDATE directory SET role=\'collaborator\' WHERE owner=? AND actor=?',(OWNER,ACTOR))
    with pytest.raises(ConditionalConflict):
        runtime.store.claim_dispatch(owner_ref=OWNER,operation_id=OP,expected_version=record.version,lease=token,provider_message_ref='fixture:protocol',now=AT)


@pytest.mark.parametrize('change',['stop','cancel','revoke','consent','expiry','session','role'])
def test_authority_changed_after_claim_prevents_synthetic_effect(runtime,change):
    approved(runtime)
    token=lease(runtime)
    sender=SimulatedSender(runtime.store,runtime.clock,test_authorized=True)
    def change_authority(stage):
        if stage!='after_claim':
            return
        if change=='stop':
            runtime.store.stop(owner_ref=OWNER)
        elif change=='cancel':
            runtime.store.cancel_action(owner_ref=OWNER,operation_id=OP)
        elif change=='revoke':
            runtime.store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
        elif change=='consent':
            runtime.directory.withdraw_consent(owner_ref=OWNER,actor_ref=ACTOR)
        elif change=='expiry':
            runtime.clock.value+=timedelta(minutes=11)
        elif change=='session':
            runtime.store.set_session(owner_ref=OWNER,session_ref=SESSION,version=2)
        elif change=='role':
            runtime.store.db.execute('UPDATE directory SET role=\'collaborator\' WHERE owner=? AND actor=?',(OWNER,ACTOR))
    uncertain=sender.send(owner_ref=OWNER,operation_id=OP,lease=token,failpoint=change_authority)
    assert uncertain.state==LedgerState.SEND_UNCERTAIN
    assert not sender.proof_refs(owner_ref=OWNER,operation_id=OP)
    assert sender.send(owner_ref=OWNER,operation_id=OP,lease=token)==uncertain
