"""A dishonest quality declaration must turn CI red (APE-4).

This atom's subject IS declared-vs-actual honesty, which makes it self-referential: if
the enforcement were unrailed, APE-4 would have shipped the very defect it exists to
catch — a control that claims something and is never checked. So the load-bearing
assertion here is not that the schema parses or that a badge renders. It is that **a
claim which is false fails the build**, proven by planting a lie on each axis and
observing the failure.

Every lie is paired with a **vacuity floor**: the same bundle declaring the same axis
HONESTLY must come back clean. Without that pair, "the verifier returned violations"
could mean the verifier is broken and fails everything — the same defect wearing a
different hat, and a much harder one to notice.

The second honesty axis, easy to miss: an app that declares NOTHING. Absent must not
read as passing, and must not be silently rewritten to ``false``. An unbadged app and a
failing app are different states; a checker that demanded evidence for an undeclared
axis would punish silence harder than an honest miss.

Everything runs under ``tmp_path``. The one test that really spawns pytest builds its
bundle there too, so no run touches a real app tree or the real home.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import personalclaw
from personalclaw.apps.manifest import AppManifest
from personalclaw.apps.quality import (
    AXE_REPORT_RELPATH,
    EOF_UNREADABLE,
    QualityViolation,
    bundle_test_files,
    frontend_sources,
    load_token_lint_rules,
    main,
    run_bundle_tests,
    strip_comments,
    token_lint_bundle,
    token_lint_file,
    verify_app,
    verify_tree,
)

# --------------------------------------------------------------------------- #
# Bundle builders
# --------------------------------------------------------------------------- #

_CLEAN_TSX = """\
import React from 'react'

export function Panel() {
  return (
    <div className="rounded-lg bg-surface-high p-4 text-on-surface">
      <h2 className="text-lg font-medium">Panel</h2>
    </div>
  )
}
"""

# Two violations the SHARED token-lint rule flags: a raw hex and an inline-style px
# where a spacing token applies. Kept in one place so the "designSystem" lie and its
# honest floor differ ONLY in this file's content.
_DIRTY_TSX = """\
import React from 'react'

