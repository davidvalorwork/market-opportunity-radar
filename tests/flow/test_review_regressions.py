from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from datetime import timedelta
from threading import Event
from uuid import uuid4

from jsonschema import ValidationError
import pytest

from radar.adapters.local.queue import FakeQueue
from radar.adapters.local.telegram import consent
from radar.adapters.local.wire import validate_envelope
from radar.ports.types import ConditionalConflict, OutboxEntry
from radar.adapters.local.runtime import FixedClock, LocalRuntime
from radar.adapters.local.ui import deliver_alerts
from conftest import ACTOR, AT, FIXTURES, OWNER, configure, update
from test_envelope_versions import command_envelope


def queued_worker(runtime):
    assert update(runtime)['statusCode']==200
    op=runtime.bridge.last_accepted.operation_id
    runtime.relay(OWNER)
    runtime.process_commands(OWNER,search_ref='search:perfume')
    runtime.relay(OWNER)
    return op


def test_revoked_actor_cannot_reactivate_by_accepting_consent(runtime):
    runtime.store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
    assert not runtime.directory.accept_consent(ACTOR,consent.CONSENT_VERSION,AT,('revoked-consent',))
    assert not runtime.store.authorize_actor(owner_ref=OWNER,actor_ref=ACTOR)
    assert runtime.store.db.execute('SELECT 1 FROM ignored WHERE ref=?',('revoked-consent',)).fetchone() is None


def test_revoked_actor_is_absent_from_directory(runtime):
    runtime.store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
    assert runtime.directory.get(101) is None


def test_revoked_webhook_update_returns_neutral_200_without_new_command(runtime):
    runtime.store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
    assert update(runtime)['statusCode']==200
    assert runtime.bridge.last_accepted is None
    assert not runtime.store.pending(owner_ref=OWNER,limit=10).entries


@pytest.mark.parametrize('cause',['cancel','stop','consent','revoke','deadline'])
def test_known_permanent_worker_rejection_is_durable_before_ack(runtime,cause):
    op=queued_worker(runtime)
    if cause=='cancel':
        runtime.store.cancel(owner_ref=OWNER,operation_id=op)
    elif cause=='stop':
        runtime.store.stop(owner_ref=OWNER)
    elif cause=='consent':
        runtime.directory.withdraw_consent(owner_ref=OWNER,actor_ref=ACTOR)
    elif cause=='revoke':
        runtime.store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
    else:
        runtime.clock.value+=timedelta(minutes=6)
    assert runtime.process_workers(OWNER)==1
    assert runtime.worker.executions==0
    assert runtime.store.db.execute('SELECT count(*) FROM queue WHERE owner=? AND destination=? AND acked=0',(OWNER,'browser.fifo')).fetchone()[0]==0
    rows=runtime.store.db.execute('SELECT owner,msg,op,code FROM quarantine').fetchall()
    assert len(rows)==1 and rows[0][0]==OWNER and rows[0][2]==op
    runtime.queue.replay(owner_ref=OWNER,destination='browser.fifo')
    assert runtime.process_workers(OWNER)==0


@pytest.mark.parametrize('cause',['consent','revoke','deadline'])
def test_known_permanent_command_rejection_is_durable_before_ack(runtime,cause):
    update(runtime)
    op=runtime.bridge.last_accepted.operation_id
    runtime.relay(OWNER)
    if cause=='consent':
        runtime.directory.withdraw_consent(owner_ref=OWNER,actor_ref=ACTOR)
    elif cause=='revoke':
        runtime.store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
    else:
        runtime.clock.value+=timedelta(minutes=6)
    assert runtime.process_commands(OWNER,search_ref='search:perfume')==1
    assert runtime.store.db.execute('SELECT count(*) FROM queue WHERE owner=? AND acked=0',(OWNER,)).fetchone()[0]==0
    assert runtime.store.db.execute('SELECT op FROM quarantine WHERE owner=?',(OWNER,)).fetchone()[0]==op


