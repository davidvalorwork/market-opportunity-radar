"""Existing A6/A8/A9 APIs with REAL vault; source/parser/access are named fixtures."""
from base64 import b64decode
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from radar import contracts
from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.tasks import SQLiteTaskStore
from radar.adapters.local.telegram import Directory
from radar.adapters.sources.generic import RawItem, ReadOperation, ReadRequest, Reader, Registry, SourceSpec
from radar.adapters.sources.generic.fixtures import FixtureAccess, FixtureCapabilities, FixtureTransport, fixture_page
from radar.adapters.telegram import consent
from radar.application.tasks import Authority, TaskRouter
from radar.application.research import ReportBuilder, SourceMaterial
from radar.adapters.local.private_vault import VaultError
from conftest import OWNER

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)
ACTOR = 'user:synthetic'
MARKER = 'SYNTHETIC Elena +58-000-123-4567'


def test_a6_real_context_snapshot_parser_result_confirm_restart_private_control(vault_factory, tmp_path):
    store = SQLiteStore(tmp_path / 'control.sqlite')
    directory = Directory(store)
    directory.enroll_synthetic(1, owner_ref=OWNER, actor_ref=ACTOR)
    directory.accept_consent(ACTOR, consent.CONSENT_VERSION, NOW, ('consent:synthetic',))
    store.allow_source(owner_ref=OWNER, source_ref='cap:inform', authorized=True)
    backend, config = vault_factory(store=store)
    task_view = backend.view('worker:task-router')
    context = task_view.seal(owner_ref=OWNER, plaintext=MARKER.encode())
    authority = Authority(OWNER, ACTOR, (('inform','cap:inform'),), NOW+timedelta(hours=1))
    request = {'schema_version': 1, 'goal': MARKER, 'privacy_scope': 'personal',
               'context_refs': [context.__dict__], 'steps': [{'step_id': 'note', 'operation': 'inform', 'arguments': {'topic': 'Astronomía y música'}}],
               'missing_fields': [], 'confidence': 1, 'budget': {'calls': 1, 'message_limit': 0, 'max_usd': '0.10'}}
    proposals = SQLiteTaskStore(store, vault=task_view)
    router = TaskRouter(proposals, validate_document=contracts.validate)
    initial = router.propose(authority=authority, event_ref='event:request', document=request, now=NOW)
    token = next(b.callback_ref for b in initial.buttons if b.text == 'Confirmar')
    confirmed = router.callback(authority=authority, callback_ref=token, event_ref='event:confirm', now=NOW)
    assert confirmed.status == 'confirmed' and MARKER in confirmed.snapshot
    # Store a validated fixture parser result through the real private boundary;
    # no parser/model is invoked or installed in production.
    fingerprint = sha256(b'SYNTHETIC input by reference').hexdigest()
    assert proposals.claim_parse(authority=authority,event_ref='event:parse',input_hash=fingerprint,
                                 context_refs=[context.__dict__],max_cost=Decimal('0.01'),now=NOW) is None
    proposals.finish_parse(authority=authority,event_ref='event:parse',input_hash=fingerprint,document=request,now=NOW)
    assert MARKER not in store.db.execute('SELECT result FROM task_router_parses').fetchone()[0]
    # Recreate real vault, not a process-local map.
    restarted = type(backend)(**config)
    loaded = SQLiteTaskStore(store, vault=restarted.view('worker:task-router')).get(authority=authority, task_ref=confirmed.task_ref, now=NOW)
    assert loaded.content_hash == confirmed.content_hash and loaded.document == confirmed.document
    control = store.db.execute('SELECT snapshot FROM task_router_proposals').fetchone()[0]
    assert MARKER not in control and 'private_ref' in control
    assert all(MARKER.encode() not in row[0] and row[0].startswith(b'age-encryption.org/v1\n')
               for row in store.db.execute('SELECT content FROM blobs').fetchall())
    assert store.db.execute('SELECT count(*) FROM ledger').fetchone()[0] == 0
    store.close()
    assert MARKER.encode() not in (tmp_path / 'control.sqlite').read_bytes()


