"""Shared-core parity.

The `best-of-n` and `check-work` bundled templates are the ENGINE-NATIVE halves of the
same-named bundled skills. The failure this module exists to prevent is skill/template
drift (two behaviors for one name), and the mitigation is that both
halves call the same core, templates are thin spec wrappers, and *a shared
test exercises both entry points*. This is that test.

Entry points driven, per template:

* **best-of-n** — the skill's tool (`mcp_subagents._best_of_n`, the call SKILL.md tells
  the presenting model to make) versus the bundled template's own `sample` action node
  dispatched through the v2 engine seam (`workflows.engine.dispatch_action`). The node
  is taken from the REAL `workflows/bundled/best-of-n/workflow.json` and its `with`
  bindings resolved from run inputs, so the template's wiring is what is under test —
  a hand-built node would prove only the provider.
* **check-work** — the core (`check_work.derive_and_run`, the documented flow the
  skill executes and the SDLC post-gate hook calls) versus the bundled template's
  `check` action node, same construction.

Model seams are stubbed where the core reaches them (`llm_helpers.one_shot_completion` for
samples; the judge through `provider_bridge.resolve_metered_model`, which is where the core's
DEFAULT factory resolves when no factory is injected — the path both real entry points take).
One stub serves both entry points in each test, so a difference in the answers could
only come from the entry points themselves.

A deliberate deviation: the sketched shape is "fan-out node → judge node →
select node", but the shipped core does not decompose — its pieces are private and the
concurrency proof, fail-open tiers, tie-break and outcome record span the whole call.
The template therefore calls ``best_of_n`` WHOLE from one action node (the engine sees
fan-out → judge → select as one metered action; the parallelism is the core's own
``asyncio.gather``), and `test_each_template_node_returns_what_its_core_returns` holds
that the wrappers stay wrappers.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from every_name import put_in_every_name
from table_rows import row

from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.workflows.models import InstanceState, Node, walk

PROMPT = "write a two-line release note for the sparse-worktree change"
CRITERIA = "specific, under 60 characters per line, no hype"


# ── shared fakes (the sampling suite's, in miniature) ─────────────────────────


class _JudgeProvider:
    """ModelProvider stand-in returning a scripted score per candidate marker.

    Keyed by a marker the candidate text carries, so the verdict depends on the
    CANDIDATE and not on call order — the property that lets one scores table serve
    both entry points and still prove the deterministic tie-break through each.
    """

    def __init__(self, scores: dict[str, float]):
        self._scores = scores

    async def start(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def set_workspace(self, path) -> None:  # pragma: no cover — protocol surface
        pass

    async def stream(self, message: str):
        score = 0.0
        for marker, value in self._scores.items():
            if marker in message:
                score = value
                break
        yield LLMEvent(
            kind=EVENT_TEXT_CHUNK,
            text=json.dumps({"score": score, "reason": f"scored {score}"}),
        )
        yield LLMEvent(kind=EVENT_COMPLETE)

    async def approve_tool(self, request_id):  # pragma: no cover — protocol surface
        pass

    async def reject_tool(self, request_id):  # pragma: no cover — protocol surface
        pass

    async def cancel(self):  # pragma: no cover — protocol surface
        pass


def _stub_samples(monkeypatch, *, fail: bool = False) -> None:
    """Deterministic, STATELESS completion stub: the text is a function of the
    temperature, so the tool call and the template dispatch see identical slates."""
    import personalclaw.llm_helpers as llm_helpers

    async def fake_one_shot(prompt, *, use_case="background", temperature=None, **_kw):
        if fail:
            raise RuntimeError("provider down")
        return f"candidate@{temperature}"

    monkeypatch.setattr(llm_helpers, "one_shot_completion", fake_one_shot)


def _stub_judge(monkeypatch, scores: dict[str, float]) -> None:
    """Patch the seam the core's DEFAULT judge factory resolves through — the path both
    the MCP tool and the action provider take, since neither injects a factory."""
    from personalclaw.providers import provider_bridge

    provider = _JudgeProvider(scores)
    monkeypatch.setattr(provider_bridge, "resolve_metered_model", lambda _uc, **_kw: provider)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """The sampling core appends its outcome record unconditionally — never to the
    real home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    return tmp_path


# ── driving the REAL template node through the engine seam ───────────────────


def _template_action_node(template: str, node_id: str) -> Node:
    from personalclaw.workflows.bundled_defs import read_template

    wf = read_template(template)
    assert wf is not None, f"bundled template {template!r} did not load"
    for _path, node in walk(wf.root):
        if node.id == node_id:
            return node
    raise AssertionError(f"{template} has no node {node_id!r}")


