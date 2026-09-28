"""A run's history row says what the action did, not the JSON it printed.

#3739 gave `ActionResult` a `summary` — the sentence a run's history row shows — and browse wrote
one. Every other provider whose success `stdout` is JSON still put that JSON on the row: a
`run-workflow` trigger's history read `{"run_id": "3f2a91c0", "workflow": "triage", "started":
true}`, a `net-fetch` one read the whole fenced page as JSON, and an `inbox-op` one read
`{"op": "archive", "item_id": "C1_100.5", "changed": true}`.

What these pin, against the real providers and the real fire recorder:

* each success shape of the three writes a sentence a person reads — what started, queued or was
  skipped and why; how much was fetched from where; what was done to which message;
* the JSON stays on `stdout`, byte for byte: it is what a workflow step binds and the row's trace;
* the sentences quote no third party's text: a fetched page is named by its `host[:port]` alone,
  and a message by who sent it and where, never by what it says.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from personalclaw.action_providers.base import ActionContext, ActionResult
from personalclaw.action_providers.inbox_op_provider import InboxOpActionProvider
from personalclaw.action_providers.net_fetch_provider import NetFetchActionProvider
from personalclaw.action_providers.run_workflow_provider import RunWorkflowActionProvider
from personalclaw.inbox import InboxItem, InboxState, InboxStore, ItemStatus
from personalclaw.workflows import defs as defs_mod
from personalclaw.workflows import store
from personalclaw.workflows.models import RunStatus, WorkflowRun
from personalclaw.workflows.watchdog import WorkflowWatchdog

NAME = "triage-inbox"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.workflows.store.config_dir", lambda: home)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


# ── run-workflow ─────────────────────────────────────────────────────────────


class _Defs(defs_mod.WorkflowDefProvider):
    def __init__(self, spec: dict[str, Any]) -> None:
        self._spec = spec

    @property
    def name(self) -> str:
        return "summary-stub"

    async def list_defs(self, *, limit: int = 200, offset: int = 0):
        return [self._spec], 1

    async def get_def(self, name: str):
        return self._spec if name == self._spec["name"] else None


@pytest.fixture
def workflow(monkeypatch):
    """A registered one-step def, with a real watchdog as the supervisor that launches it."""
    watchdog = WorkflowWatchdog()

    def install(overlap: str = "skip") -> dict[str, Any]:
        spec = {
            "name": NAME,
            "on_overlap": overlap,
            "root": {"kind": "transform", "id": "only", "config": {"expr": "done"}},
        }
        defs_mod.register_provider(_Defs(spec))
        monkeypatch.setattr(
            "personalclaw.action_providers.services.get_action_services",
            lambda: SimpleNamespace(workflows=watchdog),
        )
        return spec

    try:
        yield install
    finally:
        defs_mod.unregister_provider("summary-stub")


def _start(**config: Any) -> ActionResult:
    ctx = cast(Any, SimpleNamespace(trigger_id="trigger-summary"))
    return asyncio.run(RunWorkflowActionProvider().execute({"workflow": NAME, **config}, ctx))


def _busy(spec: dict[str, Any]) -> WorkflowRun:
    prior = store.create(WorkflowRun(id="", workflow_name=NAME, status=RunStatus.RUNNING))
    store.write_spec(prior.id, spec)
    return prior


def test_run_workflow_says_it_started_and_which_run(workflow) -> None:
    """🔴 Red on main: the row was `{"run_id": …, "workflow": …, "started": true}`."""
    workflow()
    result = _start()
    assert result.outcome == "launched"
    body = json.loads(result.stdout)
    assert body == {"run_id": body["run_id"], "workflow": NAME, "started": True}
    assert result.summary == f"Started “{NAME}” as run {body['run_id']}."


def test_run_workflow_says_it_skipped_a_start_while_one_runs(workflow) -> None:
    prior = _busy(workflow("skip"))
    result = _start()
    assert result.outcome == "skip"
    assert json.loads(result.stdout)["run_id"] == prior.id
    assert result.summary == (
        f"Did not start “{NAME}”: run {prior.id} is still going, and this workflow skips a start "
        "while one is."
    )


def test_run_workflow_says_what_it_queued_behind(workflow) -> None:
    prior = _busy(workflow("queue"))
    result = _start()
    assert result.outcome == "queued"
    queued = json.loads(result.stdout)["run_id"]
    assert result.summary == (
        f"Queued “{NAME}” as run {queued}; it starts when run {prior.id} ends."
    )
    # A second start meets the full queue.
    dropped = _start()
    assert dropped.outcome == "skip" and json.loads(dropped.stdout)["dropped"] is True
    assert dropped.summary == (
        f"Did not start “{NAME}”: its queue is full, and run {queued} is already waiting to start."
    )


def test_run_workflow_says_it_stopped_the_run_it_replaced(workflow) -> None:
    prior = _busy(workflow("cancel_previous"))
    result = _start()
    assert result.outcome == "launched"
    started = json.loads(result.stdout)["run_id"]
    assert (
        result.summary == f"Started “{NAME}” as run {started}, after asking run {prior.id} to stop."
    )


def test_run_workflow_says_a_repeated_request_started_nothing_new(workflow) -> None:
    workflow()
    first = _start(idempotency_key="k-1")
    again = _start(idempotency_key="k-1")
    run_id = json.loads(first.stdout)["run_id"]
    assert json.loads(again.stdout) == {"run_id": run_id, "deduped": True}
    assert again.summary == (
        f"This request already started “{NAME}” as run {run_id}, so no second run began."
    )


# ── net-fetch ────────────────────────────────────────────────────────────────


def _fetched(
    *,
    url: str = "https://example.com/news",
    text: str = "x" * 1234,
    status: int = 200,
    content_type: str = "text/html; charset=utf-8",
    max_chars: int = 20_000,
    capped: bool = False,
) -> ActionResult:
    """The provider's own projection of a guarded response — the code after the network."""
    response = SimpleNamespace(
        status=status,
        headers={"Content-Type": content_type},
        url=url,
        text=text,
        truncated=capped,
    )
    return NetFetchActionProvider()._to_result(
        response, requested_url=url, max_chars=max_chars, started=0.0
    )


