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

**PR2-10 — the ACP delivery path.** S6.2 stopped at the capability FLAG, and the flag had
no consumer: ``steer_capable()`` had zero production callers and no layer exposed
``set_steer_source``, so the dispatcher's ``hasattr(client, "set_steer_source")`` gate found
nothing to wire on an ACP turn. Measured before the fix: a dialect declaring
``supports_mid_turn_prompt`` produced **zero** mid-turn frames on the wire — the declaration
was inert, and at turn end an undelivered steer was cleared with no signal anywhere. The
later half of this file pins the tool-boundary drain seam, its call site, and both
negatives (a non-declaring dialect is refused a drain; an undeliverable steer stays visible).
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


# ── The ACP delivery path (PR2-10) ───────────────────────────────────────────
#
# S6.2 stopped at the capability FLAG. `steer_capable()` had zero production callers and
# no layer (`AcpAgentProvider` / `AcpClient` / `AcpSession` / `AcpSessionProvider`) exposed
# `set_steer_source`, so the dispatcher's `hasattr(client, "set_steer_source")` gate found
# nothing to wire: a dialect could flip `supports_mid_turn_prompt` and the measured result
# was ZERO mid-turn frames on the wire — the declaration was inert. These tests pin the
# delivery path, its CALL SITE (the tool boundary), and the two negatives: a non-declaring
# dialect is refused a drain, and a steer that cannot be written stays visible.


from personalclaw.acp.dialect import DefaultDialect  # noqa: E402
from personalclaw.acp.session import AcpSession  # noqa: E402
from personalclaw.acp.types import JsonRpcMessage  # noqa: E402


class _CapableDialect(DefaultDialect):
    """A dialect that DECLARES mid-turn support — the atom's premise. No shipped dialect
    does (pinned above); this fixture is how a flip is exercised without claiming one."""

    name = "test-capable"
    supports_mid_turn_prompt = True


class _LyingDialect(_CapableDialect):
    """Declares support and then builds no frame. The shape the seam must refuse to
    swallow: the steer has to stay visible rather than be popped into nothing."""

    name = "test-lying"

    def mid_turn_prompt_request(self, *, session_id, text):
        return None


def _acp_session(dialect, *, raise_on_send: bool = False):
    """A real :class:`AcpSession` over fake stdio. ``sent`` records every outbound frame,
    so a test can assert what reached the CLI rather than what a flag said."""
    q: "asyncio.Queue[JsonRpcMessage]" = asyncio.Queue()
    sent: list[tuple[str, dict]] = []
    counter = {"id": 100}

    async def send_request(method, params):
        if raise_on_send:
            raise RuntimeError("stdin is gone")
        counter["id"] += 1
        sent.append((method, params))
        return counter["id"], asyncio.get_event_loop().create_future()

    async def send_response(req_id, result):
        return None

    async def cancel_session():
        return None

    sess = AcpSession(
        "S1",
        q,
        send_request=send_request,
        send_response=send_response,
        cancel_session=cancel_session,
        is_process_alive=lambda: True,
        dialect=dialect,
    )
    return sess, q, sent


def _tool_frame() -> JsonRpcMessage:
    """A real ``tool_call`` update — the frame that IS the tool boundary."""
    return JsonRpcMessage(
        method="session/update",
        params={
            "sessionId": "S1",
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "t1",
                "title": "Read file",
                "kind": "read",
                "status": "pending",
            },
        },
    )


def _text_frame() -> JsonRpcMessage:
    """A plain text chunk — a frame, but NOT a tool boundary."""
    return JsonRpcMessage(
        method="session/update",
        params={
            "sessionId": "S1",
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "thinking out loud"},
            },
        },
    )


async def _one_acp_turn(dialect, frames, steers, *, raise_on_send: bool = False):
    """Drive ONE ACP turn over *frames* with *steers* buffered, returning
    ``(armed, mid_turn_prompts, undelivered)``."""
    sess, q, sent = _acp_session(dialect, raise_on_send=raise_on_send)
    pending = list(steers)

    def _pull() -> list[str]:
        out, pending[:] = list(pending), []
        return out

    armed = sess.set_steer_source(_pull)
    for f in frames:
        q.put_nowait(f)
    fut: "asyncio.Future" = asyncio.get_event_loop().create_future()
    fut.set_result(JsonRpcMessage(id=10, result={"stopReason": "end_turn"}))
    async for _e in sess._dispatch_frames(10, fut, 5.0, method="session/prompt"):
        pass
    return armed, [p for m, p in sent if m == "session/prompt"], sess.undelivered_steers()


