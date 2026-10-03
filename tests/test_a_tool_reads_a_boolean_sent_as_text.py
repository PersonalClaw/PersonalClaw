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
  switched off kept running, and a stored row read the text back as on;
* the control bridge switched an automation ON when the owner confirmed ``"enabled": "false"``;
* an agent's question card sent ``"multiSelect": "false"`` became a multiple-choice card;
* a bulk unpin sent ``"value": "false"`` pinned the items.

Each is read through ``safety_flags.yes_or_no``: a no is a no, a yes a yes, and a value that
spells neither is the field's safe value or, where there is none, refused. An in-process tool's
field spec refuses a text boolean before its handler runs, which is pinned here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.agents.native import read_gate
from personalclaw.agents.native.builtin_tools import NativeBuiltinToolProvider

#: The ways a caller says no, as a boolean and as text in any case.
NO = [False, "false", "False", "no", " NO ", "0", "off"]
#: The ways a caller says yes.
YES = [True, "true", "TRUE", "yes", " on ", "1"]
#: What says neither: a blank, another word, a number standing in for a boolean.
NEITHER = ["", "maybe", 1]


@pytest.fixture(autouse=True)
def _fresh_read_ledger():
    read_gate.reset_all()
    yield
    read_gate.reset_all()


# ── edit_file: replace_all ───────────────────────────────────────────────────────────────────


@pytest.fixture
def groceries(tmp_path):
    (tmp_path / "list.md").write_text("- milk\n- eggs\n- milk\n", encoding="utf-8")
    return tmp_path


async def _replace(root: Path, replace_all):
    tools = NativeBuiltinToolProvider(root, session_key="boolean-edit")
    assert (await tools.invoke("read_file", {"path": "list.md"})).success
    return await tools.invoke(
        "edit_file",
        {"path": "list.md", "old_str": "milk", "new_str": "oat milk", "replace_all": replace_all},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", NO + NEITHER)
async def test_an_edit_not_sent_a_yes_changes_no_match_of_two(groceries, sent):
    result = await _replace(groceries, sent)
    assert not result.success and "matched 2 times" in result.error
    assert (groceries / "list.md").read_text() == "- milk\n- eggs\n- milk\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", YES)
async def test_an_edit_sent_a_yes_replaces_every_match(groceries, sent):
    result = await _replace(groceries, sent)
    assert result.success, result.error
    assert (groceries / "list.md").read_text() == "- oat milk\n- eggs\n- oat milk\n"


# ── grep: regex ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def notes(tmp_path):
    (tmp_path / "notes.md").write_text("version a.c is out\nabc is the plan\n", encoding="utf-8")
    return tmp_path


async def _grep(root: Path, **args) -> str:
    result = await NativeBuiltinToolProvider(root).invoke("grep", args)
    assert result.success, result.error
    return result.output


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", NO + NEITHER)
async def test_grep_not_sent_a_yes_searches_for_the_text(notes, sent):
    out = await _grep(notes, query="a.c", regex=sent)
    assert "version a.c is out" in out and "abc is the plan" not in out


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", YES)
async def test_grep_sent_a_yes_searches_for_a_regular_expression(notes, sent):
    out = await _grep(notes, query="a.c", regex=sent)
    assert "version a.c is out" in out and "abc is the plan" in out


# ── glob and grep: ignore_case, read by the same reader ──────────────────────────────────────


@pytest.fixture
def appointments(tmp_path):
    (tmp_path / "plan.md").write_text("Dentist at four\nfill in the dentist forms\n", "utf-8")
    return tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", NO)
async def test_ignore_case_sent_a_no_matches_the_case_exactly(appointments, sent):
    out = await _grep(appointments, query="dentist", ignore_case=sent)
    assert "dentist forms" in out and "Dentist at four" not in out


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", YES)
async def test_ignore_case_sent_a_yes_ignores_a_capital(appointments, sent):
    out = await _grep(appointments, query="DENTIST", ignore_case=sent)
    assert "dentist forms" in out and "Dentist at four" in out


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, args", [("grep", {"query": "dentist"}), ("glob", {"pattern": "*"})])
@pytest.mark.parametrize("sent", ["sometimes", 1, 0])
async def test_an_ignore_case_that_spells_neither_is_refused(appointments, tool, args, sent):
    result = await NativeBuiltinToolProvider(appointments).invoke(
        tool, {**args, "ignore_case": sent}
    )
    assert not result.success and "ignore_case must be true or false" in result.error


# ── knowledge_update: is_pinned and is_archived ──────────────────────────────────────────────


