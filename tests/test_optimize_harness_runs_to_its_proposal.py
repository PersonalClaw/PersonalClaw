"""The bundled optimize-harness search runs as it ships, and holds to what it promises.

Driven as it ships: every step, binding and loop as written, its bash steps running this install's
``personalclaw optimize-harness <step>`` in their own processes, its scoring step running in this
process as the gateway runs it, and its two model-calling halves answered by stand-ins with a known
price: the proposer (a stand-in subagent manager, which books what each spawn cost the way the real
one does) and the scorer's model (a scripted provider behind the real resolution seam and the real
model-call guard, priced through Settings' own price table at exactly $0.05 a call).

🔴 Before:

* ``budget_usd`` held nothing in a template run: no step measured what the run spent, and the
  search ran on to its other halts whatever it cost;
* a candidate's score was the proposer's own claim, read straight off its answer, and the gate
  held its bar at the floor the search started from, so a later, worse candidate was admitted and
  became the winner;
* the loop stopped at a hardcoded 12 cycles whatever ``max_iterations`` asked for, and a search
  that reached it was handed to a person with its winner unfiled;
* a model step filed the winner, so nothing was filed unless the model called its proposal tool,
  and the runs that tool must cite were never handed to it.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from personalclaw.agents.defaults import TEMPLATE_REFINER_AGENT_NAME, TEMPLATE_REFINER_TOOLS
from personalclaw.evals import optimize
from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from personalclaw.llm.capabilities import Capability, ProviderCapability
from personalclaw.llm.registry import ProviderEntry, ProviderRegistry
from personalclaw.workflows import journal as journal_mod
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import InstanceState, RunStatus, WorkflowRun, spec_path, walk

TARGET = "code-project"

#: The scorer's model: one entry, priced in Settings' own table at nothing per prompt token and
#: $500 per million answer tokens. Every call answers 100 tokens, so every call costs $0.05 and
#: what the guard sets aside for a call is what it then costs.
ENTRY, MODEL = "eval-scripted", "scripted-judge-1"
CALL_USD = 0.05
ANSWER_TOKENS = 100

#: What the proposer's subagent costs per spawn, booked as the real manager books it.
PROPOSE_USD = 0.10

#: The recorded runs of the target every candidate is scored against.
CASES = ("case-1", "case-2", "case-3")

#: The statuses a run waits in for a person: a test reads where the run stopped rather than wait
#: out its deadline on a run nobody will answer.
_WAITING = (RunStatus.NEEDS_INPUT, RunStatus.PAUSED)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ── the scorer's model ───────────────────────────────────────────────────────


class _ScriptedEval:
    """Answers an arm with what its prompt asks for, and judges an answer by what it says.

    A candidate's edit writes ``PASSES=<n>`` into a prompt of the target, and the target's own
    prompts carry the recorded run's task (``case-<i>``), so an arm answers
    ``ANSWER passes=<n> case=<i>`` and the judge passes the case when ``i <= n``: a candidate that
    asks for more passes scores higher, deterministically.
    """

    supports_tools = False

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @staticmethod
    def _reply(prompt: str) -> str:
        answered = re.search(r"ANSWER passes=(\d+) case=(\d+)", prompt)
        if answered:
            passes, case = int(answered.group(1)), int(answered.group(2))
            score = 4 if case <= passes else 1
            return json.dumps({"score": score, "reason": f"case {case} against {passes}"})
        passes_m = re.search(r"PASSES=(\d+)", prompt)
        case_m = re.search(r"case-(\d+)", prompt)
        passes = int(passes_m.group(1)) if passes_m else 0
        case = int(case_m.group(1)) if case_m else 0
        return f"ANSWER passes={passes} case={case}"

    async def _answer(self, prompt: str):
        yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=self._reply(prompt))
        yield LLMEvent(kind=EVENT_COMPLETE, input_tokens=40, output_tokens=ANSWER_TOKENS)

    def stream(self, message: str):
        return self._answer(message)

    def complete(self, messages: list[dict], **kw: Any):
        text = "\n".join(str(m.get("content") or "") for m in messages if isinstance(m, dict))
        return self._answer(text)


def _bind_scripted_eval(monkeypatch) -> None:
    """The scorer's model behind the real resolution seam, with its price set as Settings sets
    it, in this test's own home (so after the home is set)."""
    registry = ProviderRegistry()
    registry.register_type(
        ProviderCapability(
            type="scripted-eval",
            capabilities=frozenset({Capability.CHAT}),
            supports_streaming=True,
            supports_tools=False,
            supports_embeddings=False,
            supports_vision=False,
            max_context_tokens=200_000,
        ),
        lambda **_kw: _ScriptedEval(),
    )
    registry.register_entry(ProviderEntry(name=ENTRY, type="scripted-eval", model=MODEL))
    monkeypatch.setattr("personalclaw.llm.registry.get_default_registry", lambda: registry)
    ref = f"{ENTRY}:{MODEL}"
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.load_active_models",
        lambda: {"chat": [ref], "background": [ref], "reasoning": [ref]},
    )
    from personalclaw.routing.rates import set_rate

    set_rate(ref, {"in_per_mtok": 0.0, "out_per_mtok": CALL_USD / ANSWER_TOKENS * 1_000_000})


