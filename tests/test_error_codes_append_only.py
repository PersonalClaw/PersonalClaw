"""Append-only guard for the §2 AgentError code registry (PLATFORM-LEGIBILITY §2).

A shipped ``ERROR_CODES`` key is a STABLE surface: an agent — and the saved
prompts/SOPs it writes — branch on the code, never on the prose. So once a code
is released it is never removed or reworded; new failure paths ADD a code. This
suite is the enforcement the plan's §2.1 comment promises ("test asserts no
removal/redefinition").

The mechanism is a frozen baseline (:data:`_RELEASED`) embedded here: the set of
codes + meanings released as of this slice. The live registry MUST be a superset
whose baseline entries match byte-for-byte. Adding a code = add a row to
``ERROR_CODES`` (the baseline is untouched → still a subset → still green).
Removing or rewording one = the baseline entry no longer matches → red, which is
the whole point. When a genuinely new batch of codes is *released* (not merely
merged), they graduate into this baseline in the same change.

Also asserts the convention that keeps this registry from colliding with the
`AGENTS.md` §"Shared conventions" HTTP error envelope (registry:
:data:`personalclaw.http_errors.HTTP_ERROR_CODES`): agent codes are
``ERR_UPPER_SNAKE``; HTTP codes are ``lowercase_snake``. The two never overlap by
construction, so a consumer always knows which surface a code belongs to.
"""

from __future__ import annotations

import re

from personalclaw.errors import ERROR_CODES

# The codes released as of PLATFORM-LEGIBILITY §2's initial slice. APPEND a row
# here only when a code is actually released; never edit or delete an existing
# row. This is deliberately a copy, not an import of a subset of ERROR_CODES —
# the copy is what detects an in-place reword of the live meaning.
_RELEASED: dict[str, str] = {
    "ERR_TOOL_ARG_INVALID": (
        "A tool argument failed validation (wrong type, out of range, or not in "
        "the allowed set)."
    ),
    "ERR_MODEL_UNRESOLVED": (
        "The model/provider bound to a use case cannot be resolved — the pin names "
        "a provider absent from config, or no provider is configured."
    ),
    "ERR_HOOK_PROVIDER_UNKNOWN": (
        "A hook/trigger names an action provider that is not registered or not in "
        "the allowed set."
    ),
    "ERR_ACTION_PROVIDER_FAILED": ("An action provider raised while executing a trigger's action."),
}

_CODE_RE = re.compile(r"^ERR_[A-Z0-9]+(?:_[A-Z0-9]+)*$")


def test_every_released_code_is_still_present():
    """No released code may be removed — a branch that reads it must never break."""
    missing = [c for c in _RELEASED if c not in ERROR_CODES]
    assert not missing, f"released error codes removed (append-only violation): {missing}"


def test_released_meanings_are_unchanged():
    """A released code's meaning is its contract — it is never reworded in place."""
    for code, meaning in _RELEASED.items():
        assert ERROR_CODES[code] == meaning, (
            f"{code}: meaning changed (append-only violation). Released codes are a "
            f"stable surface — add a NEW code instead of rewording an existing one."
        )


def test_all_codes_follow_the_err_upper_snake_convention():
    """ERR_UPPER_SNAKE keeps agent codes disjoint from the HTTP lowercase_snake space."""
    bad = [c for c in ERROR_CODES if not _CODE_RE.match(c)]
    assert not bad, f"codes violate the ERR_UPPER_SNAKE convention: {bad}"


def test_no_agent_code_collides_with_the_http_lowercase_snake_space():
    """A code that is all-lowercase would be ambiguous with the HTTP envelope."""
    lowercased = [c for c in ERROR_CODES if c == c.lower()]
    assert not lowercased, f"agent codes must not be lowercase (HTTP-envelope space): {lowercased}"


def test_every_code_has_a_nonempty_meaning():
    empty = [c for c, m in ERROR_CODES.items() if not (m and m.strip())]
    assert not empty, f"codes with an empty meaning: {empty}"
