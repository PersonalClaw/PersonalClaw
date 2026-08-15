"""Heuristic route ordering — ``route_refs`` + ``routing_policy.json`` (MRT-4, §3, §4.1, §6.1-6.2).

The router does not pick a model; it **reorders the user's own bindings**. Candidates for a routed
use case are exactly the refs ``active_models.json`` holds for it (§3.1) — this module never
invents, adds, or drops one. That is the whole contract, and it is what makes routing safe to
enable: the worst a bad ordering can do is try the user's second choice first.

**Pure reorder (the load-bearing invariant).** :func:`route_refs` returns a *permutation* of its
input: same refs, same count, same multiplicity, different order. It is built as a stable sort over
the input indices precisely so dropping a ref is not expressible, and it re-checks the invariant
before returning. A reorder that silently dropped a candidate would remove a provider the user
deliberately configured — the resolution chain would then skip straight past it, and the
"unresolvable pinned ref RAISES" rule (§3.1) would stop protecting them.

**Deterministic and total.** Every ordering is a stable sort keyed on an integer rank, so
equal-ranked refs keep their ``active_models.json`` order — the documented tie-break. The same
inputs therefore produce the same order in every process, with no dependence on dict iteration,
clock, or set ordering.

**Fail-open, always.** Every input this module reads (``routing_policy.json``, the per-use-case
settings store, the local-model registry, the provider registry's declared capabilities) is
observability-grade, not load-bearing. A missing or corrupt read degrades to the next-weaker
signal and, at worst, to the original order. A routing decision must never fail because a
telemetry read did: routing changes *order*, never *resolution semantics* (§3.1).

Ordering precedence, strongest first:

1. **mode ``off``** (the default) → identity. Routing is opt-in per use case.
2. **pin** (§6.2 lever 2) → ``local`` / ``cloud`` / an explicit ref is hoisted first and the
   heuristic is short-circuited. A user pin is mightier than any policy.
3. **an explicit table order** for this ``(use_case, query_class)`` → the stored order wins
   (its ``basis`` records whether a user or a learned proposal decided it, §6.1). Refs the
   table doesn't mention keep their original relative order *after* the listed ones, so a
   newly-bound ref is never lost.
4. **the heuristic** (§4.1) → local-first, with two class exceptions: ``extract_structured``
   prefers a candidate declaring structured output, and ``long_reasoning`` demotes a local model
   whose id exposes a parameter-size hint below :data:`_MIN_LOCAL_REASONING_B`.

Mode ``learned`` is accepted and folded onto the heuristic here: the learned scoring stage is
MRT-5's, and the heuristic is its permanent below-confidence-floor floor (§4.2), so a use case
already set to ``learned`` behaves as ``heuristic`` rather than erroring or silently going off.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from personalclaw.atomic_write import atomic_write

logger = logging.getLogger(__name__)

#: File under the home; small JSON, atomic_write (the universal convention).
_POLICY_FILE = "routing_policy.json"
#: Bump when the table's schema changes.
POLICY_VERSION = 1
#: The per-use-case routing modes (§6.2 lever 1). ``off`` is the default everywhere.
MODES = ("off", "heuristic", "learned")
#: The key the per-use-case settings store (``use_case_settings/{uc}.json``) holds the mode under.
#: Routing enablement lives beside the use case's other behavior settings, NOT in config.json —
#: it is bindings-adjacent state (§7).
MODE_KEY = "routing_mode"
#: The key that store holds the pin under, when a pin is set there rather than in the table.
PIN_KEY = "routing_pin"
#: A local model below this parameter-size hint (in billions) is demoted for ``long_reasoning``
#: (§4.1). Only applied when the model id actually exposes a hint — absent one, nothing is demoted.
_MIN_LOCAL_REASONING_B = 7.0
#: Parameter-size hint in a model id: "qwen3:8b", "…-13B-instruct", "3.8b".
_SIZE_HINT = re.compile(r"(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", re.IGNORECASE)
#: The capability spelling that means "can be asked for schema-constrained JSON" (§4.1, read from
#: the existing capability channel — no new channel).
_STRUCTURED_CAP = "structured_output"
#: Query classes with an ordering exception (mirrors routing/classifier.py's vocabulary).
_CLASS_STRUCTURED = "extract_structured"
_CLASS_LONG_REASONING = "long_reasoning"


# ── the store ───────────────────────────────────────────────────────────────────


def _policy_path(home: Path) -> Path:
    return Path(home) / _POLICY_FILE


def _empty_policy() -> dict[str, Any]:
    return {"version": POLICY_VERSION, "classifier_version": 1, "use_cases": {}}


def load_policy(home: Path | None = None) -> dict[str, Any]:
    """Read ``routing_policy.json``. A missing/corrupt file reads as an empty table (never fatal).

    ``home`` defaults to the live config dir, resolved lazily so this module stays importable
    without a configured home (and so tests can point it at ``tmp_path``).
    """
    if home is None:
        home = _default_home()
    if home is None:
        return _empty_policy()
    try:
        data = json.loads(_policy_path(home).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError, ValueError):
        return _empty_policy()
    if not isinstance(data, dict):
        return _empty_policy()
    data.setdefault("version", POLICY_VERSION)
    data.setdefault("classifier_version", 1)
    if not isinstance(data.get("use_cases"), dict):
        data["use_cases"] = {}
    return data


def save_policy(home: Path, policy: dict[str, Any]) -> None:
    """Persist the table (atomic_write, stable key order so a diff reads cleanly)."""
    atomic_write(_policy_path(home), json.dumps(policy, indent=2, sort_keys=True) + "\n")


def _default_home() -> Path | None:
    try:
        from personalclaw.config import config_dir

        return Path(config_dir())
    except Exception:  # noqa: BLE001 — no home configured is not a routing failure
        return None


def _use_case_entry(policy: dict[str, Any], use_case: str) -> dict[str, Any]:
    entry = policy.get("use_cases", {}).get(use_case)
    return entry if isinstance(entry, dict) else {}


# ── the three user levers (§6.2) ────────────────────────────────────────────────


def _settings_for(use_case: str) -> dict[str, Any]:
    """The per-use-case behavior settings, fail-open to empty."""
    try:
        from personalclaw.providers.use_cases import load_use_case_settings

        got = load_use_case_settings(use_case)
        return got if isinstance(got, dict) else {}
    except Exception:  # noqa: BLE001 — settings read is never load-bearing
        return {}


def mode_for(use_case: str, *, home: Path | None = None) -> str:
    """The routing mode for ``use_case``: one of :data:`MODES`, defaulting to ``off``.

    The per-use-case settings store wins (that is the lever the UI writes, §6.2); the table's own
    ``mode`` is the fallback so a hand-edited ``routing_policy.json`` is still honored. An
    unrecognized value reads as ``off`` — an unknown mode must not silently enable routing.
    """
    raw = str(_settings_for(use_case).get(MODE_KEY, "") or "")
    if raw not in MODES:
        raw = str(_use_case_entry(load_policy(home), use_case).get("mode", "") or "")
    return raw if raw in MODES else "off"


def pin_for(use_case: str, *, home: Path | None = None) -> str:
    """The user's pin for ``use_case``: ``"local"`` / ``"cloud"`` / a ``"provider:model"`` ref, or
    ``""`` when unpinned. The settings store wins over the table, as with the mode."""
    raw = str(_settings_for(use_case).get(PIN_KEY, "") or "")
    if not raw:
        raw = str(_use_case_entry(load_policy(home), use_case).get("pin", "") or "")
    return raw


def master_enabled() -> bool:
    """The ``routing.enabled`` master switch (config.json, §7). Default/fail-open: False.

    Read here rather than at the seam so the switch is genuinely load-bearing: with it off, no
    per-use-case mode can route anything, which is what "master" has to mean.
    """
    try:
        from personalclaw.config.loader import AppConfig

        return bool(AppConfig.load().routing.enabled)
    except Exception:  # noqa: BLE001 — an unreadable config means routing stays off
        return False


def local_timeout_secs() -> float:
    """``routing.local_timeout_secs`` (§4.1): the wall clock a local attempt gets before the chain
    moves on to the next bound ref. Fail-open to the shipped 20s default; a non-positive value
    reads as "no routing-specific timeout" so the guard keeps its own default."""
    try:
        from personalclaw.config.loader import AppConfig

        return max(0.0, float(AppConfig.load().routing.local_timeout_secs))
    except Exception:  # noqa: BLE001
        return 20.0


def routing_active(use_case: str, *, home: Path | None = None) -> bool:
    """Whether routing is enabled for ``use_case``: the master switch AND a non-``off`` mode.

    The resolution seam calls this to decide whether to route at all and to stamp the ``routed``
    provenance on the attempt audit. Fail-open to False: an unreadable config or settings store
    means "routing off", i.e. exactly the behavior of a machine that never enabled it.
    """
    try:
        return master_enabled() and mode_for(use_case, home=home) != "off"
    except Exception:  # noqa: BLE001
        return False


def table_order(use_case: str, query_class: str, *, home: Path | None = None) -> list[str]:
    """The explicitly recorded order for ``(use_case, query_class)``, or ``[]`` when the table has
    no opinion. Every recorded order carries a ``basis`` (§6.1) — see :func:`order_basis`."""
    classes = _use_case_entry(load_policy(home), use_case).get("classes")
    if not isinstance(classes, dict):
        return []
        # (no table opinion — the heuristic decides)
    cell = classes.get(query_class)
    if not isinstance(cell, dict):
        return []
    order = cell.get("order")
    if not isinstance(order, list):
        return []
    return [str(r) for r in order if isinstance(r, str)]


def order_basis(use_case: str, query_class: str, *, home: Path | None = None) -> dict[str, Any]:
    """The ``basis`` behind a recorded order — why the table says what it says (§6.1)."""
    classes = _use_case_entry(load_policy(home), use_case).get("classes")
    if not isinstance(classes, dict):
        return {}
    cell = classes.get(query_class)
    if not isinstance(cell, dict):
        return {}
    basis = cell.get("basis")
    return basis if isinstance(basis, dict) else {}


# ── local / capability classification of a candidate ref ────────────────────────


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def provider_of(ref: str) -> str:
    """The provider half of a ``"provider:model"`` ref (split on the FIRST colon, mirroring
    ``stats.ref_of`` — a colon-bearing model id round-trips)."""
    return str(ref).split(":", 1)[0]


def model_of(ref: str) -> str:
    """The model half of a ``"provider:model"`` ref (``""`` when the ref carries no colon)."""
    parts = str(ref).split(":", 1)
    return parts[1] if len(parts) == 2 else ""


def _local_provider_keys() -> set[str]:
    """Normalized keys of the registered local-model providers.

    The local-model registry is APP-name keyed (the documented spelling gotcha, §7): the app
    registers under its app name while ``active_models.json`` refs carry the *config entry* name.
    Both are normalized (lowercased, punctuation stripped) and matched by prefix in
    :func:`is_local_ref`, which absorbs that spelling difference without hardcoding any vendor.
    """
    try:
        from personalclaw.local_models.registry import registered

        return {_norm(k) for k, _ in registered() if k}
    except Exception:  # noqa: BLE001 — an unreadable registry means "assume cloud"
        return set()


def is_local_ref(ref: str, *, local_keys: set[str] | None = None) -> bool:
    """Whether ``ref`` names a locally-served model, per local-model-registry membership (§7).

    Conservative: an unknown provider is treated as CLOUD. Mis-labeling a cloud ref as local would
    order a paid, off-machine provider ahead of a free on-machine one under a local-first policy —
    the one direction of error that costs the user money and privacy.
    """
    prov = _norm(provider_of(ref))
    if not prov:
        return False
    keys = _local_provider_keys() if local_keys is None else local_keys
    for key in keys:
        if not key:
            continue
        # Either spelling may be the longer one ("ollama" vs "ollama-models"), so accept a prefix
        # match in either direction, with a floor on the shared prefix so two unrelated short names
        # can't collide.
        if key == prov:
            return True
        if len(prov) >= 4 and key.startswith(prov):
            return True
        if len(key) >= 4 and prov.startswith(key):
            return True
    return False


def _structured_providers() -> set[str]:
    """Normalized provider names whose config entry declares structured output (§4.1).

    Read from the existing capability channel (``ProviderEntry.declared_capabilities``) without
    building anything. Fail-open to empty: with no capability information the structured-output
    exception simply doesn't fire and the plain local-first heuristic stands.
    """
    out: set[str] = set()
    try:
        from personalclaw.llm.registry import get_default_registry

        for entry in get_default_registry().list_entries():
            caps = entry.declared_capabilities or frozenset()
            for cap in caps:
                if str(getattr(cap, "value", cap)) == _STRUCTURED_CAP:
                    out.add(_norm(entry.name))
                    break
    except Exception:  # noqa: BLE001 — capability introspection is never load-bearing
        return set()
    return out


def size_hint_b(ref: str) -> float:
    """The parameter-size hint (in billions) the model id exposes, or ``0.0`` when it exposes none.

    ``0.0`` means "unknown", NOT "tiny": an unknown size must not get a model demoted, or every
    model whose id omits a size would be pushed behind one that spells it out.
    """
    match = _SIZE_HINT.search(model_of(ref) or str(ref))
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return 0.0


# ── the reorder ─────────────────────────────────────────────────────────────────


def _pin_rank(ref: str, pin: str, *, local_keys: set[str]) -> int:
    """0 for a ref the pin hoists, 1 otherwise. A pin never drops anything — an unmatchable pin
    (e.g. ``local`` with no local ref bound) ranks everything 1, i.e. leaves the order alone."""
    if pin == "local":
        return 0 if is_local_ref(ref, local_keys=local_keys) else 1
    if pin == "cloud":
        return 0 if not is_local_ref(ref, local_keys=local_keys) else 1
    return 0 if ref == pin else 1


def _heuristic_rank(
    ref: str,
    query_class: str,
    *,
    local_keys: set[str],
    structured: set[str],
) -> tuple[int, int]:
    """The §4.1 rank for one ref. Lower sorts earlier; ties fall through to the input order.

    Two components, most specific first:

    * the class exception — ``extract_structured`` hoists a declared structured-output provider;
      ``long_reasoning`` demotes a local model whose size hint is below the floor (a 1B model is
      the wrong tool for long reasoning, and trying it first only spends the timeout);
    * local-first — a local ref is free and private, so it leads (§4.1, §5.2).
    """
    local = is_local_ref(ref, local_keys=local_keys)
    exception = 0
    if query_class == _CLASS_STRUCTURED:
        exception = 0 if _norm(provider_of(ref)) in structured else 1
    elif query_class == _CLASS_LONG_REASONING and local:
        hint = size_hint_b(ref)
        # 0.0 = no hint exposed → no demotion (unknown is not small).
        exception = 1 if 0.0 < hint < _MIN_LOCAL_REASONING_B else 0
    return (exception, 0 if local else 1)


def route_refs(
    use_case: str,
    query_class: str,
    refs: list[str],
    *,
    home: Path | None = None,
) -> list[str]:
    """Reorder ``refs`` — the candidate pool for ``use_case`` — for a ``query_class`` request.

    **Returns a permutation of ``refs``: never a different set, never a different length.** The
    result is always a stable sort over the input indices, so refs of equal rank keep their
    ``active_models.json`` order (the documented tie-break) and the same inputs always produce the
    same output. Fewer than two candidates, or routing off for this use case, returns the input
    order unchanged.

    Fail-open: any error anywhere in ranking returns ``list(refs)`` untouched.
    """
    ordered = list(refs)
    if len(ordered) < 2:
        return ordered
    try:
        mode = mode_for(use_case, home=home)
        if mode == "off":
            return ordered

        local_keys = _local_provider_keys()

        # Lever 2 — a user pin short-circuits the heuristic entirely (§6.2). Learned scoring may
        # keep accumulating under a pin, but it never reorders.
        pin = pin_for(use_case, home=home)
        if pin:
            return _stable_by(ordered, lambda r: (_pin_rank(r, pin, local_keys=local_keys),))

        # Lever 3 / the learned table — an explicitly recorded order wins over the heuristic.
        # Refs the table doesn't list rank after the listed ones, keeping their relative order, so
        # a ref bound after the table was written is reordered, never dropped.
        recorded = table_order(use_case, query_class, home=home)
        if recorded:
            index = {ref: i for i, ref in enumerate(recorded)}
            listed = len(index)
            return _stable_by(ordered, lambda r: (index.get(r, listed),))

        # The heuristic floor (§4.1). ``learned`` lands here too until MRT-5 scores the fold.
        structured = _structured_providers() if query_class == _CLASS_STRUCTURED else set()
        return _stable_by(
            ordered,
            lambda r: _heuristic_rank(r, query_class, local_keys=local_keys, structured=structured),
        )
    except Exception:  # noqa: BLE001 — a routing decision must never fail a resolution
        logger.debug("route_refs failed for %s/%s — keeping bound order", use_case, query_class)
        return list(refs)


def _stable_by(refs: list[str], key: Any) -> list[str]:
    """Stable-sort ``refs`` by ``key``, then VERIFY the result is a permutation of the input.

    The sort alone cannot drop a ref, but the check is cheap and the invariant is the contract the
    rest of resolution leans on ("routing changes order, never resolution semantics"), so it is
    asserted rather than assumed. A violation degrades to the input order instead of raising.
    """
    out = sorted(refs, key=key)
    if len(out) != len(refs) or sorted(out) != sorted(refs):  # pragma: no cover — defense in depth
        logger.warning("route_refs produced a non-permutation — keeping bound order")
        return list(refs)
    return out


# ── writes: the three levers, SEL-audited (§6.2, §6.4) ──────────────────────────


def _sel_policy_change(use_case: str, what: str, value: str) -> None:
    """SEL-record one policy-table mutation (§6.4).

    Routing decides which providers see which content, so CHANGING the table is
    security-relevant — a mode or pin flip can move prompts from a local model to a cloud one.
    (Routing *decisions* are not SEL events; only changes to the policy are.) Best-effort: an
    audit failure must not lose the user's edit.
    """
    try:
        from personalclaw.sel import sel

        sel().log_api_access(
            caller="user",
            operation=f"routing.{what}",
            outcome="success",
            source="routing_policy",
            resources=f"{use_case}:{value}",
        )
    except Exception:  # noqa: BLE001 — audit must never break the write
        logger.debug("routing policy SEL record failed", exc_info=True)


def set_mode(use_case: str, mode: str) -> None:
    """Set the per-use-case routing mode (§6.2 lever 1). Raises ValueError on an unknown mode —
    a write path must reject a bad value loudly rather than silently storing something the read
    path will treat as ``off``."""
    if mode not in MODES:
        raise ValueError(f"unknown routing mode {mode!r} (expected one of {MODES})")
    from personalclaw.providers.use_cases import load_use_case_settings, save_use_case_settings

    settings = dict(load_use_case_settings(use_case) or {})
    settings[MODE_KEY] = mode
    save_use_case_settings(use_case, settings)
    _sel_policy_change(use_case, "mode", mode)


def set_pin(use_case: str, pin: str) -> None:
    """Set (or clear, with ``""``) the per-use-case pin (§6.2 lever 2)."""
    from personalclaw.providers.use_cases import load_use_case_settings, save_use_case_settings

    settings = dict(load_use_case_settings(use_case) or {})
    if pin:
        settings[PIN_KEY] = pin
    else:
        settings.pop(PIN_KEY, None)
    save_use_case_settings(use_case, settings)
    _sel_policy_change(use_case, "pin", pin or "(cleared)")


def set_order(
    use_case: str,
    query_class: str,
    order: list[str],
    *,
    home: Path | None = None,
    basis: dict[str, Any] | None = None,
) -> None:
    """Record a manual order for ``(use_case, query_class)`` (§6.2 lever 3).

    The order is stored with a ``basis`` so the table can always explain itself; a hand reorder
    records ``{"source": "user"}``, which the learned stage may later propose changing but never
    silently overwrite (§6.3). Note ``route_refs`` still treats the stored order as a *ranking*,
    not a filter — a ref that is not in it is ranked last, never dropped.
    """
    if home is None:
        home = _default_home()
    if home is None:
        raise RuntimeError("no PersonalClaw home configured; cannot persist routing policy")
    policy = load_policy(home)
    use_cases = policy.setdefault("use_cases", {})
    entry = use_cases.setdefault(use_case, {})
    if not isinstance(entry, dict):
        entry = {}
        use_cases[use_case] = entry
    classes = entry.setdefault("classes", {})
    if not isinstance(classes, dict):
        classes = {}
        entry["classes"] = classes
    classes[query_class] = {
        "order": [str(r) for r in order],
        "basis": dict(basis) if basis else {"source": "user"},
    }
    save_policy(home, policy)
    _sel_policy_change(use_case, "order", f"{query_class}:{','.join(order)}")


def table_for(use_case: str, *, home: Path | None = None) -> dict[str, Any]:
    """The inspectable table for one use case (§6.1): its mode, pin, and every recorded
    per-class order with the basis behind it. What the read-only Routing tab renders."""
    entry = _use_case_entry(load_policy(home), use_case)
    raw_classes = entry.get("classes")
    classes: dict[str, Any] = raw_classes if isinstance(raw_classes, dict) else {}
    return {
        "use_case": use_case,
        "mode": mode_for(use_case, home=home),
        "pin": pin_for(use_case, home=home),
        "classes": {
            str(cls): {
                "order": table_order(use_case, str(cls), home=home),
                "basis": order_basis(use_case, str(cls), home=home),
            }
            for cls in classes
        },
    }