@pytest.fixture
def library(tmp_path, monkeypatch):
    import personalclaw.knowledge as knowledge_pkg
    from personalclaw.agents.native import builtin_tools
    from personalclaw.knowledge.store import KnowledgeStore

    store = KnowledgeStore(str(tmp_path / "k.db"))
    monkeypatch.setattr(knowledge_pkg, "get_knowledge_store", lambda *a, **k: store)
    monkeypatch.setattr(builtin_tools, "_enrich_in_background", lambda *a, **k: None)
    return store


def _note(library, **switches) -> str:
    item_id = library.create_typed_item(item_type="note", title="Soup", content="leek and potato")
    if switches:
        library.update_item(item_id, **switches)
    return item_id


SWITCHES = ["is_archived", "is_pinned"]


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize("sent", NO)
async def test_a_knowledge_switch_sent_a_no_is_off(library, switch, sent):
    item_id = _note(library, **{switch: 1})
    result = await NativeBuiltinToolProvider().invoke(
        "knowledge_update", {"id": item_id, switch: sent}
    )
    assert result.success, result.error
    assert library.get_item(item_id)[switch] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize("sent", YES)
async def test_a_knowledge_switch_sent_a_yes_is_on(library, switch, sent):
    item_id = _note(library)
    result = await NativeBuiltinToolProvider().invoke(
        "knowledge_update", {"id": item_id, switch: sent}
    )
    assert result.success, result.error
    assert library.get_item(item_id)[switch] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", SWITCHES)
@pytest.mark.parametrize("sent", NEITHER)
async def test_a_knowledge_switch_that_spells_neither_is_refused(library, switch, sent):
    item_id = _note(library, **{switch: 1})
    result = await NativeBuiltinToolProvider().invoke(
        "knowledge_update", {"id": item_id, "title": "Leek soup", switch: sent}
    )
    assert not result.success and f"{switch} is true or false" in result.error
    stored = library.get_item(item_id)
    assert (stored["title"], stored[switch]) == ("Soup", 1), "nothing of the call was applied"


# ── code_map: refresh ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def rebuilt(tmp_path, monkeypatch):
    """The workspaces ``code_map`` rebuilt an index for, over an index that already exists."""
    from personalclaw import codegraph

    built: list[str] = []

    class _Index:
        def __init__(self, workspace: str) -> None:
            self._workspace = workspace

        def is_empty(self) -> bool:
            return False

        def index(self):
            built.append(self._workspace)

    monkeypatch.setattr(codegraph, "CodeGraphIndex", _Index)
    return built


def _open(tmp_path: Path, refresh):
    from personalclaw.agents.native.builtin_tools import bind_tool_context, reset_tool_context
    from personalclaw.tool_providers.code_map import _open_index

    tokens = bind_tool_context(cwd=tmp_path)
    try:
        return _open_index({"symbol": "checkout", "refresh": refresh})
    finally:
        reset_tool_context(tokens)


@pytest.mark.parametrize("sent", NO + NEITHER)
def test_code_map_not_sent_a_yes_reads_the_index_it_has(tmp_path, rebuilt, sent):
    index, failure = _open(tmp_path, sent)
    assert failure is None and index is not None
    assert rebuilt == []


@pytest.mark.parametrize("sent", YES)
def test_code_map_sent_a_yes_rebuilds_it(tmp_path, rebuilt, sent):
    index, failure = _open(tmp_path, sent)
    assert failure is None and rebuilt == [str(tmp_path.resolve())]


# ── in-process tools: the field spec refuses text, and a real boolean is itself ────────────────


@pytest.fixture
def asked(monkeypatch):
    """What the workflow service was asked to do, instead of doing it."""
    from personalclaw.workflows import service

    calls: list[tuple[str, dict]] = []

    def _rewind(run_id, node_id, **kwargs):
        calls.append(("rewind", kwargs))
        return {"ok": True, "run_id": run_id}

    def _resume(run_id, **kwargs):
        calls.append(("resume", kwargs))
        return {"ok": True, "run_id": run_id}

    monkeypatch.setattr(service, "rewind_run", _rewind)
    monkeypatch.setattr(service, "resume_run", _resume)
    return calls


