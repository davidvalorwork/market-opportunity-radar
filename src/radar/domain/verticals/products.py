"""Product matching and full-lot estimated economics, using explicit inputs."""

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from unicodedata import normalize as unicode_normalize

from ..core import CostInput, Entity, FxRate, Knowledge, Money, Opportunity, Scenario, decimal, exact_arithmetic, positive_int, text, utc


def _word(value: str | None) -> str | None:
    return " ".join(unicode_normalize("NFC", value).casefold().split()) if value else None


def normalize(value: Entity) -> Entity:
    """Normalize spelling, never infer a missing variant or authenticity."""
    variants = []
    for key, raw in value.variant:
        key, raw = _word(key), _word(raw)
        if key == "volume_ml":
            amount = decimal(raw)
            if amount <= 0:
                raise ValueError("invalid_volume")
            with localcontext(Context(prec=64, rounding=ROUND_HALF_EVEN)):
                raw = format(amount.normalize(), "f")
        variants.append((key, raw))
    return replace(value, brand=_word(value.brand), model=_word(value.model),
                   unit=_word(value.unit), variant=tuple(sorted(variants)))


@dataclass(frozen=True, slots=True)
class Match:
    status: str
    reasons: tuple[str, ...]


def match(left: Entity, right: Entity) -> Match:
    left, right = normalize(left), normalize(right)
    conflicts, missing = [], []
    for name in ("brand", "model", "unit", "condition", "authenticity_claim"):
        a, b = getattr(left, name), getattr(right, name)
        if a in (None, "unknown") or b in (None, "unknown"):
            missing.append(f"{name}:missing")
        elif a != b:
            conflicts.append(f"{name}:conflict")
    a, b = dict(left.variant), dict(right.variant)
    if not a or not b:
        missing.append("variant:missing")
    for name in sorted(a.keys() | b.keys()):
        if name not in a or name not in b:
            missing.append(f"variant.{name}:missing")
        elif a[name] != b[name]:
            conflicts.append(f"variant.{name}:conflict")
    if conflicts:
        return Match("conflict", tuple(conflicts + missing))
    if missing:
        return Match("uncertain", tuple(missing))
    return Match("compatible", ("identity_variant_condition_unit_claim_match_not_authenticity_verification",))


@dataclass(frozen=True, slots=True)
class Economics:
    complete: bool
    missing: tuple[str, ...]
    estimated_inputs: tuple[str, ...]
    review_reasons: tuple[str, ...] = ()
    gross: Money | None = None
    net: Money | None = None
    total_cost: Money | None = None
    profit: Money | None = None
    margin: Decimal | None = None
    return_on_cost: Decimal | None = None
    capital: Money | None = None
    result_kind: str = "estimated_not_realized"
    fx_rates: tuple[FxRate, ...] = ()


def percentage_charge(name: str, base: CostInput, rate: Decimal | str | None,
                      rate_knowledge: Knowledge, fixed: CostInput, *, source: str,
                      observed_at: datetime, prepaid: bool) -> CostInput:
    """Explicit base × fractional rate + explicit fixed charge, no fee defaults.

To use a pure percentage, the caller supplies a sourced zero fixed charge.
Missing base, fixed amount or rate yields an unknown charge, not zero.
The caller chooses the applicable base: sale, acquisition, etc.
"""
    utc(observed_at)
    text(source)
    if not isinstance(rate_knowledge, Knowledge) or (rate is None) != (rate_knowledge is Knowledge.UNKNOWN):
        raise ValueError("rate_knowledge_mismatch")
    if base.observed_at > observed_at or fixed.observed_at > observed_at:
        raise ValueError("future_charge_inputs")
    rate = decimal(rate) if rate is not None else None
    if rate is not None and rate < 0:
        raise ValueError("negative_charge_rate")
    provenance = f"{source};base={base.name};rate={rate};fixed={fixed.name}"
    if base.value is None or fixed.value is None or rate is None:
        return CostInput(name, None, Knowledge.UNKNOWN, provenance, observed_at, prepaid)
    if base.value.currency != fixed.value.currency:
        raise ValueError("currency_mismatch")
    with exact_arithmetic():
        amount = Money(base.value.amount * rate + fixed.value.amount, base.value.currency)
    knowledge = Knowledge.ESTIMATED if Knowledge.ESTIMATED in (base.knowledge, fixed.knowledge, rate_knowledge) else Knowledge.KNOWN
    return CostInput(name, amount, knowledge, provenance, observed_at, prepaid)


