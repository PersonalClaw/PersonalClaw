"""A Yes on a Morning triage proposal does what the proposal says, and nothing wider.

Measured on the tree before this was written:

* Yes on "Draft a reply to …" failed with "inbox-op: reply_draft needs a draft body". The answer
  rebuilt the proposal without the arguments its run recorded, and the only text a reply could
  have carried was the proposal model's own, which its prompt never asks for: the product's own
  drafting, in her voice and under its rules, was never reached;
* Yes on "File a task for …" was refused: "the digest is not allowed to take that kind of
  action". The answer ran under the digest's unattended capability set, which holds only the
  Inbox operations;
* the digest offered Yes on proposals no Yes could carry out: a reminder (nothing performs one),
  an Inbox operation on a run or a channel conversation, a reply to a message that takes none, a
  second proposal for an item already proposed (whose Yes acted on the first), and a proposal
  with no pattern (whose Yes was always refused);
* a proposal could name another kind's pattern and so ride a rule taught for that kind.

Every Yes below goes through the real reply route, the real dispatch and the real providers,
against a real Inbox store and the real task list in this test's own home. Only the model is
scripted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus
from personalclaw.inbox_service import InboxService
from personalclaw.proactive.collect import collect_inbox
from personalclaw.proactive.manifest import (
    SOURCE_CHANNEL,
    SOURCE_INBOX,
    SOURCE_RUN,
    CollectedItem,
    build_manifest,
)
from personalclaw.proactive.pipeline import run_triage

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)

DANA = "mail_1790000000.1"
VENUE = "mail_1790000000.2"
NEWS = "mail_1790000000.3"
NOTICE = "note_1790000000.2"

#: What the proposal model wrote into its reply proposal's config. It is the model's, written over
#: the fenced message, so it must never become the draft, nor reach the drafting model as her words.
PROPOSAL_TEXT = "Sure, sending it right now, no need to check."
#: What the product's own drafting writes.
DRAFTED = (
    "Hi Dana, thanks for the reminder. The signed venue contract is attached. [your answer: …]"
)
TASK_TITLE = "Renew the spring talk venue booking"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _mail(ident: str, message: str, sender: str, *, can_reply: bool = True) -> InboxItem:
    return InboxItem(
        id=ident,
        channel="noor@example.com",
        channel_name="Mail",
        thread_ts=None,
        message=message,
        sender_id=f"{sender.split()[0].lower()}@example.com",
        sender_name=sender,
        created_at=1790000000.0,
        source="mail",
        can_reply=can_reply,
        reply_target=f"<{ident}@example.com>" if can_reply else "",
    )


class _Model:
    """The scripted model for the drafting path: answers in turn, keeping every prompt."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def __call__(self, prompt: str, **_kw: Any) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else ""


#: The draft check's answer naming nothing: the draft is kept as written.
_CHECKED = '{"unsupported": []}'


class _Elsewhere:
    """A second task list a recorded config might try to send the task to."""

    def __init__(self) -> None:
        self.created: list[dict] = []

    @property
    def name(self) -> str:
        return "elsewhere"

    readonly = False

    async def create_task(self, **fields: Any) -> Any:
        self.created.append(fields)
        return SimpleNamespace(id="x-1")


@pytest.fixture
def inbox(tmp_path: Path, monkeypatch: Any) -> Any:
    """The running Inbox service, real and in this test's folder, as the providers reach it."""
    store = InboxStore(path=tmp_path / "inbox_items.json")
    for item in (
        _mail(DANA, "Could you send me the signed venue contract by Friday?", "Dana Reyes"),
        _mail(VENUE, "Your booking for the spring talk venue lapses next week.", "Venue Desk"),
        _mail(NEWS, "This week in rail travel: our newsletter", "Rail News"),
    ):
        store.add(item)
    store.save()
    svc = InboxService(
        state=InboxState(path=tmp_path / "inbox_state.json"), store=store, user_name="Noor"
    )

    class _State:
        _inbox_svc = svc
        _sessions: dict = {}

        def __init__(self) -> None:
            self.broadcasts: list[tuple[str, Any]] = []

        def broadcast_ws(self, event: str, payload: Any) -> None:
            self.broadcasts.append((event, payload))

        def notify(self, **_kw: Any) -> None:
            return None

        def push_refresh(self, *_kinds: str) -> None:
            return None

    state = _State()
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(state=state),
    )
    return SimpleNamespace(svc=svc, store=store, state=state)


