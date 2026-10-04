"""A20 regression: bounded queues must drain accepted work, not starve consumers."""
from datetime import timedelta
import sqlite3

import pytest

from radar.adapters.local.codec import loads
from radar.adapters.local.queue import FakeQueue, QueueCapacity, relay_outbox
from radar.adapters.local.runtime import LocalRuntime
from radar.adapters.local.sqlite import SQLiteStore
from conftest import FIXTURES, OWNER, update


def test_more_than_default_queue_capacity_drains_accepted_commands(runtime):
    operations=[]
    for identifier in range(1,102):
        assert update(runtime,update_id=identifier)['statusCode']==200
        operations.append(runtime.bridge.last_accepted.operation_id)
    runtime.pump(OWNER,search_ref='search:perfume')
    assert all(runtime.store.run(owner_ref=OWNER,operation_id=op).status=='succeeded' for op in operations)
    assert all(runtime.store.reports(owner_ref=OWNER,operation_id=op) for op in operations)
    assert not runtime.store.pending(owner_ref=OWNER,limit=100).entries
    assert runtime.store.db.execute('SELECT COUNT(*) FROM queue WHERE owner=? AND acked=0',(OWNER,)).fetchone()==(0,)


def fail(stage):
    raise RuntimeError('synthetic crash')


def test_full_queue_publish_before_mark_replay_restart_fifo_without_loss(runtime):
    runtime.queue=FakeQueue(runtime.store,runtime.clock,capacity=1)
    operations=[]
    for identifier,command in enumerate(('/buscar fixture','/resumen','/buscar fixture'),1):
        assert update(runtime,update_id=identifier,command=command)['statusCode']==200
        operations.append(runtime.bridge.last_accepted.operation_id)
    first=runtime.store.pending(owner_ref=OWNER,limit=1).entries[0]
    with pytest.raises(RuntimeError,match='synthetic crash'):
        relay_outbox(runtime.store,runtime.queue,owner_ref=OWNER,now=runtime.clock.now(),failpoint=fail)
    assert len(runtime.store.pending(owner_ref=OWNER,limit=10).entries)==3
    runtime.relay(OWNER)  # Replay of accepted first item works even at capacity.
    pending=runtime.store.pending(owner_ref=OWNER,limit=10).entries
    assert [entry.envelope['operation_id'] for entry in pending]==operations[1:]
    assert runtime.store.db.execute('SELECT COUNT(*) FROM queue WHERE acked=0').fetchone()==(1,)
    path=runtime.store.path
    runtime.store.close()
    resumed=LocalRuntime(path,fixtures=FIXTURES,synthetic_authorized=True,clock=runtime.clock)
    resumed.queue=FakeQueue(resumed.store,resumed.clock,capacity=1)
    try:
        resumed.pump(OWNER,search_ref='search:perfume')
        for destination,expected in (('commands.fifo',operations),('browser.fifo',operations[::2]),('results',operations[::2])):
            rows=resumed.store.db.execute('SELECT entry FROM queue WHERE owner=? AND destination=? ORDER BY seq',(OWNER,destination)).fetchall()
            assert [loads(row[0]).envelope['operation_id'] for row in rows]==expected
        assert resumed.store.db.execute('SELECT code FROM quarantine').fetchall()==[('unsupported',)]
        assert all(resumed.store.reports(owner_ref=OWNER,operation_id=op) for op in operations[::2])
        assert resumed.worker.executions==2 and len(resumed.ui.messages(owner_ref=OWNER))==2
        assert not resumed.store.pending(owner_ref=OWNER,limit=100).entries
        assert resumed.store.db.execute('SELECT COUNT(*) FROM queue WHERE acked=0').fetchone()==(0,)
        resumed.queue.publish(owner_ref=OWNER,entry=first)
        for destination in ('commands.fifo','browser.fifo','results'):
            resumed.queue.replay(owner_ref=OWNER,destination=destination)
        resumed.pump(OWNER,search_ref='search:perfume')
        assert resumed.worker.executions==2 and len(resumed.ui.messages(owner_ref=OWNER))==2
    finally:
        resumed.store.close()
        runtime.store=SQLiteStore(path)


@pytest.mark.parametrize('boundary',['publish','mark_published'])
@pytest.mark.parametrize('error',[OverflowError('queue_capacity'),sqlite3.OperationalError('synthetic storage'),RuntimeError('synthetic crash')])
def test_unknown_publication_or_storage_errors_propagate_without_ack(runtime,monkeypatch,boundary,error):
    assert update(runtime)['statusCode']==200
    operation=runtime.bridge.last_accepted.operation_id
    def unexpected(**kwargs):raise error
    target=runtime.queue if boundary=='publish' else runtime.store
    original=getattr(target,boundary)
    monkeypatch.setattr(target,boundary,unexpected)
    with pytest.raises(type(error),match=str(error)):
        runtime.pump(OWNER,search_ref='search:perfume')
    assert runtime.store.db.execute('SELECT COUNT(*) FROM runs WHERE owner=? AND op=?',(OWNER,operation)).fetchone()==(0,)
    assert len(runtime.store.pending(owner_ref=OWNER,limit=10).entries)==1
    assert runtime.store.db.execute('SELECT COUNT(*) FROM queue WHERE acked=1').fetchone()==(0,)
    assert runtime.store.db.execute('SELECT COUNT(*) FROM queue').fetchone()==(0 if boundary=='publish' else 1,)
    monkeypatch.setattr(target,boundary,original)
    runtime.pump(OWNER,search_ref='search:perfume')
    assert runtime.store.reports(owner_ref=OWNER,operation_id=operation) and runtime.worker.executions==1
    assert runtime.store.db.execute('SELECT COUNT(*) FROM queue WHERE acked=0').fetchone()==(0,)