export function Panel() {
  return (
    <div style={{ padding: 12px }}>
      <span style={{ color: '#3355ff' }}>Panel</span>
    </div>
  )
}
"""

_PASSING_TEST = "def test_it_works():\n    assert 1 + 1 == 2\n"
_FAILING_TEST = "def test_it_works():\n    assert 1 + 1 == 3\n"


def make_bundle(
    root: Path,
    name: str = "demo-app",
    *,
    quality: dict | None = None,
    frontend: str | None = None,
    tests: str | None = None,
    axe: dict | None = None,
    version: str = "1.0.0",
    ui_pages: bool = False,
) -> Path:
    """Write a minimal, VALID app bundle under ``root`` and return its dir."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "name": name,
        "version": version,
        "displayName": name.title(),
        "description": "A bundle used to prove the quality verifier bites.",
    }
    if quality is not None:
        manifest["quality"] = quality
    if ui_pages:
        manifest["ui"] = {"pages": [{"route": "/demo", "label": "Demo", "icon": "Blocks"}]}
    (d / "app.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if frontend is not None:
        (d / "ui" / "src").mkdir(parents=True, exist_ok=True)
        (d / "ui" / "src" / "index.tsx").write_text(frontend, encoding="utf-8")
    if tests is not None:
        (d / "test_demo.py").write_text(tests, encoding="utf-8")
    if axe is not None:
        (d / "a11y").mkdir(parents=True, exist_ok=True)
        (d / "a11y" / "axe-report.json").write_text(json.dumps(axe), encoding="utf-8")
    # Every bundle must be a manifest core would accept — a lie has to be a valid
    # manifest, or the red would be "bad app.json", not "dishonest declaration".
    assert AppManifest.from_json_file(d / "app.json").validate() == []
    return d


def axes(violations: list[QualityViolation]) -> list[str]:
    return sorted(v.axis for v in violations)


def never_runs(_app_dir: Path) -> tuple[bool, str]:
    """A test runner that must not be consulted — used to prove the verifier short-
    circuits before spawning pytest when there is nothing to run / nothing claimed."""
    raise AssertionError("run_tests must not be called here")


def fake_tests(passed: bool):
    def runner(_app_dir: Path) -> tuple[bool, str]:
        return passed, "1 failed" if not passed else "1 passed"

    return runner


# --------------------------------------------------------------------------- #
# Lie 1 — tested: true
# --------------------------------------------------------------------------- #


class TestTestedAxis:
    def test_the_lie_a_bundle_with_no_tests_at_all(self, tmp_path):
        """`tested: true` with nothing to run is a lie, and it is caught STATICALLY —
        before pytest is even spawned (the runner raises if consulted)."""
        d = make_bundle(tmp_path, quality={"tested": True})
        v = verify_app(d, run_tests=never_runs)
        assert axes(v) == ["tested"]
        assert "no test_*.py" in v[0].reason

    def test_the_floor_the_same_bundle_declaring_honestly_stays_clean(self, tmp_path):
        """Vacuity floor for the lie above: `tested: false` on the SAME testless bundle
        is an honest miss and demands nothing. An honest miss must never cost more
        than silence."""
        d = make_bundle(tmp_path, quality={"tested": False})
        assert verify_app(d, run_tests=never_runs) == []

    def test_the_lie_tests_exist_but_fail(self, tmp_path):
        """Presence is not evidence. This is the leg the apps-repo `tests` job backs:
        the bundle ships tests, they run, they fail — the claim is still false."""
        d = make_bundle(tmp_path, quality={"tested": True}, tests=_FAILING_TEST)
        v = verify_app(d, run_tests=fake_tests(False))
        assert axes(v) == ["tested"]
        assert "own tests fail" in v[0].reason

    def test_the_floor_tests_exist_and_pass(self, tmp_path):
        """Same bundle, same claim, passing tests → clean. The ONLY difference from the
        lie above is the run's outcome, so the red is attributable to the outcome."""
        d = make_bundle(tmp_path, quality={"tested": True}, tests=_PASSING_TEST)
        assert verify_app(d, run_tests=fake_tests(True)) == []

    def test_the_default_runner_really_runs_the_bundles_tests(self, tmp_path):
        """The seam above is injectable, so this pins the REAL runner — otherwise the
        whole axis could pass on a fake while the shipped path never worked.

        Both directions, on bundles that differ only in one assertion.
        """
        good = make_bundle(tmp_path / "g", "good-app", tests=_PASSING_TEST)
        bad = make_bundle(tmp_path / "b", "bad-app", tests=_FAILING_TEST)
        assert bundle_test_files(good) and bundle_test_files(bad)
        passed_good, _ = run_bundle_tests(good, timeout=180.0)
        passed_bad, tail = run_bundle_tests(bad, timeout=180.0)
        assert passed_good is True
        assert passed_bad is False
        assert "fail" in tail.lower() or "error" in tail.lower()

    def test_tests_under_a_tests_subdir_also_count(self, tmp_path):
        """The apps-repo CI discovers `<app>/tests/test_*.py` too (slack-channel has
        that shape), so the presence check must not miss it and mint a false lie."""
        d = make_bundle(tmp_path, quality={"tested": True})
        (d / "tests").mkdir()
        (d / "tests" / "test_nested.py").write_text(_PASSING_TEST, encoding="utf-8")
        assert [p.name for p in bundle_test_files(d)] == ["test_nested.py"]
        assert verify_app(d, run_tests=fake_tests(True)) == []


# --------------------------------------------------------------------------- #
# Lie 2 — designSystem: "v2"
# --------------------------------------------------------------------------- #


class TestDesignSystemAxis:
    def test_the_lie_frontend_fails_token_lint(self, tmp_path):
        """`designSystem: "v2"` on a bundle carrying a raw hex + a raw inline px."""
        d = make_bundle(tmp_path, quality={"designSystem": "v2"}, frontend=_DIRTY_TSX)
        v = verify_app(d, run_tests=never_runs)
        assert axes(v) == ["designSystem"]
        assert "token-lint fails" in v[0].reason
        assert "ui/src/index.tsx" in v[0].reason

    def test_the_floor_a_token_clean_frontend_stays_green(self, tmp_path):
        """Vacuity floor: same claim, same file path, tokens instead of raw values."""
        d = make_bundle(tmp_path, quality={"designSystem": "v2"}, frontend=_CLEAN_TSX)
        assert verify_app(d, run_tests=never_runs) == []

    def test_the_floor_declaring_legacy_is_an_honest_miss(self, tmp_path):
        """`"legacy"` claims nothing, so the SAME dirty frontend is not a violation.
        This is what keeps the rule from punishing apps that admit they predate v2."""
        d = make_bundle(tmp_path, quality={"designSystem": "legacy"}, frontend=_DIRTY_TSX)
        assert verify_app(d, run_tests=never_runs) == []

    def test_a_claim_with_nothing_to_lint_is_a_violation_not_a_free_pass(self, tmp_path):
        """The vacuity trap on the OTHER side: a backend-only bundle declaring `"v2"`
        would otherwise earn the badge because the lint found no files to fail.
        Nothing was checked, so nothing was proven — that is a violation, and the
        message says which honest value to use instead."""
        d = make_bundle(tmp_path, quality={"designSystem": "v2"})
        v = verify_app(d, run_tests=never_runs)
        assert axes(v) == ["designSystem"]
        assert "no frontend source" in v[0].reason
        assert '"n/a"' in v[0].reason

    def test_build_config_and_tests_are_not_linted(self, tmp_path):
        """A bundle's vite.config.ts / *.test.tsx carry no app chrome. If they were
        linted, every app would be a liar and the rule would be worthless."""
        d = make_bundle(tmp_path, quality={"designSystem": "v2"}, frontend=_CLEAN_TSX)
        (d / "ui" / "vite.config.ts").write_text("export default { base: '#nope' }\n", "utf-8")
        (d / "ui" / "src" / "index.test.tsx").write_text(_DIRTY_TSX, encoding="utf-8")
        (d / "ui" / "node_modules").mkdir()
        (d / "ui" / "node_modules" / "dep.ts").write_text(_DIRTY_TSX, encoding="utf-8")
        names = {p.name for p in frontend_sources(d)}
        assert names == {"index.tsx"}
        assert verify_app(d, run_tests=never_runs) == []

    def test_the_lint_uses_the_shared_rule_not_a_second_dialect(self, tmp_path):
        """The rule is DATA (apps/token_lint_rules.json), shared with
        web/src/design/tokenLintRule.ts. Assert the app-side lint really consumes it
        and reproduces the host rule's exemptions — a re-implementation that drifted
        would be the same declared-vs-actual defect one layer down.
        """
        rules = load_token_lint_rules()
        assert set(rules) == {"hex", "raw_px", "px_ok_context", "calc_with_token"}
        d = make_bundle(tmp_path, quality={"designSystem": "v2"})
        (d / "ui" / "src").mkdir(parents=True)
        # Each line is a documented host-rule EXEMPTION. All must stay clean.
        (d / "ui" / "src" / "index.tsx").write_text(
            "// a comment may cite #ff00ff and 12px freely\n"
            "const a = <div style={{ width: 'calc(var(--w) + 160px)' }} />\n"
            "const b = <div style={{ gridTemplateColumns: 'minmax(0, 120px)' }} />\n"
            "const c = <div style={{ borderBottom: '1px solid var(--c)' }} />\n"
            "const d = <div style={{ height: Math.min(400px, 9) }} />\n",
            encoding="utf-8",
        )
        assert token_lint_bundle(d) == {}
        # …and the rule is not vacuous: the dirty file still fails.
        (d / "ui" / "src" / "index.tsx").write_text(_DIRTY_TSX, encoding="utf-8")
        assert list(token_lint_bundle(d)) == ["ui/src/index.tsx"]


# --------------------------------------------------------------------------- #
# Comment state — the block-comment tracker, and its parity with the TS twin
# --------------------------------------------------------------------------- #

#: BEHAVIOURAL parity pin, read by BOTH languages' tests. A regex can be shared as data
#: (``token_lint_rules.json``); a lexer cannot, so ``strip_comments`` exists twice and it
#: is the BEHAVIOUR that is shared instead. See the file's own ``_comment``.
COMMENT_CASES_PATH = Path(personalclaw.__file__).parent / "apps" / "token_lint_comment_cases.json"


def _comment_cases() -> list[dict]:
    data = json.loads(COMMENT_CASES_PATH.read_text(encoding="utf-8"))
    return list(data["cases"])


class TestCommentState:
    """An interior line of a multi-line ``{/* … */}`` block carries no marker of its own,
    so the old shape-based skip linted it as code — and since every decimal digit is a hex
    digit, the ``hex`` pattern matched any 3-to-8-digit issue reference. An app author
    citing an issue number in a block comment failed the bundle quality gate (#3337).
    """

    def test_the_shared_case_file_was_found_and_carries_both_directions(self):
        """Vacuity floor, twice over. An empty ``cases`` list would make every
        parametrised test below vanish and the suite would still be green; a file with
        only ``clean_*`` cases would green a tracker that stopped catching raw hexes
        ENTIRELY, which is the whole risk of tracking comment state.

        The counts run over INTENDED cases only. A ``gap_*`` case declares what the
        tracker does where that is a known hole — it reports no violations, so counting
        it as a negative control would let a recorded hole pad the very floor it is
        invisible to. An unknown prefix is refused rather than silently swept into the
        clean count, which is how that padding would arrive by accident.
        """
        cases = _comment_cases()
        assert len(cases) >= 10, COMMENT_CASES_PATH
        prefixes = ("clean_", "red_", "end_state_", "gap_")
        for c in cases:
            assert c["name"].startswith(prefixes), f"{c['name']}: unknown prefix {prefixes}"
        intended = [c for c in cases if not c["name"].startswith("gap_")]
        reds = [c for c in intended if any(c["expected"])]
        cleans = [c for c in intended if not any(c["expected"])]
        assert len(reds) >= 5, "no positive controls — a no-op tracker would pass"
        assert len(cleans) >= 5, "no negative controls — the bug would not be covered"
        for c in cases:
            assert len(c["expected"]) == len(c["lines"]), f"{c['name']}: one per line"
            assert c["end_state"] in ("code", "block"), f"{c['name']}: end_state"

    @pytest.mark.parametrize("case", _comment_cases(), ids=lambda c: c["name"])
    def test_the_python_tracker_reaches_the_declared_verdict(self, case, tmp_path):
        """The Python half of the parity pin. A case that reds here and passes in
        ``tokenLintRuleParity.test.ts`` (or vice versa) is the two-dialect defect the
        rules file exists to prevent, one layer up: the host would lint one way and an
        app's badge would be earned another."""
        text = "\n".join(case["lines"])
        stripped = strip_comments(text)
        assert len(stripped.code) == len(case["lines"]), "a line index must stay a line number"
        assert stripped.end_state == case["end_state"], f"{case['name']}: {case['why']}"

        # Drive it through the REAL entry point, not just the scanner, so the caller's
        # wiring is covered too: token_lint_file reports "<line>: <kind> — <text>".
        f = tmp_path / "case.tsx"
        f.write_text(text, encoding="utf-8")
        got: list[list[str]] = [[] for _ in case["lines"]]
        for hit in token_lint_file(f):
            lineno, _, rest = hit.partition(": ")
            got[int(lineno) - 1].append(rest.split(" — ")[0])
        assert got == [list(e) for e in case["expected"]], f"{case['name']}: {case['why']}"

    def test_the_reported_text_is_the_original_line_not_the_stripped_one(self, tmp_path):
        """A violation should read the way the author wrote it. Only the VERDICT runs on
        comment-stripped text; reporting the stripped text would print a line the author
        cannot find in their editor."""
        f = tmp_path / "x.tsx"
        f.write_text("/* why */ const c = '#abc'\n", encoding="utf-8")
        assert token_lint_file(f) == ["1: hex — /* why */ const c = '#abc'"]

    def test_an_unterminated_block_comment_is_refused_by_name_not_reported_clean(self, tmp_path):
        """A stuck-open tracker reads as "the rest of the file is clean" and silently
        stops catching raw hexes — the ONE way this change could weaken the gate rather
        than fix it. ``end_state`` made that observable and NOTHING read it (#3347): the
        badge is decided by :func:`token_lint_bundle`, whose empty dict awarded
        ``designSystem: "v2"`` to a bundle with a raw hex two lines under the opener.

        So the per-line silence is still correct — an unterminated ``/*`` really does
        comment the rest of the file out — and it is no longer the whole verdict: the
        bundle names the file instead of omitting it.
        """
        text = "/* opened and never closed\nconst c = '#abc'\n"
        assert strip_comments(text).end_state == "block"
        d = make_bundle(tmp_path, quality={"designSystem": "v2"})
        (d / "ui" / "src").mkdir(parents=True)
        f = d / "ui" / "src" / "index.tsx"
        f.write_text(text, encoding="utf-8")
        # Per-line: correctly empty. It saw no line of CODE violate.
        assert token_lint_file(f) == []
        # The badge's input: NOT empty. The file is named, with the reason.
        assert token_lint_bundle(d) == {"ui/src/index.tsx": [EOF_UNREADABLE]}
        # …and that reaches the declaration, so "v2" is refused rather than earned.
        v = verify_app(d, run_tests=never_runs)
        assert [x.axis for x in v] == ["designSystem"]
        assert "ui/src/index.tsx" in v[0].reason
        # The floor: close the comment and the very same line is caught the normal way,
        # with no EOF entry — the refusal is not a blanket red on every bundle.
        f.write_text("/* opened and closed */\nconst c = '#abc'\n", encoding="utf-8")
        assert token_lint_file(f) == ["2: hex — const c = '#abc'"]
        assert token_lint_bundle(d) == {"ui/src/index.tsx": ["2: hex — const c = '#abc'"]}
        # And a clean file stays omitted entirely.
        f.write_text("/* opened and closed */\nconst c = 'var(--c)'\n", encoding="utf-8")
        assert token_lint_bundle(d) == {}

    def test_a_line_violation_leads_the_report_when_the_file_is_also_unreadable(self, tmp_path):
        """Ordering matters to the human reading the refusal: ``verify_app`` renders
        ``e.g. <file> <first hit>``, so a real line violation must come first and the EOF
        entry last. Reversed, every such bundle would report the generic EOF sentence and
        bury the actionable line."""
        d = make_bundle(tmp_path, quality={"designSystem": "v2"})
        (d / "ui" / "src").mkdir(parents=True)
        (d / "ui" / "src" / "index.tsx").write_text(
            "const c = '#abc'\n/* and then the tracker never closes\n", encoding="utf-8"
        )
        assert token_lint_bundle(d) == {
            "ui/src/index.tsx": ["1: hex — const c = '#abc'", EOF_UNREADABLE]
        }
        assert "e.g. ui/src/index.tsx 1: hex" in verify_app(d, run_tests=never_runs)[0].reason


# --------------------------------------------------------------------------- #
# Lie 3 — a11y: true
# --------------------------------------------------------------------------- #


def _clean_axe(version: str = "1.0.0") -> dict:
    return {"appVersion": version, "tool": "axe-core", "violations": []}


class TestA11yAxis:
    def test_the_lie_axe_found_violations(self, tmp_path):
        d = make_bundle(
            tmp_path,
            quality={"a11y": True},
            ui_pages=True,
            axe={
                "appVersion": "1.0.0",
                "tool": "axe-core",
                "violations": [{"id": "color-contrast"}, {"id": "button-name"}],
            },
        )
        v = verify_app(d, run_tests=never_runs)
        assert axes(v) == ["a11y"]
        assert "2 axe violation(s)" in v[0].reason
        assert "color-contrast" in v[0].reason

    def test_the_floor_a_clean_scan_stays_green(self, tmp_path):
        """Vacuity floor: identical bundle, identical claim, zero violations."""
        d = make_bundle(tmp_path, quality={"a11y": True}, ui_pages=True, axe=_clean_axe())
        assert verify_app(d, run_tests=never_runs) == []

    def test_the_lie_no_evidence_at_all(self, tmp_path):
        """axe needs a browser the apps-repo CI does not have, so the app produces the
        artifact and CI checks it. NO artifact means the claim is unverified — which is
        indistinguishable from false, and must not be treated as true."""
        d = make_bundle(tmp_path, quality={"a11y": True}, ui_pages=True)
        v = verify_app(d, run_tests=never_runs)
        assert axes(v) == ["a11y"]
        assert AXE_REPORT_RELPATH in v[0].reason

    def test_the_lie_a_stale_report_from_a_previous_release(self, tmp_path):
        """The freshness leg: a clean scan of 1.0.0 must not vouch for 2.0.0. Without
        it, one honest scan would launder every later release forever."""
        d = make_bundle(
            tmp_path,
            quality={"a11y": True},
            ui_pages=True,
            version="2.0.0",
            axe=_clean_axe("1.0.0"),
        )
        v = verify_app(d, run_tests=never_runs)
        assert axes(v) == ["a11y"]
        assert "stale scan" in v[0].reason
        # Floor: the same report, produced against the version actually shipping.
        (d / "a11y" / "axe-report.json").write_text(json.dumps(_clean_axe("2.0.0")), "utf-8")
        assert verify_app(d, run_tests=never_runs) == []

    def test_the_lie_an_envelope_that_cannot_say_zero(self, tmp_path):
        """A report with no `violations` list proves nothing. Accepting it would let an
        empty `{}` buy the badge — the cheapest lie available."""
        d = make_bundle(
            tmp_path, quality={"a11y": True}, ui_pages=True, axe={"appVersion": "1.0.0"}
        )
        v = verify_app(d, run_tests=never_runs)
        assert axes(v) == ["a11y"]
        assert "cannot say 'zero'" in v[0].reason

    def test_unparseable_evidence_is_not_evidence(self, tmp_path):
        d = make_bundle(tmp_path, quality={"a11y": True}, ui_pages=True, axe=_clean_axe())
        (d / "a11y" / "axe-report.json").write_text("{not json", encoding="utf-8")
        assert axes(verify_app(d, run_tests=never_runs)) == ["a11y"]

    def test_the_floor_declaring_false_demands_no_evidence(self, tmp_path):
        d = make_bundle(tmp_path, quality={"a11y": False}, ui_pages=True)
        assert verify_app(d, run_tests=never_runs) == []


# --------------------------------------------------------------------------- #
# Declaring NOTHING — absent is neither a pass nor a false
# --------------------------------------------------------------------------- #


class TestAbsentDeclaration:
    def test_an_app_that_declares_nothing_is_never_asked_for_evidence(self, tmp_path):
        """No block → no claim → nothing to verify. A bundle with no tests, a dirty
        frontend and no axe report is CLEAN, because it promised none of it."""
        d = make_bundle(tmp_path, quality=None, frontend=_DIRTY_TSX)
        assert AppManifest.from_json_file(d / "app.json").quality is None
        assert verify_app(d, run_tests=never_runs) == []

    def test_absent_is_not_silently_upgraded_to_false(self, tmp_path):
        """The parse boundary keeps the tri-state: absent stays ``None``, and a
        declared-false stays ``False``. Collapsing them would make the Store unable
        to tell "said nothing" from "said no" — a lie in the other direction."""
        absent = AppManifest.from_json_file(make_bundle(tmp_path / "a", "no-block") / "app.json")
        declared = AppManifest.from_json_file(
            make_bundle(tmp_path / "b", "false-block", quality={"tested": False, "a11y": False})
            / "app.json"
        )
        assert absent.quality is None
        assert declared.quality is not None
        assert declared.quality.tested is False
        # …and the two do not serialise the same. This is the wire-level statement of
        # "absent ≠ false": one omits the key, the other carries it as false.
        assert "quality" not in absent.to_dict()
        assert declared.to_dict()["quality"] == {"tested": False, "a11y": False}

    def test_an_undeclared_axis_stays_undeclared_on_the_wire(self, tmp_path):
        """Per-axis, not just per-block: declaring `tested` must not conjure
        `designSystem`/`a11y` keys the app never wrote."""
        d = make_bundle(tmp_path, quality={"tested": True}, tests=_PASSING_TEST)
        m = AppManifest.from_json_file(d / "app.json")
        assert m.to_dict()["quality"] == {"tested": True}
        assert m.quality is not None
        assert m.quality.declared("tested") is True
        assert m.quality.declared("a11y") is False


# --------------------------------------------------------------------------- #
# The CALL SITE — the CLI the apps-repo CI runs
# --------------------------------------------------------------------------- #


class TestTheCiCallSite:
    def test_a_tree_with_one_liar_exits_nonzero(self, tmp_path, capsys):
        """The thing that actually turns CI red: `python -m personalclaw.apps.quality
        <tree>` exits 1 and names the app, the axis and the claim."""
        make_bundle(tmp_path, "honest-app", quality={"designSystem": "v2"}, frontend=_CLEAN_TSX)
        make_bundle(tmp_path, "lying-app", quality={"designSystem": "v2"}, frontend=_DIRTY_TSX)
        assert main([str(tmp_path)]) == 1
        out = capsys.readouterr().out
        assert "lying-app" in out
        assert "quality.designSystem" in out
        assert "honest-app" not in out

    def test_an_all_honest_tree_exits_zero(self, tmp_path, capsys):
        """The floor for the call site itself: without it, "exit 1" could be the only
        thing this CLI is capable of."""
        make_bundle(tmp_path, "honest-app", quality={"designSystem": "v2"}, frontend=_CLEAN_TSX)
        make_bundle(tmp_path, "quiet-app", quality=None, frontend=_DIRTY_TSX)
        make_bundle(tmp_path, "honest-miss-app", quality={"designSystem": "legacy", "a11y": False})
        assert main([str(tmp_path)]) == 0
        assert "every declared claim is backed" in capsys.readouterr().out

    def test_an_empty_tree_fails_rather_than_reporting_a_pass(self, tmp_path, capsys):
        """A checker that checked nothing must not print a green line. This is the
        vacuity floor for the CI job as a whole — a wrong working-directory or a
        renamed layout has to fail loudly, not silently verify zero apps."""
        assert main([str(tmp_path / "empty")]) == 1
        assert "nothing was verified" in capsys.readouterr().err

    def test_the_tree_walk_reports_how_many_apps_it_saw(self, tmp_path):
        make_bundle(tmp_path, "a-app", quality={"tested": False})
        make_bundle(tmp_path, "b-app", quality=None)
        violations, seen = verify_tree(tmp_path, run_tests=never_runs)
        assert (violations, seen) == ([], 2)

    def test_an_unreadable_manifest_is_reported_not_skipped(self, tmp_path):
        """A bundle whose app.json cannot be parsed must not slip through the quality
        job as "no declarations found"."""
        (tmp_path / "broken-app").mkdir()
        (tmp_path / "broken-app" / "app.json").write_text("{oops", encoding="utf-8")
        violations, seen = verify_tree(tmp_path, run_tests=never_runs)
        assert seen == 1
        assert axes(violations) == ["manifest"]

    def test_the_module_is_executable_as_the_ci_step_invokes_it(self, tmp_path):
        """`python -m personalclaw.apps.quality` — the literal command the apps-repo CI
        step runs. Pinning it here means a renamed module or a missing `__main__`
        guard fails in core, not silently in the other repo's workflow.

        The child's ``PYTHONPATH`` is pinned to the tree this test IMPORTED from. Without
        that the child resolves ``personalclaw`` through whatever editable install the
        venv points at — another checkout, in a worktree — and the exit code would be 1
        for "no such module" while the test read it as "caught the liar": a green that
        proves the opposite of what it claims.
        """
        make_bundle(tmp_path, "lying-app", quality={"designSystem": "v2"}, frontend=_DIRTY_TSX)
        src_root = Path(personalclaw.__file__).resolve().parent.parent
        env = {**os.environ, "PYTHONPATH": str(src_root)}
        proc = subprocess.run(
            [sys.executable, "-m", "personalclaw.apps.quality", str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=180,
            env=env,
        )
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "lying-app" in proc.stdout, proc.stdout + proc.stderr
        assert "quality.designSystem" in proc.stdout


# --------------------------------------------------------------------------- #
# Multi-axis + real-tree sanity
# --------------------------------------------------------------------------- #


def test_every_axis_can_be_caught_in_one_pass(tmp_path):
    """Three lies at once → three violations, one per axis. Guards against an early
    `return` that would let the first violation mask the rest."""
    d = make_bundle(
        tmp_path,
        quality={"tested": True, "designSystem": "v2", "a11y": True},
        frontend=_DIRTY_TSX,
        ui_pages=True,
    )
    assert axes(verify_app(d, run_tests=fake_tests(False))) == ["a11y", "designSystem", "tested"]


def test_all_three_axes_honest_at_once_stays_green(tmp_path):
    """The combined vacuity floor — the same three claims, all backed."""
    d = make_bundle(
        tmp_path,
        quality={"tested": True, "designSystem": "v2", "a11y": True},
        frontend=_CLEAN_TSX,
        tests=_PASSING_TEST,
        ui_pages=True,
        axe=_clean_axe(),
    )
    assert verify_app(d, run_tests=fake_tests(True)) == []


def test_the_verifier_has_no_gateway_call_site():
    """The verifier spawns pytest, and it is allowed to do so UNCEILINGED because it is a
    CI/CLI tool no request path can reach. That exemption is recorded in
    ``tests/test_spawn_ceiling_audit.py::_OPERATOR_EXEMPT``, and an exemption whose
    premise nobody checks is exactly this atom's defect class. So pin the premise: if
    ``apps.quality`` ever gains a runtime importer, this reds and the classification has
    to be re-argued instead of quietly inherited.

    Docstrings and comments that merely NAME the module are not importers, so the scan
    is for real import statements only.
    """
    import ast

    import personalclaw as pkg

    src = Path(pkg.__file__).resolve().parent
    importers: list[str] = []
    for path in sorted(src.rglob("*.py")):
        if path.name == "quality.py" and path.parent.name == "apps":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                mods = [base] + [f"{base}.{a.name}" for a in node.names]
            if any(m.endswith("apps.quality") or m == "personalclaw.apps.quality" for m in mods):
                importers.append(f"{path.relative_to(src).as_posix()}:{node.lineno}")
    assert importers == [], (
        "apps.quality is now imported by runtime code, so its uncapped `subprocess.run` "
        "is reachable from the gateway. Re-argue the spawn classification (ceiling-wrap "
        f"it or justify the exemption anew) — importers: {importers}"
    )
    # Vacuity floor: the scan really walks the package and really detects an import.
    assert len(list(src.rglob("*.py"))) > 100
    probe = ast.parse("from personalclaw.apps.quality import verify_app\n")
    assert any(isinstance(n, ast.ImportFrom) for n in ast.walk(probe))


def test_the_shipped_native_bundles_pass_the_verifier(tmp_path):
    """Run the real verifier over core's own native app tree. None of them declares a
    quality block today, so this proves only that the checker walks a real tree
    without crashing and does not invent violations — NOT that anything is enforced
    there. Recorded as such deliberately: an all-absent tree is a vacuous pass, and
    calling it evidence of enforcement is the mistake this file is about.
    """
    import personalclaw.apps as apps_pkg

    native = Path(apps_pkg.__file__).parent / "native"
    violations, seen = verify_tree(native, run_tests=never_runs)
    assert seen >= 20, f"expected core's native bundles, walked {seen}"
    assert violations == []
    declared = [
        d.name
        for d in sorted(native.iterdir())
        if (d / "app.json").is_file()
        and AppManifest.from_json_file(d / "app.json").quality is not None
    ]
    assert (
        declared == []
    ), f"if a native bundle starts declaring, this rail stops being vacuous: {declared}"


@pytest.mark.parametrize("level", ["v2", "legacy", "n/a"])
def test_the_three_design_system_levels_are_all_installable(tmp_path, level):
    """Validation must accept exactly the declared vocabulary. A level the manifest
    rejects at install could never reach a badge; a level it silently accepted would
    render as nothing, reading exactly like "declared nothing"."""
    m = AppManifest.from_dict(
        {
            "name": "x",
            "version": "1.0.0",
            "displayName": "X",
            "description": "d",
            "quality": {"designSystem": level},
        }
    )
    assert m.validate() == []


def test_an_unknown_design_system_level_is_an_install_error():
    m = AppManifest.from_dict(
        {
            "name": "x",
            "version": "1.0.0",
            "displayName": "X",
            "description": "d",
            "quality": {"designSystem": "v3"},
        }
    )
    assert any("quality.designSystem" in e for e in m.validate())
