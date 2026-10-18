"""A bare `pytest` must not run the browser gate — asserted, not merely intended.

The browser gate's contract has a NEGATIVE clause: "`make test-e2e` runs the browser gate offline
**and a bare pytest does not run it**". Nothing asserted the second half. It was true only by
construction — the gate is Playwright, so its specs are `.ts` and pytest collects `.py` — and
"true by construction" is exactly the state that ends with someone adding
`tests/test_e2e_smoke.py` that shells `npx playwright test`, because no rail said not to.

The cost is not abstract: the browser leg takes minutes per interpreter, which is far too slow
for the per-commit gate. A browser launch inside the unit suite also breaks two other properties
the suite depends on — it needs a built SPA (so the run stops being hermetic) and it spawns a
gateway subprocess under `-n auto`, i.e. one per xdist worker.

So this file asserts the separation from both ends:

* the `test` target is a plain pytest invocation that neither depends on nor performs the
  browser leg, and
* no pytest-collected module shells the browser gate.

The second check is AST-based on purpose. `tests/test_e2e_specs_are_executed.py` contains the
literal string ``"npx playwright test e2e/ghost.spec.ts"`` — twice — inside its own vacuity
assertion. A text scan would call that file a violation and the only available fix would be to
weaken the scan. Asking the syntax tree "is this string an argument to a subprocess call" tells
the two apart, which is the whole difference between a rail people keep and a rail people delete.
"""

from __future__ import annotations

import pytest
from browser_gate_stays_out_of_pytest_shared import (
    _BROWSER_TOKENS,
    _ROOT,
    _TESTS,
    BROWSER_TARGET,
    spawns_the_browser_gate,
)

_MAKEFILE = _ROOT / "Makefile"

#: The target a developer and CI both run for the unit suite.
UNIT_TARGET = "test"


def _make_recipe(target: str) -> list[str]:
    """The recipe lines of a Makefile target, without comments or the `.PHONY` declaration.

    Parsed rather than grepped because `test-e2e` appears in the `.PHONY` list and in nine
    comment lines; a substring search over the file cannot tell a target's body from prose
    about it — the same mistake `test_e2e_specs_are_executed.py` catches one level up.
    """
    recipe: list[str] = []
    in_target = False
    for raw in _MAKEFILE.read_text(encoding="utf-8").splitlines():
        if raw.startswith("\t"):
            if in_target:
                body = raw.lstrip("\t").strip()
                if body and not body.startswith("#"):
                    recipe.append(body)
            continue
        if raw.startswith("#") or not raw.strip():
            continue
        head, sep, _ = raw.partition(":")
        if sep and not head.startswith("."):
            in_target = head.strip() == target
    return recipe


def _target_prerequisites(target: str) -> list[str]:
    for raw in _MAKEFILE.read_text(encoding="utf-8").splitlines():
        if raw.startswith(("\t", "#")) or not raw.strip():
            continue
        head, sep, tail = raw.partition(":")
        if sep and head.strip() == target and not head.startswith("."):
            return tail.split()
    raise AssertionError(f"the Makefile has no `{target}` target — this rail is pinned to it")


class TestTheUnitTargetDoesNotDoTheBrowserLeg:
    def test_the_unit_target_is_a_plain_pytest_invocation(self):
        recipe = _make_recipe(UNIT_TARGET)
        assert recipe, f"`make {UNIT_TARGET}` has an empty recipe — this rail would be vacuous"
        assert any("pytest" in line for line in recipe), (
            f"`make {UNIT_TARGET}` no longer invokes pytest: {recipe}. Re-derive this rail "
            f"against whatever runs the unit suite now."
        )
        offenders = [ln for ln in recipe if any(t in ln.lower() for t in _BROWSER_TOKENS)]
        assert not offenders, (
            f"`make {UNIT_TARGET}` launches the browser gate: {offenders}\n\n"
            f"That is minutes per interpreter on the per-commit gate, it needs a built SPA (so "
            f"the run stops being hermetic), and under `-n auto` it spawns one gateway per xdist "
            f"worker. The browser leg belongs to `make {BROWSER_TARGET}`."
        )

    def test_the_unit_target_does_not_depend_on_the_browser_target(self):
        prereqs = _target_prerequisites(UNIT_TARGET)
        assert BROWSER_TARGET not in prereqs, (
            f"`{UNIT_TARGET}` now depends on `{BROWSER_TARGET}` ({prereqs}) — a bare unit run "
            f"would pull the browser gate in through the back door."
        )

    def test_the_browser_target_exists_and_owns_the_browser_leg(self):
        """The other side of the split. Absence proves nothing on its own: a repo that DELETED
        the browser gate would satisfy every assertion above."""
        recipe = _make_recipe(BROWSER_TARGET)
        assert recipe, f"`make {BROWSER_TARGET}` is gone — the browser gate now runs nowhere"
        assert any(
            "playwright" in line for line in recipe
        ), f"`make {BROWSER_TARGET}` no longer runs playwright: {recipe}"


class TestTheDetectorItself:
    """Vacuity, through the SAME function the sweep above uses.

    Both directions matter. A detector that never fires makes the sweep green for a repo that
    launches a browser in every module; one that fires on any mention of the word would flag
    `test_e2e_specs_are_executed.py`'s own vacuity assertion, and the cheapest way out of that
    would be to delete this file.
    """

    @pytest.mark.parametrize(
        "source",
        [
            'import subprocess\nsubprocess.run(["npx", "playwright", "test"])\n',
            'import subprocess\nsubprocess.check_call("npx playwright test", shell=True)\n',
            'import os\nos.system("npx playwright test e2e/a11y.spec.ts")\n',
            'import subprocess\nsubprocess.Popen(cmd=["npx", "playwright", "test"])\n',
        ],
    )
    def test_a_real_launch_is_caught(self, source: str):
        assert spawns_the_browser_gate(source), f"the detector missed a real launch:\n{source}"

    @pytest.mark.parametrize(
        "source",
        [
            # The shape that actually exists in this repo, and must NOT be flagged.
            'live = "      - run: npx playwright test e2e/ghost.spec.ts\\n"\n'
            'assert names_spec(live, "ghost.spec.ts")\n',
            '"""A docstring mentioning npx playwright test."""\n',
            'PATH = "web/e2e/a11y.spec.ts"\nassert "playwright" in open("Makefile").read()\n',
            'import subprocess\nsubprocess.run(["pytest", "-q"])\n',
        ],
    )
    def test_a_mere_mention_is_not_caught(self, source: str):
        assert not spawns_the_browser_gate(
            source
        ), f"the detector invented a launch from a string literal:\n{source}"

    def test_the_real_repo_shape_is_not_flagged(self):
        """The concrete file this precision exists for, by name rather than in principle.

        Since the rails split, the spec check's `playwright test` reads live in its tree half."""
        target = _TESTS / "rails" / "test_e2e_specs_are_executed_tree.py"
        assert target.exists(), "the file whose shape motivates the AST approach is gone"
        assert "playwright test" in target.read_text(encoding="utf-8"), (
            "that file no longer contains the literal this test exists to distinguish — the "
            "precision claim below would be vacuous"
        )
        assert not spawns_the_browser_gate(target.read_text(encoding="utf-8"))