def test_a6_outer_rollback_keeps_cipher_orphan_but_never_admits_reference(vault_factory, tmp_path):
    store = SQLiteStore(tmp_path / 'control.sqlite')
    store.enroll(owner_ref=OWNER, actor_ref=ACTOR)
    backend, config = vault_factory(store=store)
    pointer = None
    with pytest.raises(RuntimeError):
        with store.transaction():
            pointer = backend.view('worker:task-router').seal(owner_ref=OWNER, plaintext=MARKER.encode())
            raise RuntimeError('synthetic rollback')
    assert store.db.execute('SELECT count(*) FROM blobs').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM private_vault_refs').fetchone()[0] == 0
    assert (config['root'] / pointer.blob_key).exists()
    with pytest.raises(VaultError, match='integrity_rejected'):
        backend.view('worker:task-router').open(owner_ref=OWNER, pointer=pointer)
    store.close()


def test_a8_to_a9_real_age_explicit_boundary_preserves_source_audience(vault_factory):
    backend, _ = vault_factory()
    sink = backend.view('worker:sources').source_sink()
    spec = SourceSpec('source:articles','fixture','memory','fixture', (ReadOperation('search','fixture_search'),), min_interval_seconds=0)
    body = b'SYNTHETIC astronomy note.\nSYNTHETIC music note.'
    transport = FixtureTransport({'source:articles': (fixture_page((RawItem(body, 'https://example.org/private?synthetic=marker'),)),)})
    reader = Reader(registry=Registry((spec,)), capabilities=FixtureCapabilities(NOW), access=FixtureAccess(),
                    private_sink=sink, transports={'fixture':transport}, now=lambda:NOW)
    report = reader.read(ReadRequest(OWNER,ACTOR,'request:fixture','source:articles','search',NOW+timedelta(minutes=1)))
    assert report.complete and len(report.records) == 1
    record = report.records[0]
    assert record.owner_ref == OWNER and record.private_ref.recipient_scope == 'worker:sources'
    raw = json.loads(sink.read(owner_ref=OWNER, pointer=record.private_ref))
    assert b64decode(raw['content_b64']) == body
    class FixtureResolver:
        def resolve(self, *, owner_ref, source_ref, max_bytes, max_cost):
            assert owner_ref == record.owner_ref and source_ref == record.record_ref
            payload = json.loads(sink.read(owner_ref=owner_ref,pointer=record.private_ref,max_bytes=max_bytes))
            content = b64decode(payload['content_b64'],validate=True)
            return SourceMaterial(owner_ref,source_ref,'cap:read',NOW,content,sha256(content).hexdigest(),'fixture',
                                  Decimal(0),record.private_ref,evidence_format='content_b64_json')
    class FixtureReportAccess:
        def check_report(self, **kwargs): pass
        def check_source(self, **kwargs): pass
        def reserve_read(self, **kwargs): pass
    class FixtureClock:
        def now(self): return NOW
    from radar.application.research.models import ResearchRequest
    request = ResearchRequest(OWNER,ACTOR,'task:fixture','Astronomía y música',
                              (record.record_ref,),('document','json'),deadline=NOW+timedelta(minutes=1))
    result = ReportBuilder(resolver=FixtureResolver(),access=FixtureReportAccess(),vault=backend.view('worker:research'),clock=FixtureClock()).build(request)
    document = json.loads(backend.view('worker:research').open(owner_ref=OWNER,pointer=result.document_ref))
    cite = document['notes'][0]['citations'][0]
    assert cite['evidence_ref']['recipient_scope'] == 'worker:sources'
    assert cite['evidence_ref']['sha256'] == record.private_ref.sha256
    assert 'private?synthetic' not in repr(result)
    with pytest.raises(VaultError): sink.read(owner_ref='owner:foreign',pointer=record.private_ref)
    assert len(transport.calls) == 1 and transport.fixture_only


def test_polling_view_contract_real_durable_private(vault_factory):
    backend, config = vault_factory()
    polling = backend.view('worker:polling')
    assert polling.preflight(owner_ref=OWNER) is True
    content = json.dumps({'method':'sendMessage','chat_id':-999999,'text':MARKER}).encode()
    ref = polling.put(owner_ref=OWNER,content=content)
    assert type(backend)(**config).view('worker:polling').get(owner_ref=OWNER,ref=ref) == content
    assert all(content not in p.read_bytes() for p in config['root'].glob('*.age'))
    with pytest.raises(VaultError): polling.get(owner_ref='owner:foreign',ref=ref)
