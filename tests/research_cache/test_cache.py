from dataclasses import replace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import gzip
import json

import pytest

from radar.adapters.local.research_cache import MAX_FRAME, SQLiteResearchCache, canonical_url
from radar.application.research.cache import CacheBinding, CacheError, CachePolicy, fingerprint
from .conftest import NOW, OWNER, ACTOR, opened, page, policy, record


def test_restart_cursor_report_and_url_seen(harness):
    h = harness
    opened(h)
    result = page(h, (record(),), report=b'{"private":"synthetic report"}')
    h.restart()
    assert h.cache.continuation(h.binding, now=NOW) == b'private cursor'
    assert h.cache.report(h.binding, now=NOW) == b'{"private":"synthetic report"}'
    assert h.cache.seen_url(h.binding, url='HTTPS://EXAMPLE.TEST:443/first#fragment', now=NOW)
    assert h.cache.read_record(h.binding, record_ref=result.records[0].record_ref, now=NOW) == record()
    assert h.cache.current(h.binding, now=NOW).new == 1


def test_repeated_request_and_page_have_no_double_charge_or_blobs(harness):
    h = harness
    start = opened(h)
    first = h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=start.version, now=NOW)
    replay = h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=start.version, now=NOW)
    assert replay.replayed and replay.view == first.view
    args = dict(pass_ref='pass:first', request_ref='request:first', expected_version=first.view.version,
                records=(record(),), continuation=b'cursor', received_bytes=5, now=NOW, report_bytes=b'report')
    original = h.cache.commit_page(h.binding, **args)
    count = len(list(h.config['root'].glob('*.age')))
    again = h.cache.commit_page(h.binding, **args)
    assert again.replayed and again.records == original.records and again.view == original.view
    assert len(list(h.config['root'].glob('*.age'))) == count
    with pytest.raises(CacheError, match='replay_mismatch'):
        h.cache.commit_page(h.binding, **{**args, 'continuation': None})


def test_new_pass_drains_buffer_without_rereading(harness):
    h = harness
    opened(h, rules=policy(max_items=1))
    first = page(h, (record('one'), record('two'), record('three')))
    assert len(first.records) == 1 and first.view.pending_items == 2
    h.restart()
    start = opened(h, pass_ref='pass:more', rules=policy(max_items=2))
    assert start.new == 0 and start.requests == 0
    with pytest.raises(CacheError, match='read_blocked'):
        h.cache.reserve(h.binding, pass_ref='pass:more', request_ref='request:more', expected_version=start.version, now=NOW)
    result = h.cache.drain(h.binding, pass_ref='pass:more', expected_version=start.version, now=NOW)
    assert len(result.records) == 2 and result.view.new == 2 and result.view.pending_items == 0
    assert result.view.pages == result.view.requests == result.view.bytes_used == 0
    assert h.cache.continuation(h.binding, now=NOW) == b'private cursor'


def test_unchanged_content_dedupes_across_passes(harness):
    h = harness
    opened(h)
    first = page(h, (record(),), report=b'report')
    count = len(list(h.config['root'].glob('*.age')))
    opened(h, pass_ref='pass:more')
    second = page(h, (record(), record()), pass_ref='pass:more', request='request:more', report=b'report')
    assert second.records == () and second.view.inspected == 2 and second.view.skipped == 2 and second.view.new == 0
    assert len(list(h.config['root'].glob('*.age'))) == count
    assert h.cache.lookup(h.binding, url=record().url, now=NOW).private_ref == first.records[0].private_ref


@pytest.mark.parametrize('field,value', [('owner_ref', 'owner:foreign'), ('actor_ref', 'user:foreign'),
    ('task_ref', 'task:other'), ('source_ref', 'source:other'), ('query_sha256', 'a'*64)])
def test_exact_binding_cannot_read_existing_stream(harness, field, value):
    h = harness
    opened(h)
    result = page(h, (record(),))
    binding = replace(h.binding, **{field:value})
    with pytest.raises(CacheError):
        h.cache.read_record(binding, record_ref=result.records[0].record_ref, now=NOW)


