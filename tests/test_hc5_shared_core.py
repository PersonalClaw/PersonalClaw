"""HC-5 shared-core parity (HARNESS-CRAFT §2.3/§3.2, Success Criterion 8).

The `best-of-n` and `check-work` bundled templates are the ENGINE-NATIVE halves of the
same-named bundled skills. The plan's risk table names the failure this module exists to
prevent — "skill/template drift (two behaviors for one name)" — and its mitigation: both
halves call the same §2.1/§3.1 core, templates are thin spec wrappers, and *a shared
test exercises both entry points*. This is that test.

Entry points driven, per template:

* **best-of-n** — the skill's tool (`mcp_subagents._best_of_n`, the call SKILL.md tells
  the presenting model to make) versus the bundled template's own `sample` action node
  dispatched through the v2 engine seam (`workflows.engine.dispatch_action`). The node
  is taken from the REAL `workflows/bundled/best-of-n/workflow.json` and its `with`
  bindings resolved from run inputs, so the template's wiring is what is under test —
  a hand-built node would prove only the provider.
* **check-work** — the §3.1 core (`check_work.derive_and_run`, the documented flow the
  skill executes and the SDLC post-gate hook calls) versus the bundled template's
  `check` action node, same construction.

Model seams are stubbed exactly where `test_sampling_best_of_n.py` stubs them
(`llm_helpers.one_shot_completion` for samples; the judge through
`provider_bridge.resolve_provider_for_use_case`, which is where the core's DEFAULT
factory resolves when no factory is injected — the path both real entry points take).
One stub serves both entry points in each test, so a difference in the answers could
only come from the entry points themselves.

DEVIATION recorded (the note §2.3 owes): the plan sketches "fan-out node → judge node →
select node", but the shipped core does not decompose — its pieces are private and the
concurrency proof, fail-open tiers, tie-break and outcome record span the whole call.
The template therefore calls ``best_of_n`` WHOLE from one action node (the engine sees
fan-out → judge → select as one metered action; the parallelism is the core's own
``asyncio.gather``), and `test_the_template_providers_call_the_cores_not_copies` pins
that the wrappers stay wrappers.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

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
    monkeypatch.setattr(provider_bridge, "resolve_provider_for_use_case", lambda _uc: provider)


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
        node ⇒ same winner, same slate, same judgments — the SC 8 sentence, executed.

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
        """Same claims + same root through the §3.1 core (the skill's documented flow,
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


def test_the_template_providers_call_the_cores_not_copies():
    """The whole HC-5 contract is "CALLING the §2.1/§3.1 cores (no reimplementation)".
    Pin it at the source level: each provider imports its core's entry point, and
    neither imports the pieces a reimplementation would need."""
    from personalclaw.action_providers import best_of_n_provider, check_work_provider

    sampling_src = Path(best_of_n_provider.__file__).read_text(encoding="utf-8")
    assert "from personalclaw.sampling import best_of_n" in sampling_src
    assert "LLMJudge" not in sampling_src, "judging belongs to the core, not the wrapper"

    check_src = Path(check_work_provider.__file__).read_text(encoding="utf-8")
    assert "derive_and_run" in check_src and "from personalclaw.check_work import" in check_src
    assert "reconstruct_claims" not in check_src, "derivation belongs to the core, not the wrapper"