async def _workflow_tool(name: str, **args):
    from personalclaw.agents.native.tools import InProcessMcpToolProvider

    tools = InProcessMcpToolProvider(
        module="personalclaw.mcp_workflows",
        provider_name="personalclaw-workflows",
        display="PersonalClaw Workflows",
    )
    return await tools.invoke(name, {"run_id": "a1b2c3d4", **args})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, flag",
    [("workflow_rewind", "redo_effects"), ("workflow_rewind", "force")]
    + [("workflow_resume", "always_allow")],
)
@pytest.mark.parametrize("sent", ["false", "no", "0", "", "true", "yes"])
async def test_a_workflow_tool_sent_a_flag_as_text_runs_nothing(asked, tool, flag, sent):
    """The field spec refuses it before the call runs, naming the field, so it is never read."""
    args = {"node_id": "publish"} if tool == "workflow_rewind" else {}
    result = await _workflow_tool(tool, **args, **{flag: sent})
    assert not result.success and f"{flag}: expected bool" in (result.error or "")
    assert asked == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, flag",
    [("workflow_rewind", "redo_effects"), ("workflow_rewind", "force")]
    + [("workflow_resume", "always_allow")],
)
async def test_a_workflow_tool_reads_a_real_boolean_as_itself(asked, tool, flag):
    args = {"node_id": "publish"} if tool == "workflow_rewind" else {}
    for sent in (False, True):
        assert (await _workflow_tool(tool, **args, **{flag: sent})).success
    assert [kwargs[flag] for _, kwargs in asked] == [False, True]


# ── an automation's switches: the agent's patch, the stored row, the control bridge ────────────


@pytest.fixture
def automation(tmp_path, monkeypatch):
    """A switched-on automation the owner made, in a store under a scratch home."""
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.store import TriggerStore

    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    made = T.create(
        TriggerStore(base_dir=tmp_path),
        name="Nightly backup reminder",
        kind="clock",
        spec={"kind": "cron", "expr": "0 3 * * *"},
        workflow={"inline": {"provider": "notify", "config": {"title": "Back up", "body": "now"}}},
        created_by="user",
        owner_consented=True,
    )
    assert made.ok, made.text
    return tmp_path, made.data["trigger"]["id"]


def _stored(home: Path, trigger_id: str):
    """The row as a fresh read of the store sees it, so the stored value is what is checked."""
    from personalclaw.triggers.store import TriggerStore

    loaded = TriggerStore(base_dir=home).get(trigger_id)
    assert loaded is not None
    return loaded.trigger


def _patch(home: Path, trigger_id: str, patch: dict):
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.store import TriggerStore

    return T.update(TriggerStore(base_dir=home), trigger_id=trigger_id, patch=patch)


def _switch_off(home: Path, trigger_id: str) -> None:
    from personalclaw.triggers import tools as T
    from personalclaw.triggers.store import TriggerStore

    assert T.set_paused(TriggerStore(base_dir=home), trigger_id=trigger_id, paused=True).ok


@pytest.mark.parametrize("sent", NO)
def test_a_patch_that_switches_an_automation_off_switches_it_off(automation, sent):
    home, tid = automation
    result = _patch(home, tid, {"enabled": sent})
    assert result.ok, result.text
    assert _stored(home, tid).enabled is False


@pytest.mark.parametrize("sent", YES)
def test_a_patch_that_switches_an_automation_on_switches_it_on(automation, sent):
    home, tid = automation
    _switch_off(home, tid)
    result = _patch(home, tid, {"enabled": sent})
    assert result.ok, result.text
    assert _stored(home, tid).enabled is True


@pytest.mark.parametrize("switch", ["enabled", "yield_to_user"])
@pytest.mark.parametrize("sent", NEITHER)
def test_a_patch_switch_that_spells_neither_changes_nothing(automation, switch, sent):
    home, tid = automation
    result = _patch(home, tid, {"name": "Renamed", switch: sent})
    assert not result.ok and f"{switch} is true or false" in result.text
    stored = _stored(home, tid)
    assert (stored.name, stored.enabled, stored.yield_to_user) == (
        "Nightly backup reminder",
        True,
        False,
    )


@pytest.mark.parametrize("sent, yields", [(s, False) for s in NO] + [(s, True) for s in YES])
def test_a_patch_sets_yield_to_user_to_the_word_it_spells(automation, sent, yields):
    home, tid = automation
    assert _patch(home, tid, {"yield_to_user": sent}).ok
    assert _stored(home, tid).yield_to_user is yields


@pytest.mark.parametrize(
    "stored, on",
    [("false", False), ("no", False), ("0", False), ("", False), ("maybe", False)]
    + [(False, False), ("true", True), ("yes", True), (True, True), (1, True)],
)
def test_a_stored_switch_is_read_as_the_word_it_spells(stored, on):
    """A row an agent's patch already wrote as text reads as what it said; one nothing can read
    is off, because a switch that runs things on its own must not come on by a guess."""
    from personalclaw.triggers.models import parse_trigger

    row = {
        "id": "clock:backup",
        "name": "Nightly backup reminder",
        "kind": "clock",
        "spec": {"kind": "cron", "expr": "0 3 * * *"},
        "enabled": stored,
        "yield_to_user": stored,
    }
    trigger, _ = parse_trigger(row)
    assert (trigger.enabled, trigger.yield_to_user) == (on, on)