def test_account_session_version_are_part_of_binding(harness):
    h = harness
    h.binding = replace(h.binding, account_ref='account:alpha', session_ref='whatsapp:alpha', session_version=1)
    opened(h)
    result = page(h, (record(),))
    with pytest.raises(CacheError, match='unknown_cache_binding'):
        h.cache.read_record(replace(h.binding, session_version=2), record_ref=result.records[0].record_ref, now=NOW)


def test_authority_is_fresh_for_every_access(harness):
    h = harness
    opened(h)
    result = page(h, (record(),))
    h.allowed[0] = False
    for call in (lambda: h.cache.current(h.binding, now=NOW),
                 lambda: h.cache.seen_url(h.binding, url=record().url, now=NOW),
                 lambda: h.cache.read_record(h.binding, record_ref=result.records[0].record_ref, now=NOW)):
        with pytest.raises(CacheError, match='cache_access_denied'):
            call()


def test_exact_ttl_boundary_requires_refresh_and_no_extension_on_continue(harness):
    h = harness
    opened(h, rules=policy(retention_seconds=10))
    page(h, (record(),))  # uses pass retention=10
    current = h.cache.current(h.binding, now=NOW)
    opened(h, pass_ref='pass:more', now=NOW+timedelta(seconds=5))
    assert h.cache.current(h.binding, now=NOW).expires_at == current.expires_at
    late = NOW+timedelta(seconds=10)
    assert h.cache.lookup(h.binding, url=record().url, now=late) is None
    with pytest.raises(CacheError, match='expired_refresh_required'):
        opened(h, pass_ref='pass:late', now=late)
    refreshed = opened(h, pass_ref='pass:refresh', mode='refresh', now=late)
    assert refreshed.continuation_ref is None and refreshed.report_ref is None


def test_cache_only_never_grants_network_reservation(harness):
    h = harness
    start = opened(h, mode='cache-only')
    assert start.state == 'cache_only'
    with pytest.raises(CacheError, match='read_blocked'):
        h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=start.version, now=NOW)


@pytest.mark.parametrize('limits,expected', [({'max_pages':1},'page_limit'), ({'max_requests':1},'request_limit'), ({'max_bytes':5},'byte_limit')])
def test_budget_limits_stop_next_request(harness, limits, expected):
    h = harness
    opened(h, rules=policy(**limits))
    result = page(h, (record(),))
    assert result.view.state == expected
    with pytest.raises(CacheError, match='read_blocked'):
        h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:next', expected_version=result.view.version, now=NOW)


def test_unknown_io_is_durable_not_automatically_reissued(harness):
    h = harness
    start = opened(h, rules=policy(max_bytes=100))
    reserved = h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=start.version, now=NOW)
    h.restart()
    replay = h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=start.version, now=NOW)
    assert replay.replayed and replay.state == 'reserved' and replay.view.bytes_used == 100
    with pytest.raises(CacheError, match='request_uncertain'):
        opened(h, pass_ref='pass:more')
    result = h.cache.abandon(h.binding, request_ref='request:first', expected_version=reserved.view.version, now=NOW)
    assert result.bytes_used == 100
    with pytest.raises(CacheError, match='version_conflict'):
        h.cache.commit_page(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=result.version,
                            records=(), continuation=None, received_bytes=0, now=NOW)


def test_stale_cas_and_deadline_do_not_mutate(harness):
    h = harness
    start = opened(h)
    reserved = h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=start.version, now=NOW)
    with pytest.raises(CacheError, match='version_conflict'):
        h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:other', expected_version=start.version, now=NOW)
    with pytest.raises(CacheError, match='cache_deadline'):
        h.cache.commit_page(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=reserved.view.version,
                            records=(), continuation=None, received_bytes=0, now=NOW+timedelta(hours=1))
    assert h.cache.current(h.binding, now=NOW).requests == 1


def test_marker_and_query_urls_never_persist_in_plaintext(harness, capsys):
    h = harness
    opened(h)
    marker = b'SYNTHETIC private phone +58-000-7654321'
    item = replace(record(), url='https://example.test/item?private='+marker.decode().replace(' ','%20'), content=marker*100)
    result = page(h, (item,), cursor=marker, report=marker)
    assert h.cache.read_record(h.binding, record_ref=result.records[0].record_ref, now=NOW).content == marker*100
    for path in h.path.parent.glob('control.sqlite*'):
        assert marker not in path.read_bytes() and b'private=SYNT' not in path.read_bytes()
    assert marker.decode() not in repr(item)+repr(result)
    assert capsys.readouterr().out == ''
    assert len(list(h.config['root'].glob('*.age'))) == 3
    assert sum(p.stat().st_size for p in h.config['root'].glob('*.age')) < len(item.content)


