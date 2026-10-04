"""Model catalog for the OpenRouter adapter: user decision of 2026-10-03.

The user asked to leave the AI enabled with the cheapest capable Chinese model.
Data from ``GET https://openrouter.ai/api/v1/models`` and
``/api/v1/models/deepseek/deepseek-v4-flash/endpoints`` on 2026-10-03:

- ``deepseek/deepseek-v4-flash``: structured outputs; cheapest structured-output
  endpoint 0.028/0.056 USD per million tokens (StreamLake); others up to 0.44/1.32.
  Reasoning is optional (``mandatory: false``, default effort high), so it is
  disabled to avoid paying reasoning tokens on a short extraction.
- ``z-ai/glm-5.3-flash`` scores higher on the Artificial Analysis index but its
  reasoning is mandatory (default effort max): slower and pricier per call, so
  it is not allowed by default.

``PRICES`` are caps sent as ``provider.max_price`` (USD per million tokens):
OpenRouter then only routes to endpoints at or below them (StreamLake, DeepInfra,
Venice, DigitalOcean with structured outputs on that date). Costs computed by the
adapter are therefore upper bounds. Re-check the endpoints before changing caps.
"""

from decimal import Decimal

from radar.adapters.openrouter.client import PROMPTS, OpenRouterLLM, urllib_transport

CATALOG_DATE = "2026-10-03"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
PRICES = {DEFAULT_MODEL: (Decimal("0.10"), Decimal("0.20"))}
ALLOWED_MODELS = frozenset(PRICES)
REASONING_OFF = frozenset({DEFAULT_MODEL})
ENABLED = True  # user decision 2026-10-03; set False to switch the AI off everywhere


def build_default(*, secrets, cache, clock, transport=urllib_transport):
    """Adapter as the user wants it in production: enabled, capped, reasoning off."""
    return OpenRouterLLM(secrets=secrets, cache=cache, clock=clock, prices=PRICES, prompts=PROMPTS,
                         transport=transport, enabled=ENABLED, allowed_models=ALLOWED_MODELS,
                         price_cap=True, reasoning_off=REASONING_OFF)
