"""A workflow step's arguments never reach past the step's own run.

A workflow template can be written or edited by a model, so what a step's action is told to do is
held to the run the step belongs to. A few arguments an automation's action may name, as the owner
set it up and allowed it, a workflow step's may not, because for a step each is its own run's:

* `run-workflow`'s `project_id`: the run a step starts is in the step's own project (a run in
  another project reads that project's secrets);
* `run-prompt`'s `session`: the agent a step starts answers to no chat (a chat named there would
  lend the agent that chat's Trust and post its results into it);
* `second-opinion`'s `session_key`, `workspace` and `brief_dir`: a step's handoff works and writes
  in its run's folder, and is recorded as the run's.

🔴 Red before: each was taken from the step's arguments as written. Now a template that names one
is refused, naming the key, when it is saved, in a dry run and when a run of it starts, before
anything runs; a value bound in at run time is refused at the step, which starts nothing; and the
planner that writes templates is not offered them. The same arguments in an automation's action
work as they did: a provider tells a step from an automation by its dispatch, never by its payload.

And a second opinion never runs on the host while the agent it asks runs sandboxed: a payload no
longer picks its tier, and with none named it runs in the tier the agent's own runtime is set up
with, as every other start of that agent does.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import personalclaw.action_providers.run_prompt_provider as run_prompt
import personalclaw.proposer.service as proposer
from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider
from personalclaw.action_providers.second_opinion_provider import SecondOpinionActionProvider
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import service, store
from personalclaw.workflows.bindings import BindingContext
from personalclaw.workflows.controller import EngineServices, RunController
from personalclaw.workflows.engine import dispatch_action
from personalclaw.workflows.models import InstanceState, Node, RunStatus, WorkflowRun
from personalclaw.workflows.validator import validate_spec

pytestmark = pytest.mark.anyio

#: The workflow a `run-workflow` step starts.
CHILD = "child-sweep"
#: A chat, as its session is named.
CHAT = "chat-1-a1b2c3"
#: What every `second-opinion` call needs besides the arguments under test.
HANDOFF = {"goal": "green suite", "stuck_at": "one failing test", "origin_runner": "runner-a"}

#: Per provider, a step's arguments with nothing only an automation may name, and the arguments
#: (and values) only an automation may name.
OWN_ARGUMENTS: dict[str, dict[str, Any]] = {
    "run-workflow": {"workflow": CHILD},
    "run-prompt": {"message": "Summarise the notes"},
    "second-opinion": dict(HANDOFF),
}
ELSEWHERE: list[tuple[str, str, str]] = [
    ("run-workflow", "project_id", "project-elsewhere"),
    ("run-prompt", "session", CHAT),
    ("second-opinion", "session_key", CHAT),
    ("second-opinion", "workspace", "/srv/elsewhere"),
    ("second-opinion", "brief_dir", "/srv/elsewhere"),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    return home


class _ChildDefs(defs_mod.WorkflowDefProvider):
    """The one workflow a `run-workflow` step here starts."""

    spec = {"name": CHILD, "root": {"kind": "transform", "id": "only", "config": {"expr": "done"}}}

    @property
    def name(self) -> str:
        return "step-reach-child"

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return [self.spec], 1

    async def get_def(self, name: str):
        return self.spec if name == CHILD else None


@pytest.fixture
def launched(monkeypatch) -> list[WorkflowRun]:
    """The runs the action supervisor was asked to launch: every run `run-workflow` started."""
    runs: list[WorkflowRun] = []

    async def _launch(run: WorkflowRun, spec: dict[str, Any]) -> None:
        runs.append(run)

    defs_mod.register_provider(_ChildDefs())
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(workflows=SimpleNamespace(launch=_launch)),
    )
    try:
        yield runs
    finally:
        defs_mod.unregister_provider("step-reach-child")


@pytest.fixture
def spawned(monkeypatch) -> list[dict[str, Any]]:
    """The agents `run-prompt` started, by what it handed `spawn`."""
    starts: list[dict[str, Any]] = []

    def _spawn(**kw: Any) -> Any:
        starts.append(kw)
        return SimpleNamespace(id="c0ffee01", done=False, error="")

    monkeypatch.setattr(
        run_prompt,
        "get_action_services",
        lambda: SimpleNamespace(subagents=SimpleNamespace(spawn=_spawn)),
    )
    return starts


@pytest.fixture
def asked(monkeypatch) -> list[dict[str, Any]]:
    """The handoffs `second-opinion` asked for, by what it handed the proposer."""
    asks: list[dict[str, Any]] = []

    async def _handoff(**kw: Any) -> Any:
        asks.append(kw)
        return SimpleNamespace(accepted=True, rejection="", to_dict=lambda: {"accepted": True})

    monkeypatch.setattr(proposer, "run_second_opinion", _handoff)
    return asks


def _providers() -> Any:
    real = {
        "run-workflow": RunWorkflowActionProvider,
        "run-prompt": run_prompt.RunPromptActionProvider,
        "second-opinion": SecondOpinionActionProvider,
    }
    return lambda name: real[name]()


def _step(provider: str, arguments: Any, *, node_id: str = "start") -> dict[str, Any]:
    return {"kind": "action", "id": node_id, "config": {"provider": provider, "with": arguments}}


def _spec(*steps: dict[str, Any], name: str = "reach-probe") -> dict[str, Any]:
    return {"name": name, "root": {"kind": "sequence", "id": "s", "children": list(steps)}}


async def _dispatch(provider: str, arguments: Any, **run: Any):
    """*provider*'s step dispatched the way the run's controller dispatches it."""
    return await dispatch_action(
        Node.from_dict(_step(provider, arguments)),
        BindingContext(node_outputs=run.pop("outputs", {})),
        get_provider=_providers(),
        run_id=run.pop("run_id", "run-own"),
        instance_path="root.children[1]",
        **run,
    )


def _side_effects(launched, spawned, asked) -> list[Any]:
    return [*launched, *spawned, *asked]


# ── a template that names one is refused where it is validated ─────────────


@pytest.mark.parametrize(("provider", "key", "value"), ELSEWHERE)
def test_saving_a_template_whose_step_names_one_is_refused_naming_the_key(
    provider: str, key: str, value: str
) -> None:
    """🔴 Red before: the template validated, and the value was used at every run."""
    result = validate_spec(_spec(_step(provider, {**OWN_ARGUMENTS[provider], key: value})))

    refusals = [issue for issue in result.errors if issue.code == "WF_ARGUMENT_RUN_IDENTITY"]
    assert len(refusals) == 1, [issue.to_dict() for issue in result.issues]
    assert refusals[0].message.startswith(f"This step names `{key}` in its `with`.")
    assert "so a template cannot set that key: remove it from the step's `with`" in (
        refusals[0].message
    )
    assert refusals[0].path == "root.children[0]"


@pytest.mark.parametrize("provider", sorted(OWN_ARGUMENTS))
def test_a_step_with_only_its_own_arguments_validates(provider: str) -> None:
    """CONTROL: an ordinary step of each provider is not refused."""
    result = validate_spec(_spec(_step(provider, OWN_ARGUMENTS[provider])))

    assert not [i for i in result.issues if i.code == "WF_ARGUMENT_RUN_IDENTITY"], [
        i.to_dict() for i in result.issues
    ]


@pytest.mark.parametrize("save", [False, True], ids=["dry run", "save"])
async def test_the_dry_run_and_the_save_answer_with_the_refusal(save: bool) -> None:
    """🔴 Red before: the dry run passed, and the definition saved."""
    root = _spec(_step("run-workflow", {"workflow": CHILD, "project_id": "project-elsewhere"}))
    result = await service.author_def(
        name="reach-probe", root=root["root"], save=save, provenance="user", owner_allowed=True
    )

    assert result["ok"] is False
    assert result["code"] == "WF_DEF_INVALID"
    refusals = [i for i in result["issues"] if i["code"] == "WF_ARGUMENT_RUN_IDENTITY"]
    assert len(refusals) == 1
    assert "`project_id`" in refusals[0]["message"]


async def test_a_run_whose_step_names_another_project_starts_nothing(launched, caplog) -> None:
    """🔴 Red before: the run started, and its step started a run in the other project. A
    definition can reach a run without the save door (an app's, one saved before this rule, one
    edited on disk), so the run's start asks the same question and says it in the same words."""
    spec = _spec(_step("run-workflow", {"workflow": CHILD, "project_id": "project-elsewhere"}))
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"], project_id="project-own"))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(get_provider=_providers()))

    with caplog.at_level("WARNING", logger="personalclaw.workflows.run_start"):
        status = await controller.run_to_completion(timeout=30)

    assert status == RunStatus.FAILED
    assert launched == []
    rows, _total = store.list_runs(workflow_name=CHILD, limit=10)
    assert rows == []
    assert controller.run.error_message.startswith(
        "The run did not start: its step “start” names `project_id` in its `with`."
    )
    assert "The run a workflow step starts is in the step's own project" in (
        controller.run.error_message
    )
    logged = [r.getMessage() for r in caplog.records if run.id in r.getMessage()]
    assert logged and controller.run.error_message in logged[0]


