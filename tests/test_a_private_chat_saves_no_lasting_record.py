"""An Incognito or Temporary chat's work saves nothing that other models later read.

Such a chat's words stay with it: only the model it runs on sees them, and its work keeps nothing.
Its agent could still leave records behind that other work reads later: a skill draft, which is
kept after the chat and becomes a skill the model of every chat reads; a proposal for review (a
skill, a workflow template, a change to a project's instructions), which every chat's model reads
once accepted; a task, a task list or a project on the Tasks page, whose brief and instructions are
put before every chat and loop in it; an Inbox item, which the agents of your other chats read; and
a loop's spec or plan, which its worker and planner run from. Nothing in those stores read the
chat's mode.

Each is refused now at the one place every door to it reaches (``lasting_work``), before anything
is written, with a sentence that says why and where it can be done instead. Driven as each door is
driven: a native agent's turn whose scripted model calls the real tool, the real ``mcp-core``
process an agent CLI runs, the gateway's tool route that process can call with the internal
credential, and the gateway's routes, each beside an ordinary chat that still saves.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
from test_a_private_chat_controls_only_its_own_runs import _Scripted
from test_an_agent_clis_workflow_tools_reach_the_gateway import (  # noqa: F401 - a fixture
    CLI_MODEL,
    TEMPORARY_KEY,
    _tool_server,
    gateway,
)

from personalclaw import lasting_work, memory_writes, project_context, session_restrictions
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider
from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.dashboard.chat_forget import purge_chat
from personalclaw.dashboard.handlers.loop_routes import _build_loop_from_body
from personalclaw.inbox_providers import native_source
from personalclaw.learning import proposals, staging, template_gate
from personalclaw.llm.events import EVENT_PERMISSION_REQUEST, EVENT_TOOL_RESULT
from personalclaw.loop import files as loop_files
from personalclaw.loop import store as loop_store
from personalclaw.planning import session as plan
from personalclaw.skills import ephemeral
from personalclaw.tasks import registry
from personalclaw.tasks.hierarchy import HierarchyStore
from personalclaw.workflows.failure_taxonomy import classify_exception
from personalclaw.workflows.models import FailureClass

#: An Incognito chat and an ordinary one, live in the gateway, and the model each turn runs on.
INCOGNITO_KEY = "dashboard:chat-records-incognito"
ORDINARY_KEY = "dashboard:chat-records-ordinary"
CHAT_MODEL = "local:scripted-chat-model"

#: What the person said in the chat, which the agent would hand to what it saves.
WORDS = "Plan the surprise party for my sister at the lake house in May"

#: A procedure the template gate files: three actions, each reading the last, with slots.
STEPS = [
    "fetch the guest list for {{event}}",
    "summarize the result for {{host}}",
    "notify {{host}} with the output",
]
#: A procedure the gate declines, and records that it declined: nothing in it is a slot.
NO_SLOTS = ["fetch the guest list", "summarize the result"]


@pytest.fixture
def chats(gateway) -> Iterator[None]:  # noqa: F811 - the imported fixture
    """The Incognito chat, the ordinary chat and the agent CLI's Temporary chat, live in the
    gateway as each is while its agent's turn runs, each on the model its turn named; and the
    learning ledger the template gate records in, opened on the gateway's home."""
    for key, mode in ((INCOGNITO_KEY, "incognito"), (ORDINARY_KEY, "persistent")):
        gateway.state.get_or_create_session(key.removeprefix("dashboard:"), memory_mode=mode)
    gateway.state.get_or_create_session(
        TEMPORARY_KEY.removeprefix("dashboard:"), memory_mode="temporary"
    )
    session_restrictions.mark_own_model(INCOGNITO_KEY, CHAT_MODEL)
    session_restrictions.mark_own_model(TEMPORARY_KEY, CLI_MODEL)
    staging.reset_store()
    yield
    staging.reset_store()
    session_restrictions.clear(INCOGNITO_KEY)


