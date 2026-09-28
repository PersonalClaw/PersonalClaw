"""What a model call costs — :func:`price_call`, the one pricing function.

Every dollar PersonalClaw shows or holds to a cap is priced here: the spend guard's charge against
the daily and per-run caps, an agent CLI's metered turn, the usage row the Usage page sums, a chat
turn's cost line and its cache saving, a subagent's cost and the router's cost-aware ordering. They
priced from the shipped table alone before (``pricing.estimate_cost``), so a rate the owner set in
``model_rates.json`` and a rate an app declared reached only the router, and a model the table has
no row for counted as $0 against a cap. Only this module reads that table now
(``tests/test_every_dollar_is_priced_by_one_function.py``).

A call's price is what its provider reported when it reported one, else its tokens at the
effective rate, which resolves through a **total, explicit precedence**:

1. **overlay** — ``~/.personalclaw/model_rates.json`` (``atomic_write``), the prices set in
   Settings → Usage → Model prices (:func:`set_rate`, :func:`clear_rate`). Prices drift; a personal
   tool must let its owner correct them without shipping a new app. Read fresh on every call
   (stat-keyed memo), so a change is the answer on the very next call with **no restart-order
   dependency**.
2. **local** — a provider entry whose endpoint is on this machine prices ``0.0``: its cost axis
   is latency/energy, not dollars. This is a real, known price, NOT an absence. Where the
   endpoint is decides it, never what kind of provider it is
   (:func:`personalclaw.llm.registry.served_on_this_machine`).
3. **app default** — the declaration of the app that registered the entry's TYPE:
   :attr:`~personalclaw.sdk.provider_helpers.BrandedProviderSpec.pricing`
   (``{model_pattern: {in_per_mtok, out_per_mtok}}``), read from the live app registration, so a
   branded app ships its prices in the same place as its ``default_model``/``capabilities``. Found
   by the configured entry's type, never its name: ``acme-proxy`` may be an OpenAI-compatible
   entry, and ``work`` an Acme one.
4. **builtin** — core's shipped ``model_pricing.json`` table (:mod:`personalclaw.pricing`), i.e.
   the app-default tier for core-bundled model families.
5. **absent** — :data:`None`: the call is **unpriced**.

**Absent is None, never 0.0.** A fabricated zero would report an unpriced cloud model as *free*,
which is the one wrong answer a spend meter must never give. ``0.0`` is reserved for prices we
actually know are zero (tier 2, or an explicit overlay/app entry). So a price says whether it is
known (:attr:`CallPrice.priced`), and a cap that holds an unpriced call says so rather than
counting it as free (``guardrails.budgets.SpendMeter``).

Every read is **fail-open** to the next tier: an unreadable or corrupt overlay logs once and the
tier below answers, and a lookup that fails outright reads as unpriced. Pricing never breaks a
routing decision or a model call.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

#: User overlay under the home; small JSON, atomic_write (the universal convention).
_OVERLAY_FILE = "model_rates.json"
#: Bump when the overlay's schema changes.
RATES_VERSION = 1
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


@dataclass(frozen=True)
class ModelRate:
    """USD per 1,000,000 tokens for one (provider, model), plus WHERE it came from.

    ``source`` is excluded from equality so a test can assert a rate's value without pinning the
    tier it resolved through; :func:`rate_for` callers that care read it explicitly.

    The two cache rates are what a token the provider READ from its prompt cache and one it WROTE
    into it cost. A rate that names neither bills both as plain input, the price of the same token
    uncached: a cache discount is the provider's to state, and a row that states none is not
    evidence of one. The shipped table names both for the families that cache.
    """

    in_per_mtok: float
    out_per_mtok: float
    source: str = field(default="", compare=False)
    cache_read_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None

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
        if not isinstance(obj, dict):
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


@dataclass(frozen=True)
class CallPrice:
    """What one model call cost, and whether that is known.

    ``priced`` is False when nothing prices the call: its provider reported no cost and no tier
    has a rate for its model. ``dollars`` is then 0.0, which is NOT a price — a caller shows the
    call as unpriced and a cap counts it as a call it could not count (``SpendMeter.charge``).
    ``source`` is ``"reported"`` for the provider's own figure, else the tier the rate came from
    (:attr:`ModelRate.source`), and ``""`` when unpriced.
    """

    dollars: float
    priced: bool
    source: str = ""


def ref_of(provider: str, model: str) -> str:
    """The ``active_models.json``-spelling ref for a (provider, model) — see
    :func:`personalclaw.routing.stats.ref_of` (same spelling, kept in one place)."""
    from personalclaw.routing.stats import ref_of as _ref_of

    return _ref_of(provider, model)


# ── The overlay store ────────────────────────────────────────────────────────────────────


def _overlay_path(home: Path) -> Path:
    return Path(home) / _OVERLAY_FILE


def _resolve_home(home: Path | None) -> Path:
    if home is not None:
        return Path(home)
    from personalclaw.config.loader import config_dir

    return Path(config_dir())


#: (path, inode, mtime_ns, size) → parsed overlay. Keyed on the stat so an EDIT (including an
#: atomic_write rename, which changes the inode) is picked up on the very next call — there is no
#: import-time snapshot and therefore no restart-order dependency.
_overlay_cache: tuple[tuple[str, int, int, int], dict[str, Any]] | None = None


class RatesUnreadable(Exception):
    """``model_rates.json`` is there and cannot be read as an overlay: no price in it is in effect,
    and a save would replace whatever it holds. Says why, as a person reads it."""


def _read_overlay_file(path: Path) -> dict[str, Any] | None:
    """The overlay as the file holds it, or ``None`` when there is no file.

    Raises :class:`RatesUnreadable` for a file that cannot be read, is not JSON, or holds no
    ``rates`` object. The one reader of the file: the fail-open read the prices come from and the
    strict read a write starts from ask the same question, so they cannot disagree about a file.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise RatesUnreadable(f"{path.name} could not be read ({exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RatesUnreadable(f"{path.name} is not valid JSON (line {exc.lineno})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("rates"), dict):
        raise RatesUnreadable(f'{path.name} has no "rates" object')
    return data