@pytest.mark.parametrize('boundary',['mark_published','after_publish'])
def test_typed_capacity_after_publish_is_still_a_visible_failure(runtime,monkeypatch,boundary):
    assert update(runtime)['statusCode']==200
    def unexpected(*args,**kwargs):raise QueueCapacity('synthetic boundary failure')
    if boundary=='mark_published':monkeypatch.setattr(runtime.store,'mark_published',unexpected)
    with pytest.raises(QueueCapacity,match='synthetic boundary failure'):
        relay_outbox(runtime.store,runtime.queue,owner_ref=OWNER,now=runtime.clock.now(),pause_on_capacity=True,
                     failpoint=unexpected if boundary=='after_publish' else lambda stage:None)
    assert len(runtime.store.pending(owner_ref=OWNER,limit=10).entries)==1
    assert runtime.store.db.execute('SELECT COUNT(*) FROM queue WHERE acked=0').fetchone()==(1,)


def test_relay_default_keeps_capacity_exception_and_durable_cursor_contract(runtime):
    runtime.queue=FakeQueue(runtime.store,runtime.clock,capacity=1)
    update(runtime)
    first=runtime.bridge.last_accepted.operation_id
    update(runtime,update_id=2)
    second=runtime.bridge.last_accepted.operation_id
    with pytest.raises(QueueCapacity,match='queue_capacity'):
        relay_outbox(runtime.store,runtime.queue,owner_ref=OWNER,now=runtime.clock.now())
    assert [entry.envelope['operation_id'] for entry in runtime.store.pending(owner_ref=OWNER,limit=10).entries]==[second]
    assert runtime.store.db.execute('SELECT COUNT(*) FROM queue WHERE acked=0').fetchone()==(1,)
    assert runtime.process_commands(OWNER,search_ref='search:perfume')==1
    assert runtime.store.run(owner_ref=OWNER,operation_id=first).status=='running'


@pytest.mark.parametrize('checkpointed',[False,True])
def test_backpressure_does_not_ack_failed_quarantine_before_restart(runtime,checkpointed):
    runtime.queue=FakeQueue(runtime.store,runtime.clock,capacity=1)
    assert update(runtime,command='/resumen')['statusCode']==200
    rejected=runtime.bridge.last_accepted.operation_id
    assert update(runtime,update_id=2)['statusCode']==200
    accepted=runtime.bridge.last_accepted.operation_id
    runtime.relay(OWNER)
    def persistence_failure(stage):
        if stage=='before_quarantine_commit':fail(stage)
    if not checkpointed:runtime.store.failpoint=persistence_failure
    with pytest.raises(RuntimeError,match='synthetic crash'):
        runtime.process_commands(OWNER,search_ref='search:perfume',failpoint=fail if checkpointed else lambda stage:None)
    assert runtime.store.db.execute('SELECT COUNT(*) FROM quarantine').fetchone()==(int(checkpointed),)
    assert runtime.store.db.execute('SELECT COUNT(*) FROM queue WHERE acked=1').fetchone()==(0,)
    assert len(runtime.store.pending(owner_ref=OWNER,limit=10).entries)==1
    path=runtime.store.path
    runtime.store.close()
    resumed=LocalRuntime(path,fixtures=FIXTURES,synthetic_authorized=True,clock=runtime.clock)
    resumed.queue=FakeQueue(resumed.store,resumed.clock,capacity=1)
    try:
        resumed.pump(OWNER,search_ref='search:perfume')  # In-flight item not visible yet.
        assert resumed.store.db.execute('SELECT COUNT(*) FROM queue WHERE acked=1').fetchone()==(0,)
        assert len(resumed.store.pending(owner_ref=OWNER,limit=10).entries)==1
        resumed.clock.value+=timedelta(seconds=31)
        resumed.pump(OWNER,search_ref='search:perfume')
        assert resumed.store.db.execute('SELECT op,code FROM quarantine').fetchall()==[(rejected,'unsupported')]
        assert resumed.store.reports(owner_ref=OWNER,operation_id=accepted)
        assert resumed.worker.executions==1 and not resumed.store.pending(owner_ref=OWNER,limit=10).entries
        assert resumed.store.db.execute('SELECT COUNT(*) FROM queue WHERE acked=0').fetchone()==(0,)
    finally:
        resumed.store.close()
        runtime.store=SQLiteStore(path)


@pytest.mark.parametrize('capacity',[1,2])
def test_minimal_capacity_backpressure_still_drains_all_three_destinations(runtime,capacity):
    runtime.queue=FakeQueue(runtime.store,runtime.clock,capacity=capacity)
    operations=[]
    for identifier in range(1,capacity+2):
        assert update(runtime,update_id=identifier)['statusCode']==200
        operations.append(runtime.bridge.last_accepted.operation_id)
    runtime.pump(OWNER,search_ref='search:perfume')
    assert all(runtime.store.run(owner_ref=OWNER,operation_id=op).status=='succeeded' for op in operations)
    assert set(row[0] for row in runtime.store.db.execute('SELECT DISTINCT destination FROM queue'))=={'commands.fifo','browser.fifo','results'}
    assert not runtime.store.pending(owner_ref=OWNER,limit=100).entries
    assert runtime.store.db.execute('SELECT COUNT(*) FROM queue WHERE owner=? AND acked=0',(OWNER,)).fetchone()==(0,)
