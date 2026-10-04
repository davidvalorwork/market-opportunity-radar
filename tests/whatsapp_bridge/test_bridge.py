from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from threading import Event, Thread

import pytest

from radar.adapters.local.whatsapp_bridge import BridgeError, GoBridgeRPC, utc_now
from radar.application.conversations import DraftInput
from radar.ports.types import ConditionalConflict, LedgerState
MARKER = 'PRIVATE_SYNTHETIC_BODY_8293'
NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)


def approved(setup):
    store, repo, service, authority, vault, clock, protocol, bridge = setup
    batch = service.compose(authority=authority, event_ref='event:compose', account_ref='account:alpha', drafts=(DraftInput(('chat:one',), MARKER + ' café 🤝', 'purpose:reply'),), now=clock.now())
    screen = service.screen(authority=authority, batch_ref=batch.batch_ref, now=clock.now())
    batch = service.approve(authority=authority, event_ref='event:approve', screen=screen, now=clock.now())
    return batch.operation_ids[0], screen.callback_ref


def sync(setup, event='event:sync', **kwargs):
    return setup[-1].sync(authority=setup[3], account_ref='account:alpha', event_ref=event, enabled_chat_refs=('chat:one',), **kwargs)


def test_pair_private_code_and_ciphertext_hash(setup):
    store, repo, _, authority, vault, _, protocol, bridge = setup
    assert protocol.codes == ['SYNTHETIC_CODE']
    result = bridge.list_chats(authority=authority, account_ref='account:alpha', event_ref='event:list')
    private = vault.open(owner_ref=authority.owner_ref, pointer=result.private_ref)
    assert MARKER in private.decode() and result.private_ref.sha256 != sha256(private).hexdigest()
    with pytest.raises(Exception):
        repo.vault.open(owner_ref=authority.owner_ref, pointer=result.private_ref)
    assert MARKER not in repr(result) + repr(bridge)
    assert 'SYNTHETIC_CODE' not in repr(store.db.execute('SELECT * FROM whatsapp_bridge_calls').fetchall())


def test_sync_projection_before_ack_replay_and_restart(setup):
    store, repo, _, authority, vault, _, protocol, bridge = setup
    first = sync(setup)
    private = json.loads(vault.open(owner_ref=authority.owner_ref, pointer=first.private_ref))
    assert private['cursor'] == 'p1' and private['session_version'] == 1
    message = private['messages'][0]['provider_message_ref']
    assert repo.list_messages(authority=authority, account_ref='account:alpha', chat_ref='chat:one', now=NOW)[0][0].provider_message_ref == message
    assert sync(setup) == replace(first, replayed=True)
    # Reopen a real SQLite connection: no in-memory receipts or offsets.
    from radar.adapters.local.sqlite import SQLiteStore
    second = SQLiteStore(store.path)
    try:
        assert second.db.execute('SELECT count(*) FROM conversation_messages').fetchone() == (1,)
        assert second.db.execute('SELECT state FROM whatsapp_bridge_calls WHERE event=?', ('event:sync',)).fetchone() == ('completed',)
    finally:
        second.close()
    sync(setup, 'event:second', ack_ref=first.private_ref)
    assert protocol.acks == ['', 'p1']
    assert store.db.execute('SELECT count(*) FROM conversation_messages').fetchone() == (1,)


def test_cursor_forgery_cannot_ack(setup):
    _, _, _, authority, vault, _, protocol, _ = setup
    forged = vault.seal(owner_ref=authority.owner_ref, plaintext=json.dumps({'owner_ref': authority.owner_ref, 'account_ref': 'account:alpha', 'session_ref': 'session:alpha', 'session_version': 1, 'cursor': 'p1'}).encode())
    with pytest.raises(ConditionalConflict, match='bridge_cursor_binding'):
        sync(setup, ack_ref=forged)
    assert protocol.acks == []


@pytest.mark.parametrize('field,value', [('chat_ref', 'chat:foreign'), ('provider_message_id', ''), ('cursor', 'broken'), ('observed_at', 'invalid'), ('text', 'x' * 4097)])
def test_malformed_sync_no_projection_or_ack(setup, field, value):
    store, _, _, _, _, _, protocol, _ = setup
    protocol.messages[0][field] = value
    with pytest.raises(BridgeError):
        sync(setup)
    assert store.db.execute('SELECT count(*) FROM conversation_messages').fetchone() == (0,)
    assert sync(setup).state == 'uncertain'
    assert protocol.acks == ['']


