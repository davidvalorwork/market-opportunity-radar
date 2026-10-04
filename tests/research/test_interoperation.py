"""A6 + A8 candidates via an explicit test boundary; no production fake wiring."""
from base64 import b64decode
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json

import pytest

from radar import contracts
from radar.adapters.local.sqlite import SQLiteStore
from radar.adapters.local.tasks import SQLiteTaskStore
from radar.adapters.local.telegram import Directory
from radar.adapters.sources.generic import RawItem, ReadOperation, ReadRequest, Reader, Registry, SourceSpec
from radar.adapters.sources.generic.fixtures import (FixtureAccess, FixtureCapabilities, FixtureTransport,
                                                      MemoryPrivateSink, fixture_page)
from radar.adapters.telegram import consent
from radar.application.research import ReportBuilder, ReportError, ResearchBudget, SourceMaterial
from radar.application.research.models import digest
from radar.application.tasks import Authority, TaskRouter
from radar.ports.types import BlobPointer

from test_reports import ACTOR, NOW, OWNER, Access, Clock, MemoryVault, Resolver, document, material, request


class TaskVault(MemoryVault):
    """Only the audience differs in this memory fake, never re-scope existing bytes."""
    def seal(self, *, owner_ref, plaintext):
        pointer = BlobPointer('fixture/task_' + str(len(self.documents)), digest(plaintext), 'worker:task-router')
        self.documents[(owner_ref, pointer)] = plaintext
        return pointer

    def open(self, *, owner_ref, pointer):
        return self.read(owner_ref=owner_ref, pointer=pointer)


@pytest.fixture
def confirmed(tmp_path):
    store = SQLiteStore(tmp_path / 'tasks.sqlite')
    directory = Directory(store)
    directory.enroll_synthetic(1, owner_ref=OWNER, actor_ref=ACTOR)
    directory.accept_consent(ACTOR, consent.CONSENT_VERSION, NOW, ('consent:fixture',))
    capabilities = (('search', 'cap:search'), ('inform', 'cap:inform'))
    for _, ref in capabilities:
        store.allow_source(owner_ref=OWNER, source_ref=ref, authorized=True)
    authority = Authority(OWNER, ACTOR, capabilities, NOW + timedelta(hours=1))
    proposals = SQLiteTaskStore(store, vault=TaskVault())
    router = TaskRouter(proposals, validate_document=contracts.validate)
    doc = {'schema_version': 1, 'goal': 'Aprender sobre ciencia y literatura', 'privacy_scope': 'public',
           'steps': [{'step_id': 'find', 'operation': 'search', 'arguments': {'query': 'ciencia y literatura'}},
                     {'step_id': 'report', 'operation': 'inform', 'depends_on': ['find'], 'arguments': {'output_format': 'document'}}],
           'confidence': 1, 'missing_fields': [], 'budget': {'calls': 2, 'message_limit': 0, 'max_usd': '0.10'}}
    initial = router.propose(authority=authority, event_ref='event:request', document=doc, now=NOW)
    button = next(item.callback_ref for item in initial.buttons if item.text == 'Confirmar')
    proposal = router.callback(authority=authority, callback_ref=button, event_ref='event:confirm', now=NOW)
    yield store, proposals, router, authority, proposal
    store.close()


def test_a6_confirmed_intent_routes_to_report_without_whatsapp_or_effect_approval(confirmed):
    _, proposals, _, authority, proposal = confirmed
    clock, vault = Clock(), MemoryVault()
    access = Access(clock)
    access.check_report = lambda **values: None if (values['owner_ref'], values['actor_ref'], values['task_ref']) == (OWNER, ACTOR, proposal.task_ref) else (_ for _ in ()).throw(PermissionError())
    resolver = Resolver({'source:one': material(), 'source:two': material('source:two', b'Another synthetic note.')})
    builder = ReportBuilder(resolver=resolver, access=access, vault=vault, clock=clock)
    report = builder.from_confirmed(authority=authority, task_ref=proposal.task_ref, proposals=proposals,
                                     source_refs=('source:one', 'source:two'))
    assert report.status == 'succeeded' and report.artifacts[0].format == 'document'
    assert authority.sessions == () and json.loads(vault.read(owner_ref=OWNER, pointer=report.document_ref))['topic'] == 'Aprender sobre ciencia y literatura'
    assert confirmed[0].db.execute('SELECT count(*) FROM ledger').fetchone()[0] == 0


def test_a6_correct_cancel_expiry_and_excess_budget_not_research_authority(confirmed):
    _, proposals, router, authority, proposal = confirmed
    clock, vault = Clock(), MemoryVault()
    builder = ReportBuilder(resolver=Resolver({}), access=Access(clock), vault=vault, clock=clock)
    with pytest.raises(ReportError, match='budget_exceeds_intent'):
        builder.from_confirmed(authority=authority, task_ref=proposal.task_ref, proposals=proposals, source_refs=('source:one',), budget=ResearchBudget(max_cost=Decimal('1')))
    cancelled = next(item.callback_ref for item in proposal.buttons if item.text == 'Cancelar')
    router.callback(authority=authority, callback_ref=cancelled, event_ref='event:cancel', now=NOW)
    with pytest.raises(ReportError, match='intent_not_confirmed'):
        builder.from_confirmed(authority=authority, task_ref=proposal.task_ref, proposals=proposals, source_refs=('source:one',))
    assert not vault.documents