async def _record_a_digest(inbox: Any, proposals: list[dict]) -> dict:
    """One real digest run over the live Inbox, the model answering with *proposals*: its output."""

    async def completion(prompt: str, **_kw: Any) -> Any:
        return {"proposals": proposals}

    result = await run_triage(
        collect_inbox(inbox.store),
        gate_enabled=False,
        window_start="2026-09-20T08:00:00+00:00",
        completion=completion,
        deliver=lambda _digest: True,
    )
    summary = result.summary()
    summary["window_start"] = "2026-09-20T08:00:00+00:00"
    return summary


_PROPOSALS = [
    {
        "item_id": "1",
        "action_type": "reply_draft",
        "tier": "medium",
        "pattern_key": "reply_draft:sender:dana@example.com",
        "action_config": {"draft": PROPOSAL_TEXT},
        "reasoning": "Dana is waiting on the contract.",
    },
    {
        "item_id": "2",
        "action_type": "create_task",
        "tier": "low",
        "pattern_key": "create_task:sender:venue@example.com",
        "action_config": {"title": TASK_TITLE},
        "reasoning": "The booking lapses next week.",
    },
    {
        "item_id": "3",
        "action_type": "archive",
        "tier": "trivial",
        "pattern_key": "archive:sender:rail@example.com",
        "reasoning": "A newsletter.",
    },
]


@pytest.fixture
def reply(inbox: Any, monkeypatch: Any):
    """POST /api/proactive/digest/reply against the digest *output*, with its run's record kept."""
    import personalclaw.dashboard.handlers.proactive as mod
    import personalclaw.proactive.autoexec as autoexec

    output: dict = {}
    events: list[dict] = []
    ledger: list[dict] = []
    asked: list[frozenset[str]] = []
    run = {"run_id": "run-yes", "status": "complete"}

    monkeypatch.setattr(
        mod,
        "_install_state",
        lambda: {"installed": True, "enabled": True, "schedule": None, "drift": False},
    )
    monkeypatch.setattr(mod, "_latest_digest", lambda: (dict(run), output, list(events)))

    def write_reply_row(run_id: str, ordinal: str, **kw: Any) -> bool:
        events.append({"kind": "triage_reply", "item_ordinal": ordinal, **kw})
        return True

    monkeypatch.setattr(mod, "_write_reply_row", write_reply_row)
    monkeypatch.setattr(
        mod,
        "_run_ledger",
        lambda run_id: lambda kind, fields: ledger.append({"kind": kind, **fields}),
    )
    monkeypatch.setattr(mod, "_sel", lambda: SimpleNamespace(log_api_access=lambda **kw: None))
    monkeypatch.setattr(autoexec, "default_budget_check", lambda *a, **k: lambda: (False, ""))

    real_auto_execute = autoexec.auto_execute

    async def watched(proposals: Any, **kwargs: Any) -> Any:
        asked.append(frozenset(kwargs.get("capabilities", autoexec.AUTO_CAPABLE_PROVIDERS)))
        return await real_auto_execute(proposals, **kwargs)

    monkeypatch.setattr(autoexec, "auto_execute", watched)

    @web.middleware
    async def as_owner(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[as_owner])
    app["state"] = SimpleNamespace(_sessions={})
    app.router.add_post("/api/proactive/digest/reply", mod.api_proactive_reply)

    async def send(text: str) -> dict:
        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/api/proactive/digest/reply", json={"run_id": run["run_id"], "text": text}
            )
            assert resp.status == 200, await resp.text()
            return await resp.json()

    return SimpleNamespace(
        send=send, output=output, events=events, ledger=ledger, capabilities=asked
    )


async def _native_tasks() -> list[Any]:
    """The tasks on her own task list, in this test's home."""
    from personalclaw.tasks.registry import list_all_tasks

    tasks, _total = await list_all_tasks(provider_filter="native", limit=50)
    return list(tasks)


# ── a reply ──────────────────────────────────────────────────────────────────────────────────


