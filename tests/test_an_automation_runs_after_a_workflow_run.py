""" "When that research run finishes, post the summary": an automation that runs after a run.

The chat's `automation_create` promised "when my nightly run finishes", and refused "when the
research run 9c2c10ab finishes" (the run's id sat between "run" and "finishes"). Had it been made,
nothing would have fired it: a workflow run's end fired no `run_completed` trigger, and one waiting
on a trigger whose action started a workflow fired the moment the run began.

Now a workflow run's end fires what waits on it — that run, any run of its workflow, the trigger
that started it — with what the run said it produced, and a fire that only started its work chains
when that work ends.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from personalclaw.triggers import chain
from personalclaw.triggers import tools as T
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore
from personalclaw.workflows import store as runs
from personalclaw.workflows.models import OriginKind, RunOrigin, RunStatus, WorkflowRun


@pytest.fixture
def home(tmp_path, monkeypatch):
    from personalclaw.config import loader

    monkeypatch.setattr(loader, "config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def store(home):
    return TriggerStore(base_dir=home)


def _run(workflow: str = "deep-research", status: RunStatus = RunStatus.RUNNING, **kw: Any):
    return runs.create(WorkflowRun(id="", workflow_name=workflow, status=status, **kw))


def _create(store, when: str, **kw: Any):
    return T.create(store, name="Post the summary", when=when, message="post it", **kw)


# ── the chat's tool reads which run ──


def test_a_run_named_by_its_id_is_the_run_it_waits_on(store):
    """🔴 Before: "I could not tell what should trigger this from 'when the research run …
    finishes'"."""
    run = _run()

    made = _create(store, f"when the research run {run.id} finishes")

    assert made.ok, made.text
    saved = store.get("run_completed:post-the-summary").trigger
    assert saved.spec == {"source_run": run.id}
    assert f"runs when the workflow run {run.id} (deep-research) ends" in made.text


def test_a_run_named_by_its_workflow_is_the_one_going_now(store):
    run = _run()
    _run("feed-digest")

    made = _create(store, "when that research run finishes")

    assert made.ok, made.text
    assert store.get("run_completed:post-the-summary").trigger.spec == {"source_run": run.id}


def test_a_run_named_by_its_trigger_waits_on_that_trigger(store):
    store.upsert(Trigger(id="clock:nightly", name="nightly", kind="clock", enabled=True))

    made = _create(store, "when my nightly run finishes")

    assert made.ok, made.text
    saved = store.get("run_completed:post-the-summary").trigger
    assert saved.spec == {"source_trigger": "clock:nightly"}


def test_a_run_it_cannot_place_is_refused_with_the_runs_going_now(store):
    """A `run_completed` row with nothing to wait on matches nothing: listed and silent forever."""
    run = _run()

    made = _create(store, "when the run finishes")

    assert not made.ok
    assert "Which run should it wait for?" in made.text
    assert f"Going now: {run.id} (deep-research)." in made.text
    assert store.load() == []


def test_a_run_that_already_ended_is_refused(store):
    run = _run(status=RunStatus.COMPLETE)

    made = _create(store, f"when run {run.id} is done")

    assert not made.ok
    assert "has finished already, so there is nothing left to wait for" in made.text
    assert store.load() == []


# ── a run's end fires what waits on it ──