# ── the dialect owns the frame ───────────────────────────────────────────────


def test_only_a_declaring_dialect_builds_a_mid_turn_frame():
    """The frame and the flag are one decision. VACUITY: the default dialect's ``None`` is
    the floor — without it, "a frame was built" would prove nothing about the gate."""
    from personalclaw.acp.types import METHOD_PROMPT

    req = _CapableDialect().mid_turn_prompt_request(session_id="S1", text="make it a haiku")
    assert req is not None
    assert req.method == METHOD_PROMPT
    assert req.params["sessionId"] == "S1"
    # The user's words reach the wire — not a label, not a truncation.
    assert req.params["prompt"] == [{"type": "text", "text": "make it a haiku"}]

    assert DefaultDialect().mid_turn_prompt_request(session_id="S1", text="make it a haiku") is None
    # Empty text is not a steer on either dialect.
    assert _CapableDialect().mid_turn_prompt_request(session_id="S1", text="   ") is None


# ── the CALL SITE: the tool boundary ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_tool_boundary_delivers_a_buffered_steer_to_the_cli():
    """The atom's deliverable, asserted at the wire: crossing a tool boundary with a steer
    buffered WRITES a mid-turn prompt carrying the user's text.

    Measured before this landed: 0 frames, with the identical drive.
    """
    armed, prompts, undelivered = await _one_acp_turn(
        _CapableDialect(), [_tool_frame()], ["make it a haiku"]
    )
    assert armed is True
    assert len(prompts) == 1
    assert prompts[0]["prompt"] == [{"type": "text", "text": "make it a haiku"}]
    assert undelivered == []  # delivered, so nothing is left owed


@pytest.mark.asyncio
async def test_a_turn_with_no_tool_boundary_delivers_nothing():
    """VACUITY FLOOR for the test above: the delivery is bound to the TOOL boundary, not to
    "any frame arrived". A prose-only turn crosses no boundary, so the steer stays owed —
    and is therefore still visible at turn end rather than lost."""
    armed, prompts, undelivered = await _one_acp_turn(
        _CapableDialect(), [_text_frame()], ["make it a haiku"]
    )
    assert armed is True
    assert prompts == []
    assert undelivered == []  # never even pulled — the buffer still holds it


# ── negative 1: a non-declaring dialect gets no injection ────────────────────


@pytest.mark.asyncio
async def test_a_non_declaring_dialect_is_refused_the_drain_and_gets_no_injection():
    """The invariant that keeps S6.1's fix intact now that a drain path exists.

    A dialect that has not been proven mid-turn-capable must not be armed, must not
    buffer, and must not receive an injection even when a steer is pending. Paired with
    the capable arm in the same test so the assertion has a vacuity floor: the fixture can
    produce a delivery, so a zero here is the GATE, not a broken harness.
    """
    refused, prompts, undelivered = await _one_acp_turn(
        DefaultDialect(), [_tool_frame()], ["make it a haiku"]
    )
    assert refused is False  # the dispatcher records False → the message queues
    assert prompts == []
    assert undelivered == []

    armed, prompts_ok, _ = await _one_acp_turn(
        _CapableDialect(), [_tool_frame()], ["make it a haiku"]
    )
    assert armed is True and len(prompts_ok) == 1  # the floor: the same drive CAN deliver


def test_a_refused_dialect_leaves_no_source_behind():
    """Refusal clears the source rather than storing it for a later, laxer read."""
    sess, _q, _sent = _acp_session(DefaultDialect())
    assert sess.set_steer_source(lambda: ["x"]) is False
    assert sess._steer_pull is None
    assert sess.steer_capable() is False

    ok, _q2, _s2 = _acp_session(_CapableDialect())

    def pull() -> list[str]:
        return ["x"]

    assert ok.set_steer_source(pull) is True
    assert ok._steer_pull is pull
    assert ok.set_steer_source(None) is False  # disarming is not an arming