def test_bounded_gzip_and_trailing_members(harness):
    h = harness
    for content in (gzip.compress(b'x'*(MAX_FRAME+1)), gzip.compress(b'{}')+gzip.compress(b'{}')):
        pointer = h.cache.vault.seal(owner_ref=OWNER, plaintext=content)
        with pytest.raises(CacheError, match='cache_frame_limit'):
            h.cache._open(h.binding, pointer)


def test_receipt_distinguishes_empty_cursor_from_none(harness):
    h = harness
    opened(h)
    result = page(h, (), cursor=b'')
    with pytest.raises(CacheError, match='replay_mismatch'):
        h.cache.commit_page(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=result.view.version,
                            records=(), continuation=None, received_bytes=0, now=NOW)


def test_revoke_during_crypto_rolls_back_index(harness, monkeypatch):
    h = harness
    opened(h)
    original = h.cache.vault.seal
    def revoked(**kwargs):
        result = original(**kwargs)
        h.allowed[0] = False
        return result
    monkeypatch.setattr(h.cache.vault, 'seal', revoked)
    with pytest.raises(CacheError, match='cache_access_denied'):
        page(h, (record(),), cursor=None)
    h.allowed[0] = True
    assert not h.cache.seen_url(h.binding, url=record().url, now=NOW)
    assert h.cache.current(h.binding, now=NOW).state == 'in_flight'


@pytest.mark.parametrize('url', ['https://user:secret@example.test/', 'file:///secret', 'https://example.test/a b'])
def test_invalid_url_diagnostics_do_not_echo_input(url):
    with pytest.raises(CacheError) as caught:
        canonical_url(url)
    assert str(caught.value) == 'invalid_cache_url'


def test_conservative_url_identity():
    assert canonical_url('HTTPS://EXAMPLE.TEST:443/path?a=1#x') == 'https://example.test/path?a=1'
    assert fingerprint(OWNER,b'a') != fingerprint('owner:foreign',b'a')
    assert canonical_url('https://example.test/?a=1') != canonical_url('https://example.test/?a=2')


def test_concurrent_reservation_one_cas_wins(harness):
    h = harness
    start = opened(h)
    barrier = Barrier(2)
    def reserve(name):
        barrier.wait(timeout=5)
        try:
            return h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:'+name,
                                   expected_version=start.version, now=NOW)
        except CacheError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ('one','two')))
    assert sum(r is not None for r in results) == 1
    assert h.cache.current(h.binding, now=NOW).requests == 1


def test_same_pass_replay_keeps_quota_and_rejects_policy_change(harness):
    h = harness
    opened(h, rules=policy(max_items=1))
    result = page(h, (record(),))
    h.restart()
    assert opened(h, rules=policy(max_items=1)) == result.view
    with pytest.raises(CacheError, match='pass_replay_mismatch'):
        opened(h, rules=policy(max_items=2))


def test_byte_overrun_and_record_quota_rollback(harness):
    h = harness
    start = opened(h, rules=policy(max_bytes=2))
    reservation = h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=start.version, now=NOW)
    with pytest.raises(CacheError, match='byte_limit'):
        h.cache.commit_page(h.binding, pass_ref='pass:first', request_ref='request:first',
            expected_version=reservation.view.version, records=(record(),), continuation=None, received_bytes=5, now=NOW)
    assert h.cache.current(h.binding, now=NOW).bytes_used == 2
    assert not h.cache.seen_url(h.binding, url=record().url, now=NOW)


