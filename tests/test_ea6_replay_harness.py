"""EA-6 — the local A/B replay harness's honesty rails.

Six clauses in the atom, and each one names a specific way a measurement surface lies. The
tests below are organized by clause rather than by function, because the risk here is not that
an arithmetic helper returns the wrong float — it is that a card tells a reviewer something
nobody measured, or that evidence quietly becomes a veto.

Every positive leg has a VACUITY PARTNER that stays green under the mutation that reddens its
sibling. The pairs are named in each class docstring: a suite where the mutated and unmutated
runs report the same count has proved nothing about which line it was reading.
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest

from personalclaw.learning import proposals as proposals_mod
from personalclaw.learning import replay as replay_mod
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

# ── scripted halves of the real composition ─────────────────────────────────


class ScriptedProvider:
    """A zero-cost provider that returns scripted text. Drives the REAL ``LLMJudge``.

    Deliberately not a mock of ``judge_turn``: the atom's composition clause is about
    ``one_shot_completion`` + ``LLMJudge`` working together, so the judge under test is the
    shipped class, streaming through the shipped event protocol and running its own JSON
    parser on the result. A test that stubbed ``judge_turn`` would leave the parse-failure
    contract — the thing clause 2 is about — completely unexercised.
    """

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._idx = 0
        self.prompts: list[str] = []

    async def start(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    async def stream(self, message: str):
        self.prompts.append(message)
        text = self._responses[self._idx] if self._idx < len(self._responses) else "{}"
        self._idx += 1
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
        yield LLMEvent(kind=EVENT_COMPLETE)

    async def approve_tool(self, request_id):
        pass

    async def reject_tool(self, request_id):
        pass

    def context_usage_pct(self) -> float:
        return 0.0


def judge_scoring(*scores: float, reason: str = "ok"):
    """A real ``LLMJudge`` whose provider returns these scores in order.

    ``prompt_template`` is left at ``None`` so the judge renders through the ``eval_judge``
    prompt binding the atom names — the shipped path, not a test-local string.
    """
    from personalclaw.eval.judge import LLMJudge

    payloads = [json.dumps({"score": s, "reason": reason}) for s in scores]
    provider = ScriptedProvider(payloads)
    seen: list[str] = []

    def factory(session_key: str):
        seen.append(session_key)
        return provider

    judge = LLMJudge(factory)
    judge._test_session_keys = seen  # noqa: SLF001 — read by the binding test below
    judge._test_provider = provider  # noqa: SLF001
    return judge


def completion_returning(*texts: str):
    """A stand-in for ``one_shot_completion`` with the same signature and call shape."""
    calls: list[dict] = []

    async def _completion(prompt: str, *, use_case: str = "background", **kw):
        calls.append({"prompt": prompt, "use_case": use_case})
        idx = len(calls) - 1
        return texts[idx] if idx < len(texts) else "an answer"

    _completion.calls = calls  # type: ignore[attr-defined]
    return _completion


def a_case(session: str = "sess-a", rhash: str = "hash1", *, tool_free: bool = True):
    return replay_mod.ReplayCase(
        session_id=session,
        record_hash=rhash,
        prompt="<untrusted_content>\nrefactor the retry helper\n</untrusted_content>",
        tool_free=tool_free,
        captured_at=1.0,
    )


def a_proposal(pid: str = "skill-ea6test01", kind: str = "skill"):
    return proposals_mod.Proposal(
        id=pid,
        kind=kind,
        title="Promote the retry-helper checklist",
        body="When editing the retry helper, always widen the test first.",
        provenance="inferred",
        # `evidence_refs` is what `Row.bulk_acceptable` requires ("nothing to check" otherwise),
        # so the seam test can assert the measurement changed no eligibility.
        evidence_refs=["run-1"],
        change_manifest={"predicted_fixes": ["stops the flaky retry regression"]},
    )


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def cfg_file(tmp_path, monkeypatch):
    """Redirect ``config_path()`` at a temp file — never the operator's real home."""
    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: path)
    return path


# ── clause: NOT a gate ───────────────────────────────────────────────────────