async def test_a_yes_on_a_reply_proposal_drafts_it_in_her_voice_and_sends_nothing(
    inbox: Any, reply: Any, monkeypatch: Any
) -> None:
    model = _Model(DRAFTED, _CHECKED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))

    body = await reply.send("1 yes")
    (result,) = body["results"]
    assert result["executed"] is True, result
    assert result["not_done"] == ""

    item = inbox.store.items[DANA]
    # The product's own drafting wrote it: the drafting prompt and then its check, the draft
    # names the prompt that wrote it, and the proposal model's words are nowhere in it.
    assert item.draft == DRAFTED
    assert item.drafted_by, "a draft the drafting prompt wrote names that prompt"
    assert len(model.prompts) == 2
    assert "signed venue contract" in model.prompts[0], "the message is what the draft answers"
    assert all(
        PROPOSAL_TEXT not in prompt for prompt in model.prompts
    ), "the proposal model's text must never reach the drafting model as her words"
    # Nothing was sent: the message is still open, no reply was stamped, and the open Inbox was
    # shown the draft.
    assert item.status == ItemStatus.PENDING.value
    assert item.replied_at == 0
    assert any(event == "inbox_item_updated" for event, _ in inbox.state.broadcasts)
    # The answer admitted only what a reply needs.
    assert reply.capabilities == [frozenset({"inbox-op"})]


# ── a task ───────────────────────────────────────────────────────────────────────────────────


async def test_a_yes_on_a_task_proposal_files_the_task_it_names(inbox: Any, reply: Any) -> None:
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))

    body = await reply.send("2 yes")
    (result,) = body["results"]
    assert result["executed"] is True, result

    titles = [task.title for task in await _native_tasks()]
    assert titles == [TASK_TITLE], "the task carries the title its proposal was recorded with"
    # Filing a task touches no Inbox row.
    assert inbox.store.items[VENUE].status == ItemStatus.PENDING.value
    assert reply.capabilities == [frozenset({"create-task"})]


# ── the positive control ─────────────────────────────────────────────────────────────────────


async def test_a_yes_on_an_inbox_operation_still_does_it(inbox: Any, reply: Any) -> None:
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))

    body = await reply.send("3 yes")
    (result,) = body["results"]
    assert result["executed"] is True, result
    assert inbox.store.items[NEWS].status == ItemStatus.HANDLED.value
    assert reply.capabilities == [frozenset({"inbox-op"})]


async def test_the_run_journal_names_her_answer_as_what_allowed_it(inbox: Any, reply: Any) -> None:
    """Her Yes is not a taught rule: the row says who decided, as the tier policy's row does."""
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))

    await reply.send("3 yes")
    (row,) = [row for row in reply.ledger if row["kind"] == "auto_executed"]
    assert row["rule"] == "reply:you-approved"


# ── nothing wider ────────────────────────────────────────────────────────────────────────────


async def test_a_proposal_cannot_widen_what_its_yes_reaches(
    inbox: Any, reply: Any, monkeypatch: Any
) -> None:
    """A recorded config that names another task list, an operation, an item or a capability set
    reaches none of them: the kind and the manifest decide those, and the Yes admits only its own
    kind. As the record of an older process, or one edited on disk, might name."""
    from personalclaw.tasks import registry as task_registry

    elsewhere = _Elsewhere()
    monkeypatch.setitem(task_registry._providers, elsewhere.name, elsewhere)
    output = await _record_a_digest(inbox, _PROPOSALS)
    for row in output["proposals"]:
        if row["action_type"] == "create_task":
            row["action_config"] = {
                "title": TASK_TITLE,
                "provider": "elsewhere",
                "op": "dismiss",
                "item_id": NEWS,
                "capabilities": ["send-message", "invoke-agent"],
                "title_template": "$item_id",
            }
    reply.output.update(output)

    body = await reply.send("2 yes")
    (result,) = body["results"]
    assert result["executed"] is True, result
    assert elsewhere.created == [], "a recorded config chose where the task went"
    assert [task.title for task in await _native_tasks()] == [TASK_TITLE]
    assert inbox.store.items[NEWS].status == ItemStatus.PENDING.value
    assert reply.capabilities == [frozenset({"create-task"})]


