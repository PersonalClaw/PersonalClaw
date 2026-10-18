"""The source-tree checks split from
``tests/test_selfqa_companion.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import pytest
import selfqa_companion_shared

from personalclaw.selfqa.triage import IMPACT_NONE, IMPACT_TEST, IMPACT_USER, classify_paths

watch_state = selfqa_companion_shared.watch_state


class TestClauseTwoLedgerOnlySkip:
    """`a test-only commit yields a ledger-only skip with a one-line rationale`.

    This is an assertion about what gets WRITTEN. The row and its rationale are the only things
    that distinguish "ran and correctly skipped" from "never fired", so the row is asserted
    positively, its rationale is asserted non-empty and single-line, and the absence of findings
    is asserted only alongside it.
    """

    @pytest.fixture(autouse=True)
    def _ledger_home(self, tmp_path, monkeypatch):
        """Redirect the run ledger to a per-test tmp dir (#3546).

        `record_triage`/`Journal` resolve their file through
        `personalclaw.workflows.store.config_dir()`. `conftest.py`'s session-wide
        `_isolate_real_home_writers` guard only substitutes a tmp dir there when the caller chose
        NEITHER `$PERSONALCLAW_HOME` nor a repointed `$HOME` — it deliberately backs off the moment
        either is set, which is the ordinary shape of a contributor's local loop. Left unpatched,
        every test below appends to whichever ledger that chosen home already has, and the hard-
        coded run ids this class used to carry (`selfqa-clause2`, …) made a SECOND invocation in
        that same home count a running total instead of this run's own rows — measured as
        `assert 4 == 1` on the second run. Redirecting `config_dir` here removes the dependence on
        the ambient home entirely.

        `autouse` rather than opt-in per test: two of the tests below (the provider tests that
        check `output[...]` rather than calling `ledger()` themselves) still write real rows
        through this same seam even though their own assertions never count them, so a redirect
        that only covered the count-sensitive tests would still leave this class writing into
        whatever home the invoking shell happens to have chosen.
        """
        from personalclaw.workflows import store

        home = tmp_path / "ledger-home"
        home.mkdir()
        monkeypatch.setattr(store, "config_dir", lambda: home)

    @pytest.mark.parametrize(
        "paths,expected",
        [
            (["tests/test_a.py"], IMPACT_TEST),
            (["src/x/test_a.py"], IMPACT_TEST),
            (["web/src/a.test.ts"], IMPACT_TEST),
            (["conftest.py"], IMPACT_TEST),
            (["docs/a.md"], IMPACT_NONE),
            (["README.md"], IMPACT_NONE),
            ([".github/workflows/ci.yml"], IMPACT_NONE),
            ([], IMPACT_NONE),
            (["src/personalclaw/a.py"], IMPACT_USER),
            (["web/src/App.tsx"], IMPACT_USER),
            # One shipped line among a hundred test lines is still a shipped line.
            (["tests/test_a.py", "src/personalclaw/a.py"], IMPACT_USER),
            (["tests/test_a.py", "docs/a.md"], IMPACT_TEST),
        ],
    )
    def test_the_classifier_verdicts(self, paths, expected):
        impact, rationale = classify_paths(paths)
        assert impact == expected, (paths, rationale)
        assert rationale.strip() and "\n" not in rationale
