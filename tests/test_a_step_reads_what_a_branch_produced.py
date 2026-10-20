"""A step after a branch reads what the case the branch took produced.

🔴 Before: a branch recorded only its routing, `{"case": label}`, and nothing else a step after it
could read. So a step could never reach what the branch's taken case had done: naming that case's
own step does not work either, since a reader of a case that was not taken is skipped. Two shipped
templates read the branch's output as the taken case's result: deep-research's sweep was told how
deep to search with `{"case": "survey"}`, no angles, breadth or depth in it, and
produce-and-audit's produce step was handed `{"case": "standard"}` as everything the gather step
had found.

Now a branch's output keeps its routing and, once the taken case has ended in success, carries what
that case produced: `{"case": label, "produced": …}`, the case's output read the way a loop's cycle
is (one step's output as it is, a case of several steps as their outputs layered in order). It is
kept with the branch's step, so a resumed run reads the same value, and a step reading the branch
consumes every step inside its cases, so a rewind of one re-runs the reader.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.checkpoints import revert_node
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import (
    InstanceState,
    Node,
    NodeInstance,
    NodeKind,
    RunStatus,
    WorkflowRun,
    spec_path,
    walk,
)
from personalclaw.workflows.mutations import binding_closure

pytestmark = pytest.mark.anyio

RUN_TIMEOUT = 60.0

SMALL = {"angles": ["by-content"], "depth": 1}
LARGE = {"angles": ["by-content", "by-time"], "depth": 3}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home / "leases")
    return home


def _reader(node_id: str, expr: Any) -> dict[str, Any]:
    return {"kind": "transform", "id": node_id, "config": {"expr": expr}}


def _routed(*after: dict[str, Any], cases: dict[str, Any] | None = None) -> dict[str, Any]:
    """`pick` says which case to take, `route` takes it, and the steps `after` read the branch."""
    return {
        "name": "reads-a-branch",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [
                _reader("pick", {"tier": "{{inputs.k}}"}),
                {
                    "kind": "branch",
                    "id": "route",
                    "config": {"on": "{{nodes.pick.output.tier}}"},
                    "cases": cases
                    or {
                        "small": _reader("shape_small", SMALL),
                        "large": _reader("shape_large", LARGE),
                    },
                },
                *after,
            ],
        },
    }


async def _run(spec: dict[str, Any], inputs: dict[str, Any]) -> tuple[RunController, RunStatus]:
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs=dict(inputs)))
    store.write_spec(run.id, spec)
    ctl = RunController(run, spec, services=EngineServices())
    return ctl, await ctl.run_to_completion(timeout=20)


def _state_of(ctl: RunController, node_id: str) -> InstanceState:
    nodes = dict(walk(ctl.root))
    states = [
        inst.state
        for path, inst in ctl.instances.items()
        if (node := nodes.get(spec_path(path))) is not None and node.id == node_id
    ]
    assert len(states) == 1, (node_id, states)
    return states[0]


# ── the engine: what a step after a branch reads ─────────────────────────────


@pytest.mark.parametrize(("k", "expected"), [("small", SMALL), ("large", LARGE)])
async def test_a_step_after_a_branch_reads_what_its_taken_case_produced(
    k: str, expected: dict[str, Any]
) -> None:
    """Both cases, so the reader cannot pass by reading whichever case happens to come first.
    🔴 Red before: the whole read was `{"case": k}` and the field read failed "unresolved
    reference at 'produced'"."""
    spec = _routed(
        _reader("whole", "{{nodes.route.output}}"),
        _reader("routed", "{{nodes.route.output.case}}"),
        _reader("depth", "{{nodes.route.output.produced.depth}}"),
        _reader(
            "said",
            "Sweep {{nodes.route.output.produced.angles}} to depth "
            "{{nodes.route.output.produced.depth}}",
        ),
    )
    ctl, status = await _run(spec, {"k": k})

    assert status == RunStatus.COMPLETE, ctl.run.error_message
    assert ctl._outputs["whole"] == {"case": k, "produced": expected}
    assert ctl._outputs["routed"] == k, "the routing is still there to read"
    assert ctl._outputs["depth"] == expected["depth"], "one reference keeps its type"
    assert ctl._outputs["said"] == (
        f"Sweep {json.dumps(expected['angles'])} to depth {expected['depth']}"
    )


