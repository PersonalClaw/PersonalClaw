"""A Morning triage proposal you have not answered comes back in the next digest.

Measured on the tree before this was written: the next digest collected only what was newer than
the last completed digest, and its card replaced the old one, so a proposal nobody answered was
never offered again. A quiet morning made it worse: nothing new was collected, the run ended with an
empty digest, and that empty card became the current one, so the old proposals could not even be
answered where they were.

Each digest below is a real run in this test's own run store: the run is created and started at a
given moment, its one step is the real ``triage-digest`` provider (the real collect, pipeline, stage
and delivery, the model scripted), and its output and its ending are stored where the card and the
answer path read them. Answers go through the card's real route and the guarded door a channel's
message crosses.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import channel_delivery
from personalclaw import channel_inbound as ci
from personalclaw import channel_trust as ct
from personalclaw.channel_transports.base import ChannelMessage
from personalclaw.config.credentials import owner_id_credential
from personalclaw.config.loader import CRED_OWNER_ID
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus, set_item_status
from personalclaw.inbox_service import InboxService
from personalclaw.testing.channel_conformance import CapturingState

pytestmark = pytest.mark.anyio

TRIGGER = "system:triage:digest"
PROVIDER = "telegram"
OWNER = "4401"
DM = "4401"

DANA = "mail_1790000100.1"
VENUE = "mail_1790000100.2"
NEWS = "mail_1790000100.3"
LUNCH = "mail_1790000200.4"

#: What the model proposes for each message it is shown, by the message's own words, so a digest
#: proposes for exactly the messages its window holds, under whatever number each one has there.
_PROPOSES = {
    "Thanks for the notes": {
        "action_type": "dismiss",
        "tier": "high",
        "pattern_key": "dismiss:sender:dana@example.com",
    },
    "spring talk venue": {
        "action_type": "create_task",
        "tier": "low",
        "pattern_key": "create_task:sender:venue@example.com",
        "action_config": {"title": "Renew the spring talk venue booking"},
    },
    "rail travel": {
        "action_type": "archive",
        "tier": "trivial",
        "pattern_key": "archive:sender:rail@example.com",
    },
    "lunch on Thursday": {
        "action_type": "archive",
        "tier": "low",
        "pattern_key": "archive:sender:priya@example.com",
    },
}

_BUNDLED = (
    Path(__file__).resolve().parents[1]
    / "src/personalclaw/workflows/bundled/morning-triage/workflow.json"
)
_STEP = "root.children[0]"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _stamp(moment: datetime) -> str:
    """A run's stamp, in the form the run store writes every one of them."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mail(ident: str, message: str, sender: str, *, at: datetime) -> InboxItem:
    return InboxItem(
        id=ident,
        channel="noor@example.com",
        channel_name="Mail",
        thread_ts=None,
        message=message,
        sender_id=f"{sender.split()[0].lower()}@example.com",
        sender_name=sender,
        created_at=at.timestamp(),
        source="mail",
        can_reply=True,
        reply_target=f"<{ident}@example.com>",
    )


