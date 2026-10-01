"""What a model call costs — :func:`price_call`, the one pricing function.

Every dollar PersonalClaw shows or holds to a cap is priced here: the spend guard's charge against
the daily and per-run caps, an agent CLI's metered turn, the usage row the Usage page sums, a chat
turn's cost line and its cache saving, a subagent's cost and the router's cost-aware ordering. They
priced from the shipped table alone before (``pricing.estimate_cost``), so a rate the owner set and
a rate an app declared reached only the router, and a model the table has no row for counted as $0
against a cap. Only this module reads that table now
(``tests/test_every_dollar_is_priced_by_one_function.py``).

A call's price is what its provider reported when it reported one, else what it used at the
effective rate, in the unit its model is billed in (:data:`UNITS`): its tokens for a text model
(:func:`price_call`), and for an image, video, speech-to-text or text-to-speech model the images,
seconds, minutes or characters the call was billed for (:func:`price_units`, which the metering
seam ``guardrails.media_call`` prices every such call by). A row in one unit is no price for a call
in another. The effective rate resolves through a **total, explicit precedence**:

1. **overlay** — the prices you set, for any provider and model and over any known price:
   ``config.json`` → ``model_prices.overrides`` (``config/pricing.py``), set, edited and reset in
   Settings → Usage → Model prices (:func:`set_rate`, :func:`clear_rate`, through the one
   ``config.json`` writer, ``config.transactions.mutate_config``). Prices drift, and whoever runs a
   model may bill differently from its maker; a personal tool must let its owner say so without
   shipping a new app. Read fresh on every call (stat-keyed memo), so a change is the answer on the
   very next call with **no restart-order dependency**. The prices set before lived in
   ``model_rates.json``; the gateway's boot carries them over once
   (:func:`adopt_prices_set_before`).
2. **local** — a model this machine serves itself prices ``0.0``: its cost axis is
   latency/energy, not dollars. This is a real, known price, NOT an absence. The model runs here
   when its entry's type runs its models inside the gateway, or runs them where its endpoint is
   and that endpoint is on this machine (:func:`personalclaw.llm.registry.served_on_this_machine`),
   and an engine an app registered (speech, embedding, image) runs here by what it declares
   (``providers.engines``).
   An endpoint on this machine alone does not make a model free: an OpenAI-compatible instance
   there may be a proxy for a paid cloud API, so it is priced by the tiers below, by its model's
   id, and is unpriced when none of them knows that id.
3. **app default** — the declaration of the app that registered the entry's TYPE:
   :attr:`~personalclaw.sdk.provider_helpers.BrandedProviderSpec.pricing`
   (``{model_pattern: {in_per_mtok, out_per_mtok}}``), read from the live app registration, so a
   branded app ships its prices in the same place as its ``default_model``/``capabilities``. Found
   by the configured entry's type, never its name: ``acme-proxy`` may be an OpenAI-compatible
   entry, and ``work`` an Acme one.
4. **builtin** — core's shipped ``model_pricing.json`` table (:mod:`personalclaw.pricing`), i.e.
   the app-default tier for core-bundled model families: each row the model maker's list price,
   naming whose it is and the day the table recorded it, found by any id a service calls the
   model by (a Bedrock inference profile is priced as its base model).
5. **absent** — :data:`None`: the call is **unpriced**.

**Absent is None, never 0.0.** A fabricated zero would report an unpriced cloud model as *free*,
which is the one wrong answer a spend meter must never give. ``0.0`` is reserved for prices we
actually know are zero (tier 2, or an explicit overlay/app entry: a price of $0 set for an
instance, ``work:*``, is how its owner declares it free). So a price says whether it is known
(:attr:`CallPrice.priced`), and a dollar cap refuses a call it has no price for rather than
counting it as free (``guardrails.budgets.SpendMeter.admit``).

Every read is **fail-open** to the next tier: an unreadable or corrupt overlay logs once and the
tier below answers, and a lookup that fails outright reads as unpriced. Pricing never breaks a
routing decision or a model call.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Where the prices you set were kept before they joined your configuration; read once, at boot
#: (:func:`adopt_prices_set_before`), and removed.
_PRICES_SET_BEFORE = "model_rates.json"
#: A rate row's fields, each USD per 1,000,000 tokens of one bucket of a call, and what a person
#: calls each. The first two are required; a row that names no cache rate bills cached tokens as
#: plain input.
RATE_FIELDS: dict[str, str] = {
    "in_per_mtok": "input",
    "out_per_mtok": "output",
    "cache_read_per_mtok": "cache read",
    "cache_write_per_mtok": "cache write",
}
_REQUIRED_RATE_FIELDS = ("in_per_mtok", "out_per_mtok")
#: The longest key a rate is stored under. A model ref is far shorter; a longer one is no model.
MAX_KEY_CHARS = 200

#: The units a model is billed in, and what one of each is as a person reads a price. A row that
#: names no ``unit`` is priced per 1M tokens. The others are the ones the media models first-party
#: providers serve are billed in: an image model per image (by its size and quality where the
#: vendor prices it so), a video model per second of video, speech-to-text per minute of audio
#: and text-to-speech per 1M characters.
UNITS: dict[str, str] = {
    "token": "1M tokens",
    "image": "image",
    "second": "second of video",
    "minute": "minute of audio",
    "character": "1M characters",
}
#: The field a row priced in a unit other than tokens holds its price in.
UNIT_PRICE_FIELDS: dict[str, str] = {
    "image": "per_image",
    "second": "per_second",
    "minute": "per_minute",
    "character": "per_mchar",
}
#: The fields of an image row priced by size and quality (:class:`ImageTier`).
_IMAGE_FIELDS = ("tiers", "default_size", "default_quality")
#: The most tiers an image price holds: a vendor prices a handful of sizes and qualities.
MAX_TIERS = 24


@dataclass(frozen=True)
class ModelRate:
    """USD per 1,000,000 tokens for one (provider, model), plus WHERE it came from.

    ``source`` is excluded from equality so a test can assert a rate's value without pinning the
    tier it resolved through; :func:`rate_for` callers that care read it explicitly.

    The two cache rates are what a token the provider READ from its prompt cache and one it WROTE
    into it cost. A rate that names neither bills both as plain input, the price of the same token
    uncached: a cache discount is the provider's to state, and a row that states none is not
    evidence of one. The shipped table names both for the families that cache.

    A shipped rate also says whose list price it is (``vendor``), the day the table recorded it
    (``recorded``) and the row it was found as (``priced_as``), which Model prices shows; a rate
    from any other tier leaves them empty. None of the three counts toward equality.
    """

    in_per_mtok: float
    out_per_mtok: float
    source: str = field(default="", compare=False)
    cache_read_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None
    vendor: str = field(default="", compare=False)
    recorded: str = field(default="", compare=False)
    priced_as: str = field(default="", compare=False)

    @property
    def unit(self) -> str:
        return "token"

    def cost(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
    ) -> float:
        """USD for a token count at this rate: the three prompt buckets and the output, each at
        its own rate (they are disjoint populations, see ``stats.cache_hit_pct``)."""
        read_rate = (
            self.in_per_mtok if self.cache_read_per_mtok is None else self.cache_read_per_mtok
        )
        write_rate = (
            self.in_per_mtok if self.cache_write_per_mtok is None else self.cache_write_per_mtok
        )
        cost = (
            (input_tokens or 0) * self.in_per_mtok
            + (output_tokens or 0) * self.out_per_mtok
            + (cache_read_tokens or 0) * read_rate
            + (cache_creation_tokens or 0) * write_rate
        ) / 1_000_000.0
        return round(cost, 6)

    def dearest_per_mtok(self) -> tuple[float, float]:
        """USD per 1M prompt and answer tokens at the most this rate bills a token of each: a
        prompt's at the dearest of its input, cache-read and cache-write rates. What a call about
        to be made sets aside is weighed at it, before anyone knows how its prompt is billed."""
        prompt = max(
            self.in_per_mtok,
            self.cache_read_per_mtok or 0.0,
            self.cache_write_per_mtok or 0.0,
        )
        return prompt, self.out_per_mtok

    def to_dict(self) -> dict[str, float]:
        out = {"in_per_mtok": self.in_per_mtok, "out_per_mtok": self.out_per_mtok}
        if self.cache_read_per_mtok is not None:
            out["cache_read_per_mtok"] = self.cache_read_per_mtok
        if self.cache_write_per_mtok is not None:
            out["cache_write_per_mtok"] = self.cache_write_per_mtok
        return out

    @classmethod
    def from_obj(cls, obj: Any, *, source: str = "") -> ModelRate | None:
        """Normalize a declared rate row (``{"in_per_mtok":…, "out_per_mtok":…}``, and optionally
        ``cache_read_per_mtok``/``cache_write_per_mtok``) into a :class:`ModelRate`. A row that
        carries neither in nor out is not a rate → None (an app or a hand-edited overlay must not
        turn a typo into a free model)."""
        if isinstance(obj, ModelRate):
            return replace(obj, source=source or obj.source)
        if not isinstance(obj, dict) or row_unit(obj) != "token":
            return None
        if "in_per_mtok" not in obj and "out_per_mtok" not in obj:
            return None
        try:
            return cls(
                in_per_mtok=float(obj.get("in_per_mtok", 0.0) or 0.0),
                out_per_mtok=float(obj.get("out_per_mtok", 0.0) or 0.0),
                source=source,
                cache_read_per_mtok=_optional_rate(obj, "cache_read_per_mtok"),
                cache_write_per_mtok=_optional_rate(obj, "cache_write_per_mtok"),
            )
        except (TypeError, ValueError):
            return None


def _optional_rate(row: dict, key: str) -> float | None:
    """A cache rate a row names, or ``None`` when it names none (billed as plain input)."""
    value = row.get(key)
    return None if value is None else float(value)


def row_unit(row: object) -> str:
    """The unit a price row is quoted in: ``token`` when it names none."""
    if not isinstance(row, dict):
        return "token"
    return str(row.get("unit", "") or "token")


def image_area(size: str) -> int | None:
    """The pixels of a ``WIDTHxHEIGHT`` size (``1024x1024``), or None when it is not one."""
    width, sep, height = str(size or "").strip().lower().partition("x")
    if not sep or not width.isdigit() or not height.isdigit():
        return None
    area = int(width) * int(height)
    return area if area > 0 else None


@dataclass(frozen=True)
class ImageTier:
    """One price of an image model priced by size and quality.

    ``size`` is the largest image it covers (``WIDTHxHEIGHT``, by pixel count, as vendors price
    "up to 1024 x 1024"), ``""`` for any size; ``quality`` the quality it is for, ``""`` for any."""

    size: str = ""
    quality: str = ""
    per_image: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"size": self.size, "quality": self.quality, "per_image": self.per_image}


@dataclass(frozen=True)
class UnitRate:
    """What one model costs per unit of what it is billed for: an image, a second of video, a
    minute of audio or 1M characters (:data:`UNITS`), plus WHERE it came from.

    ``per_unit`` is the price of one unit. An image model priced by size and quality holds its
    prices as :class:`ImageTier` ``tiers`` instead, with the size and quality a call that names
    none is made at (``default_size``, ``default_quality``). ``source``, ``vendor``, ``recorded``
    and ``priced_as`` are what :class:`ModelRate` says they are and count toward no equality.
    """

    unit: str
    per_unit: float = 0.0
    tiers: tuple[ImageTier, ...] = ()
    default_size: str = ""
    default_quality: str = ""
    source: str = field(default="", compare=False)
    vendor: str = field(default="", compare=False)
    recorded: str = field(default="", compare=False)
    priced_as: str = field(default="", compare=False)

    def unit_price(self, *, size: str = "", quality: str = "") -> float | None:
        """The price of one unit of a call at *size* and *quality*; None when no price covers it.

        A call that names no size or quality is made at the defaults. Of the tiers for its
        quality (or for any) that cover its size (or any size), the smallest one prices it: the
        vendor's "up to" tier it falls in. A size beyond every tier is not priced."""
        if not self.tiers:
            return self.per_unit
        want_quality = str(quality or self.default_quality or "").strip().lower()
        want_size = str(size or self.default_size or "").strip()
        area = image_area(want_size) if want_size else None
        if want_size and area is None:
            return None
        best: tuple[tuple[float, int], float] | None = None
        for tier in self.tiers:
            if tier.quality and tier.quality.strip().lower() != want_quality:
                continue
            covers: float = math.inf
            if tier.size:
                tier_area = image_area(tier.size)
                if area is None or tier_area is None or tier_area < area:
                    continue
                covers = float(tier_area)
            rank = (covers, 0 if tier.quality else 1)
            if best is None or rank < best[0]:
                best = (rank, tier.per_image)
        return None if best is None else best[1]

    def cost(self, quantity: float | None, *, size: str = "", quality: str = "") -> float | None:
        """USD for *quantity* units at *size* and *quality*, or None when no price covers the call
        or how much of the unit it was is not known; a unit known to cost nothing costs nothing
        however much of it there is."""
        price = self.unit_price(size=size, quality=quality)
        if price is None:
            return None
        if price == 0.0:
            return 0.0
        if quantity is None:
            return None
        units = max(0.0, float(quantity))
        dollars = units * price / 1_000_000.0 if self.unit == "character" else units * price
        return round(dollars, 6)

    def to_dict(self) -> dict[str, Any]:
        """The row as the overlay stores it."""
        out: dict[str, Any] = {"unit": self.unit}
        if self.tiers:
            out["tiers"] = [tier.to_dict() for tier in self.tiers]
            if self.default_size:
                out["default_size"] = self.default_size
            if self.default_quality:
                out["default_quality"] = self.default_quality
        else:
            out[UNIT_PRICE_FIELDS[self.unit]] = self.per_unit
        return out

    @classmethod
    def from_obj(cls, obj: Any, *, source: str = "", unit: str) -> UnitRate | None:
        """Normalize a declared row in *unit* into a :class:`UnitRate`, or None when it is a row in
        another unit or holds no price (a typo never reads as a free model)."""
        if isinstance(obj, UnitRate):
            return replace(obj, source=source or obj.source) if obj.unit == unit else None
        if unit not in UNIT_PRICE_FIELDS or not isinstance(obj, dict) or row_unit(obj) != unit:
            return None
        try:
            tiers = tuple(
                ImageTier(
                    size=str(t.get("size", "") or ""),
                    quality=str(t.get("quality", "") or ""),
                    per_image=float(t["per_image"]),
                )
                for t in (obj.get("tiers") or ())
                if isinstance(t, dict) and t.get("per_image") is not None
            )
            if unit == "image" and tiers:
                return cls(
                    unit=unit,
                    tiers=tiers,
                    default_size=str(obj.get("default_size", "") or ""),
                    default_quality=str(obj.get("default_quality", "") or ""),
                    source=source,
                )
            price = obj.get(UNIT_PRICE_FIELDS[unit])
            if price is None:
                return None
            return cls(unit=unit, per_unit=float(price), source=source)
        except (TypeError, ValueError, KeyError):
            return None