async def test_a_case_of_several_steps_hands_on_what_each_of_them_produced() -> None:
    """A case that is a sequence is read the way a loop's cycle is: each step's output layered
    in order, a later step winning a key both carry."""
    cases = {
        "small": {
            "kind": "sequence",
            "id": "gather",
            "children": [
                _reader("look", {"found": "two jobs share a lock", "note": "first look"}),
                _reader("check", {"checked": True, "note": "checked twice"}),
            ],
        },
        "large": _reader("unused", {"found": "never"}),
    }
    spec = _routed(_reader("read", "{{nodes.route.output.produced}}"), cases=cases)
    ctl, status = await _run(spec, {"k": "small"})

    assert status == RunStatus.COMPLETE, ctl.run.error_message
    assert ctl._outputs["read"] == {
        "found": "two jobs share a lock",
        "checked": True,
        "note": "checked twice",
    }


async def test_the_branch_output_is_kept_with_its_step_and_read_back_on_resume() -> None:
    """Recorded as any step's output is, on the branch's own step, so the run page shows it and a
    run that restarts reads the same value. 🔴 Red before: the branch's step held only
    `{"case": "large"}`."""
    ctl, status = await _run(_routed(_reader("read", "{{nodes.route.output}}")), {"k": "large"})
    assert status == RunStatus.COMPLETE, ctl.run.error_message

    record = {"case": "large", "produced": LARGE}
    assert store.read_output(ctl.run.id, "root.children[1]") == record

    resumed = RunController(store.get(ctl.run.id), ctl.spec, services=EngineServices())
    assert resumed._outputs["route"] == record


async def test_a_case_that_fails_hands_on_nothing_and_its_reader_does_not_run() -> None:
    """A taken case that failed produced no result, so the step that reads the branch is
    skipped, never run on a value the case did not produce, and the failure stays the case's."""
    cases = {
        "small": _reader("broken", "{{inputs.missing}}"),
        "large": _reader("unused", {"found": "never"}),
    }
    spec = _routed(_reader("read", "{{nodes.route.output.produced}}"), cases=cases)
    ctl, status = await _run(spec, {"k": "small"})

    assert status == RunStatus.FAILED
    assert _state_of(ctl, "broken") == InstanceState.FAILED
    assert _state_of(ctl, "read") == InstanceState.SKIPPED


async def test_an_ordinary_branch_that_nothing_reads_still_routes() -> None:
    """Positive control: the untaken case is skipped and the taken one runs, as before."""
    ctl, status = await _run(_routed(), {"k": "small"})

    assert status == RunStatus.COMPLETE, ctl.run.error_message
    assert _state_of(ctl, "shape_small") == InstanceState.DONE
    assert _state_of(ctl, "shape_large") == InstanceState.SKIPPED


def test_a_step_reading_a_branch_consumes_the_steps_inside_its_cases() -> None:
    """A rewind of a step in a case re-runs what read the branch, and a revert of that step is
    refused once the reader has used it. 🔴 Red before: nothing consumed the case's steps, so a
    rewind left the reader holding the old result and a revert went through under it."""
    spec = _routed(_reader("read", "{{nodes.route.output.produced.depth}}"))
    root = Node.from_dict(spec["root"])

    assert "read" in binding_closure(root, {"shape_large"})
    assert "read" in binding_closure(root, {"shape_small"})
    # The branch's own routing does not depend on its cases, so it is not re-run by them.
    assert "route" not in binding_closure(root, {"shape_large"})

    instances = {
        path: NodeInstance(path=path, state=InstanceState.DONE)
        for path in (
            "root.children[0]",
            "root.children[1]",
            "root.children[1].cases[large]",
            "root.children[2]",
        )
    }
    paths, conflict = revert_node(root, instances, "shape_large")
    assert paths == []
    assert conflict is not None and conflict.dependents == ["read"]


