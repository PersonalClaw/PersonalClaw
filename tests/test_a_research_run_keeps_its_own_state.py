"""A deep-research run keeps its state in its own folder, and reads no other run's.

The round loop's state is one document, RESEARCH.md: each round reads it first, skips every source
under `## Sources read`, and writes it back. Its prompts named it by a bare relative path, and a
project-less run's steps work in the one shared workspace every session defaults to, so every such
run read and rewrote the same file: a weekly research run started from last week's report and was
told not to read last week's sources again, while its Document panel looked in the run's own
folder and found nothing, and its judge searched the whole home for the file every round.

So a run keeps the document in its own folder and hands its steps that one absolute path
(`{{run.document}}`). The sweep, the judge, the supervisor's check of the judge's pass and the
Document panel all read that file, and a run reads another run's only when it was started to
continue it, saying so in its inputs (`continue_from`): it then starts from a copy, and the run it
continued is left as it was.

The fake subagents below do to the state what each prompt says: the path a prompt names on a line
of its own, or else the bare `RESEARCH.md` in the folder the session works in, as a real session
does with a relative path.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import checkpoints, contracts, provisioning, run_cockpit, store
from personalclaw.workflows.bindings import BindingContext, BindingError, resolve
from personalclaw.workflows.bundled_defs import read_template
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import RunStatus, WorkflowRun

TEMPLATE = "deep-research"

#: The path a prompt hands its step for the run's state document, on a line of its own.
_STATE_PATH = re.compile(r"^(/\S+/RESEARCH\.md)$", re.MULTILINE)

#: Bounded, so a run that never ends costs seconds instead of hanging the suite.
RUN_TIMEOUT = 30.0


@pytest.fixture
def shared(tmp_path, monkeypatch) -> Path:
    """An isolated home, and the shared workspace a project-less run's sessions work in."""
    home = tmp_path / "home"
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: home)
    folder = tmp_path / "workspace"
    folder.mkdir()
    monkeypatch.setattr("personalclaw.config.loader.default_workspace_dir", lambda: str(folder))
    return folder


def _own_document(run_id: str) -> Path:
    """Where a run keeps its state: its own documents folder. Spelled out rather than asked of the
    code under test, so the expectation does not move with it."""
    return store.run_dir(run_id) / "documents" / "RESEARCH.md"


class _Info:
    def __init__(self, agent_id: str, prompt: str, cwd: str) -> None:
        self.id = agent_id
        self.prompt = prompt
        self.cwd = cwd
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""


def _role(prompt: str) -> str:
    if "Advance one round of deep research" in prompt:
        return "sweep"
    if "You are verifying research you did not do" in prompt:
        return "judge"
    return "synthesize"


class _Researcher:
    """The subagents of one deep-research run, each doing to the run's state what its prompt says.

    Every read is recorded as ``(role, path, what the file held)``, so a test can say which file a
    step was pointed at and what it found there.
    """

    def __init__(self, workspace: Path, source: str, *, synthesis_fails: bool = False) -> None:
        self.workspace = workspace
        self.source = source
        self.synthesis_fails = synthesis_fails
        self.infos: dict[str, _Info] = {}
        self.reads: list[tuple[str, str, str | None]] = []

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(
            f"sub{len(self.infos) + 1}", str(kw.get("task") or ""), str(kw.get("cwd") or "")
        )
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        info = self.infos.get(agent_id)
        if info is None or info.done:
            return info
        role = _role(info.prompt)
        named = _STATE_PATH.search(info.prompt)
        state = Path(named.group(1)) if named else Path(info.cwd or self.workspace) / "RESEARCH.md"
        before = state.read_text(encoding="utf-8") if state.is_file() else None
        self.reads.append((role, str(state), before))
        info.done = True
        if role == "sweep":
            state.parent.mkdir(parents=True, exist_ok=True)
            body = before or "# Research\n\n## Sources read\n"
            state.write_text(f"{body}- {self.source}\n", encoding="utf-8")
            payload: dict[str, Any] = {
                "summary": f"read {self.source}",
                "new_findings_count": 0,
                "sources_read": [self.source],
                "open_subtopics": [],
            }
        elif role == "judge":
            payload = {
                "reasoning": f"{state} cites {self.source}, and the page says what it claims",
                "verdict": "PASS",
                "scores": {
                    "new sources were fetched and read this round": 2,
                    "every claim added cites the source it came from": 2,
                },
                "evidence_refs": [self.source],
                "proof": f"web_fetch {self.source}",
                "shortfalls": [],
                "cannot_judge": "",
            }
        elif self.synthesis_fails:
            info.error = "the model could not be reached"
            return info
        else:
            state.write_text(f"{before or ''}\n## Answer\nFrom {self.source}.\n", encoding="utf-8")
            payload = {"answer": f"from {self.source}", "confidence": "medium", "unknowns": []}
        info.result = json.dumps(payload)
        return info

    def first(self, role: str) -> tuple[str, str | None]:
        path, before = next((p, b) for r, p, b in self.reads if r == role)
        return path, before


