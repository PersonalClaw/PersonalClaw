"""Mid-turn steering: the capability gate, the key contract, and the policy default.

PLATFORM-RESILIENCE S6.1/S6.2. Steering (#37) shipped with no test coverage at all,
which is how three defects survived in one path:

1. **The lookup key never matched.** ``chat_handlers`` called ``add_steer(session.key)``
   with the BARE session id while ``SessionManager`` registers under the NAMESPACED
   ``dashboard:<id>``, so ``self._sessions.get(key)`` missed every time and steering
   silently degraded to queueing on EVERY runtime, native included.
2. **Buffering with no drain path.** ``add_steer`` returned True on the semaphore
   alone. ``steers`` is cleared ONLY by ``drain_steers``, whose sole caller is the
   native runtime's pull lambda — so on an ACP-backed turn the message was buffered,
   never delivered, and the deque grew unbounded while the HTTP caller was told
   ``{"steered": true}``.
3. **The platform policy had no say.** The webui's mid-turn default was hardcoded to
   ``"steer"``, so ``resilience.mid_turn_policy`` could not express "queue".

The tests below fail against the pre-fix code (verified) and encode the invariant that
matters: ``steer_drains`` tracks a WIRED DRAIN SOURCE, never a declared intention.
"""

from __future__ import annotations

import asyncio

import pytest

from personalclaw.config import AppConfig
from personalclaw.session import SessionManager


def _cfg() -> AppConfig:
    return AppConfig.load()


class _Provider:
    """Minimal ModelProvider stand-in — these tests never dispatch a turn."""


async def _running_session(mgr: SessionManager, key: str, *, drains: bool) -> object:
    """Register a session under *key* with a turn in flight."""
    from personalclaw.session import _Session

    sess = _Session(provider=_Provider())  # type: ignore[arg-type]
    await sess.semaphore.acquire()  # a turn is generating
    mgr._sessions[key] = sess
    mgr.set_steer_drains(key, drains)
    return sess


# ── The capability gate ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_steer_refused_when_no_drain_source_is_wired():
    """The core fix: no drain path ⇒ refuse, so the caller queues instead.

    Pre-fix this returned True and the message was buffered into a deque nothing
    would ever read — a permanent silent drop reported to the user as success.
    """
    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    sess = await _running_session(mgr, "dashboard:c1", drains=False)

    assert mgr.add_steer("dashboard:c1", "also check the logs") is False
    assert list(sess.steers) == []  # nothing buffered blind


@pytest.mark.asyncio
async def test_steer_accepted_when_a_drain_source_is_wired():
    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    sess = await _running_session(mgr, "dashboard:c1", drains=True)

    assert mgr.add_steer("dashboard:c1", "also check the logs") is True
    assert list(sess.steers) == ["also check the logs"]
    assert mgr.drain_steers("dashboard:c1") == ["also check the logs"]
    assert list(sess.steers) == []  # drained exactly once


@pytest.mark.asyncio
async def test_steer_refused_when_no_turn_is_in_flight():
    """Unchanged pre-existing behavior: nothing to steer ⇒ the caller queues."""
    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    from personalclaw.session import _Session

    mgr._sessions["dashboard:c1"] = _Session(provider=_Provider())  # type: ignore[arg-type]
    mgr.set_steer_drains("dashboard:c1", True)  # capable, but idle

    assert mgr.add_steer("dashboard:c1", "hello") is False


@pytest.mark.asyncio
async def test_turn_end_clears_the_flag_and_drops_undrained_steers():
    """A steer the loop never got to must not surface inside a later, unrelated turn."""
    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    sess = await _running_session(mgr, "dashboard:c1", drains=True)
    assert mgr.add_steer("dashboard:c1", "mid-turn note") is True

    mgr.set_steer_drains("dashboard:c1", False)  # what the dispatcher does at turn end

    assert list(sess.steers) == []
    assert mgr.add_steer("dashboard:c1", "next one") is False  # no longer steerable


@pytest.mark.asyncio
async def test_blank_steer_is_refused():
    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    await _running_session(mgr, "dashboard:c1", drains=True)

    assert mgr.add_steer("dashboard:c1", "   ") is False


@pytest.mark.asyncio
async def test_unknown_session_is_refused_and_never_raises():
    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())

    assert mgr.add_steer("dashboard:nope", "hi") is False
    assert mgr.drain_steers("dashboard:nope") == []
    mgr.set_steer_drains("dashboard:nope", True)  # must not raise


