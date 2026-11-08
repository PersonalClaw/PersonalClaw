"""A `foreach` records what its items produced, and `verify_panel` judges one finding per verifier.

**The fan-out's own output.** A step after a `foreach` reads `{{nodes.<foreach>.output}}`: the list
of what each item that gave a result produced, in item order, kept with the fan-out's step so a
rebuilt controller reads the same list. An item that failed or was skipped is left out, and the
fan-out's caption says how many and why, so "2 of 4 gave a result" is not read as "2 ran". Before
this a fan-out recorded nothing, validation refused the read, and the only way to its items' work
was the body's own step id, which names one item: the last to finish. That read is unchanged.

**`verify_panel`.** Each finding gets its own verifier; the panel's `_confirmed` keeps the findings
no verifier refuted, each marked with its verdict, and a finding whose verifier gave no usable
verdict (it failed, timed out, or answered outside its schema) is kept, marked unverified. Only an
explicit `refuted: true` drops one. The panel's `context` reaches every verifier's prompt.

**`audit-sweep`**, end to end: one verifier per finding the finders returned, each shown the
target, and only what was not refuted reaches the fix stage and the completeness critic.

Driven through the real controller; the model call and the subagent manager are the only fakes.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from personalclaw.workflows import journal as J
from personalclaw.workflows import store
from personalclaw.workflows.blocks import resolve_spec
from personalclaw.workflows.bundled_defs import bundled_root
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.macros import expand_spec
from personalclaw.workflows.models import InstanceState, NodeInstance, RunStatus, WorkflowRun
from personalclaw.workflows.tick import Limits
from personalclaw.workflows.validator import validate_spec

pytestmark = pytest.mark.anyio

ITEMS = ["I0", "I1", "I2", "I3"]
LANES = Limits(lanes={"llm": 8, "io": 2, "compute": 64})


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


def _run(spec: dict, inputs: dict | None = None) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], inputs=dict(inputs or {})))
    store.write_spec(run.id, spec)
    return run


def _fan_then_read(body: dict) -> dict:
    return {
        "name": "fan-then-read",
        "root": {
            "kind": "sequence",
            "id": "main",
            "children": [
                {"kind": "transform", "id": "src", "config": {"expr": {"items": ITEMS}}},
                {
                    "kind": "foreach",
                    "id": "fan",
                    "config": {"items": "{{nodes.src.output.items}}", "on_item_error": "skip"},
                    "body": body,
                },
                {
                    "kind": "transform",
                    "id": "after",
                    "config": {"expr": {"all": "{{nodes.fan.output}}"}},
                },
            ],
        },
    }


# ── the fan-out's own output ─────────────────────────────────────────────────


async def test_a_step_after_a_fan_out_reads_every_items_result_in_item_order() -> None:
    """The items finish last-first (I3 before I0); the list is in item order all the same, and a
    controller rebuilt from the run's state reads the same list."""
    spec = _fan_then_read(
        {
            "kind": "infer",
            "id": "work",
            "config": {"prompt": "W {{item}}", "schema": {"item": "string"}},
        }
    )
    assert validate_spec(spec, strict=True).ok, validate_spec(spec).summary()
    run = _run(spec)

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        item = prompt.split(" ", 1)[1].strip()
        await asyncio.sleep(0.02 * (len(ITEMS) - int(item[1:])))
        return json.dumps({"item": item})

    ctl = RunController(run, spec, services=EngineServices(completion=fn, lane_limits=LANES))
    assert await ctl.run_to_completion(timeout=60) == RunStatus.COMPLETE, ctl.run.error_message
    expected = [{"item": item} for item in ITEMS]
    assert ctl._outputs["after"] == {"all": expected}
    assert store.read_output(run.id, "root.children[1]") == expected

    rebuilt = RunController(store.get(run.id), store.read_spec(run.id), services=EngineServices())
    assert rebuilt._outputs["fan"] == expected, "a rebuilt controller lost the fan-out's list"


