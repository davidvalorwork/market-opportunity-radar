"""The same six behavioral cases imported by SQLite and DynamoDB/moto tests."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest


OWNER, OTHER = 'owner:alpha', 'owner:beta'
NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)
TTL = timedelta(seconds=10)


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
