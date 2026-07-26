"""Fail-safe guard-flag parsing (AUTONOMY-GUARDRAILS §5, platform tenet).

**Config flags guarding destructive/trust behavior parse missing/null/unknown
values as ENABLED (fail-safe); only an explicit falsy value disables.** A typo in
a guard flag must keep the guard ON, never silently off.

``guard_flag`` covers the raw env/dict reads that bypass the dataclass tree (env
vars like ``PERSONALCLAW_DISABLE_LIVE_WRITES``, the incident flag file, a raw
denylist-enabled dict value). The complementary half of the tenet — guard-class
*dataclass* fields defaulting to their SAFE value — is enforced by a schema test
(`tests/test_guardrails_flags.py`), because `_validate_config_data` is advisory
(it strips an invalid value so the dataclass default applies).
"""

from __future__ import annotations

# Only these explicit, unambiguous falsy spellings DISABLE a guard. Everything
# else — a missing value (None), an empty string, an unknown token, a typo —
# parses as ENABLED. This is deliberately the opposite of a permissive
# truthy-parse: for a guard, ambiguity must fail SAFE (on).
_EXPLICIT_FALSE = frozenset({"0", "false", "no", "off", "disable", "disabled", "n", "f"})


def guard_flag(value: object) -> bool:
    """Return whether a guard is ENABLED, parsing fail-safe.

    * ``None`` / missing → ``True`` (enabled — the safe default).
    * a real ``bool`` → itself (an explicit dataclass/typed value is honored).
    * a string → ``False`` ONLY for an explicit falsy token (``"0"``, ``"false"``,
      ``"no"``, ``"off"``, ``"disable[d]"``, ``"n"``, ``"f"``, case/space-insensitive);
      any other string (including ``""`` and unknown tokens) → ``True``.
    * an int → C-style (``0`` disables, non-zero enables).
    * anything else → ``True`` (unknown shape fails safe).
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        token = value.strip().lower()
        return token not in _EXPLICIT_FALSE
    return True