class _Agent:
    """A chat's native agent, with the Tasks and Inbox tools and the core tools, and the turn its
    chat runs it in (``memory_writes.runs_as_its_session``, ``answered_by``)."""

    def __init__(self, key: str, mode: str, cwd: Any) -> None:
        self.key, self.mode = key, mode
        self.model = _Scripted()
        self.runtime = NativeAgentRuntime(
            definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
            model_provider=self.model,
            tool_providers=[
                NativeBuiltinToolProvider(cwd, session_key=key, categories={"tasks", "inbox"}),
                InProcessMcpToolProvider(),
            ],
            session_key=key,
        )
        self.runtime.set_task_mode("agent")

    async def calls(self, tool: str, arguments: dict[str, Any]) -> SimpleNamespace:
        """One turn in which the agent calls *tool*; its owner allows the call when asked. Returns
        whether it succeeded, what the model was answered, and how many times anyone was asked."""
        self.model.next(tool, arguments)
        results: list[str] = []
        asked = 0
        with memory_writes.derived_from(self.key, memory_mode=self.mode):
            memory_writes.answered_by(CHAT_MODEL)
            async for event in self.runtime.stream("go"):
                if event.kind == EVENT_PERMISSION_REQUEST:
                    asked += 1
                    await self.runtime.approve_tool(event.request_id)
                elif event.kind == EVENT_TOOL_RESULT:
                    results.append(str(event.tool_output))
        assert len(results) == 1, results
        return SimpleNamespace(ok=not results[0].startswith("Error"), text=results[0], asked=asked)


async def _agent(key: str, mode: str, cwd: Any) -> _Agent:
    # The gateway's worker threads carry the work's scope (`carry_scope_into_worker_threads`).
    memory_writes.carry_scope_into_worker_threads(asyncio.get_running_loop())
    agent = _Agent(key, mode, cwd)
    await agent.runtime.start()
    return agent


def _says_why(text: str, mode: str, lasts: str, undone: str) -> None:
    assert (
        f"This chat is {mode}, so nothing from it is sent to any model but the one it runs on"
        in text
    ), text
    assert lasts in text, text
    assert undone in text, text


def _drafts(key: str) -> list[ephemeral.EphemeralSkill]:
    return ephemeral.list_drafts(key)


def _filed() -> list[proposals.Proposal]:
    return proposals.list_pending()


def _declines_recorded() -> dict[str, int]:
    """What the template gate recorded declining, by reason."""
    return template_gate.skip_counts(days=1)


async def _tasks() -> list[Any]:
    tasks, _total = await registry.list_all_tasks(limit=registry.MAX_TASK_PAGE)
    return tasks


def _inbox(gw: SimpleNamespace) -> list[str]:
    items = native_source.open_inbox_items(state=gw.state) or []
    return [item.message for item in items]


async def _owners_task() -> str:
    """A task of yours, made on the Tasks page."""
    made = await registry.create_task(title="Book the band for the reunion")
    return made.id


def _skill_calls() -> list[tuple[str, dict[str, Any]]]:
    """The calls that would keep a skill, or file a proposal, holding the chat's words."""
    return [
        ("skill_remember", {"title": "party planning", "body": WORDS}),
        (
            "skill_promote",
            {
                "name": "party planning",
                "description": "When a family party is being planned.",
                "procedure": WORDS,
                "rationale": "It came up and worked.",
            },
        ),
        (
            "template_save_from_session",
            {"name": "party-planning", "description": WORDS, "steps": STEPS},
        ),
        (
            "project_context_review",
            {
                "project_id": "p-0000party",
                "items": [
                    {"kind": "project_instruction", "body": WORDS, "rationale": "Said today."}
                ],
            },
        ),
    ]


# ── a native agent, in the gateway ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_incognito_chats_agent_keeps_no_skill_and_files_no_proposal(
    gateway, chats, tmp_path  # noqa: F811 - the imported fixture
):
    """🔴 Before: the skill draft was saved, live in the chat and offered at its end as a skill for
    every chat, and the skill, the template and the project instruction were filed for review,
    each holding the chat's words, after you were asked to allow each call."""
    agent = await _agent(INCOGNITO_KEY, "incognito", tmp_path)

    answers = [await agent.calls(tool, arguments) for tool, arguments in _skill_calls()]

    for answer, (lasts, undone) in zip(
        answers,
        (
            ("a skill is kept after the chat", "none was saved"),
            ("a proposal is kept after the chat for your review", "none was filed"),
            ("a proposal is kept after the chat for your review", "none was filed"),
            ("a proposal is kept after the chat for your review", "none was filed"),
        ),
        strict=True,
    ):
        assert not answer.ok, answer.text
        _says_why(answer.text, "Incognito", lasts, undone)
        assert answer.asked == 0
    assert _drafts(INCOGNITO_KEY) == []
    assert _filed() == []


