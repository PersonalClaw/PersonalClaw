"""A dispatched `stage` node's completion must have a CONSUMER.

`engine.dispatch_stage` spawns a subagent and returns `RUNNING` carrying
`{"subagent_id": info.id}` (``engine.py:779``). Nothing read that key, so a stage node
never left `RUNNING`: `_await_progress` pops the finished asyncio task out of
`_inflight` (``controller.py:2686``) before `_apply` runs, and once `_inflight` is empty
the tick loop takes the `else` branch at ``controller.py:573`` and yields on
`asyncio.sleep(0)` forever — which also means `_await_progress`, the ONLY caller of
`_enforce_stall_timeouts` (``controller.py:2682``), stops being called at all. Measured in
`SELF-VERIFICATION.md:430-441`: a node stayed RUNNING for fifteen minutes after its
subagent reported `done: True`.

These tests assert the CALL SITE, not a helper in isolation: the whole defect was that no
code path ever asked the subagent manager whether the spawn had finished. Every test here
except `test_an_unknown_subagent_id_does_not_invent_a_verdict` (the fail-safe guard, which
is vacuous until the reconciliation exists) reds against unmodified `main`.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest

from personalclaw.workflows import store
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.models import FailureClass, InstanceState, RunStatus, WorkflowRun

STAGE_PATH = "root.children[0]"

#: Bounded so a red (the run never terminating) costs seconds, not a hung suite. Nothing
#: here waits on wall-clock progress: the fake's completion is driven by the number of
#: reconciliation lookups the controller performs, so a passing run terminates on the
#: first tick after the subagent reports done rather than after a sleep.
RUN_TIMEOUT = 6.0


class _Info:
    """The subset of `SubagentInfo` (``subagent.py:306``) that a completion is read from."""

    def __init__(self, agent_id: str) -> None:
        self.id = agent_id
        self.done = False
        self.error = ""
        self.result = ""
        self.reaped = False
        self.agent = ""


class _FakeSubagents:
    """Stands in for `SubagentManager` on exactly the two methods this path uses.

    `spawn` is what `engine.dispatch_stage` already calls; `get` is the SHIPPED lookup at
    ``subagent.py:1632``, already used by ``dashboard/handlers/sessions.py:388``. A fake
    exposing a third method would be testing a registry this fix must not add.
    """

    def __init__(self, *, finish_after: int = 1, error: str = "", known: bool = True) -> None:
        self.infos: dict[str, _Info] = {}
        self.spawns: list[dict[str, Any]] = []
        self.gets: list[str] = []
        self._finish_after = finish_after
        self._error = error
        self._known = known

    def spawn(self, **kw: Any) -> _Info:
        info = _Info(f"sub{len(self.spawns) + 1}")
        self.spawns.append(kw)
        self.infos[info.id] = info
        return info

    def get(self, agent_id: str) -> _Info | None:
        """Report the spawn as finished once the controller has asked `finish_after` times.

        Keyed on LOOKUPS, not on elapsed time: a fixed sleep would measure the tick cadence
        and a frozen clock would make every tick read "not yet" — both pass against a
        controller that never reconciles at all.
        """
        self.gets.append(agent_id)
        if not self._known:
            return None
        info = self.infos.get(agent_id)
        if info is not None and len(self.gets) >= self._finish_after:
            info.done = True
            info.error = self._error
            info.result = "" if self._error else "the stage's answer"
            info.reaped = bool(self._error)
        return info


def _spec() -> dict[str, Any]:
    return {
        "name": "stage-completion",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "stage", "id": "work", "config": {"prompt": "do the thing"}}],
        },
    }


@pytest.fixture
def wired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A real `RunController` over a real spec, with only the subagent manager faked."""
    monkeypatch.setattr("personalclaw.workflows.leases.config_dir", lambda: tmp_path)

    def _build(**kw: Any) -> tuple[RunController, _FakeSubagents, list[dict[str, Any]]]:
        fake = _FakeSubagents(**kw)
        spec = _spec()
        run = store.create(WorkflowRun(id="", workflow_name="stage-completion"))
        store.write_spec(run.id, spec)
        controller = RunController(
            run, spec, services=EngineServices(subagents=fake, cwd=str(tmp_path))
        )
        # Spy on the ledger writer rather than the ledger FILE: the symptom recorded in the
        # plan was "no `step_completed`", and the call is the thing under test.
        completed: list[dict[str, Any]] = []
        real = controller.journal.step_completed

        def _spy(path: str, node_id: str, **kwargs: Any) -> None:
            completed.append({"path": path, "node_id": node_id, **kwargs})
            real(path, node_id, **kwargs)

        controller.journal.step_completed = _spy  # type: ignore[method-assign]
        return controller, fake, completed

    return _build


def _drive(controller: RunController) -> RunStatus:
    return asyncio.run(controller.run_to_completion(timeout=RUN_TIMEOUT))


# ── the call site ────────────────────────────────────────────────────────────


