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
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: String spellings that mean False. ``""`` is included: an empty value is not an opt-in.
_FALSE_WORDS = frozenset({"false", "no", "off", "0", "n", ""})

#: String spellings that mean True.
_TRUE_WORDS = frozenset({"true", "yes", "on", "1", "y"})


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
