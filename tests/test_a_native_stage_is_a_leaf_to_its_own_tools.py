"""A workflow stage on the native runtime is a leaf to its own tools, as it is on an agent CLI.

A stage runs with its lineage and posture: the run it belongs to, its depth, whether it may write
(``engine.leaf_spawn_env``). On an agent CLI they are its tool server's environment, and that is
where ``mcp_shared`` reads them. The native runtime runs its tools inside the gateway process and
handed the stage's env to nothing. Measured on ``main``:

* a native stage read depth 0, so ``subagent_run`` — denied to every leaf — ran for it;
* a research stage's read-only posture was not seen either, so its in-process tools could write;
* ``set_onetime_task(resume_run_id="self")`` answered that the session had no run, so a goal
  monitor on the native runtime could not arm its own wake.

The native runtime now binds the stage's lineage for every tool call it makes, and every reader
goes through one accessor (``mcp_shared.leaf_value``), which reads that binding — or, in a tool
server, the process environment the spawn wrote.
"""

from __future__ import annotations

import json
import pathlib
from pathlib import Path
from typing import Any

import pytest

from personalclaw import mcp_shared
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.llm.base import EVENT_COMPLETE
from personalclaw.llm.events import EVENT_TOOL_CALL, EVENT_TOOL_RESULT, AgentEvent
from personalclaw.providers import provider_bridge as pb
from personalclaw.workflows.engine import leaf_spawn_env
from personalclaw.workflows.models import Node

RUN = "run-native"

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setenv("PERSONALCLAW_WORKSPACE", str(workspace))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    for key in ("__wf_depth", "__wf_run_id", "__wf_project_id", "__wf_node_id", "__wf_read_only"):
        monkeypatch.delenv(key, raising=False)
    return home


class _OneCall:
    """A model that calls one tool, then answers."""

    supports_tools = True
    _model = "m"

    def __init__(self, tool: str, args: dict[str, Any]) -> None:
        call = AgentEvent(
            kind=EVENT_TOOL_CALL, tool_call_id="c1", title=tool, tool_input=json.dumps(args)
        )
        self._turns = [[call, AgentEvent(kind=EVENT_COMPLETE)], [AgentEvent(kind=EVENT_COMPLETE)]]
        self.calls = 0

    async def complete(self, messages, **_: Any):
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        for event in turn:
            yield event


def _stage_env(capability: str = "mutating") -> dict[str, str]:
    """The env a stage of run :data:`RUN` is spawned with, from the engine's own builder."""
    node = Node.from_dict({"kind": "stage", "id": "check", "config": {"prompt": "x"}})
    return leaf_spawn_env(node, {"prompt": "x", "capability": capability}, run_id=RUN, depth=0)


async def _native_turn(
    monkeypatch: pytest.MonkeyPatch, module: str, tool: str, args: dict, **build: Any
) -> str:
    """One turn of a runtime built by the REAL native factory, whose model calls *tool* from the
    in-process *module*. Returns what the tool answered."""
    monkeypatch.setattr(pb, "resolve_provider_for_use_case", lambda *a, **k: _OneCall(tool, args))
    monkeypatch.setattr(pb, "_fallback_chat_model", lambda **k: "m")
    provider = InProcessMcpToolProvider(module=module, provider_name="under-test")
    monkeypatch.setattr("personalclaw.tool_providers.registry.list_providers", lambda: [provider])
    runtime = pb._build_native_runtime(
        use_case="chat",
        session_key="subagent:abc",
        agent=None,
        model_override=None,
        cwd=None,
        unattended=True,
        **build,
    )
    runtime.set_approval_policy("yolo")  # nothing asks: the leaf posture is the only gate
    await runtime.start()
    events = [event async for event in runtime.stream("go")]
    results = [str(e.tool_output or "") for e in events if e.kind == EVENT_TOOL_RESULT]
    assert len(results) == 1, [e.kind for e in events]
    return results[0]


@pytest.fixture
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """`subagent_run`'s gateway call, recorded instead of made."""
    from personalclaw import mcp_subagents

    posted: list[Any] = []

    def _post(path: str, body: Any) -> dict:
        posted.append((path, body))
        return {"ok": True, "ids": ["s1"]}

    monkeypatch.setattr(mcp_subagents, "_post", _post)
    return posted