@dataclass(frozen=True)
class CallPrice:
    """What one model call cost, and whether that is known.

    ``priced`` is False when nothing prices the call: its provider reported no cost and no tier
    has a rate for its model. ``dollars`` is then 0.0, which is NOT a price — a caller shows the
    call as unpriced and a cap counts it as a call it could not count (``SpendMeter.charge``).
    ``source`` is ``"reported"`` for the provider's own figure, else the tier the rate came from
    (:attr:`ModelRate.source`), ``"mixed"`` for the calls of a turn priced from more than one
    (:func:`summed`), and ``""`` when unpriced.
    """

    dollars: float
    priced: bool
    source: str = ""


def ref_of(provider: str, model: str) -> str:
    """The ``active_models.json``-spelling ref for a (provider, model) — see
    :func:`personalclaw.routing.stats.ref_of` (same spelling, kept in one place)."""
    from personalclaw.routing.stats import ref_of as _ref_of

    return _ref_of(provider, model)


# ── The overlay store: your prices, in config.json ──────────────────────────────────────


def _config_file(home: Path | None) -> Path:
    """The ``config.json`` your prices are kept in: the home's, or *home*'s."""
    if home is not None:
        return Path(home) / "config.json"
    from personalclaw.config.loader import config_path

    return config_path()