def test_storage_quotas_are_bounded(harness):
    h = harness
    h.cache = SQLiteResearchCache(store=h.store, vault=h.cache.vault, authorize=h.cache.authorize,
                                 max_records_per_owner=1, max_streams_per_owner=1)
    opened(h)
    with pytest.raises(CacheError, match='record_quota'):
        page(h, (record('one'), record('two')), cursor=None)
    assert not h.cache.seen_url(h.binding, url=record('one').url, now=NOW)
    h.cache.abandon(h.binding, request_ref='request:first', expected_version=h.cache.current(h.binding,now=NOW).version, now=NOW)
    with pytest.raises(CacheError, match='pass_quota'):
        opened(h, pass_ref='pass:next')


def test_refresh_rechecks_content_without_duplicate_ciphertext(harness):
    h = harness
    opened(h, rules=policy(retention_seconds=5))
    first = page(h, (record(),), cursor=None)
    count = len(list(h.config['root'].glob('*.age')))
    later = NOW+timedelta(seconds=5)
    opened(h, pass_ref='pass:refresh', mode='refresh', now=later)
    assert h.cache.lookup(h.binding, url=record().url, now=later, mode='refresh') is None
    renewed = page(h, (record(observed=later),), pass_ref='pass:refresh', request='request:refresh', now=later, cursor=None)
    assert renewed.view.skipped == 1 and renewed.records == ()
    assert h.cache.lookup(h.binding, url=record().url, now=later).private_ref == first.records[0].private_ref
    assert len(list(h.config['root'].glob('*.age'))) == count


@pytest.mark.parametrize('change', [{'max_items':0}, {'max_bytes':True}, {'max_pages':1001}, {'retention_seconds':2592001}])
def test_invalid_budget_fails_closed(change):
    with pytest.raises(CacheError, match='invalid_cache_budget'):
        policy(**change)


def test_recover_committed_page_after_host_checkpoint_crash(harness):
    h = harness
    assert h.cache.recover_page(h.binding, pass_ref='pass:first', request_ref='request:absent', now=NOW) is None
    opened(h, rules=policy(max_items=1))
    original = page(h, (record('one'), record('two')))
    count = len(list(h.config['root'].glob('*.age')))
    h.restart()  # host checkpoint not recorded, same trusted request ID retried
    recovered = h.cache.recover_page(h.binding, pass_ref='pass:first', request_ref='request:first', now=NOW)
    assert recovered.replayed and recovered.records == original.records
    assert recovered.view == original.view and recovered.view.state == 'item_limit'
    assert len(list(h.config['root'].glob('*.age'))) == count
    assert h.cache.read_record(h.binding, record_ref=recovered.records[0].record_ref, now=NOW) == record('one')
    assert h.cache.recover_page(h.binding, pass_ref='pass:first', request_ref='request:absent', now=NOW) is None
    assert h.cache.current(h.binding, now=NOW) == original.view


def test_recover_reserved_or_uncertain_never_reissues_io(harness):
    h = harness
    start = opened(h)
    reservation = h.cache.reserve(h.binding, pass_ref='pass:first', request_ref='request:first', expected_version=start.version, now=NOW)
    for abandon in (False, True):
        if abandon:
            h.cache.abandon(h.binding, request_ref='request:first', expected_version=reservation.view.version, now=NOW)
        before = h.cache.current(h.binding, now=NOW)
        with pytest.raises(CacheError, match='cache_request_uncertain'):
            h.cache.recover_page(h.binding, pass_ref='pass:first', request_ref='request:first', now=NOW)
        assert h.cache.current(h.binding, now=NOW) == before


def test_recover_revalidates_authority_binding_and_expiry(harness):
    h = harness
    opened(h, rules=policy(retention_seconds=5))
    page(h, (record(),))
    with pytest.raises(CacheError, match='pass_replay_mismatch'):
        h.cache.recover_page(h.binding, pass_ref='pass:other', request_ref='request:first', now=NOW)
    with pytest.raises(CacheError, match='unknown_cache_binding'):
        h.cache.recover_page(replace(h.binding, task_ref='task:other'), pass_ref='pass:first', request_ref='request:first', now=NOW)
    with pytest.raises(CacheError, match='expired_refresh_required'):
        h.cache.recover_page(h.binding, pass_ref='pass:first', request_ref='request:first', now=NOW+timedelta(seconds=5))
    h.allowed[0] = False
    with pytest.raises(CacheError, match='cache_access_denied'):
        h.cache.recover_page(h.binding, pass_ref='pass:first', request_ref='request:first', now=NOW)
