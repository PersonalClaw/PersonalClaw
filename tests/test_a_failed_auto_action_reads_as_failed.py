"""An action the digest tried and could not do is said as not done, everywhere it is said.

The Morning triage digest can act on its own (auto-execution) and on a tap (a reply). Measured on
the tree before this was written:

* a dispatch that FAILED left an ``auto_executed`` ledger row, so anything counting that kind
  counted the failure as an action taken;
* the digest's own text put a failed archive under "Needs you" in exactly the words of a proposal
  nobody had tried, and said nothing of the failure; the spend floor's stop sat under "What your
  machine did", and incident mode stopping every action left no word at all;
* the card read the same summary, which dropped the failure's reason, and so said "Auto-execution
  ran and found nothing it was allowed to do on its own" for a run that tried and failed;
* a "yes" on a proposal never acted: the reply rebuilt its manifest from card rows that carry no
  store id, so every row was dropped, and the refusal was recorded as the user's "declined".

So each surface is asserted with its pair: the failure, and the success it must not read like.
Nothing here touches the real home; the store-backed parts are monkeypatched or in-memory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.proactive.autoexec import SKIP_BUDGET, SKIP_FAILED, SKIP_NEEDS_YOU, auto_execute
from personalclaw.proactive.manifest import SOURCE_INBOX, CollectedItem, build_manifest
from personalclaw.proactive.pipeline import run_triage
from personalclaw.proactive.proposals import Proposal
from personalclaw.proactive.surface import build_digest_view

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)
_TITLE = "Weekly newsletter from example.com"
_ERROR = "the inbox item is gone"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _items() -> list[CollectedItem]:
    return [
        CollectedItem(
            source=SOURCE_INBOX,
            source_id="inbox-a",
            title=_TITLE,
            sender="news@example.com",
            ts="2026-08-25T01:00:00+00:00",
        )
    ]


def _archive(item_id: str = "1") -> Proposal:
    return Proposal(
        item_id=item_id, action_type="archive", tier="trivial", pattern_key="archive:sender:news"
    )


class _Dispatch:
    def __init__(self, *, ok: bool) -> None:
        self.ok = ok
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, provider: str, config: dict, ctx: Any = None) -> Any:
        self.calls.append((provider, dict(config)))
        return SimpleNamespace(
            success=self.ok,
            reversal="inbox-op:handle" if self.ok else "",
            error="" if self.ok else _ERROR,
        )


def _clean_budget() -> tuple[bool, str]:
    return False, ""


def _section(body: str, heading: str) -> str:
    """One section of the digest body, by its heading line, or '' when it is absent."""
    for block in body.split("\n\n"):
        if block.startswith(heading):
            return block
    return ""


async def _digest(*, ok: bool | None = None, budget: Any = _clean_budget) -> str:
    """The digest body for one trivial archive of the fixture's one item."""

    async def completion(prompt: str, **_kw: Any) -> Any:
        return {
            "proposals": [
                {
                    "item_id": "1",
                    "action_type": "archive",
                    "tier": "trivial",
                    "pattern_key": "archive:sender:news",
                    "reasoning": "a newsletter",
                }
            ]
        }

    dispatch = _Dispatch(ok=bool(ok))

    async def stage(proposals: Any, manifest: Any) -> Any:
        return await auto_execute(
            proposals,
            manifest=manifest,
            now=NOW,
            enabled=True,
            cap=5,
            dispatch=dispatch,
            budget_check=budget,
        )

    result = await run_triage(
        _items(),
        gate_enabled=False,
        completion=completion,
        deliver=lambda _d: True,
        auto_execute=stage,
    )
    assert result.digest is not None
    return result.digest.body


# ── the record ──────────────────────────────────────────────────────────────────────────────


