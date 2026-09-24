"""Field-metadata and value-coercion primitives shared by every config section.

The leaf of the config package: it imports nothing from PersonalClaw, so every section
module can depend on it without a cycle. That is the whole reason it exists as a module
rather than living beside ``AppConfig`` — ``_meta`` is referenced by every dataclass in
every section, so leaving it in ``loader.py`` would force each extracted section to import
its parent back (``loader`` -> ``safety`` -> ``loader``) and no section could move at all.

The two flag parsers are deliberately opposite-polarity and must stay that way: a guard's
ambiguity fails ON (:func:`_guard_flag`), an exposure's fails OFF (:func:`_expose_flag`).
"""


def _safe_int(value: object, default: int) -> int:
    """Convert *value* to int, returning *default* on failure."""
    try:
        return int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
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
    typo should never make the whole file unloadable.
    """
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _meta(label: str, help: str, **kwargs: object) -> dict:
    """Helper to build field metadata dicts with safe defaults."""
    return {"label": label, "help": help, **kwargs}


# Guard-flag spellings that DISABLE a guard; anything else (missing/unknown/typo)
# stays ENABLED. Mirrors ``guardrails.flags.guard_flag`` but is defined locally to
# keep the config loader free of a guardrails import (avoids an import cycle).
_GUARD_FALSE = frozenset({"0", "false", "no", "off", "disable", "disabled", "n", "f"})


def _guard_flag(value: object) -> bool:
    """Parse a guard-class flag fail-safe: missing/unknown ⇒ ``True`` (enabled).

    Only an explicit bool ``False``, ``0``, or a known falsy token disables. See the
    §5 fail-safe tenet — a guard's ambiguity must fail ON.
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
    a sane range is the dataclass's `__post_init__`; this only guarantees a number.
    """
    if isinstance(value, bool):  # `True` is an int in Python; a flag is not a rate.
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


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