#: (path, inode, mtime_ns, size) → your prices as read. Keyed on the stat so an EDIT (including an
#: atomic rename, which changes the inode) is picked up on the very next call — there is no
#: import-time snapshot and therefore no restart-order dependency.
_overlay_cache: tuple[tuple[str, int, int, int], dict[str, Any]] | None = None


class RatesUnreadable(Exception):
    """``config.json`` is there and cannot be read: no price you set is in effect, and a save would
    replace whatever it holds. Says why, as a person reads it."""


def _read_overrides(path: Path) -> dict[str, Any] | None:
    """Your prices as *path* holds them (``config/pricing.price_overrides``), or ``None`` when
    there is no file.

    Raises :class:`RatesUnreadable` for a file that cannot be read or is not a JSON object. The
    one reader: the fail-open read the prices come from and the view ask the same question, so
    they cannot disagree about a file.
    """
    from personalclaw.config.pricing import price_overrides

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise RatesUnreadable(f"{path.name} could not be read ({exc})") from exc
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RatesUnreadable(f"{path.name} is not valid JSON (line {exc.lineno})") from exc
    if not isinstance(data, dict):
        raise RatesUnreadable(f"{path.name} holds no settings object")
    return price_overrides(data.get("model_prices"))


def load_overlay(home: Path | None = None) -> dict[str, Any]:
    """Your prices, by key. A missing or unreadable ``config.json`` reads as none (fail-open: the
    next tier answers), and the failure is logged, not raised."""
    global _overlay_cache
    path = _config_file(home)
    try:
        st = os.stat(path)
        key = (str(path), int(st.st_ino), int(st.st_mtime_ns), int(st.st_size))
    except (FileNotFoundError, OSError):
        return {}
    cached = _overlay_cache
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        overrides = _read_overrides(path) or {}
    except RatesUnreadable as exc:
        logger.warning("%s — no price you set is in effect", exc)
        return {}
    _overlay_cache = (key, overrides)
    return overrides