class _Triage:
    """The one `infer` node: the deepest arm, so the round loop runs."""

    async def __call__(self, prompt: str, *, use_case: str = "background", output_type: Any = None):
        return json.dumps({"tier": "investigation", "why": "the answer has to be assembled"})


def _spec() -> dict[str, Any]:
    template = read_template(TEMPLATE)
    assert template is not None, f"{TEMPLATE} did not load through the bundled provider"
    return template.to_dict()


def _create(question: str, *, continue_from: str = "") -> WorkflowRun:
    spec = _spec()
    inputs = {
        "question": question,
        "exit_condition": "every claim cites a fetched source",
        "output_manner": "",
        "source_budget": 0,
        "continue_from": continue_from,
    }
    run = store.create(WorkflowRun(id="", workflow_name=TEMPLATE, inputs=inputs))
    store.write_spec(run.id, spec)
    return run


def _drive(run: WorkflowRun, researcher: _Researcher) -> RunStatus:
    spec = store.read_spec(run.id)
    services = EngineServices(subagents=researcher, completion=_Triage())
    controller = RunController(run, spec, services=services)
    assert controller.services.cwd == "", "vacuity floor: these runs must be project-less"
    return asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))


def _research(shared: Path, question: str, source: str, **kw: Any) -> tuple[str, _Researcher]:
    run = _create(question, **kw)
    researcher = _Researcher(shared, source)
    assert _drive(run, researcher) is RunStatus.COMPLETE, store.get(run.id)
    return run.id, researcher


# ── two runs back to back ───────────────────────────────────────────────────


def test_the_second_run_starts_empty_and_leaves_the_first_as_it_was(shared):
    """🔴 Before: both runs worked on the shared workspace's RESEARCH.md, so the second run's first
    round found the first run's report and its sources, and then rewrote it."""
    first, _ = _research(shared, "what changed in httpx 0.28", "https://a.example/releases")
    kept = _own_document(first).read_text(encoding="utf-8")

    second, researcher = _research(shared, "what changed in httpx 0.29", "https://b.example/log")

    path, before = researcher.first("sweep")
    assert (
        before is None
    ), f"the second run's first round started from another run's state:\n{before}"
    assert path == str(_own_document(second))
    assert _own_document(first).read_text(encoding="utf-8") == kept, "the second run rewrote it"
    assert "https://b.example/log" not in kept
    # The shared workspace never held either run's state.
    assert not (shared / "RESEARCH.md").exists()


def test_every_round_of_a_run_reads_the_state_the_last_one_left(shared):
    """The control the test above needs: the state still carries from round to round, so "starts
    empty" is the run's own first round and not a state that is never read back."""
    run_id, researcher = _research(shared, "what changed in httpx 0.28", "https://a.example/x")

    sweeps = [(p, b) for r, p, b in researcher.reads if r == "sweep"]
    assert len(sweeps) >= 2, f"vacuity floor: the loop ran {len(sweeps)} round(s)"
    assert sweeps[0][1] is None
    assert all(p == str(_own_document(run_id)) for p, _ in sweeps)
    assert all("https://a.example/x" in (b or "") for _, b in sweeps[1:])


# ── the judge and the panel read the run's own file ─────────────────────────


