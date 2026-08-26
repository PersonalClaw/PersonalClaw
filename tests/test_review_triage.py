"""EI-9 — line-anchored review findings, human triage, accepted-subset dispatch.

The atom's `done_when` is SC9: *"a workflow review stage emits line-anchored findings; the triage
panel validates anchors against the real diff; the user accepts 2 of 5; the accepted pair
auto-dispatches to the originating worker which applies them; rejected findings land in the
calibration record; nothing was auto-written without acceptance"*.

Two of those clauses need more than a happy path, and this module is organized around them:

**"Nothing was auto-written without acceptance" is a NEGATIVE property**, so a test that only
checks the accepted pair arrives proves nothing about it. `TestNothingIsWrittenWithoutAcceptance`
asserts the write path is NOT REACHED — the delivery seam is a spy that records every call, and
the assertion is on the absence of a call and on the absence of the rejected text from what WAS
sent. The vacuity leg lives in the same class: the accepted pair DOES reach the same spy through
the same function, so an assertion that could never fail is ruled out by construction.

**"Validates anchors against the real diff"** needs the stale case, not the matching one. An anchor
that no longer matches the diff must be reported UNANCHORED — never silently applied at whatever
now occupies that line number, which is the worst failure this feature can have.
"""

from __future__ import annotations

import json

import pytest

from personalclaw import review_triage as rt
from personalclaw.review_triage import (
    AnchorState,
    Finding,
    TriageDecision,
    TriageOutcome,
)

# ── fixtures: one real unified diff, five findings over it ───────────────────

#: A real `git diff HEAD` shape: two files, one hunk each, added + context + removed lines.
#: Line numbers on the NEW side: src/app.py 10..14, src/util.py 3..5.
DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,3 +10,5 @@ def handler(req):
     token = req.get("token")
+    if not token:
+        return None
     log.info("serving %s", token)
     return render(token)
diff --git a/src/util.py b/src/util.py
index 3333333..4444444 100644
--- a/src/util.py
+++ b/src/util.py
@@ -3,2 +3,3 @@ import os
 def cache_dir():
-    return "/tmp"
+    return os.environ.get("CACHE", "/tmp")

