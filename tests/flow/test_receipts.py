from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest

from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.telegram import Directory, TelegramBridge
from radar.ports.types import ConditionalConflict, OutboxEntry, Receipt
from conftest import AT, OWNER, update


def callback(runtime, update_id):
    body = json.dumps({'update_id':update_id,'callback_query':{'id':'synthetic-query','from':{'id':101},'data':'review:fixture'}}).encode()
    return runtime.webhook.handle_update({'X-Telegram-Bot-Api-Secret-Token':'synthetic-local-secret'},body)


def test_callback_different_update_id_returns_original_ids(runtime):
    assert callback(runtime,1)['statusCode'] == 200
    first = runtime.bridge.last_accepted
    assert callback(runtime,2)['statusCode'] == 200
    second = runtime.bridge.last_accepted
    assert second.replayed and (first.operation_id,first.message_id)==(second.operation_id,second.message_id)
    assert runtime.store.db.execute('SELECT count(*) FROM commands').fetchone()[0] == 1
    assert runtime.store.db.execute('SELECT count(*) FROM receipts').fetchone()[0] == 3


def test_two_connections_concurrent_receipts_converge(runtime):
    update(runtime)
    entry = runtime.store.pending(owner_ref=OWNER,limit=1).entries[0]
    command = dict(entry.envelope)
    payload = command['payload']
    stores = [SQLiteStore(runtime.store.path),SQLiteStore(runtime.store.path)]
    bridges = [TelegramBridge(s,Directory(s)) for s in stores]
    def accept(index):
        envelope = dict(command,message_id=f'00000000-0000-0000-0000-00000000000{index+1}',operation_id=f'00000000-0000-0000-0000-00000000000{index+3}')
        return bridges[index].commit({'idempotency_keys':['tg:update:1']},payload,envelope)
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(accept,range(2))) == [False,False]
    assert all(b.last_accepted.operation_id==command['operation_id'] for b in bridges)
    for store in stores:
        store.close()


def test_concurrent_callbacks_one_operation_and_atomic_secondary_refs(runtime):
    # Start with distinct events racing for the SAME secondary callback ref.
    update(runtime)
    original = runtime.store.pending(owner_ref=OWNER,limit=1).entries[0].envelope
    stores = [SQLiteStore(runtime.store.path),SQLiteStore(runtime.store.path)]
    def accept(index):
        envelope = dict(original,message_id=f'00000000-0000-0000-0000-00000000000{index+1}',operation_id=f'00000000-0000-0000-0000-00000000000{index+3}')
        receipt = Receipt('telegram',f'update:new{index}','a'*64,('callback:shared',))
        return stores[index].accept_command(owner_ref=OWNER,receipt=receipt,command=envelope,outbox=OutboxEntry(envelope,'commands.fifo',OWNER))
    with ThreadPoolExecutor(max_workers=2) as executor:
        accepted = list(executor.map(accept,range(2)))
    assert sorted(a.replayed for a in accepted)==[False,True]
    assert len({a.operation_id for a in accepted}) == 1
    assert runtime.store.db.execute('SELECT count(*) FROM receipts WHERE hash=?',('a'*64,)).fetchone()[0] == 3
    for store in stores:
        store.close()


def test_conflicting_secondary_ref_rolls_back_primary(runtime):
    update(runtime)
    envelope = runtime.store.pending(owner_ref=OWNER,limit=1).entries[0].envelope
    first = Receipt('telegram','new-first','a'*64,('shared',))
    runtime.store.accept_command(owner_ref=OWNER,receipt=first,command=dict(envelope,operation_id='00000000-0000-0000-0000-000000000001',message_id='00000000-0000-0000-0000-000000000002'),
                                outbox=OutboxEntry(dict(envelope,operation_id='00000000-0000-0000-0000-000000000001',message_id='00000000-0000-0000-0000-000000000002'),'commands.fifo'))
    with pytest.raises(ConditionalConflict):
        runtime.store.accept_command(owner_ref=OWNER,receipt=Receipt('telegram','must-not-survive','b'*64,('shared',)),command=envelope,outbox=OutboxEntry(envelope,'commands.fifo'))
    assert runtime.store.db.execute('SELECT 1 FROM receipts WHERE ref=?',('must-not-survive',)).fetchone() is None


def test_bridge_rejects_caller_supplied_owner(runtime):
    update(runtime)
    envelope = runtime.store.pending(owner_ref=OWNER,limit=1).entries[0].envelope
    with pytest.raises(ConditionalConflict):
        runtime.bridge.commit({'idempotency_keys':['unknown']},envelope['payload'],dict(envelope,owner_ref='owner:forged'))