@pytest.mark.parametrize('checkpoint', ['whatsapp_bridge_reserved', 'whatsapp_bridge_after_io'])
def test_read_crash_stays_uncertain_no_automatic_io_retry(setup, checkpoint):
    store, _, _, _, _, _, protocol, _ = setup
    def fail(name):
        if name == checkpoint:
            raise RuntimeError('synthetic_crash')
    store.failpoint = fail
    with pytest.raises(RuntimeError):
        sync(setup)
    count = len(protocol.calls)
    store.failpoint = lambda name: None
    assert sync(setup).state == 'uncertain'
    assert len(protocol.calls) == count
    assert store.db.execute('SELECT count(*) FROM conversation_messages').fetchone() == (0,)


def test_exact_approval_send_and_replay_binding(setup):
    store, _, _, authority, _, _, protocol, bridge = setup
    operation, token = approved(setup)
    record = bridge.send(authority=authority, operation_id=operation, approval_ref=token)
    assert record.state == LedgerState.PROVIDER_CONFIRMED
    assert protocol.sent[0]['text'] == MARKER + ' café 🤝'
    assert sha256(protocol.sent[0]['text'].encode()).hexdigest() == record.content_hash
    assert bridge.send(authority=authority, operation_id=operation, approval_ref=token) == record
    assert len(protocol.sent) == 1
    with pytest.raises(ConditionalConflict):
        bridge.send(authority=authority, operation_id=operation, approval_ref='approval:wrong')
    store.revoke(owner_ref=authority.owner_ref, actor_ref=authority.actor_ref)
    with pytest.raises(ConditionalConflict):
        bridge.send(authority=authority, operation_id=operation, approval_ref=token)
    assert len(protocol.sent) == 1


@pytest.mark.parametrize('checkpoint', ['whatsapp_bridge_after_claim', 'whatsapp_bridge_after_send'])
def test_send_crash_no_resend(setup, checkpoint):
    store, _, _, authority, _, _, protocol, bridge = setup
    operation, token = approved(setup)
    def fail(name):
        if name == checkpoint:
            raise RuntimeError('synthetic_crash')
    store.failpoint = fail
    with pytest.raises(RuntimeError):
        bridge.send(authority=authority, operation_id=operation, approval_ref=token)
    count = len(protocol.sent)
    store.failpoint = lambda name: None
    assert bridge.send(authority=authority, operation_id=operation, approval_ref=token).state == LedgerState.SEND_UNCERTAIN
    assert len(protocol.sent) == count


@pytest.mark.parametrize('change', ['stop', 'revoke', 'session', 'expiry', 'cancel'])
def test_current_gate_after_claim_before_effect(setup, change):
    store, _, _, authority, _, clock, protocol, bridge = setup
    operation, token = approved(setup)
    def interrupt():
        if change == 'stop':
            store.stop(owner_ref=authority.owner_ref)
        elif change == 'revoke':
            store.revoke(owner_ref=authority.owner_ref, actor_ref=authority.actor_ref)
        elif change == 'session':
            store.set_session(owner_ref=authority.owner_ref, session_ref='session:alpha', version=2)
        elif change == 'expiry':
            clock.current += timedelta(hours=2)
        else:
            store.cancel_action(owner_ref=authority.owner_ref, operation_id=operation)
    protocol.interrupt = interrupt
    result = bridge.send(authority=authority, operation_id=operation, approval_ref=token)
    assert result.state == LedgerState.SEND_UNCERTAIN and protocol.sent == []


def test_privacy_control_db_wal_repr_and_budget(setup):
    store, _, _, authority, _, _, protocol, bridge = setup
    sync(setup)
    for path in (store.path, store.path.with_name(store.path.name + '-wal')):
        if path.exists():
            assert MARKER.encode() not in path.read_bytes()
            assert b'+15550000111' not in path.read_bytes()
    bridge.daily_calls = 2  # Pair plus one sync are already reserved.
    count = len(protocol.calls)
    with pytest.raises(BridgeError, match='budget'):
        bridge.list_chats(authority=authority, account_ref='account:alpha', event_ref='event:exhausted')
    assert len(protocol.calls) == count


