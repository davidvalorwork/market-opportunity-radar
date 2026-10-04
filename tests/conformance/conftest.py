"""Lifecycle/fault hooks for control-port conformance; only SQLite is registered.

A future backend supplies this fixture's factory, not a fake Dynamo implementation.
Tests deliberately access ports and hooks, never SQL or SQLite transaction details.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import pytest

from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.telegram import Directory, consent
from radar.ports.interfaces import LeaseStore, Outbox, UnitOfWork


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)
OWNERS = ('owner:alpha', 'owner:beta')
ACTORS = dict(zip(OWNERS, ('user:alpha', 'user:beta')))


@dataclass
class ControlBackend:
    """Backend-independent ports plus test-only durable lifecycle/fault hooks."""

    uow: UnitOfWork
    outbox: Outbox
    leases: LeaseStore
    restart: Callable[[], None]
    fail_at: Callable[[str | None], None]
    read_command: Callable[[str, str], dict]
    close: Callable[[], None]


def sqlite_factory(path):
    fault = [None]

    def failpoint(stage):
        if stage == fault[0]:
            raise RuntimeError('synthetic_transaction_fault')

    store = SQLiteStore(path, failpoint=failpoint)
    directory = Directory(store)
    for numeric, owner in enumerate(OWNERS, 100):
        directory.enroll_synthetic(numeric, owner_ref=owner, actor_ref=ACTORS[owner])
        directory.accept_consent(ACTORS[owner], consent.CONSENT_VERSION, NOW,
                                 ('consent:' + owner,))

    def restart():
        nonlocal store
        store.close()
        store = SQLiteStore(path, failpoint=failpoint)
        target.uow = target.outbox = target.leases = store

    def fail_at(stage):
        fault[0] = stage

    target = ControlBackend(store, store, store, restart, fail_at,
                            lambda owner, op: store.command(owner_ref=owner, operation_id=op),
                            lambda: store.close())
    return target


@pytest.fixture(params=[sqlite_factory], ids=['sqlite'])
def backend_factory(request):
    """Register another actual backend factory here to exercise the same tests."""
    return request.param


@pytest.fixture
def backend(backend_factory, tmp_path):
    target = backend_factory(tmp_path / 'control.sqlite')
    yield target
    target.close()
