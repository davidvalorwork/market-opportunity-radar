"""Reusable behavioral subset of LeaseStore, Outbox and intake UnitOfWork."""
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

import pytest

from radar.ports.types import ConditionalConflict, OutboxEntry, Receipt

from conftest import ACTORS, NOW, OWNERS


OWNER, OTHER = OWNERS
TTL = timedelta(seconds=10)


def command(owner=OWNER, *, topic='public astronomy articles'):
    """Canonical general command: opaque actor, no product or account requirement."""
    return {'schema_version': 2, 'message_id': str(uuid4()),
            'operation_id': str(uuid4()), 'correlation_id': str(uuid4()),
            'owner_ref': owner, 'kind': 'telegram.command',
            'deadline': '2026-10-04T01:00:00Z', 'attempt': 1,
            'payload': {'schema_version': 1, 'update_id': 1,
                        'telegram_user_ref': ACTORS[owner], 'command': 'pedir',
                        'args': {'input_ref': 'input:synthetic', 'topic_fixture': topic}}}


def receipt(event='event:primary', *, aliases=(), intent='synthetic-intent'):
    # The boundary supplies a semantic hash; generated envelope IDs are not intent.
    return Receipt('telegram', event, sha256(intent.encode()).hexdigest(), aliases)


def entry(doc):
    return OutboxEntry(doc, 'commands.fifo', doc['owner_ref'])


def accept(backend, doc=None, value=None):
    doc = command() if doc is None else doc
    value = receipt('event:' + doc['message_id']) if value is None else value
    return backend.uow.accept_command(owner_ref=doc['owner_ref'], receipt=value,
                                      command=doc, outbox=entry(doc))


def pending(backend, owner=OWNER, limit=100, cursor=None):
    return backend.outbox.pending(owner_ref=owner, limit=limit, cursor=cursor)


def lease(backend, owner=OWNER, worker='worker:first', at=NOW):
    return backend.leases.acquire(owner_ref=owner, session_ref='session:shared',
                                  worker_ref=worker, now=at, ttl=TTL)


def test_lease_excludes_both_same_and_other_workers(backend):
    for invalid in (timedelta(0), timedelta(seconds=-1)):
        with pytest.raises(ValueError):
            backend.leases.acquire(owner_ref=OWNER, session_ref='session:shared',
                                   worker_ref='worker:first', now=NOW, ttl=invalid)
    first = lease(backend)
    assert first is not None
    assert lease(backend) is None
    assert lease(backend, worker='worker:second') is None
    assert backend.leases.is_current(owner_ref=OWNER, lease=first, now=NOW)


def test_release_reacquire_preserves_monotonic_epoch_across_restart(backend):
    first = lease(backend)
    assert backend.leases.release(owner_ref=OWNER, lease=first)
    backend.restart()
    second = lease(backend, worker='worker:second')
    assert second.version > first.version and second.token != first.token
    assert not backend.leases.release(owner_ref=OWNER, lease=first)
    assert backend.leases.is_current(owner_ref=OWNER, lease=second, now=NOW)


def test_expiration_boundary_allows_new_epoch_not_old_renewal(backend):
    first = lease(backend)
    end = NOW + TTL
    assert not backend.leases.is_current(owner_ref=OWNER, lease=first, now=end)
    assert backend.leases.renew(owner_ref=OWNER, lease=first, now=end, ttl=TTL) is None
    second = lease(backend, at=end)
    assert second.version > first.version and second.token != first.token


def test_renew_fences_original_snapshot_and_survives_restart(backend):
    first = lease(backend)
    for invalid in (timedelta(0), timedelta(seconds=-1)):
        with pytest.raises(ValueError):
            backend.leases.renew(owner_ref=OWNER, lease=first, now=NOW, ttl=invalid)
    assert backend.leases.is_current(owner_ref=OWNER, lease=first, now=NOW)
    renewed = backend.leases.renew(owner_ref=OWNER, lease=first,
                                   now=NOW + timedelta(seconds=1), ttl=TTL)
    assert renewed.version > first.version
    backend.restart()
    assert not backend.leases.is_current(owner_ref=OWNER, lease=first, now=NOW)
    assert not backend.leases.release(owner_ref=OWNER, lease=first)
    assert backend.leases.is_current(owner_ref=OWNER, lease=renewed, now=NOW)