async def test_a_failed_action_is_recorded_as_failed_and_never_as_executed() -> None:
    from personalclaw.ledger.kinds import AUTO_EXECUTED, AUTO_FAILED, LEDGER_KINDS

    assert AUTO_FAILED in LEDGER_KINDS, "a row outside LEDGER_KINDS is unreadable"
    # One token for the deferral and the row, as `skipped_budget` is: a reader counting failures
    # must not have to know which surface to trust.
    assert SKIP_FAILED == AUTO_FAILED

    manifest = build_manifest(_items())
    rows: list[dict] = []
    failed = await auto_execute(
        [_archive()],
        manifest=manifest,
        now=NOW,
        enabled=True,
        cap=5,
        dispatch=_Dispatch(ok=False),
        budget_check=_clean_budget,
        ledger=lambda kind, fields: rows.append({"kind": kind, **fields}),
    )
    assert [r["kind"] for r in rows] == [AUTO_FAILED]
    assert rows[0]["reason"] == _ERROR
    summary = failed.summary()
    assert summary["auto_executed"] == []
    (deferred,) = summary["auto_deferred"]
    assert (deferred["reason"], deferred["detail"]) == (SKIP_FAILED, _ERROR)

    # The pair: a dispatch that landed is the only thing written as executed.
    landed_rows: list[dict] = []
    await auto_execute(
        [_archive()],
        manifest=manifest,
        now=NOW,
        enabled=True,
        cap=5,
        dispatch=_Dispatch(ok=True),
        budget_check=_clean_budget,
        ledger=lambda kind, fields: landed_rows.append({"kind": kind, **fields}),
    )
    assert [r["kind"] for r in landed_rows] == [AUTO_EXECUTED]


# ── the digest's text (also the notification's body) ────────────────────────────────────────


async def test_the_digest_says_a_failed_action_failed_why_and_what_to_do() -> None:
    body = await _digest(ok=False)
    assert "auto-archive" not in body, "a failure is not listed as an action taken"
    assert _section(body, "What your machine did:") == ""
    needs = _section(body, "Needs you:")
    assert _TITLE in needs
    assert "failed" in needs and _ERROR in needs
    # What she can do next, in the reply grammar the digest accepts.
    assert "`1 yes`" in needs

    # The pair: the same window with a dispatch that landed.
    done = await _digest(ok=True)
    assert "auto-archive on #1" in _section(done, "What your machine did:")
    assert "failed" not in done
    assert "Needs you:" not in done


async def test_a_spend_stop_is_not_listed_as_something_the_machine_did() -> None:
    body = await _digest(budget=lambda: (True, "day token budget exceeded (9/9)"))
    # Nothing ran and no run ended in the window, so there is nothing the machine did.
    assert _section(body, "What your machine did:") == ""
    needs = _section(body, "Needs you:")
    assert "day token budget exceeded (9/9)" in needs
    assert _TITLE in needs


async def test_incident_mode_stopping_every_action_is_said(monkeypatch: Any) -> None:
    monkeypatch.setattr("personalclaw.guardrails.incident.incident_active", lambda: True)
    body = await _digest(ok=True)
    needs = _section(body, "Needs you:")
    assert "incident mode" in needs.lower()
    assert "auto-archive" not in body


def test_a_run_in_the_digest_counts_the_effects_that_landed_not_each_attempt(
    monkeypatch: Any,
) -> None:
    """A run's line under "What your machine did" counts what it changed, once each."""
    import personalclaw.ledger as ledger
    from personalclaw.proactive import collect
    from personalclaw.workflows import store as run_store

    def run(run_id: str, name: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=run_id,
            created_at="2026-08-25T04:00:00+00:00",
            status="complete",
            workflow_name=name,
            error_message="",
        )

    def effect(key: str, status: str) -> dict:
        return {
            "kind": "effect",
            "instance_path": "root.children[0]",
            "epoch": 0,
            "idempotency_key": key,
            "effect_status": status,
        }

    effects = {
        # One archive: the row written before the dispatch and the row written when it landed.
        "r-landed": [effect("k1", "attempted"), effect("k1", "committed")],
        # An attempt that never landed changed nothing.
        "r-tried": [effect("k2", "attempted")],
    }
    monkeypatch.setattr(
        run_store,
        "list_runs",
        lambda limit: ([run("r-landed", "tidy-inbox"), run("r-tried", "file-receipts")], 2),
    )
    monkeypatch.setattr(ledger, "read_events", lambda store, run_id, kinds: effects[run_id])

    items = {item.source_id: item for item in collect.collect_runs()}
    assert (items["r-landed"].title, items["r-landed"].materiality) == (
        "tidy-inbox: complete (1 effect)",
        "action",
    )
    assert (items["r-tried"].title, items["r-tried"].materiality) == (
        "file-receipts: complete",
        "response",
    )


# ── the card's read model ───────────────────────────────────────────────────────────────────