def test_the_controller_ASKS_the_manager_whether_a_dispatched_stage_finished(wired):
    """THE call-site assertion. On unmodified `main` this is zero: `subagent_id` is written
    at ``engine.py:779`` and read nowhere, so no code path ever performs the lookup. A
    timeout helper proven in isolation would not catch this — the helper is never reached
    for this node kind.
    """
    controller, fake, _ = wired()
    _drive(controller)

    assert fake.spawns, "the stage never dispatched, so this test proves nothing"
    assert fake.gets, (
        "the controller never asked the subagent manager whether the spawn finished — "
        "`subagent_id` is still a write with no reader"
    )
    assert (
        fake.gets[0] == "sub1"
    ), f"the reconciliation looked up {fake.gets[0]!r}, not the id the spawn returned"


def test_a_finished_stage_leaves_RUNNING_and_journals_step_completed(wired):
    """The reported symptom, inverted: `done: True` must become a terminal node."""
    controller, fake, completed = wired()
    status = _drive(controller)

    inst = controller.instances[STAGE_PATH]
    assert (
        inst.state is InstanceState.DONE
    ), f"a finished stage is still {inst.state.value} — this is the fifteen-minute hang"
    assert inst.completed_at, "a terminal node with no completion timestamp"
    assert status is RunStatus.COMPLETE, f"the run never completed (status={status.value})"
    assert [c["node_id"] for c in completed] == [
        "work"
    ], f"no `step_completed` for the stage: {completed}"


def test_the_stages_OUTPUT_reaches_the_binding_namespace_not_the_subagent_id(wired):
    """The RUNNING branch seeded `_outputs["work"] = {"subagent_id": ...}`. A downstream
    `{{nodes.work}}` binding must resolve to the subagent's RESULT, or the fix would leave
    every consumer of a stage reading an engine-internal id.
    """
    controller, fake, _ = wired()
    _drive(controller)

    out = controller._outputs.get("work")
    assert out is not None, "the stage produced no output at all"
    rendered = repr(out)
    assert "sub1" not in rendered, f"the binding namespace still holds the subagent id: {out!r}"
    assert "the stage's answer" in rendered, f"the subagent's result never landed: {out!r}"


# ── the hung stage ───────────────────────────────────────────────────────────


def test_a_reaped_stage_becomes_a_TIMEOUT_failure(wired):
    """A hung stage reaches the timeout machinery that actually owns a spawned worker's
    deadline: `SubagentManager._reaper_loop` (``subagent.py:739``) force-kills at
    `_default_timeout` and `_force_reap` sets `done=True` + `error="Reaped after ..."`
    (``subagent.py:791-793``). That verdict already existed and was simply unread — so
    reconciling it is what makes a hung stage visible, without a second deadline.
    """
    controller, fake, completed = wired(error="Reaped after 900s (exceeded 900s deadline) [stage]")
    status = _drive(controller)

    inst = controller.instances[STAGE_PATH]
    assert (
        inst.state is InstanceState.FAILED
    ), f"a reaped subagent left its node {inst.state.value} — the hang is not observable"
    assert inst.failure is not None
    assert inst.failure.failure_class is FailureClass.TIMEOUT, inst.failure.to_dict()
    assert "Reaped after 900s" in (
        inst.failure.cause_plain or ""
    ), "the manager's own verdict was discarded and replaced with a generic message"
    assert not completed, "a reaped stage must not be journalled as a completed step"
    assert status is RunStatus.FAILED, f"the run did not surface the failure (status={status})"


def test_an_unknown_subagent_id_does_not_invent_a_verdict(wired):
    """Fail-safe: `get()` returning None means the manager has no record — after a gateway
    restart its memory is empty for every id. "Unknown" must not be read as "finished":
    inventing DONE would bless work that never reported, and inventing FAILED would kill a
    run the watchdog is mid-adoption of. It stays RUNNING and the existing
    `audit.STALE_RUNNING` finding (``audit.py:39``) remains the backstop.

    Vacuous until the reconciliation exists, so it is falsified by mutation rather than by
    a red on `main`.
    """
    controller, fake, completed = wired(known=False)
    _drive(controller)

    inst = controller.instances[STAGE_PATH]
    assert (
        inst.state is InstanceState.RUNNING
    ), f"an unknown subagent id was turned into {inst.state.value} — a verdict on no evidence"
    assert inst.failure is None, "a failure was invented for a subagent nobody could look up"
    assert not completed, "an unknown subagent was journalled as a completed step"


# ── the non-stage RUNNING path ───────────────────────────────────────────────


def test_stage_is_the_ONLY_dispatcher_THAT_CAN_RETURN_RUNNING():
    """The blast-radius assertion: there is no non-stage RUNNING path to change.

    Parsed with `ast`, not grepped — a text scan counts the string inside comments and
    docstrings, and this file's whole subject is a claim about which code runs.
    """
    from personalclaw.workflows import engine

    # From the imported module, not a cwd-relative path: a mistyped relative path reads as
    # an empty producer set, which would satisfy no assertion but look like a clean scan.
    src = Path(engine.__file__).read_text(encoding="utf-8")
    assert "InstanceState.RUNNING" in src, "engine.py did not load — the scan is vacuous"
    tree = ast.parse(src)

    producers: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            for sub in ast.walk(node.value):
                if (
                    isinstance(sub, ast.Attribute)
                    and sub.attr == "RUNNING"
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "InstanceState"
                ):
                    producers.add(fn.name)

    assert producers == {"dispatch_stage"}, (
        f"a dispatcher other than `dispatch_stage` now returns RUNNING: {sorted(producers)}. "
        "The stage reconciliation would silently claim its completion too."
    )
