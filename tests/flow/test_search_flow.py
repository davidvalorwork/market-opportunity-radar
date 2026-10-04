from dataclasses import replace
from datetime import timedelta
import json

import pytest

from radar.adapters.local.codec import dumps, loads
from radar.adapters.local.queue import FakeQueue, relay_insert, relay_outbox
from radar.adapters.local.runtime import LocalRuntime
from radar.adapters.local.sqlite import SQLiteStore, digest
from radar.adapters.local.telegram import consent
from radar.adapters.local.ui import deliver_alerts
from radar.adapters.local.worker import decode_result
from radar.application.local_flow import handle_worker_result
from radar.domain.core import CostInput, Knowledge, Money
from radar.ports.types import BlobPointer, ConditionalConflict
from conftest import ACTOR, AT, FIXTURES, OWNER, configure, record, update


def fail(stage_name):
    def injected(stage):
        if stage == stage_name:
            raise RuntimeError(stage)
    return injected


def begin(runtime):
    assert update(runtime)['statusCode'] == 200
    return runtime.bridge.last_accepted.operation_id


def result_ready(runtime):
    op = begin(runtime)
    runtime.relay(OWNER)
    runtime.process_commands(OWNER,search_ref='search:perfume')
    runtime.relay(OWNER)
    runtime.process_workers(OWNER)
    runtime.relay(OWNER)
    delivery = runtime.queue.receive(owner_ref=OWNER,destination='results',limit=1)[0]
    return op,delivery,decode_result(runtime.store,owner_ref=OWNER,envelope=delivery.envelope)


def test_real_webhook_to_evidence_derived_economics_and_private_fake_ui(runtime):
    op = begin(runtime)
    runtime.pump(OWNER,search_ref='search:perfume')
    reports = runtime.store.reports(owner_ref=OWNER,operation_id=op)
    assert len(reports) == 1
    report = reports[0]
    assert len(report.candidates) == 1 and report.candidates[0].economics.profit == Money('30','USD')
    assert report.discarded and 'volume_ml:conflict' in report.discarded[0]
    assert report.source_status == 'succeeded' and report.source_error is None
    assert (report.jobs_used,report.pages_used,report.records_observed)==(1,1,3)
    assert report.compute_cost_usd is None and report.realized_profit is None
    message = runtime.ui.messages(owner_ref=OWNER)[0]
    assert 'precio_publicado_no_es_venta' in message and 'ganancia_estimada=' in message and 'costoUSD=None' in message
    assert op in message and report.message_id in message
    assert not runtime.store.pending(owner_ref=OWNER,limit=100).entries


@pytest.mark.parametrize('page_size', [1,2])
def test_comparables_cross_pages_and_budgets_persist(tmp_path,page_size):
    rt = LocalRuntime(tmp_path/'control.sqlite',fixtures=FIXTURES,synthetic_authorized=True)
    configure(rt,pages=4,jobs=4,page_size=page_size)
    op = begin(rt)
    rt.pump(OWNER,search_ref='search:perfume')
    reports = rt.store.reports(owner_ref=OWNER,operation_id=op)
    final = max(reports,key=lambda report:report.pages_used)
    assert final.candidates[0].economics.profit == Money('30','USD')
    assert final.records_observed == 3
    run = rt.store.run(owner_ref=OWNER,operation_id=op)
    assert run.pages_used == (3 if page_size == 1 else 2) and run.status == 'succeeded'
    rt.store.close()


def test_unknown_cost_remains_unknown(runtime):
    search = runtime.store.search(owner_ref=OWNER,search_ref='search:perfume')
    runtime.store.save_search(owner_ref=OWNER,search=replace(search,incoming=()))
    op = begin(runtime)
    runtime.pump(OWNER,search_ref='search:perfume')
    economics = runtime.store.reports(owner_ref=OWNER,operation_id=op)[0].candidates[0].economics
    assert economics.profit is None and economics.missing == ('cost:shipping',)


@pytest.mark.parametrize('stage',['after_receipt','after_command','after_outbox','before_commit'])
def test_acceptance_failure_rolls_back_every_write(runtime,stage):
    runtime.store.failpoint = fail(stage)
    assert update(runtime)['statusCode'] == 500
    for table in ('receipts','commands','outbox'):
        assert runtime.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    runtime.store.failpoint = lambda stage:None
    assert update(runtime)['statusCode'] == 200
    assert len(runtime.store.pending(owner_ref=OWNER,limit=10).entries) == 1


def test_commit_without_enqueue_survives_database_restart(runtime):
    op = begin(runtime)
    path = runtime.store.path
    runtime.store.close()
    resumed = LocalRuntime(path,fixtures=FIXTURES,synthetic_authorized=True,clock=runtime.clock)
    resumed.pump(OWNER,search_ref='search:perfume')
    assert resumed.store.reports(owner_ref=OWNER,operation_id=op)[0].candidates
    resumed.store.close()
    runtime.store = SQLiteStore(path)


