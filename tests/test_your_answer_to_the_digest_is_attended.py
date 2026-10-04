"""Your answer to the Morning triage digest is yours: attended, and taken wherever the digest says.

Measured on the tree before this was written:

* under an operator ceiling of ``ask`` her Yes on the card was refused for lacking the auto-execute
  grant, with the sentence that "the operator ceiling says a person decides every action" — said
  to the person deciding;
* in incident mode her Yes was refused as unattended work, though incident mode leaves every other
  attended action running (a chat's tool calls, a press in the Inbox);
* the digest told her to "Reply `1 yes`" in its text, which the notification in PersonalClaw shows
  with nothing that takes a reply, and on the chat channel the digest reached, ``3 yes`` went to
  the chat as an ordinary message.

Each answer below goes through a real door — the card's route, or the guarded inbound door a
channel's message crosses — and the real answer path, the real stage, the real Inbox provider and
the run's real journal, in this test's own home. The run listing and its stored output are the
only stand-ins for the workflow engine, and the model is scripted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
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
from personalclaw.guardrails import ceiling as C
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus
from personalclaw.inbox_service import InboxService
from personalclaw.proactive.collect import collect_inbox
from personalclaw.proactive.manifest import SOURCE_INBOX, CollectedItem, build_manifest
from personalclaw.proactive.pipeline import run_triage
from personalclaw.proactive.proposals import Proposal
from personalclaw.sel import sel
from personalclaw.testing.channel_conformance import CapturingState

pytestmark = pytest.mark.anyio

RUN = "run-morning-digest"
TRIGGER = "system:triage:digest"
PROVIDER = "telegram"
#: The owner's Telegram id, and her DM with the bot (a Telegram private chat's id is the user's).
OWNER = "4401"
DM = "4401"
#: Someone she paired, who is allowed to talk to the bot and is not her.
FRIEND = "5502"

DANA = "mail_1790000100.1"
VENUE = "mail_1790000100.2"
NEWS = "mail_1790000100.3"

_PROPOSALS = [
    {
        "item_id": "1",
        "action_type": "dismiss",
        "tier": "high",
        "pattern_key": "dismiss:sender:dana@example.com",
        "reasoning": "Dana's note needs nothing.",
    },
    {
        "item_id": "2",
        "action_type": "create_task",
        "tier": "low",
        "pattern_key": "create_task:sender:venue@example.com",
        "action_config": {"title": "Renew the spring talk venue booking"},
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
def anyio_backend() -> str:
    return "asyncio"


def _mail(ident: str, message: str, sender: str) -> InboxItem:
    return InboxItem(
        id=ident,
        channel="noor@example.com",
        channel_name="Mail",
        thread_ts=None,
        message=message,
        sender_id=f"{sender.split()[0].lower()}@example.com",
        sender_name=sender,
        created_at=1790000100.0,
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
def ceiling(tmp_path: Path, monkeypatch: Any):
    """Install an operator ceiling for this test: ``ceiling("ask")``."""

    def install(value: str) -> None:
        path = tmp_path / "operator" / "ceiling.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 1, "scopes": {"approval": {"value": value}}}))
        monkeypatch.setenv(C.CEILING_PATH_ENV, str(path))
        C.reset_ceiling()

    C.reset_ceiling()
    yield install
    C.reset_ceiling()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: Any):
    """Her machine, as the digest reaches it: the live Inbox, the dashboard's notices, the digest's
    schedule switched on, her rule sending a notice to the first chat channel that reaches her,
    and Telegram connected with her as its owner."""
    import personalclaw.proactive.autoexec as autoexec
    from personalclaw import notification_rules as nr
    from personalclaw.config.transactions import mutate_config
    from personalclaw.dashboard import state as st
    from personalclaw.dashboard.desktop_registry import DesktopRegistry
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    store = InboxStore(path=tmp_path / "inbox_items.json")
    for item in (
        _mail(DANA, "Thanks for the notes, nothing needed.", "Dana Reyes"),
        _mail(VENUE, "Your booking for the spring talk venue lapses next week.", "Venue Desk"),
        _mail(NEWS, "This week in rail travel: our newsletter", "Rail News"),
    ):
        store.add(item)
    store.save()
    svc = InboxService(
        state=InboxState(path=tmp_path / "inbox_state.json"), store=store, user_name="Noor"
    )

    # The dashboard, with what `notify()` and its channel DM target touch: the bell's own log and
    # its toast push are kept out of the way, the channel DM is the real one.
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
    ct.allow_sender(PROVIDER, FRIEND, name="Sam")
    telegram = _Telegram()
    channel_delivery.register(telegram, provider=PROVIDER)
    yield SimpleNamespace(store=store, dash=dash, telegram=telegram)
    channel_delivery.register(None, provider=PROVIDER)


async def _a_digest(home: Any, monkeypatch: Any, *, run_id: str = RUN) -> dict:
    """One real digest over her Inbox, delivered as a triage run delivers it, the model answering
    with the three proposals. It becomes the current digest: the run listing names it and its
    output is what the run stored. Returns that output."""
    import asyncio

    from personalclaw.workflows import service, store

    async def completion(prompt: str, **_kw: Any) -> Any:
        return {"proposals": _PROPOSALS}

    result = await run_triage(
        collect_inbox(home.store),
        gate_enabled=False,
        window_start="2026-09-20T08:00:00+00:00",
        run_id=run_id,
        trigger_id=TRIGGER,
        completion=completion,
    )
    # The notice's channel DM goes out on the loop, as it does from the gateway.
    await asyncio.gather(*list(home.dash._background_tasks))
    output = {**result.summary(), "window_start": "2026-09-20T08:00:00+00:00"}
    run = SimpleNamespace(to_dict=lambda: {"run_id": run_id, "status": "complete"})
    monkeypatch.setattr(store, "list_runs", lambda **_kw: ([run], 1))
    monkeypatch.setattr(
        service, "output", lambda rid, node: {"ok": rid == run_id, "output": output}
    )
    return output


async def _tap(text: str) -> dict:
    """POST the card's reply as her signed-in session."""
    import personalclaw.dashboard.handlers.proactive as routes

    @web.middleware
    async def as_owner(request: web.Request, handler: Any) -> web.StreamResponse:
        request["user"] = "owner"
        return await handler(request)

    app = web.Application(middlewares=[as_owner])
    app["state"] = SimpleNamespace(_sessions={})
    app.router.add_post("/api/proactive/digest/reply", routes.api_proactive_reply)
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/api/proactive/digest/reply",
            json={"run_id": RUN, "text": text},
            headers={"X-Session-Key": "dashboard:ui"},
        )
        assert resp.status == 200, await resp.text()
        return await resp.json()


