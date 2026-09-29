"""First-run onboarding progress state.

**What this holds.** The furthest step of the guided first-run flow this home has reached,
which essential apps the user set up, and which "try one" first-success cards they
completed:

.. code-block:: json

    {
      "step": "name",
      "essentials": {"model": null, "search": false, "speech": false, "channel": null},
      "first_success": {"knowledge": false, "trigger": false, "loop": false}
    }

**Why it is entity state, not config.** This is per-user progress through a flow, not a
tunable — so it lives in ``entity_settings/onboarding.json`` and is written by a dedicated
``POST /api/onboarding/state``, never through the ``_EDITABLE_CONFIG`` PATCH allowlist
(the entity-state rule). It rides the existing ``entity_settings`` inventory entry
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

**The name a run passed its first step with is, as a draft.** ``name_draft`` holds the name and
handle the run continued past step 1 with, until the run ends. It is not identity: nothing but
the flow reads it, the flow still writes identity only when the run finishes, and a finished run
holds none (a ``step: "done"`` write clears it). It exists because every other fact about a run
in progress lives here while the name lived in one tab's ``sessionStorage`` — so a second tab, or
the same browser after its tab closed, opened the flow on step 1 with an empty name, telling a
user who had imported her whole setup that nothing was set up.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: entity_settings key — ``entity_settings/onboarding.json``.
_ENTITY = "onboarding"

#: The resume points of the guided first-run flow, in order.
#:
#: **Every step is a resume point.** The first version named only three of the five, on the
#: reasoning that a point should mean "the next step you have not finished" — so the import
#: step and the recap got none. Driven on a fresh home that cost the user real progress: with
#: the flow stopped on the import step the file still said ``name``, so a reload restarted at
#: the beginning; stopped on the recap it said ``first_success``, so a reload walked the user
#: BACK a step. The field is now the high-water mark — the furthest step this run has stood on
#: — written on entry and never lowered, which is the only reading a reload can resume from.
#:
#: Extending the domain is compatible in both directions: every value an older client can have
#: written is still a member, and the frontend's ``stepFromStored`` resolves a value it does not
#: recognise to "start at the beginning" rather than guessing.
STEPS: tuple[str, ...] = ("name", "import", "essentials", "first_success", "ready", "done")

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

#: The draft of the name step (module docstring): exactly these keys, all required.
_NAME_DRAFT_SCHEMA: dict[str, type] = {"name": str, "handle": str, "handle_touched": bool}

#: Characters kept of a draft's name and handle: the cap the identity write puts on the name it
#: becomes (``dashboard.user_name``), so the draft never holds more than the name can.
_NAME_DRAFT_CHARS = 80


def default_state() -> dict[str, Any]:
    """A freshly-installed home's onboarding state."""
    return {
        "step": STEPS[0],
        "essentials": {"model": None, "search": False, "speech": False, "channel": None},
        "first_success": {k: False for k in _FIRST_SUCCESS_KEYS},
        "name_draft": None,
    }


def _name_draft(value: Any) -> dict[str, Any] | None:
    """*value* as a stored name draft, or ``None`` when it is not one. Never raises."""
    if not isinstance(value, dict) or set(value) != set(_NAME_DRAFT_SCHEMA):
        return None
    name, handle, touched = value["name"], value["handle"], value["handle_touched"]
    if not isinstance(name, str) or not name.strip():
        return None
    if not isinstance(handle, str) or not isinstance(touched, bool):
        return None
    return {
        "name": name.strip()[:_NAME_DRAFT_CHARS],
        "handle": handle.strip()[:_NAME_DRAFT_CHARS],
        "handle_touched": touched,
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

    # A finished run holds no draft whatever the file says (see `merge_onboarding_state`).
    if state["step"] != "done":
        state["name_draft"] = _name_draft(raw.get("name_draft"))

    return state


def load_onboarding_state() -> dict[str, Any]:
    """The sanitized onboarding state. Never raises, never returns a partial shape."""
    try:
        from personalclaw.providers.entity_routes import _load_entity_settings

        # Fail-OPEN on a discarded read (`or {}`): an unreadable store means the first-run
        # state starts from the top. Replaying onboarding costs a few clicks; nothing is lost.
        return _sanitize(_load_entity_settings(_ENTITY) or {})
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

    ``name_draft`` is replaced whole (``null`` clears it): the name step is one answer, not
    three fields a step could fill independently.

    Raises :class:`ValueError` for a non-object patch, an unknown key at either level,
    an out-of-domain ``step``, or a mistyped value — the caller turns that into a 400.
    """
    if not isinstance(patch, dict):
        raise ValueError("Body must be a JSON object")

    known = {"step", "essentials", "first_success", "name_draft"}
    unknown = sorted(set(patch) - known)
    if unknown:
        raise ValueError(f"Unknown field(s): {', '.join(repr(k) for k in unknown)}")

    draft: dict[str, Any] | None = None
    if "name_draft" in patch and patch["name_draft"] is not None:
        # Validated before anything is read or written, so a refused draft writes nothing.
        offered = patch["name_draft"]
        if not isinstance(offered, dict) or set(offered) != set(_NAME_DRAFT_SCHEMA):
            raise ValueError(
                "'name_draft' must be null or an object with exactly: "
                + ", ".join(_NAME_DRAFT_SCHEMA)
            )
        draft = _name_draft(offered)
        if draft is None:
            raise ValueError(
                "'name_draft' needs a non-empty string 'name', a string 'handle' and a boolean "
                "'handle_touched'"
            )

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

    if "name_draft" in patch:
        state["name_draft"] = draft
    # A finished run's name is identity now — the flow commits it before it records `done` — so
    # the draft goes with the run, and "Run setup again" starts from the stored name, never from
    # a stale draft.
    if state["step"] == "done":
        state["name_draft"] = None

    from personalclaw.providers.entity_routes import _save_entity_settings

    _save_entity_settings(_ENTITY, state)
    return state