# ── the shipped templates, run with a scripted model ─────────────────────────


class _Done:
    def __init__(self, agent_id: str, result: str) -> None:
        self.id = agent_id
        self.done = True
        self.error = ""
        self.result = result
        self.reaped = False
        self.agent = ""


class _Stages:
    """Each spawned stage finishes at once with its step's scripted answer; every task is kept,
    by the step's id."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.tasks: dict[str, list[str]] = {}
        self._done: dict[str, _Done] = {}

    def spawn(self, **kw: Any) -> _Done:
        node_id = str(kw["parent_session_key"]).rsplit(":", 1)[-1]
        task = str(kw["task"])
        self.tasks.setdefault(node_id, []).append(task)
        answer = self.answers[node_id]
        if callable(answer):
            answer = answer(task)
        done = _Done(
            f"sub{len(self._done) + 1}", answer if isinstance(answer, str) else json.dumps(answer)
        )
        self._done[done.id] = done
        return done

    def get(self, agent_id: str) -> _Done | None:
        return self._done.get(agent_id)


class _Calls:
    """The one-call (`infer`) steps' model, answering each by what its prompt asks."""

    def __init__(self, answers: list[tuple[str, Any]]) -> None:
        self.answers = answers
        self.prompts: list[str] = []

    async def __call__(self, prompt: str, *, use_case: str = "", output_type: Any = None) -> str:
        self.prompts.append(prompt)
        for opening, answer in self.answers:
            if opening in prompt:
                return json.dumps(answer)
        raise AssertionError(f"no scripted answer for: {prompt[:120]}")


async def _drive(
    name: str, inputs: dict[str, Any], stages: _Stages, calls: _Calls, workspace: Path
) -> RunController:
    template = read_template(name)
    assert template is not None
    spec = template.to_dict()
    run = store.create(WorkflowRun(id="", workflow_name=name, mode="background", inputs=inputs))
    store.write_spec(run.id, spec)
    ctl = RunController(
        run, spec, services=EngineServices(subagents=stages, completion=calls, cwd=str(workspace))
    )
    await ctl.start()
    deadline = asyncio.get_running_loop().time() + RUN_TIMEOUT
    while not ctl.run.is_terminal:
        assert asyncio.get_running_loop().time() < deadline, (name, ctl.run.status)
        await asyncio.sleep(0.05)
    return ctl


def _case_outputs(name: str, branch_id: str) -> dict[str, Any]:
    """Each case label of a shipped branch → the `expr` its shaping step returns, read from the
    template itself rather than restated here."""
    template = read_template(name)
    assert template is not None
    branch = next(n for _p, n in walk(template.root) if n.id == branch_id)
    assert branch.kind is NodeKind.BRANCH
    return {label: dict(case.config["expr"]) for label, case in branch.cases.items()}


#: The run's own state document, which deep-research hands each step on a line of its own.
_STATE_PATH = re.compile(r"^(/\S+/RESEARCH\.md)$", re.MULTILINE)


def _sweep(task: str) -> dict[str, Any]:
    """A round of research that does what the sweep is told: it keeps the run's state document."""
    found = _STATE_PATH.search(task)
    if found:
        state = Path(found.group(1))
        state.parent.mkdir(parents=True, exist_ok=True)
        with state.open("a", encoding="utf-8") as fh:
            fh.write("## Sources read\n- https://example.invalid/a\n")
    return {
        "summary": "read the release notes",
        "new_findings_count": 0,
        "sources_read": ["https://example.invalid/a"],
        "open_subtopics": [],
    }


_JUDGED = {
    "reasoning": "re-fetched the cited source; it says what the report claims",
    "verdict": "PASS",
    "scores": {
        "new sources were fetched and read this round": 2,
        "every claim added cites the source it came from": 2,
    },
    "evidence_refs": ["RESEARCH.md", "https://example.invalid/a"],
    "proof": "the fetched page matched the quoted line",
    "shortfalls": [],
    "cannot_judge": "",
}


