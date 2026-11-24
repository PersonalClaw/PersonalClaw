"""A damaged ``config.json`` or ``hooks.json`` refuses a call the owner's hooks would be asked of.

The step every tool-call path asks (``pre_tool_hooks.on_tool``) refused a call when the agent's
bindings could not be read. Two damaged files got past it. A ``config.json`` the loader discards
loads as the defaults, which bind no hook, and a ``hooks.json`` that cannot be read loads no hook,
so a bound id fired nothing. Either way the step found no hook to ask and every call went on.

Now a discarded ``config.json`` refuses the call, naming the file and where to see what's wrong, as
a tool list holds an agent to the tools always kept until it is repaired, unless no hook could be
asked before a call at all (none enabled for ``PreToolUse``, and ``hooks.json`` readable). A bound
hook the store doesn't hold while ``hooks.json`` cannot be read refuses it too. A bound hook deleted
from a readable file still runs nothing, as before, and a store that read its hooks before the file
was damaged still asks them.
"""

from __future__ import annotations

import json

import pytest

from personalclaw import pre_tool_hooks
from personalclaw.action_providers.base import ActionResult
from personalclaw.hooks import HOOK_EVENT_PRE_TOOL_USE, HOOK_EVENT_STOP, ScriptHook, ScriptHookStore

AGENT = "keeper"
WRITE = '{"path": "notes.md", "content": "x"}'


class _Command:
    """The hook's action: it lets the call go on, and counts its runs."""

    def __init__(self) -> None:
        self.runs = 0

    async def execute(self, config, ctx, timeout=30):
        self.runs += 1
        return ActionResult(success=True, exit_code=0)


@pytest.fixture
def bound(monkeypatch):
    """A hook bound to the default agent in a readable ``config.json``, held in ``hooks.json``."""
    import personalclaw.action_providers as action_providers
    from personalclaw.config import loader as config_loader

    # The discard is remembered process-wide until the next read; leave none behind.
    monkeypatch.setattr(config_loader, "_LAST_CONFIG_DISCARD", None)
    command = _Command()
    monkeypatch.setattr(action_providers, "get_action_provider", lambda name: command)
    (config_loader.config_dir() / "config.json").write_text(
        json.dumps(
            {
                "default_agent": AGENT,
                "agents": {AGENT: {"triggers": ["no-writes"]}},
                "updates": {"check_enabled": False},
            }
        ),
        encoding="utf-8",
    )
    store = ScriptHookStore()
    store.create(
        ScriptHook(
            id="no-writes",
            name="no-writes",
            event=HOOK_EVENT_PRE_TOOL_USE,
            provider="bash",
            provider_config={"command": "/nonexistent/pc-fixture-hook"},
            enabled=True,
            capabilities={"providers": ["bash"]},
        ).to_dict()
    )
    return command, store


def _hooks_file():
    from personalclaw.config.loader import config_dir

    return config_dir() / "hooks.json"


async def _asked(store: ScriptHookStore) -> pre_tool_hooks.HooksSaid:
    return await pre_tool_hooks.on_tool("fs_write", WRITE, agent=None, store=store)


@pytest.mark.asyncio
async def test_a_discarded_config_refuses_the_call_naming_the_file(bound):
    """🔴 Red before: the defaults bound no hook, so the call went on."""
    from personalclaw.config.loader import config_dir

    command, store = bound
    (config_dir() / "config.json").write_text('{"agents": ', encoding="utf-8")
    said = await _asked(store)
    assert said.refusal == pre_tool_hooks.HOOK_FAILED and not said.blocked
    assert "config.json could not be read" in said.error
    assert "Settings → Health → Doctor" in said.error  # it names the repair, where it is
    assert command.runs == 0


def _discard_config() -> None:
    from personalclaw.config.loader import config_dir

    (config_dir() / "config.json").write_text('{"agents": ', encoding="utf-8")


@pytest.mark.asyncio
async def test_a_discarded_config_refuses_nothing_when_no_hook_could_be_asked(bound):
    """🔴 Red before: an owner with no hooks had every call refused over a config typo."""
    command, store = bound
    store.delete("no-writes")
    _discard_config()
    said = await _asked(ScriptHookStore())
    assert not said.refused, said
    assert command.runs == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("held", ["disabled", "another event"])
async def test_a_hook_that_is_never_asked_before_a_call_binds_nothing(bound, held):
    """🔴 Red before: a disabled hook, or one of another event, refused every call too."""
    command, store = bound
    change = {"enabled": False} if held == "disabled" else {"event": HOOK_EVENT_STOP}
    store.update("no-writes", change)
    _discard_config()
    said = await _asked(ScriptHookStore())
    assert not said.refused, said


@pytest.mark.asyncio
async def test_a_discarded_config_still_refuses_while_hooks_json_cant_be_read_either(bound):
    command, store = bound
    store.delete("no-writes")
    _hooks_file().write_text("not json", encoding="utf-8")
    _discard_config()
    said = await _asked(ScriptHookStore())
    assert said.refused and "config.json could not be read" in said.error


@pytest.mark.asyncio
async def test_an_unreadable_hooks_file_refuses_a_call_its_bound_hook_is_missing_from(bound):
    """🔴 Red before: the store loaded no hook, so the bound id fired nothing."""
    command, _store = bound
    _hooks_file().write_text("not json", encoding="utf-8")
    said = await _asked(ScriptHookStore())
    assert said.refusal == pre_tool_hooks.HOOK_FAILED and not said.blocked
    assert "hooks.json could not be read" in said.error
    assert command.runs == 0


@pytest.mark.asyncio
async def test_both_files_readable_a_bound_hook_that_passes_lets_the_call_go_on(bound):
    command, store = bound
    said = await _asked(store)
    assert not said.refused, said
    assert command.runs == 1


@pytest.mark.asyncio
async def test_a_bound_hook_deleted_from_a_readable_file_still_lets_the_call_go_on(bound):
    command, store = bound
    store.delete("no-writes")
    said = await _asked(ScriptHookStore())
    assert not said.refused, said
    assert command.runs == 0


@pytest.mark.asyncio
async def test_a_store_that_read_its_hooks_before_the_damage_still_asks_them(bound):
    """The gateway's store holds what it read; the bound hook it holds still runs."""
    command, store = bound
    _hooks_file().write_text("not json", encoding="utf-8")
    said = await _asked(store)
    assert not said.refused, said
    assert command.runs == 1
