"""The triage digest offers Always and Never only where a rule can answer a proposal on its own.

🔴 The defect (Backlog: "Always on a triage task proposal teaches a rule that can never run on its
own"). The card offered Always ("Always allow <pattern>") on any proposal with a pattern, and a
reply's ``always yes N`` saved the rule whatever the kind. But a taught rule is read only for a
proposal the digest may run with nobody watching, and that set is the Inbox's own operations
(``autoexec.AUTO_CAPABLE_PROVIDERS``). Filing a task is not in it, by the maintainer's decision, so
the rule was stored and never applied: the Yes ran once, the card said "Done.", and the next digest
asked again. A Never on a task was as inert.

Now each pending row says whether a rule could answer one like it on its own (``always_runs``,
from the server's own tables and the set the digest's run recorded it may dispatch unattended:
its node's, which can widen the default), the card offers Always and Never only where it could,
and ``answer.persist_rule`` teaches no rule for any other row: a typed ``always yes`` or
``always no`` answers it once and says nothing was remembered, and why.

The digest, its record and the reply route are this test's, through the real answer path
(``test_a_triage_yes_does_what_it_proposed``'s harness).
"""

from __future__ import annotations

from typing import Any

import pytest
from test_a_triage_yes_does_what_it_proposed import (  # noqa: F401 - fixtures, used by name
    _PROPOSALS,
    TASK_TITLE,
    _native_tasks,
    _record_a_digest,
    anyio_backend,
    inbox,
    reply,
)

from personalclaw.proactive import autoexec

pytestmark = pytest.mark.anyio

#: What the reply says when no rule could answer a task proposal for you.
TASK_ASKS = "filing a task always asks for your answer, so no rule could answer it for you"


class _Memory:
    """The memory a taught rule is written to, noting each write."""

    def __init__(self) -> None:
        self.written: list[str] = []

    def set_semantic(self, key: str, value: Any, confidence: float, source: str) -> None:
        self.written.append(key)
        return None


@pytest.fixture
def memory(monkeypatch: Any) -> _Memory:
    import personalclaw.dashboard.handlers.memory as memory_handlers

    held = _Memory()
    # The reply route reads the memory a rule is written to from here, as it runs.
    monkeypatch.setattr(memory_handlers, "_global_service", lambda state: held)
    return held


async def _pending() -> dict[str, dict]:
    from personalclaw.proactive import digest_state

    view = digest_state.current_view()
    return {row["action_type"]: row for row in view["pending"]}


async def test_each_pending_row_says_whether_a_rule_could_answer_it_on_its_own(
    inbox: Any, reply: Any  # noqa: F811 - the imported fixtures
) -> None:
    """🔴 Before: no row said it, so the card offered Always on every row with a pattern."""
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))
    rows = await _pending()
    assert {kind: row["always_runs"] for kind, row in rows.items()} == {
        "reply_draft": True,
        "create_task": False,
        "archive": True,
    }


async def test_a_carried_over_row_says_it_too(
    inbox: Any, reply: Any  # noqa: F811 - the imported fixtures
) -> None:
    """A proposal an earlier digest left waiting, which this one carries (`carry`), is a pending
    row of its own: it says `always_runs` as this run's rows do."""
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))
    reply.output["carried"] = [
        {
            "item_id": "4",
            "action_type": "create_task",
            "tier": "low",
            "pattern_key": "create_task:sender:dana@example.com",
            "action_config": {"title": "Send Dana the contract"},
            "first_proposed_at": "2026-09-19T08:00:00+00:00",
            "first_run_id": "run-earlier",
        }
    ]
    from personalclaw.proactive import digest_state

    (carried,) = [row for row in digest_state.current_view()["pending"] if row["ordinal"] == "4"]
    assert (carried["carried_over"], carried["always_runs"]) == (True, False)


async def test_always_yes_on_a_task_files_it_once_and_remembers_nothing(
    inbox: Any, reply: Any, memory: _Memory  # noqa: F811 - the imported fixtures
) -> None:
    """🔴 Before: the rule was saved and never applied, and the reply said it was remembered."""
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))
    (result,) = (await reply.send("always yes 2"))["results"]
    assert result["executed"] is True, result
    assert [task.title for task in await _native_tasks()] == [TASK_TITLE], "the Yes ran, once"
    assert (result["rule"], result["rule_error"]) == ("", TASK_ASKS)
    assert memory.written == [], "no rule was written"


async def test_always_no_on_a_task_declines_it_once_and_remembers_nothing(
    inbox: Any, reply: Any, memory: _Memory  # noqa: F811 - the imported fixtures
) -> None:
    """A Never on a task was as inert: rules are read only where the digest may run unattended."""
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))
    (result,) = (await reply.send("always no 2"))["results"]
    assert result["executed"] is False
    assert (result["rule"], result["rule_error"]) == ("", TASK_ASKS)
    assert memory.written == []
    assert await _native_tasks() == []


