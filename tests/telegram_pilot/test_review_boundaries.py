"""Independent pilot review probes: synthetic content, no external IO."""
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from radar.adapters.local.general_fixture import ACTOR, OWNER, build
from radar.adapters.local.research_cache import SQLiteResearchCache
from radar.adapters.sources.generic.model import Code, SourceFailure
from radar.application.research.cache import CachePolicy, CacheRecord


@pytest.fixture
def pilot_case(tmp_path):
    from radar.adapters.local.pilot_backend import Pilot

    assembly = build(tmp_path / 'review.sqlite')
    controller = Pilot(store=assembly['store'], vault=assembly['views'],
        host_authority=assembly['runtime'].host_authority, clock=assembly['clock'])
    controller.runtime = assembly['runtime']
    controller.clock = assembly['clock']
    controller.cache = SQLiteResearchCache(
        store=controller.store, vault=assembly['views']['worker:research'],
        authorize=lambda binding, now: True)
    authority = controller.runtime.authority(OWNER, ACTOR)
    settings = {'max_items': 2, 'max_requests': 2, 'max_pages': 2,
                'max_bytes': 131072, 'seconds': 60, 'retention_seconds': 86400}
    controller.defaults = settings
    metadata = {'investigation_ref': 'investigation:review', 'topic': 'public science',
                'settings': settings, 'mode': 'continue', 'pass_ref': 'pass:review'}
    private_ref = json.loads(controller.runtime.seal(OWNER, metadata))
    task = controller.propose(authority, 'event:review', 'Review public science',
        [{'step_id': 'search0', 'operation': 'search',
          'arguments': {'private_ref': private_ref, 'query': 'public science'}}])
    proposal = controller.runtime.tasks._row(OWNER, task)
    token = next(button.callback_ref for button in proposal.buttons if button.text == 'Confirmar')
    controller.runtime.callback(OWNER, ACTOR, token, event_ref='event:reviewconfirm')
    controller.review_run = controller.store.db.execute(
        'SELECT ref FROM general_runs WHERE owner=? AND task=?', (OWNER, task)).fetchone()[0]
    yield assembly, controller, authority, metadata
    assembly['store'].close()


def run_search(controller, authority, metadata):
    pointer = controller.runtime.seal(OWNER, metadata)
    step = {'step_id': 'search0', 'operation': 'search',
            'arguments': {'private_ref': json.loads(pointer)}}
    return controller.search_step(runtime=controller.runtime, authority=authority,
        run_ref=controller.review_run, task_ref='task:review', step=step, outputs={})


def test_review_research_transport_timeout_obeys_short_pass_deadline(pilot_case):
    _, controller, authority, metadata = pilot_case
    metadata['settings']['seconds'] = 1
    calls = []

    def search(query, count, *, timeout, max_bytes):
        calls.append(timeout)
        return (('https://example.org/science', b'Title: Science'),), 64

    controller.search = SimpleNamespace(search=search, verified_at=None)
    run_search(controller, authority, metadata)
    assert len(calls) == 1 and 0 < calls[0] <= 1


def test_review_drained_records_survive_following_transport_failure(pilot_case):
    _, controller, authority, metadata = pilot_case
    binding = controller.binding(authority, metadata)
    now = controller.clock.now()
    view = controller.cache.open(binding, pass_ref='pass:seed', mode='continue',
        policy=CachePolicy(deadline=now + timedelta(minutes=1), max_items=1), now=now)
    reservation = controller.cache.reserve(binding, pass_ref='pass:seed',
        request_ref='request:seed', expected_version=view.version, now=now)
    seeded = controller.cache.commit_page(binding, pass_ref='pass:seed',
        request_ref='request:seed', expected_version=reservation.view.version,
        records=(CacheRecord('https://example.org/one', b'Title: One', now),
                 CacheRecord('https://example.org/two', b'Title: Two', now)),
        continuation=b'1', received_bytes=128, now=now)
    assert seeded.view.pending_items == 1

    def unavailable(*args, **kwargs):
        raise SourceFailure(Code.FAILURE)

    controller.search = SimpleNamespace(search=unavailable, verified_at=None)
    result, _, _ = run_search(controller, authority, metadata)
    view = controller.cache.current(binding, now=controller.clock.now())
    assert view.new == 1 and view.pending_items == 0
    assert len(result['cached_records']) == 1


def test_review_committed_cache_replay_recovers_result_without_new_io(pilot_case):
    _, controller, authority, metadata = pilot_case
    calls = []

    def search(query, count, *, timeout, max_bytes):
        calls.append(query)
        return (('https://example.org/science', b'Title: Science'),), 64

    controller.search = SimpleNamespace(search=search, verified_at=None)
    first = run_search(controller, authority, metadata)
    # Simulates cache commit succeeding, but the general step checkpoint not
    # being persisted. The receipt is KNOWN committed, not an unknown send.
    second = run_search(controller, authority, metadata)
    assert len(calls) == 1
    assert second[0]['cached_records'] == first[0]['cached_records']