class _Telegram:
    """The chat channel's outbound half: the owner's DM, and every text sent there."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def open_dm(self, user_id: str) -> str:
        return DM if user_id == OWNER else ""

    async def deliver_text(self, channel: str, text: str, thread_ts: str = "", **_kw: Any) -> str:
        self.sent.append((channel, text))
        return f"m{len(self.sent)}"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: Any):
    """Her machine as the digest reaches it: her Inbox, the dashboard's notices, the digest's
    schedule on, her rule sending a notice to her DM too, Telegram connected with her as its owner,
    and the model answering for each message by its words."""
    import personalclaw.proactive.autoexec as autoexec
    import personalclaw.proactive.pipeline as pipeline
    from personalclaw import notification_rules as nr
    from personalclaw.config.transactions import mutate_config
    from personalclaw.dashboard import state as st
    from personalclaw.dashboard.desktop_registry import DesktopRegistry
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    store = InboxStore(path=tmp_path / "inbox_items.json")
    svc = InboxService(
        state=InboxState(path=tmp_path / "inbox_state.json"), store=store, user_name="Noor"
    )
    dash = object.__new__(st.DashboardState)
    dash._notification_log = []
    dash.desktop = DesktopRegistry()
    dash._background_tasks = set()
    dash._inbox_svc = svc
    dash._sessions = {}
    monkeypatch.setattr(st.DashboardState, "_broadcast", lambda self, n: None)
    monkeypatch.setattr(st.DashboardState, "_announce_logged", lambda self, n: None)
    monkeypatch.setattr(
        st.DashboardState, "broadcast_ws", lambda self, *a, **k: None, raising=False
    )
    monkeypatch.setattr(st, "_persist_notification", lambda note: None)
    monkeypatch.setattr(
        "personalclaw.action_providers.services.get_action_services",
        lambda: SimpleNamespace(state=dash),
    )
    monkeypatch.setattr(autoexec, "default_budget_check", lambda *a, **k: lambda: (False, ""))

    shown: list[str] = []

    async def completion(prompt: str, **_kw: Any) -> Any:
        """The proposal call: one proposal for each message the prompt numbers. A numbered line
        opens an item and its fenced words follow on the lines after it."""
        shown.append(prompt)
        proposals: list[dict] = []
        current = ""
        for line in prompt.splitlines():
            head = line.split(".", 1)[0].strip()
            if head.isdigit() and ". [" in line:
                current = head
            for words, proposal in _PROPOSES.items():
                if current and words in line and all(p["item_id"] != current for p in proposals):
                    proposals.append({"item_id": current, **proposal})
        return {"proposals": proposals}

    monkeypatch.setattr(pipeline, "_default_completion", completion)

    mutate_config(lambda doc: doc.setdefault("proactive", {}).update({"triage_enabled": True}))
    TriggerStore().upsert(
        Trigger(id=TRIGGER, name="Morning triage", kind="clock", created_by="system")
    )
    nr.save_rules(
        {"rules": {"system/info": {"mode": "immediate", "targets": ["dashboard", "channel_dm"]}}}
    )
    for key in (CRED_OWNER_ID, owner_id_credential(PROVIDER)):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(owner_id_credential(PROVIDER), OWNER)
    ct.allow_sender(PROVIDER, OWNER, name="Noor", via="owner_pairing")
    telegram = _Telegram()
    channel_delivery.register(telegram, provider=PROVIDER)
    ci.reset_admissions()
    yield SimpleNamespace(store=store, dash=dash, telegram=telegram, shown=shown)
    ci.reset_admissions()
    channel_delivery.register(None, provider=PROVIDER)


def _mails(home: Any, *, at: datetime) -> None:
    """The three messages waiting for her when the first digest runs."""
    for item in (
        _mail(DANA, "Thanks for the notes, nothing needed.", "Dana Reyes", at=at),
        _mail(
            VENUE, "Your booking for the spring talk venue lapses next week.", "Venue Desk", at=at
        ),
        _mail(NEWS, "This week in rail travel: our newsletter", "Rail News", at=at),
    ):
        home.store.add(item)
    home.store.save()


async def _digest(
    home: Any, run_id: str, *, began: datetime, window_hours: int = 24, before_end: Any = None
) -> dict:
    """One Morning triage run, as the engine runs one: created and started at *began*, its one step
    the real provider, its output and its ending stored where the card and the answer path read
    them. *before_end* runs after the step and before the run is marked complete. Returns the
    step's output."""
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.triage_digest_provider import TriageDigestActionProvider
    from personalclaw.workflows import store
    from personalclaw.workflows.models import InstanceState, NodeInstance, RunStatus, WorkflowRun

    run = WorkflowRun(
        id=run_id,
        workflow_name="morning-triage",
        status=RunStatus.RUNNING,
        created_at=_stamp(began),
        started_at=_stamp(began),
    )
    store.create(run)
    store.write_spec(run_id, json.loads(_BUNDLED.read_text(encoding="utf-8")))
    result = await TriageDigestActionProvider().execute(
        {"window_hours": window_hours, "max_proposals": 8, "filter_rules": []},
        ActionContext(
            event="workflow_node",
            payload={"node_id": "triage", "run_id": run_id, "instance_path": _STEP},
        ),
    )
    assert result.success, result.error
    # The notice's channel DM goes out on the loop, as it does from the gateway.
    await asyncio.gather(*list(home.dash._background_tasks))
    home.dash._background_tasks.clear()
    output = json.loads(result.stdout)
    ref = store.write_output(run_id, _STEP, output)
    store.write_state(
        run_id, {_STEP: NodeInstance(path=_STEP, state=InstanceState.DONE, output_ref=ref)}
    )
    if before_end is not None:
        before_end()
    run.status = RunStatus.COMPLETE
    run.completed_at = _stamp(began + timedelta(minutes=2))
    store.save(run)
    return output


