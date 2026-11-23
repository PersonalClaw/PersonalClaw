"""audit-sweep reads what it audits before judging it, and a ruling rests on lines it cites.

🔴 The defect (Backlog: "audit-sweep's verifier never sees the code it is checking, so its verdict
is a guess"). The template invites a path target ("a path, a module, a document, a config"; its
own example audits `src/personalclaw/auth/`), and every finder and every verifier is an `infer`
step, one model call that cannot open a file. With a path, no step before the fix read a file:
the finders found issues in a path string, and each verifier, told to default to refuted when
uncertain and shown only the path, ruled by guess, refuting a round's findings or keeping them
depending on how specific they sounded.

Now a read-only first step (`read_target`) returns the target's text, each file under its path
and each line after its number, bounded; the finders and every verifier are handed it, fenced as
the untrusted content it is, and the completeness critic is handed what was read and left out. A
verifier is asked to quote, in `cited`, the lines it judged by, and to answer `refuted: null`
when what it was shown does not hold them; a ruling that cites nothing is kept as unverified,
in the panel (`| if_empty('cited', 'refuted', null)`) and on the run's review list alike.

Driven through the real controller; the model call and the subagent manager are the only fakes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.bindings import BindingContext, resolve
from personalclaw.workflows.blocks import resolve_spec
from personalclaw.workflows.bundled_defs import bundled_root
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import stage_capability
from personalclaw.workflows.macros import expand_spec
from personalclaw.workflows.models import Node, RunStatus, WorkflowRun, spec_path, walk
from personalclaw.workflows.tick import Limits

pytestmark = pytest.mark.anyio

LANES = Limits(lanes={"llm": 8, "io": 2, "compute": 64})
TARGET = "src/app/"
#: What the read step returns: the target's text, each line after its number.
MATERIAL = "== src/app/store.py ==\n3  lock.acquire()\n9  os.remove(path)  # MATERIAL-MARKER"
FINDINGS = [
    {"title": "F-CITED", "severity": "high", "location": "store.py:9", "problem": "drops a file"},
    {"title": "F-GUESSED", "severity": "high", "location": "store.py:3", "problem": "keeps a lock"},
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _runs_in_this_tests_folder(tmp_path, monkeypatch):
    home = tmp_path / "runs"
    home.mkdir()
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    return home


class _Info:
    def __init__(self, agent_id: str, task: str) -> None:
        self.id = agent_id
        self.task = task
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False


class _Subagents:
    """The subagent manager: the read step returns MATERIAL, any other stage "fixed"."""

    def __init__(self) -> None:
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"s{len(self.infos)}", str(kw.get("task") or ""))
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None and not info.done:
            info.done = True
            reads = info.task.startswith("Gather what this sweep audits")
            info.result = (
                json.dumps({"material": MATERIAL, "read": ["src/app/store.py"]})
                if reads
                else "fixed"
            )
        return info

    def tasks(self, opening: str) -> list[str]:
        return [i.task for i in self.infos.values() if i.task.startswith(opening)]


async def _swept(refuted_by: dict[str, dict[str, Any]]) -> tuple[RunController, list[str]]:
    """audit-sweep over TARGET, `fix` off: its correctness finder files FINDINGS in round 1, and
    each finding's verifier answers as *refuted_by* says for its title."""
    spec = resolve_spec(
        expand_spec(json.loads((bundled_root() / "audit-sweep" / "workflow.json").read_text()))
    )
    run = store.create(
        WorkflowRun(
            id="", workflow_name="audit-sweep", inputs={"target": TARGET, "focus": "", "fix": False}
        )
    )
    store.write_spec(run.id, spec)
    asked: list[str] = []
    first = {"correctness": True}

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        asked.append(prompt)
        if prompt.startswith("Try to REFUTE"):
            finding = prompt.split("Finding:", 1)[1].split("What the finding is about:", 1)[0]
            title = next(t for t in refuted_by if t in finding)
            return json.dumps(refuted_by[title])
        if "CORRECTNESS" in prompt and first["correctness"]:
            first["correctness"] = False
            return json.dumps({"findings": FINDINGS})
        return json.dumps(
            {"findings": [], "new_this_round": [], "new_findings": [], "uncovered": []}
        )

    ctl = RunController(
        run, spec, services=EngineServices(completion=fn, subagents=_Subagents(), lane_limits=LANES)
    )
    assert await ctl.run_to_completion(timeout=120) == RunStatus.COMPLETE, ctl.run.error_message
    return ctl, asked