def _replies(run_id: str = RUN) -> list[dict]:
    from personalclaw.workflows import journal

    return [row for row in journal.ledger(run_id) if row.get("kind") == "triage_reply"]


# ── her Yes on the card is attended ──────────────────────────────────────────────────────────


async def test_her_yes_runs_under_an_ask_ceiling(home: Any, ceiling: Any, monkeypatch: Any) -> None:
    """The ceiling says a person decides every action. She is the person, and she decided."""
    await _a_digest(home, monkeypatch)
    ceiling("ask")

    body = await _tap("3 yes")

    (result,) = body["results"]
    assert result["executed"] is True, result
    assert result["not_done"] == ""
    assert home.store.items[NEWS].status == ItemStatus.HANDLED.value
    assert "a person decides" not in json.dumps(body)
    # Her answer asked for no grant, so none was refused.
    refused = [
        r
        for r in sel().recent(500)
        if r.get("operation") == "approval.grant_refused"
        and "grant=auto_execute," in str(r.get("resources", ""))
    ]
    assert refused == []


async def test_her_yes_in_incident_mode_runs_as_attended_work(home: Any, monkeypatch: Any) -> None:
    """Incident mode suspends what nobody answered, and leaves a chat's tool calls and a press in
    the Inbox running. Her answer is the same kind of thing."""
    from personalclaw.guardrails import incident

    await _a_digest(home, monkeypatch)
    incident.activate("a drill")

    body = await _tap("3 yes")

    (result,) = body["results"]
    assert result["executed"] is True, result
    assert home.store.items[NEWS].status == ItemStatus.HANDLED.value
    (row,) = _replies()
    assert row["outcome"] == "executed"


# ── the positive controls: the digest acting on its own is still held ────────────────────────


def _one_trivial_archive() -> tuple[list[Proposal], Any]:
    manifest = build_manifest(
        [CollectedItem(source=SOURCE_INBOX, source_id=NEWS, title="newsletter", ts="1")]
    )
    archive = Proposal(item_id=manifest.items[0].ordinal, action_type="archive", tier="trivial")
    return [archive], manifest