# ── The key contract (defect 1) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_handler_uses_the_key_the_manager_registers_under():
    """Regression for the mismatch that made steering dead on arrival.

    ``SessionManager`` is keyed by the namespaced ``dashboard:<id>``. A caller passing
    the bare id gets a miss — silently, because ``add_steer`` returns False and the
    caller "just queues". This pins that the handler's key derivation agrees with the
    manager's registration.
    """
    from personalclaw.dashboard.chat_utils import _history_key_for

    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    bare = "chat-7-1769000000"
    registered = _history_key_for(bare)
    assert registered == f"dashboard:{bare}"  # the shape the manager is keyed by

    await _running_session(mgr, registered, drains=True)

    # What the handler does now:
    assert mgr.add_steer(_history_key_for(bare), "steer me") is True
    # What it did before — the bug, kept as a negative assertion:
    assert mgr.add_steer(bare, "steer me") is False


# ── The policy default (defect 3) ────────────────────────────────────────────


def test_mid_turn_policy_accepts_steer():
    """`steer` joins the enum, and an unknown value still falls back to `queue`."""
    from personalclaw.config.loader import AppConfig as _AC

    cfg = _AC.load()
    meta = type(cfg.resilience).__dataclass_fields__["mid_turn_policy"].metadata
    assert meta["enum"] == ["queue", "steer", "cancel_and_replace"]


@pytest.mark.parametrize(
    "policy,expected",
    [("steer", "steer"), ("queue", "queue"), ("cancel_and_replace", "queue")],
)
def test_default_mid_turn_mode_follows_policy(monkeypatch, policy, expected):
    """The webui default now derives from policy instead of being hardcoded to steer.

    `cancel_and_replace` maps to `queue` here because cancellation is handled earlier
    in the request path; if it declines, queueing is the safe remainder.
    """
    from personalclaw.dashboard import chat_handlers

    cfg = AppConfig.load()
    cfg.resilience.mid_turn_policy = policy
    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", classmethod(lambda cls: cfg))

    assert chat_handlers._default_mid_turn_mode() == expected


def test_default_mid_turn_mode_falls_back_to_queue_on_config_failure(monkeypatch):
    """Never drop, never cancel: a broken config read yields the safe mode."""
    from personalclaw.dashboard import chat_handlers

    def _boom(cls):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", classmethod(_boom))

    assert chat_handlers._default_mid_turn_mode() == "queue"


# ── ACP capability honesty (S6.2) ────────────────────────────────────────────


def test_acp_dialects_do_not_claim_mid_turn_prompt_support_by_default():
    """No dialect is assumed capable until a live spike proves it (same discipline as
    `supports_concurrent_sessions`)."""
    from personalclaw.acp.dialect import _DIALECTS, ACPDialect

    assert ACPDialect.supports_mid_turn_prompt is False
    for name, cls in _DIALECTS.items():
        assert cls().supports_mid_turn_prompt is False, f"{name} claims mid-turn support"


def test_acp_provider_reports_steer_capability_from_its_dialect():
    from personalclaw.llm.acp_agent import AcpAgentProvider

    prov = AcpAgentProvider.__new__(AcpAgentProvider)
    prov._dialect_id = None  # default dialect
    assert prov.steer_capable() is False

    prov._dialect_id = "definitely-not-a-dialect"  # unknown ⇒ default ⇒ False
    assert prov.steer_capable() is False


@pytest.mark.asyncio
async def test_a_capable_dialect_alone_cannot_resurrect_the_silent_drop():
    """The invariant that makes the bug class impossible.

    A dialect flipping `supports_mid_turn_prompt` to True declares intent, but the
    drop only becomes possible if `steer_drains` can be set without a wired drain
    callable. `add_steer` keys off the WIRED SOURCE, so a declaration alone changes
    nothing: with no drain source the message still queues.
    """
    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    sess = await _running_session(mgr, "dashboard:acp1", drains=False)

    assert mgr.add_steer("dashboard:acp1", "steer an ACP turn") is False
    assert list(sess.steers) == []


@pytest.mark.asyncio
async def test_steers_are_bounded_by_the_drain_contract():
    """Pre-fix, an ACP session's deque grew for the life of the process. Now every
    buffered steer has a reader, and turn-end clears the remainder."""
    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    sess = await _running_session(mgr, "dashboard:c1", drains=True)

    for i in range(50):
        mgr.add_steer("dashboard:c1", f"note {i}")
    assert len(sess.steers) == 50  # buffered because a reader exists

    assert len(mgr.drain_steers("dashboard:c1")) == 50
    assert list(sess.steers) == []

    for i in range(10):
        mgr.add_steer("dashboard:c1", f"late {i}")
    mgr.set_steer_drains("dashboard:c1", False)
    assert list(sess.steers) == []  # turn end leaves nothing stranded


