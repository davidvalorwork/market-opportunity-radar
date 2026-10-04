"""Bounded private report preparation. No network, persistence, parser or actions."""
from dataclasses import asdict, replace
from decimal import Decimal
from radar.application.tasks.models import content_hash
from radar.domain.core import utc
from .citations import verify
from .extraction import extract_notes
from .models import (Artifact, Coverage, Report, ReportError, ResearchBudget, ResearchRequest,
                     SourceMaterial, SourceUnavailable, canonical, digest, opaque, private_pointer)
from .rendering import render_html, render_telegram


class ReportBuilder:
    def __init__(self, *, resolver, access, vault, clock, parser=None):
        if any(port is None for port in (resolver, access, vault, clock)):
            raise ReportError('boundary_required')
        if parser is not None:
            raise ReportError('parser_disabled')
        self.resolver, self.access, self.vault, self.clock = resolver, access, vault, clock

    def _report_access(self, request):
        try:
            self.access.check_report(owner_ref=request.owner_ref, actor_ref=request.actor_ref,
                                     task_ref=request.task_ref, now=utc(self.clock.now()))
        except Exception:
            raise ReportError('report_access_denied') from None

    def _source_access(self, request, source_ref, material=None):
        try:
            self.access.check_source(owner_ref=request.owner_ref, actor_ref=request.actor_ref,
                                     source_ref=source_ref, capability_ref=None if material is None else material.capability_ref,
                                     private_ref=None if material is None else material.private_ref, now=utc(self.clock.now()))
        except Exception:
            raise ReportError('source_access_denied') from None

    def _validate_material(self, request, ref, material, max_bytes):
        if not isinstance(material, SourceMaterial) or material.owner_ref != request.owner_ref or material.source_ref != ref:
            raise ReportError('source_scope_mismatch')
        opaque(material.capability_ref)
        if type(material.content) is not bytes or len(material.content) > max_bytes:
            raise SourceUnavailable('budget_exhausted')
        try:
            material.content.decode('utf-8')
            if material.content_hash != digest(material.content) or utc(material.observed_at) > utc(self.clock.now()):
                raise ValueError('integrity')
            if material.provenance not in ('fixture', 'unverified', 'public_authorized', 'private_authorized') or material.evidence_format not in ('raw_utf8', 'content_b64_json') or not isinstance(material.origins, tuple) or len(material.origins) > 100:
                raise ValueError('provenance')
            for origin, page, observed in material.origins:
                opaque(origin)
                if type(page) is not int or page < 1 or utc(observed) > utc(self.clock.now()):
                    raise ValueError('origin')
        except Exception:
            raise SourceUnavailable('reader_failed') from None
        if material.private_ref is not None:
            private_pointer(material.private_ref)
        if material.public_url is not None:
            try:
                self.access.check_public_url(owner_ref=request.owner_ref, source_ref=ref, url=material.public_url)
            except Exception:
                raise ReportError('public_url_not_classified') from None
        self._source_access(request, ref, material)
        if material.cost is not None and (not isinstance(material.cost, Decimal) or not material.cost.is_finite() or not 0 <= material.cost <= request.budget.max_read_cost):
            raise SourceUnavailable('budget_exhausted')

    def _collect(self, request):
        materials, coverage, reasons = [], [], set()
        used_bytes, reserved_cost, declared_cost = 0, Decimal(0), Decimal(0)
        stop_reason = None
        for index, ref in enumerate(request.source_refs):
            if utc(self.clock.now()) >= utc(request.deadline):
                stop_reason = 'deadline'
            elif index >= request.budget.max_sources:
                stop_reason = 'sources_quota'
            elif used_bytes >= request.budget.max_input_bytes:
                stop_reason = 'input_quota'
            elif reserved_cost + request.budget.max_read_cost > request.budget.max_cost:
                stop_reason = 'cost_quota'
            if stop_reason:
                coverage.append(Coverage(ref, 'skipped', stop_reason))
                reasons.add(stop_reason)
                continue
            self._report_access(request)
            self._source_access(request, ref)
            try:
                self.access.reserve_read(owner_ref=request.owner_ref, task_ref=request.task_ref, source_ref=ref,
                                         max_cost=request.budget.max_read_cost, now=utc(self.clock.now()))
            except SourceUnavailable:
                stop_reason = 'cost_quota'
                coverage.append(Coverage(ref, 'skipped', stop_reason))
                reasons.add(stop_reason)
                continue
            except Exception:
                raise ReportError('read_reservation_failed') from None
            reserved_cost += request.budget.max_read_cost
            remaining = request.budget.max_input_bytes - used_bytes
            try:
                material = self.resolver.resolve(owner_ref=request.owner_ref, source_ref=ref, max_bytes=remaining,
                                                 max_cost=request.budget.max_read_cost)
                self._validate_material(request, ref, material, remaining)
                if utc(self.clock.now()) >= utc(request.deadline):
                    raise SourceUnavailable('timeout')
                used_bytes += len(material.content)
                if material.private_ref is None:
                    material = replace(material, private_ref=self._seal(request, material.content), evidence_format='raw_utf8')
                self._source_access(request, ref, material)
                materials.append(material)
                coverage.append(Coverage(ref, 'read', observed_at=material.observed_at,
                                         content_hash=material.content_hash, provenance=material.provenance))
                if material.cost is None:
                    declared_cost = None
                    stop_reason = 'source_cost_unknown'
                    reasons.add(stop_reason)
                elif declared_cost is not None:
                    declared_cost += material.cost
            except SourceUnavailable as error:
                declared_cost = None
                coverage.append(Coverage(ref, 'failed', str(error)))
                reasons.add('source_incomplete')
            except ReportError:
                raise
            except Exception:
                declared_cost = None
                coverage.append(Coverage(ref, 'failed', 'reader_failed'))
                reasons.add('source_incomplete')
        usage = {'input_bytes': used_bytes, 'reserved_source_cost_usd': str(reserved_cost),
                 'declared_source_cost_usd': None if declared_cost is None else str(declared_cost),
                 'compute_cost_usd': None, 'actual_total_usd': None}
        return materials, coverage, reasons, usage

    def _seal(self, request, content):
        self._report_access(request)
        try:
            pointer = self.vault.seal(owner_ref=request.owner_ref, plaintext=content)
            private_pointer(pointer, scope='worker:research')
        except Exception:
            raise ReportError('private_vault_failed') from None
        self._report_access(request)
        return pointer

    def _materials_access(self, request, materials):
        self._report_access(request)
        for material in materials:
            self._source_access(request, material.source_ref, material)

    def build(self, request, *, notes=None):
        if not isinstance(request, ResearchRequest):
            raise ReportError('invalid_request')
        self._report_access(request)
        materials, coverage, reasons, usage = self._collect(request)
        by_ref = {material.source_ref: material for material in materials}
        if notes is None:
            notes = extract_notes(materials, fields=request.fields, limit=request.budget.max_notes + 1)
        if not isinstance(notes, tuple) or len(notes) > 513:
            raise ReportError('invalid_notes')
        if len(notes) > request.budget.max_notes:
            notes = notes[:request.budget.max_notes]
            reasons.add('notes_quota')
        verified, seen = [], set()
        for note in notes:
            try:
                result = verify(note, by_ref)
                if result['note_ref'] in seen:
                    raise ReportError('duplicate_note')
                seen.add(result['note_ref'])
                verified.append(result)
            except ReportError as error:
                reasons.add(str(error))
        if not verified:
            reasons.add('no_cited_notes')
        if 'pdf' in request.formats:
            reasons.add('pdf_renderer_pending')
        now = utc(self.clock.now())
        document = {'report_version': 1, 'owner_ref': request.owner_ref, 'task_ref': request.task_ref,
                    'topic': request.topic, 'generated_at': now.isoformat(), 'status': 'partial' if reasons else 'succeeded',
                    'notes': verified, 'coverage': [asdict(row) | {'observed_at': None if row.observed_at is None else row.observed_at.isoformat()} for row in coverage],
                    'reasons': sorted(reasons), 'usage': usage,
                    'limitations': ['citations_are_not_truth', 'deterministic_extraction_only', 'real_sources_ui_pdf_unverified']}
        # Retain a labelled partial skeleton when even cited notes exceed artifact quota.
        content = canonical(document).encode('utf-8')
        if len(content) > request.budget.max_output_bytes:
            reasons.add('output_quota')
            document.update(status='partial', notes=[], reasons=sorted(reasons))
            content = canonical(document).encode('utf-8')
        if len(content) > request.budget.max_output_bytes:
            raise ReportError('output_budget_too_small')
        artifacts = []
        for format in request.formats:
            if format == 'pdf':
                continue
            output = content if format == 'json' else render_html(document) if format == 'document' else render_telegram(document)
            if len(output) > request.budget.max_output_bytes:
                raise ReportError('output_budget_too_small')
            self._materials_access(request, materials)
            artifacts.append(Artifact(format, self._seal(request, output), digest(output), len(output)))
            self._materials_access(request, materials)
        self._materials_access(request, materials)
        pointer = self._seal(request, content)
        self._materials_access(request, materials)
        return Report(request.owner_ref, request.task_ref, document['status'], pointer, digest(content),
                      tuple(artifacts), tuple(coverage), tuple(document['reasons']), len(document['notes']), now)

    def from_confirmed(self, *, authority, task_ref, proposals, source_refs, formats=None, budget=None, fields=()):
        """Reload A6 via trusted repository; caller cannot supply a fabricated confirmation."""
        now = utc(self.clock.now())
        try:
            proposal = proposals.get(authority=authority, task_ref=task_ref, now=now)
        except Exception:
            raise ReportError('intent_not_authorized') from None
        snapshot = proposal.document
        if proposal.owner_ref != authority.owner_ref or proposal.actor_ref != authority.actor_ref or proposal.task_ref != task_ref or proposal.status != 'confirmed' or proposal.expires_at <= now or proposal.confirmation_hash != proposal.content_hash or content_hash(snapshot) != proposal.content_hash:
            raise ReportError('intent_not_confirmed')
        request = snapshot['request']
        if any(step['operation'] not in ('search', 'read', 'extract', 'inform', 'compose') for step in request['steps']):
            raise ReportError('unsupported_research_plan')
        chosen = formats or (next((step['arguments']['output_format'] for step in reversed(request['steps']) if 'output_format' in step['arguments']), 'telegram'),)
        budget = budget or ResearchBudget(max_sources=max(1, min(len(source_refs), request['budget']['calls'])), max_cost=Decimal(request['budget']['max_usd']))
        if min(len(source_refs), budget.max_sources) > request['budget']['calls'] or budget.max_cost > Decimal(request['budget']['max_usd']):
            raise ReportError('budget_exceeds_intent')
        return self.build(ResearchRequest(authority.owner_ref, authority.actor_ref, task_ref, request['goal'], tuple(source_refs),
                                          tuple(chosen), tuple(fields), budget, min(proposal.expires_at, authority.expires_at)))