# ── at the step: a bound value is refused, and the run's own is used ───────


@pytest.mark.parametrize(("provider", "key", "value"), ELSEWHERE)
async def test_a_step_handed_one_by_another_steps_output_does_nothing(
    provider: str, key: str, value: str, launched, spawned, asked, tmp_path
) -> None:
    """🔴 Red before, for every key: arguments bound whole from another step's output (what the
    validator cannot read) reached the provider, which acted on them."""
    result = await _dispatch(
        provider,
        "{{nodes.plan.output}}",
        outputs={"plan": {**OWN_ARGUMENTS[provider], key: value}},
        project_id="project-own",
        cwd=str(tmp_path),
    )

    assert result.state == InstanceState.FAILED
    assert (result.failure.cause_plain or "").startswith(f"This step names `{key}` in its `with`.")
    assert _side_effects(launched, spawned, asked) == []


async def test_a_run_workflow_step_starts_its_run_in_the_steps_own_project(launched) -> None:
    """🔴 Red before: a step's run belonged to no project, whatever project its own run is in."""
    result = await _dispatch("run-workflow", {"workflow": CHILD}, project_id="project-own")

    assert result.state == InstanceState.DEGRADED, result.failure
    assert [run.project_id for run in launched] == ["project-own"]


async def test_a_run_prompt_step_that_names_a_chat_gets_no_chat_parent(spawned) -> None:
    """🔴 Red before: the agent started as the named chat's work."""
    result = await _dispatch("run-prompt", {"message": "Summarise the notes", "session": CHAT})

    assert result.state == InstanceState.FAILED
    assert "`session`" in (result.failure.cause_plain or "")
    assert not [start for start in spawned if start.get("parent_session_key")]


