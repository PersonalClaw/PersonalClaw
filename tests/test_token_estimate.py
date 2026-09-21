"""The chars-per-token estimate is defined in ONE place, with exactly TWO stated roles.

Issue #2364's third clause: ten modules had independently approximated "how many tokens
is this text?" — six named constants and four bare ``// 4`` literals with no constant at
all — and they did not all agree. The fix is a leaf module every caller imports from.

Two things are asserted here, and **both** are needed, because either alone reads green on
a half-done consolidation:

1. A **source census** — nothing under ``src/personalclaw`` defines its own chars-per-token
   constant except the canonical module, and the four files that carried bare ``// 4``
   divisors carry none. Without this, someone re-adds a local ``CHARS_PER_TOKEN = 4`` next
   month and every behavioural test still passes.
2. The **two-role property** — the conservative ratio is strictly smaller than the nominal
   one, so it over-estimates token usage. Without this, a later "tidy-up" collapses the two
   constants into one and deletes a safety margin that no budget test can see.

The census carries its own **failability control**: the same pattern that must find nothing
outside the canonical module must find exactly the two definitions inside it. A regex that
silently stopped matching would otherwise make this file pass by measuring nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.token_estimate import (
    CONSERVATIVE_CHARS_PER_TOKEN,
    NOMINAL_CHARS_PER_TOKEN,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "personalclaw"

#: The canonical home. Every other module must import from it, not restate it.
_CANONICAL = _SRC / "token_estimate.py"

#: A local definition of the ratio, under any of the names the ten sites had used. Matches
#: ``CHARS_PER_TOKEN = 4``, ``_CHARS_PER_TOKEN = 3.0`` and ``CHARS_PER_TOKEN: int = 4``; a
#: RHS that names another constant (``= NOMINAL_CHARS_PER_TOKEN``) is a re-export, not a
#: redefinition, so it deliberately does not match.
_LOCAL_DEFINITION = re.compile(r"_?CHARS_PER_TOKEN(?::\s*[a-z]+)?\s*=\s*[0-9]", re.IGNORECASE)

#: The four modules that divided by a bare literal with no constant at all — the half of
#: the clause a name-only census cannot see.
_BARE_DIVISOR_FILES = (
    "dashboard/handlers/knowledge.py",
    "learning/surfacing.py",
    "workflows/engine.py",
    "workflows/longrun.py",
)


class TestOneDefinition:
    def test_the_pattern_finds_the_canonical_definitions(self) -> None:
        """Failability control: prove the census pattern matches before trusting its zero."""
        found = _LOCAL_DEFINITION.findall(_CANONICAL.read_text(encoding="utf-8"))
        assert len(found) == 2, f"expected the two canonical definitions, found {found}"

    def test_no_module_defines_its_own_ratio(self) -> None:
        offenders: list[str] = []
        for path in sorted(_SRC.rglob("*.py")):
            if path == _CANONICAL:
                continue
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if _LOCAL_DEFINITION.search(line):
                    offenders.append(f"{path.relative_to(_SRC)}:{lineno}: {line.strip()}")
        assert offenders == [], (
            "these modules restate the chars-per-token ratio instead of importing it from "
            "personalclaw.token_estimate:\n" + "\n".join(offenders)
        )

    @pytest.mark.parametrize("relpath", _BARE_DIVISOR_FILES)
    def test_the_bare_divisor_sites_import_the_constant(self, relpath: str) -> None:
        text = (_SRC / relpath).read_text(encoding="utf-8")
        assert "// 4" not in text, f"{relpath} still divides by a bare literal"
        assert "NOMINAL_CHARS_PER_TOKEN" in text, f"{relpath} does not import the constant"


class TestTwoRoles:
    def test_the_trigger_ratio_over_estimates_relative_to_the_budget_ratio(self) -> None:
        """The whole reason there are two constants and not one."""
        assert CONSERVATIVE_CHARS_PER_TOKEN < NOMINAL_CHARS_PER_TOKEN

    def test_over_estimating_is_what_a_trigger_needs(self) -> None:
        """Stated as the behaviour, not the value: same text, more estimated tokens."""
        chars = 12_000
        nominal_tokens = chars / NOMINAL_CHARS_PER_TOKEN
        trigger_tokens = chars / CONSERVATIVE_CHARS_PER_TOKEN
        assert trigger_tokens > nominal_tokens

    def test_the_native_compaction_trigger_is_bound_to_the_conservative_ratio(self) -> None:
        """The one site of the ten that must NOT use the nominal ratio."""
        assert NativeAgentRuntime._EST_CHARS_PER_TOKEN == CONSERVATIVE_CHARS_PER_TOKEN

    def test_the_ratios_stay_in_a_plausible_band_for_english_and_code(self) -> None:
        """Guards a fat-fingered edit without pinning either value."""
        assert 2 <= CONSERVATIVE_CHARS_PER_TOKEN <= NOMINAL_CHARS_PER_TOKEN <= 6
