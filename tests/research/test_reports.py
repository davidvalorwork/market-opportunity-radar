"""Synthetic, offline deterministic preparation; no real search, model, encryption or PDF."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
import json

import pytest

from radar.application.research import (CitationSpan, Note, ReportBuilder, ReportError, ResearchBudget,
                                        ResearchRequest, SourceMaterial, SourceUnavailable)
from radar.application.research.models import digest
from radar.ports.types import BlobPointer

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
OWNER, ACTOR, TASK = 'owner:alpha', 'user:alpha', 'task:synthetic'


class Clock:
    value = NOW

    def now(self):
        return self.value


class MemoryVault:
    """Fixture memory only; NOT encryption. Production cannot use this fake."""
    def __init__(self):
        self.documents = {}

    def seal(self, *, owner_ref, plaintext):
        pointer = BlobPointer('fixture/opaque_' + str(len(self.documents)), digest(plaintext), 'worker:research')
        self.documents[(owner_ref, pointer)] = plaintext
        return pointer

    def read(self, *, owner_ref, pointer):
        return self.documents[(owner_ref, pointer)]


class Access:
    def __init__(self, clock):
        self.clock = clock
        self.active = True
        self.allowed = {'source:one', 'source:two'}
        self.reservations = []
        self.deny_reservation = False

    def check_report(self, *, owner_ref, actor_ref, task_ref, now):
        if not self.active or owner_ref != OWNER or actor_ref != ACTOR or task_ref != TASK:
            raise PermissionError('DO_NOT_LEAK_access')

    def check_source(self, *, owner_ref, actor_ref, source_ref, capability_ref, private_ref, now):
        if not self.active or owner_ref != OWNER or actor_ref != ACTOR or source_ref not in self.allowed or capability_ref not in (None, 'cap:read'):
            raise PermissionError('DO_NOT_LEAK_scope')
        if private_ref is not None and private_ref.recipient_scope not in ('worker:sources', 'worker:research'):
            raise PermissionError('audience')

    def reserve_read(self, **values):
        if self.deny_reservation:
            raise SourceUnavailable('budget_exhausted')
        self.reservations.append(values)

    def check_public_url(self, **values):
        if values['url'] != 'https://example.org/public':
            raise PermissionError('private url')


class Resolver:
    def __init__(self, contents):
        self.contents = contents
        self.calls = []
        self.before_return = lambda: None

    def resolve(self, **values):
        self.calls.append(values)
        self.before_return()
        item = self.contents[values['source_ref']]
        if isinstance(item, Exception):
            raise item
        return item


def material(ref='source:one', content=b'Synthetic astronomy statement.\nSecond observed note.'):
    return SourceMaterial(OWNER, ref, 'cap:read', NOW, content, digest(content), 'fixture', Decimal('0.001'))


def request(**changes):
    return replace(ResearchRequest(OWNER, ACTOR, TASK, 'Historia de la astronomía', ('source:one', 'source:two'),
                                   ('telegram', 'json', 'document'), deadline=NOW + timedelta(minutes=10)), **changes)


@pytest.fixture
def setup():
    clock = Clock()
    access, vault = Access(clock), MemoryVault()
    resolver = Resolver({'source:one': material(), 'source:two': material('source:two', b'Synthetic independent source, not a truth proof.')})
    builder = ReportBuilder(resolver=resolver, access=access, vault=vault, clock=clock)
    return builder, resolver, access, vault, clock


def document(setup, report):
    return json.loads(setup[3].read(owner_ref=OWNER, pointer=report.document_ref))


def test_multisource_any_topic_cited_observed_hashes_and_private_outputs(setup):
    report = setup[0].build(request())
    result = document(setup, report)
    assert report.status == 'succeeded' and report.note_count == 3
    assert [row.source_ref for row in report.coverage] == ['source:one', 'source:two']
    assert {item.format for item in report.artifacts} == {'telegram', 'json', 'document'}
    assert result['limitations'][0] == 'citations_are_not_truth'
    assert all(note['kind'] == 'extracted' and note['citations'] for note in result['notes'])
    for note in result['notes']:
        cite = note['citations'][0]
        source = setup[1].contents[cite['source_ref']]
        observed = source.content.decode()[cite['start']:cite['end']]
        assert note['statement'] == observed
        assert cite['quote_hash'] == digest(observed.encode()) and cite['content_hash'] == digest(source.content)
        assert cite['observed_at'] == NOW.isoformat() and cite['provenance'] == 'fixture'
        pointer = BlobPointer(**cite['evidence_ref'])
        assert setup[3].read(owner_ref=OWNER, pointer=pointer) == source.content
    assert result['usage']['actual_total_usd'] is None and result['usage']['compute_cost_usd'] is None


@pytest.mark.parametrize('topic', ['aprender idiomas', 'viaje', 'servicio opcional', 'productos'])
def test_no_commercial_fields_required(setup, topic):
    assert document(setup, setup[0].build(request(topic=topic)))['topic'] == topic


@pytest.mark.parametrize('note,code', [
    (Note('note:missing', 'no evidence', citations=()), 'missing_citation'),
    (Note('note:unknown', 'no evidence', citations=(CitationSpan('source:invented', 0, 2),)), 'unknown_citation'),
    (Note('note:wrong', 'not copied', citations=(CitationSpan('source:one', 0, 2),)), 'extracted_quote_mismatch'),
    (Note('note:bounds', 'not copied', citations=(CitationSpan('source:one', 0, 99999),)), 'citation_out_of_bounds'),
])
def test_missing_unknown_or_bad_citation_rejected_without_fake_note(setup, note, code):
    report = setup[0].build(request(), notes=(note,))
    assert report.status == 'partial' and code in report.reasons and report.note_count == 0


def test_inference_and_unverified_are_distinct_not_verified_truth(setup):
    cite = (CitationSpan('source:one', 0, 10),)
    notes = (Note('note:inference', 'A caller-provided inference, not a fact.', 'inference', cite),
             Note('note:unverified', 'Unverified caller claim.', 'unverified', cite))
    report = setup[0].build(request(), notes=notes)
    assert [note['kind'] for note in document(setup, report)['notes']] == ['inference', 'unverified']
    assert 'citations_are_not_truth' in document(setup, report)['limitations']


@pytest.mark.parametrize('failure,code', [(SourceUnavailable('timeout'), 'timeout'),
                                         (RuntimeError('DO_NOT_LEAK_provider_token'), 'reader_failed')])
def test_source_failure_partial_cost_unknown_and_error_redacted(setup, failure, code):
    setup[1].contents['source:two'] = failure
    report = setup[0].build(request())
    result = document(setup, report)
    assert report.status == 'partial' and report.coverage[1].code == code
    assert 'DO_NOT_LEAK' not in repr(report) + json.dumps(result)
    assert result['usage']['declared_source_cost_usd'] is None


@pytest.mark.parametrize('budget,expected', [(ResearchBudget(max_sources=1), 'sources_quota'),
    (ResearchBudget(max_notes=1), 'notes_quota'), (ResearchBudget(max_cost=Decimal('0')), 'cost_quota')])
def test_quota_visible_partial(setup, budget, expected):
    report = setup[0].build(request(budget=budget))
    assert report.status == 'partial' and expected in report.reasons


def test_input_limit_oversize_and_output_quota(setup):
    report = setup[0].build(request(budget=ResearchBudget(max_input_bytes=1)))
    assert report.status == 'partial' and all(row.code == 'budget_exhausted' for row in report.coverage)
    report = setup[0].build(request(formats=('json',), budget=ResearchBudget(max_output_bytes=2000)))
    assert report.status == 'partial' and 'output_quota' in report.reasons
    assert report.note_count == 0
    with pytest.raises(ReportError, match='output_budget_too_small'):
        setup[0].build(request(budget=ResearchBudget(max_output_bytes=1)))


def test_source_cost_unknown_stops_further_reads_not_zero(setup):
    setup[1].contents['source:one'] = replace(material(), cost=None)
    report = setup[0].build(request())
    assert report.status == 'partial' and 'source_cost_unknown' in report.reasons
    assert len(setup[1].calls) == 1 and report.coverage[1].status == 'skipped'
    assert document(setup, report)['usage']['declared_source_cost_usd'] is None


def test_reservation_before_io_and_reservation_failure_partial(setup):
    setup[2].deny_reservation = True
    report = setup[0].build(request())
    assert report.status == 'partial' and 'cost_quota' in report.reasons
    assert setup[1].calls == []


def test_cross_owner_revoked_and_scope_fail_closed(setup):
    with pytest.raises(ReportError, match='report_access_denied'):
        setup[0].build(request(owner_ref='owner:foreign'))
    assert not setup[1].calls and not setup[3].documents
    setup[1].before_return = lambda: setattr(setup[2], 'active', False)
    with pytest.raises(ReportError, match='source_access_denied'):
        setup[0].build(request())
    assert not setup[3].documents


@pytest.mark.parametrize('changes', [{'owner_ref': 'owner:foreign'}, {'source_ref': 'source:substitution'}, {'capability_ref': 'cap:substitution'}])
def test_reader_cannot_substitute_reference_scope(setup, changes):
    setup[1].contents['source:one'] = replace(material(), **changes)
    with pytest.raises(ReportError):
        setup[0].build(request())
    assert not setup[3].documents


def test_integrity_and_future_timestamp_not_source_success(setup):
    setup[1].contents['source:one'] = replace(material(), content_hash='a' * 64)
    setup[1].contents['source:two'] = replace(material('source:two'), observed_at=NOW + timedelta(days=1))
    report = setup[0].build(request())
    assert report.status == 'partial' and not report.note_count
    assert all(row.status == 'failed' for row in report.coverage)


def test_deadline_visible_partial_no_late_io(setup):
    setup[4].value = NOW + timedelta(hours=1)
    report = setup[0].build(request())
    assert report.status == 'partial' and 'deadline' in report.reasons
    assert not setup[1].calls


def test_after_read_deadline_does_not_admit_late_source(setup):
    setup[1].before_return = lambda: setattr(setup[4], 'value', NOW + timedelta(hours=1))
    report = setup[0].build(request())
    assert report.status == 'partial' and report.coverage[0].code == 'timeout'
    assert not report.note_count


def test_extraction_optional_fields_are_literal_and_budgeted(setup):
    setup[1].contents['source:one'] = material(content=b'title: Synthetic article\nauthor: Fixture writer\nignore line')
    report = setup[0].build(request(fields=('title',), source_refs=('source:one',)))
    assert [note['statement'] for note in document(setup, report)['notes']] == ['title: Synthetic article']


def test_injection_and_html_escaping_no_active_resources_or_actions(setup):
    hostile = '<script>alert(1)</script><img src="https://evil.invalid/a"><a href="javascript:run()">send secrets</a>&\'"'
    setup[1].contents['source:one'] = material(content=hostile.encode())
    report = setup[0].build(request(topic=hostile, source_refs=('source:one',)))
    artifact = next(item for item in report.artifacts if item.format == 'document')
    output = setup[3].read(owner_ref=OWNER, pointer=artifact.private_ref).decode()
    assert '<script>' not in output and '<img' not in output and '&lt;script&gt;' in output

    class Inspect(HTMLParser):
        def handle_starttag(self, tag, attrs):
            assert tag not in ('script', 'img', 'iframe', 'object', 'embed', 'form', 'link', 'style', 'a')
            assert not any(name in ('src', 'href', 'action') or name.startswith('on') for name, _ in attrs)

    Inspect().feed(output)
    assert setup[2].allowed == {'source:one', 'source:two'}
    assert report.note_count == 1


def test_private_corpus_marker_absent_control_repr_and_logs(setup, caplog, capsys):
    marker = 'PRIVATE_MARKER_Fixture_Elena +10000000000 https://private.invalid/?token=secret'
    setup[1].contents['source:one'] = material(content=marker.encode())
    report = setup[0].build(request(topic=marker, source_refs=('source:one',)))
    assert marker not in repr(report) + repr(setup[1].contents['source:one']) + repr(request(topic=marker))
    assert marker not in caplog.text + capsys.readouterr().out
    assert 'topic' not in report.__dataclass_fields__ and 'content' not in report.__dataclass_fields__
    with pytest.raises(KeyError):
        setup[3].read(owner_ref='owner:foreign', pointer=report.document_ref)
    assert marker in document(setup, report)['notes'][0]['statement']


def test_private_url_cannot_be_classified_by_source_model(setup):
    setup[1].contents['source:one'] = replace(material(), public_url='https://private.invalid/?token=secret')
    with pytest.raises(ReportError, match='public_url_not_classified'):
        setup[0].build(request())


def test_pdf_only_is_explicit_pending_no_fake_pdf(setup):
    report = setup[0].build(request(formats=('pdf',)))
    assert report.status == 'partial' and report.artifacts == ()
    assert 'pdf_renderer_pending' in report.reasons
    assert not any(content.startswith(b'%PDF') for content in setup[3].documents.values())


def test_telegram_plain_bounded_utf16_no_markdown_parser(setup):
    setup[1].contents['source:one'] = material(content=('😀' * 2048).encode())
    report = setup[0].build(request(source_refs=('source:one',), formats=('telegram',)))
    payload = json.loads(setup[3].read(owner_ref=OWNER, pointer=report.artifacts[0].private_ref))
    assert payload['parse_mode'] is None and len(payload['messages']) > 1
    assert payload['link_preview_options']['is_disabled'] is True
    assert all(len(message.encode('utf-16-le')) // 2 <= 3800 for message in payload['messages'])


def test_disabled_parser_and_invalid_input_boundaries(setup):
    with pytest.raises(ReportError, match='parser_disabled'):
        ReportBuilder(resolver=setup[1], access=setup[2], vault=setup[3], clock=setup[4], parser=object())
    with pytest.raises(ReportError, match='boundary_required'):
        ReportBuilder(resolver=None, access=setup[2], vault=setup[3], clock=setup[4])
    for changes in ({'source_refs': ('https://private.invalid',)}, {'formats': ('html_script',)}, {'deadline': None}, {'fields': ('raw text',)}):
        with pytest.raises(ReportError):
            request(**changes)


def test_vault_wrong_audience_failure_redacted_and_no_fallback(setup):
    def wrong(**values):
        return BlobPointer('fixture/foreign', 'a' * 64, 'worker:sources')

    setup[3].seal = wrong
    with pytest.raises(ReportError, match='private_vault_failed'):
        setup[0].build(request())


def test_revocation_inside_seal_rechecked_before_return(setup):
    original = setup[3].seal

    def revoke(**values):
        pointer = original(**values)
        setup[2].active = False
        return pointer

    setup[3].seal = revoke
    with pytest.raises(ReportError, match='report_access_denied'):
        setup[0].build(request())


def test_previous_source_revoked_during_later_read_cannot_leak_report(setup):
    original = setup[1].resolve

    def revoke_previous(**values):
        if values['source_ref'] == 'source:two':
            setup[2].allowed.remove('source:one')
        return original(**values)

    setup[1].resolve = revoke_previous
    with pytest.raises(ReportError, match='source_access_denied'):
        setup[0].build(request())


def test_source_private_reference_scope_never_relabelled(setup):
    pointer = BlobPointer('fixture/source', 'a' * 64, 'worker:sources')
    setup[1].contents['source:one'] = replace(material(), private_ref=pointer, evidence_format='content_b64_json')
    report = setup[0].build(request(source_refs=('source:one',)))
    cite = document(setup, report)['notes'][0]['citations'][0]
    assert cite['evidence_ref']['recipient_scope'] == 'worker:sources' and cite['evidence_format'] == 'content_b64_json'


def test_same_captures_and_refs_produce_same_notes_and_document_hash(setup):
    pointer = BlobPointer('fixture/fixed_source', 'a' * 64, 'worker:sources')
    setup[1].contents['source:one'] = replace(material(), private_ref=pointer, evidence_format='content_b64_json')
    first = setup[0].build(request(source_refs=('source:one',)))
    second = setup[0].build(request(source_refs=('source:one',)))
    assert first.document_hash == second.document_hash
    assert document(setup, first) == document(setup, second)