async def test_a_run_prompt_step_still_starts_its_agent_for_its_run(spawned) -> None:
    """CONTROL: a step that names no session starts its agent, with no chat as its parent."""
    result = await _dispatch("run-prompt", {"message": "Summarise the notes"})

    assert result.state == InstanceState.DEGRADED, result.failure
    assert len(spawned) == 1
    assert "Summarise the notes" in spawned[0]["task"]
    assert not spawned[0].get("parent_session_key")


async def test_a_second_opinion_step_works_in_its_runs_folder_as_its_run(asked, tmp_path) -> None:
    """🔴 Red before: a step's handoff was recorded as no one's. It works and writes its brief in
    the run's folder, and its audit rows name the run."""
    result = await _dispatch("second-opinion", dict(HANDOFF), cwd=str(tmp_path))

    assert result.state == InstanceState.DONE, result.failure
    assert asked[0]["workspace"] == str(tmp_path)
    assert asked[0]["brief_dir"] == ""
    assert asked[0]["session_key"] == "unattended:workflow:run-own"


async def test_a_second_opinion_step_of_a_run_with_no_folder_does_not_start(asked) -> None:
    """🔴 Red before: with nothing naming a folder, the handoff worked in PersonalClaw's own."""
    result = await _dispatch("second-opinion", dict(HANDOFF))

    assert result.state == InstanceState.FAILED
    assert "works only in its own run's folder" in (result.failure.cause_plain or "")
    assert asked == []