def _dispatch(node: Node, inputs: dict, *, cwd: str = ""):
    from personalclaw.action_providers.registry import _ensure_default_providers_registered
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch_action

    _ensure_default_providers_registered()
    ctx = BindingContext(inputs=inputs)
    return asyncio.run(dispatch_action(node, ctx, run_id="run-hc5", cwd=cwd))


# ── best-of-n: tool (skill) vs template ──────────────────────────────────────


class TestBestOfNParity:
    #: A deliberate TIE at the top (idx 1 and idx 2 both score 4.5): the winner must be
    #: the LOWEST index of the top scorers through BOTH entry points, so a template that
    #: re-owned selection (say, max() over a re-ordered slate) diverges here.
    SCORES = {"candidate@0.2": 3.0, "candidate@0.7": 4.5, "candidate@1.0": 4.5}

    def _tool_result(self) -> dict:
        from personalclaw.mcp_subagents import _best_of_n

        raw = _best_of_n({"prompt": PROMPT, "n": 3, "criteria": CRITERIA})
        return json.loads(raw)

    def test_skill_tool_and_template_produce_one_answer(self, monkeypatch):
        """Same inputs through the skill's tool and through the template's own action
        node ⇒ same winner, same slate, same judgments — one behavior for one name, executed.

        FALSIFIED BY: making the provider call anything but the core (e.g. its own
        max() over judgments with ties broken high) — the tie in SCORES flips the
        winner and this reds.
        """
        _stub_samples(monkeypatch)
        _stub_judge(monkeypatch, self.SCORES)

        tool = self._tool_result()

        node = _template_action_node("best-of-n", "sample")
        result = _dispatch(node, {"prompt": PROMPT, "n": 3, "criteria": CRITERIA})
        assert result.state is InstanceState.DONE, result.failure
        tmpl = result.output

        # One contract, key for key — the docstring in `sampling.best_of_n` promises the
        # same envelope to the tool, the skill and this template.
        assert set(tmpl) == set(tool)
        assert tmpl["winner"] == tool["winner"]
        assert tmpl["winner_idx"] == tool["winner_idx"] == 1, "tie must break to the LOWEST index"
        assert tmpl["judged"] is tool["judged"] is True
        assert tmpl["n"] == tool["n"] == 3
        assert [(c["idx"], c["temperature"], c["text"]) for c in tmpl["candidates"]] == [
            (c["idx"], c["temperature"], c["text"]) for c in tool["candidates"]
        ]
        assert [(j["idx"], j["score"]) for j in tmpl["judgments"]] == [
            (j["idx"], j["score"]) for j in tool["judgments"]
        ]

    def test_the_template_node_binds_run_inputs_not_literals(self, monkeypatch):
        """The dispatch above resolves `{{inputs.*}}` from the run — prove the binding
        is live by changing an input and watching the slate follow."""
        _stub_samples(monkeypatch)
        _stub_judge(monkeypatch, {})

        node = _template_action_node("best-of-n", "sample")
        result = _dispatch(node, {"prompt": PROMPT, "n": 2, "criteria": ""})
        assert result.state is InstanceState.DONE, result.failure
        assert result.output["n"] == 2
        assert len(result.output["candidates"]) == 2

    def test_an_all_failed_slate_is_honest_through_both_entry_points(self, monkeypatch):
        """The core's fail-open floor (`winner=None` + note) surfaces as each entry
        point's own honest form: the tool says "No candidate", the template node FAILS
        with the envelope kept in its output — never a fabricated answer through
        either."""
        _stub_samples(monkeypatch, fail=True)
        _stub_judge(monkeypatch, {})

        from personalclaw.mcp_subagents import _best_of_n

        tool_raw = _best_of_n({"prompt": PROMPT, "n": 3, "criteria": CRITERIA})
        assert tool_raw.startswith("No candidate:")

        node = _template_action_node("best-of-n", "sample")
        result = _dispatch(node, {"prompt": PROMPT, "n": 3, "criteria": CRITERIA})
        assert result.state is InstanceState.FAILED
        assert result.output["winner"] is None
        assert result.output["note"] in tool_raw


# ── check-work: core (the skill's flow) vs template ──────────────────────────


def _workspace(tmp_path: Path) -> tuple[Path, str]:
    """A work root with one claim satisfied and the claims text naming it."""
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "mod.py").write_text("def derive_checks(claims):\n    return []\n")
    (ws / "docs").mkdir()
    (ws / "docs" / "notes.md").write_text("# notes\n")
    # TWO sentences on purpose: the core derives per-claim, and one sentence naming
    # both files would attach `derive_checks` as the CONTENT needle to `docs/notes.md`
    # too — a legitimate core FAIL, which is not this fixture's job.
    claims = (
        "Added `derive_checks()` to `src/mod.py`. " "Wrote `docs/notes.md` with the design notes."
    )
    return ws, claims


