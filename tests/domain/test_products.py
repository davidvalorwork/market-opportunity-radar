from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext

import pytest
from hypothesis import given, settings, strategies as st

from radar.domain.core import (
    CostInput, Entity, Evidence, FxRate, Knowledge, Money, Opportunity,
    Outcome, ProposedAction, Scenario, Signal, Thesis,
)
from radar.domain.states import transition_action, transition_opportunity
from radar.domain.verticals.products import allocate, allowed_actions, calculate, match, normalize, percentage_charge, score


AT = datetime(2026, 10, 3, tzinfo=timezone.utc)
D = Decimal


def cost(name, amount, currency="USD", *, knowledge=Knowledge.KNOWN, prepaid=False):
    return CostInput(name, Money(D(amount), currency) if amount is not None else None,
                     Knowledge.UNKNOWN if amount is None else knowledge,
                     "synthetic-explicit-assumption", AT, prepaid)


def scenario():
    return Scenario("scenario:fixture", "USD", cost("purchase", "50", prepaid=True),
                    cost("sale", "90", knowledge=Knowledge.ESTIMATED), 1, 1, 1, 1,
                    (cost("logistics", "6", prepaid=True), cost("acquisition", "2", prepaid=True),
                     cost("packaging", "2", prepaid=True), cost("other_acquisition", "0")),
                    (cost("commission_on_sale_90_at_10_percent", "9"), cost("payment", "1"), cost("other_sale", "0")),
                    ("logistics", "acquisition", "packaging", "other_acquisition"),
                    ("commission_on_sale_90_at_10_percent", "payment", "other_sale"),
                    "hypothetical-COST_MODEL-example-not-country-tax-policy", AT)


def product():
    return Entity("entity:fixture", "Rasasi", "Hawas", (("volume_ml", "100"), ("edition", "for him")),
                  "new", "bottle", "original_declared")


def opportunity():
    return Opportunity("opportunity:fixture", Thesis("thesis:fixture", "signal:fixture", "hypothetical-destination", "price comparison only"),
                       scenario(), (Evidence("fixture:synthetic", AT, "a" * 64),))


def action():
    return ProposedAction("op:fixture", "owner:alice", "recipient:bob", "whatsapp:fixture", "a" * 64,
                          "confirm_price", 1, AT + timedelta(hours=1))


@pytest.mark.parametrize("amount", [True, False, 1.5, 1, "NaN", "sNaN", "Infinity", "-Infinity", "", " 2", "x", "1e99999", "1_000", "+1", "01"])
def test_money_rejects_invalid_values(amount):
    with pytest.raises(ValueError):
        Money(amount, "USD")


@pytest.mark.parametrize("currency", ["usd", "ZZZ", "US", "BTC", "XXX", None, []])
def test_money_explicit_iso_subset(currency):
    with pytest.raises(ValueError):
        Money(D("1"), currency)


def test_money_immutable_currency_and_signed_profit():
    value = Money(D("0.1"), "USD")
    assert (value + Money(D("0.2"), "USD")).amount == D("0.3")
    assert (value - Money(D("1"), "USD")).amount == D("-0.9")
    with pytest.raises(FrozenInstanceError):
        value.amount = D("0")
    with pytest.raises(ValueError):
        value + Money(D("1"), "EUR")
    with pytest.raises(ValueError):
        value.times(True)


def test_large_and_tiny_amounts_fail_closed_on_inexact_arithmetic():
    huge = Money(D("1E+64"), "USD")
    with pytest.raises(ValueError, match="precision_exceeded"):
        huge + Money(D("1"), "USD")
    sixty_four_digits = Money(D("9" * 64), "USD")
    with pytest.raises(ValueError, match="precision_exceeded"):
        sixty_four_digits.times(11)
    with pytest.raises(ValueError, match="precision_exceeded"):
        Money(D("1"), "USD") + Money(D("1E-64"), "USD")
    extreme_fx = FxRate("EUR", "USD", D("9" * 64), AT, AT + timedelta(days=1), "fixture")
    with pytest.raises(ValueError, match="precision_exceeded"):
        extreme_fx.convert(Money(D("9" * 64), "EUR"), at=AT)
    assert Money(D("9" * 63), "USD").times(10).amount == D("9" * 63 + "0")
    extreme_scenario = replace(scenario(), acquisition_lot=cost("purchase", "9" * 64, prepaid=True))
    with pytest.raises(ValueError, match="precision_exceeded"):
        calculate(extreme_scenario)