"""

RUN = "run-ei9"
NODE = "review"


def _five_findings() -> list[Finding]:
    """Five findings over `DIFF`: three anchorable, two not — the SC9 shape.

    The two unanchorable ones are the cases that matter: `src/app.py:999` names a line the diff
    does not contain, and `src/util.py:4` names a line that EXISTS while quoting text that no
    longer does. The second is the dangerous one — a line-number-only check calls it anchored.
    """
    return rt.parse_findings(
        {
            "findings": [
                {
                    "severity": "Critical",
                    "location": "src/app.py:11",
                    "problem": "a falsy token short-circuits to None with no log",
                    "why": "a caller cannot tell a missing token from a render failure",
                    "recommended_fix": "log the rejection before returning",
                    "status": "Open",
                    "auto_fixable": False,
                    "line_text": "    if not token:",
                },
                {
                    "severity": "Minor",
                    "location": "src/util.py:5",
                    "problem": "CACHE is read without expanduser",
                    "why": "a ~-relative CACHE resolves to a literal ~ directory",
                    "recommended_fix": "wrap in os.path.expanduser",
                    "status": "Open",
                    "auto_fixable": True,
                },
                {
                    "severity": "Nit",
                    "location": "src/app.py:14",
                    "problem": "render(token) could name its argument",
                    "status": "Open",
                    "auto_fixable": True,
                },
                {
                    "severity": "Major",
                    "location": "src/app.py:999",
                    "problem": "the retry loop never backs off",
                    "status": "Open",
                },
                {
                    "severity": "Major",
                    "location": "src/util.py:4",
                    "problem": 'the hardcoded "/tmp" ignores XDG',
                    "status": "Open",
                    "line_text": '    return "/tmp"',
                },
            ]
        },
        run_id=RUN,
        node_id=NODE,
        session_key="worker:origin",
    )


class _Spy:
    """The delivery seam, recording every call. The absence of a call is the assertion."""

    def __init__(self, ok: bool = True) -> None:
        self.calls: list[tuple[str, str]] = []
        self.ok = ok

    def __call__(self, target: str, brief: str) -> bool:
        self.calls.append((target, brief))
        return self.ok


# ── clause: a review stage emits line-anchored findings ──────────────────────


class TestTheCanonicalFindingRecordIsParsedNotReinvented:
    def test_five_findings_parse_with_severity_location_and_auto_fixable(self):
        findings = _five_findings()
        assert len(findings) == 5
        assert [f.severity for f in findings] == [
            "Critical",
            "Minor",
            "Nit",
            "Major",
            "Major",
        ]
        assert (findings[0].path, findings[0].line) == ("src/app.py", 11)
        assert findings[1].auto_fixable is True
        assert findings[3].auto_fixable is False

    def test_a_row_with_no_problem_is_not_a_finding(self):
        """An empty row would occupy a triage slot and train the user to bulk-reject."""
        assert rt.parse_findings({"findings": [{"severity": "Nit", "location": "a.py:1"}]}) == []

    def test_output_may_arrive_as_a_json_string_or_a_bare_list(self):
        row = [{"severity": "Nit", "location": "a.py:1", "problem": "p"}]
        assert len(rt.parse_findings(row)) == 1
        assert len(rt.parse_findings(json.dumps({"findings": row}))) == 1

    def test_prose_output_yields_no_findings(self):
        """Inventing findings from prose is how a panel shows things no reviewer said."""
        assert rt.parse_findings("the code looks fine to me") == []
        assert rt.parse_findings({"summary": "fine"}) == []

    def test_auto_fixable_resolves_to_false_when_in_doubt(self):
        raw = {"findings": [{"severity": "Nit", "location": "a.py:1", "problem": "p"}]}
        assert rt.parse_findings(raw)[0].auto_fixable is False
        raw["findings"][0]["auto_fixable"] = "no"
        assert rt.parse_findings(raw)[0].auto_fixable is False
        raw["findings"][0]["auto_fixable"] = "true"
        assert rt.parse_findings(raw)[0].auto_fixable is True

    def test_the_key_is_content_derived_not_positional(self):
        """A positional key would move the user's accept onto a different finding."""
        findings = _five_findings()
        assert len({f.key for f in findings}) == 5
        assert findings[0].key == _five_findings()[0].key
        moved = Finding(
            severity="Critical",
            location="src/app.py:11",
            problem="a falsy token short-circuits to None with no log",
            origin_run_id=RUN,
            origin_node_id=NODE,
        )
        assert moved.key == findings[0].key


# ── clause: anchors validated against the REAL diff ──────────────────────────


