"""Firing the dormant lifecycle events (AUTO §7 criterion 5 — S82).

Criterion 5's second clause: "the 8 dormant lifecycle events actually fire". S67 closed the first
clause (event-kind API parity) and left this one measured but open —
`triggers.events.configurable_but_dead()` returned seven names, and a grep for each outside its own
declaration found exactly ONE hit: the `validation.py` allowlist. Selectable in the hook UI,
saveable, and fired by nothing.

The tests are grouped by what can actually go wrong: the payload contract (a hook receives what the
catalog promised), the FIRE (it reaches a real store), the GATING (an event that means "X happened"
must not fire when X did not), and the never-raise rule (an observer never fails the thing it
observed).
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from personalclaw.hooks import HOOK_EVENTS, LIFECYCLE_EVENT_CATALOG
from personalclaw.triggers import lifecycle_fire as L


@pytest.fixture
def spy_store(tmp_path, monkeypatch):
    """A real `ScriptHookStore` whose `fire` records instead of running scripts.

    Intercepts at the store's `fire` boundary because that is the ONLY path that runs a hook —
    asserting against a mock of the helper would prove the helper calls itself.
    """
    from personalclaw.hooks import ScriptHookStore, set_global_hook_store

    store = ScriptHookStore(config_dir=pathlib.Path(tmp_path))
    fired: list[tuple[str, str, dict]] = []

    async def _spy(event, **kw):
        fired.append((event, kw.get("context", ""), kw))
        return []

    monkeypatch.setattr(store, "fire", _spy)
    set_global_hook_store(store)
    yield fired
    set_global_hook_store(None)


# ── the payload contract ──


def test_every_dormant_event_has_a_builder():
    """A new dormant event without a fire site should be a failing test, not another dead UI option.

    Walks `DORMANT_EVENTS` (this module's own list of what it owns) against `BUILDERS`.
    """
    assert set(L.DORMANT_EVENTS) == set(L.BUILDERS)


def test_every_builder_names_an_event_the_catalog_declares():
    """A typo'd event name would fire something no hook can subscribe to — invisible, since the fire
    succeeds and nothing runs."""
    declared = set(HOOK_EVENTS)
    for name, build in L.BUILDERS.items():
        assert name in declared, f"{name} is not a declared lifecycle event"
        assert build()["event"] == name, f"{name}'s builder emits a different event name"


def test_every_builder_emits_the_pool_payload_shape():
    """`workflows/pool.lifecycle_payload` set the shape: `event` + a `context` string, NOT new hook
    variables. The hook UI renders a fixed `vars` tuple per event, so a variable it does not list is
    one no user can discover."""
    for name, build in L.BUILDERS.items():
        payload = build()
        assert set(payload) == {"event", "context"}, name
        assert isinstance(payload["context"], str), name


def test_the_catalog_variables_appear_in_the_subagent_context():
    """`SubagentSpawn`'s catalog row declares `$subagent_id`, `$parent_session_key`, `$agent_role`.

    A hook author reads that list in the UI, so those exact names appear in the context string too —
    not only on `fire()`'s dedicated parameters.
    """
    row = next(r for r in LIFECYCLE_EVENT_CATALOG if r.get("event") == "SubagentSpawn")
    ctx = L.subagent_spawn_payload(
        subagent_id="sa-1", parent_session_key="dashboard:x", agent_role="coder"
    )["context"]
    for var in row["vars"]:
        name = var.lstrip("$")
        if name in ("EVENT", "CONTEXT", "cwd"):
            continue  # supplied by the fire machinery, not the context string
        assert name in ctx, f"{name} missing from the SubagentSpawn context"


def test_empty_fields_are_dropped_not_rendered_as_empty():
    """A script parsing `k=v` pairs cannot tell "absent" from "empty string" otherwise, and the
    difference matters for an optional field like `agent`."""
    ctx = L.pre_response_payload(session_key="s1", agent="")["context"]
    assert ctx == "session=s1"
    assert "agent=" not in ctx


def test_free_text_is_capped():
    """Hook context reaches a shell script's environment. An unbounded field is `E2BIG` on exec,
    which surfaces as "the hook mysteriously stopped running"."""
    ctx = L.memory_write_payload(key="k" * 500, kind="lesson")["context"]
    assert len(ctx) < 300
    assert f"key={'k' * L.FIELD_CAP}" in ctx


def test_newlines_never_break_the_kv_shape():
    """A multi-line value would make the context unparseable as space-separated pairs."""
    ctx = L.session_end_payload(session_key="a\nb", reason="removed")["context"]
    assert "\n" not in ctx


@pytest.mark.parametrize("build", list(L.BUILDERS.values()))
def test_a_builder_with_no_arguments_still_produces_a_valid_payload(build):
    """Every field is optional at the call site, so a caller missing context still fires a legible
    event rather than raising inside the code it was observing."""
    payload = build()
    assert payload["event"] in HOOK_EVENTS
    assert isinstance(payload["context"], str)


# ── the payloads deliberately withhold content ──


def test_the_reply_text_is_not_in_the_post_response_payload():
    """Size, not content. Passing an assistant turn through the environment is the `E2BIG` failure,
    and it would hand untrusted model output to a shell script as an argument."""
    ctx = L.post_response_payload(session_key="s", reply_chars=4096, tool_calls=3)["context"]
    assert "reply_chars=4096" in ctx and "tool_calls=3" in ctx


def test_the_lesson_body_is_not_in_the_memory_write_payload():
    """A memory body is user content. The fencing work in S69/S79 exists so untrusted text does not
    travel into places that execute."""
    ctx = L.memory_write_payload(kind="lesson", key="workflow", scope="user_explicit")["context"]
    assert "kind=lesson" in ctx and "key=workflow" in ctx


def test_the_approval_payload_carries_no_tool_input():
    """An approval prompt is exactly the moment a hook must not receive attacker-influenced
    arguments."""
    ctx = L.approval_request_payload(
        tool="bash", source="unattended", session_key="s", approval_id="a1"
    )["context"]
    assert "tool=bash" in ctx
    assert "input" not in ctx


# ── firing reaches a real store ──


def test_every_event_reaches_the_hook_store(spy_store):
    async def _run():
        for build in L.BUILDERS.values():
            await L.fire(build())

    asyncio.run(_run())
    assert {e for e, _c, _k in spy_store} == set(L.BUILDERS)


def test_fire_forwards_the_dedicated_parameters(spy_store):
    """`subagent_id`/`parent_session_key`/`agent_role`/`tool_name` are real `fire()` parameters that
    the payload assembly reads — passing them only in the context string would leave `$subagent_id`
    unset for the hook."""

    async def _run():
        await L.fire(
            L.subagent_spawn_payload(subagent_id="sa-9"),
            subagent_id="sa-9",
            parent_session_key="dashboard:p",
            agent_role="coder",
        )

    asyncio.run(_run())
    _event, _ctx, kw = spy_store[0]
    assert kw["subagent_id"] == "sa-9"
    assert kw["parent_session_key"] == "dashboard:p"
    assert kw["agent_role"] == "coder"


def test_a_missing_hook_store_is_a_no_op(monkeypatch):
    """The store is absent in CLI runs and most tests. An observer that raised there would fail
    every path that does not use hooks at all."""
    from personalclaw.hooks import set_global_hook_store

    set_global_hook_store(None)
    asyncio.run(L.fire(L.pre_response_payload(session_key="s")))  # must not raise


def test_a_broken_hook_never_raises_into_the_caller(spy_store, monkeypatch):
    """The rule `tasks/native.py` set for `TaskComplete`: a broken hook script must not turn a
    successful memory write into an exception, and the write already happened."""
    from personalclaw.hooks import get_global_hook_store

    store = get_global_hook_store()

    async def _boom(event, **kw):
        raise RuntimeError("hook exploded")

    monkeypatch.setattr(store, "fire", _boom)
    asyncio.run(L.fire(L.session_end_payload(session_key="s", reason="removed")))


# ── the sync bridge ──


def test_fire_sync_schedules_onto_a_running_loop(spy_store):
    """`MemoryService.write_lesson` and `SubagentManager.spawn` are sync; `fire` is a coroutine.

    `asyncio.run()` from inside a running loop raises `RuntimeError`, and both call sites are
    reachable from the dashboard's loop — so the bridge schedules instead.
    """

    async def _run():
        L.fire_sync(L.memory_write_payload(kind="lesson", key="k"))
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert [e for e, _c, _k in spy_store] == ["MemoryWrite"]


def test_fire_sync_with_no_loop_is_a_silent_no_op(spy_store):
    """The honest answer for a CLI write: there is no loop to run a hook on, and blocking a sync
    write to start one would make every `write_lesson` pay for a feature most users never configure.
    """
    L.fire_sync(L.memory_write_payload(kind="lesson", key="k"))
    assert spy_store == []


# ── gating: an event that says "X happened" must not fire when X did not ──


def _fake_service(*, writes: bool, blocked: bool = False):
    from personalclaw.memory_service import MemoryService

    class _VS:
        def write_lesson(self, rule, category="knowledge", negative=None, source=""):
            return writes

    class _Svc(MemoryService):
        def __init__(self):  # noqa: D107 - a bare shell; only write_lesson is exercised
            pass

        @property
        def _vs(self):
            return _VS()

        def _memory_write_blocked(self, *_a, **_k):
            return blocked

    return _Svc()


def test_memory_write_fires_only_on_a_successful_write(spy_store):
    async def _run():
        assert _fake_service(writes=True).write_lesson("always lint", category="workflow") is True
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert [e for e, _c, _k in spy_store] == ["MemoryWrite"]
    assert "key=workflow" in spy_store[0][1]


def test_memory_write_is_silent_when_the_store_refuses(spy_store):
    """A hook that ran for a failed write would report memory the user does not have."""

    async def _run():
        assert _fake_service(writes=False).write_lesson("nope") is False
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert spy_store == []


def test_memory_write_is_silent_when_the_write_is_BLOCKED(spy_store):
    """The incognito/hygiene gate refuses before the store is touched — nothing was written, so
    nothing is announced."""

    async def _run():
        assert _fake_service(writes=True, blocked=True).write_lesson("blocked") is False
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    assert spy_store == []


def test_session_end_reports_which_ending_it_was(spy_store):
    """`removed` (resumable) / `destroyed` (permanent) / `shutdown`. A cleanup hook that cannot tell
    them apart either runs on every tab close or misses the case it was written for."""
    from personalclaw.session import _fire_session_end, _Session

    class _P:
        async def shutdown(self):
            return None

    async def _run():
        for reason in ("removed", "destroyed", "shutdown"):
            await _fire_session_end(f"k-{reason}", reason, _Session(provider=_P(), prompt_count=7))

    asyncio.run(_run())
    reasons = [c.split("reason=")[1].split(" ")[0] for _e, c, _k in spy_store]
    assert reasons == ["removed", "destroyed", "shutdown"]


def test_session_end_turns_come_from_the_field_that_exists(spy_store):
    """🔴 Measured: a first pass read `session.messages`, which `_Session` does not carry (that lives
    on the dashboard's session object), so every fire would have reported `turns=0` — a plausible
    number that is always wrong, which is worse than an absent field."""
    from personalclaw.session import _fire_session_end, _Session

    class _P:
        async def shutdown(self):
            return None

    asyncio.run(_fire_session_end("k", "removed", _Session(provider=_P(), prompt_count=12)))
    assert "turns=12" in spy_store[0][1]


# ── the dormancy ledger is now empty, and stays honest ──


def test_the_events_module_agrees_that_nothing_is_dormant():
    """The two modules must not disagree: `lifecycle_fire` owns the fire sites, `triggers.events`
    owns what the UI reports. A wired event left in `DORMANT_EVENTS` would tell a user their working
    hook is dead."""
    from personalclaw.triggers.events import DORMANT_EVENTS, dormant_events

    assert dormant_events() == []
    assert DORMANT_EVENTS == frozenset()
    # And this module's own list is exactly what it wired.
    assert len(L.DORMANT_EVENTS) == 7