def test_a_row_that_never_said_is_on_and_does_not_yield():
    from personalclaw.triggers.models import parse_trigger

    trigger, _ = parse_trigger(
        {
            "id": "clock:b",
            "name": "B",
            "kind": "clock",
            "spec": {"kind": "cron", "expr": "0 3 * * *"},
        }
    )
    assert (trigger.enabled, trigger.yield_to_user) == (True, False)


async def _bridge_toggle(trigger_id: str, **params):
    from personalclaw.inbound import bridge

    return await bridge._toggle_automation(object(), {"id": trigger_id, **params})


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", NO)
async def test_the_bridge_switches_an_automation_off_on_a_no(automation, sent):
    home, tid = automation
    assert await _bridge_toggle(tid, enabled=sent) == {"id": tid, "enabled": False}
    assert _stored(home, tid).enabled is False


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", YES)
async def test_the_bridge_switches_an_automation_on_on_a_yes(automation, sent):
    home, tid = automation
    _switch_off(home, tid)
    assert await _bridge_toggle(tid, enabled=sent) == {"id": tid, "enabled": True}
    assert _stored(home, tid).enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize("sent", NEITHER)
async def test_the_bridge_refuses_an_enabled_that_spells_neither(automation, sent):
    home, tid = automation
    with pytest.raises(ValueError, match="enabled is true or false"):
        await _bridge_toggle(tid, enabled=sent)
    assert _stored(home, tid).enabled is True


@pytest.mark.asyncio
async def test_the_bridge_left_without_enabled_switches_it_over(automation):
    home, tid = automation
    assert await _bridge_toggle(tid) == {"id": tid, "enabled": False}
    assert await _bridge_toggle(tid) == {"id": tid, "enabled": True}


# ── an agent's question card: multiSelect ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "sent, multiple", [(s, False) for s in NO + NEITHER] + [(s, True) for s in YES]
)
def test_a_question_card_takes_several_answers_only_on_a_yes(sent, multiple):
    from personalclaw.validation import validate_ask_user_question

    [card] = validate_ask_user_question(
        {"questions": [{"question": "Which days?", "options": ["Mon", "Tue"], "multiSelect": sent}]}
    )
    assert card["multiSelect"] is multiple


# ── a bulk pin or favourite: value ───────────────────────────────────────────────────────────


@pytest.fixture
def shelf(tmp_path):
    from personalclaw.knowledge.store import KnowledgeStore

    return KnowledgeStore(tmp_path / "k.db")


OPS = [("pin", "is_pinned"), ("favorite", "favorited")]


@pytest.mark.parametrize("op, column", OPS)
@pytest.mark.parametrize("sent", NO)
def test_a_bulk_switch_sent_a_no_switches_it_off(shelf, op, column, sent):
    item = shelf.create_typed_item(item_type="note", title="Soup", content="leek")
    shelf.bulk_apply(op, [item], value=True)
    assert shelf.bulk_apply(op, [item], value=sent)["changed"] == [item]
    assert not shelf.get_item(item)[column]


@pytest.mark.parametrize("op, column", OPS)
@pytest.mark.parametrize("sent", YES)
def test_a_bulk_switch_sent_a_yes_switches_it_on(shelf, op, column, sent):
    item = shelf.create_typed_item(item_type="note", title="Soup", content="leek")
    assert shelf.bulk_apply(op, [item], value=sent)["changed"] == [item]
    assert shelf.get_item(item)[column]


@pytest.mark.parametrize("op, column", OPS)
@pytest.mark.parametrize("sent", NEITHER)
def test_a_bulk_switch_that_spells_neither_is_refused(shelf, op, column, sent):
    item = shelf.create_typed_item(item_type="note", title="Soup", content="leek")
    with pytest.raises(ValueError, match=f"{op} requires value true or false"):
        shelf.bulk_apply(op, [item], value=sent)
    assert not shelf.get_item(item)[column]


def test_a_bulk_switch_left_without_a_value_switches_it_on(shelf):
    item = shelf.create_typed_item(item_type="note", title="Soup", content="leek")
    assert shelf.bulk_apply("pin", [item])["changed"] == [item]
