"""DAS-9: `SURFACE_BACKGROUND` must have a real PRODUCER, not just a declaration.

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

import ast
import asyncio
import types
from pathlib import Path

import pytest

import personalclaw
from personalclaw.durability import state_history as sh

SRC = Path(personalclaw.__file__).resolve().parent

#: The producer that already existed. It is the census's vacuity floor: if the walker stops
#: matching calls (a rename, an alias, a bad AST assumption) this row disappears too, so a census
#: that has quietly stopped seeing anything fails here instead of reading clean.
KNOWN_SCHEDULED_PRODUCER = "durability/state_history.py"

#: The producer this atom adds.
EXPECTED_BACKGROUND_PRODUCER = "gateway.py"


def _writing_surface_calls() -> dict[str, set[str]]:
    """Map ``<path relative to src/personalclaw>`` -> surface constants passed to a CALL of
    ``writing_surface(...)``.

    Deliberately an AST walk over calls, not a text scan for the constant's name. A file that only
    MENTIONS `SURFACE_BACKGROUND` — in a comment, a docstring, or a read like
    `history_debounce._dominant_surface`'s membership test — is not a producer, and a census that
    counted those would report this atom as already done before a single line was written.
    """
    found: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a template/fixture, never a producer
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if called != "writing_surface":
                continue
            arg = node.args[0]
            surface = arg.attr if isinstance(arg, ast.Attribute) else getattr(arg, "id", "")
            found.setdefault(str(path.relative_to(SRC)), set()).add(surface)
    return found


# ── the census ─────────────────────────────────────────────────────────────


def test_the_census_still_sees_the_KNOWN_producer():
    """🔴 The vacuity floor, asserted on its own so a broken walker cannot read as a clean tree.

    Every claim below is "the census found / did not find X". That is only evidence if the census
    can still find something it is known to contain.
    """
    calls = _writing_surface_calls()
    assert KNOWN_SCHEDULED_PRODUCER in calls, (
        "the census found no `writing_surface(...)` call in "
        f"{KNOWN_SCHEDULED_PRODUCER} — the walker is broken, not the tree"
    )
    assert "SURFACE_SCHEDULED" in calls[KNOWN_SCHEDULED_PRODUCER]


def test_SURFACE_BACKGROUND_has_a_producer_outside_state_history():
    """🔴 The gap this atom closes. A declared surface with no writer is an inert control: the
    panel's filter offers a choice the data can never satisfy."""
    calls = _writing_surface_calls()
    producers = {
        path
        for path, surfaces in calls.items()
        if "SURFACE_BACKGROUND" in surfaces and path != KNOWN_SCHEDULED_PRODUCER
    }
    assert producers, (
        "SURFACE_BACKGROUND has no producer outside state_history.py — it is still a declared "
        "constant nothing writes"
    )
    assert EXPECTED_BACKGROUND_PRODUCER in producers, (
        f"expected the unattended trigger dispatch in {EXPECTED_BACKGROUND_PRODUCER} to be the "
        f"producer; found {sorted(producers)}"
    )


def test_the_producer_is_APPLIED_to_the_unattended_dispatch():
    """A call that exists is not a call that runs.

    The producer is a decorator, so deleting the one line that APPLIES it would leave the
    `writing_surface(SURFACE_BACKGROUND)` call sitting in the tree, still passing the census above,
    while every unattended fire went back to the interactive surface. So the census names the
    decorated function too — the same "assert the call SITE, not the mechanism" reason the census
    exists at all.
    """
    tree = ast.parse((SRC / "gateway.py").read_text(encoding="utf-8"))
    dispatches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_fire_store_trigger"
    ]
    assert len(dispatches) == 1, (
        "expected exactly one `_fire_store_trigger`; the dispatch seam has moved or forked, and "
        "this census no longer measures the shared path"
    )
    decorators = {
        d.attr if isinstance(d, ast.Attribute) else getattr(d, "id", "")
        for d in dispatches[0].decorator_list
    }
    assert "_background_write_surface" in decorators, (
        "`_fire_store_trigger` is no longer wrapped in the background writing surface — the "
        f"producer exists but nothing applies it (decorators: {sorted(decorators)})"
    )


def test_a_MENTION_of_the_constant_does_not_count_as_a_producer():
    """The census must be scoped to the call, not to any file that names the constant.

    `history_debounce` names `SURFACE_BACKGROUND` (its `_dominant_surface` prefers it when a
    coalesced burst contains one) and `gateway`'s wrap carries it in prose. Neither of those is a
    writer; a text scan would have scored both.
    """
    debounce = SRC / "durability" / "history_debounce.py"
    text = debounce.read_text(encoding="utf-8")
    assert (
        "SURFACE_BACKGROUND" in text
    ), "this discriminator is vacuous unless the file really does mention the constant"
    calls = _writing_surface_calls()
    assert "SURFACE_BACKGROUND" not in calls.get(
        "durability/history_debounce.py", set()
    ), "a READ of the constant was scored as a producer"

    gateway_text = (SRC / "gateway.py").read_text(encoding="utf-8")
    prose = [
        line
        for line in gateway_text.splitlines()
        if "SURFACE_BACKGROUND" in line and line.lstrip().startswith("#")
    ]
    for line in prose:
        assert "writing_surface(" not in line, f"a commented-out call would score: {line!r}"


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

    async def _record(trigger, *, result=None, exc=None):  # noqa: ANN001, ARG001
        at_record.append(sh.current_surface())

    orch._record_fire_outcome = _record
    monkeypatch.setattr(
        type(orch), "_deliver_fire_outcome", lambda self, *a, **k: None, raising=True
    )
    asyncio.run(orch._fire_store_trigger(_trigger(), {"trigger_id": "clock:surface"}))

    assert at_record == [
        sh.SURFACE_BACKGROUND
    ], f"the outcome record ran on {at_record!r} — the wrap does not cover the whole dispatch"


def test_the_ATTENDED_dispatch_is_NOT_background(probe):
    """🔴 The other direction, which is what makes the positive test mean anything.

    A hand-driven "Run now" goes through `dashboard.handlers.triggers._dispatch_store_action`, a
    human-watched path. Labelling it `background` would put the user's own click into "what changed
    while I slept" — so the default `interactive` surface must survive here.
    """
    from personalclaw.dashboard.handlers.triggers import _dispatch_store_action

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