@pytest.mark.asyncio
async def test_an_ordinary_chats_agent_keeps_a_skill_and_files_its_proposals(
    gateway, chats, tmp_path  # noqa: F811 - the imported fixture
):
    agent = await _agent(ORDINARY_KEY, "persistent", tmp_path)

    answers = [await agent.calls(tool, arguments) for tool, arguments in _skill_calls()]

    for answer, said in zip(
        answers,
        (
            "Saved a session skill draft",
            "Filed a skill proposal",
            "Filed a DRAFT template proposal",
            "Filed 1 project-context proposal",
        ),
        strict=True,
    ):
        assert answer.ok and said in answer.text, answer.text
    assert [draft.body for draft in _drafts(ORDINARY_KEY)] == [WORDS]
    assert sorted(p.kind for p in _filed()) == ["project_instruction", "skill", "template"]


@pytest.mark.asyncio
async def test_an_incognito_chats_agent_saves_no_task_project_or_inbox_item(
    gateway, chats, tmp_path  # noqa: F811 - the imported fixture
):
    """🔴 Before: a task and a project carried the chat's words onto the Tasks page (a project's
    instructions are put before every chat and loop in it), your task's description was replaced
    by them, and they were posted to the Inbox the agents of your other chats read."""
    yours = await _owners_task()
    agent = await _agent(INCOGNITO_KEY, "incognito", tmp_path)

    answers = [
        await agent.calls("task_create", {"title": WORDS}),
        await agent.calls("task_update", {"id": yours, "description": WORDS}),
        await agent.calls(
            "project_create", {"name": "Lake house", "agent_instructions_template": WORDS}
        ),
        await agent.calls("task_list_create", {"name": WORDS}),
        await agent.calls("post_to_inbox", {"message": WORDS}),
    ]

    tasks_page = "a task, task list or project on the Tasks page is kept after the chat"
    for answer, (lasts, undone) in zip(
        answers,
        (
            (tasks_page, "none was created"),
            (tasks_page, "it was left as it is"),
            (tasks_page, "none was created"),
            (tasks_page, "none was created"),
            ("an Inbox item is kept after the chat", "nothing was posted"),
        ),
        strict=True,
    ):
        assert not answer.ok, answer.text
        _says_why(answer.text, "Incognito", lasts, undone)
        assert answer.asked == 0
    assert [(t.id, t.description) for t in await _tasks()] == [(yours, "")]
    assert not any(p.name == "Lake house" for p in HierarchyStore().list_projects())
    assert not any(tl.name == WORDS for tl in HierarchyStore().list_task_lists())
    assert WORDS not in _inbox(gateway)


@pytest.mark.asyncio
async def test_an_ordinary_chats_agent_saves_tasks_projects_and_inbox_items(
    gateway, chats, tmp_path  # noqa: F811 - the imported fixture
):
    yours = await _owners_task()
    agent = await _agent(ORDINARY_KEY, "persistent", tmp_path)

    answers = [
        await agent.calls("task_create", {"title": WORDS}),
        await agent.calls("task_update", {"id": yours, "description": WORDS}),
        await agent.calls(
            "project_create", {"name": "Lake house", "agent_instructions_template": WORDS}
        ),
        await agent.calls("task_list_create", {"name": "Guests"}),
        await agent.calls("post_to_inbox", {"message": WORDS}),
    ]

    assert all(answer.ok for answer in answers), [a.text for a in answers]
    assert sorted(t.title for t in await _tasks()) == ["Book the band for the reunion", WORDS]
    [project] = [p for p in HierarchyStore().list_projects() if p.name == "Lake house"]
    assert project.agent_instructions_template == WORDS
    assert WORDS in _inbox(gateway)


# ── an agent CLI's tool server, and the gateway's tool route it can reach ───────────────────────