@pytest.mark.asyncio
async def test_concurrent_steers_and_a_drain_do_not_lose_or_duplicate():
    """The deque is touched from the request handler and the loop's pull; drained
    content must be exactly what was added, once each."""
    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    await _running_session(mgr, "dashboard:c1", drains=True)

    seen: list[str] = []

    async def _add(i: int) -> None:
        mgr.add_steer("dashboard:c1", f"m{i}")

    async def _drain() -> None:
        seen.extend(mgr.drain_steers("dashboard:c1"))

    await asyncio.gather(*[_add(i) for i in range(20)], _drain(), _drain())
    seen.extend(mgr.drain_steers("dashboard:c1"))

    assert sorted(seen) == sorted(f"m{i}" for i in range(20))


# ── The drain boundary (defect 4) ────────────────────────────────────────────


def _runtime_with_steers(steers: list[str]):
    """A NativeAgentRuntime shell wired to a steer source, with no model or session.

    `__new__` avoids the real constructor's provider/definition/config graph — these
    tests exercise the drain helper's contract, not a turn.
    """
    from personalclaw.agents.native.runtime import NativeAgentRuntime

    rt = NativeAgentRuntime.__new__(NativeAgentRuntime)
    rt._messages = []
    rt._steers_injected = 0
    rt._steer_pending = []
    pending = list(steers)

    def _pull() -> list[str]:
        out, pending[:] = list(pending), []
        return out

    rt._pull_steer = _pull if steers else None
    return rt


def test_drain_appends_pending_steers_as_user_input():
    rt = _runtime_with_steers(["stop at 5"])

    assert rt._drain_steers_into_history() is True
    assert len(rt._messages) == 1
    assert rt._messages[0]["role"] == "user"
    assert "stop at 5" in rt._messages[0]["content"]
    assert "[Steering" in rt._messages[0]["content"]  # labelled, not smuggled in
    # Drained once: a second call has nothing left and must not re-append.
    assert rt._drain_steers_into_history() is False
    assert len(rt._messages) == 1


def test_drain_is_a_no_op_without_a_steer_source():
    rt = _runtime_with_steers([])

    assert rt._drain_steers_into_history() is False
    assert rt._messages == []


def test_drain_respects_the_per_turn_cap():
    """A flood cannot extend one turn forever."""
    from personalclaw.agents.native.runtime import _MAX_STEERS_PER_TURN

    rt = _runtime_with_steers([f"s{i}" for i in range(_MAX_STEERS_PER_TURN + 3)])

    assert rt._drain_steers_into_history() is True
    assert len(rt._messages) == _MAX_STEERS_PER_TURN
    assert rt._steers_injected == _MAX_STEERS_PER_TURN
    # At the cap the drain reports False so the loop ends the turn normally.
    rt._pull_steer = lambda: ["one more"]
    assert rt._drain_steers_into_history() is False
    assert len(rt._messages) == _MAX_STEERS_PER_TURN


# ── The overflow's fate (the pop-then-discard defect) ────────────────────────


@pytest.mark.asyncio
async def test_the_capped_overflow_is_retained_not_discarded():
    """The cap bounds DELIVERY, and used to also destroy the overflow.

    ``_drain_steers_into_history`` pulls the session's whole steer deque and then broke
    at ``_MAX_STEERS_PER_TURN``, discarding text it had already removed from the only
    place holding it. Measured on the pre-fix code with the cap at 4 and 7 buffered:
    ``s4``/``s5``/``s6`` were in history, in the session deque and on the runtime —
    nowhere. The user had been told ``{"steered": true}``.

    Both halves are asserted, because either alone is passable while the bug is live:
    a "the cap held" test passes while the rest evaporate, and a "the rest are still
    there" test passes if the drain never pulled them at all.
    """
    from personalclaw.agents.native.runtime import _MAX_STEERS_PER_TURN

    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    sess = await _running_session(mgr, "dashboard:c1", drains=True)

    offered = [f"s{i}" for i in range(_MAX_STEERS_PER_TURN + 3)]
    for text in offered:
        assert mgr.add_steer("dashboard:c1", text) is True
    # VACUITY FLOOR for half A, two parts. The fixture must actually overflow, or
    # "exactly the cap was delivered" is the same statement as "everything was
    # delivered" and the cap is untested:
    assert len(offered) > _MAX_STEERS_PER_TURN
    # ...and the cap's VALUE is pinned, because `offered` is derived from it: without
    # this, every assertion below is cap-relative and a raised cap would satisfy all of
    # them. Enlarging the bound on how much redirection one turn absorbs is a product
    # decision; it is not a way to stop losing the overflow.
    assert _MAX_STEERS_PER_TURN == 4

    rt = _runtime_with_steers([])
    rt._pull_steer = lambda: mgr.drain_steers("dashboard:c1")

    assert rt._drain_steers_into_history() is True

    # Half A — the capped number really is delivered into THIS turn's history.
    delivered = [m["content"].splitlines()[-1] for m in rt._messages]
    assert delivered == offered[:_MAX_STEERS_PER_TURN]

    # VACUITY FLOOR for half B: the drain must genuinely have emptied the session's
    # buffer. Without this, a drain that pulled nothing would satisfy "the remainder
    # survives" by never having endangered it — the assertion below would be measuring
    # the fixture instead of the fix.
    assert list(sess.steers) == []  # type: ignore[attr-defined]

    # Half B — the overflow is still there afterwards, owed rather than lost.
    assert rt.undelivered_steers() == offered[_MAX_STEERS_PER_TURN:]

    # ...and stays owed: further boundaries in the same turn are already at the cap, so
    # the remainder must not be silently consumed by a later drain either.
    assert rt._drain_steers_into_history() is False
    assert len(rt._messages) == _MAX_STEERS_PER_TURN
    assert rt.undelivered_steers() == offered[_MAX_STEERS_PER_TURN:]