def test_forged_lease_cannot_renew_or_release(backend):
    original = lease(backend)
    for change in ({'worker_ref': 'worker:other'},
                   {'token': 'synthetic-wrong-token'}, {'version': 99}):
        forged = replace(original, **change)
        assert not backend.leases.is_current(owner_ref=OWNER, lease=forged, now=NOW)
        assert backend.leases.renew(owner_ref=OWNER, lease=forged, now=NOW, ttl=TTL) is None
        assert not backend.leases.release(owner_ref=OWNER, lease=forged)
    assert backend.leases.is_current(owner_ref=OWNER, lease=original, now=NOW)


def test_same_session_ref_is_independent_per_owner(backend):
    first, second = lease(backend), lease(backend, owner=OTHER)
    assert first is not None and second is not None
    assert not backend.leases.is_current(owner_ref=OTHER, lease=first, now=NOW)
    assert backend.leases.renew(owner_ref=OTHER, lease=first, now=NOW, ttl=TTL) is None
    assert not backend.leases.release(owner_ref=OTHER, lease=first)
    assert backend.leases.is_current(owner_ref=OTHER, lease=second, now=NOW)


def test_outbox_is_owner_scoped_and_durable(backend):
    for limit in (0, 101, True):
        with pytest.raises(ValueError):
            pending(backend, limit=limit)
    a, b = command(), command(OTHER)
    accept(backend, a)
    accept(backend, b)
    backend.restart()
    assert [x.envelope for x in pending(backend).entries] == [a]
    assert [x.envelope for x in pending(backend, OTHER).entries] == [b]


def test_keyset_keeps_position_when_previous_page_published(backend):
    docs = [command() for _ in range(3)]
    for doc in docs:
        accept(backend, doc)
    first = pending(backend, limit=1)
    assert len(first.entries) == 1
    assert tuple(x.envelope for x in first.entries) == (docs[0],)
    assert first.next_cursor is not None
    assert backend.outbox.mark_published(owner_ref=OWNER, message_id=docs[0]['message_id'],
                                         expected_version=0, published_at=NOW)
    backend.restart()
    second = pending(backend, limit=1, cursor=first.next_cursor)
    assert len(second.entries) == 1
    assert tuple(x.envelope for x in second.entries) == (docs[1],)
    assert second.next_cursor is not None
    third = pending(backend, limit=1, cursor=second.next_cursor)
    assert len(third.entries) == 1
    assert tuple(x.envelope for x in third.entries) == (docs[2],)
    assert third.next_cursor is None


def test_outbox_publication_cas_checks_owner_version_and_once(backend):
    doc = command()
    accept(backend, doc)
    for owner, version in ((OTHER, 0), (OWNER, 1)):
        assert not backend.outbox.mark_published(owner_ref=owner, message_id=doc['message_id'],
                                                 expected_version=version, published_at=NOW)
    assert len(pending(backend).entries) == 1
    assert backend.outbox.mark_published(owner_ref=OWNER, message_id=doc['message_id'],
                                         expected_version=0, published_at=NOW)
    assert not backend.outbox.mark_published(owner_ref=OWNER, message_id=doc['message_id'],
                                             expected_version=0, published_at=NOW)
    backend.restart()
    assert not pending(backend).entries


def test_crash_after_external_acceptance_before_mark_preserves_delivery(backend):
    doc = command()
    accept(backend, doc)
    # An observation of an external acceptance, not a broker/emulator or send.
    accepted_message = pending(backend).entries[0].envelope['message_id']
    backend.restart()  # Crash window: no publication CAS has happened.
    again = pending(backend).entries[0]
    assert again.envelope['message_id'] == accepted_message
    assert again.envelope['operation_id'] == doc['operation_id']
    assert backend.outbox.mark_published(owner_ref=OWNER, message_id=accepted_message,
                                         expected_version=again.version, published_at=NOW)
    assert not pending(backend).entries


def test_receipt_all_aliases_replay_original_ids_without_extra_outbox(backend):
    original = command()
    value = receipt(aliases=('event:alias_a', 'event:alias_b'))
    first = accept(backend, original, value)
    assert backend.read_command(OWNER, first.operation_id) == original
    backend.restart()
    assert backend.read_command(OWNER, first.operation_id) == original
    for ref in (value.event_ref, *value.idempotency_refs):
        replay = accept(backend, command(), receipt(ref))
        assert replay.replayed and (replay.operation_id, replay.message_id) == (
            first.operation_id, first.message_id)
        assert backend.read_command(OWNER, first.operation_id) == original
    assert len(pending(backend).entries) == 1