async def test_a_failed_and_a_skipped_item_are_left_out_and_counted() -> None:
    """Four items: I0 and I3 gave a result, I1 failed, I2 was skipped. The list holds the two
    results, and the fan-out's own caption counts the two left out and why."""
    spec = _fan_then_read({"kind": "transform", "id": "work", "config": {"expr": "{{item}}"}})
    run = _run(spec)
    instances: dict[str, NodeInstance] = {}

    def done(path: str, value: Any) -> None:
        instances[path] = NodeInstance(
            path=path, state=InstanceState.DONE, output_ref=store.write_output(run.id, path, value)
        )

    done("root.children[0]", {"items": ITEMS})
    done("root.children[1].body#0", "r0")
    instances["root.children[1].body#1"] = NodeInstance(
        path="root.children[1].body#1", state=InstanceState.FAILED
    )
    instances["root.children[1].body#2"] = NodeInstance(
        path="root.children[1].body#2", state=InstanceState.SKIPPED
    )
    done("root.children[1].body#3", "r3")
    store.write_state(run.id, instances)

    ctl = RunController(store.get(run.id), spec, services=EngineServices())
    await ctl.run_to_completion(timeout=30)
    assert ctl._outputs["after"] == {"all": ["r0", "r3"]}
    caption = ctl.instances["root.children[1]"].degraded_reason
    assert caption == "2 of 4 items gave a result: 1 failed, 1 skipped", caption


def test_a_field_read_of_a_fan_out_is_refused_and_names_the_read_that_works() -> None:
    """The output is a list: a field of it never resolves, and the refusal says how to take that
    field from each entry instead."""
    spec = _fan_then_read({"kind": "transform", "id": "work", "config": {"expr": "{{item}}"}})
    spec["root"]["children"][2]["config"]["expr"] = {"x": "{{nodes.fan.output.title}}"}
    result = validate_spec(spec)
    refused = [i for i in result.errors if i.code == "WF_UNSATISFIABLE_OUTPUT_REF"]
    assert refused and "map('title')" in refused[0].message, result.to_dict()


# ── verify_panel ─────────────────────────────────────────────────────────────

FINDINGS = [
    {"title": "F-KEEP"},
    {"title": "F-REFUTE"},
    {"title": "F-BROKEN"},
    {"title": "F-KEEP-2"},
]


def _panel_spec() -> dict:
    return resolve_spec(
        expand_spec(
            {
                "name": "panel",
                "root": {
                    "kind": "sequence",
                    "id": "main",
                    "children": [
                        {"kind": "transform", "id": "src", "config": {"expr": {"f": FINDINGS}}},
                        {
                            "macro": "verify_panel",
                            "id": "v",
                            "config": {
                                "findings": "{{nodes.src.output.f}}",
                                "context": "{{inputs.target}}",
                            },
                        },
                    ],
                },
            }
        )
    )


def _verifier(seen: list[str]):
    """Refutes F-REFUTE, answers F-BROKEN with something that is not JSON, upholds the rest."""

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        seen.append(prompt)
        finding = prompt.split("Finding:", 1)[1].split("What the finding is about:", 1)[0]
        if "F-REFUTE" in finding:
            return json.dumps({"refuted": True, "refute_reason": "already handled"})
        if "F-BROKEN" in finding:
            return "I could not decide."
        return json.dumps({"refuted": False, "refute_reason": "real"})

    return fn


async def _run_panel() -> tuple[list[str], dict[str, Any]]:
    """Runs the panel over FINDINGS; returns every verifier prompt and `_confirmed`'s output."""
    spec = _panel_spec()
    assert validate_spec(spec, strict=True).ok, validate_spec(spec).summary()
    run = _run(spec, {"target": "TARGET-MARKER"})
    seen: list[str] = []
    ctl = RunController(
        run, spec, services=EngineServices(completion=_verifier(seen), lane_limits=LANES)
    )
    assert await ctl.run_to_completion(timeout=60) == RunStatus.COMPLETE, ctl.run.error_message
    return [p for p in seen if "Try to REFUTE" in p], ctl._outputs["v_confirmed"]


def _titles(confirmed: dict[str, Any]) -> dict[str, list[str]]:
    return {key: [f["title"] for f in confirmed[key]] for key in confirmed}