def test_result_of_cancelled_run_is_parked_and_later_delivery_can_finish(runtime):
    op=queued_worker(runtime)
    runtime.process_workers(OWNER)
    runtime.relay(OWNER)
    runtime.store.cancel(owner_ref=OWNER,operation_id=op)
    assert runtime.process_results(OWNER)==1
    assert runtime.store.db.execute('SELECT count(*) FROM quarantine WHERE owner=?',(OWNER,)).fetchone()[0]==1
    update(runtime,update_id=2)
    following=runtime.bridge.last_accepted.operation_id
    runtime.pump(OWNER,search_ref='search:perfume')
    assert runtime.store.reports(owner_ref=OWNER,operation_id=following)
    assert runtime.store.db.execute('SELECT count(*) FROM queue WHERE owner=? AND acked=0',(OWNER,)).fetchone()[0]==0


def test_unsupported_command_is_parked_instead_of_repeated(runtime):
    update(runtime,command='/resumen')
    runtime.pump(OWNER,search_ref='search:perfume')
    assert runtime.store.db.execute('SELECT count(*) FROM queue WHERE owner=? AND acked=0',(OWNER,)).fetchone()[0]==0
    assert runtime.store.db.execute('SELECT code FROM quarantine WHERE owner=?',(OWNER,)).fetchone()[0]=='unsupported'


def test_queue_capacity_is_scoped_to_owner(runtime):
    configure(runtime,owner='owner:beta',actor='user:beta',numeric=202)
    queue=FakeQueue(runtime.store,runtime.clock,capacity=1)
    first=command_envelope(2)
    other=dict(command_envelope(2),owner_ref='owner:beta')
    other['payload']=dict(other['payload'],telegram_user_ref='user:beta')
    queue.publish(owner_ref=OWNER,entry=OutboxEntry(first,'commands.fifo'))
    queue.publish(owner_ref='owner:beta',entry=OutboxEntry(other,'commands.fifo'))
    assert len(queue.receive(owner_ref='owner:beta',destination='commands.fifo',limit=1))==1


def test_visible_queue_timestamp_crossing_zero_microsecond_is_correct(runtime):
    envelope=command_envelope(2)
    runtime.queue.publish(owner_ref=OWNER,entry=OutboxEntry(envelope,'commands.fifo'))
    runtime.clock.value+=timedelta(microseconds=1)
    assert len(runtime.queue.receive(owner_ref=OWNER,destination='commands.fifo',limit=1))==1


def test_shared_connection_serializes_concurrent_transactions(runtime):
    entered, release, second_attempt=Event(),Event(),Event()
    def first():
        with runtime.store.transaction():
            entered.set()
            assert release.wait(2)
    def second():
        second_attempt.set()
        with runtime.store.transaction():
            return 'committed'
    with ThreadPoolExecutor(max_workers=2) as executor:
        one=executor.submit(first)
        assert entered.wait(2)
        two=executor.submit(second)
        assert second_attempt.wait(2)
        try:
            with pytest.raises(TimeoutError):
                two.result(timeout=0.05)
        finally:
            release.set()
        one.result()
        assert two.result()=='committed'


def test_missing_envelope_version_is_invalid_input():
    envelope=command_envelope(2)
    envelope.pop('schema_version')
    with pytest.raises(ValidationError) as caught:
        validate_envelope(envelope)
    assert caught.value.message=='invalid_input'