def test_publish_without_mark_and_modify_events_do_not_recurse(runtime):
    begin(runtime)
    with pytest.raises(RuntimeError):
        relay_outbox(runtime.store,runtime.queue,owner_ref=OWNER,now=AT,failpoint=fail('after_publish'))
    assert len(runtime.store.pending(owner_ref=OWNER,limit=10).entries) == 1
    relay_insert(runtime.store,runtime.queue,owner_ref=OWNER,event_name='MODIFY',now=AT)
    assert len(runtime.store.pending(owner_ref=OWNER,limit=10).entries) == 1
    runtime.relay(OWNER)
    assert runtime.store.db.execute('SELECT count(*) FROM queue').fetchone()[0] == 1
    runtime.pump(OWNER,search_ref='search:perfume')


@pytest.mark.parametrize('stage',['after_projection','after_alert','before_commit'])
def test_projection_and_alert_atomic_before_ack(runtime,stage):
    op,delivery,result = result_ready(runtime)
    runtime.store.failpoint = fail(stage)
    with pytest.raises(RuntimeError):
        handle_worker_result(runtime.store,owner_ref=OWNER,result=result,now=AT)
    assert not runtime.store.reports(owner_ref=OWNER,operation_id=op)
    assert not runtime.store.pending_alerts(owner_ref=OWNER)
    runtime.store.failpoint = lambda stage:None
    report = handle_worker_result(runtime.store,owner_ref=OWNER,result=result,now=AT)
    runtime.queue.replay(owner_ref=OWNER,destination='results')
    runtime.process_results(OWNER)
    assert runtime.store.reports(owner_ref=OWNER,operation_id=op) == (report,)


@pytest.mark.parametrize('boundary',['after_worker_result','after_result_projection'])
def test_result_before_ack_replays_after_five_minutes_and_restart(runtime,boundary):
    op = begin(runtime)
    runtime.relay(OWNER)
    runtime.process_commands(OWNER,search_ref='search:perfume')
    runtime.relay(OWNER)
    if boundary == 'after_worker_result':
        with pytest.raises(RuntimeError):
            runtime.process_workers(OWNER,failpoint=fail(boundary))
        # Complete the first projection while deadline is still current.
        runtime.relay(OWNER)
        runtime.process_results(OWNER)
    else:
        runtime.process_workers(OWNER)
        runtime.relay(OWNER)
        with pytest.raises(RuntimeError):
            runtime.process_results(OWNER,failpoint=fail(boundary))
    original = runtime.store.reports(owner_ref=OWNER,operation_id=op)
    path = runtime.store.path
    runtime.store.close()
    runtime.clock.value += timedelta(minutes=8)
    resumed = LocalRuntime(path,fixtures=FIXTURES,synthetic_authorized=True,clock=runtime.clock)
    resumed.queue.replay(owner_ref=OWNER,destination='browser.fifo')
    resumed.queue.replay(owner_ref=OWNER,destination='results')
    resumed.process_workers(OWNER)
    resumed.relay(OWNER)
    resumed.process_results(OWNER)
    assert resumed.worker.executions == 0
    assert resumed.store.reports(owner_ref=OWNER,operation_id=op) == original
    resumed.store.close()
    runtime.store = SQLiteStore(path)


@pytest.mark.parametrize('field,changed', [('owner_ref','owner:other'),('operation_id','00000000-0000-0000-0000-000000000001'),
                                        ('correlation_id','00000000-0000-0000-0000-000000000002'),
                                        ('causation_id','00000000-0000-0000-0000-000000000003'),('expected_version',9),
                                        ('deadline','2026-10-03T00:04:00Z'),('message_id','00000000-0000-0000-0000-000000000004')])
def test_forged_result_cannot_project(runtime,field,changed):
    op,_,result = result_ready(runtime)
    env = dict(result.envelope,**{field:changed})
    forged = replace(result,envelope=env,content_hash=digest(env))
    with pytest.raises(ConditionalConflict):
        handle_worker_result(runtime.store,owner_ref=OWNER,result=forged,now=AT)
    assert not runtime.store.reports(owner_ref=OWNER,operation_id=op)


def test_forged_hash_and_changed_evidence_cannot_project(runtime):
    op,_,result = result_ready(runtime)
    with pytest.raises(ConditionalConflict):
        handle_worker_result(runtime.store,owner_ref=OWNER,result=replace(result,content_hash='0'*64),now=AT)
    env = json.loads(json.dumps(result.envelope))
    env['payload']['records'][0]['fields']['amount']='1'
    with pytest.raises(ConditionalConflict):
        decode_result(runtime.store,owner_ref=OWNER,envelope=env)
    assert not runtime.store.reports(owner_ref=OWNER,operation_id=op)