async def test_each_finding_gets_its_own_verifier_shown_the_target() -> None:
    refuters, _ = await _run_panel()
    assert len(refuters) == len(FINDINGS), f"{len(refuters)} verifiers for {len(FINDINGS)}"
    assert all("TARGET-MARKER" in p for p in refuters), "a verifier was not shown the target"
    assert all(p.count('"title"') == 1 for p in refuters), "a verifier judged several findings"


async def test_only_an_explicitly_refuted_finding_is_dropped() -> None:
    _, confirmed = await _run_panel()
    titles = _titles(confirmed)
    assert "F-REFUTE" not in titles["kept"], titles
    assert titles["survived"] == ["F-KEEP", "F-KEEP-2"], titles


async def test_a_finding_whose_verifier_gave_no_usable_verdict_is_kept_marked_unverified() -> None:
    """F-BROKEN's verifier answers prose, not the schema: no verdict, so no evidence the finding
    is fake. It stays in `kept`, in item order, and is listed and marked as unverified."""
    _, confirmed = await _run_panel()
    titles = _titles(confirmed)
    assert titles["kept"] == ["F-KEEP", "F-BROKEN", "F-KEEP-2"], titles
    assert titles["unverified"] == ["F-BROKEN"], titles
    broken = confirmed["unverified"][0]
    assert broken["refuted"] is None and "no usable verdict" in broken["refute_reason"]


# ── audit-sweep end to end ───────────────────────────────────────────────────


class _Info:
    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False


class _Subagents:
    """Stands in for the subagent manager: every spawned stage finishes at its first lookup."""

    def __init__(self) -> None:
        self.spawns: list[dict[str, Any]] = []
        self.infos: dict[str, _Info] = {}

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"s{len(self.spawns)}")
        self.spawns.append(kw)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is not None:
            info.done, info.result = True, "fixed"
        return info


async def test_audit_sweep_verifies_each_finding_against_the_target_and_fixes_only_survivors() -> (
    None
):
    """Round 1's finders return four findings across two lenses. 🔴 Red before: one verifier was
    handed all of them at once as an object of lists, never saw the target, and the fix stage and
    the critic were handed the findings it refuted along with the rest."""
    spec = resolve_spec(
        expand_spec(json.loads((bundled_root() / "audit-sweep" / "workflow.json").read_text()))
    )
    run = _run(spec, {"target": "TARGET-MARKER", "focus": "", "fix": True})
    seen: list[str] = []
    verify = _verifier(seen)
    first = {"correctness": True, "safety": True}

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        if "Try to REFUTE" in prompt:
            return await verify(prompt)
        seen.append(prompt)
        if "CORRECTNESS" in prompt and first["correctness"]:
            first["correctness"] = False
            return json.dumps({"findings": FINDINGS[:3]})
        if "SAFETY" in prompt and first["safety"]:
            first["safety"] = False
            return json.dumps({"findings": FINDINGS[3:]})
        return json.dumps({"findings": [], "new_findings": [], "uncovered": []})

    subagents = _Subagents()
    ctl = RunController(
        run,
        spec,
        services=EngineServices(completion=fn, subagents=subagents, lane_limits=LANES),
    )
    await ctl.run_to_completion(timeout=120)

    refuters = [p for p in seen if "Try to REFUTE" in p]
    assert len(refuters) == len(FINDINGS), f"{len(refuters)} verifiers for {len(FINDINGS)}"
    assert all("TARGET-MARKER" in p for p in refuters), "a verifier was not shown the target"

    fix_task = subagents.spawns[0]["task"]
    critic = next(p for p in seen if "What did this round MISS" in p)
    for handed in (fix_task, critic):
        assert "F-REFUTE" not in handed, "a refuted finding reached a step after the panel"
        for title in ("F-KEEP", "F-BROKEN", "F-KEEP-2"):
            assert title in handed, f"{title} did not reach a step after the panel"
    assert '"lens": "safety"' in fix_task, "a finding lost the lens it came from"


