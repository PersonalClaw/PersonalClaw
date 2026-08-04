"""`automation_delete_all` — the scoped bulk delete (S109).

Replaces `test_cron_session_scope.py`, which tested `schedule_remove_all` — the one capability the
retired `schedule_*` aliases had that `automation_*` did not. That alias was not just a convenience:
it enforced a real access control (`jobs = [j for j in jobs if j.session_key == session_key]`), so
retiring it without carrying the scope forward would either lose the bulk operation or leave a
future author to re-add it unscoped.

**The scope changed from `session_key` to `created_by`, deliberately.** Measured before porting:
`mcp_schedule` SET `job.session_key` on add, but a row created through `tools.create` carries
`session="fresh"` (the default) and `created_by="agent"` — so a session-keyed filter would match
NOTHING for exactly the rows an agent can create. It would look identical in a diff and enforce
nothing, which is the defect class this program keeps finding. `created_by` is the ownership the
store actually records.

The two old env-var tests (`PERSONALCLAW_SESSION_KEY` capture, `PERSONALCLAW_CLI=1` admin escape)
are NOT ported: they describe a mechanism that no longer exists. The MCP dispatcher hard-codes
`created_by="agent"` rather than reading it from args, which is a stronger boundary than an env var
an agent's own process could set — asserted below.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from personalclaw.triggers import tools as T
from personalclaw.triggers.models import Trigger
from personalclaw.triggers.store import TriggerStore


@pytest.fixture
def store(tmp_path):
    return TriggerStore(base_dir=tmp_path)


def _make(store, trigger_id, *, created_by):
    store.upsert(
        Trigger(
            id=trigger_id,
            name=trigger_id,
            kind="clock",
            created_by=created_by,
            spec={"kind": "interval", "interval_secs": 600},
        )
    )


def _ids(store):
    return {row.trigger.id for row in store.load()}


# ── the access control ──


def test_it_deletes_only_the_rows_the_caller_created(store):
    """🔴 THE CONTROL, ported from `test_removes_only_own_session_jobs`. An agent must not be able
    to mass-delete the automations the USER built."""
    _make(store, "clock:agent-one", created_by="agent")
    _make(store, "clock:agent-two", created_by="agent")
    _make(store, "clock:mine", created_by="user")
    _make(store, "app:some-app:job", created_by="app:some-app")

    result = T.delete_all(store, created_by="agent", confirm=True)

    assert result.ok
    assert _ids(store) == {"clock:mine", "app:some-app:job"}
    assert set(result.data["deleted"]) == {"clock:agent-one", "clock:agent-two"}


def test_the_mcp_dispatcher_hard_codes_the_scope(store):
    """🔴 `created_by` is NOT an argument. An agent able to pass `created_by="user"` could delete
    every automation the human made — so the dispatcher supplies the caller's identity, and the
    validation schema deliberately has no field for it.

    Source-level plus a schema assertion, because the boundary IS the absence of a parameter: there
    is no call that could demonstrate it at runtime.
    """
    import inspect

    from personalclaw import mcp_automation
    from personalclaw.validation import MCP_AUTOMATION_SCHEMAS

    src = inspect.getsource(mcp_automation._call_tool_inner)
    assert 'T.delete_all(store, created_by="agent"' in src
    assert "created_by=str(args" not in src
    fields = {f.name for f in MCP_AUTOMATION_SCHEMAS["automation_delete_all"].fields}
    assert fields == {"confirm"}, f"the scope must not be caller-settable: {fields}"


def test_a_different_scope_leaves_the_agents_rows_alone(store):
    """The scope cuts both ways — asking for `user` must not sweep the agent's."""
    _make(store, "clock:agent-one", created_by="agent")
    _make(store, "clock:mine", created_by="user")

    T.delete_all(store, created_by="user", confirm=True)

    assert _ids(store) == {"clock:agent-one"}


# ── the confirm gate ──


def test_it_refuses_without_confirm(store):
    """Ported in spirit from single `delete`'s gate, and it matters more here: this is the most
    destructive tool in the namespace."""
    _make(store, "clock:agent-one", created_by="agent")

    result = T.delete_all(store, created_by="agent")

    assert not result.ok
    assert "confirm: true" in result.text
    assert _ids(store) == {"clock:agent-one"}, "nothing may be deleted on a refusal"