class TestCheckWorkParity:
    def test_core_and_template_produce_one_report(self, tmp_path, monkeypatch):
        """Same claims + same root through the core (the skill's documented flow,
        and the SDLC hook's call) and through the template's `check` node ⇒ the same
        checks, statuses, evidence and rendered report."""
        from personalclaw.check_work import derive_and_run, render_report

        ws, claims = _workspace(tmp_path)
        core = derive_and_run(claims, root=ws)
        assert core.results, "the fixture must derive at least one check"

        node = _template_action_node("check-work", "check")
        result = _dispatch(node, {"claims": claims, "root": str(ws)})
        assert result.state is InstanceState.DONE, result.failure
        tmpl = result.output

        assert tmpl["verdict"] == core.verdict == "pass"
        assert tmpl["note"] == core.note
        assert [(c["label"], c["how"], c["status"], c["evidence"]) for c in tmpl["checks"]] == [
            (r.check.label, r.check.how, r.status, r.evidence) for r in core.results
        ]
        assert tmpl["report"] == render_report(core)

    def test_a_failed_check_fails_the_template_node_as_it_fails_the_hook(
        self, tmp_path, monkeypatch
    ):
        """A claimed-but-missing file is verdict `fail` in the core; the template's
        rendering of that verdict is a FAILED node (the SDLC hook's `ok = verdict !=
        "fail"` mapping), with the full report kept in the output."""
        from personalclaw.check_work import derive_and_run

        ws, _ = _workspace(tmp_path)
        claims = "Wrote `docs/missing.md` with the rollout plan."
        core = derive_and_run(claims, root=ws)
        assert core.verdict == "fail"

        node = _template_action_node("check-work", "check")
        result = _dispatch(node, {"claims": claims, "root": str(ws)})
        assert result.state is InstanceState.FAILED
        assert result.output["verdict"] == "fail"
        assert [c["status"] for c in result.output["checks"]] == [r.status for r in core.results]

    def test_a_blank_root_falls_back_to_the_run_workspace(self, tmp_path, monkeypatch):
        """The template's `root` input defaults to "" and the provider then checks the
        run's own workspace — where an upstream stage's files land — so a workflow can
        end with this node without restating a path the engine already holds."""
        from personalclaw.check_work import derive_and_run

        ws, claims = _workspace(tmp_path)
        core = derive_and_run(claims, root=ws)

        node = _template_action_node("check-work", "check")
        result = _dispatch(node, {"claims": claims, "root": ""}, cwd=str(ws))
        assert result.state is InstanceState.DONE, result.failure
        assert result.output["verdict"] == core.verdict
        assert len(result.output["checks"]) == len(core.results)

    def test_command_claims_stay_unverifiable_not_executed(self, tmp_path):
        """Doctrine parity: the provider injects no command runner, so a claim naming a
        command is `unverifiable` through the template exactly as the core reports it
        with no runner — the node never shells out on its own authority."""
        ws, _ = _workspace(tmp_path)
        claims = "Ran `pytest tests/test_mod.py` and it passed clean."

        node = _template_action_node("check-work", "check")
        result = _dispatch(node, {"claims": claims, "root": str(ws)})
        assert result.state is InstanceState.DONE, result.failure
        statuses = {c["status"] for c in result.output["checks"]}
        assert statuses == {"unverifiable"}, result.output["checks"]


# ── the wrappers stay wrappers ────────────────────────────────────────────────


#: What the stand-in for each core hands back in the table below: nothing the real core would make
#: of these inputs, so a node that answers with it can only have taken it from the stand-in.
_PICKED = {
    "winner": "the core's pick",
    "winner_idx": 2,
    "candidates": [{"idx": 2, "temperature": 0.4, "text": "the core's pick"}],
    "judgments": [{"idx": 2, "score": 9.5, "reason": "the core judged it"}],
    "judged": True,
    "n": 3,
    "note": "from the core",
}
_NOTHING = {
    "winner": None,
    "winner_idx": None,
    "candidates": [],
    "judgments": [],
    "judged": False,
    "n": 3,
    "note": "the core sampled nothing",
    "failure": {"class": "auth", "fix": "sign in to the provider again", "retry_at": None},
}