#: Findings a reviewer would file: each with a severity, a location and a problem, so each is a
#: row on the run's review list.
REVIEWED = [
    {"title": "F-KEEP", "severity": "high", "location": "app.py:3", "problem": "keeps a lock"},
    {"title": "F-REFUTE", "severity": "high", "location": "app.py:9", "problem": "drops a file"},
    {"title": "F-BROKEN", "severity": "low", "location": "app.py:12", "problem": "slow loop"},
]


async def _swept(
    refuter_says: dict[str, Any], reviewed: list[dict[str, Any]] = REVIEWED
) -> tuple[str, list[str], list[str]]:
    """audit-sweep, `fix` on, over *reviewed* from its correctness finder, whose verifier answers
    F-REFUTE with *refuter_says*: the run's id, the prompts its critic was sent, and the tasks its
    fix step spawned."""
    spec = resolve_spec(
        expand_spec(json.loads((bundled_root() / "audit-sweep" / "workflow.json").read_text()))
    )
    run = _run(spec, {"target": "TARGET-MARKER", "focus": "", "fix": True})
    first = {"correctness": True}
    critic: list[str] = []

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        if "Try to REFUTE" in prompt:
            finding = prompt.split("Finding:", 1)[1]
            if "F-REFUTE" in finding:
                return json.dumps(refuter_says)
            return json.dumps({"refuted": False, "refute_reason": "real"})
        if "What did this round MISS" in prompt:
            critic.append(prompt)
        if "CORRECTNESS" in prompt and first["correctness"]:
            first["correctness"] = False
            return json.dumps({"findings": reviewed})
        return json.dumps({"findings": [], "new_findings": [], "uncovered": []})

    subagents = _Subagents()
    ctl = RunController(
        run, spec, services=EngineServices(completion=fn, subagents=subagents, lane_limits=LANES)
    )
    await ctl.run_to_completion(timeout=120)
    return run.id, critic, [str(s.get("task", "")) for s in subagents.spawns]


REFUTED = {"refuted": True, "refute_reason": "already handled"}
#: A refuter that refutes the finding and says it in its own words: another location, another
#: problem. Its verdict is on the finding it was given, whatever it calls it.
REWORDED = {**REFUTED, "location": "elsewhere.py:1", "problem": "a different problem"}


def _review_list(run_id: str) -> list[tuple[str, str]]:
    from personalclaw.workflows.review_service import findings_for

    return sorted((f.location, f.status) for f in findings_for(run_id))


@pytest.mark.parametrize("refuter_says", [REFUTED, REWORDED], ids=["as given", "reworded"])
async def test_the_review_list_says_which_findings_the_panel_refuted(refuter_says) -> None:
    """audit-sweep's finder files three findings and the panel refutes one. 🔴 Red before: the
    review list read three Open, the refuted one among them, with nothing saying it was refuted,
    and a match on the finding's words left it Open once its refuter reworded it."""
    from personalclaw.workflows.review_service import findings_for

    run_id, critic, fixes = await _swept(refuter_says)
    assert fixes, "the fix step spawned nothing"
    assert critic, "the critic was not reached"
    for prompt in [*critic, *fixes]:
        assert "drops a file" not in prompt and "a different problem" not in prompt, prompt
    rows = findings_for(run_id)
    assert sorted((f.problem, f.status) for f in rows) == [
        ("drops a file", "Refuted"),
        ("keeps a lock", "Open"),
        ("slow loop", "Open"),
    ], [(f.problem, f.location, f.status) for f in rows]
    refuted = next(f for f in rows if f.status == "Refuted")
    assert (refuted.location, refuted.refute_reason) == ("app.py:9", "already handled")


async def test_a_verifier_that_fails_its_schema_refutes_nothing() -> None:
    """A verdict with no reason fails the verifier's schema: the step did not succeed, so its
    answer is not bound. The finding goes on to the fix step, and its row reads Open."""
    run_id, _critic, fixes = await _swept({"refuted": True})
    assert fixes and any("drops a file" in task for task in fixes), fixes
    assert ("app.py:9", "Open") in _review_list(run_id), _review_list(run_id)


