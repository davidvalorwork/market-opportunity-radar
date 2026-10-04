"""Catalog: AI enabled with DeepSeek V4 Flash, price cap and reasoning off (user decision 2026-10-03)."""

from decimal import Decimal

import pytest

from radar.adapters.openrouter import catalog
from radar.adapters.openrouter.client import BudgetRefused, ModelRefused

from test_client import Cache, Clock, Secrets, Transport, ok, request


def adapter(*replies):
    transport = Transport(*replies)
    return catalog.build_default(secrets=Secrets(), cache=Cache(), clock=Clock(), transport=transport), transport


def test_catalog_is_enabled_and_consistent():
    assert catalog.ENABLED is True
    assert catalog.DEFAULT_MODEL in catalog.PRICES
    assert catalog.ALLOWED_MODELS == frozenset(catalog.PRICES)
    assert catalog.REASONING_OFF <= catalog.ALLOWED_MODELS
    assert not any(m.endswith(":free") for m in catalog.ALLOWED_MODELS)
    for price_in, price_out in catalog.PRICES.values():
        assert Decimal("0") < price_in <= Decimal("1") and Decimal("0") < price_out <= Decimal("1")


def test_default_model_body_caps_price_and_disables_reasoning():
    llm, transport = adapter(ok())
    result = llm.generate(owner_ref="owner:radar-pilot", request=request(model_ref=catalog.DEFAULT_MODEL))
    body = transport.calls[0]["body"]
    price_in, price_out = catalog.PRICES[catalog.DEFAULT_MODEL]
    assert body["model"] == catalog.DEFAULT_MODEL
    assert body["provider"] == {"require_parameters": True,
                                "max_price": {"prompt": float(price_in), "completion": float(price_out)}}
    assert body["reasoning"] == {"enabled": False}
    assert body["temperature"] == 0 and body["response_format"]["json_schema"]["strict"] is True
    # cost is an upper bound computed at the cap prices: 900 in + 150 out tokens
    assert result.cost == (Decimal(900) * price_in + Decimal(150) * price_out) / Decimal(1_000_000)


def test_personal_scope_keeps_zdr_and_adds_cap():
    llm, transport = adapter(ok())
    llm.generate(owner_ref="owner:radar-pilot",
                 request=request(model_ref=catalog.DEFAULT_MODEL, privacy_scope="personal"))
    provider = transport.calls[0]["body"]["provider"]
    assert provider["zdr"] is True and provider["data_collection"] == "deny"
    assert provider["require_parameters"] is True and "max_price" in provider


def test_other_models_are_refused_without_http():
    llm, transport = adapter(ok())
    with pytest.raises(ModelRefused):
        llm.generate(owner_ref="owner:radar-pilot", request=request(model_ref="z-ai/glm-5.3-flash"))
    assert transport.calls == []


def test_budget_still_enforced_before_http():
    llm, transport = adapter(ok())
    with pytest.raises(BudgetRefused):
        llm.generate(owner_ref="owner:radar-pilot",
                     request=request(model_ref=catalog.DEFAULT_MODEL, max_cost=Decimal("0.0000001")))
    assert transport.calls == []