def test_review_research_command_yields_confirmable_plan(pilot_case):
    _, controller, authority, _ = pilot_case
    task = controller.research_proposal(authority, 'event:newresearch',
                                        'public astronomy', 'new')
    proposal = controller.runtime.tasks._row(OWNER, task)
    assert proposal.status == 'proposed'
    assert any(button.text == 'Confirmar' for button in proposal.buttons)


def test_review_research_topic_accepted_limit_fits_versioned_plan(pilot_case):
    _, controller, authority, _ = pilot_case
    task = controller.research_proposal(authority, 'event:longpublictopic', 'a' * 2000, 'new')
    assert controller.runtime.tasks._row(OWNER, task).status == 'proposed'


def test_review_drained_plus_fetched_refs_survive_host_checkpoint_crash(pilot_case):
    _, controller, authority, metadata = pilot_case
    binding, now = controller.binding(authority, metadata), controller.clock.now()
    view = controller.cache.open(binding, pass_ref='pass:seed', mode='continue',
        policy=CachePolicy(deadline=now + timedelta(minutes=1), max_items=1), now=now)
    reserved = controller.cache.reserve(binding, pass_ref='pass:seed',
        request_ref='request:seed', expected_version=view.version, now=now)
    seeded = controller.cache.commit_page(binding, pass_ref='pass:seed',
        request_ref='request:seed', expected_version=reserved.view.version,
        records=(CacheRecord('https://example.org/one', b'Title: One', now),
                 CacheRecord('https://example.org/two', b'Title: Two', now)),
        continuation=b'1', received_bytes=128, now=now)
    assert seeded.view.pending_items == 1
    calls = []

    def search(query, count, **kwargs):
        calls.append(query)
        return (('https://example.org/three', b'Title: Three'),), 64

    controller.search = SimpleNamespace(search=search, verified_at=None)
    first = run_search(controller, authority, metadata)
    assert len(first[0]['cached_records']) == 2
    # Both refs have now been charged/emitted by the cache, but the host output
    # is not yet checkpointed. Recovery must preserve the drained ref too.
    second = run_search(controller, authority, metadata)
    assert len(calls) == 1
    assert second[0]['cached_records'] == first[0]['cached_records']


def test_review_crash_after_drain_before_reservation_keeps_drained_ref(pilot_case, monkeypatch):
    _, controller, authority, metadata = pilot_case
    binding, now = controller.binding(authority, metadata), controller.clock.now()
    view = controller.cache.open(binding, pass_ref='pass:seed', mode='continue',
        policy=CachePolicy(deadline=now + timedelta(minutes=1), max_items=1), now=now)
    reserved = controller.cache.reserve(binding, pass_ref='pass:seed',
        request_ref='request:seed', expected_version=view.version, now=now)
    controller.cache.commit_page(binding, pass_ref='pass:seed', request_ref='request:seed',
        expected_version=reserved.view.version,
        records=(CacheRecord('https://example.org/one', b'Title: One', now),
                 CacheRecord('https://example.org/two', b'Title: Two', now)),
        continuation=b'1', received_bytes=128, now=now)
    calls, drained = [], []
    original = controller.cache.drain

    def interrupted(*args, **kwargs):
        page = original(*args, **kwargs)
        drained.extend(record.record_ref for record in page.records)
        raise RuntimeError('synthetic host crash after durable drain')

    def search(query, count, **kwargs):
        calls.append(query)
        return (('https://example.org/three', b'Title: Three'),), 64

    controller.search = SimpleNamespace(search=search, verified_at=None)
    monkeypatch.setattr(controller.cache, 'drain', interrupted)
    with pytest.raises(RuntimeError, match='synthetic host crash'):
        run_search(controller, authority, metadata)
    assert len(drained) == 1 and not calls
    monkeypatch.setattr(controller.cache, 'drain', original)
    result = run_search(controller, authority, metadata)
    assert len(calls) == 1
    assert len(result[0]['cached_records']) == 2
    assert result[0]['cached_records'][0] == drained[0]


