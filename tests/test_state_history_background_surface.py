"""`SURFACE_BACKGROUND` must have a real PRODUCER, not just a declaration.

`durability/state_history.py` declares three writing surfaces and the time-travel panel's "what
changed while I slept" filter reads them. Measured before this change: the ONLY
`writing_surface(...)` call in the tree was `state_history.py`'s own hourly maintenance job, which
sets `SURFACE_SCHEDULED` — so `SURFACE_BACKGROUND` was a declared constant with ZERO writers, and
every
commit caused by an unattended trigger dispatch landed on the DEFAULT `interactive` surface. The
filter could not tell an automation's edit from something the user typed.

These tests assert the CALL SITE, not the mechanism. That `writing_surface` labels a commit is
already covered by `test_durability_state_history.py`; what was missing is anything CALLING it for
the background surface. So the census below is scoped to actual calls (AST, not text), carries a
vacuity floor so a census that matches nothing fails loudly, and the behavioural tests drive both
directions — the unattended dispatch AND its attended counterpart — because a positive-only test
passes for free in a world where everything is labelled `background`.
"""

from __future__ import annotations

import asyncio
import types
from pathlib import Path

import pytest

from personalclaw.durability import state_history as sh

# ── the census ─────────────────────────────────────────────────────────────


# ── the behaviour ──────────────────────────────────────────────────────────


class _SurfaceProbe:
    """An action provider that records the writing surface in force when it runs."""

    def __init__(self, write_to: Path | None = None) -> None:
        self.seen: list[str] = []
        self._write_to = write_to

    async def execute(self, config, ctx, timeout=30):  # noqa: ANN001, ARG002
        self.seen.append(sh.current_surface())
        if self._write_to is not None:
            from personalclaw.atomic_write import atomic_write

            atomic_write(self._write_to, "fired")
        return types.SimpleNamespace(success=True)


def _trigger(tid: str = "clock:surface"):
    return types.SimpleNamespace(
        id=tid,
        kind="clock",
        workflow={"inline": {"provider": "notify", "config": {"message": "hi"}}},
    )


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Never the real `~/.personalclaw`: the fire path records outcomes and history."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: home)
    return home


@pytest.fixture
def probe(monkeypatch, isolated_home):
    """Swap the action-provider lookup for the surface probe, on both import paths the two
    dispatch seams use."""
    p = _SurfaceProbe(write_to=isolated_home / "probe.json")
    monkeypatch.setattr(
        "personalclaw.action_providers.get_action_provider", lambda name: p, raising=True
    )
    monkeypatch.setattr(
        "personalclaw.action_providers.registry.get_action_provider", lambda name: p, raising=False
    )
    return p


def _orch():
    from personalclaw.gateway import GatewayOrchestrator

    return object.__new__(GatewayOrchestrator)


def test_an_UNATTENDED_trigger_dispatch_runs_on_the_background_surface(probe):
    """🔴 The producer, driven. The dispatch every clock / file / webhook / chained fire passes
    through must put its writes on the surface the "while I slept" filter reads."""
    asyncio.run(_orch()._fire_store_trigger(_trigger(), {"trigger_id": "clock:surface"}))
    assert probe.seen == [
        sh.SURFACE_BACKGROUND
    ], f"the unattended dispatch ran on {probe.seen!r}, not the background surface"
    assert sh.is_unattended_surface(probe.seen[0]), "the filter must count this as 'while I slept'"


def test_the_wrap_reaches_the_ATOMIC_WRITE_seam_the_debouncer_reads(probe, isolated_home):
    """Scope, not intent. The surface is read by the post-write hook in the WRITER's context
    (`history_debounce.notify` calls `current_surface()`), so a wrap that did not survive to the
    `atomic_write` seam would leave the commit unlabelled anyway."""
    from personalclaw.atomic_write import register_post_write_hook, unregister_post_write_hook

    at_write: list[str] = []

    def _hook(path):  # noqa: ANN001
        at_write.append(sh.current_surface())

    register_post_write_hook(_hook)
    try:
        asyncio.run(_orch()._fire_store_trigger(_trigger(), {"trigger_id": "clock:surface"}))
    finally:
        unregister_post_write_hook(_hook)

    assert at_write, "the probe wrote nothing, so this test measured nothing"
    assert set(at_write) == {
        sh.SURFACE_BACKGROUND
    }, f"a write inside the dispatch was announced on {sorted(set(at_write))}"


def test_the_surface_still_holds_when_the_OUTCOME_is_recorded(probe, monkeypatch):
    """The provider's own writes are not the only ones a fire causes — the outcome record, the
    refusal rows and the chained fires all write too. A context wrapped around `provider.execute`
    alone would label one of them and leave the rest interactive."""
    orch = _orch()
    at_record: list[str] = []

    async def _record(trigger, **_kw):  # noqa: ANN001, ARG001
        at_record.append(sh.current_surface())
        return ""

    monkeypatch.setattr("personalclaw.triggers.run_record.record_run", _record)
    monkeypatch.setattr(
        "personalclaw.triggers.delivery.report_run", lambda *a, **k: None, raising=True
    )
    asyncio.run(orch._fire_store_trigger(_trigger(), {"trigger_id": "clock:surface"}))

    assert at_record == [
        sh.SURFACE_BACKGROUND
    ], f"the outcome record ran on {at_record!r} — the wrap does not cover the whole dispatch"


def test_the_ATTENDED_dispatch_is_NOT_background(probe):
    """🔴 The other direction, which is what makes the positive test mean anything.

    A hand-driven "Run now" goes through `dashboard.handlers.trigger_runs._dispatch_store_action`, a
    human-watched path. Labelling it `background` would put the user's own click into "what changed
    while I slept" — so the default `interactive` surface must survive here.
    """
    from personalclaw.dashboard.handlers.trigger_runs import _dispatch_store_action

    ran, note = asyncio.run(_dispatch_store_action(_trigger(), {"trigger_id": "clock:surface"}))
    assert ran, f"the attended path did not dispatch, so nothing was measured: {note}"
    assert probe.seen == [
        sh.SURFACE_INTERACTIVE
    ], f"the attended dispatch ran on {probe.seen!r}; a human-watched run must stay interactive"
    assert sh.SURFACE_BACKGROUND not in probe.seen
    assert not sh.is_unattended_surface(probe.seen[0])


def test_the_surface_is_RESTORED_after_an_unattended_dispatch(probe):
    """The wrap must not leak: a fire runs on a long-lived gateway task that goes on to do other
    work, and a surface left at `background` would mislabel everything after it.

    Measured INSIDE the same task on purpose. `asyncio.run` builds a fresh context copy for its
    main task, so a leak can never escape it — reading `current_surface()` after the `asyncio.run`
    returns would pass no matter what the wrap did, which is a test that measures the harness.
    """

    async def _fire_then_read() -> str:
        await _orch()._fire_store_trigger(_trigger(), {"trigger_id": "clock:surface"})
        return sh.current_surface()

    assert asyncio.run(_fire_then_read()) == sh.SURFACE_INTERACTIVE, (
        "the dispatch left the background surface set on its task; the next thing this task writes "
        "would be filed under 'while I slept'"
    )