def test_fx_direct_dated_conversion():
    rate = FxRate("EUR", "USD", D("1.2"), AT, AT + timedelta(days=1), "synthetic quote")
    assert rate.convert(Money(D("50"), "EUR"), at=AT) == Money(D("60"), "USD")
    for instant in (AT - timedelta(seconds=1), rate.valid_until):
        with pytest.raises(ValueError):
            rate.convert(Money(D("50"), "EUR"), at=instant)
    with pytest.raises(ValueError):
        rate.convert(Money(D("50"), "USD"), at=AT)


@pytest.mark.parametrize("changes", [{"rate": D("0")}, {"rate": D("-1")}, {"quote": "EUR"},
                                      {"source": ""}, {"observed_at": AT.replace(tzinfo=None)}, {"valid_until": AT}])
def test_fx_invalid(changes):
    params = dict(base="EUR", quote="USD", rate=D("1.2"), observed_at=AT,
                  valid_until=AT + timedelta(days=1), source="fixture")
    with pytest.raises(ValueError):
        FxRate(**(params | changes))


def test_documented_cost_example():
    result = calculate(scenario())
    assert result.complete
    assert (result.total_cost.amount, result.net.amount, result.profit.amount, result.capital.amount) == (D("60"), D("80"), D("20"), D("60"))
    with localcontext() as ctx:
        ctx.prec = 64
        assert result.margin == D("20") / D("90")
        assert result.return_on_cost == D("20") / D("60")
    assert result.result_kind == "estimated_not_realized"
    assert result.estimated_inputs == ("sale",)


def test_calculation_independent_of_ambient_decimal_context():
    baseline = calculate(scenario())
    with localcontext() as ctx:
        ctx.prec = 3
        ctx.rounding = ROUND_DOWN
        assert calculate(scenario()) == baseline
        assert normalize(replace(product(), variant=(("volume_ml", "100.00000001"),))).variant == (("volume_ml", "100.00000001"),)


def test_unknown_required_zero_and_future_are_not_silently_free():
    value = scenario()
    for amended in (
        replace(value, incoming=value.incoming[:-1]),
        replace(value, incoming=value.incoming[:-1] + (cost("other_acquisition", None),)),
        replace(value, incoming=value.incoming[:-1] + (replace(value.incoming[-1], observed_at=AT + timedelta(seconds=1)),)),
    ):
        result = calculate(amended)
        assert not result.complete and result.missing
        assert result.profit is None and result.total_cost is None and score(result, match(product(), product())).rank is None
    assert calculate(value).complete  # only the explicitly sourced zero is accepted


def test_fx_missing_stale_ambiguous_and_reverse_not_parity():
    value = replace(scenario(), acquisition_lot=cost("purchase", "50", "EUR", prepaid=True))
    active = FxRate("EUR", "USD", D("1.2"), AT, AT + timedelta(days=1), "fixture")
    stale = replace(active, observed_at=AT-timedelta(days=2), valid_until=AT)
    reverse = FxRate("USD", "EUR", D("0.8"), AT, AT+timedelta(days=1), "fixture")
    for rates in ((), (stale,), (reverse,), (active, active)):
        assert not calculate(value, rates).complete
    assert calculate(value, (active,)).total_cost.amount == D("70")
    assert calculate(value, (active,)).fx_rates == (active,)


def test_percentage_charge_requires_explicit_base_fixed_and_rate():
    charge = percentage_charge("fee", cost("gross_sale", "90", knowledge=Knowledge.ESTIMATED),
                               D("0.10"), Knowledge.KNOWN, cost("fixed", "1"),
                               source="hypothetical tariff", observed_at=AT, prepaid=False)
    assert charge.value == Money(D("10"), "USD")
    assert charge.knowledge is Knowledge.ESTIMATED and "base=gross_sale" in charge.source
    on_purchase = percentage_charge("fee", cost("purchase", "50"), D("0.10"), Knowledge.KNOWN,
                                    cost("fixed", "0"), source="explicit zero fixed fee", observed_at=AT, prepaid=False)
    assert on_purchase.value.amount == D("5")
    for base, rate, knowledge, fixed in ((cost("base", None), D("0.1"), Knowledge.KNOWN, cost("fixed", "0")),
                                        (cost("base", "90"), None, Knowledge.UNKNOWN, cost("fixed", "0")),
                                        (cost("base", "90"), D("0.1"), Knowledge.KNOWN, cost("fixed", None))):
        unknown = percentage_charge("fee", base, rate, knowledge, fixed, source="fixture", observed_at=AT, prepaid=False)
        assert unknown.value is None and unknown.knowledge is Knowledge.UNKNOWN
    for rate in (D("-0.1"), True, 0.1):
        with pytest.raises(ValueError):
            percentage_charge("fee", cost("base", "90"), rate, Knowledge.KNOWN, cost("fixed", "0"),
                              source="fixture", observed_at=AT, prepaid=False)