# ── negative 2: an undeliverable steer is visible, not swallowed ─────────────


@pytest.mark.asyncio
async def test_a_dialect_that_builds_no_frame_leaves_the_steer_visible():
    """A declared-capable dialect that produces no frame must NOT consume the steer.

    This is the failure mode the whole atom is about, one layer in: the seam pops text off
    the session buffer, so a delivery that silently fails would destroy it. It stays owed on
    ``undelivered_steers()``, which the dispatcher requeues at turn end.
    """
    armed, prompts, undelivered = await _one_acp_turn(
        _LyingDialect(), [_tool_frame()], ["make it a haiku"]
    )
    assert armed is True  # it DID declare support
    assert prompts == []  # ...and delivered nothing
    assert undelivered == ["make it a haiku"]  # so it still owes the user this message


@pytest.mark.asyncio
async def test_a_failed_write_leaves_the_steer_visible():
    """VACUITY-paired with the happy path: same dialect, same frames, only the write
    breaks — and the steer is still accounted for instead of vanishing."""
    _armed, prompts, undelivered = await _one_acp_turn(
        _CapableDialect(), [_tool_frame()], ["make it a haiku"], raise_on_send=True
    )
    assert prompts == []
    assert undelivered == ["make it a haiku"]

    _a2, prompts_ok, undelivered_ok = await _one_acp_turn(
        _CapableDialect(), [_tool_frame()], ["make it a haiku"]
    )
    assert len(prompts_ok) == 1 and undelivered_ok == []


@pytest.mark.asyncio
async def test_an_agent_that_refuses_the_written_frame_still_owes_the_steer():
    """The hole the LIVE spike found in the first cut of this seam.

    Measured against an authenticated kiro-cli: the write SUCCEEDS and the CLI answers
    ``-32603 "Prompt already in progress"``. A seam that only tracked write failures
    reported ``{"steered": true}`` for a message the agent discarded — and the steer
    appeared nowhere: not in that answer, not requeued. An async refusal has to reach the
    same visible path a failed write does.
    """
    sess, q, sent = _acp_session(_CapableDialect())
    futures: list = []

    async def _send_request(method, params):
        fut: "asyncio.Future" = asyncio.get_event_loop().create_future()
        sent.append((method, params))
        futures.append(fut)
        return len(futures), fut

    sess._send_request = _send_request  # type: ignore[assignment]
    sess.set_steer_source(lambda: ["make it a haiku"])
    q.put_nowait(_tool_frame())
    terminal: "asyncio.Future" = asyncio.get_event_loop().create_future()
    terminal.set_result(JsonRpcMessage(id=10, result={"stopReason": "end_turn"}))
    async for _e in sess._dispatch_frames(10, terminal, 5.0):
        pass

    # The frame WAS written, so nothing is owed yet — this is the state the first cut
    # stopped at, and the vacuity floor for the assertion below.
    assert [m for m, _p in sent if m == "session/prompt"]
    assert sess.undelivered_steers() == []

    # ...then the agent refuses it, exactly as kiro-cli did.
    futures[0].set_result(
        JsonRpcMessage(id=1, error={"code": -32603, "data": "Prompt already in progress"})
    )
    await asyncio.sleep(0)  # let the done-callback run
    assert sess.undelivered_steers() == ["make it a haiku"]


@pytest.mark.asyncio
async def test_a_steer_whose_answer_never_comes_is_owed_rather_than_claimed():
    """A future that is cancelled (process gone) is not evidence of delivery."""
    sess, q, sent = _acp_session(_CapableDialect())
    futures: list = []

    async def _send_request(method, params):
        fut: "asyncio.Future" = asyncio.get_event_loop().create_future()
        sent.append((method, params))
        futures.append(fut)
        return len(futures), fut

    sess._send_request = _send_request  # type: ignore[assignment]
    sess.set_steer_source(lambda: ["make it a haiku"])
    q.put_nowait(_tool_frame())
    terminal: "asyncio.Future" = asyncio.get_event_loop().create_future()
    terminal.set_result(JsonRpcMessage(id=10, result={"stopReason": "end_turn"}))
    async for _e in sess._dispatch_frames(10, terminal, 5.0):
        pass

    futures[0].cancel()
    await asyncio.sleep(0)
    assert sess.undelivered_steers() == ["make it a haiku"]