def test_reader_cannot_observe_another_threads_rolled_back_write(runtime):
    entered, release, reading = Event(), Event(), Event()
    def writer():
        with pytest.raises(RuntimeError, match='rollback_probe'):
            with runtime.store.transaction():
                runtime.store.db.execute('UPDATE actors SET enabled=0 WHERE owner=? AND actor=?',(OWNER,ACTOR))
                entered.set()
                assert release.wait(2)
                raise RuntimeError('rollback_probe')
    def reader():
        reading.set()
        return runtime.store.authorize_actor(owner_ref=OWNER,actor_ref=ACTOR)
    with ThreadPoolExecutor(max_workers=2) as executor:
        one=executor.submit(writer)
        assert entered.wait(2)
        two=executor.submit(reader)
        assert reading.wait(2)
        try:
            with pytest.raises(TimeoutError):
                two.result(timeout=0.05)
        finally:
            release.set()
        one.result()
        assert two.result() is True


def test_cancel_does_not_retrogress_terminal_success(runtime):
    update(runtime)
    op=runtime.bridge.last_accepted.operation_id
    runtime.pump(OWNER,search_ref='search:perfume')
    before=runtime.store.run(owner_ref=OWNER,operation_id=op)
    assert before.status=='succeeded'
    runtime.store.cancel(owner_ref=OWNER,operation_id=op)
    assert runtime.store.run(owner_ref=OWNER,operation_id=op)==before


@pytest.mark.parametrize('failure',['before_quarantine_commit','after_quarantine'])
def test_quarantine_crash_restart_redelivery_is_idempotent(runtime,failure):
    op=queued_worker(runtime)
    runtime.store.cancel(owner_ref=OWNER,operation_id=op)
    def crash(stage):
        if stage==failure:
            raise RuntimeError('checkpoint_crash')
    if failure=='before_quarantine_commit':
        runtime.store.failpoint=crash
    with pytest.raises(RuntimeError,match='checkpoint_crash'):
        runtime.process_workers(OWNER,failpoint=crash)
    expected=0 if failure=='before_quarantine_commit' else 1
    assert runtime.store.db.execute('SELECT count(*) FROM quarantine').fetchone()[0]==expected
    assert runtime.store.db.execute('SELECT count(*) FROM queue WHERE destination=? AND acked=0',('browser.fifo',)).fetchone()[0]==1
    path=runtime.store.path
    runtime.store.close()
    resumed=LocalRuntime(path,fixtures=FIXTURES,synthetic_authorized=True,clock=FixedClock(AT))
    try:
        resumed.queue.replay(owner_ref=OWNER,destination='browser.fifo')
        assert resumed.process_workers(OWNER)==1
        assert resumed.store.db.execute('SELECT count(*) FROM quarantine').fetchone()[0]==1
        assert resumed.store.db.execute('SELECT count(*) FROM queue WHERE acked=0').fetchone()[0]==0
        assert resumed.worker.executions==0
    finally:
        resumed.store.close()


@pytest.mark.parametrize('error',[ConditionalConflict('transient_lease_busy'),RuntimeError('injected_crash'),OSError('disk_unavailable')])
def test_unknown_failures_are_not_quarantined_or_acked(runtime,monkeypatch,error):
    queued_worker(runtime)
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(runtime.worker,'execute',fail)
    with pytest.raises(type(error)):
        runtime.process_workers(OWNER)
    assert runtime.store.db.execute('SELECT count(*) FROM quarantine').fetchone()[0]==0
    assert runtime.store.db.execute('SELECT count(*) FROM queue WHERE destination=? AND acked=0',('browser.fifo',)).fetchone()[0]==1


@pytest.mark.parametrize('mismatch',['owner','operation','message','content','token'])
def test_quarantine_rejects_foreign_or_changed_delivery_without_ack(runtime,mismatch):
    queued_worker(runtime)
    delivery=runtime.queue.receive(owner_ref=OWNER,destination='browser.fifo',limit=1)[0]
    owner=OWNER
    envelope=dict(delivery.envelope)
    if mismatch=='owner':
        owner='owner:beta'
    elif mismatch=='operation':
        envelope['operation_id']=str(uuid4())
    elif mismatch=='message':
        envelope['message_id']=str(uuid4())
    elif mismatch=='content':
        envelope['payload']=dict(envelope['payload'],batch=1 if envelope['payload']['batch']!=1 else 2)
    else:
        delivery=replace(delivery,acknowledgement_ref='wrong-token')
    delivery=replace(delivery,envelope=envelope)
    validate_envelope(envelope)
    with pytest.raises(ConditionalConflict):
        runtime.queue.quarantine(owner_ref=owner,delivery=delivery,code='unsupported')
    assert runtime.store.db.execute('SELECT count(*) FROM quarantine').fetchone()[0]==0
    assert runtime.store.db.execute('SELECT count(*) FROM queue WHERE destination=? AND acked=0',('browser.fifo',)).fetchone()[0]==1