def _output(deferred: list[dict], **extra: Any) -> dict:
    return {
        "collected": 2,
        "items": [
            {
                "ordinal": "1",
                "source": "inbox",
                "source_id": "inbox-a",
                "title": _TITLE,
                "permalink": "",
                "materiality": "response",
            },
            {
                "ordinal": "2",
                "source": "inbox",
                "source_id": "inbox-b",
                "title": "Review request",
                "permalink": "",
                "materiality": "action",
            },
        ],
        "kept": ["1", "2"],
        "proposals": [
            {
                "item_id": "1",
                "action_type": "archive",
                "tier": "trivial",
                "pattern_key": "archive:sender:news",
                "clamped": False,
            },
            {
                "item_id": "2",
                "action_type": "reply_draft",
                "tier": "medium",
                "pattern_key": "reply_draft:inbox",
                "clamped": True,
            },
        ],
        "auto_executed": [],
        "auto_deferred": deferred,
        "budget_breached": False,
        "budget_reason": "",
        "auto_ledger_rows": 1,
        "dropped": 0,
        "refused": [],
        "ledger_rows": 0,
        **extra,
    }


_RUN = {"run_id": "run-abc", "status": "complete"}


def _view(output: dict, events: list[dict] | None = None) -> dict:
    return build_digest_view(
        enabled=True, installed=True, run=_RUN, output=output, events=events or []
    )


def test_the_card_row_says_the_action_failed_why_and_what_to_do() -> None:
    view = _view(
        _output(
            [
                {
                    "item_id": "1",
                    "action_type": "archive",
                    "tier": "trivial",
                    "reason": SKIP_FAILED,
                    "rule": "",
                    "detail": _ERROR,
                },
                {
                    "item_id": "2",
                    "action_type": "reply_draft",
                    "tier": "medium",
                    "reason": SKIP_NEEDS_YOU,
                    "rule": "",
                    "detail": "",
                },
            ]
        )
    )
    rows = {row["ordinal"]: row for row in view["pending"]}
    failed = rows["1"]["not_done"]
    assert "failed" in failed and _ERROR in failed
    # What she can do next, on the card: its own Yes button, not the channel reply grammar.
    assert "Yes tries it again" in failed and "`1 yes`" not in failed
    # The pair: a proposal nobody tried carries no failure.
    assert rows["2"]["not_done"] == ""
    assert view["auto_done"] == []


def test_the_card_says_why_nothing_ran_on_its_own() -> None:
    incident = _view(
        _output(
            [
                {
                    "item_id": o,
                    "action_type": a,
                    "tier": t,
                    "reason": "incident_active",
                    "rule": "",
                    "detail": "incident mode is active",
                }
                for o, a, t in (("1", "archive", "trivial"), ("2", "reply_draft", "medium"))
            ]
        )
    )
    assert "incident mode" in incident["auto_stopped"].lower()
    # One sentence for the stage, not a failure per proposal: the medium one was never going to
    # run on its own.
    assert all(row["not_done"] == "" for row in incident["pending"])

    budget = _view(
        _output(
            [
                {
                    "item_id": "1",
                    "action_type": "archive",
                    "tier": "trivial",
                    "reason": SKIP_BUDGET,
                    "rule": "",
                    "detail": "day token budget exceeded (9/9)",
                }
            ],
            budget_breached=True,
            budget_reason="day token budget exceeded (9/9)",
        )
    )
    assert "day token budget exceeded (9/9)" in budget["auto_stopped"]

    # The pair: a stage that simply proposed says nothing stopped it.
    quiet = _view(
        _output(
            [
                {
                    "item_id": "2",
                    "action_type": "reply_draft",
                    "tier": "medium",
                    "reason": SKIP_NEEDS_YOU,
                    "rule": "",
                    "detail": "",
                }
            ]
        )
    )
    assert quiet["auto_stopped"] == ""


def test_the_run_journal_lists_a_failure_under_its_own_kind_with_its_reason() -> None:
    view = _view(
        _output([]),
        events=[
            {
                "kind": "auto_failed",
                "seq": 2,
                "item_ordinal": "1",
                "action_type": "archive",
                "outcome": SKIP_FAILED,
                "reason": _ERROR,
                "rule": "policy:trivial-tier",
            },
            {
                "kind": "auto_executed",
                "seq": 1,
                "item_ordinal": "2",
                "action_type": "archive",
                "outcome": "executed",
                "rule": "policy:trivial-tier",
            },
        ],
    )
    assert [(row["kind"], row["reason"]) for row in view["journal"]] == [
        ("auto_executed", ""),
        ("auto_failed", _ERROR),
    ]
    # The run's record is not published under a name that says the machine did all of it.
    assert "machine_did" not in view