# ── the target's recorded runs, and the space the search runs in ─────────────


def _seed_target_runs() -> None:
    """Three runs of the target that ended, journaled the way the controller journals them."""
    for task in CASES:
        run = store.create(WorkflowRun(id="", workflow_name=TARGET, inputs={"task": task}))
        run.status = RunStatus.COMPLETE
        store.save(run)
        journal = journal_mod.Journal(run.id)
        journal.run_started(TARGET, inputs={"task": task}, spec_version=1)
        journal.step_completed(
            "root.children[0]", "init", epoch=1, cache_key=f"ck-{task}", state=InstanceState.DONE
        )
        journal.run_finished(RunStatus.COMPLETE.value, elapsed_secs=1.0, tokens=10)


@pytest.fixture
def space(tmp_path, monkeypatch) -> Path:
    """This test's homes, set in the environment for the steps' own processes, the scorer's
    priced model, the target's recorded runs, and a work area apart from them, so a stage's
    write-scope check watches only the run's own folders."""
    home = tmp_path / "homes" / "pc-home"
    home.mkdir(parents=True)
    user = tmp_path / "homes" / "user-home"
    user.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path / "homes")
    _bind_scripted_eval(monkeypatch)
    work = tmp_path / "space"
    (work / "workspace").mkdir(parents=True)
    live = work / "live" / TARGET
    live.mkdir(parents=True)
    (live / "workflow.json").write_text(json.dumps({"name": TARGET}), encoding="utf-8")
    (live / optimize.LOCK_NAME).write_text(json.dumps({"hashes": {}}), encoding="utf-8")
    _seed_target_runs()
    return work


def _proposal(passes: int, *, fingerprint: str = "", claimed: float | None = None) -> dict:
    """A proposer's answer: an edit asking the target's handoff for *passes* passes.

    It also claims a score, as a proposer asked for one would: by default the score the candidate
    then measures, so a search that read its scores off the proposer climbs the same way as one
    that measures them, and the two differ only where the claim does. Nothing may read it.
    """
    return {
        "fix_fingerprint": fingerprint or f"passes-{passes}",
        "diff_text": f"--- a/workflow.json\n+++ b/workflow.json\n@@ PASSES={passes} @@\n",
        "rationale": f"the handoff should hold {passes} of the runs to their request",
        "ops": [
            {
                "op": "update_node",
                "node_id": "handoff",
                "fields": {"prompt": f"Summarize {{{{inputs.task}}}} for review. PASSES={passes}"},
            }
        ],
        "score": round(min(passes, len(CASES)) / len(CASES), 2) if claimed is None else claimed,
    }


class _Done:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = True
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""
        # What the spawn cost, where `step_usage.subagent_usage` reads it.
        self.input_tokens = 40
        self.output_tokens = 60
        self.cost_usd = PROPOSE_USD
        self.model = MODEL


class _Proposer:
    """Stands in for the subagent manager: each spawn finishes at once, and books what it cost to
    the usage ledger under the spawn's parent session, as ``SubagentManager._record_subagent_usage``
    books a real one.

    The proposer's spawns answer with the next proposal (the last one again once they run out).
    Any other model step is answered with an object that does nothing, so a template with a model
    step this one does not have still runs to its end, and the test reads what it did there."""

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers
        self.prompts: dict[str, list[str]] = {}
        self.spawns: list[dict[str, Any]] = []
        self._done: dict[str, _Done] = {}

    @property
    def proposals(self) -> list[str]:
        return self.prompts.get("propose", [])

    def spawn(self, **kw: Any) -> _Done:
        from personalclaw.usage_ledger import TurnUsage, record_turn

        node_id = str(kw["parent_session_key"]).rsplit(":", 1)[-1]
        asked = self.prompts.setdefault(node_id, [])
        asked.append(str(kw["task"]))
        self.spawns.append(kw)
        answer: dict[str, Any] = {}
        if node_id == "propose" and self.answers:
            answer = self.answers[min(len(asked), len(self.answers)) - 1]
        record_turn(
            TurnUsage(
                ts=datetime.now(timezone.utc).isoformat(),
                session_key=str(kw["parent_session_key"]),
                source="subagent",
                agent=str(kw.get("agent") or ""),
                provider=ENTRY,
                model=MODEL,
                input_tokens=40,
                output_tokens=60,
                cost_usd=PROPOSE_USD,
                priced=True,
            )
        )
        done = _Done(f"sub{len(self.spawns)}", json.dumps(answer))
        self._done[done.id] = done
        return done

    def get(self, agent_id: str) -> _Done | None:
        return self._done.get(agent_id)


