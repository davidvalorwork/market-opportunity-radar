"""Real SQLite + real age, synthetic local identities, no network."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from radar.adapters.local.private_vault import PrivateVault
from radar.adapters.local.research_cache import SQLiteResearchCache
from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.telegram import Directory
from radar.adapters.telegram import consent
from radar.application.research.cache import CacheBinding, CachePolicy, CacheRecord, fingerprint
from tests.private_vault.conftest import binaries, vault_factory  # noqa: F401

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
OWNER, ACTOR = 'owner:synthetic', 'user:synthetic'


@dataclass
class Harness:
    cache: SQLiteResearchCache
    store: SQLiteStore
    config: dict
    binding: CacheBinding
    allowed: list
    path: object

    def restart(self):
        self.store.db.close()
        self.store = SQLiteStore(self.path)
        self.cache = SQLiteResearchCache(store=self.store,
            vault=PrivateVault(**self.config).view('worker:research'),
            authorize=lambda binding, now: self.allowed[0])
        return self.cache


@pytest.fixture
def harness(tmp_path, vault_factory):
    path = tmp_path / 'control.sqlite'
    store = SQLiteStore(path)
    directory = Directory(store)
    directory.enroll_synthetic(101, owner_ref=OWNER, actor_ref=ACTOR)
    directory.accept_consent(ACTOR, consent.CONSENT_VERSION, NOW, ('consent:synthetic',))
    backend, config = vault_factory(owner=OWNER)
    allowed = [True]
    binding = CacheBinding(OWNER, ACTOR, 'task:astronomy', 'source:public', fingerprint(OWNER, b'astronomy'))
    cache = SQLiteResearchCache(store=store, vault=backend.view('worker:research'), authorize=lambda binding, now: allowed[0])
    result = Harness(cache, store, config, binding, allowed, path)
    yield result
    result.store.db.close()


def policy(**changes):
    return CachePolicy(deadline=NOW + timedelta(hours=1), **changes)


def record(name='first', *, body=None, observed=NOW):
    return CacheRecord('https://example.test/' + name, body or name.encode(), observed)


def opened(h, *, pass_ref='pass:first', mode='continue', now=NOW, rules=None):
    try:
        version = h.cache.current(h.binding, now=now).version
    except Exception:
        version = None
    return h.cache.open(h.binding, pass_ref=pass_ref, mode=mode, policy=rules or policy(),
                        now=now, expected_version=version)


def page(h, records, *, pass_ref='pass:first', request='request:first', now=NOW,
         cursor=b'private cursor', report=None, size=None):
    current = h.cache.current(h.binding, now=now)
    reservation = h.cache.reserve(h.binding, pass_ref=pass_ref, request_ref=request,
                                  expected_version=current.version, now=now)
    return h.cache.commit_page(h.binding, pass_ref=pass_ref, request_ref=request,
        expected_version=reservation.view.version, records=records, continuation=cursor,
        received_bytes=size if size is not None else sum(len(r.content) for r in records),
        report_bytes=report, now=now)