def test_the_judge_reads_the_runs_own_file(shared):
    """🔴 Before: the judge was told only `RESEARCH.md`, so it read the shared workspace's copy (in
    the field, it first searched the whole home for one, every round)."""
    _research(shared, "what changed in httpx 0.28", "https://a.example/one")
    run_id, researcher = _research(shared, "what changed in httpx 0.29", "https://b.example/two")

    judged = [(p, b) for r, p, b in researcher.reads if r == "judge"]
    assert judged, "vacuity floor: no judge ran"
    for path, content in judged:
        assert path == str(_own_document(run_id))
        assert "https://b.example/two" in (content or "")
        assert "https://a.example/one" not in (content or "")


def test_the_panel_reads_the_runs_own_file(shared):
    """🔴 Before: the panel showed a copy of the shared workspace's file taken as each step
    settled, so it named another folder and, until a step settled, showed nothing."""
    run_id, _ = _research(shared, "what changed in httpx 0.28", "https://a.example/one")

    body = run_cockpit.run_deliverable(run_id)
    report = body["report"]

    assert report["present"] is True, report
    assert report["content"] == _own_document(run_id).read_text(encoding="utf-8")
    assert report["kept_from"] is None and report["kept_by"] is None, report
    assert body["roots"][0]["path"] == str(_own_document(run_id).parent)
    assert body["instructed"] is True
    assert body["continued_from"] is None


def test_the_panel_names_the_runs_own_folder_before_anything_is_written(shared):
    """Absent is named where the steps were told to write, not in a folder nobody uses."""
    run = _create("what changed in httpx 0.28")

    report = run_cockpit.run_deliverable(run.id)

    assert report["report"]["absent_reason"] == "not_written"
    assert report["roots"][0] == {
        "kind": "kept",
        "path": str(_own_document(run.id).parent),
        "exists": False,
    }


# ── a run continues another only when it says so ────────────────────────────


def test_a_continuation_starts_from_a_copy_of_its_parents_document(shared):
    """🔴 Before: there was no continuation, only one shared file every run wrote."""
    parent, _ = _research(shared, "what changed in httpx 0.28", "https://a.example/one")
    kept = _own_document(parent).read_text(encoding="utf-8")

    child, researcher = _research(
        shared, "what changed in httpx 0.28", "https://b.example/two", continue_from=parent
    )

    path, before = researcher.first("sweep")
    assert path == str(_own_document(child))
    assert before == kept, "the continuation did not start from its parent's document"
    assert _own_document(parent).read_text(encoding="utf-8") == kept, "the parent was rewritten"
    continued = {"run_id": parent, "name": "RESEARCH.md", "carried": True}
    assert (store.get(child).extra or {}).get("continued_from") == continued
    assert run_cockpit.run_deliverable(child)["continued_from"] == continued


def test_a_fork_says_it_continues_its_parent_and_its_retry_starts_from_the_parents_state(shared):
    """A retry is a fork, and it inherits the rounds its parent finished, so its next step must
    read the document those rounds left, from a copy in its own folder.

    🔴 Before: the fork's inputs said nothing, and its steps read the shared workspace."""
    run = _create("what changed in httpx 0.28")
    failing = _Researcher(shared, "https://a.example/one", synthesis_fails=True)
    assert _drive(run, failing) is RunStatus.FAILED
    kept = _own_document(run.id).read_text(encoding="utf-8")

    fork = checkpoints.fork_run(
        store.get(run.id), store.read_spec(run.id), store.read_state(run.id), now="now"
    )
    child = fork.child
    assert child.inputs.get("continue_from") == run.id

    retry = _Researcher(shared, "https://b.example/two")
    assert _drive(child, retry) is RunStatus.COMPLETE
    assert [r for r, _p, _b in retry.reads] == ["synthesize"], "the fork re-ran finished rounds"
    path, before = retry.first("synthesize")
    assert path == str(_own_document(child.id))
    assert before == kept
    assert _own_document(run.id).read_text(encoding="utf-8") == kept


def test_a_start_that_cannot_continue_is_refused_in_words(shared):
    """A continuation that cannot happen is refused before a run exists, never run as a fresh
    start that claims to continue."""
    spec = _spec()
    with_document, _ = _research(shared, "what changed in httpx 0.28", "https://a.example/one")
    without = _create("a run that kept nothing").id
    other = store.create(WorkflowRun(id="", workflow_name="general-project")).id

    def problem(continue_from: str) -> str:
        provided = {"question": "q", "continue_from": continue_from}
        return contracts.start_problem(spec, provided, name=TEMPLATE)[1]

    assert problem(with_document) == ""
    assert problem("") == ""
    assert "there is no run 'deadbeef' to continue" in problem("deadbeef")
    assert f"run {other} is a run of general-project" in problem(other)
    assert f"run {without} kept no RESEARCH.md to continue from" in problem(without)