async def test_a_proposal_cannot_ride_a_rule_taught_for_another_kind() -> None:
    """A pattern is the proposal's own kind's: one naming another kind teaches and matches
    nothing, so a rule she taught for archiving cannot run a dismissal."""
    from personalclaw.proactive.approval import ApprovalRule, Verdict
    from personalclaw.proactive.autoexec import auto_execute

    items = [CollectedItem(source=SOURCE_INBOX, source_id="mail-x", title="newsletter", ts="1")]
    calls: list[str] = []

    async def completion(prompt: str, **_kw: Any) -> Any:
        return {
            "proposals": [
                {
                    "item_id": "1",
                    "action_type": "dismiss",
                    "tier": "high",
                    "pattern_key": "archive:sender:news.example.com",
                }
            ]
        }

    async def dispatch(provider: str, config: dict, ctx: Any) -> Any:
        calls.append(config["op"])
        return SimpleNamespace(success=True, reversal="", error="")

    async def stage(proposals: Any, manifest: Any) -> Any:
        return await auto_execute(
            proposals,
            manifest=manifest,
            rules=[
                ApprovalRule(pattern="archive:sender:news.example.com", verdict=Verdict.APPROVE)
            ],
            now=NOW,
            enabled=True,
            cap=5,
            dispatch=dispatch,
            budget_check=lambda: (False, ""),
        )

    result = await run_triage(
        items,
        gate_enabled=False,
        completion=completion,
        deliver=lambda _digest: True,
        auto_execute=stage,
    )
    (proposal,) = result.proposals
    assert proposal.pattern_key == ""
    assert calls == [], "a rule taught for archiving ran a dismissal"
    assert result.auto is not None
    assert [d.reason for d in result.auto.deferred] == ["needs_you"]


# ── a proposal with no pattern ───────────────────────────────────────────────────────────────


async def test_a_yes_on_a_proposal_with_no_pattern_still_acts(inbox: Any, reply: Any) -> None:
    """A pattern is what "always" remembers. One Yes needs none: it answers the one proposal."""
    proposals = [dict(p) for p in _PROPOSALS]
    for row in proposals:
        row.pop("pattern_key")
    reply.output.update(await _record_a_digest(inbox, proposals))

    body = await reply.send("3 yes")
    (result,) = body["results"]
    assert result["executed"] is True, result
    assert inbox.store.items[NEWS].status == ItemStatus.HANDLED.value

    # "always" on one has nothing to remember: it says so and still does the one thing.
    body = await reply.send("always yes 2")
    (result,) = body["results"]
    assert result["executed"] is True, result
    assert result["rule"] == ""
    assert "no pattern" in result["rule_error"]


# ── what the digest offers ───────────────────────────────────────────────────────────────────


def _window(tmp_path: Path) -> list[CollectedItem]:
    """A window of four lanes' items: a mail that takes a reply, a message that takes none, a
    channel conversation and a run. The Inbox rows are read by the real collector."""
    store = InboxStore(path=tmp_path / "window_items.json")
    store.add(_mail(DANA, "Could you send me the signed venue contract?", "Dana Reyes"))
    store.add(_mail(NOTICE, "Your nightly backup finished", "Backups", can_reply=False))
    return [
        *collect_inbox(store),
        CollectedItem(
            source=SOURCE_CHANNEL,
            source_id="channel:telegram:42",
            title="unanswered in Family",
            ts="1790000000",
        ),
        CollectedItem(
            source=SOURCE_RUN,
            source_id="run-77",
            title="nightly-sweep: failed",
            materiality="error",
            ts="2026-09-21T03:00:00+00:00",
        ),
    ]