def _inputs(space: Path, **overrides: Any) -> dict[str, Any]:
    return {
        "target_template": TARGET,
        "live_target": str(space / "live" / TARGET),
        "sandbox": str(space / "sandbox"),
        "budget_usd": 10.0,
        "suite_threshold": 0.3,
        "hypothesis_abandon_after": 5,
        "no_improvement_halt": 50,
        "max_iterations": 12,
        **overrides,
    }


async def _run(
    space: Path, inputs: dict[str, Any], answers: list[dict[str, Any]]
) -> tuple[RunController, _Proposer]:
    from personalclaw.action_providers.registry import _ensure_default_providers_registered

    _ensure_default_providers_registered()
    template = read_template("optimize-harness")
    assert template is not None
    spec = template.to_dict()
    run = store.create(
        WorkflowRun(id="", workflow_name=spec["name"], mode="background", inputs=inputs)
    )
    store.write_spec(run.id, spec)
    proposer = _Proposer(answers)
    ctl = RunController(
        run, spec, services=EngineServices(subagents=proposer, cwd=str(space / "workspace"))
    )
    await ctl.start()
    deadline = asyncio.get_running_loop().time() + 240
    while not ctl.run.is_terminal and ctl.run.status not in _WAITING:
        assert asyncio.get_running_loop().time() < deadline, ctl.run.status
        await asyncio.sleep(0.2)
    return ctl, proposer


def _steps(ctl: RunController) -> dict[str, tuple[str, str]]:
    """node id → (its last instance's state, why it failed), for a red that names the step."""
    nodes = dict(walk(ctl.root))
    out: dict[str, tuple[str, str]] = {}
    for path, inst in sorted(ctl.instances.items()):
        node = nodes.get(spec_path(path))
        if node is not None and node.id:
            out[node.id] = (inst.state.value, inst.failure.cause_plain if inst.failure else "")
    return out


def _halt(ctl: RunController) -> str:
    """Why the search loop said it stopped (its last cycle's verdict); ``""`` when it said none."""
    return str((ctl._outputs.get("search") or {}).get("halt") or "")


def _filed(ctl: RunController) -> dict[str, Any]:
    """What the run's last step reported filing, ``{}`` when no step did."""
    return dict(ctl._outputs.get("file") or {})


def _ledger(space: Path) -> list[dict[str, Any]]:
    path = space / "sandbox" / optimize.EXPERIENCE_DIR / "index.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def _usage_rows(run_id: str) -> list[dict[str, Any]]:
    """The run's own rows in the per-call usage ledger: what the run actually spent."""
    from personalclaw.usage_ledger import _iter_rows
    from personalclaw.workflows.ownership import parse_owned

    return [
        r for r in _iter_rows() if (parse_owned(str(r.get("session_key"))) or ("",))[0] == run_id
    ]


def _pending() -> list[Any]:
    from personalclaw.learning import proposals

    return list(proposals.list_pending(kind=proposals.Kind.TEMPLATE_DIFF.value))