@pytest.mark.asyncio
async def test_an_accepted_steer_is_never_reported_as_owed():
    """VACUITY FLOOR for both tests above: a plain success answer leaves nothing owed, so a
    requeue can never duplicate a steer that DID land."""
    sess, q, sent = _acp_session(_CapableDialect())
    futures: list = []

    async def _send_request(method, params):
        fut: "asyncio.Future" = asyncio.get_event_loop().create_future()
        sent.append((method, params))
        futures.append(fut)
        return len(futures), fut

    sess._send_request = _send_request  # type: ignore[assignment]
    sess.set_steer_source(lambda: ["make it a haiku"])
    q.put_nowait(_tool_frame())
    terminal: "asyncio.Future" = asyncio.get_event_loop().create_future()
    terminal.set_result(JsonRpcMessage(id=10, result={"stopReason": "end_turn"}))
    async for _e in sess._dispatch_frames(10, terminal, 5.0):
        pass

    futures[0].set_result(JsonRpcMessage(id=1, result={"stopReason": "end_turn"}))
    await asyncio.sleep(0)
    assert sess.undelivered_steers() == []


@pytest.mark.asyncio
async def test_the_per_turn_cap_bounds_deliveries_and_keeps_the_remainder_visible():
    """A flood cannot keep one turn alive forever — and the overflow is owed, not dropped.
    (The native drain discards its own overflow inside the pull; this path does not.)"""
    from personalclaw.acp.session import _MAX_STEERS_PER_TURN

    steers = [f"s{i}" for i in range(_MAX_STEERS_PER_TURN + 2)]
    _armed, prompts, undelivered = await _one_acp_turn(
        _CapableDialect(), [_tool_frame(), _tool_frame()], steers
    )
    assert len(prompts) == _MAX_STEERS_PER_TURN
    assert undelivered == steers[_MAX_STEERS_PER_TURN:]


def test_the_acp_cap_matches_the_native_cap():
    """The constant is duplicated (acp must not import upward), so pin the parity — a
    change to one runtime's flood cap is a red here, not a silent divergence."""
    from personalclaw.acp.session import _MAX_STEERS_PER_TURN as acp_cap
    from personalclaw.agents.native.runtime import _MAX_STEERS_PER_TURN as native_cap

    assert acp_cap == native_cap


@pytest.mark.asyncio
async def test_an_undelivered_steer_does_not_leak_into_the_next_turn():
    """The cross-turn leak S6.1 closed must stay closed: a steer aimed at THIS answer is
    not carried into the next one by the pending list."""
    sess, q, sent = _acp_session(_LyingDialect())
    sess.set_steer_source(lambda: ["make it a haiku"])
    q.put_nowait(_tool_frame())
    fut: "asyncio.Future" = asyncio.get_event_loop().create_future()
    fut.set_result(JsonRpcMessage(id=10, result={"stopReason": "end_turn"}))
    async for _e in sess._dispatch_frames(10, fut, 5.0):
        pass
    assert sess.undelivered_steers() == ["make it a haiku"]

    # Second turn, nothing pending: the previous turn's owed steer is gone from the
    # pending list (the dispatcher already requeued it at the first turn's end).
    sess.set_steer_source(lambda: [])
    q.put_nowait(_tool_frame())
    fut2: "asyncio.Future" = asyncio.get_event_loop().create_future()
    fut2.set_result(JsonRpcMessage(id=11, result={"stopReason": "end_turn"}))
    async for _e in sess._dispatch_frames(11, fut2, 5.0):
        pass
    assert sess.undelivered_steers() == []


# ── the wrappers agree with the session ──────────────────────────────────────


def test_both_acp_providers_expose_the_same_seam():
    """One seam, both ACP paths. Leaving the pooled provider out would make steering depend
    on which ACP path a session happened to open on."""
    from personalclaw.llm.acp_agent import AcpAgentProvider
    from personalclaw.llm.acp_session_provider import AcpSessionProvider

    for cls in (AcpAgentProvider, AcpSessionProvider):
        assert callable(getattr(cls, "set_steer_source", None)), cls.__name__
        assert callable(getattr(cls, "undelivered_steers", None)), cls.__name__
        assert callable(getattr(cls, "steer_capable", None)), cls.__name__

    sess, _q, _s = _acp_session(_CapableDialect())
    pooled = AcpSessionProvider.__new__(AcpSessionProvider)
    pooled._session = sess
    assert pooled.steer_capable() is True
    assert pooled.set_steer_source(lambda: []) is True
    assert pooled.undelivered_steers() == []


