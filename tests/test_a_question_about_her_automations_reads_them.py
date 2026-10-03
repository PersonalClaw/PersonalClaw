"""A question about her automations reads them, and what it reads says how each one stands.

Asked in a weekly review whether her morning brief and her digest had happened, the agent answered
"unknown", and said a file automation was still waiting for her approval: the brief had run that
morning, the digest that evening, and the file automation ran on its own. The turn never read her
automations. Nothing in it said a tool could (with a few tool servers set up, the catalog showed
the automation tools as a bare count), and that tool listed each automation's kind and health but
neither when it last ran nor how that went, so what the agent remembered of earlier chats was all
it had.

Now `automation_list` says, for each automation, whether it runs now, when it last ran and how that
went, and when it runs next, and marks the ones that need her; and every native turn that may call
it carries one line saying how many automations she has, how many need attention, and the tool
that reads them.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from personalclaw.nl_to_cron import Schedule
from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore
from personalclaw.triggers import tools as T
from personalclaw.triggers.store import TriggerStore

_ZONE = "America/Toronto"

#: Friday 2 October 2026 in her zone (EDT, UTC-4).
_BRIEF_RAN = 1790938800.0  # 07:00
_KITCHEN_RAN = 1790973057.0  # 16:30:57
_DIGEST_RAN = 1790979840.0  # 18:24
_BACKUP_FAILED = 1790982000.0  # 19:00
#: Saturday 3 October 2026, 07:00 in her zone.
_BRIEF_NEXT = "2026-10-03T11:00:00+00:00"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Her PersonalClaw home, in her time zone."""
    pc_home = Path(os.path.realpath(tmp_path)) / "pc-home"
    (pc_home / "workspace").mkdir(parents=True)
    (pc_home / "config.json").write_text(json.dumps({"timezone": _ZONE}), encoding="utf-8")
    monkeypatch.setenv("PERSONALCLAW_HOME", str(pc_home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: pc_home)
    return pc_home


def _daily(expr: str):
    """The cadence converter, injected so no test needs a model."""
    return lambda _cadence: Schedule(expr=expr)


def _make(store: TriggerStore, name: str, when: str, *, owner: bool = True, expr: str = "") -> str:
    result = T.create(
        store,
        name=name,
        when=when,
        message=f"{name}: do it.",
        created_by="user" if owner else "agent",
        owner_consented=owner,
        cadence_to_cron=_daily(expr) if expr else None,
    )
    assert result.ok, result.text
    return result.data["trigger"]["id"]


def _ran(home: Path, trigger_id: str, at: float, status: str = "success", **fields) -> None:
    ScheduleRunStore(home).append_sync(
        ScheduleRun(job_id=trigger_id, started_at=at, finished_at=at + 20, status=status, **fields)
    )


def _edit(store: TriggerStore, trigger_id: str, **fields) -> None:
    row = store.get(trigger_id)
    assert row is not None
    for key, value in fields.items():
        setattr(row.trigger, key, value)
    store.upsert(row.trigger)


def _her_automations(home: Path) -> dict[str, str]:
    """Six automations, as her week left them: three that ran fine, one whose last runs failed,
    one an agent made that waits for her Allow, and one she switched off."""
    store = TriggerStore(base_dir=home)
    ids = {
        "brief": _make(store, "Morning brief", "every day at 7:00", expr="0 7 * * *"),
        "digest": _make(store, "Project digest", "every day at 18:00", expr="0 18 * * *"),
        "kitchen": _make(
            store, "Kitchen PDF summary", "when a file in ~/Documents/Home/Kitchen changes"
        ),
        "backup": _make(store, "Backup check", "every day at 19:00", expr="0 19 * * *"),
        "agent": _make(store, "Inbox sweep", "every day at 8:00", owner=False, expr="0 8 * * *"),
        "off": _make(store, "Soccer bag", "every Saturday at 8:00", expr="0 8 * * 6"),
    }
    _ran(home, ids["brief"], _BRIEF_RAN, summary="Your brief: three things today.")
    _ran(home, ids["kitchen"], _KITCHEN_RAN, summary="Summarised the new quote.")
    _ran(home, ids["digest"], _DIGEST_RAN, summary="12 items digested.")
    _ran(
        home,
        ids["backup"],
        _BACKUP_FAILED,
        status="failure",
        error="the backup folder ~/Backups is not mounted",
    )
    _edit(store, ids["brief"], next_fire_at=_BRIEF_NEXT)
    _edit(store, ids["backup"], health_status="degraded", last_error_summary="failure 1 of 5")
    assert T.set_paused(store, trigger_id=ids["off"], paused=True).ok
    return ids


def _block(text: str, trigger_id: str) -> list[str]:
    """The lines the list says about one automation: its own line and the indented ones under it."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{trigger_id} — "))
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        block.append(line.strip())
    return block


# ── what the list says ──────────────────────────────────────────────────────────────────────


def test_the_list_says_when_each_automation_last_ran_and_how_that_went(home):
    """🔴 Before: one line each, its kind and `health=ok`, and no run at all."""
    ids = _her_automations(home)
    text = T.list_automations(TriggerStore(base_dir=home)).text

    brief = _block(text, ids["brief"])
    assert brief[0].startswith(f"{ids['brief']} — Morning brief (clock"), brief
    assert brief[1:] == [
        "it is active now and visible on the Triggers page",
        "last run Fri 2 Oct 2026, 07:00: ok",
        "next run Sat 3 Oct 2026, 07:00",
    ], brief
    assert "last run Fri 2 Oct 2026, 18:24: ok" in _block(text, ids["digest"])
    kitchen = _block(text, ids["kitchen"])
    assert kitchen[1:] == [
        "it is active now and visible on the Triggers page",
        "last run Fri 2 Oct 2026, 16:30: ok",
    ], kitchen


def test_the_list_marks_what_needs_her_and_says_why(home):
    ids = _her_automations(home)
    text = T.list_automations(TriggerStore(base_dir=home)).text

    agent = _block(text, ids["agent"])
    assert agent[0].endswith("⚠ needs attention"), agent
    assert agent[1].startswith(
        "it is on the Triggers page, and it does not run until you allow"
    ), agent
    assert agent[2] == "it has not run yet", agent
    assert not any(line.startswith("next run") for line in agent), "it will not run on its own"

    backup = _block(text, ids["backup"])
    assert backup[0].endswith("⚠ needs attention"), backup
    assert "⚠ its health reads degraded — failure 1 of 5" in backup, backup
    assert (
        "last run Fri 2 Oct 2026, 19:00: failed — the backup folder ~/Backups is not mounted"
    ) in backup, backup

    off = _block(text, ids["off"])
    assert not off[0].endswith("⚠ needs attention"), "switching it off was her own choice"
    assert off[1] == "it is switched off until you enable it, and visible on the Triggers page"
    assert not any(line.startswith("next run") for line in off), off

    for calm in ("brief", "digest", "kitchen"):
        assert "⚠" not in " ".join(_block(text, ids[calm])), calm


def test_the_list_opens_with_how_many_there_are_and_the_zone_its_times_are_in(home):
    _her_automations(home)
    text = T.list_automations(TriggerStore(base_dir=home)).text
    assert text.splitlines()[0] == (
        "6 automations: 5 on, 1 switched off; 2 need attention. Times are in America/Toronto."
    ), text


def test_the_data_carries_each_last_run_beside_the_words(home):
    ids = _her_automations(home)
    rows = {a["id"]: a for a in T.list_automations(TriggerStore(base_dir=home)).data["automations"]}
    assert rows[ids["digest"]]["last_run"] == {
        "at": _DIGEST_RAN,
        "status": "success",
        "said": "Fri 2 Oct 2026, 18:24: ok",
    }
    assert rows[ids["agent"]]["last_run"] is None
    assert rows[ids["agent"]]["needs_attention"] is True
    assert rows[ids["off"]]["needs_attention"] is False
    assert rows[ids["brief"]]["standing"] == "it is active now and visible on the Triggers page"


def test_a_run_whose_record_is_gone_is_read_from_the_automations_own_stamps(home):
    """Its history aged out or was not restored: the trigger's stamps still say when, and whether
    it went wrong."""
    store = TriggerStore(base_dir=home)
    trigger_id = _make(store, "Morning brief", "every day at 7:00", expr="0 7 * * *")
    _edit(
        store,
        trigger_id,
        last_failure_at="2026-10-02T11:00:00+00:00",
        last_error_summary="the model did not answer",
    )
    block = _block(T.list_automations(store).text, trigger_id)
    assert "last run Fri 2 Oct 2026, 07:00: went wrong — the model did not answer" in block, block


def test_every_status_a_run_record_can_hold_has_its_words():
    """The run statuses are a closed vocabulary (`SCHEDULE_STATUS_TO_OUTCOME`, which the status
    vocabulary rail keeps complete): a new one needs its words here, or the list would say a raw
    status to the owner."""
    from personalclaw.triggers.history import SCHEDULE_STATUS_TO_OUTCOME
    from personalclaw.triggers.standing import RUN_SAID

    assert set(RUN_SAID) == set(SCHEDULE_STATUS_TO_OUTCOME)
    assert all(words.strip() and "_" not in words for words in RUN_SAID.values()), RUN_SAID


def test_a_skipped_run_says_what_held_it(home):
    store = TriggerStore(base_dir=home)
    trigger_id = _make(store, "Morning brief", "every day at 7:00", expr="0 7 * * *")
    _ran(home, trigger_id, _BRIEF_RAN, status="skipped_gate", error="quiet hours until 07:30")
    block = _block(T.list_automations(store).text, trigger_id)
    assert (
        "last run Fri 2 Oct 2026, 07:00: skipped: one of its conditions held it — quiet hours "
        "until 07:30"
    ) in block, block


# ── what the turn is told ───────────────────────────────────────────────────────────────────


def _offered(_name: str) -> bool:
    return True


def test_the_turn_line_counts_them_and_names_the_tool_that_reads_them(home):
    from personalclaw.triggers.standing import automations_note

    _her_automations(home)
    note = asyncio.run(automations_note({"automation_list": object()}, _offered))
    assert note.startswith(
        "[automations] The user has 6 automations: 5 on, 1 switched off; 2 need attention. "
        "automation_list says how each one stands, when it last ran and how that went, and when "
        "it runs next"
    ), note


def test_the_turn_line_says_when_there_are_none(home):
    from personalclaw.triggers.standing import automations_note

    note = asyncio.run(automations_note({"automation_list": object()}, _offered))
    assert note == "[automations] The user has no automations."


def test_a_turn_that_cannot_read_them_is_not_told_of_them(home):
    """No tool to read them, or a run its host does not show the tool to: no line."""
    from personalclaw.triggers.standing import automations_note

    _her_automations(home)
    assert asyncio.run(automations_note({}, _offered)) == ""
    hidden = asyncio.run(automations_note({"automation_list": object()}, lambda _n: False))
    assert hidden == ""


def test_a_store_that_cannot_be_read_costs_the_turn_nothing(home, monkeypatch):
    from personalclaw.triggers.standing import automations_note

    def _broken(self):
        raise PermissionError("the store cannot be read")

    monkeypatch.setattr(TriggerStore, "load", _broken)
    assert asyncio.run(automations_note({"automation_list": object()}, _offered)) == ""


def test_the_tool_says_what_it_reads_where_the_catalog_cuts_it_short():
    """A turn that defers the tool's schema lists it by the first 100 characters of its
    description (`ToolRetriever.catalog`): they say what it reads."""
    from personalclaw.agents.native.tool_retrieval import ToolRetriever
    from personalclaw.tool_providers.registry import create_automation_provider

    defs = asyncio.run(create_automation_provider().list_tools())
    catalog = ToolRetriever(defs).catalog()
    (line,) = [entry for entry in catalog.splitlines() if entry.startswith("- automation_list:")]
    assert line.startswith(
        "- automation_list: The owner's automations: whether each runs now, when it last ran and "
        "how that went, its next run."
    ), line


# ── the weekly review, driven through the native loop ─────────────────────────────────────

#: A saved weekly-review prompt as a chat runs one (`chat_runner` expands `@name` this way).
_WEEKLY_REVIEW = (
    "Execute the following instructions:\n\n"
    "Run my weekly review from ~/Notes/Garden.\n\n"
    "- Read the last 7 daily notes in `Daily/`.\n"
    "- List what I said I would do last Sunday (`Weekly/` latest) and whether it happened.\n"
    "- Propose at most five priorities for the coming week.\n"
)


def test_the_weekly_review_turn_reads_her_automations(home):
    """A scripted model asks the list in the review's turn: the turn told it the tool exists, the
    read asks nobody, and what comes back says how each automation's last run went."""
    from test_native_runtime import _defn, _drain, _ScriptedModel

    from personalclaw.agents.native.builtin_tools import (
        PLATFORM_CATEGORIES,
        NativeBuiltinToolProvider,
    )
    from personalclaw.agents.native.runtime import NativeAgentRuntime
    from personalclaw.llm.events import (
        EVENT_COMPLETE,
        EVENT_PERMISSION_REQUEST,
        EVENT_TEXT_CHUNK,
        EVENT_TOOL_CALL,
        EVENT_TOOL_RESULT,
        AgentEvent,
    )
    from personalclaw.tool_providers.registry import create_automation_provider

    ids = _her_automations(home)
    model = _ScriptedModel(
        [
            [
                AgentEvent(
                    kind=EVENT_TOOL_CALL,
                    tool_call_id="c1",
                    title="automation_list",
                    tool_input="{}",
                ),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
            [
                AgentEvent(kind=EVENT_TEXT_CHUNK, text="The brief and the digest both ran."),
                AgentEvent(kind=EVENT_COMPLETE),
            ],
        ]
    )
    platform = NativeBuiltinToolProvider(cwd=home / "workspace", categories=PLATFORM_CATEGORIES)

    async def _turn():
        runtime = NativeAgentRuntime(
            definition=_defn(),
            model_provider=model,
            tool_providers=[platform, create_automation_provider()],
            cwd=home / "workspace",
        )
        await runtime.start()
        return await asyncio.wait_for(_drain(runtime, _WEEKLY_REVIEW), timeout=20)

    events = asyncio.run(_turn())
    notes = [m["content"] for m in model.seen_messages[0] if m.get("role") == "system"]
    told = next((n for n in notes if "[automations]" in n), "")
    assert "The user has 6 automations: 5 on, 1 switched off; 2 need attention." in told, notes
    assert "automation_list says how each one stands" in told, told
    assert EVENT_PERMISSION_REQUEST not in [e.kind for e in events]
    result = str(next(e for e in events if e.kind == EVENT_TOOL_RESULT).tool_output)
    assert "last run Fri 2 Oct 2026, 07:00: ok" in "\n".join(_block(result, ids["brief"]))
    assert "last run Fri 2 Oct 2026, 18:24: ok" in "\n".join(_block(result, ids["digest"]))
    kitchen = _block(result, ids["kitchen"])
    assert "it is active now and visible on the Triggers page" in kitchen, kitchen
    assert "last run Fri 2 Oct 2026, 16:30: ok" in kitchen, kitchen