def test_a_run_its_inputs_name_is_read_only_when_it_is_an_earlier_run_of_this_workflow(shared):
    """The run a run continues is its input, and the folder its document is read from is built from
    it. So the carry asks the store for that run itself rather than trusting the check a start
    made: a run made another way, whose input names a folder or a run of another workflow, starts
    from nothing and is never handed that file.

    🔴 Before: the carry read the `documents/RESEARCH.md` under whatever the input named."""
    other = store.create(WorkflowRun(id="", workflow_name="general-project"))
    (store.run_dir(other.id) / "documents").mkdir(parents=True)
    (store.run_dir(other.id) / "documents" / "RESEARCH.md").write_text(
        "# Not research\n", encoding="utf-8"
    )
    elsewhere = store.config_dir() / "elsewhere" / "documents"
    elsewhere.mkdir(parents=True)
    (elsewhere / "RESEARCH.md").write_text("# Planted\n", encoding="utf-8")
    folder = "../../elsewhere"
    assert os.path.realpath(store.run_dir(folder) / "documents") == os.path.realpath(elsewhere)

    for named in (folder, other.id):
        run_id, researcher = _research(
            shared, "what changed in httpx 0.28", "https://a.example/one", continue_from=named
        )
        _path, before = researcher.first("sweep")
        assert before is None, f"the run that named {named!r} started from the file there"
        continued = {"run_id": named, "name": "RESEARCH.md", "carried": False}
        assert (store.get(run_id).extra or {}).get("continued_from") == continued


@pytest.mark.asyncio
async def test_a_nested_run_that_cannot_continue_fails_in_words(shared, monkeypatch):
    """A subworkflow child's inputs are bound from upstream, a model's answer among them, so a child
    that says it continues a run is held to the start check: refused as its node's failure, before
    a child run exists.

    🔴 Before: the child was made and started without the check."""
    from personalclaw.workflows import defs as defs_mod
    from personalclaw.workflows.bundled_defs import register_bundled_provider
    from personalclaw.workflows.engine import dispatch_subworkflow
    from personalclaw.workflows.models import FailureClass, Node

    monkeypatch.setattr(defs_mod, "_providers", {})
    register_bundled_provider()
    inputs = {"question": "q", "exit_condition": "e", "output_manner": "", "source_budget": 0}
    node = Node.from_dict(
        {
            "kind": "subworkflow",
            "id": "nested",
            "config": {"ref": TEMPLATE, "inputs": {**inputs, "continue_from": "deadbeef"}},
        }
    )
    before = len(store.list_runs()[0])

    result = await dispatch_subworkflow(node, BindingContext(), supervisor=object())

    assert result.failure is not None and result.failure.failure_class == FailureClass.USER
    assert "cannot continue: there is no run 'deadbeef' to continue" in result.failure.cause_plain
    assert len(store.list_runs()[0]) == before, "a child run was made"


def test_an_edit_cannot_change_the_run_a_run_continues(shared):
    """`continue_from` is read when a run starts: what it started from is in its folder and on its
    page. An edit that changed it would leave its inputs saying otherwise, and let a resume carry
    another run's document into a run that never started as its continuation.

    🔴 Before: the edit applied."""
    from personalclaw.workflows import mutations

    def codes(spec: dict[str, Any], overrides: dict[str, Any]) -> list[str]:
        ops = [{"op": "set_input", "overrides": overrides}]
        return [i.code for i in mutations.prepare_batch(ops, spec, {}).issues]

    assert codes(_spec(), {"continue_from": "deadbeef"}) == ["WF_MUT_START_ONLY_INPUT"]
    assert "WF_MUT_START_ONLY_INPUT" not in codes(_spec(), {"question": "what changed"})
    general = read_template("general-project").to_dict()
    assert "WF_MUT_START_ONLY_INPUT" not in codes(general, {"continue_from": "deadbeef"})


# ── a step reaches its own run's documents folder, and no other run's ───────