@pytest.mark.asyncio
async def test_an_orchestration_tool_is_denied_to_a_native_stage(home, monkeypatch, no_spawn):
    """🔴 Red on main: the stage read depth 0 and `subagent_run` spawned."""
    answer = await _native_turn(
        monkeypatch,
        "personalclaw.mcp_subagents",
        "subagent_run",
        {"task": "fan out"},
        extra_env=_stage_env(),
    )
    assert "orchestration tool" in answer and "denied" in answer, answer
    assert no_spawn == [], "a denied spawn reached the gateway"


@pytest.mark.asyncio
async def test_a_chat_on_the_native_runtime_is_no_leaf(home, monkeypatch, no_spawn):
    """The floor: a session with no stage lineage is the parent, and its spawn goes through."""
    answer = await _native_turn(
        monkeypatch, "personalclaw.mcp_subagents", "subagent_run", {"task": "fan out"}
    )
    assert "denied" not in answer, answer
    assert len(no_spawn) == 1, answer


@pytest.mark.asyncio
async def test_a_read_only_native_stage_cannot_write_through_an_in_process_tool(home, monkeypatch):
    """🔴 Red on main: a research stage's posture never reached its native tools."""
    from personalclaw.triggers.store import TriggerStore

    answer = await _native_turn(
        monkeypatch,
        "personalclaw.mcp_automation",
        "automation_create",
        {"name": "x", "when": "every day at 9", "message": "y"},
        extra_env=_stage_env(capability="research"),
    )
    assert "grants 'read' tools only" in answer, answer
    assert TriggerStore(base_dir=home).load() == [], "the read-only stage wrote a trigger"


@pytest.mark.asyncio
async def test_a_native_stage_arms_a_wake_for_its_own_run(home, monkeypatch):
    """🔴 Red on main: "resume_run_id='self' only works from inside a workflow run"."""
    from personalclaw.triggers.store import TriggerStore
    from personalclaw.triggers.wakeup import resume_target_of

    answer = await _native_turn(
        monkeypatch,
        "personalclaw.mcp_automation",
        "set_onetime_task",
        {"name": "next check", "when": "in 1 hour", "message": "look", "resume_run_id": "self"},
        extra_env=_stage_env(),
    )
    rows = TriggerStore(base_dir=home).load()
    assert len(rows) == 1, answer
    assert resume_target_of(rows[0].trigger)["run_id"] == RUN, answer


def test_a_tool_server_still_reads_the_env_its_spawn_wrote(monkeypatch):
    """The other half of the one mechanism: with nothing bound, the process environment is the
    leaf's — an agent CLI's tool server was spawned with it."""
    for key, value in mcp_shared.leaf_lineage(_stage_env(capability="research")).items():
        monkeypatch.setenv(key, value)
    assert mcp_shared.leaf_value("__wf_run_id") == RUN
    assert mcp_shared.leaf_depth() == 1
    assert "orchestration tool" in mcp_shared.leaf_tool_denial("subagent_run")


def test_a_binding_speaks_for_the_session_even_when_the_process_env_disagrees(monkeypatch):
    """Inside the gateway the process environment is nobody's lineage: a bound session reads its
    own, and an empty binding reads as no lineage at all rather than falling back."""
    monkeypatch.setenv("__wf_depth", "3")
    token = mcp_shared.bind_leaf_lineage({})
    try:
        assert mcp_shared.leaf_depth() == 0
    finally:
        mcp_shared.reset_leaf_lineage(token)
    assert mcp_shared.leaf_depth() == 3


def _reads_a_leaf_key_from_the_environment(line: str) -> bool:
    return "environ" in line and any(
        key in line for key in ("__wf_", "WF_DEPTH_KEY", "LEAF_READ_ONLY_KEY")
    )


def test_every_reader_of_the_leaf_keys_goes_through_the_one_accessor():
    """🔴 Red on main (four readers of the process environment). A reader of its own is how the
    native runtime lost the lineage: it read a place a native session never writes."""
    assert _reads_a_leaf_key_from_the_environment('os.environ.get("__wf_run_id", "")'), "control"
    files = sorted(_SRC.rglob("*.py"))
    assert len(files) > 500, "vacuity floor: the package was not read"
    offenders = [
        f"{path.relative_to(_SRC)}:{number}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _reads_a_leaf_key_from_the_environment(line)
    ]
    assert offenders == [], offenders