def test_net_fetch_says_how_much_it_read_and_from_where() -> None:
    """🔴 Red on main: the row was the JSON, the whole fenced page included."""
    result = _fetched()
    assert result.success is True
    assert result.summary == "Fetched 1,234 characters from example.com (HTTP 200, text/html)."
    body = json.loads(result.stdout)
    assert body["chars"] == 1234 and body["url"] == "https://example.com/news"
    assert "<untrusted_content" in body["text"], "the page stays fenced on stdout for the model"


@pytest.mark.parametrize(
    ("kw", "said"),
    [
        (
            {"url": "https://someone:hunter2@bank.example:8443/account?token=abc#top"},
            "Fetched 1,234 characters from bank.example:8443 (HTTP 200, text/html).",
        ),
        (
            {"text": "abcdefghij", "max_chars": 5, "content_type": "text/plain"},
            "Fetched the first 5 characters from example.com (HTTP 200, text/plain); the page is "
            "longer.",
        ),
        (
            {"capped": True},
            "Fetched 1,234 characters from example.com (HTTP 200, text/html). The download "
            "stopped at its size limit, so the page may be incomplete.",
        ),
        (
            {"text": "", "status": 204, "content_type": ""},
            "Fetched an empty page from example.com (HTTP 204).",
        ),
    ],
    ids=["names-the-site-only", "cut-to-the-bound", "cut-by-the-transfer-cap", "an-empty-page"],
)
def test_net_fetch_sentence_edges(kw: dict[str, Any], said: str) -> None:
    assert _fetched(**kw).summary == said


# ── inbox-op ─────────────────────────────────────────────────────────────────


def _message(**fields: Any) -> InboxItem:
    base: dict[str, Any] = {
        "id": "C1_100.5",
        "channel": "C1",
        "channel_name": "#general",
        "thread_ts": None,
        "message": "a secret the row must never quote",
        "sender_id": "U1",
        "sender_name": "alice",
        "status": ItemStatus.PENDING.value,
        "created_at": 100.5,
    }
    return InboxItem(**{**base, **fields})