# ── an automation's action, which the owner allowed, works as it did ───────


def _fire(**payload: Any) -> ActionContext:
    """An automation's fire, as the trigger dispatch builds it."""
    return ActionContext(event="trigger.fired", trigger_id="trigger-nightly", payload=payload)


async def test_an_automations_run_starts_in_the_project_it_names(launched) -> None:
    """CONTROL: the owner's automation names its run's project, and an event's payload does not."""
    result = await RunWorkflowActionProvider().execute(
        {"workflow": CHILD, "project_id": "project-chosen"},
        _fire(project_id="project-from-the-event"),
    )

    assert result.success, result.error
    assert [run.project_id for run in launched] == ["project-chosen"]


async def test_an_automations_prompt_still_pins_the_session_it_names(spawned) -> None:
    """CONTROL: the owner's automation pins its session for continuity."""
    result = await run_prompt.RunPromptActionProvider().execute(
        {"message": "Draft the standup", "session": "cron:standup"}, _fire()
    )

    assert result.success, result.error
    assert spawned[0]["parent_session_key"] == "cron:standup"


async def test_an_automations_handoff_keeps_the_folders_and_session_it_names(asked, tmp_path):
    """CONTROL: the owner's automation names the stalled session and the handoff's folders."""
    briefs = tmp_path / "briefs"
    result = await SecondOpinionActionProvider().execute(
        {**HANDOFF, "workspace": str(tmp_path), "brief_dir": str(briefs), "session_key": "loop-42"},
        _fire(),
    )

    assert result.success, result.error
    assert (asked[0]["workspace"], asked[0]["brief_dir"]) == (str(tmp_path), str(briefs))
    assert asked[0]["session_key"] == "loop-42"


async def test_an_automations_handoff_with_no_folder_does_not_start(asked) -> None:
    """🔴 Red before: it worked in PersonalClaw's own folder."""
    result = await SecondOpinionActionProvider().execute(dict(HANDOFF), _fire())

    assert not result.success
    assert "needs a 'workspace'" in result.error
    assert asked == []


# ── a second opinion's sandbox ──────────────────────────────────────────────


async def test_a_second_opinion_names_no_tier_its_payload_gives(asked, tmp_path) -> None:
    """🔴 Red before: with none in its config it took the payload's tier, else `none`, the host."""
    result = await SecondOpinionActionProvider().execute(
        {**HANDOFF, "workspace": str(tmp_path)}, _fire(sandbox="none")
    )

    assert result.success, result.error
    assert asked[0]["sandbox"] == ""


async def test_a_named_tier_still_reaches_the_handoff(asked, tmp_path) -> None:
    """CONTROL: the stalled run's tier, named by its caller, is the proposer's."""
    await SecondOpinionActionProvider().execute(
        {**HANDOFF, "workspace": str(tmp_path), "sandbox": "docker"}, _fire()
    )

    assert asked[0]["sandbox"] == "docker"