@pytest.mark.asyncio
async def test_a_temporary_agent_clis_tools_keep_no_skill_and_file_no_proposal(
    gateway, chats  # noqa: F811 - the imported fixture
):
    """🔴 Before: the real tool server saved the skill draft, filed the skill, the template and
    the project instruction for review, and recorded the declined procedure in the learning
    ledger, each holding the chat's words."""
    calls = [
        *_skill_calls(),
        ("template_save_from_session", {"name": "party-checklist", "steps": NO_SLOTS}),
    ]

    answers = await _tool_server(gateway, *calls, key=TEMPORARY_KEY)

    for ok, text in answers:
        assert not ok, text
        assert text.startswith(f"Error [{lasting_work.CODE}]: This chat is Temporary"), text
        assert "from an ordinary chat" in text, text
    assert _drafts(TEMPORARY_KEY) == []
    assert _filed() == []
    assert _declines_recorded() == {}


async def _internal(gw: SimpleNamespace, path: str, body: dict[str, Any]) -> tuple[int, Any]:
    """A call an agent CLI's process makes with the internal credential, naming its chat."""
    secret = (gw.home / ".local_secret").read_text(encoding="utf-8").strip()
    async with aiohttp.ClientSession() as http:
        resp = await http.post(
            f"http://127.0.0.1:{gw.port}{path}",
            json=body,
            headers={"X-Internal-Secret": secret, "X-Session-Key": TEMPORARY_KEY},
        )
        return resp.status, await resp.json(content_type=None)


@pytest.mark.asyncio
async def test_a_temporary_agent_clis_call_through_the_gateway_saves_no_task_or_inbox_item(
    gateway, chats  # noqa: F811 - the imported fixture
):
    """The tool route runs a tool as the work of the chat the call names, so the Tasks and Inbox
    tools an agent CLI has no server of its own for are held to the chat there too."""
    calls = [
        ("task_create", {"title": WORDS}),
        ("project_create", {"name": "Lake house", "agent_instructions_template": WORDS}),
        ("task_list_create", {"name": WORDS}),
        ("post_to_inbox", {"message": WORDS}),
    ]

    answers = [
        await _internal(gateway, "/api/tools/invoke", {"tool": tool, "arguments": arguments})
        for tool, arguments in calls
    ]

    for status, answer in answers:
        assert status == 200 and answer["ok"] is False, answer
        assert answer["error"].startswith("This chat is Temporary"), answer
        assert answer["not_run"] == "refused_by_tool", answer
    assert await _tasks() == []
    assert not any(p.name == "Lake house" for p in HierarchyStore().list_projects())
    assert WORDS not in _inbox(gateway)


# ── the gateway's routes ────────────────────────────────────────────────────────────────────────