@pytest.mark.parametrize('allow_self,exact_text,exact_purpose,accepted', [
    (False, True, True, False),
    (True, False, True, False),
    (True, True, False, False),
    (True, True, True, True),
])
def test_review_self_test_exception_requires_host_and_exact_content(
        pilot_case, allow_self, exact_text, exact_purpose, accepted):
    from radar.adapters.local.conversations import SELF_TEST_PURPOSE, SELF_TEST_TEXT
    from radar.application.conversations.models import ConversationError, DraftInput

    _, controller, authority, _ = pilot_case
    repository = controller.runtime.conversations.repository
    repository.allow_self_test = allow_self
    identity = {'account_ref': 'account:fixture', 'chat_ref': 'chat:selfreview',
                'recipient_ref': 'recipient:self', 'display': 'Synthetic self',
                'address': 'synthetic self address'}
    pointer = repository.vault.seal(owner_ref=OWNER,
                                    plaintext=json.dumps(identity).encode())
    repository.enable_chat(authority=authority, account_ref='account:fixture',
        chat_ref='chat:selfreview', recipient_ref='recipient:self',
        identity_ref=pointer, now=controller.clock.now())
    draft = DraftInput(('chat:selfreview',),
        SELF_TEST_TEXT if exact_text else 'Unapproved arbitrary self text',
        SELF_TEST_PURPOSE if exact_purpose else 'purpose:arbitrary')
    arguments = dict(authority=authority, event_ref='event:selfreview',
        account_ref='account:fixture', drafts=(draft,), now=controller.clock.now())
    if accepted:
        batch = controller.runtime.conversations.compose(**arguments)
        assert batch.status != 'approved'
        assert all(controller.store.get(owner_ref=OWNER, operation_id=operation).state.value == 'proposed'
                   for operation in batch.operation_ids)
    else:
        with pytest.raises(ConversationError, match='self_contact_forbidden'):
            controller.runtime.conversations.compose(**arguments)


def test_review_real_age_setup_and_factory_do_not_activate_network(tmp_path, monkeypatch):
    """Actual keygen/vault/config/factory; no bot, Exa, or WhatsApp IO allowed."""
    from hashlib import sha256
    import os
    from pathlib import Path
    import subprocess

    from radar.adapters.local.general_bindings import from_config
    from radar.adapters.local.general_runtime import GeneralError
    from radar.adapters.local.private_vault import create_private_directory
    from radar.adapters.local.pilot_sources import AgentReachExa
    from radar.adapters.local.whatsapp_bridge import GoBridgeRPC
    from radar.adapters.telegram.allowlist import PhoneAllowlist
    from radar.entrypoints import telegram_pilot

    source_root = Path(__file__).resolve().parents[2]
    binaries = create_private_directory(tmp_path / 'review-binaries')
    suffix = '.exe' if os.name == 'nt' else ''
    helper = binaries / ('vault' + suffix)
    keygen = binaries / ('keygen' + suffix)
    whatsapp = binaries / ('whatsapp' + suffix)
    environment = {**os.environ, 'GOPROXY': 'off', 'GOSUMDB': 'off',
                   'GOTOOLCHAIN': 'local', 'GOWORK': 'off'}
    for target, package, directory in (
            (helper, './cmd/vault', source_root / 'helpers/private-vault'),
            (keygen, './cmd/keygen', source_root / 'helpers/private-vault'),
            (whatsapp, '.', source_root / 'helpers/whatsapp-local')):
        result = subprocess.run(['go', 'build', '-mod=readonly', '-trimpath',
            '-buildvcs=false', '-o', str(target), package], cwd=directory,
            env=environment, capture_output=True, timeout=60)
        assert result.returncode == 0, 'offline review helper build failed'

    node = binaries / ('node' + suffix)
    node.write_bytes(b'INERT_SYNTHETIC_NOT_AN_EXECUTABLE')
    script = binaries / 'node_modules/mcporter/dist/cli.js'
    script.parent.mkdir(parents=True)
    script.write_text('INERT_SYNTHETIC_NOT_A_SCRIPT', encoding='utf-8')
    monkeypatch.setattr(telegram_pilot.shutil, 'which', lambda name: str(node))
    source_allowlist = binaries / 'synthetic-allowlist.json'
    source_allowlist.write_text(json.dumps({'schema_version': 1,
        'entries': [{'phone_e164': '+15550000111', 'role': 'owner'}]}), encoding='utf-8')
    private_root = tmp_path / 'review-operator'
    setup_result = telegram_pilot.setup(root=private_root, allowlist_path=source_allowlist,
        vault_helper=helper, keygen_helper=keygen, whatsapp_helper=whatsapp)
    assert setup_result['configured'] and setup_result['llm_enabled'] is False

    def forbidden(*args, **kwargs):
        raise AssertionError('real provider IO forbidden in independent review')

    monkeypatch.setattr(AgentReachExa, 'search', forbidden)
    monkeypatch.setattr(GoBridgeRPC, 'call', forbidden)
    wiring, runtime = from_config(state_db=private_root / 'control.sqlite',
        phone_allowlist=PhoneAllowlist.from_file(private_root / 'allowlist.json'),
        secret=(private_root / 'webhook-secret.txt').read_text(),
        config_path=private_root / 'config.json', api=None)
    try:
        assert runtime.router.parser_enabled is False
        assert wiring.vault.preflight(owner_ref=wiring.owner_ref) is True
        with pytest.raises(GeneralError):
            runtime.authority(wiring.owner_ref, 'actor:unknownreview')
        assert not wiring.store.db.execute('SELECT 1 FROM general_inputs').fetchone()
        wiring.tick()  # No enrolled/consented owner: no source/provider IO.
        assert sha256(helper.read_bytes()).hexdigest() == json.loads(
            (private_root / 'config.json').read_text())['vault']['helper_sha256']
    finally:
        wiring.store.close()