def _owner_app() -> web.Application:
    import personalclaw.dashboard.handlers.proactive as routes

    @web.middleware
    async def as_owner(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[as_owner])
    app["state"] = SimpleNamespace(_sessions={})
    app.router.add_get("/api/proactive/digest", routes.api_proactive_digest)
    app.router.add_post("/api/proactive/digest/reply", routes.api_proactive_reply)
    return app


async def _card() -> dict:
    """The digest card, as her signed-in session reads it."""
    async with TestClient(TestServer(_owner_app())) as client:
        resp = await client.get("/api/proactive/digest")
        assert resp.status == 200, await resp.text()
        return await resp.json()


async def _tap(run_id: str, text: str) -> dict:
    """One tap on the card, as her signed-in session."""
    async with TestClient(TestServer(_owner_app())) as client:
        resp = await client.post(
            "/api/proactive/digest/reply",
            json={"run_id": run_id, "text": text},
            headers={"X-Session-Key": "dashboard:ui"},
        )
        assert resp.status == 200, await resp.text()
        return await resp.json()


def _ordinal_of(card: dict, words: str) -> str:
    (row,) = [row for row in card["pending"] if words in row["title"]]
    return str(row["ordinal"])


def _journal(run_id: str, kind: str) -> list[dict]:
    from personalclaw.workflows import journal

    return [row for row in journal.ledger(run_id) if row.get("kind") == kind]


def _pending(card: dict) -> list[tuple[str, bool]]:
    return sorted((row["title"], bool(row.get("carried_over"))) for row in card["pending"])


# ── what waits on her comes back ─────────────────────────────────────────────────────────────


async def test_a_proposal_she_did_not_answer_is_in_the_next_digest(home: Any) -> None:
    """Monday's digest proposes three things and she answers none of them. Wednesday's has nothing
    new: it carries all three, each marked as carried over with how long it has waited."""
    now = datetime.now(UTC)
    _mails(home, at=now - timedelta(days=3))
    await _digest(home, "run-mon", began=now - timedelta(days=2), window_hours=96)

    output = await _digest(home, "run-wed", began=now)
    card = await _card()

    assert card["run_id"] == "run-wed"
    assert _pending(card) == [
        ("Thanks for the notes, nothing needed.", True),
        ("This week in rail travel: our newsletter", True),
        ("Your booking for the spring talk venue lapses next week.", True),
    ]
    for row in card["pending"]:
        assert row["first_run_id"] == "run-mon"
        assert "Carried over" in row["carried_note"] and "2 days ago" in row["carried_note"]
        assert row["answered"] is False
    # The task it would file is still the one Monday showed.
    venue = [row for row in card["pending"] if "venue" in row["title"]][0]
    assert venue["action_config"] == {"title": "Renew the spring talk venue booking"}
    # Its text says so too, and says how long a proposal comes back for.
    body = output["digest_body"]
    assert "Needs you:" in body and "Carried over" in body and "2 days ago" in body
    assert "for up to 7 days" in body
    # Nothing new was collected, so no model was asked anything.
    assert output["llm_calls"] == 0 and output["collected"] == 0
    # It reached her as a digest does.
    assert [n["title"] for n in home.dash._notification_log] == ["Morning triage"] * 2
    # And the run's record says what it carried, from where.
    rows = _journal("run-wed", "proposal_carried")
    assert sorted(r["first_run_id"] for r in rows) == ["run-mon"] * 3


