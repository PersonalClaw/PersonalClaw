"""First-run onboarding progress state (ONBOARDING-UX C1).

**What this holds.** The resume point of the guided first-run flow, which essential
apps the user set up, and which "try one" first-success cards they completed:

.. code-block:: json

    {
      "step": "name",
      "essentials": {"model": null, "search": false, "speech": false, "channel": null},
      "first_success": {"knowledge": false, "trigger": false, "loop": false}
    }

**Why it is entity state, not config.** This is per-user progress through a flow, not a
tunable — so it lives in ``entity_settings/onboarding.json`` and is written by a dedicated
``POST /api/onboarding/state``, never through the ``_EDITABLE_CONFIG`` PATCH allowlist
(the §2.1 entity-state rule). It rides the existing ``entity_settings`` inventory entry
(``KIND_JSON_ENTITY_DIR``), so snapshot/restore and durability sync already cover it —
exactly like ``feedback.json``, ``channel_trust.json`` and ``legibility.json``.

**Tolerant reads are the contract.** :func:`load_onboarding_state` never raises: a missing
file, a corrupt file, a file written by an older client that has none of these fields, or a
field carrying the wrong type all resolve to the default for that field. The flow degrades
to "start at the beginning", never to a 500.

**Strict writes are the contract.** :func:`merge_onboarding_state` rejects an unknown or
mistyped key with :class:`ValueError` rather than dropping it silently. Bug #22 (the
entity-settings PUT that blind-merged any body key) taught the repo that a lenient write
path leaks garbage back out through every read; for a brand-new endpoint an explicit 400
also means a frontend typo surfaces immediately instead of becoming a silent no-op.

**Naming deviation from C1, recorded.** C1 spelled the middle step ``provider`` and the
field ``provider_chosen``. The 2026-07-26 amendment (ruling a) re-scoped that step from
"pick a provider" to "install the essential apps" and generalized the field to
``essentials``; this module names the *step* ``essentials`` too, so the step id and the
field it fills agree. C1 also annotated ``name``/``completed`` as "existing" fields of this
state — they are not: the name lives in server identity and ``onboarded`` is derived from
it being non-empty (``web/src/app/identity.tsx``). Storing either here would create a
second source of truth, so neither is part of this schema.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: entity_settings key — ``entity_settings/onboarding.json``.
_ENTITY = "onboarding"

#: The resume points of the guided first-run flow, in order.
STEPS: tuple[str, ...] = ("name", "essentials", "first_success", "done")

#: The essential-apps a run can set up. ``model`` and ``channel`` hold the chosen app's
#: name (or ``None``); ``search``/``speech`` are "did the user set one up" flags.
_ESSENTIALS_SCHEMA: dict[str, type] = {
    "model": str,
    "search": bool,
    "speech": bool,
    "channel": str,
}

#: The three "try one" cards of the first-success step.
_FIRST_SUCCESS_KEYS: tuple[str, ...] = ("knowledge", "trigger", "loop")

_FIRST_SUCCESS_SCHEMA: dict[str, type] = {k: bool for k in _FIRST_SUCCESS_KEYS}


def default_state() -> dict[str, Any]:
    """A freshly-installed home's onboarding state."""
    return {
        "step": STEPS[0],
        "essentials": {"model": None, "search": False, "speech": False, "channel": None},
        "first_success": {k: False for k in _FIRST_SUCCESS_KEYS},
    }


def _sanitize(raw: Any) -> dict[str, Any]:
    """Project whatever is on disk onto the schema, field by field.

    Every field falls back to its default independently, so a single bad value cannot
    lose the rest of the user's progress. Unknown keys are dropped (the notifications
    store's self-healing behaviour) rather than leaked back through the API.
    """
    state = default_state()
    if not isinstance(raw, dict):
        return state

    step = raw.get("step")
    if isinstance(step, str) and step in STEPS:
        state["step"] = step

    ess = raw.get("essentials")
    if isinstance(ess, dict):
        for key, typ in _ESSENTIALS_SCHEMA.items():
            val = ess.get(key)
            if typ is bool:
                if isinstance(val, bool):
                    state["essentials"][key] = val
            elif isinstance(val, str):
                state["essentials"][key] = val

    fs = raw.get("first_success")
    if isinstance(fs, dict):
        for key in _FIRST_SUCCESS_KEYS:
            if isinstance(fs.get(key), bool):
                state["first_success"][key] = fs[key]

    return state


def load_onboarding_state() -> dict[str, Any]:
    """The sanitized onboarding state. Never raises, never returns a partial shape."""
    try:
        from personalclaw.providers.entity_routes import _load_entity_settings

        return _sanitize(_load_entity_settings(_ENTITY))
    except Exception:  # noqa: BLE001 — onboarding must never 500 the first-run signal
        logger.warning("onboarding state unreadable — starting from the top", exc_info=True)
        return default_state()


def _validate_nested(name: str, patch: Any, schema: dict[str, type]) -> dict[str, Any]:
    """Validate one nested block of a patch, returning only its supplied keys."""
    if not isinstance(patch, dict):
        raise ValueError(f"'{name}' must be a JSON object")
    out: dict[str, Any] = {}
    for key, val in patch.items():
        if key not in schema:
            raise ValueError(f"Unknown '{name}' field: {key!r}")
        typ = schema[key]
        if typ is bool:
            if not isinstance(val, bool):
                raise ValueError(f"'{name}.{key}' must be a boolean")
        elif val is not None and not isinstance(val, str):
            raise ValueError(f"'{name}.{key}' must be a string or null")
        out[key] = val
    return out


def merge_onboarding_state(patch: Any) -> dict[str, Any]:
    """Merge a partial patch into the stored state and persist it.

    The merge is partial at BOTH levels: a patch naming only ``step`` leaves
    ``essentials`` and ``first_success`` untouched, and a patch naming only
    ``first_success.knowledge`` leaves ``trigger``/``loop`` at their stored values.
    That is what makes several independent onboarding steps able to record their own
    progress without reading and echoing back the whole document.

    Raises :class:`ValueError` for a non-object patch, an unknown key at either level,
    an out-of-domain ``step``, or a mistyped value — the caller turns that into a 400.
    """
    if not isinstance(patch, dict):
        raise ValueError("Body must be a JSON object")

    known = {"step", "essentials", "first_success"}
    unknown = sorted(set(patch) - known)
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(repr(k) for k in unknown)}")

    state = load_onboarding_state()

    if "step" in patch:
        step = patch["step"]
        if not isinstance(step, str) or step not in STEPS:
            raise ValueError(f"'step' must be one of: {', '.join(STEPS)}")
        state["step"] = step

    if "essentials" in patch:
        state["essentials"].update(
            _validate_nested("essentials", patch["essentials"], _ESSENTIALS_SCHEMA)
        )

    if "first_success" in patch:
        state["first_success"].update(
            _validate_nested("first_success", patch["first_success"], _FIRST_SUCCESS_SCHEMA)
        )

    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings(_ENTITY, state)
    return state