def _reports():
    from personalclaw.check_work import CheckResult, CheckWorkReport, DerivedCheck

    found = DerivedCheck(kind="file_exists", target="from/the/core.txt", claim="the core's claim")
    missing = DerivedCheck(
        kind="file_contains", target="core.md", claim="the core's other claim", needle="core"
    )
    return {
        "pass": CheckWorkReport(
            results=[CheckResult(found, "pass", "the core looked")], note="the core's note"
        ),
        "fail": CheckWorkReport(
            results=[
                CheckResult(found, "pass", "the core looked"),
                CheckResult(missing, "fail", "the core found no such line"),
            ]
        ),
    }


def _no_copy(monkeypatch, refused: list[str]) -> None:
    """The pieces a wrapper would need to sample, judge or derive on its own: each one records
    that it was reached, and refuses."""
    import personalclaw.check_work as check_work
    import personalclaw.eval.judge as judge
    import personalclaw.llm_helpers as llm_helpers
    import personalclaw.sampling as sampling
    from personalclaw.providers import provider_bridge

    pieces = [
        (llm_helpers, "one_shot_completion"),
        (provider_bridge, "resolve_metered_model"),
        (judge, "LLMJudge"),
        (sampling, "_sample_one"),
        (sampling, "_judge_candidates"),
        (sampling, "_select_winner"),
        (check_work, "reconstruct_claims"),
        (check_work, "derive_checks"),
        (check_work, "run_checks"),
    ]
    for module, name in pieces:

        def _refuse(*_a, _name=f"{module.__name__}.{name}", **_kw):
            refused.append(_name)
            raise AssertionError(f"the wrapper reached {_name} itself")

        put_in_every_name(monkeypatch, getattr(module, name), _refuse)


def test_each_template_node_returns_what_its_core_returns(tmp_path, monkeypatch, subtests):
    """The whole contract is CALLING the cores (no reimplementation). Each row puts a stand-in in
    the core entry point's place, under every name that holds it, and dispatches the bundled
    template's own action node: the node hands the core its inputs once, and its output and
    state are the core's answer as the provider renders it (the envelope verbatim for best-of-n,
    the report's checks and render_report for check-work). The pieces a copy would need (the
    sampling and judging seams, LLMJudge, the core's private steps, reconstruct_claims and the
    derivation behind it) refuse if the wrapper reaches them, so neither provider samples,
    judges or derives on its own."""
    import personalclaw.check_work as check_work
    import personalclaw.sampling as sampling
    from personalclaw.check_work import render_report

    ws, claims = _workspace(tmp_path)
    reports = _reports()
    rows = [
        ("best-of-n, the core picks", "best-of-n", "sample", _PICKED, InstanceState.DONE),
        ("best-of-n, the core has nothing", "best-of-n", "sample", _NOTHING, InstanceState.FAILED),
        ("check-work, the core passes", "check-work", "check", "pass", InstanceState.DONE),
        ("check-work, the core fails", "check-work", "check", "fail", InstanceState.FAILED),
    ]
    for name, template, node_id, answer, state in rows:
        with row(subtests, name=name), monkeypatch.context() as m:
            calls: list[tuple] = []
            refused: list[str] = []
            if template == "best-of-n":

                async def _core(prompt, n=3, criteria="", *_a, _answer=answer, **_kw):
                    calls.append((prompt, n, criteria))
                    return json.loads(json.dumps(_answer))

                put_in_every_name(m, sampling.best_of_n, _core)
                inputs = {"prompt": PROMPT, "n": 3, "criteria": CRITERIA}
                wanted_calls = [(PROMPT, 3, CRITERIA)]
                expected = answer
            else:
                report = reports[answer]

                def _core(text, *, root, max_checks=check_work.MAX_CHECKS, _r=report, **_kw):
                    calls.append((text, str(root), max_checks))
                    return _r

                put_in_every_name(m, check_work.derive_and_run, _core)
                inputs = {"claims": claims, "root": str(ws)}
                wanted_calls = [(claims, str(ws), check_work.MAX_CHECKS)]
                expected = {
                    "verdict": report.verdict,
                    "note": report.note,
                    "checks": [
                        {
                            "kind": r.check.kind,
                            "target": r.check.target,
                            "needle": r.check.needle,
                            "claim": r.check.claim,
                            "label": r.check.label,
                            "how": r.check.how,
                            "status": r.status,
                            "evidence": r.evidence,
                        }
                        for r in report.results
                    ],
                    "report": render_report(report),
                }
            _no_copy(m, refused)

            result = _dispatch(_template_action_node(template, node_id), inputs)

            assert refused == [], f"{name}: the wrapper did the core's work itself: {refused}"
            assert calls == wanted_calls, f"{name}: the core was handed {calls}"
            assert result.state is state, f"{name}: {result.state} ({result.failure})"
            assert result.output == expected, f"{name}: the node answered {result.output}"
