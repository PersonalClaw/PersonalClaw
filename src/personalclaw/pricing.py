"""The shipped model price table, ``model_pricing.json`` — the rate table's builtin tier.

Providers report token counts but not always a dollar cost (most set ``cost_usd=0.0``). This
module reads the table core ships for :mod:`personalclaw.routing.rates`, which prices every call:
the table is its fourth tier, under a rate the owner set, a local model's known zero and a rate the
serving app declared. Nothing else reads it
(``tests/test_every_dollar_is_priced_by_one_function.py``), because a consumer that did priced by
this table alone, which never sees those three.

Each row is one model's list price, in the unit the model is billed in (``unit``: per 1M tokens
when it names none, else per image, per second of video, per minute of audio or per 1M
characters), and names whose list price it is (``vendor``) and the day the table recorded it
(``recorded``). A list price is the model maker's; the service that runs a model may bill
differently, and a price the owner sets comes first.

A model absent from the table has no row: :func:`price_row` is None and the rate table reads it as
unpriced. We never invent a price for an unknown model, and a row for one model never prices
another: a row prices the model it names and that model's dated snapshots
(``claude-haiku-4.5-20251001``), never a later version (``claude-opus-5`` is not
``claude-opus-5.5``). And no row prices a model free by its name: an open-weight family
(``llama3.1``, ``mistral``) costs whatever the machine serving it bills, which is the rate table's
local tier's question, not this table's.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PRICING_FILE = Path(__file__).resolve().parent / "model_pricing.json"

# model id -> its row: the rates in its unit, plus ``vendor`` and ``recorded``.
_PRICES: dict[str, dict[str, Any]] = {}
if _PRICING_FILE.exists():
    try:
        with open(_PRICING_FILE, encoding="utf-8") as _fp:
            _PRICES = {
                k: v
                for k, v in json.load(_fp).items()
                if not k.startswith("_") and isinstance(v, dict)
            }
    except (OSError, ValueError):
        logger.warning("Could not load model_pricing.json; cost estimates disabled")

# A Bedrock model id is ``[<profile>.]<vendor>.<model>``: ``global.anthropic.claude-opus-5-5``,
# ``us.amazon.nova-pro-v1:0``, ``amazon.titan-embed-text-v2:0``. A profile routes the vendor's
# model through a set of regions and is the same model, so its id is priced as the base model's.
# Profiles are a pattern, not a list (``us``, ``eu``, ``apac``, ``global``, ``us-gov`` and whatever
# Bedrock mints next): a dotted segment that a vendor segment and a model follow.
_PROFILE = re.compile(r"^[a-z]{2,8}(?:-[a-z]+)?\.(?=[a-z0-9-]+\.[^.])")
# Anthropic's models are one family across every service that serves them, so this table keys
# them as Anthropic names them, dotted (``claude-opus-4.8``). A service spells the same model
# with a vendor prefix, an API revision and a hyphenated version (Bedrock's
# ``anthropic.claude-opus-4-8-v1:0``, the Anthropic API's ``claude-opus-4-8``), which
# :func:`_family` turns back into the dotted name. The version pair is the one BEFORE a dated
# snapshot (``…-4-8-20260101``), a ``@`` snapshot or a ``[…]`` variant, never a date's digits.
_ANTHROPIC_PREFIX = "anthropic."
_API_REVISION = re.compile(r"-v\d+:\d+$")
_VERSION_TAIL = re.compile(r"-(\d{1,2})-(\d{1,2})(?=-\d{8}|@|\[|$)")
# What may follow a row's key in an id that row prices: a dated snapshot of the same model
# (``-20251001``, ``-2024-08-06``, ``-0613``), a ``@`` snapshot or a ``[…]`` variant tag. Never a
# version part (``.5``, ``-6``) or a name (``-mini``, ``-tts``): those are other models.
_SNAPSHOT = re.compile(r"(?:-\d{4,}|@|\[)")


@dataclass(frozen=True)
class PriceRow:
    """One model's row in the shipped table, and the key that priced it.

    ``key`` is the row's model id, which differs from the id asked about when that id is a
    profile, a service's spelling or a dated snapshot of it. ``fields`` is the row as the table
    holds it: the rates in its ``unit``. ``vendor`` is whose list price it is and ``recorded``
    the day the table recorded it (``YYYY-MM-DD``).
    """

    key: str
    fields: Mapping[str, Any]
    vendor: str
    recorded: str

    @property
    def unit(self) -> str:
        return str(self.fields.get("unit", "") or "token")


def _candidates(model: str) -> list[str]:
    """The spellings *model* is looked up by, in order: itself, its base model's id when it is a
    profile's, that id without a vendor prefix or API revision, and that as Anthropic names the
    model, dotted (see :data:`_ANTHROPIC_PREFIX`)."""
    base = _PROFILE.sub("", model, count=1)
    bare = _API_REVISION.sub("", base.removeprefix(_ANTHROPIC_PREFIX))
    dotted = _VERSION_TAIL.sub(r"-\1.\2", bare, count=1)
    return list(dict.fromkeys((model, base, bare, dotted)))


def _row(key: str) -> PriceRow:
    fields = _PRICES[key]
    return PriceRow(
        key=key,
        fields=fields,
        vendor=str(fields.get("vendor", "") or ""),
        recorded=str(fields.get("recorded", "") or ""),
    )


def price_row(model: str) -> PriceRow | None:
    """The shipped row that prices *model*, or None when the table has none for it.

    Any spelling of the model's own id first (:func:`_candidates`), then the longest key one of
    those spellings is a dated snapshot of (:data:`_SNAPSHOT`)."""
    if not model:
        return None
    candidates = _candidates(model)
    for candidate in candidates:
        if candidate in _PRICES:
            return _row(candidate)
    for candidate in candidates:
        best = ""
        for key in _PRICES:
            if (
                len(key) > len(best)
                and candidate.startswith(key)
                and _SNAPSHOT.match(candidate, len(key))
            ):
                best = key
        if best:
            return _row(best)
    return None