def load_overlay(home: Path | None = None) -> dict[str, Any]:
    """Read ``model_rates.json``. Missing/corrupt/foreign-shaped reads as an empty overlay
    (fail-open: the next tier answers), and the failure is logged, not raised."""
    global _overlay_cache
    path = _overlay_path(_resolve_home(home))
    try:
        st = os.stat(path)
        key = (str(path), int(st.st_ino), int(st.st_mtime_ns), int(st.st_size))
    except (FileNotFoundError, OSError):
        return {"version": RATES_VERSION, "rates": {}}
    cached = _overlay_cache
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        data = _read_overlay_file(path)
    except RatesUnreadable as exc:
        logger.warning("%s — falling back to app defaults", exc)
        return {"version": RATES_VERSION, "rates": {}}
    if data is None:
        return {"version": RATES_VERSION, "rates": {}}
    try:
        version = int(data.get("version", RATES_VERSION) or RATES_VERSION)
    except (TypeError, ValueError):
        version = RATES_VERSION
    overlay = {"version": version, "rates": data["rates"]}
    _overlay_cache = (key, overlay)
    return overlay


def save_overlay(rates: dict[str, Any], *, home: Path | None = None) -> Path:
    """Write the overlay (``atomic_write``). ``rates`` maps a ref key to a rate row; see
    :func:`_match_key` for the key forms."""
    path = _overlay_path(_resolve_home(home))
    payload = {"version": RATES_VERSION, "rates": rates}
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


# ── Setting one rate (Settings → Usage → Model prices) ──────────────────────────────────

#: A change reads the file and writes it back: two in one process must not interleave.
_WRITE_LOCK = threading.Lock()