async def _on_its_own(proposals: list[Proposal], manifest: Any) -> tuple[list, Any]:
    from personalclaw.proactive.autoexec import auto_execute

    dispatched: list[str] = []

    async def dispatch(provider: str, config: dict, ctx: Any) -> Any:
        dispatched.append(config["op"])
        return SimpleNamespace(success=True, reversal="", error="")

    result = await auto_execute(
        proposals,
        manifest=manifest,
        now=datetime.now(UTC),
        enabled=True,
        cap=5,
        dispatch=dispatch,
        budget_check=lambda: (False, ""),
    )
    return dispatched, result


async def test_the_digest_on_its_own_runs_nothing_under_an_ask_ceiling(ceiling: Any) -> None:
    dispatched, _ = await _on_its_own(*_one_trivial_archive())
    assert dispatched == ["archive"], "the control: with no ceiling, a trivial archive runs"

    ceiling("ask")
    dispatched, result = await _on_its_own(*_one_trivial_archive())
    assert dispatched == []
    assert [d.reason for d in result.deferred] == ["refused_by_ceiling"]


async def test_the_digest_on_its_own_runs_nothing_in_incident_mode() -> None:
    from personalclaw.guardrails import incident

    incident.activate("a drill")
    dispatched, result = await _on_its_own(*_one_trivial_archive())
    assert dispatched == []
    assert [d.reason for d in result.deferred] == ["incident_active"]


# ── a reply on the chat channel the digest reached ───────────────────────────────────────────


class _Services:
    """The gateway's services handle as a channel's transport holds it: the real door."""

    def __init__(self) -> None:
        self.dashboard_state = CapturingState()
        self.ctx_builder = None
        self.turns: list[str] = []

    async def deliver_channel_inbound(self, provider: str, msg: Any, *, is_dm: bool = True) -> Any:
        async def turn(state: Any, session: Any, text: str) -> None:
            self.turns.append(text)

        verdict = await ci.deliver_inbound(self, provider, msg, is_dm=is_dm, turn_runner=turn)
        import asyncio

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return verdict


def _dm(text: str, *, sender: str = OWNER, mid: str = "41", channel: str = DM) -> ChannelMessage:
    return ChannelMessage(
        channel_id=channel, text=text, sender=sender, thread_id=channel, message_id=mid
    )


async def test_the_digest_on_a_channel_names_its_card_and_says_how_to_answer_there(
    home: Any, monkeypatch: Any
) -> None:
    """Every instruction the digest gives is true where it is shown: PersonalClaw's notice names
    the card, and the DM it reached adds the reply that answers it there."""
    from personalclaw.proactive.autoexec import SKIP_FAILED, not_done_note

    await _a_digest(home, monkeypatch)
    ((where, text),) = home.telegram.sent
    assert where == DM
    assert "Needs you:" in text
    assert "`3 yes`" in text and "`yes all`" in text
    assert "`help` for this line" not in text, "a bare help on a channel is the chat's"
    (note,) = home.dash._notification_log
    assert "`3 yes`" not in note["body"], "nothing in PersonalClaw's notice takes a typed reply"

    # A proposal the digest tried on its own and could not do: its text names the card.
    sentence = not_done_note(SKIP_FAILED, "the store was locked")
    assert "Yes on the Morning triage card in your Inbox tries it again" in sentence
    assert "Reply" not in sentence


async def test_her_3_yes_on_the_channel_answers_the_third_proposal(
    home: Any, monkeypatch: Any
) -> None:
    await _a_digest(home, monkeypatch)
    home.telegram.sent.clear()
    services = _Services()

    verdict = await services.deliver_channel_inbound(PROVIDER, _dm("3 yes"), is_dm=True)

    # Answered, through the card's own path: the newsletter is archived, the run records her answer
    # as given on Telegram, and no chat turn ran for it.
    assert home.store.items[NEWS].status == ItemStatus.HANDLED.value
    assert services.turns == []
    assert verdict.allowed is False
    (row,) = _replies()
    assert (row["item_ordinal"], row["verb"], row["outcome"]) == ("3", "yes", "executed")
    assert row["answered_by"] == f"channel:{PROVIDER}"
    audit = [r for r in sel().recent(500) if r.get("operation") == "triage_reply"]
    assert [(r["caller_identity"], r["source"]) for r in audit] == [
        (f"channel:{PROVIDER}", "channel")
    ]
    # What it did is said back in her DM.
    ((where, said),) = home.telegram.sent
    assert where == DM and said.startswith("3. archive") and "done" in said
    # The other proposals still wait.
    assert home.store.items[DANA].status == ItemStatus.PENDING.value


