"""The prices you set for models: ``config.json`` → ``model_prices``.

A model's price is data. PersonalClaw ships a table of list prices (``model_pricing.json``, each
row naming whose price it is and the day it was recorded), and a provider app may declare its
models' prices (``BrandedProviderSpec.pricing``). Whatever provider and model you use, you can set
its price here, a known one included: ``overrides`` maps a model's ``provider:model`` ref (or a
pattern over refs, ``work:*``, or a model id alone) to its price in the unit it is billed in, and
your price is the first thing the one resolution function reads (``routing.rates``), before this
machine's $0, the app's price and the table. It is set, edited and reset in Settings → Usage →
Model prices (``PUT`` and ``DELETE /api/models/rates``), which also records the day you set it.

Annotations here are evaluated, not postponed: ``config/schema.py`` renders a field's type by
resolving its annotation, and a postponed one renders every field as a string
(``tests/test_config_section_modules.py``).
"""

from dataclasses import dataclass, field
from typing import Any

from personalclaw.config.coercion import _meta


def price_overrides(section: object) -> dict[str, dict[str, Any]]:
    """The prices a raw ``model_prices`` section holds: each row a JSON object under a non-empty
    key. ``AppConfig.load()`` and the rate table (``routing.rates``) both read the section through
    this, so they cannot read one file two ways. Whether a row is a price in its unit is the rate
    table's question: a row that is none prices nothing."""
    overrides = section.get("overrides") if isinstance(section, dict) else None
    if not isinstance(overrides, dict):
        return {}
    return {
        key: dict(row)
        for key, row in overrides.items()
        if isinstance(key, str) and key.strip() and isinstance(row, dict)
    }


@dataclass
class ModelPricesConfig:
    """The prices you set for models (Settings → Usage → Model prices)."""

    overrides: dict[str, dict[str, Any]] = field(
        default_factory=dict,
        metadata=_meta(
            "Your model prices",
            "Your price for a model, by its provider:model ref, a pattern such as work:*, or a "
            "model id alone, in the unit it is billed in, and the day you set it. It comes before "
            "the known $0 of a model this machine runs, the price its provider app declares and "
            "PersonalClaw's price table. Set, edit and reset each in Settings → Usage → Model "
            "prices.",
        ),
    )