def test_a_step_reaches_its_runs_documents_folder_and_no_other(shared):
    from personalclaw.file_scope import FileScope, OutOfScope

    mine = _create("mine")
    theirs = _create("theirs")
    general = store.create(WorkflowRun(id="", workflow_name="general-project"))
    store.write_spec(general.id, read_template("general-project").to_dict())

    reach = provisioning.step_documents(f"workflow:{mine.id}")

    assert reach == [os.path.realpath(_own_document(mine.id).parent)]
    assert (os.stat(reach[0]).st_mode & 0o777) == 0o700
    assert provisioning.step_documents("subagent:chat-1") == []
    assert provisioning.step_documents(f"workflow:{general.id}") == []

    scope = FileScope([shared, *reach])
    assert scope.resolve(str(_own_document(mine.id)), change=True) == os.path.realpath(
        _own_document(mine.id)
    )
    with pytest.raises(OutOfScope):
        scope.resolve(str(_own_document(theirs.id)), change=False)


class _Writes:
    """A model that calls `write_file` once per path it is given, with its arguments as JSON text
    as a streaming provider sends them, then answers. What each call was answered is kept."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, paths: list[str]) -> None:
        self._paths = list(paths)
        self.turns = 0
        self.answers: list[str] = []

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        from personalclaw.llm.events import (
            EVENT_COMPLETE,
            EVENT_TEXT_CHUNK,
            EVENT_TOOL_CALL,
            AgentEvent,
        )

        if self.turns:
            last = messages[-1] if messages else {}
            content = last.get("content") if isinstance(last, dict) else None
            self.answers.append(content if isinstance(content, str) else json.dumps(content))
        self.turns += 1
        if self.turns <= len(self._paths):
            args = {"path": self._paths[self.turns - 1], "content": "# Research\n"}
            yield AgentEvent(
                kind=EVENT_TOOL_CALL,
                tool_call_id=f"c{self.turns}",
                title="write_file",
                tool_input=json.dumps(args),
            )
        else:
            yield AgentEvent(kind=EVENT_TEXT_CHUNK, text="Done.")
        yield AgentEvent(kind=EVENT_COMPLETE)


@pytest.mark.asyncio
async def test_a_step_of_the_run_writes_its_document_and_cannot_write_another_runs(
    shared, tmp_path, monkeypatch
):
    """The run's own step, spawned as the engine spawns it, on a real native runtime: its file
    tools reach its run's documents folder (as an unattended step's writes are held to its
    folders), and another run's is out of their reach."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from personalclaw.agents.native.builtin_tools import (
        PLATFORM_CATEGORIES,
        NativeBuiltinToolProvider,
    )
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.agents.provider import AgentRuntimeDefinition
    from personalclaw.subagent import SubagentManager
    from personalclaw.workflows.engine import stage_capability
    from personalclaw.workflows.models import Node

    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    monkeypatch.setattr("personalclaw.subagent_persistence._subagents_dir", lambda: tmp_path / "s")
    mine, theirs = _create("mine"), _create("theirs")
    sweep = next(n for n in _nodes(Node.from_dict(_spec()["root"])) if n.id == "sweep")
    assert stage_capability(sweep.config or {}) == "mutating", "vacuity floor: the sweep writes"
    script = _Writes([str(_own_document(mine.id)), str(_own_document(theirs.id))])

    async def get_or_create(_key, **kw):
        tools = NativeBuiltinToolProvider(
            cwd=shared,
            extra_roots=[Path(r) for r in (kw.get("extra_tool_roots") or [])],
            read_roots=[Path(r) for r in (kw.get("read_tool_roots") or [])],
            categories=PLATFORM_CATEGORIES,
        )
        runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="PersonalClaw", provider="native", model="s"),
            model_provider=script,
            tool_providers=[tools],
            cwd=shared,
            unattended=bool(kw.get("unattended")),
        )
        runtime.set_approval_policy(kw.get("approval_policy") or "")
        await runtime.start()
        return runtime, True, False

    sessions = MagicMock()
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(side_effect=get_or_create)
    sessions.release = MagicMock()
    sessions.reset = AsyncMock()
    sessions.record_success = MagicMock()
    sessions.get_agent = MagicMock(return_value="")
    sessions.has_session = MagicMock(return_value=False)
    ctx = MagicMock()
    ctx.build_message = MagicMock(return_value=("go", None))
    ctx.hooks.auto_approve_subagent_spawn = True
    ctx.hooks.auto_approve_subagent_tools = False
    manager = SubagentManager(sessions=sessions, ctx_builder=ctx)
    with (
        patch("personalclaw.subagent.Stats"),
        patch("personalclaw.guardrails.policy.ceiling_permits_approval", lambda _v: True),
    ):
        manager.spawn(
            task="Advance one round of deep research.",
            parent_session_key=f"workflow:{mine.id}:sweep",
            parent_run=f"workflow:{mine.id}",
            approval_mode="auto",
            # The sweep's own, as the engine derives it (`engine.stage_capability`).
            capability_class=stage_capability(sweep.config or {}),
            silent=True,
        )
        for task in list(manager._tasks.values()):
            await task

    assert script.turns == 3, f"vacuity floor: the model was asked {script.turns} time(s)"
    assert _own_document(mine.id).read_text(encoding="utf-8") == "# Research\n", script.answers
    assert not _own_document(theirs.id).exists(), "a step wrote another run's document"
    assert script.answers[0].startswith("Wrote "), script.answers
    assert script.answers[1].startswith("Error: path "), script.answers