class TestItIsNotAGate:
    """The load-bearing clause. A ``regressed`` verdict is a sentence, not a veto.

    Vacuity partner for `test_a_regressed_verdict_still_accepts`:
    `test_accept_is_still_gated_on_the_human`. Both call `accept`; only the first would go
    green if `accept` grew a replay check, and only the second would go green if the human
    gate were deleted. A single test could not tell those two failures apart.
    """

    def test_a_regressed_verdict_still_accepts(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        prop = a_proposal()
        proposals_mod._save(prop)  # noqa: SLF001
        report = replay_mod.ReplayReport(
            state=replay_mod.REPLAY_REPLAYED,
            verdict=replay_mod.VERDICT_REGRESSED,
            candidate_mean=1.0,
            baseline_mean=4.0,
        )
        assert proposals_mod.attach_replay(prop.id, report.to_dict()) is True
        reloaded = proposals_mod.get(prop.id)
        assert reloaded.replay["verdict"] == "regressed"

        installed: list[str] = []
        accepted = proposals_mod.accept(prop.id, installer=lambda p: installed.append(p.id))
        assert accepted.status == "accepted"
        assert installed == [prop.id], "a regressed replay must not stop the install"

    def test_accept_is_still_gated_on_the_human(self, tmp_path, monkeypatch):
        """The vacuity partner: `accept` refuses an AGENT even with an IMPROVED verdict.

        Without this, `test_a_regressed_verdict_still_accepts` passing would be consistent
        with `accept` having no gate at all — "nothing blocks" is not the claim; the claim is
        "the REPLAY does not block, and the human gate still does".
        """
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        prop = a_proposal(pid="skill-ea6test02")
        proposals_mod._save(prop)  # noqa: SLF001
        proposals_mod.attach_replay(
            prop.id,
            replay_mod.ReplayReport(
                state=replay_mod.REPLAY_REPLAYED,
                verdict=replay_mod.VERDICT_IMPROVED,
                candidate_mean=4.5,
                baseline_mean=2.0,
            ).to_dict(),
        )
        with pytest.raises(proposals_mod.AcceptError):
            proposals_mod.accept(prop.id, installer=lambda p: None, actor="agent")

    def test_attach_moves_neither_status_nor_timestamp(self, tmp_path, monkeypatch):
        """A measurement is not a decision. Same contract as ``attach_gate``."""
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        prop = a_proposal(pid="skill-ea6test03")
        prop.updated_at = "2026-01-01T00:00:00+00:00"
        proposals_mod._save(prop)  # noqa: SLF001
        proposals_mod.attach_replay(prop.id, {"state": "replayed", "verdict": "improved"})
        after = proposals_mod.get(prop.id)
        assert after.status == "pending"
        assert after.updated_at == "2026-01-01T00:00:00+00:00"

    def test_the_replay_field_is_absent_from_the_bulk_acceptability_rule(self):
        """A regressed replay must not silently remove a row from bulk accept either.

        The card is allowed to shout; the CONTROL is not allowed to change. `bulk_acceptable`
        consults four conditions and `replay` is deliberately none of them — a veto smuggled
        in through a UI eligibility flag would be exactly as semantic a change as one in
        `accept`, and harder to notice.
        """
        from personalclaw.learning.inbox import Row

        regressed = Row(
            id="r1",
            kind="skill",
            title="t",
            provenance="inferred",
            evidence_refs=["run-1"],
            risk_tier="low",
            replay={"state": "replayed", "verdict": "regressed"},
        )
        clean = Row(
            id="r2",
            kind="skill",
            title="t",
            provenance="inferred",
            evidence_refs=["run-1"],
            risk_tier="low",
            replay={"state": "replayed", "verdict": "improved"},
        )
        assert regressed.bulk_acceptable is True
        assert regressed.bulk_acceptable == clean.bulk_acceptable


# ── clause: an empty scored set publishes NO number ─────────────────────────


class TestAnEmptySetPublishesNoNumber:
    """`0.0` and "nothing was scored" must reach the card as DIFFERENT values.

    Vacuity partner for `test_an_empty_case_set_has_null_means`:
    `test_a_measured_zero_publishes_zero`. The two are asserted to DISAGREE in
    `test_the_two_disagree`, which is the only leg that actually catches `_mean` returning
    `0.0` for an empty set — each of the first two alone would still pass if the other's
    value were substituted.
    """

    def test_mean_of_an_empty_set_is_none(self):
        assert replay_mod._mean([]) is None  # noqa: SLF001

    def test_a_measured_zero_is_a_real_mean(self):
        assert replay_mod._mean([0.0, 0.0]) == 0.0  # noqa: SLF001

    def test_the_two_disagree(self):
        """The discriminating leg. If `_mean([])` ever returns 0.0 this is the red."""
        assert replay_mod._mean([]) != replay_mod._mean([0.0])  # noqa: SLF001

    def test_an_empty_case_set_has_null_means_and_an_unmeasured_verdict(self):
        report = replay_mod.finalize(
            replay_mod.ReplayReport(state=replay_mod.REPLAY_REPLAYED, reason="")
        )
        assert report.candidate_mean is None
        assert report.baseline_mean is None
        assert report.verdict == replay_mod.VERDICT_UNMEASURED
        assert "no candidate/baseline pair" in report.reason

    def test_all_cases_rejected_scores_nothing(self):
        report = replay_mod.ReplayReport(state=replay_mod.REPLAY_REPLAYED, reason="")
        report.cases = [
            replay_mod.CaseScore(
                provenance="capture:s#h", session_id="s", record_hash="h", rejected=True
            )
        ]
        out = replay_mod.finalize(report)
        assert out.candidate_mean is None and out.baseline_mean is None
        assert out.verdict == replay_mod.VERDICT_UNMEASURED
        assert "1 rejected by the judge" in out.reason

    def test_a_genuinely_zero_candidate_still_publishes_zero(self):
        """The partner that keeps the rail honest in the other direction.

        A candidate the judge scored 0 on every case is the STRONGEST possible evidence that
        it made things worse. Suppressing that as "unmeasured" would hide the one verdict a
        reviewer most needs.
        """
        report = replay_mod.ReplayReport(state=replay_mod.REPLAY_REPLAYED, reason="")
        report.cases = [
            replay_mod.CaseScore(
                provenance="capture:s#h",
                session_id="s",
                record_hash="h",
                baseline=4.0,
                candidate=0.0,
            )
        ]
        out = replay_mod.finalize(report)
        assert out.candidate_mean == 0.0
        assert out.verdict == replay_mod.VERDICT_REGRESSED

    def test_summary_passes_a_null_mean_through_as_null(self):
        """The last hop before the screen. Coercing here would undo `_mean` entirely."""
        projected = replay_mod.summary(
            {"state": "replayed", "verdict": "unmeasured", "candidate_mean": None, "cases": []}
        )
        assert projected["candidate_mean"] is None
        measured = replay_mod.summary(
            {"state": "replayed", "verdict": "regressed", "candidate_mean": 0.0, "cases": []}
        )
        assert measured["candidate_mean"] == 0.0
        assert projected["candidate_mean"] != measured["candidate_mean"]

    def test_an_absent_report_is_unreplayed_with_a_reason(self):
        for absent in (None, {}):
            projected = replay_mod.summary(absent)
            assert projected["state"] == "unreplayed"
            assert projected["reason"] == replay_mod.UNREPLAYED_NOT_RUN
            assert projected["candidate_mean"] is None
            assert projected["verdict"] == "unmeasured"

    def test_an_unknown_verdict_reads_as_unmeasured(self):
        assert replay_mod.summary({"state": "replayed", "verdict": "wonderful"})["verdict"] == (
            "unmeasured"
        )

    def test_classify_refuses_to_call_a_missing_mean_neutral(self):
        assert replay_mod.classify(None, 3.0) == replay_mod.VERDICT_UNMEASURED
        assert replay_mod.classify(3.0, None) == replay_mod.VERDICT_UNMEASURED
        # The partner: an equal PAIR really is neutral, so `unmeasured` above is about the
        # absence and not about the comparison always answering `unmeasured`.
        assert replay_mod.classify(3.0, 3.0) == replay_mod.VERDICT_NEUTRAL


# ── clause: parse-failure → 0 REJECT ────────────────────────────────────────


class TestParseFailureRejects:
    """An unparseable judge response scores 0 and is REJECTED — not skipped, not passed.

    Vacuity partner for `test_a_parse_failure_rejects_the_case`:
    `test_a_genuine_zero_score_is_kept`. Both drive the same real `LLMJudge`; only the
    `reason` prefix distinguishes them, which is the discriminator `_is_parse_failure` reads.
    """

    def test_the_real_judge_signals_a_parse_failure_the_way_we_read_it(self):
        """Pins the CONTRACT this module depends on, in the shipped class.

        If `LLMJudge` ever stopped prefixing `parse_error:`, `_is_parse_failure` would go
        silently inert and every unparseable case would be counted as a scored zero. This is
        the test that turns that into a red.
        """
        from personalclaw.eval.judge import LLMJudge

        judge = LLMJudge(lambda _k: ScriptedProvider(["not json at all"]))
        run(judge.start())
        verdict = run(judge.judge_turn("d", "c", "u", "a"))
        assert verdict.score == 0
        assert verdict.reason.startswith("parse_error")
        assert replay_mod._is_parse_failure(verdict) is True  # noqa: SLF001

    def test_a_genuine_zero_is_not_a_parse_failure(self):
        from personalclaw.eval.judge import LLMJudge

        judge = LLMJudge(lambda _k: ScriptedProvider([json.dumps({"score": 0, "reason": "wrong"})]))
        run(judge.start())
        verdict = run(judge.judge_turn("d", "c", "u", "a"))
        assert verdict.score == 0
        assert replay_mod._is_parse_failure(verdict) is False  # noqa: SLF001

    def test_a_parse_failure_rejects_the_case(self):
        """End to end through the real composition: unparseable judge → rejected case."""
        from personalclaw.eval.judge import LLMJudge

        judge = LLMJudge(lambda _k: ScriptedProvider(["garbage", "garbage"]))
        run(judge.start())
        score = run(
            replay_mod._run_case(  # noqa: SLF001
                a_case(),
                prop=a_proposal(),
                criteria="c",
                judge=judge,
                completion=completion_returning("base answer", "cand answer"),
            )
        )
        assert score.rejected is True
        assert score.baseline is None and score.candidate is None
        assert "could not be parsed" in score.reason

    def test_a_genuine_zero_score_is_kept(self):
        """The vacuity partner. Same path, parseable judge, score 0 → SCORED, not rejected."""
        judge = judge_scoring(0.0, 0.0, reason="the response was wrong")
        run(judge.start())
        score = run(
            replay_mod._run_case(  # noqa: SLF001
                a_case(),
                prop=a_proposal(),
                criteria="c",
                judge=judge,
                completion=completion_returning("base answer", "cand answer"),
            )
        )
        assert score.rejected is False
        assert score.scored is True
        assert score.baseline == 0.0 and score.candidate == 0.0

    def test_an_empty_arm_rejects_rather_than_scoring_zero(self):
        judge = judge_scoring(4.0, 4.0)
        run(judge.start())
        score = run(
            replay_mod._run_case(  # noqa: SLF001
                a_case(),
                prop=a_proposal(),
                criteria="c",
                judge=judge,
                completion=completion_returning("a real answer", "   "),
            )
        )
        assert score.rejected is True
        assert "empty completion" in score.reason


# ── clause: composes directly, NEVER eval/runner.py ─────────────────────────


class TestNeverTheEvalRunner:
    """The atom's explicit hazard: ``eval/runner.py`` spawns a child with an env override.

    Vacuity partner for `test_the_replay_path_never_imports_the_eval_runner`:
    `test_the_guard_would_have_caught_an_import`. The guard is an import hook, and an import
    hook that is never consulted is a rail that reports a pass it could not reach — so the
    partner proves the hook fires on a function that DOES import the runner.
    """

    @staticmethod
    def _guard():
        """A meta_path finder that raises the moment ``evals.runner`` is imported."""
        forbidden = "personalclaw.evals.runner"

        class Guard:
            hit = False

            def find_module(self, name, path=None):  # pragma: no cover - legacy protocol
                return None

            def find_spec(self, name, path=None, target=None):
                if name == forbidden:
                    Guard.hit = True
                    raise AssertionError(f"EA-6 imported {forbidden} — the env-mutation hazard")
                return None

        return Guard, forbidden

    def test_the_replay_path_never_imports_the_eval_runner(self):
        Guard, forbidden = self._guard()
        # Evict it so the import machinery actually consults meta_path. Without this the
        # guard is unreachable whenever an earlier test in the session imported the runner,
        # and the assertion below would pass for the wrong reason.
        cached = sys.modules.pop(forbidden, None)
        sys.meta_path.insert(0, Guard())
        try:
            judge = judge_scoring(4.0, 4.5)
            report = run(
                replay_mod.replay_proposal(
                    a_proposal(),
                    [a_case()],
                    completion=completion_returning("base", "cand"),
                    judge_factory=lambda: judge,
                    budget_dollars=1.0,
                )
            )
            assert report.state == replay_mod.REPLAY_REPLAYED
        finally:
            sys.meta_path = [f for f in sys.meta_path if not isinstance(f, Guard)]
            if cached is not None:
                sys.modules[forbidden] = cached
        assert Guard.hit is False

    def test_the_guard_would_have_caught_an_import(self):
        """The vacuity partner: the hook is reachable and it does fire."""
        Guard, forbidden = self._guard()
        cached = sys.modules.pop(forbidden, None)
        sys.meta_path.insert(0, Guard())
        try:
            with pytest.raises(AssertionError, match="env-mutation hazard"):
                __import__(forbidden)
        finally:
            sys.meta_path = [f for f in sys.meta_path if not isinstance(f, Guard)]
            if cached is not None:
                sys.modules[forbidden] = cached
        assert Guard.hit is True

    @staticmethod
    def _imported_modules(module) -> set[str]:
        """Every module name this module imports, including LAZY in-function imports.

        An AST walk rather than a substring scan over the source: this module's own docstring
        names ``evals.runner`` to explain why it is avoided, and a text scan would either red
        on the prose or have to be weakened until it stopped catching a real import. The AST
        cannot be fooled by either — it sees `Import`/`ImportFrom` nodes and nothing else.
        """
        import ast
        from pathlib import Path

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                names.add(base)
                names.update(f"{base}.{alias.name}" for alias in node.names)
        return names

    def test_the_module_imports_the_runner_nowhere_not_even_lazily(self):
        """The static half — it catches a lazily-imported reference no async test reaches."""
        import personalclaw.learning.replay as mod

        imported = self._imported_modules(mod)
        assert "personalclaw.evals.runner" not in imported
        assert not any("evals" in name for name in imported), sorted(imported)

    def test_the_scan_would_have_caught_the_runner(self):
        """The vacuity partner for the static half.

        `evals.gate` DOES import the runner — deliberately, it is the right tool there. Running
        the same scan over it proves the scan can see an import at all, rather than reporting a
        pass because it looks at the wrong thing.
        """
        import personalclaw.evals.gate as gate_mod

        assert "personalclaw.evals.runner" in self._imported_modules(gate_mod)

    def test_the_module_never_names_a_home_override(self):
        """The env-mutation hazard by its other name. Prose-tolerant: only CODE is scanned."""
        import ast
        from pathlib import Path

        import personalclaw.learning.replay as mod

        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        strings = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None:
                    docstrings.add(doc)
        assert not any("PERSONALCLAW_HOME" in s for s in strings - docstrings)

    def test_it_does_compose_the_two_halves_the_atom_names(self):
        """The positive side: the arms go through a `one_shot_completion`-shaped call and the
        score comes from a real `LLMJudge` over the `eval_judge` session key."""
        judge = judge_scoring(3.0, 4.5)
        completion = completion_returning("base", "cand")
        report = run(
            replay_mod.replay_proposal(
                a_proposal(),
                [a_case()],
                completion=completion,
                judge_factory=lambda: judge,
                budget_dollars=1.0,
            )
        )
        assert [c["use_case"] for c in completion.calls] == ["background", "background"]
        assert judge._test_session_keys == ["eval_judge"]  # noqa: SLF001
        assert report.verdict == replay_mod.VERDICT_IMPROVED
        assert report.baseline_mean == 3.0 and report.candidate_mean == 4.5

    def test_the_candidate_arm_is_the_only_one_carrying_the_body(self):
        """What makes the pair an A/B at all: exactly one arm sees the candidate."""
        judge = judge_scoring(3.0, 4.0)
        completion = completion_returning("base", "cand")
        run(
            replay_mod.replay_proposal(
                a_proposal(),
                [a_case()],
                completion=completion,
                judge_factory=lambda: judge,
                budget_dollars=1.0,
            )
        )
        baseline_prompt, candidate_prompt = (c["prompt"] for c in completion.calls)
        body = "always widen the test first"
        assert body not in baseline_prompt
        assert body in candidate_prompt
        # And the candidate body is FENCED — it is unreviewed machine-authored text.
        assert "<untrusted_content" in candidate_prompt

    def test_both_arms_are_judged_on_the_same_criteria(self):
        """A rubric that moved between arms would measure the rubric."""
        judge = judge_scoring(3.0, 4.0)
        run(
            replay_mod.replay_proposal(
                a_proposal(),
                [a_case()],
                completion=completion_returning("base", "cand"),
                judge_factory=lambda: judge,
                budget_dollars=1.0,
            )
        )
        judge_prompts = judge._test_provider.prompts  # noqa: SLF001
        assert len(judge_prompts) == 2
        assert judge_prompts[0] == judge_prompts[1].replace("cand", "base")


# ── clause: budget exhaustion DEFERS with a labeled card ────────────────────


class TestTheBudgetDefers:
    """Exhaustion produces a labelled deferral, never a silent skip.

    Vacuity partner for `test_exhaustion_defers_with_a_label`:
    `test_a_sufficient_budget_does_not_defer`. Identical drive, one raises
    `BudgetExceededError` and one does not — so a `deferred` flag hard-wired to `True` or
    `False` reddens exactly one of them.
    """

    def test_an_unbudgeted_replay_does_not_run_at_all(self):
        """`Budget(max_dollars=0)` is UNLIMITED, which is what this must never be."""
        report = run(
            replay_mod.replay_proposal(
                a_proposal(), [a_case()], budget_dollars=0.0, completion=completion_returning()
            )
        )
        assert report.state == replay_mod.REPLAY_UNREPLAYED
        assert report.reason == replay_mod.UNREPLAYED_NO_BUDGET
        assert report.candidate_mean is None
        assert report.deferred is False, "no budget is a config gap, not a deferral"

    def test_exhaustion_defers_with_a_label(self):
        from personalclaw.guardrails.failure import BudgetExceededError

        async def broke(prompt, *, use_case="background", **kw):
            raise BudgetExceededError("run", "dollars", 1.0, 2.0)

        judge = judge_scoring(4.0, 4.0)
        report = run(
            replay_mod.replay_proposal(
                a_proposal(),
                [a_case()],
                completion=broke,
                judge_factory=lambda: judge,
                budget_dollars=1.0,
            )
        )
        assert report.deferred is True
        assert report.reason == replay_mod.UNREPLAYED_BUDGET_EXHAUSTED
        assert "deferred on the learning replay budget" in report.reason
        assert report.state == replay_mod.REPLAY_UNREPLAYED, "nothing scored, so not 'replayed'"
        assert report.candidate_mean is None, "a deferral must not publish a mean"

    def test_a_sufficient_budget_does_not_defer(self):
        judge = judge_scoring(4.0, 4.0)
        report = run(
            replay_mod.replay_proposal(
                a_proposal(),
                [a_case()],
                completion=completion_returning("base", "cand"),
                judge_factory=lambda: judge,
                budget_dollars=1.0,
            )
        )
        assert report.deferred is False
        assert report.state == replay_mod.REPLAY_REPLAYED

    def test_a_partial_measurement_survives_the_deferral(self):
        """Cases that already scored are real evidence and stay on the report."""
        from personalclaw.guardrails.failure import BudgetExceededError

        calls = {"n": 0}

        async def two_then_broke(prompt, *, use_case="background", **kw):
            calls["n"] += 1
            if calls["n"] > 2:
                raise BudgetExceededError("run", "dollars", 1.0, 2.0)
            return "an answer"

        judge = judge_scoring(2.0, 4.0)
        report = run(
            replay_mod.replay_proposal(
                a_proposal(),
                [a_case(), a_case(rhash="hash2")],
                completion=two_then_broke,
                judge_factory=lambda: judge,
                budget_dollars=1.0,
            )
        )
        assert report.deferred is True
        assert len(report.scored_cases) == 1
        assert report.candidate_mean == 4.0
        assert report.state == replay_mod.REPLAY_REPLAYED, "one case DID score"
        assert report.reason == replay_mod.UNREPLAYED_BUDGET_EXHAUSTED

    def test_the_ceiling_is_bound_where_the_guard_reads_it(self):
        """The bound is only real if it lands in the ContextVars ``ModelCallGuard`` reads.

        Measured, not assumed: the guard resolves the run key and the run budget from
        `budgets._CURRENT_RUN_KEY` / `_CURRENT_RUN_BUDGET` at call time, so a harness that
        merely constructed a `Budget` would have an inert ceiling — the exact shape
        `budgets`' own docstring records for `check_run`.
        """
        from personalclaw.guardrails.budgets import current_run_budget, current_run_key

        seen: list[tuple[str, float]] = []

        async def observe(prompt, *, use_case="background", **kw):
            seen.append((current_run_key(), current_run_budget().max_dollars))
            return "an answer"

        judge = judge_scoring(4.0, 4.0)
        run(
            replay_mod.replay_proposal(
                a_proposal(),
                [a_case()],
                completion=observe,
                judge_factory=lambda: judge,
                budget_dollars=2.5,
            )
        )
        assert seen == [("learning_replay", 2.5), ("learning_replay", 2.5)]

    def test_the_scope_is_released_afterwards(self):
        """A leaked run key would silently charge every later call to the replay scope."""
        from personalclaw.guardrails.budgets import current_run_key

        judge = judge_scoring(4.0, 4.0)
        run(
            replay_mod.replay_proposal(
                a_proposal(),
                [a_case()],
                completion=completion_returning("b", "c"),
                judge_factory=lambda: judge,
                budget_dollars=1.0,
            )
        )
        assert current_run_key() == ""


# ── clause: ≤3/session, tool-free-preferring, provenance-pointed ────────────


class TestMining:
    """Three bounds and a pointer. Each mined case must name the turn it came from.

    Vacuity partner for `test_at_most_three_cases_per_session`:
    `test_a_second_session_contributes_its_own_three`. If the cap were global rather than
    per-session the first stays green and the second reddens; if there were no cap at all the
    first reddens and the second stays green.
    """

    @staticmethod
    def _write_session(root, session: str, turns: list[dict]):
        root.mkdir(parents=True, exist_ok=True)
        rec = root / f"{session}.jsonl"
        side = root / f"{session}.content.jsonl"
        with rec.open("w") as rf, side.open("w") as sf:
            for i, turn in enumerate(turns):
                rhash = turn.get("hash", f"{session}-h{i}")
                rf.write(
                    json.dumps(
                        {
                            "record_hash": rhash,
                            "ts": turn.get("ts", float(i)),
                            "tool_calls": turn.get("tool_calls", []),
                        }
                    )
                    + "\n"
                )
                body = turn.get("prompt", "please refactor the retry helper " * 6)
                fenced = f"<untrusted_content source=cli>\n{body}\n</untrusted_content>"
                sf.write(json.dumps({"record_hash": rhash, "prompt": fenced}) + "\n")

    def _mine(self, tmp_path, monkeypatch, **kw):
        root = tmp_path / "capture"
        monkeypatch.setattr(replay_mod, "capture_root_for_test", None, raising=False)
        monkeypatch.setattr(
            "personalclaw.inbound.capture_store.capture_dir", lambda: root, raising=True
        )
        return root

    def test_at_most_three_cases_per_session(self, tmp_path, monkeypatch):
        root = self._mine(tmp_path, monkeypatch)
        self._write_session(root, "sess-a", [{} for _ in range(9)])
        cases = replay_mod.mine_cases(limit=50)
        assert len(cases) == 3, "the ≤3/session bound"
        assert {c.session_id for c in cases} == {"sess-a"}

    def test_a_second_session_contributes_its_own_three(self, tmp_path, monkeypatch):
        root = self._mine(tmp_path, monkeypatch)
        self._write_session(root, "sess-a", [{} for _ in range(9)])
        self._write_session(root, "sess-b", [{} for _ in range(9)])
        cases = replay_mod.mine_cases(limit=50)
        assert len(cases) == 6
        assert {c.session_id for c in cases} == {"sess-a", "sess-b"}

    def test_every_case_points_back_at_its_capture_record(self, tmp_path, monkeypatch):
        root = self._mine(tmp_path, monkeypatch)
        self._write_session(root, "sess-a", [{"hash": "abc123"}])
        (case,) = replay_mod.mine_cases()
        assert case.provenance == "capture:sess-a#abc123"
        assert case.session_id == "sess-a" and case.record_hash == "abc123"
        assert case.to_dict()["provenance"] == "capture:sess-a#abc123"

    def test_tool_free_turns_are_PREFERRED_not_required(self, tmp_path, monkeypatch):
        root = self._mine(tmp_path, monkeypatch)
        self._write_session(
            root,
            "sess-a",
            [
                {"hash": "tooly", "tool_calls": [{"name": "Read"}], "ts": 99.0},
                {"hash": "clean", "tool_calls": [], "ts": 1.0},
            ],
        )
        cases = replay_mod.mine_cases()
        assert [c.record_hash for c in cases] == [
            "clean",
            "tooly",
        ], "tool-free sorts first even though the tool-using turn is NEWER"
        assert cases[0].tool_free is True and cases[1].tool_free is False

    def test_an_all_tool_using_home_still_yields_cases(self, tmp_path, monkeypatch):
        """The partner for 'preferring': a tool-free-ONLY rule would return nothing here, and
        a proposal would read `unreplayed` on a home full of usable turns."""
        root = self._mine(tmp_path, monkeypatch)
        self._write_session(root, "sess-a", [{"tool_calls": [{"name": "Edit"}]} for _ in range(2)])
        cases = replay_mod.mine_cases()
        assert len(cases) == 2
        assert all(not c.tool_free for c in cases)

    def test_an_acknowledgement_is_not_a_case(self, tmp_path, monkeypatch):
        root = self._mine(tmp_path, monkeypatch)
        self._write_session(root, "sess-a", [{"prompt": "thanks"}, {"prompt": "ok " * 60}])
        cases = replay_mod.mine_cases()
        assert len(cases) == 1, "the short prompt is dropped, the long one is kept"

    def test_a_mega_context_is_dropped_not_clipped(self, tmp_path, monkeypatch):
        """Clipping a fenced prompt would sever `</untrusted_content>` — a fence BREAK."""
        root = self._mine(tmp_path, monkeypatch)
        self._write_session(root, "sess-a", [{"prompt": "x" * (replay_mod.MAX_PROMPT_CHARS + 10)}])
        assert replay_mod.mine_cases() == []

    def test_the_prompt_reaches_the_arm_still_fenced(self, tmp_path, monkeypatch):
        """The capture sidecar's fence is the injection defence; mining must not strip it."""
        root = self._mine(tmp_path, monkeypatch)
        self._write_session(root, "sess-a", [{}])
        (case,) = replay_mod.mine_cases()
        assert case.prompt.strip().startswith("<untrusted_content")
        assert case.prompt.strip().endswith("</untrusted_content>")

    def test_a_record_with_no_sidecar_is_skipped(self, tmp_path, monkeypatch):
        root = self._mine(tmp_path, monkeypatch)
        root.mkdir(parents=True, exist_ok=True)
        (root / "sess-a.jsonl").write_text(
            json.dumps({"record_hash": "h", "ts": 1.0, "tool_calls": []}) + "\n"
        )
        assert replay_mod.mine_cases() == []

    def test_a_truncated_line_drops_the_record_not_the_session(self, tmp_path, monkeypatch):
        root = self._mine(tmp_path, monkeypatch)
        self._write_session(root, "sess-a", [{"hash": "good"}])
        with (root / "sess-a.jsonl").open("a") as handle:
            handle.write('{"record_hash": "trunc"')
        (case,) = replay_mod.mine_cases()
        assert case.record_hash == "good"

    def test_an_absent_capture_dir_mines_nothing_and_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "personalclaw.inbound.capture_store.capture_dir",
            lambda: tmp_path / "nope",
            raising=True,
        )
        assert replay_mod.mine_cases() == []