async def test_the_digest_proposes_only_what_a_yes_can_carry_out(tmp_path: Path) -> None:
    window = _window(tmp_path)
    ordinal = {item.source_id: item.ordinal for item in build_manifest(window).items}
    mail, notice = ordinal[DANA], ordinal[NOTICE]
    chat, run = ordinal["channel:telegram:42"], ordinal["run-77"]

    def proposal(item_id: str, action_type: str, **extra: Any) -> dict:
        return {
            "item_id": item_id,
            "action_type": action_type,
            "tier": "low",
            "pattern_key": f"{action_type}:x",
            **extra,
        }

    async def completion(prompt: str, **_kw: Any) -> Any:
        return {
            "proposals": [
                proposal(mail, "reply_draft"),
                # A second proposal for an item already proposed: its Yes would answer the first.
                proposal(mail, "archive"),
                # A notice takes no reply.
                proposal(notice, "reply_draft"),
                # A channel conversation and a run have no Inbox row to archive or mute.
                proposal(chat, "archive"),
                proposal(run, "mute_thread"),
                # Nothing performs a reminder.
                proposal(run, "remind"),
                # Any item can become a task.
                proposal(run, "create_task", action_config={"title": "Look at the failed sweep"}),
            ]
        }

    digests: list[Any] = []
    result = await run_triage(
        window, gate_enabled=False, completion=completion, deliver=digests.append
    )

    assert [(p.item_id, p.action_type) for p in result.proposals] == [
        (mail, "reply_draft"),
        (run, "create_task"),
    ]
    refused = sorted((r.item_id, r.action_type, r.reason) for r in result.refused)
    assert refused == sorted(
        [
            (mail, "archive", "duplicate_item"),
            (notice, "reply_draft", "cannot_act_on_item"),
            (chat, "archive", "cannot_act_on_item"),
            (run, "mute_thread", "cannot_act_on_item"),
            (run, "remind", "unknown_action_type"),
        ]
    )
    # The digest's text offers exactly the two, and names the task it would file.
    (digest,) = digests
    needs = next(block for block in digest.body.split("\n\n") if block.startswith("Needs you:"))
    assert "reply_draft" in needs and "create_task" in needs
    assert "mute_thread" not in needs and "remind" not in needs and "archive" not in needs
    assert "Look at the failed sweep" in needs


def test_every_kind_the_digest_proposes_has_a_way_to_run() -> None:
    """A kind in the action set with no provider is a Yes that can only fail."""
    from personalclaw.action_providers.registry import (
        _ensure_default_providers_registered,
        get_action_provider,
    )
    from personalclaw.proactive.autoexec import PROVIDER_FOR_ACTION
    from personalclaw.proactive.proposals import ACTION_TYPES

    _ensure_default_providers_registered()
    kinds = [kind for kind in ACTION_TYPES if kind != "none"]
    unrunnable = [kind for kind in kinds if kind not in PROVIDER_FOR_ACTION]
    assert unrunnable == []
    for kind in kinds:
        assert get_action_provider(PROVIDER_FOR_ACTION[kind]) is not None, kind

    # The proposal prompt offers exactly these kinds.
    prompt = (
        Path(__file__).resolve().parents[1]
        / "src/personalclaw/config/prompts/task-triage-propose.md"
    ).read_text(encoding="utf-8")
    section = prompt.split("Allowed action types", 1)[1].split("\n\n", 1)[0]
    offered = {line.split('"')[1] for line in section.splitlines() if line.startswith('- "')}
    assert offered == set(kinds)


async def test_the_dispatch_reads_only_what_the_kind_declares() -> None:
    """Whatever a proposal carries, its dispatch names the kind's provider, its operation and the
    manifest's item, and a task's title is filed as text, never read as a template."""
    from personalclaw.proactive.autoexec import auto_execute
    from personalclaw.proactive.proposals import Proposal

    manifest = build_manifest(
        [
            CollectedItem(source=SOURCE_RUN, source_id="run-77", title="sweep: failed", ts="1"),
        ]
    )
    sent: list[tuple[str, dict]] = []

    async def dispatch(provider: str, config: dict, ctx: Any) -> Any:
        sent.append((provider, dict(config)))
        return SimpleNamespace(success=True, reversal="task:native:t-1", error="")

    from personalclaw.proactive.approval import ApprovalRule, Verdict

    await auto_execute(
        [
            Proposal(
                item_id="1",
                action_type="create_task",
                tier="low",
                pattern_key="create_task:run",
                action_config={
                    "title": "Fix the $item_id sweep",
                    "provider": "elsewhere",
                    "op": "dismiss",
                    "item_id": "someone-else",
                    "title_template": "$item_id",
                },
            )
        ],
        manifest=manifest,
        rules=[ApprovalRule(pattern="create_task:run", verdict=Verdict.APPROVE)],
        now=NOW,
        enabled=True,
        cap=1,
        capabilities=frozenset({"create-task"}),
        dispatch=dispatch,
        budget_check=lambda: (False, ""),
    )
    ((provider, config),) = sent
    assert provider == "create-task"
    assert config == {
        "op": "create_task",
        "action_type": "create_task",
        "item_id": "run-77",
        "title": "Fix the $item_id sweep",
        "title_template": "$title",
    }

    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.template import render_template

    # What the task provider renders from it is the title as written.
    title = render_template(config["title_template"], ActionContext(event="t", payload=config))
    assert title == "Fix the $item_id sweep"