def test_a8_private_records_convert_at_test_boundary_preserving_owner_audience_and_bytes():
    clock, vault, source_sink = Clock(), MemoryVault(), MemoryPrivateSink()
    spec = SourceSpec('source:articles', 'fixture', 'memory', 'fixture', (ReadOperation('search', 'fixture_search'),), min_interval_seconds=0)
    raw = b'Synthetic science article.\nAnother synthetic statement.'
    transport = FixtureTransport({'source:articles': (fixture_page((RawItem(raw, 'https://example.org/article?private=fixture'),)),)})
    reader = Reader(registry=Registry((spec,)), capabilities=FixtureCapabilities(NOW), access=FixtureAccess(),
                    private_sink=source_sink, transports={'fixture': transport}, now=clock.now)
    read_report = reader.read(ReadRequest(OWNER, ACTOR, 'request:articles', 'source:articles', 'search', NOW + timedelta(minutes=1)))
    assert read_report.complete and len(read_report.records) == 1
    record = read_report.records[0]
    assert record.private_ref.recipient_scope == 'worker:sources'

    class BoundaryResolver:
        def resolve(self, *, owner_ref, source_ref, max_bytes, max_cost):
            assert source_ref == record.record_ref and owner_ref == record.owner_ref
            payload = source_sink.read(owner_ref=owner_ref, pointer=record.private_ref, max_bytes=max_bytes)
            private = json.loads(payload)
            content = b64decode(private['content_b64'], validate=True)
            return SourceMaterial(owner_ref, source_ref, 'cap:read', record.provenance[0].observed_at,
                                  content, digest(content), 'fixture', None, record.private_ref,
                                  origins=tuple((p.source_ref, p.page, p.observed_at) for p in record.provenance),
                                  evidence_format='content_b64_json')

    access = Access(clock)
    access.allowed = {record.record_ref}
    builder = ReportBuilder(resolver=BoundaryResolver(), access=access, vault=vault, clock=clock)
    report = builder.build(request(source_refs=(record.record_ref,)))
    result = json.loads(vault.read(owner_ref=OWNER, pointer=report.document_ref))
    assert report.status == 'partial' and 'source_cost_unknown' in report.reasons
    cite = result['notes'][0]['citations'][0]
    assert cite['evidence_ref']['recipient_scope'] == 'worker:sources'
    assert cite['evidence_ref']['sha256'] == record.private_ref.sha256
    assert cite['content_hash'] == digest(raw) and cite['evidence_format'] == 'content_b64_json'
    assert cite['origins'][0]['source_ref'] == 'source:articles'
    assert cite['public_url'] is None
    assert 'https:' not in repr(report)
    with pytest.raises(PermissionError):
        source_sink.read(owner_ref='owner:foreign', pointer=record.private_ref)
    assert len(transport.calls) == 1 and transport.fixture_only


def test_public_url_is_only_classified_source_text_never_active_link():
    clock, vault = Clock(), MemoryVault()
    resolver = Resolver({'source:one': replace(material(), public_url='https://example.org/public')})
    builder = ReportBuilder(resolver=resolver, access=Access(clock), vault=vault, clock=clock)
    report = builder.build(request(source_refs=('source:one',), formats=('document',)))
    html = vault.read(owner_ref=OWNER, pointer=report.artifacts[0].private_ref).decode()
    assert 'https://example.org/public' in html and 'href=' not in html


@pytest.mark.parametrize('change', ['owner', 'actor', 'consent', 'correct', 'expired'])
def test_current_a6_authority_rechecked_before_research_read(confirmed, change):
    store, proposals, router, authority, proposal = confirmed
    clock, vault, resolver = Clock(), MemoryVault(), Resolver({})
    if change == 'owner':
        authority = replace(authority, owner_ref='owner:foreign')
    elif change == 'actor':
        authority = replace(authority, actor_ref='user:foreign')
    elif change == 'consent':
        Directory(store).withdraw_consent(owner_ref=OWNER, actor_ref=ACTOR)
    elif change == 'correct':
        token = next(item.callback_ref for item in proposal.buttons if item.text == 'Corregir')
        router.callback(authority=authority, callback_ref=token, event_ref='event:correct', now=NOW)
    else:
        clock.value = NOW + timedelta(minutes=16)
    builder = ReportBuilder(resolver=resolver, access=Access(clock), vault=vault, clock=clock)
    with pytest.raises(ReportError, match='intent_not_authorized|intent_not_confirmed'):
        builder.from_confirmed(authority=authority, task_ref=proposal.task_ref, proposals=proposals, source_refs=('source:one',))
    assert not resolver.calls and not vault.documents