@pytest.mark.asyncio
async def test_the_client_rearms_the_seam_on_the_session_that_runs_the_turn(monkeypatch):
    """`ensure_ready` can bind a BRAND-NEW AcpSession (first turn, respawn, fresh-turn
    session). A seam wired onto a discarded session delivers nothing, silently — so the
    client re-arms at the one place every turn passes through."""
    from personalclaw.acp.client import AcpClient
    from personalclaw.acp.types import EVENT_COMPLETE, AcpEvent, AcpPromptStats

    class _StubSession:
        def __init__(self) -> None:
            self.armed: list = []
            self.last_prompt_stats = AcpPromptStats()
            self._last_stop_reason = ""

        def set_steer_source(self, pull):
            self.armed.append(pull)
            return pull is not None

        def undelivered_steers(self):
            return []

        async def stream_events(self, message, timeout=0.0):
            yield AcpEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client = AcpClient(command=["/bin/true"], dialect=_CapableDialect())

    def pull() -> list[str]:
        return ["make it a haiku"]

    # No session bound yet — the answer comes from the dialect, the same predicate the
    # session will apply, so the dispatcher cannot record a capability the session denies.
    assert client.set_steer_source(pull) is True
    assert client._session is None

    stub = _StubSession()

    async def _ready():
        client._session = stub  # type: ignore[assignment]

    monkeypatch.setattr(client, "ensure_ready", _ready)
    assert [e.kind async for e in client.stream_events("hi")] == [EVENT_COMPLETE]
    assert stub.armed == [pull]  # re-armed on the session that ran the turn
    assert client.undelivered_steers() == []


def test_the_client_answers_from_the_dialect_before_a_session_exists():
    """VACUITY floor for the test above: a NON-declaring dialect answers False at the same
    pre-session moment, so the True there is the declaration and not the default."""
    from personalclaw.acp.client import AcpClient

    assert AcpClient(command=["/bin/true"]).set_steer_source(lambda: []) is False
    assert AcpClient(command=["/bin/true"], dialect=_CapableDialect()).steer_capable() is True
    assert AcpClient(command=["/bin/true"]).undelivered_steers() == []


# ── turn end hands back what it clears ───────────────────────────────────────


@pytest.mark.asyncio
async def test_turn_end_hands_back_the_steers_it_clears():
    """The buffer must still be emptied at turn end (no cross-turn leak), but emptying it
    and telling nobody is how a message the user was shown as accepted disappeared."""
    mgr = SessionManager(_cfg(), provider_factory=lambda *_a, **_k: _Provider())
    sess = await _running_session(mgr, "dashboard:s1", drains=True)
    mgr.add_steer("dashboard:s1", "one")
    mgr.add_steer("dashboard:s1", "two")

    assert mgr.set_steer_drains("dashboard:s1", False) == ["one", "two"]
    assert list(sess.steers) == []  # still cleared — the leak stays closed
    # VACUITY: arming returns nothing, so a non-empty list means "a turn ENDED owing these".
    assert mgr.set_steer_drains("dashboard:s1", True) == []
    assert mgr.set_steer_drains("dashboard:s1", False) == []
    assert mgr.set_steer_drains("dashboard:nope", False) == []


# ── the DISPATCHER call site: an undelivered steer is requeued, visibly ───────