async def test_a_models_config_binds_only_what_its_kind_declares(tmp_path: Path) -> None:
    """The run records a task's title and nothing else, and says what it dropped."""

    async def completion(prompt: str, **_kw: Any) -> Any:
        return {
            "proposals": [
                {
                    "item_id": "1",
                    "action_type": "reply_draft",
                    "tier": "medium",
                    "action_config": {"draft": PROPOSAL_TEXT, "send": True},
                },
                {
                    "item_id": "2",
                    "action_type": "create_task",
                    "tier": "low",
                    "action_config": {
                        "title": f"  {TASK_TITLE}\n\twith a second line ",
                        "provider": "elsewhere",
                        "capabilities": ["send-message"],
                    },
                },
            ]
        }

    result = await run_triage(
        _window(tmp_path), gate_enabled=False, completion=completion, deliver=lambda _digest: True
    )
    recorded = {row["action_type"]: row for row in result.summary()["proposals"]}
    assert recorded["reply_draft"]["action_config"] == {}
    assert recorded["create_task"]["action_config"] == {"title": f"{TASK_TITLE} with a second line"}
    assert set(result.batch.extra_keys) >= {
        "action_config.draft",
        "action_config.send",
        "action_config.provider",
        "action_config.capabilities",
    }


# ── the drafting, at the provider ────────────────────────────────────────────────────────────


async def test_a_reply_drafted_for_a_yes_is_undone_by_its_handle(
    inbox: Any, monkeypatch: Any
) -> None:
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.inbox_op_provider import InboxOpActionProvider

    inbox.store.update(DANA, draft="her own words")
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _Model(DRAFTED, _CHECKED))
    provider = InboxOpActionProvider()
    done = await provider.execute(
        {"op": "reply_draft", "item_id": DANA}, ActionContext(event="triage_auto_execute")
    )
    assert done.success is True, done.error
    assert inbox.store.items[DANA].draft == DRAFTED
    assert "nothing was sent" in done.summary
    assert done.reversal

    undone = await provider.reverse(done.reversal)
    assert undone.success is True
    assert inbox.store.items[DANA].draft == "her own words"


async def test_a_message_that_takes_no_reply_is_refused_before_the_model_runs(
    inbox: Any, monkeypatch: Any
) -> None:
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.inbox_op_provider import InboxOpActionProvider

    inbox.store.add(
        _mail("native_note_1790000001.0", "Backup finished", "Backups", can_reply=False)
    )
    model = _Model(DRAFTED, _CHECKED)
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", model)
    result = await InboxOpActionProvider().execute(
        {"op": "reply_draft", "item_id": "native_note_1790000001.0"},
        ActionContext(event="triage_auto_execute"),
    )
    assert result.success is False
    assert "takes no reply" in result.error
    assert model.prompts == []
    assert inbox.store.items["native_note_1790000001.0"].draft == ""


async def test_a_draft_the_model_declines_leaves_her_draft_where_it_was(
    inbox: Any, monkeypatch: Any
) -> None:
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.inbox_op_provider import InboxOpActionProvider

    inbox.store.update(DANA, draft="her own words")
    monkeypatch.setattr("personalclaw.llm_helpers.one_shot_completion", _Model("SKIP"))
    result = await InboxOpActionProvider().execute(
        {"op": "reply_draft", "item_id": DANA}, ActionContext(event="triage_auto_execute")
    )
    assert result.success is False
    assert "no reply" in result.error
    assert inbox.store.items[DANA].draft == "her own words"


async def test_a_draft_that_needs_her_word_first_says_so_and_writes_nothing(
    inbox: Any, monkeypatch: Any
) -> None:
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.inbox_op_provider import InboxOpActionProvider

    monkeypatch.setattr(
        "personalclaw.llm_helpers.one_shot_completion",
        _Model("ASK: Which version of the contract should go?"),
    )
    result = await InboxOpActionProvider().execute(
        {"op": "reply_draft", "item_id": DANA}, ActionContext(event="triage_auto_execute")
    )
    assert result.success is False
    assert "Which version of the contract should go?" in result.error
    assert inbox.store.items[DANA].draft == ""