# ── the budget ───────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_a_search_stops_before_its_next_cycle_would_pass_its_budget(space: Path) -> None:
    """🔴 Red before: nothing in a template run measured its spend, so the search ran past its
    budget to its other halts.

    Each cycle costs $0.40: the proposal ($0.10) and the scoring of its candidate against the
    target's three runs (an arm and a verdict each, six calls at $0.05). With $1.00, two cycles
    spend $0.80, and a third would pass the budget, so the search stops there and says so."""
    answers = [_proposal(1), _proposal(2), _proposal(3)]
    ctl, proposer = await _run(space, _inputs(space, budget_usd=1.0), answers)

    assert _halt(ctl) == optimize.HaltReason.BUDGET_EXHAUSTED.value, (
        _halt(ctl),
        len(proposer.proposals),
        _steps(ctl),
    )
    assert len(proposer.proposals) == 2, "a third cycle started after the budget said stop"
    assert ctl.run.status == RunStatus.COMPLETE, (ctl.run.error_message, _steps(ctl))

    rows = _usage_rows(ctl.run.id)
    spent = round(sum(float(r["cost_usd"]) for r in rows), 6)
    assert spent == pytest.approx(0.80) and spent <= 1.0, rows
    assert all(r["priced"] for r in rows)
    assert {r["session_key"].rsplit(":", 1)[-1] for r in rows} == {"propose", "score"}
    assert len([r for r in rows if r["session_key"].endswith(":score")]) == 12

    summary = _filed(ctl)["summary"]
    assert summary.startswith(
        "The search stopped at its budget: it has spent $0.80 of its $1.00 budget, and a cycle "
        "of the search has cost up to $0.40, so another would pass it. It kept candidate 2, "
        "which scored 0.67."
    ), summary
    # The run's own ledger books each scoring's cost to its step, as the run page shows it.
    events = journal_mod.ledger(ctl.run.id, kinds={"step_completed"})
    scored = [e for e in events if e.get("node_id") == "score"]
    assert [round(float(e["cost_usd"]), 6) for e in scored] == [0.30, 0.30], scored


# ── the score, and the bar it must beat ──────────────────────────────────────


@pytest.mark.anyio
async def test_a_later_candidate_that_scores_lower_is_not_admitted(space: Path) -> None:
    """🔴 Red before: every candidate was held to the floor the search started from, so the second
    candidate here (0.67, after a 1.00) was admitted and became the winner the search filed."""
    answers = [_proposal(3), _proposal(2)]
    ctl, _proposer = await _run(space, _inputs(space, max_iterations=2), answers)

    assert [(r["outcome"], round(r["score"], 2)) for r in _ledger(space)] == [
        ("admitted", 1.0),
        ("not_best_ever", 0.67),
    ], _steps(ctl)
    assert ctl.run.status == RunStatus.COMPLETE, (ctl.run.error_message, _steps(ctl))
    filed = _filed(ctl)
    assert filed.get("filed") is True, filed
    (proposal,) = _pending()
    manifest = proposal.change_manifest
    fix = manifest.get("targeted_fix") if isinstance(manifest, dict) else manifest.targeted_fix
    assert fix == _proposal(3)["ops"], "the search filed a candidate other than its best"
    assert "Candidate 1 scored 1.00" in proposal.body, proposal.body


@pytest.mark.anyio
async def test_a_refiners_claimed_score_is_not_the_candidates_score(space: Path) -> None:
    """🔴 Red before: the proposer's ``score`` was read as the candidate's score, so a candidate
    that claimed 0.99 was admitted whatever it was.

    The proposer claims 0.99; the target's runs say the candidate passes one of three, below the
    0.5 threshold, so it is not admitted and the ledger holds what was measured."""
    answers = [_proposal(1, claimed=0.99)]
    inputs = _inputs(space, suite_threshold=0.5, max_iterations=1)
    ctl, proposer = await _run(space, inputs, answers)

    assert [round(r["score"], 2) for r in _ledger(space)] == [0.33], _steps(ctl)
    (row,) = _ledger(space)
    assert row["outcome"] == optimize.CandidateOutcome.BELOW_SUITE_THRESHOLD.value
    assert ctl.run.status == RunStatus.COMPLETE, (ctl.run.error_message, _steps(ctl))
    assert _filed(ctl)["filed"] is False
    assert '"score"' not in proposer.proposals[0], "the proposer is still asked for a score"


# ── the cycle limit ──────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_max_iterations_is_the_number_of_candidates_the_search_tries(space: Path) -> None:
    """🔴 Red before: the loop stopped at 12 cycles whatever ``max_iterations`` said, and a
    search asking for 13 was handed to a person after 12, with nothing filed."""
    answers = [_proposal(1, fingerprint=f"diagnosis-{i}") for i in range(20)]
    ctl, proposer = await _run(space, _inputs(space, max_iterations=13), answers)

    assert _halt(ctl) == optimize.HaltReason.ITERATIONS_EXHAUSTED.value, (
        _halt(ctl),
        ctl.run.status,
        len(proposer.proposals),
    )
    assert len(proposer.proposals) == 13
    assert len(_ledger(space)) == 13
    assert ctl.run.status == RunStatus.COMPLETE, (ctl.run.error_message, _steps(ctl))
    assert "it tried 13 candidates, the most its max_iterations allows" in _filed(ctl)["summary"]


# ── the positive control: a search within its budget files its winner ────────