def test_moq_lot_and_capital_vs_postpaid():
    value = scenario()
    bulk = replace(value, units_per_lot=10, lots=2, minimum_lots=2, sale_units=20)
    result = calculate(bulk)
    assert result.total_cost.amount == D("110")  # logistics/etc whole scenario, not duplicated per bottle
    assert result.gross.amount == D("1800")
    assert result.capital.amount == D("110")
    postpaid = replace(bulk, incoming=(replace(bulk.incoming[0], prepaid=False),) + bulk.incoming[1:])
    assert calculate(postpaid).capital.amount == D("104")
    for changes in ({"lots": 1}, {"sale_units": 1}, {"units_per_lot": True}, {"lots": 0}, {"incoming": list(value.incoming)}):
        with pytest.raises(ValueError):
            replace(bulk, **changes)


def test_duplicate_charges_and_cost_metadata():
    value = scenario()
    with pytest.raises(ValueError):
        replace(value, outgoing=value.outgoing + (value.incoming[0],))
    with pytest.raises(ValueError):
        replace(value.incoming[0], knowledge=Knowledge.UNKNOWN)
    with pytest.raises(ValueError):
        cost("negative", "-1")
    with pytest.raises(ValueError):
        replace(value.incoming[0], source="")


def test_nonpositive_denominators_and_losses():
    assert calculate(replace(scenario(), sale_unit=cost("sale", "0"))).margin is None
    loss = calculate(replace(scenario(), sale_unit=cost("sale", "30")))
    assert loss.profit.amount == D("-40") and score(loss, match(product(), product())).rank == 0
    free = replace(scenario(), acquisition_lot=cost("purchase", "0", prepaid=True), incoming=(), required_incoming=())
    assert calculate(free).return_on_cost is None
    assert score(calculate(free), match(product(), product())).rank is None
    zero_purchase_with_logistics = replace(scenario(), acquisition_lot=cost("purchase", "0", prepaid=True))
    assert calculate(zero_purchase_with_logistics).complete
    assert score(calculate(zero_purchase_with_logistics), match(product(), product())).band == "needs_review"


@pytest.mark.parametrize("changes", [
    {"variant": (("volume_ml", "30"), ("edition", "for him"))},
    {"variant": (("volume_ml", "100"), ("edition", "ice"))},
    {"condition": "used"}, {"unit": "pack"}, {"authenticity_claim": "replica_declared"},
    {"brand": "another"}, {"model": "another"},
])
def test_matching_conflicts(changes):
    assert match(product(), replace(product(), **changes)).status == "conflict"


@pytest.mark.parametrize("changes", [{"brand": None}, {"model": None}, {"unit": None}, {"condition": "unknown"},
                                      {"authenticity_claim": "unknown"}, {"variant": ()}, {"variant": (("volume_ml", "100"),)}])
def test_matching_missing_is_uncertain(changes):
    assert match(product(), replace(product(), **changes)).status == "uncertain"


def test_matching_normalizes_only_spelling():
    amended = replace(product(), brand="  RASASI ", model="HAWAS", variant=(("edition", "For   Him"), ("volume_ml", "100.0")))
    assert match(product(), amended).status == "compatible"
    assert "not_authenticity_verification" in match(product(), amended).reasons[0]
    with pytest.raises(ValueError):
        replace(product(), authenticity_claim="verified")
    with pytest.raises(ValueError):
        normalize(replace(product(), variant=(("Volume_Ml", "100"), ("volume_ml", "30"))))