def test_whole_second_legacy_visibility_remains_readable(runtime):
    runtime.queue.publish(owner_ref=OWNER,entry=OutboxEntry(command_envelope(2),'commands.fifo'))
    runtime.store.db.execute('UPDATE queue SET visible=?',(AT.strftime('%Y-%m-%dT%H:%M:%SZ'),))
    runtime.clock.value+=timedelta(microseconds=1)
    assert len(runtime.queue.receive(owner_ref=OWNER,destination='commands.fifo',limit=1))==1


def test_revoked_alert_recipient_is_rejected_without_delivery(runtime):
    queued_worker(runtime)
    runtime.process_workers(OWNER)
    runtime.relay(OWNER)
    runtime.process_results(OWNER)
    intent=runtime.store.pending_alerts(owner_ref=OWNER)[0][1]
    runtime.store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
    with pytest.raises(ConditionalConflict,match='ui_directory_binding'):
        runtime.ui.deliver(owner_ref=OWNER,intent=intent)
    assert not runtime.ui.messages(owner_ref=OWNER)


def test_revoked_alerts_do_not_starve_authorized_recipient_or_next_run(runtime):
    queued_worker(runtime)
    runtime.process_workers(OWNER)
    runtime.relay(OWNER)
    runtime.process_results(OWNER)
    intent=runtime.store.pending_alerts(owner_ref=OWNER)[0][1]
    # Exercise more than one indexed page, using a real evidence-derived report.
    for index in range(11):
        runtime.store._alert(OWNER,replace(intent,intent_ref=f'denied:{index}'))
    configure(runtime,owner=OWNER,actor='user:beta',numeric=202)
    authorized=replace(intent,intent_ref='authorized:beta',actor_ref='user:beta')
    runtime.store._alert(OWNER,authorized)
    runtime.store.revoke(owner_ref=OWNER,actor_ref=ACTOR)
    denied=deliver_alerts(runtime.store,runtime.ui,owner_ref=OWNER)
    assert len(denied)==12
    assert all(code=='ui_directory_binding' for ref,code in denied)
    assert runtime.store.db.execute('SELECT count(*) FROM alerts WHERE owner=? AND delivered=0',(OWNER,)).fetchone()[0]==12
    assert len(runtime.ui.messages(owner_ref=OWNER))==1
    update(runtime,numeric=202,update_id=2)
    operation=runtime.bridge.last_accepted.operation_id
    runtime.pump(OWNER,search_ref='search:perfume')
    assert runtime.store.run(owner_ref=OWNER,operation_id=operation).status=='succeeded'
    assert len(runtime.ui.messages(owner_ref=OWNER))==2


@pytest.mark.parametrize('error',[ConditionalConflict('unknown_ui_conflict'),RuntimeError('ui_crash'),OSError('disk_failure')])
def test_alert_unknown_failures_remain_visible_without_mark(runtime,monkeypatch,error):
    queued_worker(runtime)
    runtime.process_workers(OWNER)
    runtime.relay(OWNER)
    runtime.process_results(OWNER)
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(runtime.ui,'deliver',fail)
    with pytest.raises(type(error)):
        deliver_alerts(runtime.store,runtime.ui,owner_ref=OWNER)
    assert len(runtime.store.pending_alerts(owner_ref=OWNER))==1
