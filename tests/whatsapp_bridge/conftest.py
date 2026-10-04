"""Explicit synthetic authority/provider; REAL age encryption, offline helpers."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

import pytest

from radar.adapters.local.private_vault import PrivateVault, create_private_directory
from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.conversations import SQLiteConversations
from radar.adapters.local.whatsapp_bridge import LocalWhatsAppBridge
from radar.adapters.telegram import consent
from radar.application.conversations import Account, Channel, Conversations
from radar.application.tasks.models import Authority, Budget
from radar.ports.types import Capability

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
MARKER = 'PRIVATE_SYNTHETIC_BODY_8293'


@pytest.fixture(scope='session')
def binaries():
    output = ROOT / '.local' / 'whatsapp-crypto'
    output.mkdir(parents=True, exist_ok=True)
    suffix = '.exe' if os.name == 'nt' else ''
    paths = [output / ('vault' + suffix), output / ('keys' + suffix)]
    env = {**os.environ, 'GOPROXY': 'off', 'GOSUMDB': 'off', 'GOWORK': 'off', 'GOTOOLCHAIN': 'local'}
    for target, package in zip(paths, ('./cmd/vault', './cmd/synthetic-keys')):
        process = subprocess.run(['go', 'build', '-mod=readonly', '-trimpath', '-buildvcs=false', '-o', str(target), package], cwd=ROOT / 'helpers/private-vault', env=env, capture_output=True, timeout=60)
        assert process.returncode == 0, 'offline crypto build failed'
    whatsapp = output / ('whatsapp' + suffix)
    process = subprocess.run(['go', 'build', '-mod=readonly', '-trimpath', '-buildvcs=false', '-o', str(whatsapp), '.'], cwd=ROOT / 'helpers/whatsapp-local', env=env, capture_output=True, timeout=60)
    assert process.returncode == 0, 'offline bridge build failed'
    # Offline tests only invoke denied methods; no provider calls/accounts.
    paths.append(whatsapp)
    return paths


class Clock:
    current = NOW
    def now(self):
        return self.current


class Caps:
    def get(self, *, owner_ref, platform, backend, operation, session_ref=None):
        return Capability(platform, backend, operation, 'probado_local', 'synthetic', NOW.date(), True)


class Protocol:
    fixture_only = True
    def __init__(self):
        self.calls, self.codes, self.sent, self.acks = [], [], [], []
        self.interrupt = None
        self.messages = [{'chat_ref': 'chat:one', 'cursor': 'p1', 'provider_message_id': 'SYNTHETIC_ID', 'text': MARKER, 'observed_at': NOW.isoformat()}]
    def call(self, *, session_dir, request, gate, authorize_live):
        assert authorize_live is False
        self.calls.append(request['method'])
        gate('preflight', None)
        method = request['method']
        if method == 'pair':
            gate('pair_code', {'code': 'SYNTHETIC_CODE'})
            return {'paired': True}
        if method == 'send':
            if self.interrupt:
                self.interrupt()
            gate('effect_gate', None)
            self.sent.append(request['body'])
            return {'state': 'provider_confirmed', 'provider_message_id': 'SYNTHETIC_SENT'}
        if method == 'list_chats':
            return {'chats': [{'chat_ref': 'chat:one', 'display_name': MARKER, 'is_self': False}], 'has_more': False}
        self.acks.append(request['body']['ack'])
        if self.interrupt:
            self.interrupt()
        return {'messages': json.loads(json.dumps(self.messages)), 'has_more': False}


@pytest.fixture
def setup(tmp_path, binaries):
    helper, keys_helper, _ = binaries
    root = create_private_directory(tmp_path / 'private')
    keys = create_private_directory(root / 'keys')
    process = subprocess.run([str(keys_helper), '--synthetic-only', str(keys)], capture_output=True, timeout=10)
    assert process.returncode == 0 and process.stdout == process.stderr == b''
    store = SQLiteStore(root / 'control.sqlite')
    owner, actor = 'owner:alpha', 'actor:alpha'
    store.enroll(owner_ref=owner, actor_ref=actor)
    store.db.execute('INSERT INTO directory VALUES(?,?,?,?,?,?)', (1234, owner, actor, 'owner', consent.CONSENT_VERSION, NOW.isoformat()))
    authority = Authority(owner, actor, tuple((op, 'cap:' + op) for op in ('read', 'compose', 'contact')), NOW + timedelta(hours=1), Budget(30, 20), (('session:alpha', 1),))
    for _, cap in authority.capabilities:
        store.allow_source(owner_ref=owner, source_ref=cap, authorized=True)
    store.set_session(owner_ref=owner, session_ref='session:alpha', version=1)
    config = dict(root=create_private_directory(root / 'blobs'), helper=helper,
                  helper_sha256=sha256(helper.read_bytes()).hexdigest(), identity_file=keys / 'identity.agekey',
                  recipient=(keys / 'recipient.txt').read_text().strip(), owner_ref=owner,
                  audiences=('worker:conversations', 'worker:whatsapp-local'), authorize=lambda **kw: True, store=store)
    vault = PrivateVault(**config)
    views = (vault.view('worker:conversations'), vault.view('worker:whatsapp-local'))
    clock, protocol = Clock(), Protocol()
    repo = SQLiteConversations(store, vault=views[0], capabilities=Caps(), clock=clock, channels=(Channel('whatsapp', 'fixture', True, True),), synthetic_authorized=True)
    account = Account('account:alpha', 'whatsapp', 'fixture', 'recipient:self', 'session:alpha', 1, NOW + timedelta(hours=1))
    repo.register_account(authority=authority, account=account, now=NOW)
    identity = views[0].seal(owner_ref=owner, plaintext=json.dumps({'account_ref': account.account_ref, 'chat_ref': 'chat:one', 'recipient_ref': 'recipient:one', 'display': MARKER, 'address': 'SYNTHETIC_ADDRESS'}).encode())
    repo.enable_chat(authority=authority, account_ref=account.account_ref, chat_ref='chat:one', recipient_ref='recipient:one', identity_ref=identity, now=NOW)
    session_root = create_private_directory(root / 'sessions')
    provider = lambda **kw: authority
    bridge = LocalWhatsAppBridge(store=store, conversations=repo, vault=views[1], authority_provider=provider,
                                 clock=clock, transport=protocol, session_root=session_root, synthetic_authorized=True,
                                 pair_authorizer=lambda **kw: True)
    bridge.bind_account(authority=authority, account=account)
    phone = views[1].seal(owner_ref=owner, plaintext=b'{"declared_phone":"+15550000111"}')
    bridge.pair(authority=authority, account_ref=account.account_ref, event_ref='event:pair', phone_ref=phone, notifier=lambda **kw: protocol.codes.append(kw['code']) is None)
    reopened = []
    def reopen():
        other = SQLiteStore(store.path)
        reopened.append(other)
        private = PrivateVault(**{**config, 'store': other})
        other_repo = SQLiteConversations(other, vault=private.view('worker:conversations'), capabilities=Caps(), clock=clock, channels=(Channel('whatsapp', 'fixture', True, True),), synthetic_authorized=True)
        other_bridge = LocalWhatsAppBridge(store=other, conversations=other_repo, vault=private.view('worker:whatsapp-local'), authority_provider=provider, clock=clock, transport=protocol, session_root=session_root, synthetic_authorized=True, pair_authorizer=lambda **kw: True)
        return other, other_repo, other_bridge
    protocol.reopen = reopen
    yield store, repo, Conversations(repo), authority, views[1], clock, protocol, bridge
    for other in reopened:
        other.close()
    store.close()