def _dispatch_state(tmp_path, client, stranded: list[str]):
    """A ``run_chat``-drivable state whose session manager reports *stranded* steers ONCE at
    turn end (once, or the requeued message would strand again forever)."""
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.dashboard.state import DashboardState
    from personalclaw.history import ConversationLog
    from personalclaw.hooks import ToolHookResult

    remaining = [list(stranded)]

    def _set_drains(_key, value):
        if value:
            return []
        return remaining.pop() if remaining else []

    sessions = MagicMock(count=0)
    sessions.get_pid = MagicMock(return_value=None)
    sessions.get_or_create = AsyncMock(return_value=(client, False, False))
    sessions.record_failure = AsyncMock()
    sessions.check_context_usage = MagicMock()
    sessions.set_steer_drains = MagicMock(side_effect=_set_drains)
    sessions.drain_steers = MagicMock(return_value=[])
    state = DashboardState(
        sessions=sessions, start_time=0.0, conversation_log=ConversationLog(base_dir=tmp_path)
    )
    cb = MagicMock()
    cb.hooks.on_tool_call.return_value = ToolHookResult.allow()
    cb.build_message.return_value = ("hello", None)
    cb.conversation_log = None
    state.context_builder = cb
    hooks = MagicMock()
    hooks.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hooks
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()
    return state


def _stub_client(*, armed: bool):
    """A provider that EXPOSES the seam and answers *armed* — the distinction `hasattr`
    could not make."""
    from unittest.mock import MagicMock

    from personalclaw.llm.base import EVENT_COMPLETE, LLMEvent

    async def _events(*_a, **_k):
        yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

    client = MagicMock()
    client.provider_id = "acp:test-cli"
    client.stream = MagicMock(side_effect=lambda *a, **kw: _events())
    client.set_steer_source = MagicMock(return_value=armed)
    client.undelivered_steers = MagicMock(return_value=[])
    return client


def _pushed(state) -> list[dict]:
    return [
        call.args[1]
        for call in state.broadcast_ws.call_args_list
        if call.args and call.args[0] == "queue_push"
    ]


async def _drive_turn(tmp_path, client, stranded):
    from unittest.mock import MagicMock, patch

    from personalclaw.dashboard.chat_runner import run_chat
    from personalclaw.dashboard.state import _ChatSession

    state = _dispatch_state(tmp_path, client, stranded)
    session = _ChatSession("chat-1-pr210")
    session._trust = True
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        await run_chat(state, session, "hello")
    for task in list(state._background_tasks):
        try:
            await task
        except Exception:
            pass
    return state, session


@pytest.mark.asyncio
async def test_an_undelivered_steer_is_requeued_with_a_visible_queue_push(tmp_path):
    """The dispatcher's half of "visibly not-delivered".

    Pre-fix, turn end cleared the buffer and told nobody: a message the HTTP caller was shown
    as ``{"steered": true}`` disappeared with no trace on any surface. Now it lands on the
    SAME queue the ``steer`` policy already promises as its fallback ("never drop, never
    cancel"), with the ``queue_push`` echo the composer's queue strip renders — deliberately
    NOT an ``activity_event {kind:"status"}``, which the frontend filters as noise.
    """
    state, session = await _drive_turn(tmp_path, _stub_client(armed=True), ["make it a haiku"])

    pushes = _pushed(state)
    assert [p["content"] for p in pushes] == ["make it a haiku"]
    assert pushes[0]["session"] == session.key
    assert pushes[0]["queue_id"]  # a real, cancellable queue entry
    # ...and it really became the next turn's input, not just a notification.
    assert any(
        m.get("role") == "user" and "make it a haiku" in (m.get("content") or "")
        for m in session.messages
    )


@pytest.mark.asyncio
async def test_a_turn_that_strands_nothing_pushes_nothing(tmp_path):
    """VACUITY FLOOR: the same drive with an empty stranded list emits no queue_push, so the
    assertion above is about the stranded steer and not run_chat's normal chatter."""
    state, _session = await _drive_turn(tmp_path, _stub_client(armed=True), [])

    assert _pushed(state) == []


@pytest.mark.asyncio
async def test_the_dispatcher_records_the_seams_answer_not_its_presence(tmp_path):
    """`hasattr` was the old gate, and it cannot tell a capable dialect from a declared one.

    A provider that EXPOSES the seam but refuses to arm it (every non-declaring ACP dialect)
    must leave the session non-draining, so ``add_steer`` keeps returning False and the
    message queues. Paired with the armed arm as its vacuity floor.
    """
    state, _s = await _drive_turn(tmp_path, _stub_client(armed=False), [])
    assert state.sessions.set_steer_drains.call_args_list[0].args[1] is False

    state2, _s2 = await _drive_turn(tmp_path, _stub_client(armed=True), [])
    assert state2.sessions.set_steer_drains.call_args_list[0].args[1] is True