@pytest.mark.parametrize('authorized,pages,jobs,expected',[(False,1,1,'blocked'),(True,1,1,'budget_exhausted')])
def test_preflight_and_budget_stop_new_jobs(tmp_path,authorized,pages,jobs,expected):
    rt = LocalRuntime(tmp_path/'control.sqlite',fixtures=FIXTURES,synthetic_authorized=True)
    configure(rt,authorized=authorized,pages=pages,jobs=jobs,page_size=1)
    op = begin(rt)
    rt.pump(OWNER,search_ref='search:perfume')
    run = rt.store.run(owner_ref=OWNER,operation_id=op)
    assert run.status == expected and run.jobs_used <= jobs and run.pages_used <= pages
    assert rt.worker.executions == (1 if authorized else 0)
    rt.store.close()


@pytest.mark.parametrize('stop',['cancel','stop'])
def test_cancel_and_stop_prevent_worker_reads(runtime,stop):
    op = begin(runtime)
    runtime.relay(OWNER)
    runtime.process_commands(OWNER,search_ref='search:perfume')
    if stop == 'cancel':
        runtime.store.cancel(owner_ref=OWNER,operation_id=op)
    else:
        assert update(runtime,update_id=2,command='/stop')['statusCode'] == 200
        runtime.relay(OWNER)
        runtime.process_commands(OWNER,search_ref='search:perfume')
    runtime.relay(OWNER)
    runtime.process_workers(OWNER)
    assert runtime.store.db.execute('SELECT count(*) FROM quarantine WHERE owner=?',(OWNER,)).fetchone()[0]==1
    assert runtime.worker.executions == 0
    assert runtime.store.run(owner_ref=OWNER,operation_id=op).status == 'cancelled'


def test_alert_delivery_crash_converges(runtime):
    op,_,result = result_ready(runtime)
    handle_worker_result(runtime.store,owner_ref=OWNER,result=result,now=AT)
    with pytest.raises(RuntimeError):
        deliver_alerts(runtime.store,runtime.ui,owner_ref=OWNER,failpoint=fail('after_ui_delivery'))
    assert runtime.store.pending_alerts(owner_ref=OWNER)
    deliver_alerts(runtime.store,runtime.ui,owner_ref=OWNER)
    assert len(runtime.ui.messages(owner_ref=OWNER)) == 1


def test_codec_external_tag_keys_remain_plain_data():
    value = {'@type':'Money','fields':{'amount':'9','currency':'USD'},'@tuple':['x'],'@mapping':[['x','y']]}
    assert loads(dumps(value)) == value


def test_blobs_immutable_owner_scoped_no_plaintext_private_claim(runtime):
    from hashlib import sha256
    content = b'public synthetic fixture'
    pointer = BlobPointer('public/fixture',sha256(content).hexdigest())
    runtime.store.put_if_absent(owner_ref=OWNER,blob=pointer,content=content)
    assert runtime.store.read(owner_ref=OWNER,blob=pointer,max_bytes=100) == content
    for owner,blob,limit in [('owner:other',pointer,100),(OWNER,pointer,1),(OWNER,replace(pointer,recipient_scope='worker:whatsapp'),100)]:
        with pytest.raises(ConditionalConflict):
            runtime.store.read(owner_ref=owner,blob=blob,max_bytes=limit)
    with pytest.raises(ConditionalConflict):
        runtime.store.put_if_absent(owner_ref=OWNER,blob=replace(pointer,recipient_scope='worker:whatsapp'),content=content)


def test_consent_persist_and_rollback(runtime):
    actor = 'user:beta'
    runtime.directory.enroll_synthetic(202,owner_ref=OWNER,actor_ref=actor)
    runtime.store.failpoint = fail('after_consent_receipt')
    version = consent.CONSENT_VERSION if consent else 'synthetic-v1'
    with pytest.raises(RuntimeError):
        runtime.directory.accept_consent(actor,version,AT,('new-consent',))
    assert runtime.directory.get(202)['consent_version'] is None
    assert runtime.store.db.execute('SELECT 1 FROM ignored WHERE ref=?',('new-consent',)).fetchone() is None
    runtime.store.failpoint = lambda stage:None
    assert runtime.directory.accept_consent(actor,version,AT,('new-consent',))
    assert not runtime.directory.accept_consent(actor,version,AT,('new-consent',))
    assert runtime.directory.get(202)['consent_version'] == version


