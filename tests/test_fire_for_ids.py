"""ScriptHookStore.fire_for_ids — agent-scoped hook firing.

An agent references a subset of the hook library; only those hooks fire for it,
in contrast to the global ``fire()`` which fires every enabled matching hook.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.hooks import HOOK_EVENT_USER_PROMPT_SUBMIT, ScriptHook, ScriptHookStore


@pytest.fixture
def store(tmp_path: Path) -> ScriptHookStore:
    return ScriptHookStore(tmp_path)


def _add(store: ScriptHookStore, hid: str, name: str, enabled: bool = True) -> ScriptHook:
    return store.create(
        {
            "id": hid,
            "name": name,
            "event": HOOK_EVENT_USER_PROMPT_SUBMIT,
            "provider": "bash",
            "provider_config": {"command": f"echo {name}"},
            "enabled": enabled,
        }
    )


@pytest.mark.asyncio
async def test_fire_for_ids_fires_only_referenced(store: ScriptHookStore):
    _add(store, "h1", "alpha")
    _add(store, "h2", "beta")
    _add(store, "h3", "gamma")

    results = await store.fire_for_ids(HOOK_EVENT_USER_PROMPT_SUBMIT, {"h1", "h3"}, context="hi")
    fired = {r.hook_id for r in results}
    assert fired == {"h1", "h3"}  # h2 NOT fired


@pytest.mark.asyncio
async def test_fire_for_ids_empty_fires_nothing(store: ScriptHookStore):
    _add(store, "h1", "alpha")
    assert await store.fire_for_ids(HOOK_EVENT_USER_PROMPT_SUBMIT, set(), context="hi") == []
    assert await store.fire_for_ids(HOOK_EVENT_USER_PROMPT_SUBMIT, None, context="hi") == []


@pytest.mark.asyncio
async def test_fire_for_ids_respects_event_and_enabled(store: ScriptHookStore):
    _add(store, "h1", "alpha", enabled=False)  # disabled → must not fire even if referenced
    results = await store.fire_for_ids(HOOK_EVENT_USER_PROMPT_SUBMIT, {"h1"}, context="hi")
    assert results == []  # h1 disabled


@pytest.mark.asyncio
async def test_fire_still_fires_all_enabled(store: ScriptHookStore):
    """The global fire() fires every enabled matching hook regardless of id."""
    _add(store, "h1", "alpha")
    _add(store, "h2", "beta")
    results = await store.fire(HOOK_EVENT_USER_PROMPT_SUBMIT, context="hi")
    assert {r.hook_id for r in results} == {"h1", "h2"}