def test_core_objects_are_product_vocabulary_not_realized_results():
    item = opportunity()
    signal = Signal("signal:fixture", product(), "sell_listing", scenario().sale_unit, item.evidence[0])
    assert signal.kind == "sell_listing"
    assert Outcome("op:fixture", "estimated", item.evidence).status == "estimated"
    with pytest.raises(ValueError):
        Outcome("op:fixture", "realized_profit", item.evidence)
    with pytest.raises(ValueError):
        replace(item, evidence=list(item.evidence))
    with pytest.raises(FrozenInstanceError):
        signal.kind = "observed_transaction"


def test_opportunity_review_and_actions_no_effects():
    item = opportunity()
    assert allowed_actions(item) == ("inspect_evidence", "review")
    pending = transition_opportunity(item, "pending_review")
    with pytest.raises(ValueError):
        transition_opportunity(pending, "accepted")
    accepted = transition_opportunity(pending, "accepted", human_reviewed=True)
    assert allowed_actions(accepted) == ("inspect_evidence",)
    with pytest.raises(ValueError):
        transition_opportunity(item, "accepted", human_reviewed=True)


def test_action_binding_expiry_uncertain_no_retry():
    proposed = action()
    for approval in (None, replace(proposed, recipient_ref="recipient:other"), replace(proposed, content_hash="b"*64), replace(proposed, expected_version=2)):
        with pytest.raises(ValueError):
            transition_action(proposed, "approved", at=AT, approved_action=approval)
    with pytest.raises(ValueError):
        transition_action(proposed, "approved", at=proposed.expires_at, approved_action=proposed)
    approved = transition_action(proposed, "approved", at=AT, approved_action=proposed)
    committed = transition_action(approved, "dispatch_committed", at=AT, approved_action=approved)
    uncertain = transition_action(committed, "send_uncertain", at=AT)
    for target in ("approved", "dispatch_committed", "cancelled"):
        with pytest.raises(ValueError):
            transition_action(uncertain, target, at=AT, approved_action=uncertain)
    with pytest.raises(ValueError):
        transition_action(uncertain, "provider_confirmed", at=AT)
    assert transition_action(uncertain, "provider_confirmed", at=AT, provider_evidence=True).state == "provider_confirmed"


@settings(max_examples=100, deadline=None)
@given(st.decimals(min_value="0", max_value="1000000", places=2, allow_nan=False, allow_infinity=False),
       st.decimals(min_value="0", max_value="1000000", places=2, allow_nan=False, allow_infinity=False))
def test_property_cost_sum_and_negative_profit(first, second):
    value = replace(scenario(), incoming=(cost("a", str(first)), cost("b", str(second))), required_incoming=("a", "b"))
    result = calculate(value)
    assert result.total_cost.amount == D("50") + first + second
    assert result.profit.amount == D("30") - first - second
    missing = replace(value, incoming=(cost("a", None), value.incoming[1]))
    assert not calculate(missing).complete and calculate(missing).profit is None


@given(st.integers(min_value=1, max_value=1000000), st.lists(st.integers(min_value=1, max_value=100), min_size=1, max_size=10))
def test_property_allocation_conserves_every_cent(cents, weights):
    total = Money(D(cents) / 100, "USD")
    parts = allocate(total, tuple(weights), quantum=D("0.01"))
    assert sum((part.amount for part in parts), D(0)) == total.amount
    assert all(part.amount >= 0 for part in parts)


@given(st.integers(min_value=1, max_value=10000), st.integers(min_value=0, max_value=10000))
def test_property_score_monotone_fixed_costs_evidence(first, increment):
    value = scenario()
    low = calculate(replace(value, sale_unit=cost("sale", str(first))))
    high = calculate(replace(value, sale_unit=cost("sale", str(first + increment))))
    compatibility = match(product(), product())
    assert score(low, compatibility).rank <= score(high, compatibility).rank


@given(st.decimals(min_value="0.01", max_value="1000", places=2, allow_nan=False, allow_infinity=False))
def test_property_fx_dated_no_missing_rate_guess(rate):
    value = replace(scenario(), acquisition_lot=cost("purchase", "50", "EUR", prepaid=True))
    quote = FxRate("EUR", "USD", rate, AT, AT + timedelta(hours=1), "fixture")
    assert calculate(value, (quote,)).total_cost.amount == D("50") * rate + D("10")
    assert not calculate(value).complete