def rate_entry(body: object) -> tuple[str, dict[str, float]]:
    """``(key, row)`` from a request to set one rate, or ``ValueError`` saying what is wrong.

    The key is a ``provider:model`` ref, a pattern over refs (``anthropic:claude-*``) or a model
    spelled alone, which prices that model whoever serves it, this machine included (a price you
    set comes before this machine's $0). The input and output rates are required; the cache rates
    are optional, and a row that names neither bills cached tokens as plain input. Each rate is a
    finite number of USD per 1,000,000 tokens, zero or more, and a field nothing reads is refused
    rather than stored.
    """
    if not isinstance(body, dict):
        raise ValueError("Send the model's key and its rates as one JSON object.")
    unknown = sorted(str(k) for k in body if k != "key" and k not in RATE_FIELDS)
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
    row: dict[str, float] = {}
    for name, label in RATE_FIELDS.items():
        value = body.get(name)
        if value is None:
            if name in _REQUIRED_RATE_FIELDS:
                raise ValueError(f"Give the {label} rate, in dollars per 1M tokens.")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"The {label} rate must be a number of dollars per 1M tokens.")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"The {label} rate must be zero or more dollars per 1M tokens.")
        row[name] = float(value)
    return key, row


def _stored_rates(home: Path | None) -> dict[str, Any]:
    """The overlay's rates as stored, for a change to one of them. Raises
    :class:`RatesUnreadable` rather than start from nothing: saving would replace the file."""
    data = _read_overlay_file(_overlay_path(_resolve_home(home)))
    return dict(data["rates"]) if data is not None else {}


def set_rate(key: str, row: dict[str, float], *, home: Path | None = None) -> None:
    """Price *key* at *row* in the overlay (:func:`rate_entry` validates both), leaving every other
    rate as it is stored now. Raises :class:`RatesUnreadable` when the file cannot be read."""
    with _WRITE_LOCK:
        rates = _stored_rates(home)
        rates[key] = dict(row)
        save_overlay(rates, home=home)


def clear_rate(key: str, *, home: Path | None = None) -> bool:
    """Remove the rate set for *key*; whether there was one. Raises :class:`RatesUnreadable` when
    the file cannot be read."""
    with _WRITE_LOCK:
        rates = _stored_rates(home)
        if key not in rates:
            return False
        del rates[key]
        save_overlay(rates, home=home)
        return True


def _rate_fields(rate: ModelRate) -> dict[str, float | None]:
    return {
        "in_per_mtok": rate.in_per_mtok,
        "out_per_mtok": rate.out_per_mtok,
        "cache_read_per_mtok": rate.cache_read_per_mtok,
        "cache_write_per_mtok": rate.cache_write_per_mtok,
    }


def rates_view(refs: Iterable[str], *, home: Path | None = None) -> dict[str, Any]:
    """What Settings → Usage → Model prices shows.

    ``rates`` — every rate set in the overlay, by key; a row that is no rate is not in effect and
    not listed. ``models`` — each model *refs* names (the models the uses are bound to) with the
    rate a call to it is counted at and where that rate comes from (``source``: ``overlay``,
    ``local``, ``app_default``, ``builtin``), or ``priced: false`` when nothing prices it.
    ``unreadable`` — why the overlay could not be read, when it could not: no rate in it is then in
    effect, and a change is refused until it is fixed or removed.
    """
    unreadable = ""
    try:
        data = _read_overlay_file(_overlay_path(_resolve_home(home)))
    except RatesUnreadable as exc:
        unreadable = str(exc)
        data = None
    rows: list[dict[str, Any]] = []
    for key, value in sorted((data or {}).get("rates", {}).items()):
        rate = ModelRate.from_obj(value, source="overlay")
        if isinstance(key, str) and rate is not None:
            rows.append({"key": key, **_rate_fields(rate)})
    models: list[dict[str, Any]] = []
    for ref in dict.fromkeys(str(r) for r in refs):
        provider, _, model = ref.partition(":")
        if not provider or not model:
            continue  # a ref that names no model has no model to price
        rate = _effective_rate(provider, model, home)
        entry: dict[str, Any] = {
            "ref": ref,
            "priced": rate is not None,
            "source": rate.source if rate is not None else "",
        }
        entry.update(_rate_fields(rate) if rate is not None else dict.fromkeys(RATE_FIELDS))
        models.append(entry)
    return {"rates": rows, "models": models, "unreadable": unreadable}


# ── Resolution ───────────────────────────────────────────────────────────────────────────