@pytest.mark.anyio
async def test_a_search_within_its_budget_whose_candidates_improve_files_its_winner(
    space: Path,
) -> None:
    """The control for the budget: a search its budget never stops climbs on measured scores and
    files its best candidate as a proposal, citing what it scored on the target's own runs.

    🔴 Red before as well: a model step filed the winner, so nothing was filed unless the model
    called its proposal tool, and the runs that tool must cite were never handed to it."""
    answers = [_proposal(1), _proposal(2), _proposal(3)]
    ctl, proposer = await _run(space, _inputs(space, max_iterations=3), answers)

    assert [(r["outcome"], round(r["score"], 2)) for r in _ledger(space)] == [
        ("admitted", 0.33),
        ("admitted", 0.67),
        ("admitted", 1.0),
    ], _steps(ctl)
    filed = _filed(ctl)
    assert filed.get("filed") is True, (filed, _steps(ctl))
    pending = _pending()
    assert len(pending) == 1, pending
    (proposal,) = pending
    assert proposal.id == filed["proposal_id"] and proposal.target == TARGET
    manifest = proposal.change_manifest
    fix = manifest.get("targeted_fix") if isinstance(manifest, dict) else manifest.targeted_fix
    assert fix == _proposal(3)["ops"]
    assert "Candidate 3 scored 1.00" in proposal.body, proposal.body
    assert sorted(proposal.evidence_refs) == sorted(
        r.id for r in store.list_runs(workflow_name=TARGET)[0]
    )
    assert f"It filed candidate 3 as proposal {proposal.id}" in filed["summary"]
    assert ctl.run.status == RunStatus.COMPLETE, (ctl.run.error_message, _steps(ctl))

    # The proposer is handed what earlier candidates tried, each one's ledger row and raw diff,
    # by the template's own step: the refiner's tools read evidence and file proposals, and
    # cannot open a file, and nothing widened them.
    first, second, _third = proposer.proposals
    assert ".experience/index.json" not in first + second, "a prompt still asks for a file read"
    assert "@@ PASSES=1 @@" not in first
    assert "@@ PASSES=1 @@" in second, "the raw prior diff did not reach the proposer"
    assert "passes-1" in second
    assert {kw["agent"] for kw in proposer.spawns} == {TEMPLATE_REFINER_AGENT_NAME}
    assert TEMPLATE_REFINER_TOOLS == ["refiner_evidence", "propose_template_diff"]


# ── the preflight refuses a search it cannot run honestly ────────────────────


@pytest.mark.anyio
async def test_a_target_without_enough_finished_runs_is_refused_at_preflight(
    space: Path,
) -> None:
    """🔴 Red before: a target with nothing to score against started a search that could only ever
    read its scores off the proposer."""
    inputs = _inputs(space, target_template="design-project")
    ctl, proposer = await _run(space, inputs, [_proposal(1)])

    assert proposer.spawns == [], "a model step ran after the refusal"
    assert ctl.run.status == RunStatus.FAILED
    assert "optimize-harness cannot score a candidate for design-project" in (
        ctl.run.error_message or ""
    ), ctl.run.error_message
    assert not (space / "sandbox").exists(), "the preflight wrote before it refused"


@pytest.mark.anyio
async def test_a_search_started_with_no_budget_stops_at_its_preflight_and_says_why(
    space: Path,
) -> None:
    """A preflight that refuses ends the run with its reason, and nothing after it runs."""
    ctl, proposer = await _run(space, _inputs(space, budget_usd=0), [])

    assert ctl.run.status == RunStatus.FAILED
    reason = (
        "optimize-harness refuses to search without a positive `budget_usd`: an unbudgeted "
        "search over model calls has no ceiling at all, and 0 means UNLIMITED to the guardrails "
        "Budget it would be handed"
    )
    preflight_label = "Refuse or report, before the first model call"
    assert ctl.run.error_message == (
        f"“{preflight_label}” failed: {reason}, so nothing after it ran."
    )
    preflight = ctl.instances["root.children[0]"]
    assert preflight.failure is not None and preflight.failure.cause_plain == reason
    assert proposer.spawns == [], "a model step ran after the refusal"
    assert not (space / "sandbox").exists(), "a step after the refusal ran"


def test_the_fewest_runs_a_score_rests_on_is_the_evidence_a_filed_diff_needs() -> None:
    """A winner scored on fewer runs than the proposal queue's evidence floor could never be
    filed, so the two numbers are one rule."""
    from personalclaw.evals import candidate_score
    from personalclaw.learning.refiner import MIN_RUNS_FOR_EVIDENCE

    assert candidate_score.MIN_CASES == MIN_RUNS_FOR_EVIDENCE