class _FixtureTier:
    """A sandbox tier that records what it was asked to wrap and runs nothing."""

    name = "pc-fixture-tier"
    display_name = "Fixture tier"

    def __init__(self) -> None:
        self.modes: list[str] = []

    def available(self) -> bool:
        return True

    def wrap(self, spec: Any, argv: list[str]) -> Any:
        from personalclaw.sandbox_providers import SandboxUnavailableError

        self.modes.append(spec.mode)
        raise SandboxUnavailableError(
            what="The fixture tier ran nothing", why="it is a test's.", fix="none needed."
        )


@pytest.fixture
def set_up_runner(monkeypatch) -> Any:
    """A cataloged runner whose runtime the owner set up to run in a sandbox tier, at the strict
    OS level, and a CLI path for it that names no real program."""
    from personalclaw.agents.runners import catalog
    from personalclaw.llm import registry as llm_registry
    from personalclaw.proposer import backends
    from personalclaw.sandbox_providers import register_provider, unregister_provider

    defn = catalog()["gemini-cli"]
    owner = llm_registry.ProviderRegistry()
    owner.register_entry(
        llm_registry.ProviderEntry(
            name=defn.runtime_id,
            type="acp_agent",
            model="",
            options={
                "command": ["/nonexistent/pc-fixture-agent"],
                "sandbox": _FixtureTier.name,
                "sandbox_mode": "strict",
            },
        )
    )
    monkeypatch.setattr(llm_registry, "_default_registry", owner)
    monkeypatch.setattr(backends, "resolve_runner_command", lambda d: ["/nonexistent/pc-fixture"])
    tier = _FixtureTier()
    register_provider(tier)  # type: ignore[arg-type]
    try:
        yield SimpleNamespace(defn=defn, tier=tier)
    finally:
        unregister_provider(_FixtureTier.name)


async def test_a_second_opinion_runs_its_runner_in_the_tier_its_runtime_is_set_up_with(
    set_up_runner, tmp_path: Path
) -> None:
    """🔴 Red before: a handoff that named no tier ran the runner as `none`, on the host, though
    every session of that runner runs in the tier its runtime names."""
    from personalclaw.proposer.backends import RunnerProposerBackend
    from personalclaw.proposer.brief import build_brief

    backend = RunnerProposerBackend(set_up_runner.defn)
    brief = build_brief(goal="g", stuck_at="s", workspace=str(tmp_path), origin_runner="codex")

    prepared = await backend.prepare(brief)
    ref = await backend.invoke(prepared)

    assert prepared.sandbox == _FixtureTier.name
    assert set_up_runner.tier.modes == ["strict"]
    assert not ref.launched
    assert "The fixture tier ran nothing" in ref.error


async def test_the_tier_a_handoff_names_wins_over_the_runtimes(set_up_runner, tmp_path) -> None:
    """CONTROL: a tier the handoff names is the stalled run's, and the proposer gets it."""
    from personalclaw.proposer.backends import RunnerProposerBackend
    from personalclaw.proposer.brief import build_brief

    brief = build_brief(
        goal="g", stuck_at="s", workspace=str(tmp_path), origin_runner="codex", sandbox="docker"
    )

    prepared = await RunnerProposerBackend(set_up_runner.defn).prepare(brief)

    assert prepared.sandbox == "docker"


# ── the planner that writes templates is not offered them ──────────────────


@pytest.mark.parametrize("provider", sorted(OWN_ARGUMENTS))
def test_the_planner_is_not_offered_an_argument_a_step_may_not_set(provider: str) -> None:
    """🔴 Red before: the planner was told each provider takes these, and a spec it wrote with
    one would now be refused."""
    from personalclaw.workflows.grounding import build_bundle
    from personalclaw.workflows.step_arguments import RUN_SCOPED_ARGUMENTS

    bundle = build_bundle(include_mcp=False)
    offered = {
        name
        for name, _type, _required in next(p for p in bundle.providers if p.name == provider).fields
    }

    held, _why = RUN_SCOPED_ARGUMENTS[provider]
    assert offered, f"{provider} lists no arguments at all, so this check proves nothing"
    assert not offered & set(held)