def _change_overrides(change: Callable[[dict[str, Any]], T], home: Path | None) -> T:
    """Apply *change* to your prices in ``config.json``, through its one writer
    (``config.transactions.mutate_config``), and answer what it returned. Raises
    :class:`RatesUnreadable` when the file cannot be read: writing it would replace what it
    holds."""
    from personalclaw.config.loader import ConfigPreserveError
    from personalclaw.config.pricing import price_overrides
    from personalclaw.config.transactions import mutate_config

    def mutate(document: dict[str, Any]) -> T:
        section = document.get("model_prices")
        overrides = price_overrides(section)
        result = change(overrides)
        document["model_prices"] = {
            **(section if isinstance(section, dict) else {}),
            "overrides": overrides,
        }
        return result

    try:
        return mutate_config(mutate, path=_config_file(home))
    except ConfigPreserveError as exc:
        raise RatesUnreadable(str(exc)) from exc


def save_overlay(rates: dict[str, Any], *, home: Path | None = None) -> Path:
    """Replace your prices with *rates* (key → row; see :func:`_match_key` for the key forms), and
    answer the ``config.json`` they are kept in."""

    def replace_all(overrides: dict[str, Any]) -> None:
        overrides.clear()
        overrides.update({key: dict(row) for key, row in rates.items()})

    _change_overrides(replace_all, home)
    return _config_file(home)


def adopt_prices_set_before(*, home: Path | None = None) -> int:
    """Carry the prices kept in ``model_rates.json`` into your configuration, once, and remove the
    file; how many rows it carried. A price already in ``config.json`` wins, and a row that was no
    price, in effect nowhere, is left behind. Run by the gateway's boot, before the config is
    loaded (``cli_server._boot_config``): nothing reads the file after it.

    A file that cannot be read is left where it is and said in the log: its prices were never in
    effect, and are set again in Settings → Usage → Model prices.
    """
    legacy = (Path(home) if home is not None else _config_file(None).parent) / _PRICES_SET_BEFORE
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
        rows = data.get("rates") if isinstance(data, dict) else None
        if not isinstance(rows, dict):
            raise ValueError('it holds no "rates" object')
    except FileNotFoundError:
        return 0
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        logger.warning(
            "%s could not be read (%s), so its prices were not carried into Settings → Usage → "
            "Model prices: set them there, then remove the file",
            legacy,
            exc,
        )
        return 0
    prices = {
        key: dict(row)
        for key, row in rows.items()
        if isinstance(key, str) and _as_rate(row, source="overlay", unit=None) is not None
    }

    def adopt(overrides: dict[str, Any]) -> int:
        new = {key: row for key, row in prices.items() if key not in overrides}
        overrides.update(new)
        return len(new)

    try:
        carried = _change_overrides(adopt, home)
    except Exception:  # noqa: BLE001 — boot goes on; the file stays to be carried next boot
        logger.warning("the prices in %s could not be carried over", legacy, exc_info=True)
        return 0
    legacy.unlink(missing_ok=True)
    logger.info("carried %d price(s) from %s into config.json", carried, legacy.name)
    return carried


# ── Setting one rate (Settings → Usage → Model prices) ──────────────────────────────────