# ── the supervisor's check reads the same file ──────────────────────────────


def test_a_judges_pass_while_the_runs_document_is_missing_escalates(shared):
    """The judge contract's declared `artifact_exists`, asked of the one path the run handed its
    steps: a PASS over a document that is not there is the judge and the run disagreeing."""
    from personalclaw.workflows.models import Node
    from personalclaw.workflows.stage_settlement import _settled_stage_output

    run = _create("what changed in httpx 0.28")
    spec = store.read_spec(run.id)
    controller = RunController(run, spec, services=EngineServices())
    judge = next(n for n in _nodes(Node.from_dict(spec["root"])) if n.id == "judge")
    verdict = json.dumps(
        {
            "reasoning": "the cited page says what the report claims",
            "verdict": "PASS",
            "scores": {
                "new sources were fetched and read this round": 2,
                "every claim added cites the source it came from": 2,
            },
            "evidence_refs": ["https://a.example/one"],
            "proof": "web_fetch https://a.example/one",
            "shortfalls": [],
            "cannot_judge": "",
        }
    )

    missing = _settled_stage_output(controller, judge, verdict).output
    assert missing["fallback_result"] is False and missing["escalated"] is True, missing

    _own_document(run.id).parent.mkdir(parents=True, exist_ok=True)
    _own_document(run.id).write_text("# Research\n", encoding="utf-8")
    present = _settled_stage_output(controller, judge, verdict).output
    assert present["fallback_result"] is True and present["escalated"] is False, present


def _nodes(root: Any) -> list[Any]:
    from personalclaw.workflows.models import walk

    return [node for _path, node in walk(root)]


# ── what is not the run's document stays out of it ──────────────────────────


def test_a_same_named_file_in_the_shared_workspace_is_never_kept_over_it(shared):
    """The copy a settled step makes of what it wrote in a shared folder is for a document its
    task put there, never for the one the run keeps itself."""
    from personalclaw.workflows import deliverable

    run = _create("what changed in httpx 0.28")
    own = _own_document(run.id)
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text("# This run's state\n", encoding="utf-8")
    started = time.time() - 5
    (shared / "RESEARCH.md").write_text("# Another run's state\n", encoding="utf-8")

    kept = deliverable.keep_step_documents(
        store.get(run.id), store.read_spec(run.id), folder=str(shared), since=started, step="sweep"
    )

    assert "RESEARCH.md" not in kept
    assert own.read_text(encoding="utf-8") == "# This run's state\n"


def test_a_run_that_keeps_no_document_cannot_bind_one():
    """`{{run.document}}` in a run that keeps none says what the root holds, never an empty path."""
    with pytest.raises(BindingError) as raised:
        resolve("Write it to {{run.document}}", BindingContext(inputs={}))
    assert "the document this run keeps in its own folder" in raised.value.remediation
    assert resolve("{{run.document}}", BindingContext(run_document="/r/RESEARCH.md")) == (
        "/r/RESEARCH.md"
    )