def test_the_mcp_schema_requires_confirm():
    """A required flag the tool schema does not mark required is a gate a model will skip."""
    from personalclaw import mcp_automation

    tool = next(t for t in mcp_automation._list_tools() if t["name"] == "automation_delete_all")
    assert tool["inputSchema"]["required"] == ["confirm"]
    # And the description must state the blast radius: a bulk-delete tool whose scope is only
    # discoverable by reading the implementation is one an agent will misuse.
    assert "YOU created" in tool["description"]


# ── empty + partial outcomes ──


def test_an_empty_scope_reports_that_it_deleted_nothing(store):
    """Ported from `test_no_matching_jobs_returns_message`. "Removed 0" beside an untouched list is
    how a caller learns its scope was wrong instead of assuming the work is done."""
    _make(store, "clock:mine", created_by="user")

    result = T.delete_all(store, created_by="agent", confirm=True)

    assert result.ok
    assert "No agent-created automations" in result.text
    assert result.data["deleted"] == []
    assert _ids(store) == {"clock:mine"}


def test_deleting_twice_is_idempotent(store):
    _make(store, "clock:agent-one", created_by="agent")

    first = T.delete_all(store, created_by="agent", confirm=True)
    second = T.delete_all(store, created_by="agent", confirm=True)

    assert len(first.data["deleted"]) == 1
    assert second.ok and second.data["deleted"] == []


def test_a_partial_failure_is_reported_not_swallowed(tmp_path):
    """A bulk delete that claimed full success while rows it could not remove kept firing would be
    the worst possible outcome — the caller believes the list is empty."""
    real = TriggerStore(base_dir=tmp_path)
    _make(real, "clock:agent-one", created_by="agent")
    _make(real, "clock:agent-two", created_by="agent")

    flaky = MagicMock()
    flaky.load.side_effect = real.load
    calls: list[str] = []

    def _delete(trigger_id):
        calls.append(trigger_id)
        if trigger_id == "clock:agent-two":
            raise OSError("locked")
        return real.delete(trigger_id)

    flaky.delete.side_effect = _delete

    result = T.delete_all(flaky, created_by="agent", confirm=True)

    assert result.ok  # the half that worked DID work
    assert result.data["deleted"] == ["clock:agent-one"]
    assert "1 could not be deleted" in result.text
    assert len(calls) == 2, "a failure must not strand the remaining rows"


# ── the retirement itself ──


def test_the_legacy_alias_surface_is_gone():
    """🔴 The clean break. Nine aliases, one module, one bundled tools app, four validation schemas,
    and the `@personalclaw-schedule` grant in `defaults.json`.

    Pinned because a re-added alias would write `crons.json` — a file the clock engine does not read
    (S108) — so the automation would report success and never fire.
    """
    import json
    import pathlib

    import personalclaw

    with pytest.raises(ModuleNotFoundError):
        __import__("personalclaw.mcp_schedule")

    import personalclaw.mcp_core as mc

    names = [t["name"] for t in mc._aggregated_list_tools()]
    assert not [n for n in names if n.startswith("schedule_")]

    import personalclaw.validation as v

    assert not hasattr(v, "MCP_SCHEDULE_SCHEMAS")

    from personalclaw.agent import _MANAGED_MCP_SERVERS
    from personalclaw.mcp_discovery import _MANAGED_SERVER_NAMES

    assert "personalclaw-schedule" not in _MANAGED_MCP_SERVERS
    assert "personalclaw-schedule" not in _MANAGED_SERVER_NAMES

    root = pathlib.Path(personalclaw.__file__).parent
    defaults = json.loads((root / "config" / "defaults.json").read_text())
    assert "@personalclaw-schedule" not in defaults["tools"]
    assert "@personalclaw-schedule" not in defaults["allowedTools"]
    assert not (root / "apps" / "native" / "personalclaw-schedule-tools").exists()


def test_the_shipped_prompts_do_not_name_a_retired_tool():
    """🔴 The highest-impact reference: a prompt that tells the model to call `schedule_add` teaches
    it to invoke a tool that no longer exists, and the failure surfaces as the assistant apologizing
    rather than as anything a developer would see."""
    import pathlib

    import personalclaw

    prompts = pathlib.Path(personalclaw.__file__).parent / "config" / "prompts"
    for name in ("chat.md", "background.md"):
        text = (prompts / name).read_text(encoding="utf-8")
        assert "schedule_add" not in text, name
        assert "automation_create" in text, name