async def test_always_yes_on_an_archive_is_still_remembered(
    inbox: Any, reply: Any, memory: _Memory  # noqa: F811 - the imported fixtures
) -> None:
    """The control: a kind a rule does answer on its own keeps its Always."""
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))
    (result,) = (await reply.send("always yes 3"))["results"]
    assert result["executed"] is True, result
    assert result["rule"] and result["rule_error"] == ""
    assert memory.written == [result["rule"]]


async def test_a_channel_reply_says_nothing_was_remembered_and_why(
    inbox: Any, reply: Any, memory: _Memory  # noqa: F811 - the imported fixtures
) -> None:
    """What a chat channel's reply says back: the Yes ran, and no rule was kept, with the reason."""
    from personalclaw.proactive.channel_reply import _result_line

    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))
    (result,) = (await reply.send("always yes 2"))["results"]
    line = _result_line(result)
    assert "done." in line and f"Nothing was remembered: {TASK_ASKS}." in line, line


#: A digest node that lets a rule file a task with nobody watching, as its run records it.
WIDENED = ["create-task", "inbox-op"]


async def test_a_node_that_widens_its_set_keeps_always_where_its_rules_run(
    inbox: Any, reply: Any, memory: _Memory  # noqa: F811 - the imported fixtures
) -> None:
    """A digest whose node lists `create-task` runs a taught rule for a task on its own, so its
    card offers Always on a task and an `always yes` teaches the rule."""
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))
    reply.output["auto_capabilities"] = WIDENED
    assert (await _pending())["create_task"]["always_runs"] is True
    (result,) = (await reply.send("always yes 2"))["results"]
    assert result["executed"] is True, result
    assert result["rule"] and result["rule_error"] == ""
    assert memory.written == [result["rule"]]


@pytest.mark.parametrize("recorded", ["create-task", "[not json", [], [" "], {"x": 1}])
async def test_an_unreadable_recorded_set_is_the_default_one(
    inbox: Any, reply: Any, memory: _Memory, recorded: Any  # noqa: F811 - the imported fixtures
) -> None:
    """A recorded set that cannot be read, or names nothing, never widens the default: the task
    row still asks each time, and an `always yes` on it teaches nothing."""
    reply.output.update(await _record_a_digest(inbox, _PROPOSALS))
    reply.output["auto_capabilities"] = recorded
    assert (await _pending())["create_task"]["always_runs"] is False
    (result,) = (await reply.send("always yes 2"))["results"]
    assert (result["rule"], result["rule_error"]) == ("", TASK_ASKS)
    assert memory.written == []


@pytest.mark.parametrize(
    ("node", "recorded"),
    [
        ({}, ["inbox-op"]),
        ({"capabilities": ["inbox-op", "create-task"]}, WIDENED),
        ({"capabilities": '["inbox-op", "create-task"]'}, WIDENED),
        ({"capabilities": "[not json"}, ["inbox-op"]),
        ({"capabilities": []}, ["inbox-op"]),
    ],
)
async def test_the_digest_run_records_the_set_its_node_declares(
    monkeypatch: Any, node: dict, recorded: list[str]
) -> None:
    """What the view and the reply read is what the run ran under: the node's own set, read once
    (`autoexec.capability_set`), the default when it declares none or one that cannot be read."""
    import json

    import personalclaw.proactive.pipeline as pipeline_mod
    from personalclaw.action_providers.base import ActionContext
    from personalclaw.action_providers.triage_digest_provider import TriageDigestActionProvider
    from personalclaw.proactive.manifest import build_manifest
    from personalclaw.proactive.pipeline import TriageResult

    async def fake_run_triage(items: Any, **kwargs: Any) -> TriageResult:
        return TriageResult(manifest=build_manifest([]))

    monkeypatch.setattr(pipeline_mod, "run_triage", fake_run_triage)
    monkeypatch.setattr(
        "personalclaw.action_providers.triage_digest_provider._proactive_config",
        lambda: type("C", (), {"triage_enabled": True, "classifier_gate_enabled": False})(),
    )
    monkeypatch.setattr(
        "personalclaw.action_providers.triage_digest_provider._approval_rules", lambda: []
    )
    result = await TriageDigestActionProvider().execute(
        node, ActionContext(event="clock", payload={})
    )
    assert result.success is True, result
    assert json.loads(result.stdout)["auto_capabilities"] == recorded


def test_no_taught_rule_files_a_task_with_nobody_watching() -> None:
    """The line stays where the maintainer drew it: filing a task is not in the default
    unattended set, so no rule can file one on its own unless a digest's node widens its set, and
    none is taught for one otherwise."""
    assert autoexec.AUTO_CAPABLE_PROVIDERS == frozenset({"inbox-op"})
    assert autoexec.always_runs("create_task") is False
    assert all(
        autoexec.always_runs(kind) for kind in ("archive", "mute_thread", "dismiss", "reply_draft")
    )