async def _request(
    gw: SimpleNamespace,
    method: str,
    path: str,
    *,
    work: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """A request with your sign-in, naming *work* as the session it is made for."""
    secret = (gw.home / ".local_secret").read_text(encoding="utf-8").strip()
    async with aiohttp.ClientSession() as http:
        token = await http.get(
            f"http://127.0.0.1:{gw.port}/api/token/local", headers={"X-Local-Secret": secret}
        )
        bearer = (await token.json())["token"]
        resp = await http.request(
            method,
            f"http://127.0.0.1:{gw.port}{path}",
            json=body,
            headers={
                "Authorization": f"Bearer {bearer}",
                "X-Session-Key": work,
                **(headers or {}),
            },
        )
        return resp.status, await resp.json(content_type=None)


def _owners_loop_awaiting_review() -> tuple[str, str]:
    """A Goal loop of yours, still a draft, whose planning walkthrough waits on your review of
    its first step. Returns the loop's id and the step's."""
    made = loop_store.create(
        _build_loop_from_body({"kind": "goal", "task": "Compare the three quotes for the roof"})
    )
    step = plan.PlanStep(
        id="intent",
        kind="intent",
        title="Intent",
        status=plan.StepStatus.AWAITING_REVIEW.value,
        artifact={"markdown": "Pick the roofer by Friday."},
    )
    loop_files.write_plan_session(plan.PlanSession(project_id=made.id, steps=[step]))
    return made.id, step.id


def _refused(answer: tuple[int, Any], undone: str) -> None:
    status, body = answer
    assert status == 403, body
    assert body["error"]["code"] == lasting_work.CODE, body
    assert body["error"]["message"].startswith("This chat is Temporary"), body
    assert undone in body["error"]["message"], body


@pytest.mark.asyncio
async def test_a_request_made_for_a_temporary_chat_changes_no_loop_spec_or_plan(
    gateway, chats  # noqa: F811 - the imported fixture
):
    """A loop's spec and its plan are what its worker and its planner run from, on a model of
    their own. 🔴 Before: the chat's words replaced your loop's task and the reviewed step's
    draft, a comment carrying them went to the planner, and planning started. Your own edits
    from the loop's page are made."""
    loop_id, step_id = _owners_loop_awaiting_review()
    session_before = loop_files.read_plan_session(loop_id).to_dict()

    edited = await _request(
        gateway, "PUT", f"/api/loops/{loop_id}", work=TEMPORARY_KEY, body={"task": WORDS}
    )
    renamed = await _request(
        gateway, "PUT", f"/api/loops/{loop_id}", work=TEMPORARY_KEY, body={"name": WORDS}
    )
    planning = [
        await _request(gateway, "POST", f"/api/loops/{loop_id}/plan/{route}", **request)
        for route, request in (
            ("start", {"work": TEMPORARY_KEY}),
            ("comment", {"work": TEMPORARY_KEY, "body": {"step_id": step_id, "text": WORDS}}),
            ("edit", {"work": TEMPORARY_KEY, "body": {"step_id": step_id, "markdown": WORDS}}),
            ("approve", {"work": TEMPORARY_KEY, "body": {"step_id": step_id}}),
        )
    ]

    _refused(edited, "it was left as it is. Change it on its page.")
    _refused(renamed, "it was left as it is. Change it on its page.")
    for answer in planning:
        _refused(answer, "its plan was left as it is. Plan it on its page.")
    loop = loop_store.get(loop_id)
    assert loop.task == "Compare the three quotes for the roof"
    assert WORDS not in (loop.name or "")
    assert loop_files.read_plan_session(loop_id).to_dict() == session_before

    step = loop_files.read_plan_session(loop_id).steps[0]
    yours = await _request(
        gateway, "PUT", f"/api/loops/{loop_id}", work="dashboard:ui", body={"task": WORDS}
    )
    your_edit = await _request(
        gateway,
        "POST",
        f"/api/loops/{loop_id}/plan/edit",
        work="dashboard:ui",
        body={"step_id": step_id, "markdown": "Pick the roofer by Monday."},
        headers={"If-Match": plan.step_revision(step)},
    )
    assert yours[0] == 200 and loop_store.get(loop_id).task == WORDS, yours
    assert your_edit[0] == 200, your_edit
    assert loop_files.read_plan_session(loop_id).steps[0].artifact["markdown"] == (
        "Pick the roofer by Monday."
    )


@pytest.mark.asyncio
async def test_a_request_made_for_a_temporary_chat_saves_no_task_project_or_skill(
    gateway, chats  # noqa: F811 - the imported fixture
):
    """The Tasks routes and the skill-draft route reach the same stores: refused 403
    ``restricted_session`` in the refusal's words, and your own request is made."""
    ephemeral.remember(ORDINARY_KEY, "party planning", "Ask about the cake first.")

    task = await _request(gateway, "POST", "/api/tasks", work=TEMPORARY_KEY, body={"title": WORDS})
    project = await _request(
        gateway, "POST", "/api/projects", work=TEMPORARY_KEY, body={"name": WORDS}
    )
    kept = await _request(
        gateway,
        "POST",
        f"/api/skills/ephemeral/{ORDINARY_KEY}/promote",
        work=TEMPORARY_KEY,
        body={"slug": "party-planning", "scope": "global", "body": WORDS},
    )
    yours = await _request(
        gateway, "POST", "/api/tasks", work="dashboard:ui", body={"title": "Order the cake"}
    )

    _refused(task, "none was created")
    _refused(project, "none was created")
    _refused(kept, "none was saved")
    assert [t.title for t in await _tasks()] == ["Order the cake"], yours
    assert not any(p.name == WORDS for p in HierarchyStore().list_projects())
    assert [d.body for d in _drafts(ORDINARY_KEY)] == ["Ask about the cake first."]


# ── the answers each door relies on ─────────────────────────────────────────────────────────────


def _audited(monkeypatch) -> list[dict[str, Any]]:
    """The rows the Security log is given."""
    rows: list[dict[str, Any]] = []
    log = SimpleNamespace(log_api_access=lambda **row: rows.append(row))
    monkeypatch.setattr("personalclaw.sel.sel", lambda: log)
    return rows


def test_a_run_a_private_chat_started_leaves_its_project_no_overview(tmp_path, monkeypatch):
    """A project's overview and ledgers are put before every session inside it, so a run a
    Temporary chat started, which runs as the chat's own work, writes neither when it ends
    (``run_finish.revise_project_overview``), nor is a refusal recorded for a write its work
    never asked for; one it asks for is refused. A run of yours writes both."""
    from personalclaw.workflows import run_finish

    monkeypatch.setattr("personalclaw.tasks.hierarchy.config_dir", lambda: tmp_path)
    audited = _audited(monkeypatch)
    project = HierarchyStore().create_project("Lake house")
    run = SimpleNamespace(
        project_id=project.id,
        workflow_name="party-plan",
        status=SimpleNamespace(value="complete"),
        extra={"summary": WORDS},
        id="r1",
    )

    with memory_writes.derived_from(TEMPORARY_KEY, memory_mode="temporary"):
        run_finish.revise_project_overview(SimpleNamespace(run=run))
        assert audited == []
        with pytest.raises(lasting_work.Refused, match="it was left as it is"):
            project_context.append_ledger(project.id, "decisions", WORDS)

    assert [row["operation"] for row in audited] == ["tasks_change"]
    assert project_context.read_overview(project.id) == ""
    assert project_context.read_ledger(project.id, "decisions") == []
    run_finish.revise_project_overview(SimpleNamespace(run=run))
    assert WORDS in project_context.read_overview(project.id)


def test_a_run_a_private_chat_started_puts_none_of_its_steps_on_the_tasks_page(
    tmp_path, monkeypatch
):
    """🔴 Before: each step of a run that settled was put on the Tasks page as a task
    (``task_projection``), titled in the chat's words, which your other chats and loops read and
    which outlived the run. A Temporary or Incognito chat's run puts none there, and no refusal is
    recorded for a write its work never asked for; a run of yours still does."""
    from personalclaw.action_providers import registry as action_providers
    from personalclaw.workflows import ownership
    from personalclaw.workflows import store as runs
    from personalclaw.workflows.controller import EngineServices, RunController
    from personalclaw.workflows.journal import TASK_MATERIALIZED, ledger
    from personalclaw.workflows.models import WorkflowRun

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(runs, "config_dir", lambda: tmp_path)
    action_providers._ensure_default_providers_registered()
    audited = _audited(monkeypatch)
    step = {"provider": "bash", "with": {"command": "true"}}
    spec = {
        "name": "party",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [{"kind": "action", "id": "book", "label": WORDS, "config": step}],
        },
    }

    def projected_by_a_run_of(mode: str) -> list[dict[str, Any]]:
        keeps = ownership.MemoryMode(mode)
        extra = {}
        if keeps is not ownership.MemoryMode.NORMAL:
            extra = ownership.stamp_run_mode({}, keeps, model=CHAT_MODEL)
        run = runs.create(WorkflowRun(id=f"r-{mode}", workflow_name="party", extra=extra))
        controller = RunController(run, spec, services=EngineServices(publish=lambda *_: None))

        async def _runs_to_its_end() -> None:
            await controller.run_to_completion()
            await asyncio.gather(*list(controller._projection_writes))

        asyncio.run(_runs_to_its_end())
        assert run.status.value == "complete", run.status
        return [row for row in ledger(run.id) if row["kind"] == TASK_MATERIALIZED]

    assert projected_by_a_run_of("temporary") == []
    assert projected_by_a_run_of("incognito") == []
    assert asyncio.run(_tasks()) == []
    assert audited == []

    [yours] = projected_by_a_run_of("normal")
    assert yours["node_id"] == "book" and yours["task_id"]
    assert [task.title for task in asyncio.run(_tasks())] == [WORDS]


