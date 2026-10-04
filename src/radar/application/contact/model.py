"""Deterministic templates, bounded shortlist and optional declared quotations."""
from __future__ import annotations
from dataclasses import dataclass, field
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from string import Formatter
from typing import Protocol
import re

from radar.domain.core import FxRate, Money, utc
from radar.ports.types import BlobPointer


class ContactError(ValueError):
    """Call sites supply only static codes, never private/provider text."""


def limit(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ContactError('contact_limit_invalid')
    return value


def private_text(value, maximum=4096):
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > maximum:
        raise ContactError('contact_text_invalid')
    return value


def facts_valid(facts):
    if type(facts) is not dict or len(facts) > 32:
        raise ContactError('facts_invalid')
    for key, value in facts.items():
        if not isinstance(key, str) or not re.fullmatch('[a-z][a-z0-9_]{0,31}', key):
            raise ContactError('facts_invalid')
        private_text(value, 1024)
    return facts


def render(template: str, explicit_facts: dict[str, str]) -> str:
    """No attribute/index lookup, format code, default facts or evaluation.

    Facts are declarations, not verified truths; template author selects the
    wording. Publication/response instructions remain literal data.
    """
    private_text(template, 4096)
    facts_valid(explicit_facts)
    pieces = []
    try:
        for literal, key, specification, conversion in Formatter().parse(template):
            pieces.append(literal)
            if key is not None:
                if not re.fullmatch('[a-z][a-z0-9_]{0,31}', key) or specification or conversion:
                    raise ContactError('template_field_unsupported')
                if key not in explicit_facts:
                    raise ContactError('template_fact_missing')
                pieces.append(explicit_facts[key])
    except ContactError:
        raise
    except ValueError:
        raise ContactError('template_invalid') from None
    return private_text(''.join(pieces), 4096)


@dataclass(frozen=True)
class CandidateEvidence:
    record_ref: str
    source_ref: str
    observed_at: datetime
    ciphertext_hash: str


@dataclass(frozen=True)
class PublishedContact:
    candidate_ref: str
    kind: str
    value: str = field(repr=False)
    facts: tuple[tuple[str, str], ...] = field(repr=False)
    evidence: tuple[CandidateEvidence, ...]
    verified: bool = False


@dataclass(frozen=True)
class Shortlist:
    candidates: tuple[PublishedContact, ...] = field(repr=False)
    seen: int
    discarded: int
    duplicates: int
    truncated: bool
    coverage: tuple[tuple[str, str, bool], ...]


def choose_contacts(candidates, *, maximum=10, filters=None, coverage=()):
    """Stable explicit filters/dedupe, never inferred services or channel support."""
    limit(maximum, 1, 50)
    filters = facts_valid(filters or {})
    if type(candidates) is not tuple or len(candidates) > 12800:
        raise ContactError('shortlist_limit_invalid')
    selected, positions = [], {}
    discarded = duplicates = 0
    for candidate in candidates:
        facts = dict(candidate.facts)
        if any(facts.get(key) != value for key, value in filters.items()):
            discarded += 1
            continue
        key = (candidate.kind, candidate.value.casefold() if candidate.kind == 'email'
               else re.sub('[ ()-]', '', candidate.value))
        if key in positions:
            duplicates += 1
            index = positions[key]
            original = selected[index]
            # Conflicting source facts are not merged into invented certainty.
            common = tuple((key, value) for key, value in original.facts
                           if facts.get(key) == value)
            selected[index] = replace(original, facts=common,
                                      evidence=original.evidence+candidate.evidence)
        else:
            positions[key] = len(selected)
            selected.append(candidate)
    return Shortlist(tuple(selected[:maximum]), len(candidates), discarded,
                     duplicates, len(selected) > maximum, tuple(coverage))


@dataclass(frozen=True)
class ResolutionBinding:
    owner_ref: str
    actor_ref: str
    operation_ref: str
    candidate_ref: str
    purpose_ref: str
    private_ref: BlobPointer
    content_hash: str


@dataclass(frozen=True)
class ResolvedContact:
    """Loaded from independent approved resolver proof, not supplied by caller."""
    binding: ResolutionBinding
    channel: str
    account_ref: str
    chat_ref: str
    recipient_ref: str
    session_ref: str
    session_version: int
    identity_ref: BlobPointer
    expires_at: datetime


class ApprovedContactResolver(Protocol):
    def resolve(self, *, binding: ResolutionBinding, proof_ref: str,
                now: datetime) -> ResolvedContact:
        """Check exact resolution approval + independent canonical recipient proof.

        A published phone, caller bool or synthetic enabled chat is not proof.
        Resolver approval covers this private endpoint/content hash/purpose.
        Message approval remains separate (A10).
        """
        ...


@dataclass(frozen=True)
class ResponseObservation:
    owner_ref: str
    operation_ref: str
    recipient_ref: str
    state: str
    observed_until: datetime
    confirmed_at: datetime
    provider_message_ref: str
    private_ref: BlobPointer | None = None


class ContactObservations(Protocol):
    def resolve(self, *, owner_ref: str, operation_ref: str,
                proof_ref: str, now: datetime) -> ResponseObservation:
        """Independent provider journal/complete read, never arbitrary caller data.

        silence must mean complete authorized coverage after confirmed dispatch,
        not an empty/failed read. Uncertain sends cannot establish silence.
        """
        ...


class ContactVault(Protocol):
    """Owner-bound immutable encrypted backend. No production plaintext fallback."""
    def seal(self, *, owner_ref: str, plaintext: bytes) -> BlobPointer: ...
    def open(self, *, owner_ref: str, pointer: BlobPointer) -> bytes: ...


@dataclass(frozen=True)
class PreparedContact:
    operation_ref: str
    candidate_ref: str
    private_ref: BlobPointer
    content_hash: str
    purpose_ref: str
    state: str = 'pending_resolution_approval'


@dataclass(frozen=True)
class CampaignView:
    campaign_ref: str
    private_ref: BlobPointer
    candidate_refs: tuple[str, ...]
    discarded: int
    duplicates: int
    truncated: bool
    coverage: tuple[tuple[str, str, bool], ...]


@dataclass(frozen=True)
class ConversationPreparation:
    operation_ref: str
    resolved: ResolvedContact
    text: str = field(repr=False)
    purpose_ref: str


@dataclass(frozen=True)
class Quotation:
    candidate_ref: str
    status: str
    amount: Money | None
    evidence_ref: str
    conditions: tuple[str, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class QuoteComparison:
    ranked: tuple[tuple[str, Money], ...]
    missing: tuple[tuple[str, str], ...]
    note: str = 'declared_quotes_not_transactions'


def declared_quote(*, candidate_ref, status, amount=None, currency=None,
                   evidence_ref, conditions=()):
    if status not in ('declared', 'ambiguous', 'no_response', 'unavailable'):
        raise ContactError('response_status_invalid')
    if type(conditions) is not tuple or len(conditions) > 16:
        raise ContactError('quote_conditions_invalid')
    for condition in conditions:
        private_text(condition, 1024)
    money = None
    if status == 'declared' and amount is not None and currency is not None:
        try:
            money = Money(amount, currency)
            if money.amount < Decimal(0):
                raise ContactError('quote_amount_invalid')
        except ValueError:
            return Quotation(candidate_ref, 'unknown_price_or_currency', None, evidence_ref, conditions)
    return Quotation(candidate_ref, status, money, evidence_ref, conditions)


def compare_quotes(quotes, *, currency, rates: tuple[FxRate, ...], at):
    utc(at)
    Money(Decimal(0), currency)  # Validate denomination, not a fabricated quote.
    if type(quotes) is not tuple or len(quotes) > 100:
        raise ContactError('quote_limit_invalid')
    ranked, missing = [], []
    for quote in quotes:
        if quote.status != 'declared' or quote.amount is None:
            missing.append((quote.candidate_ref, quote.status if quote.amount is None else 'not_comparable'))
            continue
        value = quote.amount
        if value.currency != currency:
            matching = [rate for rate in rates if rate.base == value.currency and rate.quote == currency
                        and rate.observed_at <= at < rate.valid_until]
            if len(matching) != 1:
                missing.append((quote.candidate_ref, 'fx_missing_expired_or_ambiguous'))
                continue
            value = matching[0].convert(value, at=at)
        ranked.append((quote.candidate_ref, value))
    return QuoteComparison(tuple(sorted(ranked, key=lambda pair: (pair[1].amount, pair[0]))), tuple(missing))