async def test_a_proposal_she_answered_is_not_carried(home: Any) -> None:
    """She answers two of Monday's three on the card. Wednesday's digest carries only the one she
    left."""
    now = datetime.now(UTC)
    _mails(home, at=now - timedelta(days=3))
    await _digest(home, "run-mon", began=now - timedelta(days=2), window_hours=96)
    monday = await _card()
    await _tap("run-mon", f"{_ordinal_of(monday, 'rail travel')} yes")
    await _tap("run-mon", f"{_ordinal_of(monday, 'Thanks for the notes')} no")

    await _digest(home, "run-wed", began=now)
    card = await _card()

    assert _pending(card) == [("Your booking for the spring talk venue lapses next week.", True)]


async def test_a_proposal_whose_item_was_dealt_with_elsewhere_drops_out(home: Any) -> None:
    """Between the two digests she dismissed Dana's note in her Inbox. Its proposal is not offered
    again and is not listed as dropped: there is nothing left to ask. The run's record says why it
    went."""
    now = datetime.now(UTC)
    _mails(home, at=now - timedelta(days=3))
    await _digest(home, "run-mon", began=now - timedelta(days=2), window_hours=96)
    set_item_status(None, home.store, [home.store.items[DANA]], ItemStatus.DISMISSED.value)

    await _digest(home, "run-wed", began=now)
    card = await _card()

    assert _pending(card) == [
        ("This week in rail travel: our newsletter", True),
        ("Your booking for the spring talk venue lapses next week.", True),
    ]
    assert card["no_longer_offered"] == []
    (dropped,) = _journal("run-wed", "proposal_dropped")
    assert (dropped["reason"], dropped["item_source_id"]) == ("handled", DANA)


async def test_a_fresh_proposal_is_numbered_and_shown_as_it_always_was(home: Any) -> None:
    """The control: a message that arrived since Monday gets its proposal on Wednesday as it would
    with nothing carried, first and under number 1; what Monday left waiting follows it."""
    now = datetime.now(UTC)
    _mails(home, at=now - timedelta(days=3))
    await _digest(home, "run-mon", began=now - timedelta(days=2), window_hours=96)
    monday = await _card()
    for words in ("rail travel", "Thanks for the notes"):
        await _tap("run-mon", f"{_ordinal_of(monday, words)} no")
    home.store.add(
        _mail(
            LUNCH,
            "Are we still on for lunch on Thursday?",
            "Priya Nair",
            at=now - timedelta(hours=3),
        )
    )

    output = await _digest(home, "run-wed", began=now)
    card = await _card()

    fresh, carried = card["pending"]
    assert (fresh["ordinal"], fresh["title"]) == ("1", "Are we still on for lunch on Thursday?")
    assert (fresh["action_type"], fresh["carried_over"], fresh["carried_note"]) == (
        "archive",
        False,
        "",
    )
    assert output["collected"] == 1 and output["llm_calls"] == 1
    assert (carried["ordinal"], carried["carried_over"]) == ("2", True)
    assert "venue" in carried["title"]
    # The model was shown only what is new: the carried proposal is not proposed on again.
    (prompt,) = home.shown[1:]
    assert "lunch on Thursday" in prompt and "spring talk venue" not in prompt


# ── her answer to a carried proposal ─────────────────────────────────────────────────────────


async def test_her_yes_to_a_carried_proposal_on_the_new_card_does_it(home: Any) -> None:
    """The task Monday proposed is filed when she says yes to it on Wednesday's card, under the
    number Wednesday gives it, and her answer is recorded on Wednesday's run."""
    from personalclaw.tasks.registry import list_all_tasks

    now = datetime.now(UTC)
    _mails(home, at=now - timedelta(days=3))
    await _digest(home, "run-mon", began=now - timedelta(days=2), window_hours=96)
    await _digest(home, "run-wed", began=now)
    card = await _card()
    venue = _ordinal_of(card, "spring talk venue")

    body = await _tap("run-wed", f"{venue} yes")

    (result,) = body["results"]
    assert result["executed"] is True, result
    tasks, _total = await list_all_tasks(provider_filter="native", limit=50)
    assert [task.title for task in tasks] == ["Renew the spring talk venue booking"]
    (reply,) = _journal("run-wed", "triage_reply")
    assert (reply["item_ordinal"], reply["outcome"]) == (venue, "executed")
    # Answered, so the next digest does not carry it again.
    await _digest(home, "run-fri", began=now + timedelta(minutes=5))
    assert "spring talk venue" not in json.dumps((await _card())["pending"])