def test_keyset_repair_capacity_and_owner_isolation(runtime):
    for i in range(1,6):
        assert update(runtime,update_id=i)['statusCode'] == 200
    page = runtime.store.pending(owner_ref=OWNER,limit=2)
    second = runtime.store.pending(owner_ref=OWNER,limit=2,cursor=page.next_cursor)
    last = runtime.store.pending(owner_ref=OWNER,limit=2,cursor=second.next_cursor)
    assert [len(p.entries) for p in (page,second,last)] == [2,2,1]
    assert len({e.envelope['message_id'] for p in (page,second,last) for e in p.entries}) == 5
    assert not runtime.store.pending(owner_ref='owner:other',limit=2).entries
    assert any('pending_outbox' in str(r) for r in runtime.store.db.execute('EXPLAIN QUERY PLAN SELECT seq FROM outbox WHERE owner=? AND published IS NULL AND seq>? ORDER BY seq LIMIT 2',(OWNER,0)))
    queue = FakeQueue(runtime.store,runtime.clock,capacity=1)
    queue.publish(owner_ref=OWNER,entry=page.entries[0])
    with pytest.raises(OverflowError):
        queue.publish(owner_ref=OWNER,entry=page.entries[1])


def test_run_freezes_costs_source_and_budget_across_edit_and_restart(runtime):
    search = runtime.store.search(owner_ref=OWNER,search_ref='search:perfume')
    runtime.store.save_search(owner_ref=OWNER,search=replace(search,page_size=1))
    op = begin(runtime)
    runtime.relay(OWNER)
    runtime.process_commands(OWNER,search_ref=search.search_ref)
    changed = replace(search,incoming=(CostInput('shipping',Money('0','USD'),Knowledge.KNOWN,'other explicit quote',AT,True),),
                      max_jobs=10,max_pages=10,source_ref='source:other')
    runtime.store.save_search(owner_ref=OWNER,search=changed)
    path = runtime.store.path
    runtime.store.close()
    resumed = LocalRuntime(path,fixtures=FIXTURES,synthetic_authorized=True,clock=runtime.clock)
    resumed.pump(OWNER,search_ref=search.search_ref)
    frozen = resumed.store.search_for_run(owner_ref=OWNER,operation_id=op)
    assert frozen.incoming == search.incoming and frozen.source_ref == search.source_ref
    run = resumed.store.run(owner_ref=OWNER,operation_id=op)
    assert run.jobs_used == run.pages_used == 1 and run.status == 'budget_exhausted'
    resumed.store.close()
    runtime.store = SQLiteStore(path)


def test_durable_result_admitted_before_deadline_projects_after_restart(runtime):
    search = runtime.store.search(owner_ref=OWNER,search_ref='search:perfume')
    runtime.store.save_search(owner_ref=OWNER,search=replace(search,max_pages=10,max_jobs=10,page_size=2))
    op,delivery,result = result_ready(runtime)
    path = runtime.store.path
    runtime.store.close()
    runtime.clock.value += timedelta(minutes=8)
    resumed = LocalRuntime(path,fixtures=FIXTURES,synthetic_authorized=True,clock=runtime.clock)
    resumed.queue.replay(owner_ref=OWNER,destination='results')
    resumed.process_results(OWNER)
    reports = resumed.store.reports(owner_ref=OWNER,operation_id=op)
    assert reports[0].candidates[0].economics.profit == Money('30','USD')
    assert resumed.store.run(owner_ref=OWNER,operation_id=op).status == 'expired'
    assert resumed.store.db.execute('SELECT count(*) FROM tasks').fetchone()[0] == 1
    resumed.store.close()
    runtime.store = SQLiteStore(path)


@pytest.mark.parametrize('field,value',[('owner_ref','owner:other'),('operation_id','00000000-0000-0000-0000-000000000003'),
                                     ('correlation_id','00000000-0000-0000-0000-000000000004'),('expected_version',19)])
def test_worker_replay_binding_precedes_existing_return(runtime,field,value):
    op,delivery,result = result_ready(runtime)
    env = dict(result.envelope,**{field:value})
    with pytest.raises(ConditionalConflict):
        runtime.store.complete_worker(owner_ref=OWNER,task_message_id=result.envelope['causation_id'],envelope=env,next_cursor=None,now=AT)
    assert runtime.store.worker_result(owner_ref=OWNER,task_message_id=result.envelope['causation_id']) == result.envelope


def test_changed_payload_is_not_silent_worker_replay(runtime):
    _,_,result = result_ready(runtime)
    env = json.loads(json.dumps(result.envelope))
    env['payload']['records'][0]['fields']['amount'] = '1'
    with pytest.raises(ConditionalConflict):
        runtime.store.complete_worker(owner_ref=OWNER,task_message_id=result.envelope['causation_id'],envelope=env,next_cursor=None,now=AT)


def test_logs_only_metadata(runtime,caplog):
    with caplog.at_level('INFO'):
        op = begin(runtime)
        runtime.pump(OWNER,search_ref='search:perfume')
    assert 'fixture' not in caplog.text and 'Synthetic' not in caplog.text
    assert 'update 1' in caplog.text