async def test_a_second_copy_of_her_reply_answers_nothing_again(
    home: Any, monkeypatch: Any
) -> None:
    await _a_digest(home, monkeypatch)
    services = _Services()
    await services.deliver_channel_inbound(PROVIDER, _dm("3 yes"), is_dm=True)
    home.telegram.sent.clear()

    await services.deliver_channel_inbound(PROVIDER, _dm("3 yes"), is_dm=True)

    assert len(_replies()) == 1
    assert home.telegram.sent == []
    assert services.turns == []


async def test_3_yes_from_someone_who_is_not_the_owner_answers_nothing(
    home: Any, monkeypatch: Any
) -> None:
    """Sam is paired, so his message reaches a chat as any of his does. It answers no proposal."""
    await _a_digest(home, monkeypatch)
    home.telegram.sent.clear()
    services = _Services()

    verdict = await services.deliver_channel_inbound(
        PROVIDER, _dm("3 yes", sender=FRIEND, mid="42", channel="5502"), is_dm=True
    )

    assert verdict.allowed is True
    assert services.turns == ["3 yes"]
    assert home.store.items[NEWS].status == ItemStatus.PENDING.value
    assert _replies() == []
    assert home.telegram.sent == []


async def test_her_3_yes_in_a_group_answers_nothing(home: Any, monkeypatch: Any) -> None:
    await _a_digest(home, monkeypatch)
    ct.track(PROVIDER, "-100777")
    services = _Services()

    await services.deliver_channel_inbound(
        PROVIDER, _dm("3 yes", mid="43", channel="-100777"), is_dm=False
    )

    assert home.store.items[NEWS].status == ItemStatus.PENDING.value
    assert _replies() == []


async def test_an_ordinary_message_from_her_still_reaches_the_chat(
    home: Any, monkeypatch: Any
) -> None:
    await _a_digest(home, monkeypatch)
    services = _Services()

    verdict = await services.deliver_channel_inbound(
        PROVIDER, _dm("3 yes please, and remind me about the venue", mid="44"), is_dm=True
    )

    assert verdict.allowed is True
    assert services.turns == ["3 yes please, and remind me about the venue"]
    assert _replies() == []


async def test_her_reply_to_a_digest_that_was_replaced_acts_on_nothing(
    home: Any, monkeypatch: Any
) -> None:
    """Her DM got this morning's digest. A newer digest went elsewhere: its third item is not the
    one she is answering, so nothing runs and she is told why."""
    from personalclaw.workflows import service, store

    await _a_digest(home, monkeypatch)
    newer = SimpleNamespace(to_dict=lambda: {"run_id": "run-later", "status": "complete"})
    monkeypatch.setattr(store, "list_runs", lambda **_kw: ([newer], 1))
    monkeypatch.setattr(
        service, "output", lambda rid, node: {"ok": True, "output": {"collected": 3}}
    )
    home.telegram.sent.clear()
    services = _Services()

    await services.deliver_channel_inbound(PROVIDER, _dm("3 yes", mid="45"), is_dm=True)

    assert home.store.items[NEWS].status == ItemStatus.PENDING.value
    assert services.turns == []
    ((_, said),) = home.telegram.sent
    assert "replaced by a newer one" in said and "nothing was done" in said


async def test_a_channel_that_runs_its_own_turns_offers_her_reply_first(
    home: Any, monkeypatch: Any
) -> None:
    """Slack runs a DM's conversation itself. It hands her message to core before its own turn,
    and core answers it exactly as the door does."""
    from personalclaw.gateway import GatewayOrchestrator

    await _a_digest(home, monkeypatch)
    services = _Services()
    services.answer_channel_reply = (  # type: ignore[attr-defined]
        lambda provider, msg, is_dm=True: GatewayOrchestrator.answer_channel_reply(
            services, provider, msg, is_dm=is_dm
        )
    )

    taken = await services.answer_channel_reply(PROVIDER, _dm("no 3", mid="46"))
    not_taken = await services.answer_channel_reply(PROVIDER, _dm("what's on today?", mid="47"))

    assert (taken, not_taken) == (True, False)
    (row,) = _replies()
    assert (row["verb"], row["outcome"]) == ("no", "declined")
    assert home.store.items[NEWS].status == ItemStatus.PENDING.value