def _dollars(value: object, what: str, per: str) -> float:
    """*value* as a price of zero or more USD per *per*, or ``ValueError`` naming *what*."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"The {what} must be a number of dollars per {per}.")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"The {what} must be zero or more dollars per {per}.")
    return float(value)


def _size(value: object, what: str) -> str:
    """*value* as an image size, ``WIDTHxHEIGHT``, or ``""``; ``ValueError`` naming *what*."""
    text = str(value or "").strip().lower()
    if text and image_area(text) is None:
        raise ValueError(f"The {what} is a width x height in pixels, such as 1024x1024.")
    return text


def _quality(value: object, what: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) > 40 or any(ch.isspace() or not ch.isprintable() for ch in text):
        raise ValueError(f"The {what} is one word, such as standard or premium.")
    return text


def _token_row(body: dict) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for name, label in RATE_FIELDS.items():
        value = body.get(name)
        if value is None:
            if name in _REQUIRED_RATE_FIELDS:
                raise ValueError(f"Give the {label} rate, in dollars per 1M tokens.")
            continue
        row[name] = _dollars(value, f"{label} rate", "1M tokens")
    return row


def _image_row(body: dict) -> dict[str, Any]:
    tiers = body.get("tiers")
    flat = body.get("per_image")
    if tiers and flat is not None:
        raise ValueError("Give either one price per image or prices by size and quality, not both.")
    if not tiers:
        if flat is None:
            raise ValueError("Give the price per image, or prices by size and quality.")
        if body.get("default_size") or body.get("default_quality"):
            raise ValueError("A default size or quality goes with prices by size and quality.")
        return {"unit": "image", "per_image": _dollars(flat, "price per image", "image")}
    if not isinstance(tiers, list) or len(tiers) > MAX_TIERS:
        raise ValueError(f"Give at most {MAX_TIERS} prices by size and quality, as a list.")
    out: list[dict[str, Any]] = []
    for tier in tiers:
        if not isinstance(tier, dict):
            raise ValueError("Each price by size and quality is one object.")
        extra = sorted(str(k) for k in tier if k not in ("size", "quality", "per_image"))
        if extra:
            raise ValueError(f"A price by size and quality has no field {extra[0]!r}.")
        if tier.get("per_image") is None:
            raise ValueError("Give each size and quality its price per image.")
        out.append(
            {
                "size": _size(tier.get("size"), "size"),
                "quality": _quality(tier.get("quality"), "quality"),
                "per_image": _dollars(tier["per_image"], "price per image", "image"),
            }
        )
    row: dict[str, Any] = {"unit": "image", "tiers": out}
    default_size = _size(body.get("default_size"), "default size")
    default_quality = _quality(body.get("default_quality"), "default quality")
    if default_size:
        row["default_size"] = default_size
    if default_quality:
        row["default_quality"] = default_quality
    return row


def rate_entry(body: object) -> tuple[str, dict[str, Any]]:
    """``(key, row)`` from a request to set one rate, or ``ValueError`` saying what is wrong.

    The key is a ``provider:model`` ref, a pattern over refs (``anthropic:claude-*``) or a model
    spelled alone, which prices that model whoever serves it, this machine included (a price you
    set comes before this machine's $0). ``unit`` is what the model is billed in
    (:data:`UNITS`), per 1M tokens when it names none. Per 1M tokens the input and output rates
    are required and the cache rates optional (a row that names neither bills cached tokens as
    plain input); per image, one price or prices by size and quality (:class:`ImageTier`) with
    the size and quality a call that names none is made at; per second, minute or 1M characters,
    that one price. Each price is a finite number of USD, zero or more, and a field nothing reads
    is refused rather than stored.
    """
    if not isinstance(body, dict):
        raise ValueError("Send the model's key and its rates as one JSON object.")
    unit = str(body.get("unit") or "token")
    if unit not in UNITS:
        raise ValueError(
            "A price is per 1M tokens, image, second of video, minute of audio or 1M characters."
        )
    if unit == "token":
        allowed: tuple[str, ...] = tuple(RATE_FIELDS)
    elif unit == "image":
        allowed = ("per_image", *_IMAGE_FIELDS)
    else:
        allowed = (UNIT_PRICE_FIELDS[unit],)
    unknown = sorted(str(k) for k in body if k not in ("key", "unit", *allowed))
    if unknown:
        raise ValueError(f"A rate has no field {unknown[0]!r}.")
    key = body.get("key")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("Name the model the rate is for, as provider:model or a model name.")
    key = key.strip()
    if len(key) > MAX_KEY_CHARS:
        raise ValueError(f"A model key is at most {MAX_KEY_CHARS} characters.")
    if any(ch.isspace() or not ch.isprintable() for ch in key):
        raise ValueError("A model key has no spaces or control characters.")
    if unit == "token":
        return key, _token_row(body)
    if unit == "image":
        return key, _image_row(body)
    field_name = UNIT_PRICE_FIELDS[unit]
    if body.get(field_name) is None:
        raise ValueError(f"Give the price per {UNITS[unit]}.")
    return key, {"unit": unit, field_name: _dollars(body[field_name], "price", UNITS[unit])}


def set_rate(key: str, row: dict[str, Any], *, home: Path | None = None) -> None:
    """Price *key* at *row* (:func:`rate_entry` validates both), recording the day it was set
    (``recorded``, the machine's local day) and leaving every other price as it is stored now.
    Raises :class:`RatesUnreadable` when ``config.json`` cannot be read."""
    from personalclaw import spend_day

    priced = {**dict(row), "recorded": spend_day.today()}

    def put(overrides: dict[str, Any]) -> None:
        overrides[key] = priced

    _change_overrides(put, home)


def clear_rate(key: str, *, home: Path | None = None) -> bool:
    """Remove your price for *key*, so the model's default prices it again; whether there was
    one. Raises :class:`RatesUnreadable` when ``config.json`` cannot be read."""

    def remove(overrides: dict[str, Any]) -> bool:
        return overrides.pop(key, None) is not None

    return _change_overrides(remove, home)


#: A model's price, in whichever unit it is billed in.
Rate = ModelRate | UnitRate


def _rate_fields(rate: Rate | None) -> dict[str, Any]:
    """A rate's fields as the page reads them: its unit and its price in that unit, every other
    unit's fields ``None``; all of them ``None`` when nothing prices the model."""
    token = rate if isinstance(rate, ModelRate) else None
    per_unit = rate if isinstance(rate, UnitRate) else None
    return {
        "unit": rate.unit if rate is not None else "",
        "in_per_mtok": token.in_per_mtok if token else None,
        "out_per_mtok": token.out_per_mtok if token else None,
        "cache_read_per_mtok": token.cache_read_per_mtok if token else None,
        "cache_write_per_mtok": token.cache_write_per_mtok if token else None,
        "per_unit": per_unit.per_unit if per_unit and not per_unit.tiers else None,
        "tiers": [t.to_dict() for t in per_unit.tiers] if per_unit else [],
        "default_size": per_unit.default_size if per_unit else "",
        "default_quality": per_unit.default_quality if per_unit else "",
    }


def _overlay_row_fields(rate: Rate) -> dict[str, Any]:
    """A rate set in the overlay, as the page lists it: only its own unit's fields."""
    fields = _rate_fields(rate)
    if isinstance(rate, ModelRate):
        return {"unit": "token", **{name: fields[name] for name in RATE_FIELDS}}
    return {
        "unit": rate.unit,
        "per_unit": fields["per_unit"],
        "tiers": fields["tiers"],
        "default_size": fields["default_size"],
        "default_quality": fields["default_quality"],
    }


def _as_rate(row: object, *, source: str, unit: str | None) -> Rate | None:
    """*row* as a rate in *unit* (any unit when None), or None when it is no rate in it."""
    row_in = row_unit(row) if isinstance(row, dict) else getattr(row, "unit", "token")
    if unit is not None and row_in != unit:
        return None
    if row_in == "token":
        return ModelRate.from_obj(row, source=source)
    return UnitRate.from_obj(row, source=source, unit=row_in)


def _provenance(rate: Rate | None) -> dict[str, Any]:
    """Where *rate* comes from, as the page says it: its tier (``source``), whose price it is
    (``vendor``: the model maker for the table, the app's provider type for an app), its date
    (``recorded``: the day the table recorded its row, or the day you set yours) and the row it
    was found as (``priced_as``), with its fields in its unit."""
    return {
        "source": rate.source if rate is not None else "",
        "vendor": rate.vendor if rate is not None else "",
        "recorded": rate.recorded if rate is not None else "",
        "priced_as": rate.priced_as if rate is not None else "",
        **_rate_fields(rate),
    }


def rates_view(models: Iterable[tuple[str, str]], *, home: Path | None = None) -> dict[str, Any]:
    """What Settings → Usage → Model prices shows.

    ``rates`` — every price you set, by key, with its ``unit``, its price in that unit and the day
    you set it (``recorded``); a row that is no rate is not in effect and not listed. ``models`` —
    each ``(provider, model)`` *models* names (the models the uses are bound to, and the ones that
    were used lately), keyed by its ``ref``, the spelling a price is set under: the rate a call to
    it is counted at, in the unit it is billed in, and where it comes from (``source``:
    ``overlay``, ``local``, ``app_default``, ``builtin``, with its ``vendor``, ``recorded`` and
    ``priced_as``), or ``priced: false`` when nothing prices it; and, when the price is yours,
    ``default``: the price resetting yours brings back, in the same shape (``None`` when nothing
    else prices the model). ``unreadable`` — why ``config.json`` could not be read, when it could
    not: no price you set is then in effect, and a change is refused until it is fixed.
    """
    unreadable = ""
    try:
        stored = _read_overrides(_config_file(home)) or {}
    except RatesUnreadable as exc:
        unreadable = str(exc)
        stored = {}
    rows: list[dict[str, Any]] = []
    for key, value in sorted(stored.items()):
        set_rate_row = _as_rate(value, source="overlay", unit=None)
        if set_rate_row is not None:
            rows.append(
                {
                    "key": key,
                    **_overlay_row_fields(set_rate_row),
                    "recorded": str(value.get("recorded", "") or ""),
                }
            )
    listed: list[dict[str, Any]] = []
    for provider, model in dict.fromkeys((str(p), str(m)) for p, m in models):
        if not provider or not model:
            continue  # a ref that names no model has no model to price
        rate = _price_or_none(provider, model, None, home)
        default = None
        if rate is not None and rate.source == "overlay":
            try:
                under = _default_rate(provider, model, rate.unit)
            except Exception:  # noqa: BLE001 — a lookup that fails reads as no default
                logger.warning("default price lookup failed for %s:%s", provider, model)
                under = None
            default = _provenance(under) if under is not None else None
        listed.append(
            {
                "ref": ref_of(provider, model),
                "priced": rate is not None,
                **_provenance(rate),
                "default": default,
            }
        )
    return {"rates": rows, "models": listed, "unreadable": unreadable}


# ── Resolution ───────────────────────────────────────────────────────────────────────────


def _match_key(table: dict[str, Any], candidates: list[str], unit: str | None = None) -> Any:
    """Find ``table``'s row in *unit* (any unit when None) for the first matching candidate.

    For each candidate in order: an EXACT key wins, else the LONGEST matching glob pattern
    (``anthropic:claude-sonnet-*``) — longest so a specific pattern beats a catch-all ``*``. A row
    in another unit is no price for a call in this one, so it is passed over.
    """

    def _in_unit(value: Any) -> bool:
        return unit is None or _as_rate(value, source="", unit=unit) is not None

    for candidate in candidates:
        row = table.get(candidate)
        if row is not None and _in_unit(row):
            return row
        best: tuple[int, Any] | None = None
        for key, value in table.items():
            if not isinstance(key, str) or not any(c in key for c in "*?["):
                continue
            if (
                fnmatchcase(candidate, key)
                and (best is None or len(key) > best[0])
                and _in_unit(value)
            ):
                best = (len(key), value)
        if best is not None:
            return best[1]
    return None


def _overlay_rate(provider: str, model: str, unit: str | None, home: Path | None) -> Rate | None:
    """Tier 1. Keys may be an exact ref (``anthropic:claude-sonnet-4.5``), a ref glob
    (``anthropic:claude-*``) or a bare model spelling (``claude-*``, provider-agnostic)."""
    table = load_overlay(home)
    if not table:
        return None
    row = _match_key(table, [ref_of(provider, model), model], unit)
    rate = _as_rate(row, source="overlay", unit=unit) if row is not None else None
    # The day you set it, which Model prices says beside it.
    return replace(rate, recorded=str(row.get("recorded", "") or "")) if rate is not None else None


def _app_default_rate(provider: str, model: str, unit: str | None) -> Rate | None:
    """Tier 3. The ``BrandedProviderSpec.pricing`` of the app that registered the TYPE of the
    entry named ``provider`` (keyed by model pattern, so no provider prefix). Read live from the
    registration — an app installed after import is visible on the next call.

    By the entry's type, never its name: a name says nothing about what serves it. A name no
    configured entry has has no type, so nothing an app declared prices it."""
    try:
        import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
        from personalclaw.llm.branded_specs import spec_pricing
        from personalclaw.llm.registry import ProviderResolutionError, get_default_registry

        try:
            provider_type = get_default_registry().get_entry(str(provider or "").strip()).type
        except ProviderResolutionError:
            return None
        table = spec_pricing(provider_type)
    except Exception:  # noqa: BLE001 — a pricing lookup must never break a call
        logger.warning("app pricing lookup failed for provider %r", provider, exc_info=True)
        return None
    if not table:
        return None
    row = _match_key(dict(table), [model], unit)
    rate = _as_rate(row, source="app_default", unit=unit) if row is not None else None
    # Whose price it is: the app that serves this provider type.
    return replace(rate, vendor=provider_type) if rate is not None else None


def _builtin_rate(model: str, unit: str | None) -> Rate | None:
    """Tier 4. Core's shipped ``model_pricing.json`` (:mod:`personalclaw.pricing`), in the unit
    its row is quoted in, saying whose list price it is and the row it was found as."""
    try:
        from personalclaw.pricing import price_row

        found = price_row(model)
        if found is None or (unit is not None and found.unit != unit):
            return None
        row = found.fields
        if found.unit != "token":
            rate = UnitRate.from_obj(dict(row), source="builtin", unit=found.unit)
            if rate is None:
                return None
            return replace(rate, vendor=found.vendor, recorded=found.recorded, priced_as=found.key)
        return ModelRate(
            in_per_mtok=float(row.get("in", 0.0)),
            out_per_mtok=float(row.get("out", 0.0)),
            source="builtin",
            # A row that names no cache rate bills cached tokens as plain input, as any rate does.
            cache_read_per_mtok=_optional_rate(dict(row), "cache_read"),
            cache_write_per_mtok=_optional_rate(dict(row), "cache_write"),
            vendor=found.vendor,
            recorded=found.recorded,
            priced_as=found.key,
        )
    except Exception:  # noqa: BLE001
        logger.warning("builtin pricing lookup failed for model %r", model, exc_info=True)
        return None


def _local(unit: str | None) -> Rate:
    """A model this machine runs itself, at its known $0, in *unit*."""
    if unit is None or unit == "token":
        return ModelRate(0.0, 0.0, source="local")
    return UnitRate(unit=unit, per_unit=0.0, source="local")


def _price(provider: str, model: str, unit: str | None, home: Path | None) -> Rate | None:
    """The effective rate for one (provider, model) in *unit* (any unit when None) — **overlay >
    local > app default > builtin > absent**, the first hit winning. A tier whose row for the
    model is in another unit has no price for a call in this one, and the next tier answers."""
    if not str(model or "").strip():
        return None
    overlay = _overlay_rate(provider, model, unit, home)
    if overlay is not None:
        return overlay
    return _default_rate(provider, model, unit)


def _default_rate(provider: str, model: str, unit: str | None) -> Rate | None:
    """The rate a model has when you set none — **local > app default > builtin > absent** —
    which resetting your price brings back."""
    from personalclaw.llm.registry import served_on_this_machine

    if served_on_this_machine(provider):
        return _local(unit)
    app_default = _app_default_rate(provider, model, unit)
    if app_default is not None:
        return app_default
    return _builtin_rate(model, unit)


def _price_or_none(provider: str, model: str, unit: str | None, home: Path | None) -> Rate | None:
    """:func:`_price`, with a lookup that fails outright read as no rate: unpriced, not free."""
    try:
        return _price(provider, model, unit, home)
    except Exception:  # noqa: BLE001 — pricing never breaks a call; an unknown is unpriced
        logger.warning("rate lookup failed for %s:%s", provider, model, exc_info=True)
        return None


def rate_for(provider: str, model: str, *, home: Path | None = None) -> ModelRate | None:
    """The effective rate per 1M tokens for one (provider, model) (:func:`_price`).

    Returns ``None`` when nothing prices this model's tokens. ``None`` means "unknown", NOT
    "free"; a caller that needs a number must decide what an unknown price means for it rather
    than inheriting a fabricated ``0.0``. A model billed in another unit has no price per token.
    """
    rate = _price(provider, model, "token", home)
    return rate if isinstance(rate, ModelRate) else None


def unit_rate_for(
    provider: str, model: str, unit: str, *, home: Path | None = None
) -> UnitRate | None:
    """The effective rate per *unit* (an image, a second, a minute, 1M characters) for one
    (provider, model) (:func:`_price`), or ``None`` when nothing prices it in that unit."""
    if unit not in UNIT_PRICE_FIELDS:
        return None
    rate = _price(provider, model, unit, home)
    return rate if isinstance(rate, UnitRate) else None


def effective_rate(provider: str, model: str, home: Path | None = None) -> ModelRate | None:
    """:func:`rate_for`, with a lookup that fails outright read as no rate: unpriced, not free.

    What a call is priced at when it settles (:func:`price_call`) and weighed at before it starts:
    the spend caps set aside what a call about to be made may cost at this rate, and refuse one
    it is ``None`` for (``guardrails.budgets.SpendMeter.admit``)."""
    rate = _price_or_none(provider, model, "token", home)
    return rate if isinstance(rate, ModelRate) else None


def price_units(
    provider: str,
    model: str,
    unit: str,
    quantity: float | None,
    *,
    size: str = "",
    quality: str = "",
    home: Path | None = None,
) -> CallPrice:
    """What a call to *model* billed in *unit* costs for *quantity* of it (images, seconds of
    video, minutes of audio, characters) — :func:`price_call` for a call that is not billed in
    tokens. Unpriced when nothing prices the model in that unit, no price covers the call's size
    and quality, or how much of the unit it is is not known (*quantity* None)."""
    rate = _price_or_none(provider, model, unit, home) if unit in UNIT_PRICE_FIELDS else None
    if not isinstance(rate, UnitRate):
        return CallPrice(0.0, False, "")
    dollars = rate.cost(quantity, size=size, quality=quality)
    if dollars is None:
        return CallPrice(0.0, False, "")
    return CallPrice(dollars, True, rate.source)


def price_call(
    provider: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    reported_usd: float = 0.0,
    home: Path | None = None,
) -> CallPrice:
    """What one call to *model* on the provider entry *provider* cost — THE pricing function.

    The provider's own figure wins when it reported one: it is a charge, where anything priced
    here is an estimate. Otherwise the call's tokens at the effective rate (:func:`rate_for`),
    every prompt bucket at its own rate. With neither, the call is unpriced: ``dollars`` is 0.0
    and ``priced`` is False, and a caller must not show or count that as free.
    """
    reported = max(0.0, float(reported_usd or 0.0))
    if reported > 0.0:
        return CallPrice(round(reported, 6), True, "reported")
    rate = effective_rate(provider, model, home)
    if rate is None:
        return CallPrice(0.0, False, "")
    dollars = rate.cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )
    return CallPrice(dollars, True, rate.source)


def summed(prices: Iterable[CallPrice | None]) -> CallPrice | None:
    """What the calls of one turn cost, from each call's own price: the sum, priced only when
    every call was (a total with an unpriced call in it is a floor), and the source they share.
    ``None`` when there were no calls or one of them was not priced where it was made (no guard
    priced it), since a sum without it would not be what the turn cost."""
    known = list(prices)
    if not known or any(price is None for price in known):
        return None
    calls = [price for price in known if price is not None]
    sources = {price.source for price in calls}
    return CallPrice(
        round(sum(price.dollars for price in calls), 6),
        all(price.priced for price in calls),
        sources.pop() if len(sources) == 1 else "mixed",
    )


def price_event(event: object, *, provider: str, model: str, home: Path | None = None) -> CallPrice:
    """:func:`price_call` for the call a terminal ``EVENT_COMPLETE`` reports: its token counts
    and the cost its provider reported (``cost_usd``), read off the event.

    An event whose calls were priced where they were made says so (``LLMEvent.charged``, the
    price the guard charged the spend meter and wrote to the model-call log), and that price is
    the answer: the usage it carries is not priced a second time, so the three figures agree.
    """
    charged = getattr(event, "charged", None)
    if isinstance(charged, CallPrice):
        return charged
    return price_call(
        provider,
        model,
        input_tokens=int(getattr(event, "input_tokens", 0) or 0),
        output_tokens=int(getattr(event, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(event, "cache_read_tokens", 0) or 0),
        cache_creation_tokens=int(getattr(event, "cache_creation_tokens", 0) or 0),
        reported_usd=float(getattr(event, "cost_usd", 0.0) or 0.0),
        home=home,
    )


def cache_savings_usd(
    provider: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    home: Path | None = None,
) -> float | None:
    """USD the prompt cache saved (or cost) on one turn — counterfactual minus actual, both at the
    effective rate, so the saving is priced by the rate its turn's cost is.

    * ``actual`` — what this turn cost with its real cache split.
    * ``counterfactual`` — the same turn with NO prompt cache at all: every cached token billed as
      plain input (``input + cache_read + cache_creation`` as input, cache buckets zero). Sound
      because the three token buckets are DISJOINT populations, not overlapping views of one
      number (``stats.cache_hit_pct``).

    ``None`` — never ``0.0`` — when the model is unpriced: a ``0.0`` would be indistinguishable
    from a priced model that saved nothing. A priced model with no cache activity at all returns
    ``0.0``, which is a real measurement, and so does a local one, whose tokens cost nothing
    cached or not.

    NEGATIVE on a first turn that only WROTE the cache (a write is priced above plain input for
    the families that bill one), returned as-is rather than clamped: a cache write genuinely costs
    more than the uncached call it replaces, and the saving only materializes on the later reads.
    """
    rate = effective_rate(provider, model, home)
    if rate is None:
        return None
    actual = rate.cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )
    uncached_prompt_tokens = (
        (input_tokens or 0) + (cache_read_tokens or 0) + (cache_creation_tokens or 0)
    )
    counterfactual = rate.cost(input_tokens=uncached_prompt_tokens, output_tokens=output_tokens)
    return round(counterfactual - actual, 6)


__all__ = [
    "MAX_KEY_CHARS",
    "RATE_FIELDS",
    "CallPrice",
    "ImageTier",
    "ModelRate",
    "Rate",
    "RatesUnreadable",
    "cache_savings_usd",
    "clear_rate",
    "effective_rate",
    "load_overlay",
    "price_call",
    "price_event",
    "price_units",
    "rate_entry",
    "rate_for",
    "rates_view",
    "ref_of",
    "save_overlay",
    "set_rate",
    "unit_rate_for",
    "UNITS",
    "UNIT_PRICE_FIELDS",
    "UnitRate",
]