def test_no_unapproved_no_cross_owner_no_implicit_live(setup):
    _, _, service, authority, _, _, protocol, bridge = setup
    batch = service.compose(authority=authority, event_ref='event:proposal', account_ref='account:alpha', drafts=(DraftInput(('chat:one',), MARKER, 'purpose:proposal'),), now=NOW)
    with pytest.raises(ConditionalConflict):
        bridge.send(authority=authority, operation_id=batch.operation_ids[0], approval_ref='approval:invented')
    with pytest.raises(ConditionalConflict):
        bridge.send(authority=replace(authority, owner_ref='owner:foreign'), operation_id=batch.operation_ids[0], approval_ref='approval:invented')
    assert protocol.sent == []
    bridge.synthetic_authorized = False
    with pytest.raises(BridgeError, match='live_bridge_disabled'):
        sync(setup)


def test_real_restart_send_replay_never_calls_transport(setup):
    store, _, _, authority, _, _, protocol, bridge = setup
    operation, token = approved(setup)
    confirmed = bridge.send(authority=authority, operation_id=operation, approval_ref=token)
    store.close()
    _, _, reopened = protocol.reopen()
    assert reopened.send(authority=authority, operation_id=operation, approval_ref=token) == confirmed
    assert len(protocol.sent) == 1


def test_two_hosts_same_session_os_lock_prevents_duplicate_io(setup):
    _, _, _, authority, _, _, protocol, bridge = setup
    _, _, other = protocol.reopen()
    entered, release = Event(), Event()
    errors = []
    protocol.interrupt = lambda: (entered.set(), release.wait(5))
    def first():
        try:
            sync(setup)
        except Exception as error:
            errors.append(type(error).__name__)
    thread = Thread(target=first)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises((BridgeError, ConditionalConflict)):
            other.sync(authority=authority, account_ref='account:alpha', event_ref='event:other', enabled_chat_refs=('chat:one',))
    finally:
        release.set()
        thread.join(5)
    assert errors == [] and protocol.calls.count('sync') == 1


def test_revoked_authority_cannot_ack_checkpoint(setup):
    store, _, _, authority, _, _, protocol, bridge = setup
    first = sync(setup)
    store.revoke(owner_ref=authority.owner_ref, actor_ref=authority.actor_ref)
    with pytest.raises(ConditionalConflict):
        sync(setup, 'event:later', ack_ref=first.private_ref)
    assert protocol.acks == ['']


def test_lease_replaced_during_io_prevents_checkpoint(setup):
    store, _, _, authority, _, clock, protocol, _ = setup
    def replace_lease():
        clock.current += timedelta(seconds=131)
        assert store.acquire(owner_ref=authority.owner_ref, session_ref='session:alpha', worker_ref='worker:other', now=clock.now(), ttl=timedelta(seconds=30)) is not None
    protocol.interrupt = replace_lease
    with pytest.raises(ConditionalConflict, match='bridge_lease_changed'):
        sync(setup)
    assert store.db.execute('SELECT count(*) FROM conversation_messages').fetchone() == (0,)


def test_unknown_storage_failure_is_visible_not_confirmed(setup):
    store, _, _, authority, _, _, protocol, bridge = setup
    operation, token = approved(setup)
    protocol.interrupt = lambda: (_ for _ in ()).throw(OSError('synthetic_storage_failure'))
    with pytest.raises(OSError):
        bridge.send(authority=authority, operation_id=operation, approval_ref=token)
    assert store.get(owner_ref=authority.owner_ref, operation_id=operation).state == LedgerState.DISPATCH_COMMITTED
    assert protocol.sent == []


def test_real_helper_private_ipc_default_network_closed(binaries, tmp_path):
    helper = binaries[2]
    rpc = GoBridgeRPC(helper_path=helper, helper_sha256=sha256(helper.read_bytes()).hexdigest())
    from radar.adapters.local.sqlite import stamp
    request = {'v': 1, 'id': 'event:synthetic', 'owner_ref': 'owner:synthetic', 'account_ref': 'account:synthetic', 'session_ref': 'session:synthetic', 'session_version': 1, 'deadline': stamp(utc_now() + timedelta(seconds=20)), 'method': 'send', 'body': {'text': MARKER}}
    with pytest.raises(BridgeError, match='protocol_operation_uncertain') as captured:
        rpc.call(session_dir=tmp_path, request=request, gate=lambda *args: pytest.fail('disabled method reached gate'), authorize_live=False)
    assert MARKER not in str(captured.value) + repr(rpc)
    assert not (tmp_path / 'bridge.db').exists()
    with pytest.raises(BridgeError, match='helper_integrity'):
        GoBridgeRPC(helper_path=helper, helper_sha256='0' * 64)
