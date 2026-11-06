"""Field-metadata and value-coercion primitives shared by every config section.

The leaf of the config package: it imports nothing from PersonalClaw, so every section
module can depend on it without a cycle. That is the whole reason it exists as a module
rather than living beside ``AppConfig`` — ``_meta`` is referenced by every dataclass in
every section, so leaving it in ``loader.py`` would force each extracted section to import
its parent back (``loader`` -> ``safety`` -> ``loader``) and no section could move at all.

The two flag parsers are deliberately opposite-polarity and must stay that way: a guard's
ambiguity fails ON (:func:`_guard_flag`), an exposure's fails OFF (:func:`_expose_flag`).
"""

import math


def _safe_int(value: object, default: int) -> int:
    """Convert *value* to int, returning *default* on failure.

    A boolean takes the default too: ``True`` is ``1`` to Python, and a flag written where a count
    belongs is not a count (``capture.retention_days: true`` would keep one day of recordings). So
    does a number no int holds (``1e999`` reads as infinity, and ``int()`` of it overflows)."""
    if isinstance(value, bool):
        return default
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_choice(value: object, allowed: tuple[str, ...], default: str) -> str:
    """Coerce *value* to one of *allowed* (case/space-insensitive), else *default*.

    The ``_safe_int`` sibling for closed string enums. An off-scale value resolving to the
    shipped default rather than raising keeps one typo from making config.json unloadable —
    and for a security knob the default must be the SAFE end of the scale, which is the
    caller's choice of *default*, never the first listed value.
    """
    try:
        candidate = str(value).strip().lower()
    except Exception:  # noqa: BLE001 — an unstringable value is just an invalid one
        return default
    return candidate if candidate in allowed else default


def _safe_float(value: object, default: float) -> float:
    """Convert *value* to float, returning *default* on failure (the ``_safe_int`` sibling).

    A malformed number in config.json must degrade to the shipped default, not raise — a config
    typo should never make the whole file unloadable. A boolean takes the default, as in
    :func:`_safe_int`, and so does a value that is not finite (``"nan"``, ``"inf"``): no setting
    means either, and a threshold of infinity is no threshold.
    """
    if isinstance(value, bool):
        return default
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _meta(label: str, help: str, **kwargs: object) -> dict:
    """Helper to build field metadata dicts with safe defaults."""
    return {"label": label, "help": help, **kwargs}


# Guard-flag spellings that DISABLE a guard; anything else (missing/unknown/typo)
# stays ENABLED. Mirrors ``guardrails.flags.guard_flag`` but is defined locally to
# keep the config loader free of a guardrails import (avoids an import cycle).
_GUARD_FALSE = frozenset({"0", "false", "no", "off", "disable", "disabled", "n", "f"})


def _guard_flag(value: object) -> bool:
    """Parse a guard-class flag fail-safe: missing/unknown ⇒ ``True`` (enabled).

    Only an explicit bool ``False``, ``0``, or a known falsy token disables. This is the
    fail-safe tenet — a guard's ambiguity must fail ON.
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in _GUARD_FALSE
    return True


# Exposure-flag spellings that ENABLE a surface. The inverse polarity of
# ``_GUARD_FALSE``: for anything that opens an attack surface, ambiguity must fail
# OFF, so ONLY these exact spellings turn it on. `bool("false")` is True in Python,
# which is precisely the trap this avoids.
_EXPOSE_TRUE = frozenset({"1", "true", "yes", "on", "enable", "enabled", "y", "t"})


def _expose_flag(value: object) -> bool:
    """Parse an exposure flag fail-CLOSED: missing/unknown/garbage ⇒ ``False``.

    Use for any flag whose ``True`` opens a network surface or widens access. The
    mirror of :func:`_guard_flag`, which fails ON because a guard's ambiguity must
    keep protecting.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in _EXPOSE_TRUE
    return False


def _num(value: object, default: float) -> float:
    """Coerce a config number, falling back to ``default`` for anything unparseable.

    Needed because the cap fields below are read from a hand-editable `config.json`:
    a bare `float("abc")` raises, and an exception inside `AppConfig.load()`'s single
    expression does not degrade one field — it takes down the WHOLE config load, so a
    typo in a rate limit would present as an instance that cannot start. Clamping to
    a sane range is the dataclass's `__post_init__`; this only guarantees a finite number: a JSON
    NaN passes the schema as a number, and the clamp's ``min(1000.0, nan)`` is 1000, the widest
    rate there is.
    """
    if isinstance(value, bool):  # `True` is an int in Python; a flag is not a rate.
        return default
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError:
            return default
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return default
    else:
        return default
    return number if math.isfinite(number) else default


def _str_list(raw: object) -> list[str]:
    """A list-of-strings config value, or ``[]``.

    Fail-closed on SHAPE, matching `_ea_surface_data`: a string, a dict or a stray
    number yields the empty list rather than raising. For `upstream_allowlist` the
    empty list is the DENY-everything position, so a malformed value degrades to "no
    approved upstreams" — never to "allow all".
    """
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, (str, int, float)) and str(item).strip()]
