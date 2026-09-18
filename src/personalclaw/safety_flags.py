"""Strict boolean coercion for flags that decide whether a safety control applies.

``bool("false")`` is ``True``. ``bool("0")`` is ``True``. So a flag read as
``bool(data.get("auto_approve_subagent_spawn", False))`` is **enabled** by a value whose author
plainly meant to disable it — and the failure lands in the unsafe direction, silently.

That is not a hypothetical typo. Five flags read this way from data a user or a client controls:

* ``hooks.py`` — ``auto_approve_subagent_spawn`` / ``auto_approve_subagent_tools``, from the
  hooks JSON file the user edits by hand. JSON accepts both ``false`` and ``"false"``, and the
  quoted form is the habit anyone arriving from YAML or a shell env brings with them.
* ``workflows/handlers.py`` — ``skip_preflight`` / ``always_allow``, from an **HTTP request body**,
  where the value is whatever the client sent.
* ``workflows/engine.py`` — ``unattended_suppress``, from config.

The asymmetry is what makes it worth a helper rather than five local fixes: for a normal field a
truthy string is a harmless coercion, and for a safety flag it is the difference between a control
applying and not applying. So this refuses to guess:

* a real ``bool`` passes through;
* a **recognised** string spelling maps as written — ``"false"``/``"no"``/``"off"``/``"0"``/``""``
  are False, ``"true"``/``"yes"``/``"on"``/``"1"`` are True;
* a number coerces normally (``0`` → False), because that spelling is unambiguous;
* anything else — an unrecognised string, a list, a dict — logs a WARNING and returns the
  **default**, which for every caller here is the safe value. An unreadable flag must not enable a
  control, and it must not do so quietly either.

Destructive consent — the ``confirm`` family — is the same defect on a sharper edge, and it
lives here too
==============================================================================================

``confirm`` is the consent flag on every destructive door in the tree: a knowledge merge that
deletes an item, a tag merge that deletes a tag, a history revert, a task-list reset, a lexicon
wipe, an archive restore, a keychain migration. It shipped implemented **two** different ways.
Nine body-reading doors compared it to the literal ``True``; nine used plain Python truthiness. So
``confirm: "false"`` — a body that literally says do-not-confirm — read as **confirmed**, and
``POST /api/knowledge/items/{id}/merge`` deleted the merged-away item at HTTP 200 (issue 3000).
Every truthy JSON value was a yes (``"false"``, ``"0"``, ``"no"``, ``{"nested": 1}``, ``["x"]``);
only the falsy ones (``0``, ``0.0``, ``[]``, ``{}``) refused. A bool that has been through a
template, a query string, or a model's JSON emitter arrives as a *string*, so ``"false"`` is the
value clients actually send.

:func:`confirm_granted` is deliberately **stricter than** :func:`strict_bool`: consent must be the
JSON literal ``true``, so the string ``"true"`` is refused rather than honoured. That is not a
preference, it is the only choice that is fail-closed on all twenty-four doors — nine already
required the literal, so accepting ``strict_bool``'s wider spelling set would have *loosened* a
security control to fix a different one. A refused ``"true"`` costs a client one retry against a
message that names the literal; an honoured ``"false"`` costs a user their data.

**The fix is the coercion, not the route.** A per-route repair is what left issue 2983 open after
eight of nine doors were fixed: the rail passed, the ninth door stayed broken, and nothing said so.
The same shape sank the flag half of this module — ``test_safety_flags_reject_truthy_strings``
enumerates ``(module, flag)`` pairs, so the whole ``confirm`` family was invisible to it and it
read green. ``tests/test_confirm_gate_parity.py`` is therefore a **ban on the pattern**, not a
list of doors: no module in ``src/`` other than this one may read a :data:`CONFIRM_FIELDS` key at
all, so a destructive route added tomorrow either calls :func:`confirm_granted` or reds the rail.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

#: String spellings that mean False. ``""`` is included: an empty value is not an opt-in.
_FALSE_WORDS = frozenset({"false", "no", "off", "0", "n", ""})

#: String spellings that mean True.
_TRUE_WORDS = frozenset({"true", "yes", "on", "1", "y"})

#: Every field name that carries DESTRUCTIVE consent. The parity rail reads this set, so a new
#: consent flag is registered here rather than re-derived at its call site.
CONFIRM_FIELDS: frozenset[str] = frozenset({"confirm", "confirm_cascade"})

#: The one spelling :func:`confirm_granted_query` accepts, case-insensitively.
QUERY_CONFIRM_TRUE = "true"


def strict_bool(value: object, *, field: str, default: bool = False) -> bool:
    """Coerce *value* to a bool without letting a string enable a safety control by accident.

    *field* is used only in the warning, so an operator can find the line they wrote.
    *default* is returned for ``None`` and for anything unrecognised — pass the SAFE value.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _FALSE_WORDS:
            return False
        if word in _TRUE_WORDS:
            return True
        logger.warning(
            "%s: %r is not a boolean — using %r. Write true or false (unquoted) to be explicit.",
            field,
            value,
            default,
        )
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    logger.warning(
        "%s: %r is a %s, not a boolean — using %r.",
        field,
        value,
        type(value).__name__,
        default,
    )
    return default


def confirm_granted(payload: Any, field: str = "confirm") -> bool:
    """True only when ``payload[field]`` is the JSON literal ``true`` — destructive consent.

    :param payload: The decoded request body or tool-argument mapping. A non-mapping (a bare
        list, a string, the ``{}`` a caller substitutes for a body that failed to parse) is
        **not** consent, so a call site needs no ``isinstance(body, dict)`` guard of its own.
    :param field: The consent field, one of :data:`CONFIRM_FIELDS`.

    Every other value is a refusal, including ``"true"``, ``1`` and ``"1"``. That is the point:
    a predicate that honours any truthy value cannot tell a yes from a stringified no, which is
    how ``confirm: "false"`` deleted a knowledge item at HTTP 200. Use :func:`strict_bool` for an
    ordinary safety flag, where a hand-written ``"true"`` should be honoured; use this for a door
    that destroys data, where being wrong in the permissive direction is unrecoverable.
    """
    return isinstance(payload, Mapping) and payload.get(field) is True


def confirm_granted_query(query: Any, field: str = "confirm") -> bool:
    """True only when the ``?{field}=`` query value spells ``true`` (any case).

    The query-string peer of :func:`confirm_granted`, for the two doors whose consent rides in
    the URL because the body is a multipart stream that must not be buffered to answer the
    question. A URL cannot carry a real boolean, so this compares against the one spelling the
    docs promise (``docs/reference/api-overview.md`` documents ``?mode=replace&confirm=true``).
    Whitespace is stripped; every other value — ``1``, ``yes``, an absent param — is a refusal.
    ``api_durability_import`` used to accept ``1``/``yes``; no caller in the tree, the frontend or
    the docs ever sent them, and a door that overwrites a home is the wrong place to keep an
    undocumented synonym alive.
    """
    try:
        raw = query.get(field, "")
    except (AttributeError, TypeError):
        return False
    return isinstance(raw, str) and raw.strip().lower() == QUERY_CONFIRM_TRUE