class TestAnchorsAreValidatedAgainstTheRealDiff:
    def test_the_diff_parses_to_new_side_line_numbers(self):
        table = rt.parse_diff_lines(DIFF)
        assert set(table) == {"src/app.py", "src/util.py"}
        assert table["src/app.py"][11] == "    if not token:"
        assert table["src/app.py"][14] == "    return render(token)"
        # The REMOVED line has no new-side number: nothing may anchor to it.
        assert '    return "/tmp"' not in table["src/util.py"].values()
        assert table["src/util.py"][4] == '    return os.environ.get("CACHE", "/tmp")'

    def test_three_anchor_and_two_do_not(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        assert [a.state.value for a in anchored] == [
            "anchored",
            "anchored",
            "anchored",
            "unanchored",
            "unanchored",
        ]

    def test_a_line_the_diff_does_not_contain_is_unanchored_not_relocated(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        stale = anchored[3]
        assert stale.state is AnchorState.UNANCHORED
        assert stale.reason == "line_not_in_diff"
        assert stale.resolved_line == 0, "an unanchored finding must not claim a line"

    def test_a_stale_anchor_whose_line_still_exists_is_caught_by_content(self):
        """The worst available failure: line 4 EXISTS, so a number-only check calls this anchored
        and the accepted fix lands on an unrelated line."""
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        moved = anchored[4]
        assert moved.state is AnchorState.UNANCHORED
        assert moved.reason == "content_moved"
        assert moved.diff_line_text == '    return os.environ.get("CACHE", "/tmp")'

    def test_a_location_with_no_line_is_honestly_unanchorable(self):
        findings = rt.parse_findings(
            {"findings": [{"severity": "Major", "location": "the error handling", "problem": "p"}]}
        )
        (only,) = rt.validate_anchors(findings, DIFF)
        assert (only.state, only.reason) == (AnchorState.UNANCHORED, "no_line_anchor")

    def test_a_file_outside_the_diff_is_unanchored(self):
        findings = rt.parse_findings(
            {"findings": [{"severity": "Major", "location": "src/other.py:11", "problem": "p"}]}
        )
        (only,) = rt.validate_anchors(findings, DIFF)
        assert (only.state, only.reason) == (AnchorState.UNANCHORED, "file_not_in_diff")

    def test_an_ambiguous_basename_is_refused_rather_than_guessed(self):
        """Two `handlers.py` in one diff is common; picking either anchors to a file nobody read."""
        diff = (
            "diff --git a/a/handlers.py b/a/handlers.py\n--- a/a/handlers.py\n+++ b/a/handlers.py\n"
            "@@ -1,0 +1,1 @@\n+one\n"
            "diff --git a/b/handlers.py b/b/handlers.py\n--- a/b/handlers.py\n+++ b/b/handlers.py\n"
            "@@ -1,0 +1,1 @@\n+two\n"
        )
        findings = rt.parse_findings(
            {"findings": [{"severity": "Nit", "location": "handlers.py:1", "problem": "p"}]}
        )
        (only,) = rt.validate_anchors(findings, diff)
        assert (only.state, only.reason) == (AnchorState.UNANCHORED, "ambiguous_path")

    def test_a_unique_suffix_match_resolves_to_the_diffs_own_spelling(self):
        findings = rt.parse_findings(
            {"findings": [{"severity": "Nit", "location": "app.py:11", "problem": "p"}]}
        )
        (only,) = rt.validate_anchors(findings, DIFF)
        assert only.state is AnchorState.ANCHORED
        assert only.resolved_path == "src/app.py"

    def test_no_diff_at_all_anchors_nothing(self):
        anchored = rt.validate_anchors(_five_findings(), "")
        assert {a.reason for a in anchored} == {"empty_diff"}
        assert not any(a.anchored for a in anchored)

    def test_stat_preamble_and_no_newline_markers_do_not_derail_the_parser(self):
        diff = (
            " src/app.py | 2 +-\n 1 file changed\n"
            "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
            "@@ -1,1 +1,2 @@\n one\n+two\n\\ No newline at end of file\n"
        )
        table = rt.parse_diff_lines(diff)
        assert table["src/app.py"] == {1: "one", 2: "two"}


# ── clause: the user accepts 2 of 5 ──────────────────────────────────────────


def _accept_two_reject_three(anchored):
    """The SC9 decision set: accept the two most severe anchored findings, reject the rest."""
    keys = [a.finding.key for a in anchored]
    return [
        TriageDecision(keys[0], TriageOutcome.ACCEPT),
        TriageDecision(keys[1], TriageOutcome.ACCEPT),
        TriageDecision(keys[2], TriageOutcome.REJECT, "style preference, not a defect"),
        TriageDecision(keys[3], TriageOutcome.REJECT, "no such line in my diff"),
        TriageDecision(keys[4], TriageOutcome.REJECT, "already fixed"),
    ]


class TestTheUserAcceptsTwoOfFive:
    def test_two_accepted_three_rejected(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(anchored, _accept_two_reject_three(anchored))
        assert len(result.accepted) == 2
        assert len(result.rejected) == 3
        assert result.refused == [] and result.untriaged == []

    def test_an_undecided_finding_is_untriaged_not_accepted(self):
        """Silence is not consent."""
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(anchored, [])
        assert result.accepted == []
        assert len(result.untriaged) == 5

    def test_accepting_an_unanchored_finding_is_refused(self):
        """Acceptance means "apply this here" and there is no here."""
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        stale = anchored[4]
        result = rt.triage(anchored, [TriageDecision(stale.finding.key, TriageOutcome.ACCEPT)])
        assert result.accepted == []
        assert [a.finding.key for a, _ in result.refused] == [stale.finding.key]
        assert result.refused[0][1] == "content_moved"

    def test_a_decision_for_an_unknown_key_is_ignored(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(anchored, [TriageDecision("deadbeefdeadbeef", TriageOutcome.ACCEPT)])
        assert result.accepted == []
        assert len(result.untriaged) == 5


# ── clause: nothing was auto-written without acceptance ──────────────────────


class TestNothingIsWrittenWithoutAcceptance:
    """The load-bearing negative. Every leg spies on the ONE write path.

    The vacuity assertion is :meth:`test_the_accepted_pair_does_reach_the_write_path` — the same
    function, the same spy, a call that DOES happen. Without it, an assertion that the spy stayed
    empty could be passing because the wiring is broken rather than because consent is enforced.
    """

    def test_a_full_rejection_never_calls_the_delivery_seam(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        keys = [a.finding.key for a in anchored]
        result = rt.triage(anchored, [TriageDecision(k, TriageOutcome.REJECT, "no") for k in keys])
        spy = _Spy()
        receipt = rt.dispatch_accepted(result, deliver=spy, target=RUN)
        assert spy.calls == [], "the write path was reached with nothing accepted"
        assert receipt.delivered is False
        assert receipt.reason == "nothing_accepted"

    def test_an_untriaged_set_never_calls_the_delivery_seam(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        spy = _Spy()
        receipt = rt.dispatch_accepted(rt.triage(anchored, []), deliver=spy, target=RUN)
        assert spy.calls == []
        assert receipt.reason == "nothing_accepted"

    def test_an_accepted_but_unanchored_finding_never_calls_the_delivery_seam(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(
            anchored,
            [
                TriageDecision(a.finding.key, TriageOutcome.ACCEPT)
                for a in anchored
                if not a.anchored
            ],
        )
        spy = _Spy()
        receipt = rt.dispatch_accepted(result, deliver=spy, target=RUN)
        assert spy.calls == []
        assert receipt.reason == "nothing_accepted"

    def test_the_accepted_pair_does_reach_the_write_path(self):
        """VACUITY LEG: the same function and the same spy, with a call that must happen."""
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(anchored, _accept_two_reject_three(anchored))
        spy = _Spy()
        receipt = rt.dispatch_accepted(result, deliver=spy, target=RUN)
        assert len(spy.calls) == 1, "the accepted pair did not reach the write path"
        assert receipt.delivered is True
        assert receipt.count == 2

    def test_no_rejected_finding_appears_in_what_was_dispatched(self):
        """The positive path must not smuggle the rejections along with the accepts."""
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(anchored, _accept_two_reject_three(anchored))
        spy = _Spy()
        rt.dispatch_accepted(result, deliver=spy, target=RUN)
        _, brief = spy.calls[0]
        for item, _ in result.rejected:
            assert item.finding.problem not in brief, item.finding.problem
        for item in result.accepted:
            assert item.finding.problem in brief

    def test_the_brief_cites_the_resolved_anchor_not_the_claimed_one(self):
        findings = rt.parse_findings(
            {"findings": [{"severity": "Nit", "location": "app.py:11", "problem": "p"}]},
            run_id=RUN,
            node_id=NODE,
        )
        anchored = rt.validate_anchors(findings, DIFF)
        result = rt.triage(
            anchored, [TriageDecision(anchored[0].finding.key, TriageOutcome.ACCEPT)]
        )
        spy = _Spy()
        rt.dispatch_accepted(result, deliver=spy, target=RUN)
        assert "src/app.py:11" in spy.calls[0][1]

    def test_auto_fixable_batching_reads_the_accepted_list_not_the_findings(self):
        """`auto_fixable: true` on a REJECTED finding buys it nothing."""
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        keys = [a.finding.key for a in anchored]
        # Reject BOTH auto_fixable findings (index 1 Minor, index 2 Nit); accept the Critical one.
        result = rt.triage(
            anchored,
            [
                TriageDecision(keys[0], TriageOutcome.ACCEPT),
                TriageDecision(keys[1], TriageOutcome.REJECT, "no"),
                TriageDecision(keys[2], TriageOutcome.REJECT, "no"),
            ],
        )
        assert rt.auto_apply_candidates(result) == []
        # VACUITY: accepting the same two puts them on the mechanical list.
        result2 = rt.triage(
            anchored,
            [
                TriageDecision(keys[1], TriageOutcome.ACCEPT),
                TriageDecision(keys[2], TriageOutcome.ACCEPT),
            ],
        )
        assert {a.finding.key for a in rt.auto_apply_candidates(result2)} == {keys[1], keys[2]}

    def test_a_critical_accept_is_not_mechanically_appliable(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        critical = anchored[0]
        critical.finding.auto_fixable = True
        result = rt.triage(anchored, [TriageDecision(critical.finding.key, TriageOutcome.ACCEPT)])
        assert result.accepted and rt.auto_apply_candidates(result) == []

    def test_an_off_ladder_severity_is_not_mechanically_appliable(self):
        """`severity_rank` sorts an unknown last, which would clear every ceiling."""
        findings = rt.parse_findings(
            {
                "findings": [
                    {
                        "severity": "Blocker",
                        "location": "src/app.py:11",
                        "problem": "p",
                        "auto_fixable": True,
                    }
                ]
            },
            run_id=RUN,
        )
        anchored = rt.validate_anchors(findings, DIFF)
        result = rt.triage(
            anchored, [TriageDecision(anchored[0].finding.key, TriageOutcome.ACCEPT)]
        )
        assert result.accepted and rt.auto_apply_candidates(result) == []

    def test_a_missing_origin_worker_is_a_no_send_not_a_broadcast(self):
        findings = rt.parse_findings(
            {"findings": [{"severity": "Nit", "location": "src/app.py:11", "problem": "p"}]}
        )
        anchored = rt.validate_anchors(findings, DIFF)
        result = rt.triage(
            anchored, [TriageDecision(anchored[0].finding.key, TriageOutcome.ACCEPT)]
        )
        spy = _Spy()
        receipt = rt.dispatch_accepted(result, deliver=spy, target="")
        assert spy.calls == []
        assert receipt.reason == "no_origin_worker"

    def test_a_refusing_seam_is_reported_as_undelivered(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(anchored, _accept_two_reject_three(anchored))
        spy = _Spy(ok=False)
        receipt = rt.dispatch_accepted(result, deliver=spy, target=RUN)
        assert len(spy.calls) == 1
        assert receipt.delivered is False
        assert receipt.reason == "delivery_refused"


# ── clause: rejected findings land in the calibration record ─────────────────


class TestRejectionsLandInTheCalibrationRecord:
    def test_one_divergence_row_per_rejection_and_none_for_an_accept(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(anchored, _accept_two_reject_three(anchored))
        rows = rt.calibration_records(result, template="code-implementation")
        assert len(rows) == 3
        assert {r["finding_key"] for r in rows} == {a.finding.key for a, _ in result.rejected}
        accepted_keys = {a.finding.key for a in result.accepted}
        assert not accepted_keys & {r["finding_key"] for r in rows}

    def test_the_direction_reads_false_reject_so_a_fake_gate_is_detectable(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(anchored, _accept_two_reject_three(anchored))
        rows = rt.calibration_records(result, template="t")
        assert {r["direction"] for r in rows} == {"false_reject"}
        assert {r["judge_verdict"] for r in rows} == {"REJECT"}
        assert {r["human_verdict"] for r in rows} == {"PASS"}

    def test_the_rejection_reason_is_kept_verbatim(self):
        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(anchored, _accept_two_reject_three(anchored))
        rows = rt.calibration_records(result, template="t")
        assert "style preference, not a defect" in {r["reason"] for r in rows}

    def test_the_row_is_the_shape_the_existing_detector_reads(self):
        """Reusing `DivergenceRecord` is the point — a second dialect would be invisible to it."""
        from personalclaw.workflows import judge_calibration

        anchored = rt.validate_anchors(_five_findings(), DIFF)
        result = rt.triage(anchored, _accept_two_reject_three(anchored))
        rows = rt.calibration_records(result, template="code-implementation")
        entries = [{"kind": "judge_divergence", **r} for r in rows]
        parsed = judge_calibration.divergences_from_journal(entries)
        assert len(parsed) == 3
        assert {p.direction for p in parsed} == {"false_reject"}
        assert {p.template for p in parsed} == {"code-implementation"}

    def test_the_kind_is_in_the_ledger_vocabulary_so_the_panel_can_read_it_back(self):
        from personalclaw.workflows import journal as journal_mod

        assert journal_mod.REVIEW_FINDING == "review_finding"
        assert journal_mod.REVIEW_FINDING in journal_mod.LEDGER_KINDS


# ── the engine emit: a review stage's output becomes ledger rows ─────────────


class TestAReviewStageEmitsLineAnchoredFindings:
    """Asserts the CALL SITE in `RunController`, not the primitive in isolation.

    The primitive parsing correctly proves nothing about whether the engine ever calls it — a
    review stage whose findings never reach the ledger leaves the panel permanently empty, and
    every test above would still be green.
    """

    def test_the_controller_settle_path_emits_one_row_per_finding(self, tmp_path, monkeypatch):
        from personalclaw.workflows import journal as journal_mod
        from personalclaw.workflows import store
        from personalclaw.workflows.controller import EngineServices, RunController
        from personalclaw.workflows.models import WorkflowRun

        monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
        run = store.create(WorkflowRun(id="", workflow_name="audit-sweep"))
        spec = {
            "name": "audit-sweep",
            "root": {
                "kind": "transform",
                "id": "reviewer",
                "config": {
                    "expr": json.dumps({"findings": [f.to_dict() for f in _five_findings()]})
                },
            },
        }
        store.write_spec(run.id, spec)
        controller = RunController(run, spec, services=EngineServices())
        import asyncio

        asyncio.run(controller.run_to_completion(timeout=30))
        rows = journal_mod.ledger(run.id, kinds={journal_mod.REVIEW_FINDING})
        assert len(rows) == 5, "the review stage's findings never reached the ledger"
        assert {r["severity"] for r in rows} == {"Critical", "Major", "Minor", "Nit"}
        assert all(r["origin_run_id"] == run.id for r in rows)
        assert all(r["node_id"] == "reviewer" for r in rows)

    def test_a_stage_with_no_findings_emits_nothing(self, tmp_path, monkeypatch):
        """VACUITY: the emit above is conditional, so the absence case must be checked too."""
        from personalclaw.workflows import journal as journal_mod
        from personalclaw.workflows import store
        from personalclaw.workflows.controller import EngineServices, RunController
        from personalclaw.workflows.models import WorkflowRun

        monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
        run = store.create(WorkflowRun(id="", workflow_name="plain"))
        spec = {
            "name": "plain",
            "root": {"kind": "transform", "id": "writer", "config": {"expr": "just prose"}},
        }
        store.write_spec(run.id, spec)
        import asyncio

        asyncio.run(
            RunController(run, spec, services=EngineServices()).run_to_completion(timeout=30)
        )
        assert journal_mod.ledger(run.id, kinds={journal_mod.REVIEW_FINDING}) == []


# ── the run-scoped service: diff read, TOCTOU re-anchor, dispatch, calibrate ─


@pytest.fixture()
def wf_home(tmp_path, monkeypatch):
    """An isolated run store. Never the real `~/.personalclaw`."""
    from personalclaw.workflows import store

    home = tmp_path / "wfhome"
    home.mkdir()
    monkeypatch.setattr(store, "config_dir", lambda: home)
    assert store.config_dir() == home
    return home


def _seed_run(findings, *, name="code-implementation"):
    from personalclaw.workflows import journal as journal_mod
    from personalclaw.workflows import store
    from personalclaw.workflows.models import WorkflowRun

    run = store.create(WorkflowRun(id="", workflow_name=name))
    journal = journal_mod.Journal(run.id)
    for f in findings:
        journal.write(
            journal_mod.REVIEW_FINDING,
            node_id=f.origin_node_id,
            template=name,
            **{**f.to_dict(), "origin_run_id": run.id},
        )
    return run


class TestTheRunScopedService:
    def test_findings_round_trip_through_the_ledger(self, wf_home):
        from personalclaw.workflows import review_service

        run = _seed_run(_five_findings())
        back = review_service.findings_for(run.id)
        assert [f.location for f in back] == [f.location for f in _five_findings()]
        assert back[1].auto_fixable is True

    def test_the_get_anchors_against_the_live_diff(self, wf_home, monkeypatch):
        import asyncio

        from personalclaw.workflows import review_service

        run = _seed_run(_five_findings())

        async def fake_diff(_run):
            return "/ws", DIFF, False

        monkeypatch.setattr(review_service, "workspace_diff", fake_diff)
        payload = asyncio.run(review_service.review_findings(run.id))
        assert payload["ok"] is True
        assert payload["counts"] == {"total": 5, "anchored": 3, "unanchored": 2}

    def test_an_accept_that_went_stale_between_render_and_post_is_refused(
        self, wf_home, monkeypatch
    ):
        """The TOCTOU leg: the panel showed an anchored finding, the worker moved the line."""
        import asyncio

        from personalclaw.workflows import review_service

        run = _seed_run(_five_findings())
        rendered = rt.validate_anchors(review_service.findings_for(run.id), DIFF)
        accept = TriageDecision(rendered[0].finding.key, TriageOutcome.ACCEPT)
        assert rendered[0].anchored, "precondition: the panel showed this as anchored"

        moved = DIFF.replace("+    if not token:", "+    if token is None:")

        async def fake_diff(_run):
            return "/ws", moved, False

        monkeypatch.setattr(review_service, "workspace_diff", fake_diff)
        sent: list[str] = []
        monkeypatch.setattr(
            review_service.service,
            "steer_run",
            lambda rid, text: sent.append(text) or {"ok": True},
        )
        out = asyncio.run(
            review_service.apply_triage(run.id, [{"key": accept.key, "outcome": "accept"}])
        )
        assert sent == [], "a stale accept reached the worker"
        assert out["receipt"]["reason"] == "nothing_accepted"
        assert [r["anchor_reason"] for r in out["refused"]] == ["content_moved"]

    def test_the_accepted_pair_is_steered_and_the_rejections_are_journaled(
        self, wf_home, monkeypatch
    ):
        import asyncio

        from personalclaw.workflows import journal as journal_mod
        from personalclaw.workflows import review_service

        run = _seed_run(_five_findings())
        anchored = rt.validate_anchors(review_service.findings_for(run.id), DIFF)

        async def fake_diff(_run):
            return "/ws", DIFF, False

        monkeypatch.setattr(review_service, "workspace_diff", fake_diff)
        sent: list[tuple[str, str]] = []

        def fake_steer(rid, text):
            sent.append((rid, text))
            return {"ok": True, "queued": 1}

        monkeypatch.setattr(review_service.service, "steer_run", fake_steer)
        out = asyncio.run(
            review_service.apply_triage(
                run.id,
                [
                    {"key": d.key, "outcome": d.outcome.value, "reason": d.reason}
                    for d in _accept_two_reject_three(anchored)
                ],
            )
        )
        assert out["receipt"]["delivered"] is True
        assert [rid for rid, _ in sent] == [run.id]
        assert out["calibrated"] == 3
        rows = journal_mod.ledger(run.id, kinds={journal_mod.JUDGE_DIVERGENCE})
        assert len(rows) == 3
        assert {r["source"] for r in rows} == {"review_triage"}

    def test_a_dry_run_delivers_nothing_and_journals_nothing(self, wf_home, monkeypatch):
        import asyncio

        from personalclaw.workflows import journal as journal_mod
        from personalclaw.workflows import review_service

        run = _seed_run(_five_findings())
        anchored = rt.validate_anchors(review_service.findings_for(run.id), DIFF)

        async def fake_diff(_run):
            return "/ws", DIFF, False

        monkeypatch.setattr(review_service, "workspace_diff", fake_diff)
        called: list[str] = []
        monkeypatch.setattr(
            review_service.service,
            "steer_run",
            lambda rid, text: called.append(rid) or {"ok": True},
        )
        out = asyncio.run(
            review_service.apply_triage(
                run.id,
                [
                    {"key": d.key, "outcome": d.outcome.value, "reason": d.reason}
                    for d in _accept_two_reject_three(anchored)
                ],
                dispatch=False,
            )
        )
        assert called == []
        assert out["dry_run"] is True
        assert len(out["accepted"]) == 2 and out["brief"]
        assert journal_mod.ledger(run.id, kinds={journal_mod.JUDGE_DIVERGENCE}) == []

    def test_an_unreadable_outcome_is_an_error_not_a_default(self, wf_home):
        import asyncio

        from personalclaw.workflows import review_service

        run = _seed_run(_five_findings())
        out = asyncio.run(review_service.apply_triage(run.id, [{"key": "abc", "outcome": "maybe"}]))
        assert out["ok"] is False
        assert out["code"] == "WF_TRIAGE_BAD_DECISIONS"

    def test_an_unknown_run_is_a_typed_failure(self, wf_home):
        import asyncio

        from personalclaw.workflows import review_service

        assert asyncio.run(review_service.review_findings("nope"))["code"] == "WF_RUN_NOT_FOUND"

    def test_a_terminal_run_parks_the_brief_instead_of_starting_one_unasked(
        self, wf_home, monkeypatch
    ):
        import asyncio

        from personalclaw.workflows import review_service, store

        run = _seed_run(_five_findings())
        anchored = rt.validate_anchors(review_service.findings_for(run.id), DIFF)

        async def fake_diff(_run):
            return "/ws", DIFF, False

        monkeypatch.setattr(review_service, "workspace_diff", fake_diff)
        monkeypatch.setattr(
            review_service.service,
            "steer_run",
            lambda rid, text: {"ok": False, "code": "WF_RUN_ALREADY_TERMINAL"},
        )
        out = asyncio.run(
            review_service.apply_triage(
                run.id,
                [
                    {"key": d.key, "outcome": d.outcome.value, "reason": d.reason}
                    for d in _accept_two_reject_three(anchored)
                ],
            )
        )
        assert out["receipt"]["delivered"] is False
        assert out["receipt"]["reason"] == "handoff_parked"
        assert store.get(run.id).extra["review_handoff_brief"]


class TestTheEndpointsAreRegistered:
    """A service function nothing routes to is a feature the user cannot reach."""

    def test_both_review_routes_are_mounted(self):
        from aiohttp import web

        from personalclaw.workflows.handlers import register_workflow_routes

        app = web.Application()
        register_workflow_routes(app)
        mounted = {
            (r.method, r.resource.canonical) for r in app.router.routes() if r.resource is not None
        }
        assert ("GET", "/api/workflows/runs/{run_id}/review") in mounted
        assert ("POST", "/api/workflows/runs/{run_id}/review/triage") in mounted
