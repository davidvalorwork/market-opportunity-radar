"""Small immutable vocabulary used by the product vertical.

Currency support is an explicit MVP subset of ISO 4217, not a claim to accept
every current/historical code. Adding a currency requires a reviewed fixture.
Amounts remain Decimal; presentation rounding belongs at the reporting boundary.
"""

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Context, Decimal, DecimalException, Inexact, InvalidOperation, ROUND_HALF_EVEN, Rounded, localcontext
from enum import Enum
import re


SUPPORTED_CURRENCIES = frozenset({
    "USD", "EUR", "VES", "COP", "CLP", "MXN", "PEN", "BRL", "ARS",
    "GBP", "CAD", "CHF", "JPY", "CNY", "AUD", "INR", "UYU", "BOB",
})


@contextmanager
def exact_arithmetic():
    """Bounded exact arithmetic, fail closed instead of silently losing money."""
    context = Context(prec=64, rounding=ROUND_HALF_EVEN)
    context.traps[Inexact] = True
    context.traps[Rounded] = True
    try:
        with localcontext(context):
            yield
    except DecimalException as exc:
        raise ValueError("precision_exceeded") from exc


def decimal(value: Decimal | str) -> Decimal:
    """Never silently import binary floating-point or bool into money."""
    if not isinstance(value, (Decimal, str)):
        raise ValueError("decimal_requires_string_or_decimal")
    if isinstance(value, str) and (len(value) > 128 or re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?", value) is None):
        raise ValueError("invalid_decimal")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid_decimal") from exc
    if not result.is_finite() or len(result.as_tuple().digits) > 64 or abs(result.as_tuple().exponent) > 64:
        raise ValueError("decimal_not_finite_or_bounded")
    return result


def currency(value: str) -> str:
    if not isinstance(value, str) or value not in SUPPORTED_CURRENCIES:
        raise ValueError("unsupported_currency")
    return value


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError("utc_datetime_required")
    return value


def text(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("nonempty_text_required")
    return value


def positive_int(value: int) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("positive_integer_required")
    return value


class Knowledge(str, Enum):
    KNOWN = "known"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self):
        object.__setattr__(self, "amount", decimal(self.amount))
        currency(self.currency)

    def __add__(self, other: "Money") -> "Money":
        if not isinstance(other, Money) or self.currency != other.currency:
            raise ValueError("currency_mismatch")
        with exact_arithmetic():
            return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        if not isinstance(other, Money) or self.currency != other.currency:
            raise ValueError("currency_mismatch")
        with exact_arithmetic():
            return Money(self.amount - other.amount, self.currency)

    def times(self, quantity: int) -> "Money":
        positive_int(quantity)
        with exact_arithmetic():
            return Money(self.amount * quantity, self.currency)


@dataclass(frozen=True, slots=True)
class FxRate:
    base: str
    quote: str
    rate: Decimal
    observed_at: datetime
    valid_until: datetime
    source: str

    def __post_init__(self):
        currency(self.base)
        currency(self.quote)
        object.__setattr__(self, "rate", decimal(self.rate))
        utc(self.observed_at)
        utc(self.valid_until)
        text(self.source)
        if self.base == self.quote or self.rate <= 0 or self.valid_until <= self.observed_at:
            raise ValueError("invalid_fx_rate")

    def convert(self, value: Money, *, at: datetime) -> Money:
        utc(at)
        if value.currency != self.base:
            raise ValueError("fx_direction_mismatch")
        if not self.observed_at <= at < self.valid_until:
            raise ValueError("fx_expired_or_future")
        with exact_arithmetic():
            return Money(value.amount * self.rate, self.quote)


@dataclass(frozen=True, slots=True)
class Evidence:
    source: str
    observed_at: datetime
    content_hash: str

    def __post_init__(self):
        text(self.source)
        utc(self.observed_at)
        if not isinstance(self.content_hash, str) or len(self.content_hash) != 64 or any(c not in "0123456789abcdef" for c in self.content_hash):
            raise ValueError("invalid_content_hash")


@dataclass(frozen=True, slots=True)
class Entity:
    entity_id: str
    brand: str | None
    model: str | None
    variant: tuple[tuple[str, str], ...]
    condition: str
    unit: str | None
    authenticity_claim: str

    def __post_init__(self):
        text(self.entity_id)
        if type(self.variant) is not tuple or any(type(pair) is not tuple or len(pair) != 2 for pair in self.variant):
            raise ValueError("immutable_variant_required")
        for key, value in self.variant:
            text(key)
            text(value)
        if len({key for key, _ in self.variant}) != len(self.variant):
            raise ValueError("duplicate_variant_key")
        if self.condition not in {"new", "used", "unknown"} or self.authenticity_claim not in {"original_declared", "replica_declared", "unknown"}:
            raise ValueError("invalid_product_claim")
        for value in (self.brand, self.model, self.unit):
            if value is not None:
                text(value)


@dataclass(frozen=True, slots=True)
class CostInput:
    """A whole-scenario charge (not per-unit), including explicit zeroes.

Commissions must be resolved from an explicit base/rate by the caller. source
records that rule; no country-wide tax or commission default is invented here.
"""
    name: str
    value: Money | None
    knowledge: Knowledge
    source: str
    observed_at: datetime
    prepaid: bool

    def __post_init__(self):
        text(self.name)
        text(self.source)
        utc(self.observed_at)
        if not isinstance(self.knowledge, Knowledge) or type(self.prepaid) is not bool:
            raise ValueError("invalid_cost_metadata")
        if (self.value is None) != (self.knowledge is Knowledge.UNKNOWN):
            raise ValueError("unknown_value_mismatch")
        if self.value is not None and (not isinstance(self.value, Money) or self.value.amount < 0):
            raise ValueError("nonnegative_cost_required")


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    entity: Entity
    kind: str
    price: CostInput
    evidence: Evidence

    def __post_init__(self):
        text(self.signal_id)
        if not isinstance(self.entity, Entity) or not isinstance(self.price, CostInput) or not isinstance(self.evidence, Evidence):
            raise ValueError("immutable_signal_values_required")
        if self.kind not in {"sell_listing", "buy_request", "observed_transaction"}:
            raise ValueError("invalid_signal_kind")


@dataclass(frozen=True, slots=True)
class Thesis:
    thesis_id: str
    acquisition_signal_id: str
    destination_market: str
    rationale: str

    def __post_init__(self):
        for value in (self.thesis_id, self.acquisition_signal_id, self.destination_market, self.rationale):
            text(value)


@dataclass(frozen=True, slots=True)
class Scenario:
    """Full-lot liquidation scenario, not a prediction or partial-inventory model.

Explicit required cost names + cost_plan_source state which charges apply.
Missing required charges remain unknown. Acquisition lot price is prepaid.
Sale units must equal acquired units; partial inventory needs a future model.
"""
    scenario_id: str
    report_currency: str
    acquisition_lot: CostInput
    sale_unit: CostInput
    units_per_lot: int
    lots: int
    minimum_lots: int
    sale_units: int
    incoming: tuple[CostInput, ...]
    outgoing: tuple[CostInput, ...]
    required_incoming: tuple[str, ...]
    required_outgoing: tuple[str, ...]
    cost_plan_source: str
    at: datetime

    def __post_init__(self):
        text(self.scenario_id)
        text(self.cost_plan_source)
        if not isinstance(self.acquisition_lot, CostInput) or not isinstance(self.sale_unit, CostInput):
            raise ValueError("cost_input_required")
        currency(self.report_currency)
        utc(self.at)
        for value in (self.units_per_lot, self.lots, self.minimum_lots, self.sale_units):
            positive_int(value)
        if self.lots < self.minimum_lots or self.sale_units != self.lots * self.units_per_lot:
            raise ValueError("moq_or_inventory_mismatch")
        if not self.acquisition_lot.prepaid:
            raise ValueError("acquisition_must_be_prepaid_in_this_model")
        for group in (self.incoming, self.outgoing, self.required_incoming, self.required_outgoing):
            if type(group) is not tuple:
                raise ValueError("immutable_cost_plan_required")
        for group in (self.incoming, self.outgoing):
            if any(not isinstance(item, CostInput) for item in group) or len({item.name for item in group}) != len(group):
                raise ValueError("invalid_or_duplicate_cost")
        names = [item.name for item in self.incoming + self.outgoing]
        if len(set(names)) != len(names):
            raise ValueError("cost_double_counted")
        for group in (self.required_incoming, self.required_outgoing):
            if len(set(group)) != len(group):
                raise ValueError("duplicate_required_cost")
            for name in group:
                text(name)


@dataclass(frozen=True, slots=True)
class Opportunity:
    opportunity_id: str
    thesis: Thesis
    scenario: Scenario
    evidence: tuple[Evidence, ...]
    state: str = "candidate"

    def __post_init__(self):
        text(self.opportunity_id)
        if not isinstance(self.thesis, Thesis) or not isinstance(self.scenario, Scenario):
            raise ValueError("immutable_opportunity_values_required")
        if type(self.evidence) is not tuple or any(not isinstance(item, Evidence) for item in self.evidence):
            raise ValueError("immutable_evidence_required")
        if self.state not in {"candidate", "pending_review", "accepted", "rejected", "archived"}:
            raise ValueError("invalid_opportunity_state")


@dataclass(frozen=True, slots=True)
class ProposedAction:
    operation_id: str
    owner_ref: str
    recipient_ref: str
    session_ref: str
    content_hash: str
    purpose: str
    expected_version: int
    expires_at: datetime
    state: str = "proposed"

    def __post_init__(self):
        for value in (self.operation_id, self.owner_ref, self.recipient_ref, self.session_ref, self.purpose):
            text(value)
        if not isinstance(self.content_hash, str) or len(self.content_hash) != 64 or any(c not in "0123456789abcdef" for c in self.content_hash):
            raise ValueError("invalid_content_hash")
        positive_int(self.expected_version)
        utc(self.expires_at)
        if self.state not in {"proposed", "approved", "dispatch_committed", "provider_confirmed", "send_uncertain", "cancelled"}:
            raise ValueError("invalid_action_state")


@dataclass(frozen=True, slots=True)
class Outcome:
    """Observation only; a fixture or provider ACK is not realized profit."""
    operation_id: str
    status: str
    evidence: tuple[Evidence, ...]

    def __post_init__(self):
        text(self.operation_id)
        if self.status not in {"estimated", "provider_confirmed", "send_uncertain", "rejected"}:
            raise ValueError("invalid_outcome")
        if type(self.evidence) is not tuple or any(not isinstance(item, Evidence) for item in self.evidence):
            raise ValueError("immutable_evidence_required")