def calculate(value: Scenario, rates: tuple[FxRate, ...] = ()) -> Economics:
    """No FX triangulation/parity, tariffs, demand, or invented charges.

All supplied charges count exactly once. Explicit required-name lists describe
applicability; their source must be supplied when constructing the scenario.
"""
    if type(rates) is not tuple or any(not isinstance(rate, FxRate) for rate in rates):
        raise ValueError("immutable_rates_required")
    missing, estimated, converted, used_rates = [], [], {}, []
    for group, required in ((value.incoming, value.required_incoming), (value.outgoing, value.required_outgoing)):
        present = {item.name for item in group}
        missing.extend(f"cost:{name}" for name in required if name not in present)
    inputs = (value.acquisition_lot, value.sale_unit) + value.incoming + value.outgoing
    if len({item.name for item in inputs}) != len(inputs):
        raise ValueError("cost_double_counted")
    for item in inputs:
        if item.observed_at > value.at:
            missing.append(f"cost:{item.name}:future_observation")
            continue
        if item.value is None:
            missing.append(f"cost:{item.name}")
            continue
        amount = item.value
        if item.knowledge is Knowledge.ESTIMATED:
            estimated.append(item.name)
        if amount.currency != value.report_currency:
            applicable = [rate for rate in rates if rate.base == amount.currency and rate.quote == value.report_currency and rate.observed_at <= value.at < rate.valid_until]
            if len(applicable) != 1:
                missing.append(f"fx:{item.name}:missing_expired_or_ambiguous")
                continue
            amount = applicable[0].convert(amount, at=value.at)
            if applicable[0] not in used_rates:
                used_rates.append(applicable[0])
        converted[item.name] = amount.amount
    if missing:
        return Economics(False, tuple(sorted(set(missing))), tuple(sorted(estimated)), fx_rates=tuple(used_rates))
    with exact_arithmetic():
        purchase = converted[value.acquisition_lot.name] * value.lots
        gross = converted[value.sale_unit.name] * value.sale_units
        total = purchase + sum((converted[item.name] for item in value.incoming), Decimal(0))
        net = gross - sum((converted[item.name] for item in value.outgoing), Decimal(0))
        profit = net - total
        capital = purchase + sum((converted[item.name] for item in value.incoming + value.outgoing if item.prepaid), Decimal(0))
        review = tuple(name for name, number in (("zero_acquisition_price", purchase), ("zero_sale_price", gross)) if number == 0)
    def money(number):
        return Money(number, value.report_currency)
    # Only display ratios are rounded to the documented precision, never money.
    with localcontext(Context(prec=64, rounding=ROUND_HALF_EVEN)):
        return Economics(True, (), tuple(sorted(estimated)), review, money(gross), money(net), money(total), money(profit),
                         profit / gross if gross > 0 else None,
                         profit / total if total > 0 else None, money(capital), fx_rates=tuple(used_rates))


def allocate(total: Money, weights: tuple[int, ...], *, quantum: Decimal) -> tuple[Money, ...]:
    """Explicit proportional allocation; last item gets rounding residual.

Rounding is HALF_EVEN at the caller-provided currency quantum. This operation
only allocates a known nonnegative total; it never creates a free shipment.
"""
    quantum = decimal(quantum)
    digits = quantum.as_tuple().digits
    if total.amount < 0 or quantum <= 0 or digits[0] != 1 or any(digit != 0 for digit in digits[1:]):
        raise ValueError("invalid_allocation_quantum_or_total")
    if type(weights) is not tuple or not weights:
        raise ValueError("immutable_weights_required")
    for weight in weights:
        positive_int(weight)
    with exact_arithmetic():
        units = total.amount / quantum
    if units != units.to_integral_value():
        raise ValueError("total_not_representable_at_quantum")
    units, denominator = int(units), sum(weights)
    parts, remaining = [], units
    for weight in weights[:-1]:
        quotient, remainder = divmod(units * weight, denominator)
        if 2 * remainder > denominator or (2 * remainder == denominator and quotient % 2):
            quotient += 1
        part = min(remaining, quotient)
        with exact_arithmetic():
            parts.append(Money(Decimal(part) * quantum, total.currency))
        remaining -= part
    with exact_arithmetic():
        return tuple(parts + [Money(Decimal(remaining) * quantum, total.currency)])


@dataclass(frozen=True, slots=True)
class Score:
    band: str
    rank: int | None
    reasons: tuple[str, ...]


def score(economics: Economics, compatibility: Match) -> Score:
    """Monotone margin band only when evidence/match/cost completeness are fixed."""
    if compatibility.status != "compatible":
        return Score("needs_review", None, compatibility.reasons)
    if economics.review_reasons:
        return Score("needs_review", None, economics.review_reasons)
    if not economics.complete or economics.margin is None or economics.return_on_cost is None:
        return Score("incomplete", None, economics.missing or ("invalid_denominator",))
    margin = economics.margin
    rank = 0 if margin <= 0 else 1 if margin < Decimal("0.10") else 2 if margin < Decimal("0.25") else 3
    return Score(("nonpositive", "low", "medium", "high")[rank], rank, ("estimated_margin_not_realized_profit",))


def allowed_actions(value: Opportunity) -> tuple[str, ...]:
    """MVP actions are read/review only, never send/buy even if margin is high."""
    return ("inspect_evidence", "review") if value.state in {"candidate", "pending_review"} else ("inspect_evidence",)
