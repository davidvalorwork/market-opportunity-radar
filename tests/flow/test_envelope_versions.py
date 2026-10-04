from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from jsonschema import ValidationError
import pytest

from radar.adapters.local.codec import loads
from radar.adapters.local.runtime import LocalRuntime
from radar.adapters.local.sqlite import SQLiteStore, digest, stamp
from radar.adapters.local.wire import validate_envelope
from radar.adapters.local.worker import decode_result
from radar.application.local_flow import handle_worker_result
from radar.ports.types import ConditionalConflict, OutboxEntry
from conftest import ACTOR, AT, FIXTURES, OWNER, update


def command_envelope(version, *, update_id=77, callback=None):
    payload = {'schema_version':1,'update_id':update_id,'telegram_user_ref':ACTOR,
               'command':'buscar','args':{'query':'fixture'}}
    if callback is not None:
        payload.update(command='callback',args={},callback_ref=callback)
    return {'schema_version':version,'message_id':str(uuid4()),'operation_id':str(uuid4()),
            'correlation_id':str(uuid4()),'owner_ref':OWNER,'kind':'telegram.command',
            'deadline':stamp(AT+timedelta(minutes=5)),'attempt':1,'payload':payload}


def accept(runtime, envelope, *, keys=('tg:update:77',)):
    assert runtime.bridge.commit({'update_id':77,'idempotency_keys':keys},envelope['payload'],envelope)


@pytest.mark.parametrize('version',[1,2])
def test_queue_run_each_page_and_result_preserve_envelope_version(runtime,version):
    search=runtime.store.search(owner_ref=OWNER,search_ref='search:perfume')
    runtime.store.save_search(owner_ref=OWNER,search=replace(search,page_size=1,max_jobs=3,max_pages=3))
    envelope=command_envelope(version)
    accept(runtime,envelope)
    runtime.pump(OWNER,search_ref=search.search_ref)
    rows=runtime.store.db.execute('SELECT entry FROM queue WHERE owner=?',(OWNER,)).fetchall()
    emitted=[loads(row[0]).envelope for row in rows]
    assert len(emitted)==7  # command plus three read/result pairs
    assert {item['schema_version'] for item in emitted}=={version}
    assert {item['payload']['schema_version'] for item in emitted}=={1}
    assert {item['kind'] for item in emitted}=={'telegram.command','browser.read','browser.result'}
    reports=runtime.store.reports(owner_ref=OWNER,operation_id=envelope['operation_id'])
    assert max(reports,key=lambda r:r.pages_used).candidates[0].economics.profit.amount==30
    for item in emitted:
        validate_envelope(item)


@pytest.mark.parametrize('version',[1,2])
def test_durable_result_and_replay_after_restart_keep_original_version(runtime,version):
    envelope=command_envelope(version)
    accept(runtime,envelope)
    runtime.pump(OWNER,search_ref='search:perfume')
    saved=runtime.store.reports(owner_ref=OWNER,operation_id=envelope['operation_id'])
    result=loads(runtime.store.db.execute('SELECT doc FROM worker_results WHERE owner=?',(OWNER,)).fetchone()[0])
    path=runtime.store.path
    runtime.store.close()
    runtime.clock.value+=timedelta(minutes=8)
    resumed=LocalRuntime(path,fixtures=FIXTURES,synthetic_authorized=True,clock=runtime.clock)
    decoded=decode_result(resumed.store,owner_ref=OWNER,envelope=result)
    assert decoded.envelope['schema_version']==version
    assert handle_worker_result(resumed.store,owner_ref=OWNER,result=decoded,now=resumed.clock.now())==saved[0]
    task,_,_=resumed.store.task(owner_ref=OWNER,message_id=result['causation_id'])
    assert resumed.worker.execute(owner_ref=OWNER,envelope=task)==result
    assert resumed.worker.executions==0
    resumed.store.close()
    runtime.store=SQLiteStore(path)


def test_current_telegram_webhook_emits_v2_with_v1_payload(runtime):
    assert update(runtime)['statusCode']==200
    envelope=runtime.store.pending(owner_ref=OWNER,limit=1).entries[0].envelope
    assert envelope['schema_version']==2 and envelope['payload']['schema_version']==1
    runtime.pump(OWNER,search_ref='search:perfume')
    assert runtime.ui.messages(owner_ref=OWNER)


def test_migration_callback_replay_preserves_legacy_receipt_and_command(runtime):
    legacy=command_envelope(1,callback='review:legacy')
    accept(runtime,legacy,keys=('tg:update:77','callback:legacy'))
    original=runtime.bridge.last_accepted
    migrated=command_envelope(2,update_id=78,callback='review:legacy')
    assert not runtime.bridge.commit({'update_id':78,'idempotency_keys':('tg:update:78','callback:legacy')},migrated['payload'],migrated)
    replay=runtime.bridge.last_accepted
    assert replay.replayed and (replay.operation_id,replay.message_id)==(original.operation_id,original.message_id)
    assert runtime.store.command(owner_ref=OWNER,operation_id=original.operation_id)==legacy
    assert len(runtime.store.pending(owner_ref=OWNER,limit=10).entries)==1


@pytest.mark.parametrize('version',[0,3,-1,True,1.0,2.0,'1',None,'absent'])
def test_unsupported_version_rejected_before_receipt_or_queue_write(runtime,version):
    envelope=command_envelope(version)
    if version=='absent':
        envelope.pop('schema_version')
    with pytest.raises(ValidationError):
        runtime.bridge.commit({'idempotency_keys':['tg:update:77']},envelope['payload'],envelope)
    with pytest.raises(ValidationError):
        runtime.queue.publish(owner_ref=OWNER,entry=OutboxEntry(envelope,'commands.fifo'))
    for table in ('receipts','commands','outbox','queue'):
        assert runtime.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0


@pytest.mark.parametrize('version',[1,2])
@pytest.mark.parametrize('invalid',['payload_v2','lease','unknown_kind'])
def test_envelope_validation_keeps_canonical_negative_gates(runtime,version,invalid):
    envelope=command_envelope(version)
    if invalid=='payload_v2':
        envelope['payload']['schema_version']=2
    elif invalid=='lease':
        envelope['lease']='untrusted:token'
    else:
        envelope['kind']='telegram.alert'
    with pytest.raises(ValidationError):
        runtime.queue.publish(owner_ref=OWNER,entry=OutboxEntry(envelope,'commands.fifo'))
    assert runtime.store.db.execute('SELECT count(*) FROM queue').fetchone()[0]==0


@pytest.mark.parametrize('version',[1,2])
def test_result_cannot_switch_version_of_the_accepted_task(runtime,version):
    envelope=command_envelope(version)
    accept(runtime,envelope)
    runtime.pump(OWNER,search_ref='search:perfume')
    result=loads(runtime.store.db.execute('SELECT doc FROM worker_results WHERE owner=?',(OWNER,)).fetchone()[0])
    decoded=decode_result(runtime.store,owner_ref=OWNER,envelope=result)
    other=dict(result,schema_version=3-version)
    validate_envelope(other)  # Individually valid; the operation binding forbids it.
    with pytest.raises(ConditionalConflict):
        runtime.store.complete_worker(owner_ref=OWNER,task_message_id=result['causation_id'],envelope=other,next_cursor=None,now=AT)
    with pytest.raises(ConditionalConflict):
        handle_worker_result(runtime.store,owner_ref=OWNER,result=replace(decoded,envelope=other,content_hash=digest(other)),now=AT)
    assert runtime.store.worker_result(owner_ref=OWNER,task_message_id=result['causation_id'])==result
