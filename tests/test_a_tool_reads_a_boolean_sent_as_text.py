"""A boolean a tool is sent as text is read as the word it spells, never by truthiness.

``bool("false")`` is True, and a model sends a declared boolean as text as often as not. Outside the
task and loop tools, which already read one so:

* ``edit_file`` sent ``"replace_all": "false"`` replaced every match of ``old_str``;
* ``grep`` sent ``"regex": "false"`` searched for a regular expression;
* ``glob`` and ``grep`` refused an ``ignore_case`` of ``"no"`` or ``"yes"``: a second reader of
  their own, which knew only ``true`` and ``false``;
* ``knowledge_update`` sent ``"is_archived": "false"`` archived the item;
* ``code_map`` sent ``"refresh": "false"`` re-indexed the whole tree;
* ``automation_update`` stored a patch's ``"enabled": "false"`` as sent, so the automation it
  switched off kept running (the stored row reading it back is a door of
  ``test_safety_flags_reject_truthy_strings``);
* the control bridge switched an automation ON when the owner confirmed ``"enabled": "false"``;
* an agent's question card sent ``"multiSelect": "false"`` became a multiple-choice card;
* a bulk unpin sent ``"value": "false"`` pinned the items;
* ``reset_tools`` switched on a group it was sent ``"false"`` for.

Each is read through ``safety_flags.yes_or_no``: a no is a no, a yes a yes, and a value that
spells neither is the field's safe value or, where there is none, refused. An in-process tool's
field spec refuses a text boolean before its handler runs, which is pinned here too.

The spellings themselves are rows of the one table in ``test_safety_flags_reject_truthy_strings``,
and ``test_tool_boolean_census`` holds every boolean a tool declares to the reader. So each door
here is driven through its real entry point three times, once per answer, each a row of one of
the three tests below: a yes, a no, and the values that spell neither. A door that holds a switch
starts each row from the other side of what it is sent, so a row shows the door wrote it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple

import pytest
from table_rows import row
from test_safety_flags_reject_truthy_strings import (
    JSON_NO,
    JSON_REFUSED,
    JSON_YES,
    WORD_NEITHER,
    WORD_NEITHER_NOT_BLANK,
    WORD_NO,
    WORD_YES,
    spells,
)

from personalclaw.agents.native import read_gate
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

pytestmark = pytest.mark.security

#: What a door does with a value it refuses, in place of reading it.
REFUSED = "refused"


def _starts_on(sent: object) -> bool:
    """Where a switch starts before it is sent *sent*: off before a yes, on before a no or a
    value that spells neither, so each row shows the door wrote what it read, or wrote nothing."""
    return spells(sent) is not True


@pytest.fixture(autouse=True)
def _fresh_read_ledger():
    read_gate.reset_all()
    yield
    read_gate.reset_all()


# ── edit_file: replace_all ───────────────────────────────────────────────────────────────────


async def _edit(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> str:
    """``"every match"``, or ``"no match"`` when it refused an edit that matched twice."""
    (scratch / "list.md").write_text("- milk\n- eggs\n- milk\n", encoding="utf-8")
    tools = NativeBuiltinToolProvider(scratch, session_key=f"boolean-edit-{scratch.name}")
    assert (await tools.invoke("read_file", {"path": "list.md"})).success
    result = await tools.invoke(
        "edit_file",
        {"path": "list.md", "old_str": "milk", "new_str": "oat milk", "replace_all": sent},
    )
    text = (scratch / "list.md").read_text()
    if result.success:
        assert text == "- oat milk\n- eggs\n- oat milk\n"
        return "every match"
    assert "matched 2 times" in result.error and text == "- milk\n- eggs\n- milk\n"
    return "no match"


# ── grep and glob: regex and ignore_case ─────────────────────────────────────────────────────


async def _grep_regex(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> str:
    (scratch / "notes.md").write_text("version a.c is out\nabc is the plan\n", encoding="utf-8")
    result = await NativeBuiltinToolProvider(scratch).invoke(
        "grep", {"query": "a.c", "regex": sent}
    )
    assert result.success, result.error
    assert "version a.c is out" in result.output
    return "a regular expression" if "abc is the plan" in result.output else "the text"


_PLAN = "Dentist at four\nfill in the dentist forms\n"


async def _grep_case(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> str:
    (scratch / "plan.md").write_text(_PLAN, "utf-8")
    result = await NativeBuiltinToolProvider(scratch).invoke(
        "grep", {"query": "dentist", "ignore_case": sent}
    )
    if not result.success:
        assert "ignore_case must be true or false" in result.error
        return REFUSED
    assert "dentist forms" in result.output
    return "any case" if "Dentist at four" in result.output else "the case as sent"


async def _glob_case(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> str:
    (scratch / "Plan.md").write_text(_PLAN, "utf-8")
    result = await NativeBuiltinToolProvider(scratch).invoke(
        "glob", {"pattern": "plan.md", "ignore_case": sent}
    )
    if not result.success:
        assert "ignore_case must be true or false" in result.error
        return REFUSED
    return "any case" if "Plan.md" in result.output else "the case as sent"


# ── knowledge_update: is_pinned and is_archived ──────────────────────────────────────────────


def _library(scratch: Path, mp: pytest.MonkeyPatch):
    import personalclaw.knowledge as knowledge_pkg
    from personalclaw.agents.native import builtin_tools
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(str(scratch / "k.db"))
    mp.setattr(knowledge_pkg, "get_knowledge_store", lambda *a, **k: store)
    mp.setattr(builtin_tools, "_enrich_in_background", lambda *a, **k: None)
    return store


def _knowledge_switch(switch: str):
    async def drive(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> object:
        """The switch as stored after the call, or refused with nothing of the call applied."""
        library = _library(scratch, mp)
        item = library.create_typed_item(item_type="note", title="Soup", content="leek and potato")
        start = int(_starts_on(sent))
        library.update_item(item, **{switch: start})
        # A value to refuse comes with an edit beside it, so a refusal shows nothing was applied.
        edit = {"title": "Leek soup"} if spells(sent) is None else {}
        result = await NativeBuiltinToolProvider().invoke(
            "knowledge_update", {"id": item, **edit, switch: sent}
        )
        stored = library.get_item(item)
        if not result.success:
            assert f"{switch} is true or false" in result.error
            assert (stored["title"], stored[switch]) == ("Soup", start), "the call was applied"
            return REFUSED
        return bool(stored[switch])

    return drive


# ── code_map: refresh ────────────────────────────────────────────────────────────────────────


async def _code_map(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> str:
    """Whether ``code_map`` rebuilt an index that already exists."""
    from personalclaw import codegraph
    from personalclaw.agents.native.builtin_tools import bind_tool_context, reset_tool_context
    from personalclaw.tool_providers.code_map import _open_index

    built: list[str] = []

    class _Index:
        def __init__(self, workspace: str) -> None:
            self._workspace = workspace

        def is_empty(self) -> bool:
            return False

        def index(self):
            built.append(self._workspace)

    mp.setattr(codegraph, "CodeGraphIndex", _Index)
    tokens = bind_tool_context(cwd=scratch)
    try:
        index, failure = _open_index({"symbol": "checkout", "refresh": sent})
    finally:
        reset_tool_context(tokens)
    assert failure is None and index is not None
    return "rebuilt" if built == [str(scratch.resolve())] else "kept"


# ── in-process tools: the field spec refuses text, and a real boolean is itself ────────────────


def _workflow_tool(tool: str, flag: str):
    async def drive(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> object:
        """What the workflow service was asked with, or refused before it was asked anything."""
        from personalclaw.agents.native.tools import InProcessMcpToolProvider
        from personalclaw.workflows import service

        asked: list[dict] = []

        def _record(run_id, *args, **kwargs):
            asked.append(kwargs)
            return {"ok": True, "run_id": run_id}

        mp.setattr(service, "rewind_run", _record)
        mp.setattr(service, "resume_run", _record)
        tools = InProcessMcpToolProvider(
            module="personalclaw.mcp_workflows",
            provider_name="personalclaw-workflows",
            display="PersonalClaw Workflows",
        )
        args = {"node_id": "publish"} if tool == "workflow_rewind" else {}
        result = await tools.invoke(tool, {"run_id": "a1b2c3d4", **args, flag: sent})
        if not result.success:
            assert f"{flag}: expected bool" in (result.error or "") and asked == []
            return REFUSED
        [kwargs] = asked
        return kwargs[flag]

    return drive


# ── an automation's switches: the agent's patch and the control bridge ─────────────────────────


def _automation(scratch: Path, mp: pytest.MonkeyPatch) -> str:
    """A switched-on automation the owner made, in a store under a scratch home."""
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.store import TriggerStore

    mp.setenv("PERSONALCLAW_HOME", str(scratch))
    mp.setattr("personalclaw.config.loader.config_dir", lambda: scratch)
    made = T.create(
        TriggerStore(base_dir=scratch),
        name="Nightly backup reminder",
        kind="clock",
        spec={"kind": "cron", "expr": "0 3 * * *"},
        workflow={"inline": {"provider": "notify", "config": {"title": "Back up", "body": "now"}}},
        created_by="user",
        owner_consented=True,
    )
    assert made.ok, made.text
    return made.data["trigger"]["id"]


def _stored(home: Path, trigger_id: str):
    """The row as a fresh read of the store sees it, so the stored value is what is checked."""
    from personalclaw.triggers.store import TriggerStore

    loaded = TriggerStore(base_dir=home).get(trigger_id)
    assert loaded is not None
    return loaded.trigger


def _patch_switch(switch: str, start: bool | None = None):
    """*start* is where the switch starts; left out, the other side of what each row sends."""

    async def drive(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> object:
        """The switch as stored after the patch, or refused with nothing of the patch applied."""
        from personalclaw.triggers import tools as T
        from personalclaw.triggers.store import TriggerStore

        tid = _automation(scratch, mp)
        store = TriggerStore(base_dir=scratch)
        begin = _starts_on(sent) if start is None else start
        if switch == "enabled" and not begin:
            assert T.set_paused(store, trigger_id=tid, paused=True).ok
        if switch == "yield_to_user" and begin:
            assert T.update(store, trigger_id=tid, patch={switch: True}).ok
        assert getattr(_stored(scratch, tid), switch) is begin
        result = T.update(store, trigger_id=tid, patch={"name": "Renamed", switch: sent})
        stored = _stored(scratch, tid)
        if not result.ok:
            assert f"{switch} is true or false" in result.text
            assert stored.name == "Nightly backup reminder", "the patch was applied"
            assert getattr(stored, switch) is begin
            return REFUSED
        return getattr(stored, switch)

    return drive


async def _bridge(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> object:
    """The automation as stored after the bridge toggled it, or refused and still as it was."""
    from personalclaw.inbound import bridge
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.store import TriggerStore

    tid = _automation(scratch, mp)
    start = _starts_on(sent)
    if not start:
        assert T.set_paused(TriggerStore(base_dir=scratch), trigger_id=tid, paused=True).ok
    try:
        answer = await bridge._toggle_automation(object(), {"id": tid, "enabled": sent})
    except ValueError as refused:
        assert "enabled is true or false" in str(refused)
        assert _stored(scratch, tid).enabled is start
        return REFUSED
    assert answer == {"id": tid, "enabled": _stored(scratch, tid).enabled}
    return answer["enabled"]


# ── an agent's question card: multiSelect ────────────────────────────────────────────────────


async def _question_card(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> str:
    from personalclaw.validation import validate_ask_user_question

    [card] = validate_ask_user_question(
        {"questions": [{"question": "Which days?", "options": ["Mon", "Tue"], "multiSelect": sent}]}
    )
    return "several answers" if card["multiSelect"] is True else "one answer"


# ── a bulk pin or favourite: value ───────────────────────────────────────────────────────────


def _bulk(op: str, column: str):
    async def drive(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> object:
        """The switch after a bulk call, or refused and still as it was."""
        from personalclaw.knowledge.store import KnowledgeStore

        shelf = KnowledgeStore(scratch / "k.db")
        item = shelf.create_typed_item(item_type="note", title="Soup", content="leek")
        start = _starts_on(sent)
        if start:
            shelf.bulk_apply(op, [item], value=True)
        try:
            done = shelf.bulk_apply(op, [item], value=sent)
        except ValueError as refused:
            assert f"{op} requires value true or false" in str(refused)
            assert bool(shelf.get_item(item)[column]) is start
            return REFUSED
        assert done["changed"] == [item], done
        return bool(shelf.get_item(item)[column])

    return drive


# ── reset_tools: one boolean per tool group ──────────────────────────────────────────────────


async def _reset_tools(scratch: Path, mp: pytest.MonkeyPatch, sent: object) -> str:
    from test_tool_boolean_census import _runtime

    runtime = await _runtime(scratch)
    start = _starts_on(sent)
    runtime._reset_tools({"groups": {"schedule": start}})
    assert ("schedule" in runtime._active_groups) is start
    runtime._reset_tools({"groups": {"schedule": sent}})
    return "on" if "schedule" in runtime._active_groups else "off"


class Door(NamedTuple):
    """A tool door, how it is driven, and what it does with each answer."""

    name: str
    drive: Callable[[Path, pytest.MonkeyPatch, object], Awaitable[object]]
    yes: object
    no: object
    neither: object
    #: Whether the door takes words, or only a JSON boolean (an in-process field spec).
    words: bool = True
    #: What a door that takes words is sent for neither.
    spells_neither: tuple[object, ...] = WORD_NEITHER


#: Every door the defect reached, each with what a yes, a no and a value that spells neither does.
DOORS = [
    Door("edit_file.replace_all", _edit, "every match", "no match", "no match"),
    Door("grep.regex", _grep_regex, "a regular expression", "the text", "the text"),
    # A blank ignore_case is the field left out: smart case.
    *[
        Door(name, drive, "any case", "the case as sent", REFUSED, True, WORD_NEITHER_NOT_BLANK)
        for name, drive in (("grep.ignore_case", _grep_case), ("glob.ignore_case", _glob_case))
    ],
    Door("knowledge_update.is_archived", _knowledge_switch("is_archived"), True, False, REFUSED),
    Door("knowledge_update.is_pinned", _knowledge_switch("is_pinned"), True, False, REFUSED),
    Door("code_map.refresh", _code_map, "rebuilt", "kept", "kept"),
    *[
        Door(f"{tool}.{flag}", _workflow_tool(tool, flag), True, False, REFUSED, words=False)
        for tool, flag in (
            ("workflow_rewind", "redo_effects"),
            ("workflow_rewind", "force"),
            ("workflow_resume", "always_allow"),
        )
    ],
    Door("automation_update.enabled, on", _patch_switch("enabled", True), True, False, REFUSED),
    Door("automation_update.enabled, off", _patch_switch("enabled", False), True, False, REFUSED),
    Door("automation_update.yield_to_user", _patch_switch("yield_to_user"), True, False, REFUSED),
    Door("toggle_automation.enabled", _bridge, True, False, REFUSED),
    Door(
        "ask_user_question.multiSelect",
        _question_card,
        "several answers",
        "one answer",
        "one answer",
    ),
    Door("knowledge bulk pin.value", _bulk("pin", "is_pinned"), True, False, REFUSED),
    Door("knowledge bulk favorite.value", _bulk("favorite", "favorited"), True, False, REFUSED),
    Door("reset_tools.groups.*", _reset_tools, "on", "off", "off"),
]


def _sent(door: Door, answer: str) -> tuple[object, ...]:
    """What *door* is sent for *answer*. A door that reads words is sent the word and, for a yes
    or a no, the real boolean too: a real boolean is still itself there."""
    words, json = {
        "yes": ((WORD_YES,), JSON_YES),
        "no": ((WORD_NO,), JSON_NO),
        "neither": (door.spells_neither, JSON_REFUSED),
    }[answer]
    if not door.words:
        return (json,)
    return words if answer == "neither" else (*words, json)


async def _answers(subtests, tmp_path: Path, answer: str) -> None:
    assert len(DOORS) == 18, "a door was dropped from the table"
    assert len(DOORS) == len({door.name for door in DOORS}), "two doors share a name"
    for at, door in enumerate(DOORS):
        for value in _sent(door, answer):
            with row(subtests, door=door.name, sent=repr(value)):
                scratch = tmp_path / f"door-{at}-{value!r}"
                scratch.mkdir()
                with pytest.MonkeyPatch.context() as mp:
                    read_gate.reset_all()
                    got = await door.drive(scratch, mp, value)
                expected = getattr(door, answer)
                assert got == expected, f"{door.name} sent {value!r} did {got!r}, not {expected!r}"


@pytest.mark.asyncio
async def test_every_tool_door_does_what_a_yes_says(subtests, tmp_path):
    await _answers(subtests, tmp_path, "yes")


@pytest.mark.asyncio
async def test_every_tool_door_does_what_a_no_says(subtests, tmp_path):
    await _answers(subtests, tmp_path, "no")


@pytest.mark.asyncio
async def test_every_tool_door_reads_what_spells_neither_as_its_safe_value_or_refuses_it(
    subtests, tmp_path
):
    await _answers(subtests, tmp_path, "neither")


# ── what a door does when the boolean is left out ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_bridge_left_without_enabled_switches_it_over(tmp_path, monkeypatch):
    from personalclaw.inbound import bridge

    tid = _automation(tmp_path, monkeypatch)
    assert await bridge._toggle_automation(object(), {"id": tid}) == {"id": tid, "enabled": False}
    assert await bridge._toggle_automation(object(), {"id": tid}) == {"id": tid, "enabled": True}


def test_a_bulk_switch_left_without_a_value_switches_it_on(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    shelf = KnowledgeStore(tmp_path / "k.db")
    item = shelf.create_typed_item(item_type="note", title="Soup", content="leek")
    assert shelf.bulk_apply("pin", [item])["changed"] == [item]