def _match_key(table: dict[str, Any], candidates: list[str]) -> Any:
    """Find ``table``'s row for the first matching candidate spelling.

    For each candidate in order: an EXACT key wins, else the LONGEST matching glob pattern
    (``anthropic:claude-sonnet-*``) — longest so a specific pattern beats a catch-all ``*``.
    """
    for candidate in candidates:
        row = table.get(candidate)
        if row is not None:
            return row
        best: tuple[int, Any] | None = None
        for key, value in table.items():
            if not isinstance(key, str) or not any(c in key for c in "*?["):
                continue
            if fnmatchcase(candidate, key) and (best is None or len(key) > best[0]):
                best = (len(key), value)
        if best is not None:
            return best[1]
    return None


def _overlay_rate(provider: str, model: str, home: Path | None) -> ModelRate | None:
    """Tier 1. Keys may be an exact ref (``anthropic:claude-sonnet-4.5``), a ref glob
    (``anthropic:claude-*``) or a bare model spelling (``claude-*``, provider-agnostic)."""
    table = load_overlay(home).get("rates", {})
    if not isinstance(table, dict) or not table:
        return None
    row = _match_key(table, [ref_of(provider, model), model])
    return ModelRate.from_obj(row, source="overlay")


def _app_default_rate(provider: str, model: str) -> ModelRate | None:
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
    return ModelRate.from_obj(_match_key(dict(table), [model]), source="app_default")


def _builtin_rate(model: str) -> ModelRate | None:
    """Tier 4. Core's shipped ``model_pricing.json`` (:mod:`personalclaw.pricing`): the cost of
    exactly 1M tokens of one bucket IS that bucket's per-Mtok rate, cache buckets included."""
    try:
        from personalclaw.pricing import estimate_cost, has_pricing

        if not has_pricing(model):
            return None
        return ModelRate(
            in_per_mtok=float(estimate_cost(model, input_tokens=1_000_000)),
            out_per_mtok=float(estimate_cost(model, output_tokens=1_000_000)),
            source="builtin",
            cache_read_per_mtok=float(estimate_cost(model, cache_read_tokens=1_000_000)),
            cache_write_per_mtok=float(estimate_cost(model, cache_creation_tokens=1_000_000)),
        )
    except Exception:  # noqa: BLE001
        logger.warning("builtin pricing lookup failed for model %r", model, exc_info=True)
        return None


def rate_for(provider: str, model: str, *, home: Path | None = None) -> ModelRate | None:
    """The effective rate for one (provider, model) — **overlay > local > app default > builtin >
    absent**, evaluated in that order with the first hit winning.

    Returns ``None`` when nothing prices this model. ``None`` means "unknown", NOT "free"; a
    caller that needs a number must decide what an unknown price means for it rather than
    inheriting a fabricated ``0.0``.
    """
    from personalclaw.llm.registry import served_on_this_machine

    if not str(model or "").strip():
        return None
    overlay = _overlay_rate(provider, model, home)
    if overlay is not None:
        return overlay
    if served_on_this_machine(provider):
        return ModelRate(0.0, 0.0, source="local")
    app_default = _app_default_rate(provider, model)
    if app_default is not None:
        return app_default
    return _builtin_rate(model)


def _effective_rate(provider: str, model: str, home: Path | None) -> ModelRate | None:
    """:func:`rate_for`, with a lookup that fails outright read as no rate: unpriced, not free."""
    try:
        return rate_for(provider, model, home=home)
    except Exception:  # noqa: BLE001 — pricing never breaks a call; an unknown is unpriced
        logger.warning("rate lookup failed for %s:%s", provider, model, exc_info=True)
        return None


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
    rate = _effective_rate(provider, model, home)
    if rate is None:
        return CallPrice(0.0, False, "")
    dollars = rate.cost(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )
    return CallPrice(dollars, True, rate.source)


def price_event(event: object, *, provider: str, model: str, home: Path | None = None) -> CallPrice:
    """:func:`price_call` for the call a terminal ``EVENT_COMPLETE`` reports: its token counts
    and the cost its provider reported (``cost_usd``), read off the event."""
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
    rate = _effective_rate(provider, model, home)
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
    "RATES_VERSION",
    "RATE_FIELDS",
    "CallPrice",
    "ModelRate",
    "RatesUnreadable",
    "cache_savings_usd",
    "clear_rate",
    "load_overlay",
    "price_call",
    "price_event",
    "rate_entry",
    "rate_for",
    "rates_view",
    "ref_of",
    "save_overlay",
    "set_rate",
]