@pytest.fixture
def inbox(tmp_path, monkeypatch):
    """Real, tmp-backed inbox handles behind the `live_store`/`live_state` seams the provider
    reads — the same shape `test_proactive_autoexec` uses."""

    def install(item: InboxItem) -> None:
        store_ = InboxStore(path=tmp_path / "inbox_items.json")
        state_ = InboxState(path=tmp_path / "inbox_state.json")
        store_.add(item)
        store_.save()

        class _Svc:
            inbox = store_

        _Svc.state = state_

        class _State:
            _inbox_svc = _Svc()
            _sessions: dict = {}

            def broadcast_ws(self, event: str, payload: Any) -> None:
                pass

            def notify(self, **kwargs: Any) -> None:
                pass

        dashboard = _State()
        monkeypatch.setattr(
            "personalclaw.action_providers.services.get_action_services",
            lambda: SimpleNamespace(state=dashboard),
        )

    return install


def _op(op: str, **config: Any) -> ActionResult:
    return asyncio.run(
        InboxOpActionProvider().execute(
            {"op": op, "item_id": "C1_100.5", **config}, ActionContext(event="triage")
        )
    )


@pytest.mark.parametrize(
    ("op", "done", "already"),
    [
        (
            "archive",
            "Archived the message from alice in #general.",
            "The message from alice in #general was already archived, so nothing changed.",
        ),
        (
            "mark_read",
            "Marked the message from alice in #general as read.",
            "The message from alice in #general was already read, so nothing changed.",
        ),
        (
            "dismiss",
            "Dismissed the message from alice in #general.",
            "The message from alice in #general was already dismissed, so nothing changed.",
        ),
        (
            "mute_thread",
            "Muted the thread of the message from alice in #general.",
            "The thread of the message from alice in #general was already muted, so nothing "
            "changed.",
        ),
    ],
)
def test_inbox_op_says_what_it_did_to_which_message(inbox, op: str, done: str, already: str):
    """🔴 Red on main: the row was `{"op": …, "item_id": …, "changed": true}`."""
    inbox(_message())
    first = _op(op)
    assert first.success and json.loads(first.stdout)["changed"] is True
    assert first.summary == done
    second = _op(op)
    assert second.success and json.loads(second.stdout)["changed"] is False
    assert second.summary == already


def test_inbox_op_says_it_drafted_and_sent_nothing(inbox) -> None:
    inbox(_message())
    result = _op("reply_draft", draft="Thanks — on it.")
    assert json.loads(result.stdout)["drafted"] == len("Thanks — on it.")
    assert (
        result.summary == "Drafted a reply to the message from alice in #general; nothing was sent."
    )


def test_inbox_op_names_a_message_in_one_short_line_and_never_quotes_it(inbox) -> None:
    inbox(
        _message(sender_name="a sender\nwhose name runs on and on past any line", channel_name="")
    )
    said = _op("archive").summary
    assert said == "Archived the message from a sender whose name runs on and on past…."
    assert "secret" not in said
    inbox(_message(sender_name="", channel_name=""))
    assert _op("archive").summary == "Archived the Inbox item."
    # A name is the channel's string: no control character in it reaches the row.
    inbox(_message(sender_name="eve\x1b[2J\x0c\x00", channel_name="#ops\r\n"))
    assert _op("archive").summary == "Archived the message from eve [2J in #ops."


# ── the row the recorder writes ──────────────────────────────────────────────


def _row(home: Path, result: ActionResult, trigger_id: str) -> dict[str, Any]:
    """Record one fire through the real recorder, and read its row back with its trace."""
    from personalclaw.gateway import GatewayOrchestrator
    from personalclaw.schedule_history import ScheduleRunStore
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    trigger = Trigger(
        id=trigger_id, name=trigger_id, kind="clock", spec={"kind": "cron", "expr": "0 9 * * *"}
    )
    TriggerStore(base_dir=home).upsert(trigger)
    orch = object.__new__(GatewayOrchestrator)
    orch.dashboard_state = None
    asyncio.run(orch._record_fire_outcome(trigger, result=result))
    runs = ScheduleRunStore(home)
    (row,), _total = asyncio.run(runs.list_for_job(trigger_id, 0, 5))
    full = asyncio.run(runs.get_run(trigger_id, row["run_id"]))
    assert full is not None
    return {**row, "trace": full.get("trace")}


def test_the_row_is_the_sentence_and_the_trace_is_what_it_printed(
    _isolated_home, inbox, workflow
) -> None:
    workflow()
    started = _start()
    inbox(_message())  # after the start: each fixture wires the action services it needs
    for index, result in enumerate((started, _fetched(), _op("archive"))):
        row = _row(_isolated_home, result, f"clock:t{index}")
        assert row["summary"] == result.summary != ""
        assert row["trace"] == result.stdout