def test_review_observed_self_chat_matches_a10_self_recipient(pilot_case):
    from hashlib import sha256
    from radar.adapters.local.general_fixture import MemoryView

    assembly, controller, authority, _ = pilot_case
    controller.wa_vault = MemoryView(controller.store, 'worker:whatsapp', assembly['contents'])
    controller.save(authority, 'account', 'active', {'account_ref': 'account:fixture'})
    private_ref = controller.wa_vault.seal(owner_ref=OWNER, plaintext=json.dumps({
        'chats': [{'chat_ref': 'chat:observedself', 'display_name': 'Synthetic self',
                   'is_self': True}], 'has_more': False}).encode())
    controller.bridge = SimpleNamespace(list_chats=lambda **kwargs:
        SimpleNamespace(private_ref=private_ref))
    chats = controller._chats(authority, 'event:observedself')
    controller.enable(authority, next(iter(chats)))
    account = controller.runtime.conversations.repository._check(
        authority, 'account:fixture', 'read', controller.clock.now())
    expected = 'recipient:r' + sha256(b'account:fixturechat:observedself').hexdigest()
    assert account.self_recipient_ref == expected
    recipient, _, _ = controller.runtime.conversations.repository._chat(
        OWNER, 'account:fixture', 'chat:observedself')
    assert recipient == account.self_recipient_ref


@pytest.mark.parametrize('operation', ['send', 'compose'])
def test_review_pairing_does_not_claim_operation_proven_real(pilot_case, operation):
    from radar.adapters.local.pilot_sources import PilotCapabilities

    assembly, _, _, _ = pilot_case
    capabilities = PilotCapabilities(owner=OWNER, clock=assembly['clock'])
    capabilities.whatsapp_verified = lambda session: True
    capability = capabilities.get(owner_ref=OWNER, platform='whatsapp',
        backend='local_wameow', operation=operation, session_ref='session:fixture')
    # Pairing establishes account access, not operation success. An explicit
    # bounded self-test bootstrap is separate from this evidence claim.
    assert capability.status != 'probado_real'


@pytest.mark.parametrize('is_self,exact_text,accepted', [
    (True, True, True), (False, True, False), (True, False, False),
])
def test_review_documented_session_trial_is_only_exact_self_draft(
        pilot_case, is_self, exact_text, accepted):
    from dataclasses import replace
    from radar.adapters.local.conversations import SELF_TEST_PURPOSE, SELF_TEST_TEXT
    from radar.application.conversations.models import Channel, ConversationError, DraftInput
    from radar.ports.types import Capability

    _, controller, authority, _ = pilot_case
    repository, now = controller.runtime.conversations.repository, controller.clock.now()
    account = repository._check(authority, 'account:fixture', 'read', now)
    account = replace(account, backend='local_wameow')
    repository.channels[('whatsapp', 'local_wameow')] = Channel('whatsapp', 'local_wameow', True, False)
    repository.register_account(authority=authority, account=account, now=now)
    repository.capabilities = SimpleNamespace(get=lambda **values: Capability(
        'whatsapp', 'local_wameow', values['operation'], 'documentado',
        'proof:pairedsession', now.date(), True))
    repository.allow_self_test = True
    repository.session_trial_authorizer = lambda **values: True
    recipient = 'recipient:self' if is_self else 'recipient:another'
    identity = {'account_ref': account.account_ref, 'chat_ref': 'chat:trialreview',
        'recipient_ref': recipient, 'display': 'Synthetic trial', 'address': 'synthetic address'}
    pointer = repository.vault.seal(owner_ref=OWNER, plaintext=json.dumps(identity).encode())
    repository.enable_chat(authority=authority, account_ref=account.account_ref,
        chat_ref='chat:trialreview', recipient_ref=recipient, identity_ref=pointer, now=now)
    arguments = dict(authority=authority, event_ref='event:trialreview',
        account_ref=account.account_ref, drafts=(DraftInput(('chat:trialreview',),
        SELF_TEST_TEXT if exact_text else 'Arbitrary text', SELF_TEST_PURPOSE),), now=now)
    if accepted:
        batch = controller.runtime.conversations.compose(**arguments)
        assert batch.status == 'proposed'
    else:
        with pytest.raises(ConversationError, match='trial_self_test_only'):
            controller.runtime.conversations.compose(**arguments)