async def test_her_reply_on_the_channel_answers_a_carried_proposal(home: Any) -> None:
    """Wednesday's digest reached her DM with Monday's proposals in it. Her "N yes" there answers
    the carried proposal Wednesday numbered N."""
    now = datetime.now(UTC)
    _mails(home, at=now - timedelta(days=3))
    await _digest(home, "run-mon", began=now - timedelta(days=2), window_hours=96)
    await _digest(home, "run-wed", began=now)
    card = await _card()
    news = _ordinal_of(card, "rail travel")
    _, wednesday = home.telegram.sent[-1]
    assert "Carried over" in wednesday and f"{news}. [trivial] archive" in wednesday
    home.telegram.sent.clear()

    services = SimpleNamespace(dashboard_state=CapturingState(), ctx_builder=None)
    turns: list[str] = []

    async def turn(state: Any, session: Any, text: str) -> None:
        turns.append(text)

    await ci.deliver_inbound(
        services,
        PROVIDER,
        ChannelMessage(
            channel_id=DM, text=f"{news} yes", sender=OWNER, thread_id=DM, message_id="9"
        ),
        is_dm=True,
        turn_runner=turn,
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert turns == [], "her answer reached a chat"
    assert home.store.items[NEWS].status == ItemStatus.HANDLED.value
    (reply,) = _journal("run-wed", "triage_reply")
    assert (reply["item_ordinal"], reply["answered_by"]) == (news, f"channel:{PROVIDER}")
    ((_, said),) = home.telegram.sent
    assert said.startswith(f"{news}. archive") and "done" in said


# ── for how long ─────────────────────────────────────────────────────────────────────────────


async def test_a_proposal_that_waited_a_week_is_dropped_with_a_note(home: Any) -> None:
    """A proposal comes back for up to 7 days. The digest that would carry one longer drops it and
    names it, on its card and in its text, so it does not simply vanish."""
    now = datetime.now(UTC)
    _mails(home, at=now - timedelta(days=9))
    await _digest(home, "run-old", began=now - timedelta(days=8), window_hours=24 * 10)

    output = await _digest(home, "run-now", began=now)
    card = await _card()

    assert card["pending"] == []
    gone = sorted(row["title"] for row in card["no_longer_offered"])
    assert gone == [
        "Thanks for the notes, nothing needed.",
        "This week in rail travel: our newsletter",
        "Your booking for the spring talk venue lapses next week.",
    ]
    for row in card["no_longer_offered"]:
        assert "Not offered again" in row["note"] and "8 days" in row["note"]
    assert "for up to 7 days" in card["carry_rule"]
    body = output["digest_body"]
    assert "No longer offered" in body and "Not offered again" in body
    assert {r["reason"] for r in _journal("run-now", "proposal_dropped")} == {"waited_too_long"}


def test_the_week_is_counted_in_whole_days_from_the_digest_that_first_proposed_it() -> None:
    """Six days on, it is offered again; seven days on, it is dropped. A digest that fires a few
    seconds earlier than the one a week before it still counts seven days."""
    from personalclaw.proactive.carry import DROPPED_EXPIRED, Waiting, place
    from personalclaw.proactive.gate import GateResult
    from personalclaw.proactive.manifest import SOURCE_INBOX, CollectedItem, Manifest
    from personalclaw.proactive.proposals import Proposal

    def waiting(ident: str, first: str) -> Waiting:
        return Waiting(
            proposal=Proposal(item_id="1", action_type="archive", tier="low"),
            item=CollectedItem(source=SOURCE_INBOX, source_id=ident, title=ident, ordinal="1"),
            first_proposed_at=first,
            first_run_id="run-before",
        )

    now = datetime(2026, 10, 9, 8, 0, 1, tzinfo=UTC)
    six_days = waiting("six-days", "2026-10-03T08:00:00Z")
    a_week = waiting("a-week", "2026-10-02T08:00:05Z")

    placed = place([six_days, a_week], window=Manifest(), gate=GateResult(), proposals=(), now=now)

    assert [(c.item.source_id, c.proposal.item_id) for c in placed.carried] == [("six-days", "1")]
    assert [(d.waiting.item.source_id, d.reason) for d in placed.dropped] == [
        ("a-week", DROPPED_EXPIRED)
    ]


def test_one_item_never_has_two_proposals() -> None:
    """A carried proposal whose item the new window collected again (something new happened to it)
    gives way to the fresh look: a fresh proposal replaces it, an item her rules now filter takes
    it with it, and an item kept with no fresh proposal keeps the carried one under its new
    number."""
    from personalclaw.proactive.carry import DROPPED_FILTERED, DROPPED_SUPERSEDED, Waiting, place
    from personalclaw.proactive.gate import GateDisposition, GateOutcome, apply_gate
    from personalclaw.proactive.manifest import SOURCE_INBOX, CollectedItem, build_manifest
    from personalclaw.proactive.proposals import Proposal

    def mail(ident: str) -> CollectedItem:
        return CollectedItem(source=SOURCE_INBOX, source_id=ident, title=ident, ts=ident)

    window = build_manifest([mail("again-proposed"), mail("again-filtered"), mail("again-kept")])
    number = {item.source_id: item.ordinal for item in window.items}
    gate = apply_gate(
        window,
        {
            number["again-filtered"]: GateOutcome(
                disposition=GateDisposition.DROP, rationale="your rule"
            )
        },
    )
    fresh = [Proposal(item_id=number["again-proposed"], action_type="archive", tier="low")]

    def waiting(ident: str) -> Waiting:
        return Waiting(
            proposal=Proposal(item_id="7", action_type="create_task", tier="low"),
            item=replace_ordinal(mail(ident), "7"),
            first_proposed_at="2026-10-08T08:00:00Z",
            first_run_id="run-before",
        )

    placed = place(
        [waiting("again-proposed"), waiting("again-filtered"), waiting("again-kept")],
        window=window,
        gate=gate,
        proposals=fresh,
        now=datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
    )

    assert [(c.item.source_id, c.proposal.item_id) for c in placed.carried] == [
        ("again-kept", number["again-kept"])
    ]
    assert placed.items == (), "an item the window numbered is not numbered twice"
    assert sorted((d.waiting.item.source_id, d.reason) for d in placed.dropped) == [
        ("again-filtered", DROPPED_FILTERED),
        ("again-proposed", DROPPED_SUPERSEDED),
    ]


def replace_ordinal(item: Any, ordinal: str) -> Any:
    from dataclasses import replace

    return replace(item, ordinal=ordinal)


def test_a_proposal_whose_item_cannot_be_looked_up_stays_as_it_was_recorded() -> None:
    """Not knowing is not "gone": a proposal whose lane cannot be read here stays, as recorded.
    One whose item is gone is dealt with, and one whose item is there takes its words as now."""
    from personalclaw.proactive.carry import Waiting, recheck
    from personalclaw.proactive.collect import LaneUnreadable
    from personalclaw.proactive.manifest import SOURCE_INBOX, CollectedItem
    from personalclaw.proactive.proposals import Proposal

    def waiting(ident: str) -> Waiting:
        return Waiting(
            proposal=Proposal(item_id="1", action_type="archive", tier="low"),
            item=CollectedItem(source=SOURCE_INBOX, source_id=ident, title="as recorded"),
            first_proposed_at="2026-10-08T08:00:00Z",
            first_run_id="run-before",
        )

    def look(source: str, source_id: str) -> CollectedItem | None:
        if source_id == "gone":
            return None
        if source_id == "unknown":
            raise LaneUnreadable("inbox lane is not readable here")
        return CollectedItem(source=source, source_id=source_id, title="as it is now")

    still, handled = recheck([waiting("gone"), waiting("unknown"), waiting("here")], look=look)

    assert [(w.item.source_id, w.item.title) for w in still] == [
        ("unknown", "as recorded"),
        ("here", "as it is now"),
    ]
    assert [w.item.source_id for w in handled] == ["gone"]


# ── where the window starts ──────────────────────────────────────────────────────────────────


async def test_a_message_that_arrived_while_the_last_digest_ran_is_in_the_next_one(
    home: Any,
) -> None:
    """Monday's digest collected, then spent its two minutes asking the model, and a message
    arrived in between. It is too new for Monday's digest, and Wednesday's window starts where
    Monday's began, so it is in Wednesday's."""
    now = datetime.now(UTC)
    _mails(home, at=now - timedelta(days=3))
    began = now - timedelta(days=2)

    def arrives() -> None:
        home.store.add(
            _mail(
                LUNCH,
                "Are we still on for lunch on Thursday?",
                "Priya Nair",
                at=began + timedelta(seconds=40),
            )
        )

    await _digest(home, "run-mon", began=began, window_hours=96, before_end=arrives)
    output = await _digest(home, "run-wed", began=now)

    assert output["collected"] == 1
    assert [row["source_id"] for row in output["items"]][:1] == [LUNCH]


def test_a_run_that_ended_after_the_last_digest_is_in_the_next_one(monkeypatch: Any) -> None:
    """A sweep that started before Monday's digest and failed after it is in Wednesday's: a run is
    in the window it ENDED in, whenever it started. A Morning triage run is in none: it is the
    digest."""
    import personalclaw.ledger as ledger
    from personalclaw.proactive import collect
    from personalclaw.workflows import store as run_store

    def run(ident: str, workflow: str, status: str, created: str, ended: str) -> Any:
        return SimpleNamespace(
            id=ident,
            workflow_name=workflow,
            status=status,
            created_at=created,
            completed_at=ended,
            error_message="",
        )

    runs = [
        run("r-sweep", "nightly-sweep", "failed", "2026-09-28T07:00:00Z", "2026-09-28T09:00:00Z"),
        run(
            "r-digest", "morning-triage", "complete", "2026-09-28T08:00:00Z", "2026-09-28T08:02:00Z"
        ),
        run("r-before", "nightly-sweep", "failed", "2026-09-27T07:00:00Z", "2026-09-27T07:30:00Z"),
    ]
    monkeypatch.setattr(run_store, "list_runs", lambda limit: (runs, len(runs)))
    monkeypatch.setattr(ledger, "read_events", lambda store, run_id, kinds: [])

    got = {
        item.source_id: item.materiality
        for item in collect.collect_runs(since="2026-09-28T08:00:00Z")
    }

    assert got == {"r-sweep": "error"}


def test_a_digest_whose_proposal_step_failed_still_lists_what_waits_and_says_it_failed() -> None:
    """The proposal step gave nothing usable this morning. What an earlier digest left waiting is
    still listed, and the digest still says this window got no proposals."""
    from personalclaw.proactive.carry import Waiting, place
    from personalclaw.proactive.gate import GateResult
    from personalclaw.proactive.manifest import SOURCE_INBOX, CollectedItem, Manifest
    from personalclaw.proactive.proposals import Proposal
    from personalclaw.proactive.rank import render_digest

    now = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    carry = place(
        [
            Waiting(
                proposal=Proposal(item_id="2", action_type="archive", tier="low"),
                item=CollectedItem(
                    source=SOURCE_INBOX, source_id="m1", title="Rail newsletter", ordinal="2"
                ),
                first_proposed_at="2026-10-08T08:00:00+00:00",
                first_run_id="run-before",
            )
        ],
        window=Manifest(),
        gate=GateResult(),
        proposals=(),
        now=now,
    )

    digest = render_digest(
        Manifest(), kept=(), proposals=(), dropped_count=0, degraded=True, carry=carry, now=now
    )

    assert "1. [low] archive — Rail newsletter" in digest.body
    assert "Carried over: proposed 1 day ago" in digest.body
    assert "(no new proposals this run — the proposal stage was refused)" in digest.body
    assert digest.proposed == 1, "a reply to this digest answers the carried proposal"