def test_a_step_refused_for_a_private_chats_run_is_the_users_to_change():
    """A run's step the rule refuses failed as the run's chat decides, not as a fault: no Retry
    changes it, and the run page says why in the refusal's words."""
    why = "This chat is Temporary, so nothing from it is sent to any model but the one it runs on"

    failure = classify_exception(lasting_work.Refused(why))

    assert failure.failure_class is FailureClass.USER
    assert not failure.retryable
    assert failure.cause_plain == why
    assert "ordinary chat" in failure.remediation
    asked = classify_exception(lasting_work.Refused("Nothing was set up", code=lasting_work.ASKED))
    assert asked.failure_class is FailureClass.USER and "ask the owner" in asked.remediation


def test_forgetting_a_chat_forgets_the_skills_it_was_taught(tmp_path, monkeypatch):
    """A chat's skill drafts are kept for its review at its end, under its key; forgetting the
    chat forgets them, so a chat named after it later is not handed them as its own. Another
    chat's drafts stay."""
    monkeypatch.setattr("personalclaw.skills.ephemeral.skills_dir", lambda: tmp_path / "skills")
    ephemeral.remember("dashboard:chat-7-100", "party planning", WORDS)
    ephemeral.remember("dashboard:chat-8-200", "roof quotes", "Ask for three.")
    state = SimpleNamespace(conversation_log=None)

    purge_chat(state, "dashboard:chat-7-100", keys={"dashboard:chat-7-100", "chat-7-100"})

    assert ephemeral.list_drafts("dashboard:chat-7-100") == []
    assert [d.title for d in ephemeral.list_drafts("dashboard:chat-8-200")] == ["roof quotes"]