def test_the_runtime_exposes_the_dispatcher_s_turn_end_steer_seam():
    """CALL SITE contract: the dispatcher reads the remainder duck-typed, by NAME.

    ``chat_runner`` collects what a turn still owes with
    ``callable(getattr(client, "undelivered_steers", None))`` and requeues each entry via
    ``session.queue_append`` + the ``queue_push`` WS echo, so the text lands in the
    composer's queue strip where the user can read and cancel it. That is the ACP path's
    seam (PR2-10), reused rather than duplicated — a second mechanism for the same problem
    is the dual path the clean-break tenet forbids.

    This pins the runtime's half: the exact spelling, callable, and a plain ``list[str]``.
    A rename would leave the dispatcher's ``getattr`` silently returning None, which is
    precisely the invisible drop being fixed.
    """
    from personalclaw.agents.native.runtime import NativeAgentRuntime

    seam = getattr(NativeAgentRuntime, "undelivered_steers", None)
    assert callable(seam)

    rt = _runtime_with_steers([])
    assert rt.undelivered_steers() == []  # empty on the happy path, never None
    rt._steer_pending = ["owed"]
    owed = rt.undelivered_steers()
    assert owed == ["owed"]
    assert isinstance(owed, list)
    # A COPY, not the live buffer: the dispatcher iterates it while requeueing, and
    # handing out the internal list would let that mutate turn state mid-iteration.
    owed.append("mutated")
    assert rt.undelivered_steers() == ["owed"]


def test_a_new_turn_does_not_replay_the_previous_turn_s_owed_steers():
    """`stream` clears `_steer_pending` at turn start, structurally pinned.

    The dispatcher requeues the remainder at the END of a turn. If the runtime also kept
    it, the next turn would inject a second copy of a steer already visible in the queue
    strip — the same text delivered twice, from two owners.
    """
    import inspect

    from personalclaw.agents.native.runtime import NativeAgentRuntime

    src = inspect.getsource(NativeAgentRuntime.stream)
    assert "self._steer_pending.clear()" in src
    clear_at = src.index("self._steer_pending.clear()")
    first_append = src.index('self._messages.append({"role": "user"')
    assert clear_at < first_append, "the reset must precede the turn's first message"


def test_drain_never_raises_when_the_source_does():
    rt = _runtime_with_steers(["x"])

    def _boom() -> list[str]:
        raise RuntimeError("session went away")

    rt._pull_steer = _boom
    assert rt._drain_steers_into_history() is False  # a broken pull ends the turn
    assert rt._messages == []


def test_a_no_tool_turn_reaches_the_drain_before_returning():
    """Defect 4, pinned structurally.

    The drain used to live ONLY after the tool batch, so the `if not tool_calls`
    early return fired first and a plain-prose turn discarded the steer. The source
    must call the drain BEFORE that return, or steering silently fails on the most
    common turn shape.
    """
    import inspect

    from personalclaw.agents.native.runtime import NativeAgentRuntime

    src = inspect.getsource(NativeAgentRuntime.stream)
    drain_at = src.index("_drain_steers_into_history")
    stop_at = src.index("if not tool_calls or self._cancelled:")
    assert drain_at < stop_at, "the steer drain must precede the no-tool-call return"