def test_an_answer_that_did_not_happen_says_so_on_the_card() -> None:
    sentence = "Not done: it was tried and it failed — the inbox item is gone."
    view = _view(
        _output(
            [
                {
                    "item_id": "1",
                    "action_type": "archive",
                    "tier": "trivial",
                    "reason": SKIP_NEEDS_YOU,
                    "rule": "",
                    "detail": "",
                },
                {
                    "item_id": "2",
                    "action_type": "reply_draft",
                    "tier": "medium",
                    "reason": SKIP_NEEDS_YOU,
                    "rule": "",
                    "detail": "",
                },
            ]
        ),
        events=[
            {
                "kind": "triage_reply",
                "item_ordinal": "1",
                "verb": "yes",
                "outcome": "failed",
                "detail": sentence,
                "seq": 4,
            },
            {
                "kind": "triage_reply",
                "item_ordinal": "2",
                "verb": "no",
                "outcome": "declined",
                "detail": "",
                "seq": 5,
            },
        ],
    )
    rows = {row["ordinal"]: row for row in view["pending"]}
    assert rows["1"]["answered"] is True
    assert rows["1"]["answer_not_done"] == sentence
    # The pair: a "no" is an answer that happened.
    assert rows["2"]["answer_not_done"] == ""


# ── a reply ────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def reply(monkeypatch: Any):
    """POST /api/proactive/digest/reply against one digest, with the stores replaced in memory."""
    import personalclaw.dashboard.handlers.proactive as mod
    import personalclaw.proactive.autoexec as autoexec

    written: list[dict] = []
    dispatch = _Dispatch(ok=True)
    # No auto stage ran, so the pending set is the run's raw proposals.
    output = {k: v for k, v in _output([]).items() if not k.startswith(("auto_", "budget_"))}

    monkeypatch.setattr(
        mod,
        "_install_state",
        lambda: {"installed": True, "enabled": True, "schedule": None, "drift": False},
    )
    monkeypatch.setattr(mod, "_latest_digest", lambda: (dict(_RUN), output, []))
    monkeypatch.setattr(
        mod,
        "_write_reply_row",
        lambda run_id, ordinal, **kw: written.append({"ordinal": ordinal, **kw}) or True,
    )
    monkeypatch.setattr(mod, "_run_ledger", lambda run_id: lambda kind, fields: None)
    monkeypatch.setattr(mod, "_sel", lambda: SimpleNamespace(log_api_access=lambda **kw: None))
    monkeypatch.setattr(autoexec, "_default_dispatch", dispatch)
    monkeypatch.setattr(autoexec, "default_budget_check", lambda *a, **k: _clean_budget)

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
                "/api/proactive/digest/reply", json={"run_id": "run-abc", "text": text}
            )
            assert resp.status == 200, await resp.text()
            return await resp.json()

    return SimpleNamespace(send=send, written=written, dispatch=dispatch)


async def test_a_yes_acts_on_the_item_its_proposal_names(reply: Any) -> None:
    body = await reply.send("1 yes")
    (result,) = body["results"]
    assert result["executed"] is True, result
    assert [(p, c["item_id"]) for p, c in reply.dispatch.calls] == [("inbox-op", "inbox-a")]
    assert reply.written[0]["outcome"] == "executed"
    assert result["not_done"] == ""


async def test_a_yes_that_fails_is_recorded_as_failed_and_said_plainly(reply: Any) -> None:
    reply.dispatch.ok = False
    body = await reply.send("1 yes")
    (result,) = body["results"]
    assert result["executed"] is False
    assert "failed" in result["not_done"] and _ERROR in result["not_done"]
    # Not the user's "declined": she said yes, and it failed.
    assert reply.written[0]["outcome"] == "failed"
    assert reply.written[0]["detail"] == result["not_done"]


async def test_a_no_is_recorded_as_declined(reply: Any) -> None:
    body = await reply.send("1 no")
    (result,) = body["results"]
    assert reply.dispatch.calls == []
    assert reply.written[0]["outcome"] == "declined"
    assert result["not_done"] == ""
