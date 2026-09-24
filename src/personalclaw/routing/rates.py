"""Shared model pricing rate table — ``rate_for(provider, model)`` (MRT-2, §5.1).

Three consumers need the same dollar-per-token number: this router's cost-aware ordering,
AUTONOMY-GUARDRAILS' SpendMeter ``dollars_est``, and WF2's run ``cost_usd``. Before this module
each of them either carried its own table or silently reported nothing, so "who owns the rate
table" was open. This module owns it, and it resolves ONE effective rate through a **total,
explicit precedence**:

1. **overlay** — ``~/.personalclaw/model_rates.json`` (``atomic_write``). Prices drift; a personal
   tool must let its owner correct them without shipping a new app. Read fresh on every call
   (stat-keyed memo), so editing the file changes the answer with **no restart-order dependency**.
2. **local** — a local provider prices ``0.0`` (SC #7): its cost axis is latency/energy, not
   dollars. This is a real, known price, NOT an absence.
3. **app default** — the provider app's own declaration:
   :attr:`~personalclaw.sdk.provider_helpers.BrandedProviderSpec.pricing`
   (``{model_pattern: {in_per_mtok, out_per_mtok}}``), read from the live app registration, so a
   branded app ships its prices in the same place as its ``default_model``/``capabilities``.
4. **builtin** — core's shipped ``model_pricing.json`` table (via the public
   :mod:`personalclaw.pricing` API), i.e. the app-default tier for core-bundled model families.
5. **absent** — :data:`None`.

**Absent is None, never 0.0.** A fabricated zero would report an unpriced cloud model as *free*,
which is the one wrong answer a spend meter must never give. ``0.0`` is reserved for prices we
actually know are zero (tier 2, or an explicit overlay/app entry). Callers must branch on
``None`` — that is why this returns an optional and not a float.

Every read is **fail-open**: an unreadable or corrupt overlay logs once and degrades to the next
tier. A pricing lookup is observability, never load-bearing — it must not break a routing
decision or a model call.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

#: User overlay under the home; small JSON, atomic_write (the universal convention).
_OVERLAY_FILE = "model_rates.json"
#: Bump when the overlay's schema changes.
RATES_VERSION = 1

#: Provider TYPES that serve models on this machine — content never leaves, and no invoice
#: arrives. Matched case-insensitively against the type/name, exactly OR as a substring, because
#: a local app registers several spellings of one engine (``ollama``/``ollama-models``).
#: Unknown → NOT local (the conservative default: a hosted provider must not be priced free).
LOCAL_PROVIDER_HINTS: frozenset[str] = frozenset(
    {"ollama", "lmstudio", "lm-studio", "llamacpp", "llama-cpp", "llama.cpp", "vllm", "localai"}
)


@dataclass(frozen=True)
class ModelRate:
    """USD per 1,000,000 tokens for one (provider, model), plus WHERE it came from.

    ``source`` is excluded from equality so a test can assert a rate's value without pinning the
    tier it resolved through; :func:`rate_for` callers that care read it explicitly.
    """

    in_per_mtok: float
    out_per_mtok: float
    source: str = field(default="", compare=False)

    def cost(self, *, input_tokens: int = 0, output_tokens: int = 0) -> float:
        """USD for a token count at this rate."""
        cost = (
            (input_tokens or 0) * self.in_per_mtok + (output_tokens or 0) * self.out_per_mtok
        ) / 1_000_000.0
        return round(cost, 6)

    def to_dict(self) -> dict[str, float]:
        return {"in_per_mtok": self.in_per_mtok, "out_per_mtok": self.out_per_mtok}

    @classmethod
    def from_obj(cls, obj: Any, *, source: str = "") -> ModelRate | None:
        """Normalize a declared rate row (``{"in_per_mtok":…, "out_per_mtok":…}``) into a
        :class:`ModelRate`. A row that carries neither key is not a rate → None (an app or a
        hand-edited overlay must not turn a typo into a free model)."""
        if isinstance(obj, ModelRate):
            return ModelRate(obj.in_per_mtok, obj.out_per_mtok, source=source or obj.source)
        if not isinstance(obj, dict):
            return None
        if "in_per_mtok" not in obj and "out_per_mtok" not in obj:
            return None
        try:
            return cls(
                in_per_mtok=float(obj.get("in_per_mtok", 0.0) or 0.0),
                out_per_mtok=float(obj.get("out_per_mtok", 0.0) or 0.0),
                source=source,
            )
        except (TypeError, ValueError):
            return None


def ref_of(provider: str, model: str) -> str:
    """The ``active_models.json``-spelling ref for a (provider, model) — see
    :func:`personalclaw.routing.stats.ref_of` (same spelling, kept in one place)."""
    from personalclaw.routing.stats import ref_of as _ref_of

    return _ref_of(provider, model)


def is_local_provider_type(provider: str) -> bool:
    """Whether ``provider`` (a provider TYPE/name string) serves models locally → price 0.0."""
    name = str(provider or "").strip().lower()
    if not name:
        return False
    return any(hint in name for hint in LOCAL_PROVIDER_HINTS)


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
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        logger.warning("model_rates.json unreadable — falling back to app defaults", exc_info=True)
        return {"version": RATES_VERSION, "rates": {}}
    if not isinstance(data, dict) or not isinstance(data.get("rates"), dict):
        logger.warning("model_rates.json has no 'rates' object — falling back to app defaults")
        return {"version": RATES_VERSION, "rates": {}}
    overlay = {
        "version": int(data.get("version", RATES_VERSION) or RATES_VERSION),
        "rates": data["rates"],
    }
    _overlay_cache = (key, overlay)
    return overlay


def save_overlay(rates: dict[str, Any], *, home: Path | None = None) -> Path:
    """Write the overlay (``atomic_write``). ``rates`` maps a ref key to a rate row; see
    :func:`_match_key` for the key forms."""
    path = _overlay_path(_resolve_home(home))
    payload = {"version": RATES_VERSION, "rates": rates}
    atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


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
    """Tier 3. The provider app's own ``BrandedProviderSpec.pricing`` (keyed by model pattern,
    so no provider prefix). Read live from the registration — an app installed after import is
    visible on the next call."""
    try:
        import personalclaw.sdk.model  # noqa: F401 — package import order (sdk.model first)
        from personalclaw.llm.branded_specs import spec_pricing

        table = spec_pricing(provider)
    except Exception:  # noqa: BLE001 — a pricing lookup must never break a call
        logger.warning("app pricing lookup failed for provider %r", provider, exc_info=True)
        return None
    if not table:
        return None
    return ModelRate.from_obj(_match_key(dict(table), [model]), source="app_default")


def _builtin_rate(model: str) -> ModelRate | None:
    """Tier 4. Core's shipped ``model_pricing.json``, read through the public
    :mod:`personalclaw.pricing` API: the cost of exactly 1M tokens IS the per-Mtok rate."""
    try:
        from personalclaw.pricing import estimate_cost, has_pricing

        if not has_pricing(model):
            return None
        return ModelRate(
            in_per_mtok=float(estimate_cost(model, input_tokens=1_000_000)),
            out_per_mtok=float(estimate_cost(model, output_tokens=1_000_000)),
            source="builtin",
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
    if not str(model or "").strip():
        return None
    overlay = _overlay_rate(provider, model, home)
    if overlay is not None:
        return overlay
    if is_local_provider_type(provider):
        return ModelRate(0.0, 0.0, source="local")
    app_default = _app_default_rate(provider, model)
    if app_default is not None:
        return app_default
    return _builtin_rate(model)


def cost_for(
    provider: str,
    model: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    home: Path | None = None,
) -> float | None:
    """USD for a token count at the effective rate, or ``None`` when the model is unpriced.

    The shared implementation offered to SpendMeter's ``dollars_est`` and WF2's ``cost_usd`` — the
    ``None`` is the whole point: both must be able to say "unpriced" instead of "$0.00".
    """
    rate = rate_for(provider, model, home=home)
    if rate is None:
        return None
    return rate.cost(input_tokens=input_tokens, output_tokens=output_tokens)


__all__ = [
    "LOCAL_PROVIDER_HINTS",
    "RATES_VERSION",
    "ModelRate",
    "cost_for",
    "is_local_provider_type",
    "load_overlay",
    "rate_for",
    "ref_of",
    "save_overlay",
]
