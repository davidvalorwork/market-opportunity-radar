"""Strict internal composition boundaries, unrelated to worker wire authority."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Protocol

from radar.domain.core import utc
from radar.ports.types import BlobPointer, StructuredRequest, StructuredResult
from radar.application.research.models import SourceMaterial, Coverage, Note, CitationSpan
from radar.application.research.citations import verify


class ContextualError(ValueError):
    """Only static reason codes, never a provider/chat/query/body."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return sha256(canonical(value).encode()).hexdigest()


def opaque(value):
    if not isinstance(value, str) or len(value) > 129 or not re.fullmatch(r'[a-z][a-z0-9_]{0,31}:[A-Za-z0-9_-]*[A-Za-z][A-Za-z0-9_-]*', value):
        raise ContextualError('invalid_reference')


def text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > maximum:
        raise ContextualError('invalid_private_text')
    return value


@dataclass(frozen=True)
class ReplyBudget:
    calls: int = 12
    tokens: int = 20000
    max_usd: Decimal = Decimal('0.10')
    input_bytes: int = 12000
    output_tokens: int = 1024
    sources: int = 8

    def __post_init__(self):
        for value, maximum in ((self.calls, 100), (self.tokens, 1000000), (self.input_bytes, 65536), (self.output_tokens, 8192), (self.sources, 32)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ContextualError('invalid_budget')
        if not isinstance(self.max_usd, Decimal) or not self.max_usd.is_finite() or not 0 <= self.max_usd <= Decimal(100):
            raise ContextualError('invalid_budget')


@dataclass(frozen=True)
class ReplyRequest:
    owner_ref: str
    actor_ref: str
    run_ref: str
    task_ref: str
    event_ref: str
    account_ref: str
    chat_ref: str
    provider_message_ref: str
    purpose_ref: str
    question_ref: BlobPointer
    evidence_refs: tuple[str, ...]
    deadline: datetime
    query_ref: BlobPointer | None = None
    mode: str = 'reasoned'
    model_ref: str = 'unconfigured'
    language: str = 'es'
    evidence_ttl_seconds: int = 86400
    budget: ReplyBudget = ReplyBudget()

    def __post_init__(self):
        for value in (self.owner_ref, self.actor_ref, self.run_ref, self.task_ref, self.event_ref, self.account_ref, self.chat_ref, self.provider_message_ref, self.purpose_ref):
            opaque(value)
        if not isinstance(self.question_ref, BlobPointer) or (self.query_ref is not None and not isinstance(self.query_ref, BlobPointer)):
            raise ContextualError('private_reference_required')
        if self.mode not in ('reasoned', 'extracts', 'literal') or not isinstance(self.budget, ReplyBudget):
            raise ContextualError('unsupported_composition')
        if self.mode == 'literal' and (self.evidence_refs or self.query_ref is not None):
            raise ContextualError('literal_has_unrequested_research')
        if not isinstance(self.evidence_refs, tuple) or len(self.evidence_refs) > self.budget.sources or len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ContextualError('invalid_evidence_refs')
        for value in self.evidence_refs:
            opaque(value)
        if type(self.evidence_ttl_seconds) is not int or not 1 <= self.evidence_ttl_seconds <= 30*86400:
            raise ContextualError('invalid_freshness')
        if not isinstance(self.model_ref, str) or not re.fullmatch(r'[A-Za-z0-9_./:-]{1,128}', self.model_ref) or not re.fullmatch(r'[a-z]{2,3}(-[A-Za-z0-9]{2,8}){0,3}', self.language):
            raise ContextualError('invalid_model_or_language')
        utc(self.deadline)

    @property
    def scope(self):
        return dict(owner_ref=self.owner_ref, account_ref=self.account_ref, chat_ref=self.chat_ref, provider_message_ref=self.provider_message_ref)


@dataclass(frozen=True)
class ContextMessage:
    owner_ref: str
    account_ref: str
    chat_ref: str
    provider_message_ref: str
    recipient_ref: str
    session_ref: str
    session_version: int
    private_ref: BlobPointer
    text: str = field(repr=False)
    observed_at: datetime = field(default=None)


@dataclass(frozen=True)
class EvidenceBundle:
    materials: tuple[SourceMaterial, ...] = field(repr=False)
    coverage: tuple[Coverage, ...]
    complete: bool
    new_external_cost: Decimal | None = None


@dataclass(frozen=True)
class Composition:
    invocation_ref: str
    state: str
    reason: str | None = None
    private_ref: BlobPointer | None = None
    replayed: bool = False


@dataclass(frozen=True)
class Invocation:
    reference: str
    acquired: bool
    result: Composition


class ContextReader(Protocol):
    def resolve(self, request: ReplyRequest, *, now: datetime) -> ContextMessage: ...


class EvidenceReader(Protocol):
    def collect(self, request: ReplyRequest, *, query: str | None, now: datetime) -> EvidenceBundle:
        """Host-reviewed deidentified query only; obey request I/O ceilings."""
        ...


class CompositionAccess(Protocol):
    def check(self, request: ReplyRequest, *, stage: str, now: datetime) -> None:
        """Fresh host run/task/version/lease/owner/chat/session/consent authority."""
        ...
    def approve_public_query(self, request: ReplyRequest, *, query: str, now: datetime) -> None:
        """Trusted policy, NOT model output; no private identity in public queries."""
        ...
    def approve_reasoner(self, request: ReplyRequest, *, now: datetime) -> None:
        """Configured registered prompt/schema + priced personal ZDR endpoint."""
        ...


def validate_reply(document, request, context, materials):
    """Closed app format. Citations bind observations, not truth or permissions."""
    if type(document) is not dict or set(document) != {'schema_version', 'scope', 'claims', 'unknowns', 'contradictions'} or type(document['schema_version']) is not int or document['schema_version'] != 1 or document['scope'] != request.scope:
        raise ContextualError('reply_scope_or_schema')
    claims, unknowns, contradictions = (document[k] for k in ('claims', 'unknowns', 'contradictions'))
    if type(claims) is not list or not 1 <= len(claims) <= 16 or type(unknowns) is not list or len(unknowns) > 8 or type(contradictions) is not list or len(contradictions) > 8:
        raise ContextualError('reply_shape_invalid')
    verified = []
    for index, claim in enumerate(claims):
        if type(claim) is not dict or set(claim) != {'kind', 'statement', 'citations'} or claim['kind'] not in ('extracted', 'inference', 'interlocutor') or type(claim['citations']) is not list or len(claim['citations']) > 8:
            raise ContextualError('reply_claim_invalid')
        text(claim['statement'], 1024)
        if claim['kind'] == 'interlocutor':
            if claim['citations'] or claim['statement'] not in context.text:
                raise ContextualError('interlocutor_quote_invalid')
            verified.append({**claim, 'citations': []})
            continue
        spans = []
        for citation in claim['citations']:
            if type(citation) is not dict or set(citation) != {'source_ref', 'start', 'end'}:
                raise ContextualError('reply_citation_invalid')
            spans.append(CitationSpan(**citation))
        try:
            verified.append(verify(Note('note:contextual_'+str(index), claim['statement'], claim['kind'], tuple(spans)), materials))
        except ValueError:
            raise ContextualError('reply_citation_invalid') from None
    for item in unknowns:
        text(item, 512)
    for item in contradictions:
        if type(item) is not dict or set(item) != {'description', 'source_refs'} or type(item['source_refs']) is not list or not 2 <= len(item['source_refs']) <= 8 or len(set(item['source_refs'])) != len(item['source_refs']) or any(ref not in materials for ref in item['source_refs']):
            raise ContextualError('reply_contradiction_invalid')
        text(item['description'], 512)
    return verified


def draft_text(document):
    labels = {'extracted': 'Fuente observada', 'inference': 'Inferencia con evidencia', 'interlocutor': 'Declaración del interlocutor'}
    parts = []
    for claim in document['claims']:
        citations = ' '.join('['+c['source_ref']+':'+str(c['start'])+'-'+str(c['end'])+']' for c in claim['citations'])
        parts.append(labels[claim['kind']]+': '+claim['statement']+(' '+citations if citations else ''))
    if document['unknowns']:
        parts.append('Desconocidos: '+'; '.join(document['unknowns']))
    if document['contradictions']:
        parts.append('Contradicciones declaradas: '+'; '.join(row['description'] for row in document['contradictions']))
    return text('\n'.join(parts), 4096)