def test_oracle_rejects_overfull_page_with_preserved_cursor(backend, monkeypatch):
    original_pending = backend.outbox.pending

    def overfull(*, owner_ref, limit, cursor=None):
        page = original_pending(owner_ref=owner_ref, limit=limit, cursor=cursor)
        if limit == 1:
            expanded = original_pending(owner_ref=owner_ref, limit=2, cursor=cursor)
            return replace(page, entries=expanded.entries)
        return page

    # Deliberately violate only the page-size contract, keeping original cursors.
    monkeypatch.setattr(backend.outbox, 'pending', overfull)
    with pytest.raises(AssertionError):
        test_keyset_keeps_position_when_previous_page_published(backend)


def test_oracle_rejects_command_missing_only_after_restart(backend, monkeypatch):
    original_restart = backend.restart

    def missing_command(owner, op):
        raise ConditionalConflict('synthetic_missing_command_after_restart')

    def restart_with_missing_command_projection():
        original_restart()
        monkeypatch.setattr(backend, 'read_command', missing_command)

    # Mutate the observation hook, not production SQL: receipts/outbox still exist.
    monkeypatch.setattr(backend, 'restart', restart_with_missing_command_projection)
    with pytest.raises(ConditionalConflict, match='synthetic_missing_command_after_restart'):
        test_receipt_all_aliases_replay_original_ids_without_extra_outbox(backend)


def test_replay_adds_new_receipt_refs_atomically(backend):
    first = accept(backend, value=receipt(aliases=('event:callback',)))
    replay = accept(backend, value=receipt('event:new_update', aliases=('event:callback', 'event:new_alias')))
    assert replay.replayed
    backend.restart()
    for ref in ('event:new_update', 'event:new_alias'):
        replay = accept(backend, value=receipt(ref))
        assert replay.replayed and replay.operation_id == first.operation_id
    assert len(pending(backend).entries) == 1


@pytest.mark.parametrize('stage', ['after_receipt', 'after_command', 'after_outbox', 'before_commit'])
def test_intake_rolls_back_command_outbox_and_every_receipt_ref(backend, stage):
    doc = command()
    value = receipt(aliases=('event:alias_a', 'event:alias_b'))
    backend.fail_at(stage)
    with pytest.raises(RuntimeError, match='synthetic_transaction_fault'):
        accept(backend, doc, value)
    backend.fail_at(None)
    backend.restart()
    assert not pending(backend).entries
    with pytest.raises(ConditionalConflict):
        backend.read_command(OWNER, doc['operation_id'])
    # Different intents on each ref prove none of the receipt indexes survived.
    for ref in (value.event_ref, *value.idempotency_refs):
        accepted = accept(backend, value=receipt(ref, intent=ref))
        assert not accepted.replayed
    assert len(pending(backend).entries) == 3


def test_mismatched_receipt_hash_cannot_bind_a_new_alias(backend):
    first = accept(backend, value=receipt())
    with pytest.raises(ConditionalConflict):
        accept(backend, value=receipt('event:new', aliases=('event:primary',), intent='different'))
    second = accept(backend, value=receipt('event:new', intent='fresh'))
    assert not second.replayed and second.operation_id != first.operation_id
    assert len(pending(backend).entries) == 2


def test_receipt_collision_across_two_operations_cannot_bind_new_ref(backend):
    accept(backend, value=receipt('event:a'))
    accept(backend, value=receipt('event:b'))
    with pytest.raises(ConditionalConflict):
        accept(backend, value=receipt('event:new', aliases=('event:a', 'event:b')))
    assert not accept(backend, value=receipt('event:new', intent='fresh')).replayed
    assert len(pending(backend).entries) == 3


def test_identical_receipt_refs_are_owner_scoped(backend):
    a = accept(backend, value=receipt())
    b = accept(backend, command(OTHER), receipt())
    assert not a.replayed and not b.replayed and a.operation_id != b.operation_id
    assert len(pending(backend).entries) == len(pending(backend, OTHER).entries) == 1


def test_outbox_mismatch_cannot_leave_receipt_or_command(backend):
    doc = command()
    altered = deepcopy(doc)
    altered['payload']['args']['topic_fixture'] = 'a different public topic'
    with pytest.raises(ConditionalConflict):
        backend.uow.accept_command(owner_ref=OWNER, receipt=receipt(), command=doc,
                                   outbox=entry(altered))
    assert not pending(backend).entries
    with pytest.raises(ConditionalConflict):
        backend.read_command(OWNER, doc['operation_id'])
    assert not accept(backend, doc, receipt(intent='fresh')).replayed