async def test_a_refuted_finding_that_quotes_a_credential_reads_refuted() -> None:
    """The ledger masks a credential the finding quotes, so the row's words are not the words its
    key was taken from: the verdict still names the row, by the key the row was written with."""
    quoting = [
        *(f for f in REVIEWED if f["title"] != "F-REFUTE"),
        {
            "title": "F-REFUTE",
            "severity": "high",
            "location": "app.py:9",
            "problem": "drops a file and logs the key sk-notarealfixture0123456789abcdefghij",
        },
    ]
    run_id, critic, fixes = await _swept(REFUTED, quoting)
    assert fixes, "the fix step spawned nothing"
    assert critic, "the critic was not reached"
    for prompt in [*critic, *fixes]:
        assert "drops a file" not in prompt, prompt
    from personalclaw.workflows.review_service import findings_for

    (row,) = [f for f in findings_for(run_id) if f.location == "app.py:9"]
    assert "[REDACTED" in row.problem, "the ledger kept the key: this case tests nothing"
    assert row.status == "Refuted", (row.problem, row.status)


FILED = {"findings": [{"severity": "high", "location": "app.py:3", "problem": "keeps a lock"}]}


@pytest.mark.parametrize("kind", ["transform", "infer", "carried"])
async def test_a_loop_that_files_the_same_findings_each_cycle_is_a_stall(kind) -> None:
    """Each cycle files them again under a stamp of its own (`review_key`), and the breaker reads
    the findings, not the stamp: the same findings four times over trip `identical_output`, also
    when a later step carries the stamped text on inside its own output."""
    infer = {"kind": "infer", "id": "review", "config": {"prompt": "Review it."}}
    if kind == "transform":
        body: dict[str, Any] = {"kind": "transform", "id": "review", "config": {"expr": FILED}}
    elif kind == "infer":
        body = infer
    else:
        carry = {
            "kind": "transform",
            "id": "carry",
            "config": {"expr": {"r": "{{nodes.review.output}}"}},
        }
        body = {"kind": "sequence", "id": "cyc", "children": [infer, carry]}

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        return json.dumps(FILED)

    loop = {"kind": "loop", "id": "again", "config": {"mode": "counted", "n": 4}, "body": body}
    spec = {"name": "reviews", "root": {"kind": "sequence", "id": "s", "children": [loop]}}
    run = _run(spec)
    ctl = RunController(run, spec, services=EngineServices(completion=fn, lane_limits=LANES))
    await ctl.run_to_completion(timeout=60)
    outcomes = [str(r.get("outcome", "")) for r in J.ledger(run.id) if r["kind"] == J.ITERATION]
    assert "breaker:identical_output" in outcomes, outcomes
    filed = {r["instance_path"] for r in J.ledger(run.id, kinds={J.REVIEW_FINDING})}
    assert len(filed) > 1, "no two cycles filed the findings: this tests nothing"


# ── a step that tolerates its failure does not fail its loop's cycle ─────────


async def test_a_tolerated_failure_does_not_fail_its_loops_cycle() -> None:
    """The panel's verifier declares `allow_failure`, and audit-sweep runs it inside a loop. Its
    failure says nothing about the cycle: the cycle that holds only that failure ends dry, and the
    loop completes. 🔴 Red if the cycle reads any FAILED step as its own failure: the loop is
    handed to a person as "3 of 3 cycles failed"."""
    spec = {
        "name": "tolerated",
        "root": {
            "kind": "loop",
            "id": "l",
            "config": {"mode": "until_dry", "streak": 1, "max_iterations": 3},
            "body": {
                "kind": "sequence",
                "id": "b",
                "children": [
                    {
                        "kind": "infer",
                        "id": "maybe",
                        "config": {"prompt": "x", "allow_failure": True, "schema": {"a": "string"}},
                    },
                    {"kind": "transform", "id": "out", "config": {"expr": {}}},
                ],
            },
        },
    }
    run = _run(spec)

    async def fn(prompt, *, use_case="background", output_type=None, model=""):
        return "not json"

    ctl = RunController(run, spec, services=EngineServices(completion=fn, lane_limits=LANES))
    status = await ctl.run_to_completion(timeout=30)
    assert ctl.instances["root.body@0.children[0]"].state == InstanceState.FAILED
    assert status == RunStatus.COMPLETE, ctl.run.error_message