def _in_round_one(ctl: RunController, node_id: str) -> Any:
    """What *node_id* produced in the sweep's first round (a later round's overwrites its id)."""
    paths = {p for p, n in walk(Node.from_dict(ctl.spec["root"])) if n.id == node_id}
    (at,) = [k for k in ctl.instances if spec_path(k) in paths and ".body@0." in k]
    return store.read_output(ctl.run.id, at)


CITES = {"refuted": True, "refute_reason": "handled by the caller", "cited": "9  os.remove(path)"}
GUESSES = {"refuted": True, "refute_reason": "probably handled", "cited": ""}


async def test_every_finder_and_verifier_judges_the_targets_text_not_its_path():
    """🔴 Before: with a path target, each finder and verifier prompt held only the path."""
    _ctl, asked = await _swept({"F-CITED": CITES, "F-GUESSED": CITES})
    finders = [p for p in asked if p.startswith("Find ")]
    refuters = [p for p in asked if p.startswith("Try to REFUTE")]
    assert len(finders) >= 3 and len(refuters) == len(FINDINGS), (len(finders), len(refuters))
    for prompt in [*finders, *refuters]:
        assert "MATERIAL-MARKER" in prompt, prompt[-800:]
        # Fenced: the target's text is content to judge, never instructions to follow.
        held = prompt.split("MATERIAL-MARKER")[0].rsplit("<untrusted_content", 1)
        assert len(held) == 2 and "</untrusted_content>" not in held[1], prompt[-800:]
    critic, *_later = [p for p in asked if p.startswith("What did this round MISS?")]
    assert "src/app/store.py" in critic.split("left out:")[-1], critic[-800:]


async def test_the_read_step_reads_only():
    spec = json.loads((bundled_root() / "audit-sweep" / "workflow.json").read_text())
    (reader,) = [c for c in spec["root"]["children"] if c.get("id") == "read_target"]
    assert reader["kind"] == "stage" and stage_capability(reader["config"]) == "research"
    assert spec["root"]["children"].index(reader) == 0, "it reads before any round judges"


async def test_a_ruling_that_cites_nothing_is_kept_as_unverified():
    """🔴 Before: F-GUESSED's verifier refuted it without quoting a line, and it was dropped."""
    from personalclaw.workflows.review_service import findings_for

    ctl, asked = await _swept({"F-CITED": CITES, "F-GUESSED": GUESSES})
    assert any("quote in `cited`" in p for p in asked if p.startswith("Try to REFUTE"))
    confirmed = _in_round_one(ctl, "verify_confirmed")
    titles = {key: [f["title"] for f in confirmed[key]] for key in confirmed}
    assert titles["kept"] == ["F-GUESSED"], titles
    assert titles["unverified"] == ["F-GUESSED"], titles
    # The review list reads it the same way: Open, not Refuted; the cited refutation stands.
    rows = {f.location: f.status for f in findings_for(ctl.run.id)}
    assert rows == {"store.py:9": "Refuted", "store.py:3": "Open"}, rows


def test_if_empty_sets_a_field_where_another_is_empty_and_keeps_the_order():
    records = [
        {"t": 1, "cited": "line 3", "refuted": True},
        {"t": 2, "cited": "", "refuted": True},
        {"t": 3, "refuted": False},
        {"t": 4, "cited": None, "refuted": False},
        {"t": 5, "cited": [], "refuted": True},
        "not a record",
    ]
    ctx = BindingContext(node_outputs={"v": records})
    out = resolve("{{nodes.v.output | if_empty('cited', 'refuted', null)}}", ctx)
    assert [r["refuted"] if isinstance(r, dict) else r for r in out] == [
        True,
        None,
        None,
        None,
        None,
        "not a record",
    ]
    assert records[1]["refuted"] is True, "a copy, never an edit of the step's output"
    one = BindingContext(node_outputs={"v": {"cited": "", "refuted": True}})
    assert resolve("{{nodes.v.output | if_empty('cited', 'refuted', null)}}", one) == {
        "cited": "",
        "refuted": None,
    }
