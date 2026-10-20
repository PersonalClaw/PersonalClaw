"""The source-tree checks split from
``tests/test_replay_harness.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import sys

import pytest
from replay_harness_shared import a_case, a_proposal, completion_returning, judge_scoring, run

from personalclaw.learning import replay as replay_mod


class TestNeverTheEvalRunner:
    """The explicit hazard: ``eval/runner.py`` spawns a child with an env override.

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
                    raise AssertionError(
                        f"the replay path imported {forbidden} — the env-mutation hazard"
                    )
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

    def test_it_does_compose_the_completion_and_the_judge(self):
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