# ── the curator-cadence pass ─────────────────────────────────────────────────


class TestTheCuratorPass:
    """The pass wiring: which proposals it considers, and what it attaches when off."""

    def test_only_skill_and_template_kinds_are_replayable(self):
        assert replay_mod.REPLAYABLE_KINDS == {"skill", "template"}
        # `template_diff` carries typed OPS, not body text — replaying the ops list would
        # measure a JSON blob rather than the change.
        assert "template_diff" not in replay_mod.REPLAYABLE_KINDS

    def test_a_disabled_pass_attaches_an_honest_reason_rather_than_nothing(
        self, tmp_path, monkeypatch
    ):
        """A proposal with no `replay` key and one saying "replay is off" look identical on a
        card otherwise, and only one of them is something the user can act on."""
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        monkeypatch.setattr(replay_mod, "replay_enabled", lambda: False)
        prop = a_proposal(pid="skill-ea6pass01")
        proposals_mod._save(prop)  # noqa: SLF001
        result = run(replay_mod.run_pass())
        assert result == {"considered": 1, "replayed": 0, "deferred": 0, "unreplayed": 1}
        stored = proposals_mod.get(prop.id)
        assert stored.replay["state"] == "unreplayed"
        assert stored.replay["reason"] == replay_mod.UNREPLAYED_DISABLED

    def test_a_lesson_batch_is_never_considered(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        monkeypatch.setattr(replay_mod, "replay_enabled", lambda: False)
        proposals_mod._save(a_proposal(pid="lesson-ea6x", kind="lesson_batch"))  # noqa: SLF001
        assert run(replay_mod.run_pass())["considered"] == 0

    def test_an_already_replayed_proposal_is_not_replayed_again(self, tmp_path, monkeypatch):
        """Re-spending on a proposal that already carries evidence is money for nothing."""
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        monkeypatch.setattr(replay_mod, "replay_enabled", lambda: False)
        prop = a_proposal(pid="skill-ea6pass02")
        prop.replay = {"state": "replayed", "verdict": "improved"}
        proposals_mod._save(prop)  # noqa: SLF001
        assert run(replay_mod.run_pass())["considered"] == 0

    def test_summarize_pass_is_silent_when_nothing_happened(self):
        assert replay_mod.summarize_pass({"considered": 0}) == ""
        assert replay_mod.summarize_pass({}) == ""
        assert "considered=2" in replay_mod.summarize_pass({"considered": 2, "replayed": 2})

    def test_the_budget_is_fail_closed(self, monkeypatch):
        """Unlike the day budget's fail-OPEN, an unreadable config here means no replay.

        The directions are not symmetric: a day budget failing open leaves the breaker as the
        hard control, while this failing open would put unbounded LLM spend on a background
        tick nobody is watching.
        """

        def boom():
            raise RuntimeError("config unreadable")

        monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", staticmethod(boom))
        assert replay_mod.replay_budget() == 0.0
        assert replay_mod.replay_enabled() is False

    def test_the_curator_tick_awaits_the_replay_pass(self):
        """The hook itself. A pass with no scheduled caller is the shape this program keeps
        finding — `capture_store.prune`'s docstring named a "curator tick" for a whole release
        while nothing called it."""
        from pathlib import Path

        import personalclaw.history as history_mod

        source = Path(history_mod.__file__).read_text(encoding="utf-8")
        assert "await replay_mod.run_pass()" in source
        assert "from personalclaw.learning import replay as replay_mod" in source


# ── the config round-trip ────────────────────────────────────────────────────


class TestConfigRoundTrip:
    """dataclass + _meta → load() → to_dict() → write path. All four legs, or the knob is
    one a user can set and lose on the next save."""

    @staticmethod
    def _reload(cfg_file, learning: dict):
        from personalclaw.config.loader import AppConfig

        cfg_file.write_text(json.dumps({"learning": learning}), encoding="utf-8")
        return AppConfig.load()

    def test_both_fields_survive_a_save_load_cycle(self, cfg_file):
        cfg = self._reload(cfg_file, {"replay_enabled": True, "replay_max_dollars": 1.25})
        assert cfg.learning.replay_enabled is True
        assert cfg.learning.replay_max_dollars == 1.25
        # The half a `to_dict`-only test misses: save it back and reload. A field in
        # `to_dict` but absent from `load`'s mapping reverts to its default every reload,
        # and the next save then wipes the user's value out of the file.
        cfg.save()
        again = json.loads(cfg_file.read_text(encoding="utf-8"))["learning"]
        assert again["replay_enabled"] is True
        assert again["replay_max_dollars"] == 1.25

    def test_defaults_are_off(self, cfg_file):
        cfg = self._reload(cfg_file, {})
        assert cfg.learning.replay_enabled is False
        assert cfg.learning.replay_max_dollars == 0.0

    def test_a_negative_ceiling_is_clamped_to_off(self, cfg_file):
        """`Budget.is_unlimited` reads a negative ceiling as UNLIMITED."""
        cfg = self._reload(cfg_file, {"replay_max_dollars": -5.0})
        assert cfg.learning.replay_max_dollars == 0.0

    def test_both_are_in_the_patch_allowlist(self):
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        assert _EDITABLE_CONFIG["learning.replay_enabled"] == {"type": "bool"}
        spec = _EDITABLE_CONFIG["learning.replay_max_dollars"]
        assert spec["type"] == "float" and spec["min"] == 0.0

    def test_both_carry_a_label_and_help(self):
        from dataclasses import fields

        from personalclaw.config.learning import LearningConfig

        by_name = {f.name: f for f in fields(LearningConfig)}
        for name in ("replay_enabled", "replay_max_dollars"):
            meta = by_name[name].metadata
            assert meta.get("label"), f"{name} has no _meta label"
            assert meta.get("help"), f"{name} has no _meta help text"


# ── the seam, end to end ─────────────────────────────────────────────────────


class TestTheWholeSeam:
    """Capture files → mined cases → replay → attach → the row the API actually serves.

    Every class above tests one hop. This is the one that would have caught a hop wired to the
    wrong shape — the defect class EA-5's own execution log records twice ("each half's suite was
    green in isolation", and `stage_records` rejecting every record the importer produced). The
    projection is the specific risk here: `build_view` reads through `getattr`, so a field named
    differently on either side degrades to a default rather than raising, and the card would go
    quietly blank instead of failing.

    Drives the four hops directly rather than through `run_pass` (covered by `TestTheCuratorPass`),
    so nothing production-side is monkeypatched and the shapes under test are the real ones.
    """

    def test_a_replayed_proposal_reaches_the_inbox_row_with_its_numbers(
        self, tmp_path, monkeypatch
    ):
        from personalclaw.learning.inbox import build_view

        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        capture = tmp_path / "capture"
        TestMining._write_session(capture, "sess-real", [{"hash": "h1"}, {"hash": "h2"}])
        monkeypatch.setattr(
            "personalclaw.inbound.capture_store.capture_dir", lambda: capture, raising=True
        )

        # HOP 1 — mine.
        cases = replay_mod.mine_cases()
        assert len(cases) == 2, "the fixture's two turns must both mine"

        # HOP 2 — replay, through a real LLMJudge: baseline 2.0, candidate 4.0, per case.
        prop = a_proposal(pid="skill-ea6seam01")
        judge = judge_scoring(2.0, 4.0, 2.0, 4.0)
        report = run(
            replay_mod.replay_proposal(
                prop,
                cases,
                completion=completion_returning(*(["an answer"] * 4)),
                judge_factory=lambda: judge,
                budget_dollars=1.0,
            )
        )
        assert report.verdict == replay_mod.VERDICT_IMPROVED

        # HOP 3 — attach, and read it back off disk rather than trusting the object.
        proposals_mod._save(prop)  # noqa: SLF001
        assert replay_mod.attach(prop.id, report) is True
        stored = proposals_mod.get(prop.id)
        assert stored.replay["state"] == "replayed"
        assert stored.replay["verdict"] == "improved"
        assert stored.replay["baseline_mean"] == 2.0
        assert stored.replay["candidate_mean"] == 4.0
        # Provenance survived to the persisted record — the claim is checkable.
        assert all(c["provenance"].startswith("capture:sess-real#") for c in stored.replay["cases"])

        # HOP 4 — the ROW the handler serves, through the real projection.
        (row,) = build_view([stored]).rows
        served = row.to_dict()
        assert served["replay"]["state"] == "replayed"
        assert served["replay"]["verdict"] == "improved"
        assert served["replay"]["baseline_mean"] == 2.0
        assert served["replay"]["candidate_mean"] == 4.0
        assert served["replay"]["scored"] == 2
        assert served["replay"]["provenance"], "the card must be able to cite the turns"
        # And the row is still acceptable — the measurement changed no eligibility.
        assert served["bulk_acceptable"] is True

    def test_an_unreplayed_proposal_reaches_the_row_as_an_honest_absence(
        self, tmp_path, monkeypatch
    ):
        """The vacuity partner for the seam. Same projection, opposite content — so the test above
        cannot be passing because `to_dict` emits a constant."""
        from personalclaw.learning.inbox import build_view

        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        prop = a_proposal(pid="skill-ea6seam02")
        proposals_mod._save(prop)  # noqa: SLF001
        (row,) = build_view([proposals_mod.get(prop.id)]).rows
        served = row.to_dict()
        assert served["replay"]["state"] == "unreplayed"
        assert served["replay"]["reason"] == replay_mod.UNREPLAYED_NOT_RUN
        assert served["replay"]["candidate_mean"] is None
        assert served["replay"]["verdict"] == "unmeasured"
        assert served["bulk_acceptable"] is True