def test_work_whose_chat_cannot_be_read_saves_no_record_and_says_so(tmp_path, monkeypatch):
    """A chat whose memory setting nothing can read is held to the same rule, saying so; work that
    derives from no chat is not held to it."""
    monkeypatch.setattr("personalclaw.skills.ephemeral.skills_dir", lambda: tmp_path / "skills")
    key = "dashboard:chat-records-unreadable"
    session_restrictions.mark_unreadable(key)
    try:
        with memory_writes.derived_from(key):
            with pytest.raises(lasting_work.Refused) as refused:
                ephemeral.remember(key, "party planning", WORDS)
            with pytest.raises(lasting_work.Refused):
                proposals.enqueue(kind="skill", title="party", body=WORDS)
    finally:
        session_restrictions.clear(key)

    assert str(refused.value).startswith("This chat's memory setting cannot be read")
    assert ephemeral.list_drafts(key) == []
    assert ephemeral.remember(key, "party planning", WORDS) is not None


def test_every_core_tool_that_keeps_a_record_says_so_before_it_is_allowed():
    """The core tools whose stores refuse such work are the ones whose preflight asks the rule,
    so a call is refused before anyone is asked to allow it, not after."""
    from personalclaw import mcp_core

    listed = {tool["name"] for tool in mcp_core._list_tools()}
    assert set(mcp_core._LASTING) <= listed
    with memory_writes.derived_from(TEMPORARY_KEY, memory_mode="temporary"):
        for name, arguments in _skill_calls():
            refused = mcp_core._preflight(name, arguments)
            assert refused is not None and "This chat is Temporary" in refused.reason, name


@pytest.mark.asyncio
async def test_every_native_tool_that_keeps_a_record_says_so_before_it_is_allowed(tmp_path):
    """The native tools whose stores refuse such work are named in one table, which the
    provider's preflight asks: each is a tool it serves, refused before anyone is asked."""
    from personalclaw.agents.native import lasting_tools

    tools = NativeBuiltinToolProvider(tmp_path, session_key=TEMPORARY_KEY)
    assert all(getattr(tools, f"_t_{name}", None) for name in lasting_tools.TOOLS)
    with memory_writes.derived_from(TEMPORARY_KEY, memory_mode="temporary"):
        for name in lasting_tools.TOOLS:
            refused = await tools.preflight(name, {})
            assert refused is not None and "This chat is Temporary" in refused.error, name
    assert await tools.preflight("task_create", {"title": WORDS}) is None


def test_the_proposal_queue_files_nothing_for_a_private_chat_and_says_why(tmp_path, monkeypatch):
    """The one queue every proposal reaches: a skill, a template, a change to a project, a
    knowledge draft. Refused before anything is fingerprinted, filed or put in the Inbox."""
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)

    with memory_writes.derived_from(INCOGNITO_KEY, memory_mode="incognito"):
        with pytest.raises(lasting_work.Refused, match="none was filed"):
            proposals.enqueue(kind="knowledge_draft", title="Lake house", body=WORDS)

    assert proposals.list_pending() == []
    verdict, filed = proposals.enqueue(kind="knowledge_draft", title="Lake house", body=WORDS)
    assert filed is not None and filed.body == WORDS, verdict