def _waiting(store, tid: str, spec: dict[str, Any]) -> None:
    store.upsert(
        Trigger(
            id=tid,
            name=tid,
            kind="run_completed",
            enabled=True,
            spec=spec,
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )


def test_a_runs_end_fires_what_waits_on_it_with_what_it_produced(store):
    run = _run()
    _waiting(store, "run_completed:on-this-run", {"source_run": run.id})
    _waiting(store, "run_completed:on-any-run", {"source_def": "deep-research"})
    _waiting(store, "run_completed:elsewhere", {"source_run": "00000000"})

    fires, refused = chain.next_fires(
        store,
        source_id="",
        source_payload=chain.run_end_payload(run, status="complete", summary="Three findings."),
        source_def=run.workflow_name,
        source_run=run.id,
    )

    assert refused == []
    assert sorted(t.id for t, _ in fires) == [
        "run_completed:on-any-run",
        "run_completed:on-this-run",
    ]
    payload = next(p for t, p in fires if t.id == "run_completed:on-this-run")
    assert payload["source_run_id"] == run.id and payload["summary"] == "Three findings."
    assert payload["run_status"] == "complete" and payload["source_workflow"] == "deep-research"


def test_a_run_that_said_nothing_gives_an_empty_summary_not_none(store):
    """An action's `$summary` reads "" for a run that produced nothing, not the placeholder."""
    run = _run()
    _waiting(store, "run_completed:on-this-run", {"source_run": run.id})

    fires, _ = chain.next_fires(
        store,
        source_id="",
        source_payload=chain.run_end_payload(run, status="failed"),
        source_run=run.id,
    )

    assert fires[0][1]["summary"] == ""


def test_a_loop_through_a_workflow_run_is_still_a_loop(store):
    """A trigger that runs a workflow after that workflow's run ends would start it forever: the run
    carries the chain that started it, so its end refuses the same trigger as a cycle."""
    run = _run(
        origin=RunOrigin(kind=OriginKind.HOOK, trigger_id="run_completed:again"),
        extra={chain.CHAIN_EXTRA_KEY: {chain.DEPTH_KEY: 1, chain.PATH_KEY: ["run:first"]}},
    )
    _waiting(store, "run_completed:again", {"source_def": "deep-research"})
    payload = chain.run_end_payload(run, status="complete")

    fires, refused = chain.next_fires(
        store,
        source_id="run_completed:again",
        source_payload=payload,
        source_def=run.workflow_name,
        source_run=run.id,
    )

    assert fires == []
    assert [r["trigger_id"] for r in refused] == ["run_completed:again"]
    assert "chain cycle" in refused[0]["reason"]


def test_a_chained_fire_is_told_which_run_ended_and_what_it_produced():
    from personalclaw.triggers import fire_facts

    trigger = SimpleNamespace(kind="run_completed", id="run_completed:post", name="Post it")
    facts = asyncio.run(
        fire_facts.describe(
            trigger,
            {
                "source_run_id": "9c2c10ab",
                "source_workflow": "deep-research",
                "run_status": "complete",
                "summary": "Three findings.",
            },
        )
    )

    assert "The workflow run 9c2c10ab (deep-research) has finished" in facts.text
    assert "#/workflows/runs/9c2c10ab" in facts.text
    # What the run said is its own words, from outside: fenced, with the trigger as their source.
    said = facts.text.split("What the run said it produced:\n", 1)[1]
    assert said.startswith("<untrusted_content source=trigger:run_completed:post "), said
    assert "Three findings." in said.split("</untrusted_content>", 1)[0]


# ── the engine hands its end over, and the gateway chains then ──


def _ended(status: RunStatus) -> list:
    from personalclaw.workflows import run_finish

    seen: list = []
    run_finish.chain_after_run(
        SimpleNamespace(run_ended=lambda run, **kw: seen.append((run.id, kw))),
        SimpleNamespace(id="9c2c10ab", extra={"summary": "Done."}),  # type: ignore[arg-type]
        status,
    )
    return seen


def test_the_engine_hands_a_runs_end_to_what_waits_on_it():
    assert _ended(RunStatus.COMPLETE) == [("9c2c10ab", {"status": "complete", "summary": "Done."})]
    assert _ended(RunStatus.FAILED)[0][1]["status"] == "failed"


def test_a_run_its_owner_stopped_hands_nothing_over():
    assert _ended(RunStatus.CANCELLED) == [] and _ended(RunStatus.DECLINED) == []


def _gateway(chained: list) -> Any:
    from personalclaw.gateway import GatewayOrchestrator

    orch = object.__new__(GatewayOrchestrator)
    orch._background_tasks = set()

    async def _chain(**kw: Any) -> None:
        chained.append(kw)

    orch._fire_chained_triggers = _chain  # type: ignore[method-assign]
    return orch


def test_the_gateway_chains_a_workflow_runs_end_on_the_run_its_workflow_and_its_trigger():
    chained: list = []
    orch = _gateway(chained)
    run = WorkflowRun(
        id="9c2c10ab",
        workflow_name="deep-research",
        origin=RunOrigin(kind=OriginKind.HOOK, trigger_id="clock:nightly"),
    )

    async def _end() -> None:
        orch._chain_after_workflow_run(run, status="complete", summary="Done.")
        await asyncio.gather(*orch._background_tasks)

    asyncio.run(_end())

    (kw,) = chained
    assert (kw["source_trigger"], kw["source_def"], kw["source_run"]) == (
        "clock:nightly",
        "deep-research",
        "9c2c10ab",
    )
    assert kw["payload"]["summary"] == "Done."


def test_a_fire_that_only_started_its_work_does_not_chain_yet(home, monkeypatch):
    """🔴 Before: "when my nightly run finishes" fired the moment the nightly run started, because
    the fire that launched it returned at once."""
    from personalclaw.action_providers.base import ActionResult

    class _Launches:
        name = "notify"

        async def execute(self, config, ctx, timeout=30):
            return ActionResult(success=True, outcome="launched")

    monkeypatch.setattr(
        "personalclaw.action_providers.get_action_provider", lambda name: _Launches()
    )
    store = TriggerStore(base_dir=home)
    store.upsert(
        Trigger(
            id="clock:nightly",
            name="nightly",
            kind="clock",
            enabled=True,
            capabilities={"providers": ["notify"]},
            workflow={"inline": {"provider": "notify", "config": {}}},
        )
    )
    chained: list = []
    orch = _gateway(chained)
    orch.dashboard_state = None
    trigger = store.get("clock:nightly").trigger

    carried = {chain.DEPTH_KEY: 1, chain.PATH_KEY: ["run_completed:before"]}
    asyncio.run(orch._fire_store_trigger(trigger, {"trigger_id": trigger.id, **carried}))

    assert chained == []
    assert chain.held_chain("clock:nightly") == carried, "its work's end continues this chain"


def test_two_triggers_after_each_others_agent_task_are_still_a_loop(store):
    """The agent task a fire started ends later, away from the fire's payload: the chain the fire
    carried is held for it, so a loop through agent tasks is refused like any other."""
    _waiting(store, "run_completed:a", {"source_trigger": "run_completed:b"})
    _waiting(store, "run_completed:b", {"source_trigger": "run_completed:a"})
    chain.hold_chain("run_completed:b", {chain.DEPTH_KEY: 1, chain.PATH_KEY: ["run_completed:a"]})

    fires, refused = chain.next_fires(
        store, source_id="run_completed:b", source_payload=chain.held_chain("run_completed:b")
    )

    assert fires == []
    assert "chain cycle" in refused[0]["reason"]


# ── the Triggers page makes one ──


def _post_create(home, monkeypatch, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    import json
    from unittest.mock import MagicMock

    from aiohttp import web
    from aiohttp.test_utils import make_mocked_request

    from personalclaw.dashboard.handlers import triggers as handlers

    monkeypatch.setattr(handlers, "config_dir", lambda: home)
    monkeypatch.setattr(handlers, "_trigger_store", lambda: TriggerStore(base_dir=home))
    app = web.Application()
    app["state"] = MagicMock()
    req = make_mocked_request("POST", "/api/triggers", app=app)
    req["user"] = "tester"

    async def _json() -> dict[str, Any]:
        return body

    req.json = _json  # type: ignore[assignment]
    resp = asyncio.run(handlers.api_trigger_create(req))
    return resp.status, json.loads(resp.body.decode())


_NOTIFY = {"provider": "notify", "config": {"title": "Research", "body": "$summary"}}


def test_the_triggers_page_makes_one_that_waits_on_a_run_going_now(home, store, monkeypatch):
    run = _run()

    status, body = _post_create(
        home,
        monkeypatch,
        {
            "trigger_type": "run_completed",
            "name": "After it",
            "source_run": run.id,
            "action": _NOTIFY,
        },
    )

    assert status == 201, body
    saved = store.get("run_completed:after-it").trigger
    assert (saved.kind, saved.spec) == ("run_completed", {"source_run": run.id})


def test_the_triggers_page_is_refused_a_run_that_ended(home, store, monkeypatch):
    run = _run(status=RunStatus.FAILED)

    status, body = _post_create(
        home,
        monkeypatch,
        {
            "trigger_type": "run_completed",
            "name": "After it",
            "source_run": run.id,
            "action": _NOTIFY,
        },
    )

    assert status == 400
    assert "failed already, so there is nothing left to wait for" in body["error"]["message"]
    assert store.load() == []


def test_the_triggers_page_is_refused_one_that_waits_on_nothing(home, store, monkeypatch):
    status, body = _post_create(
        home, monkeypatch, {"trigger_type": "run_completed", "name": "After it", "action": _NOTIFY}
    )

    assert status == 400
    assert body["error"]["message"] == "Pick the workflow or the run it runs after."


def test_the_page_offers_the_variables_a_run_completed_action_gets():
    from personalclaw.dashboard.handlers import triggers as handlers

    resp = asyncio.run(handlers.api_trigger_variables(None))  # type: ignore[arg-type]
    import json

    assert "$summary" in json.loads(resp.body.decode())["run_completed"]


def test_a_run_the_watchdog_fails_hands_its_end_over_too(home):
    """A run whose spec cannot be read is failed without a controller, so without `_finish`:
    what waits on it hears from there."""
    from personalclaw.workflows.controller import EngineServices
    from personalclaw.workflows.watchdog import WorkflowWatchdog

    seen: list = []
    run = _run()
    watchdog = WorkflowWatchdog(
        None, EngineServices(run_ended=lambda r, **kw: seen.append((r.id, kw["status"])))
    )

    asyncio.run(watchdog._fail(run, "run spec is missing or unreadable"))

    assert seen == [(run.id, "failed")]
