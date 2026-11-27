"""audit-sweep ends when a round finds nothing new, and each round looks past the ones before.

🔴 The defect (Backlog: "audit-sweep's until-dry loop can never end dry, so every run repeats the
same audit three times and ends escalated"). The sweep loop declared no `progress_field`, so the
engine judged each round by its last step's whole output, the completeness critic's
`{"new_findings": [...], "uncovered": [...]}`, and an object reads as dry only when it has no
keys: two empty lists counted as progress, and every run went to its cap of three rounds and
ended escalated. And no round read the one before: the finders read only the inputs, and the
critic's leads were never checked. The amnesiac-loop lint let it through because its body held a
`{{nodes.` read, though each was of a step earlier in the same round.

Now each round ends carrying what the sweep found so far and the critic's leads (`round_end`),
the finders read both through `{{last.output…}}`, the critic copies out which of this round's
findings are new, and the loop's `progress_field` is what the round added: two rounds that add
nothing end the run complete. The lint counts a `{{nodes.X}}` read as carried state only when X
is outside the loop or runs after the reader.

Driven through the bundled template's own controller, with every model call answered by a stub
that does what its prompt asks.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.template_lint import lint_template

FINDING = {"title": "The parser drops a trailing comma", "severity": "medium", "where": "parse()"}
LEAD = "the error path of parse() was never read"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)


class _Models:
    """Every model call of the sweep. The finders report one finding each round; the refuter
    lets it stand; the critic answers from `rounds`, one entry per round."""

    def __init__(self, rounds: list[dict[str, list[Any]]]) -> None:
        self.rounds = rounds
        self.asked: list[str] = []
        self.critic_turns = 0

    async def __call__(self, prompt: str, *, use_case: str = "", output_type=None, **_kw) -> str:
        self.asked.append(prompt)
        if prompt.startswith("Find "):
            return json.dumps({"findings": [FINDING]})
        if prompt.startswith("Try to REFUTE"):
            return json.dumps({"refuted": False, "refute_reason": "", "cited": "parse(): line 4"})
        if prompt.startswith("What did this round MISS?"):
            said = self.rounds[min(self.critic_turns, len(self.rounds) - 1)]
            self.critic_turns += 1
            return json.dumps(
                {
                    "new_this_round": said.get("new_this_round", []),
                    "new_findings": said.get("new_findings", []),
                    "uncovered": said.get("uncovered", []),
                }
            )
        raise AssertionError(f"an unexpected model call: {prompt[:120]!r}")

    def finder_prompts(self, round_index: int) -> list[str]:
        finders = [p for p in self.asked if p.startswith("Find ")]
        return finders[3 * round_index : 3 * round_index + 3]


class _Reader:
    """The subagent manager, for the one stage the sweep spawns with fixing off: its read step,
    which returns the target's text."""

    def __init__(self) -> None:
        self.infos: dict[str, SimpleNamespace] = {}

    def spawn(self, **kw: Any) -> SimpleNamespace:
        info = SimpleNamespace(id=f"r{len(self.infos)}", done=False, error="", reaped=False)
        info.result = json.dumps({"material": "def parse(text): ...", "read": ["parser.py"]})
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> SimpleNamespace | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done = True
        return info


def _sweep(models: _Models) -> tuple[WorkflowRun, list[dict[str, Any]]]:
    template = read_template("audit-sweep")
    assert template is not None
    spec = template.to_dict()
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="audit-sweep",
            inputs={"target": "the parser module", "focus": "", "fix": False},
        )
    )
    store.write_spec(run.id, spec)
    controller = RunController(
        run, spec, services=EngineServices(completion=models, subagents=_Reader())
    )
    asyncio.run(controller.run_to_completion(timeout=30.0))
    rows = J.ledger(run.id, kinds={J.ITERATION})
    return store.get(run.id), rows


def test_two_rounds_that_add_nothing_end_the_sweep_complete():
    """🔴 Before: three rounds whatever the critic said, and the run ended escalated."""
    models = _Models([{}, {}])
    run, rows = _sweep(models)
    assert run.status is RunStatus.COMPLETE, (run.status, run.error_message)
    assert models.critic_turns == 2, "two rounds, not the cap of three"
    assert [r.get("iteration") for r in rows] == [0, 1], rows


def test_a_round_that_adds_a_finding_or_a_lead_is_progress():
    models = _Models([{"new_this_round": [FINDING], "new_findings": [LEAD]}, {}, {}])
    run, _rows = _sweep(models)
    assert run.status is RunStatus.COMPLETE, (run.status, run.error_message)
    assert models.critic_turns == 3, "the first round added something, the next two nothing"


def test_the_next_round_reads_what_the_last_found_and_the_critics_leads():
    """🔴 Before: every round's finders read only the inputs."""
    models = _Models([{"new_this_round": [FINDING], "new_findings": [LEAD]}, {}, {}])
    _sweep(models)
    first, second = models.finder_prompts(0), models.finder_prompts(1)
    assert len(first) == 3 and len(second) == 3
    assert all(FINDING["title"] not in p and LEAD not in p for p in first), first[0][-600:]
    assert all(FINDING["title"] in p and LEAD in p for p in second), second[0][-600:]
    critics = [p for p in models.asked if p.startswith("What did this round MISS?")]
    assert FINDING["title"] in critics[1].split("What earlier rounds found:")[-1], critics[1][-500:]


def _loop(*body: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "x",
        "description": "x",
        "root": {
            "kind": "loop",
            "id": "rounds",
            "config": {"mode": "until_dry", "streak": 2, "max_iterations": 3},
            "body": {"kind": "sequence", "id": "round", "children": list(body)},
        },
    }


def _infer(node_id: str, prompt: str) -> dict[str, Any]:
    return {"kind": "infer", "id": node_id, "config": {"prompt": prompt}}


def _amnesiac(spec: dict[str, Any]) -> bool:
    return any(f.code == "WFL_AMNESIAC_LOOP" for f in lint_template(spec).findings)


def test_the_lint_reads_a_step_earlier_in_the_round_as_no_carried_state():
    """🔴 Before: any `{{nodes.` in the body passed, audit-sweep's own included."""
    same_round = _loop(_infer("find", "Find."), _infer("check", "Check {{nodes.find.output}}."))
    assert _amnesiac(same_round)
    after_the_reader = _loop(
        _infer("find", "Find past {{nodes.check.output}}."), _infer("check", "Check.")
    )
    assert not _amnesiac(after_the_reader), "it reads the previous round's check"
    outside = _loop(_infer("find", "Find in {{nodes.plan.output}}."))
    assert not _amnesiac(outside)
    last = _loop(_infer("find", "Find past {{last.output.found | filter}}."))
    assert not _amnesiac(last)


def test_the_bundled_audit_sweep_carries_its_rounds():
    template = read_template("audit-sweep")
    assert template is not None
    spec = template.to_dict()
    (sweep,) = [c for c in spec["root"]["children"] if c["kind"] == "loop"]
    assert sweep["config"]["progress_field"] == "new"
    assert not _amnesiac(spec)