@pytest.mark.parametrize("tier", ["lookup", "survey", "investigation"])
async def test_deep_research_sweeps_with_the_shape_its_triage_chose(
    tier: str, tmp_path: Path
) -> None:
    """The sweep is told the angles, breadth and depth of the tier triage chose, as that tier's
    own shaping step produced them. 🔴 Red before: the sweep was told `{"case": "<tier>"}`."""
    shape = _case_outputs("deep-research", "entry")[tier]
    stages = _Stages(
        {
            "sweep": _sweep,
            "judge": _JUDGED,
            "synthesize": {
                "answer": "the pool size changed",
                "confidence": "medium",
                "unknowns": [],
            },
        }
    )
    calls = _Calls([("Classify what this question needs", {"tier": tier, "why": "scripted"})])
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inputs = {
        "question": "why did the p99 latency double after the last release",
        "exit_condition": "every claim cites a fetched source",
        "output_manner": "",
        "source_budget": 0,
        "continue_from": "",
    }

    ctl = await _drive("deep-research", inputs, stages, calls, workspace)

    sweeps = stages.tasks.get("sweep") or []
    assert sweeps, f"the sweep never ran ({ctl.run.status.value}: {ctl.run.error_message})"
    first = sweeps[0]
    assert f"run the angles {json.dumps(shape['angles'])}" in first, first
    assert f"advance {shape['breadth']} open subtopics" in first, first
    assert f"fetch and read {shape['depth']} sources per subtopic" in first, first
    assert '"case"' not in first, "the sweep was handed the branch's routing"
    assert ctl.run.status == RunStatus.COMPLETE, ctl.run.error_message


#: The produce-and-audit gather cases each find something only they could have found.
_LOOKED_AROUND = "The scheduler runs four jobs, and two of them share one lock file."
_SWEPT_CLAIM = "Job C holds the lock while job B waits for it."


@pytest.mark.parametrize(
    ("tier", "gathered"),
    [
        ("light", "Triaged light — writing directly from the subject."),
        ("standard", _LOOKED_AROUND),
        ("deep", _SWEPT_CLAIM),
    ],
)
async def test_produce_and_audit_produces_from_what_its_gather_step_found(
    tier: str, gathered: str, tmp_path: Path
) -> None:
    """The produce step is handed what the gather case triage chose found: the light case's
    note, the look-around's findings, or what the research sweep extracted. 🔴 Red before: it
    was handed `{"case": "<tier>"}` as "What was gathered"."""
    sweep_found = "https://example.invalid/scheduler: describes the four jobs and their locks"
    stages = _Stages(
        {
            "look_around": _LOOKED_AROUND,
            "sweep_by_content": sweep_found,
            "sweep_by_entity": sweep_found,
            "sweep_by_time": sweep_found,
            "produce": "A one-page plan for moving the four jobs to the new scheduler.",
        }
    )
    calls = _Calls(
        [
            ("Classify how much work this subject warrants", {"tier": tier, "why": "scripted"}),
            (
                "Extract what this source actually says",
                {
                    "relevant": True,
                    "claims": [_SWEPT_CLAIM],
                    "quotes": ["job C acquires the lock first"],
                    "gaps": [],
                },
            ),
            ("Audit the artifact below", {"findings": [], "verdict": "pass"}),
        ]
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    inputs = {
        "subject": "a plan for moving the cron jobs to the new scheduler",
        "artifact_kind": "plan",
        "acceptance": "names every job",
    }

    ctl = await _drive("produce-and-audit", inputs, stages, calls, workspace)

    produced = stages.tasks.get("produce") or []
    assert len(produced) == 1, f"produce ran {len(produced)} times ({ctl.run.error_message})"
    assert gathered in produced[0], produced[0]
    assert '"case"' not in produced[0], "produce was handed the branch's routing"
    assert ctl.run.status == RunStatus.COMPLETE, ctl.run.error_message
