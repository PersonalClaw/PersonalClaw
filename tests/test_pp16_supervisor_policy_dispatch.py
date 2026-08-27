"""`PP-16` seam 3 — the CALL SITE, not the mechanism.

The seam's claim is that the loop supervisor stopped being pluggable Python: the convergence
decision is now DECLARED in `workflows/supervisor_policy.KIND_CONVERGENCE` and evaluated by the one
kind-agnostic evaluator in `loop/supervisor.py`. A test that only exercised `supervisor.done_signal`
directly would prove nothing about that — a policy object nothing dispatches through is the
"declared strategy without an executor" shape this repo keeps getting burned by, and the deleted
plugin would still be the thing that decided if the watchdog had never been rewired.

So every assertion here drives the REAL `LoopWatchdog._poll_once` over a real store row and
observes the shipped seam:

* the watchdog RESOLVES a policy for all five kinds (spying on the `policy_for_kind` name the
  watchdog module actually calls), and the policy it resolves is the declared row; and
* the MECHANISM the table names for a kind is the mechanism that then runs — a `verify_command`
  row really spawns the command path, a `judge_assessment` row really reaches the judge, a `never`
  row reaches neither, and an `orchestrated` row does not reach the point-in-time evaluator at all
  because its per-cycle hook owns done-ness.

Vacuity floors: each spy carries a positive control (it observed something) and the mechanism
matrix is cross-checked in the negative direction (a judge row must NOT run a command, and vice
versa), so a spy that silently observed nothing cannot pass by being uniformly blind.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.loop import manager, store, supervisor
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import KINDS, Loop, LoopStatus
from personalclaw.workflows.supervisor_policy import (
    DONE_JUDGE_ASSESSMENT,
    DONE_NEVER,
    DONE_ORCHESTRATED,
    DONE_VERIFY_COMMAND,
    KIND_CONVERGENCE,
    convergence_key,
    policy_for_kind,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def _tmp_config(monkeypatch, tmp_path):
    """Destructive by nature (it creates loop rows + finding files), so the store is redirected at
    BOTH bindings a caller can reach: `loop.store.config_dir` is the name `store` resolved at
    import, and `config.loader.config_dir` is the origin. Patching only one leaves an import-bound
    reader pointed at the real home."""
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    assert store.config_dir() == tmp_path, "the store redirect did not take — refusing to run"
    return tmp_path


class _FakeSession:
    def __init__(self, key, running=False):
        self.key = key
        self._running = running
        self._trust = True
        self.messages = []

    @property
    def running(self):
        return self._running


class _FakeState:
    def __init__(self):
        self._sessions = {}
        self.notes = []
        self.refreshed = []
        from personalclaw.dashboard.sse import SseRegistry

        self._sse = SseRegistry()

    def loop_sse(self):
        return self._sse

    def push_refresh(self, *kinds):
        self.refreshed.append(kinds)

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append((kind, title, body, meta or {}))


class _FakeNudge:
    def __init__(self, lid, session_name):
        self.id, self.session_name, self.active, self.cycle_count = lid, session_name, True, 0


class _FakeSvc:
    def __init__(self):
        self._loops = {}
        self._n = 0

    async def add(
        self, *, session_name, message, idle_secs, max_cycles, stop_sentinel_path, first_idle_secs=0
    ):
        self._n += 1
        lp = _FakeNudge(f"N{self._n}", session_name)
        self._loops[lp.id] = lp
        return lp

    def get_by_session(self, session_name):
        return next((lp for lp in self._loops.values() if lp.session_name == session_name), None)

    async def update(self, loop_id, **kw):
        lp = self._loops.get(loop_id)
        if lp:
            for k, v in kw.items():
                setattr(lp, k, v)

    async def remove(self, loop_id):
        self._loops.pop(loop_id, None)


def _wd():
    wd = W.LoopWatchdog(_FakeState(), _FakeSvc())
    wd._publish = lambda lid, event, data=None: None  # type: ignore[assignment]
    return wd


def _running(**over):
    base = dict(
        id="",
        name="L",
        kind="goal",
        task="investigate the latency regression",
        kind_config={"goal_type": "open_ended"},
        idle_secs=120,
        max_cycles=20,
    )
    base.update(over)
    loop = store.create(Loop(**base))
    store.update_status(loop.id, LoopStatus.RUNNING)
    return store.get(loop.id)


def _write_finding(cid, cycle):
    (store.loop_dir(cid) / "findings" / f"cycle_{cycle:03d}.json").write_text(
        json.dumps({"cycle": cycle, "new_findings_count": 1, "summary": f"cycle {cycle} work"})
    )


def _drive_one_cycle(wd, loop):
    """Seed liveness, land ONE new finding, then poll again — the shipped path that reaches the
    convergence decision. Returns nothing; the caller reads its spies."""
    wd._state._sessions[manager.session_key(loop.id)] = _FakeSession(manager.session_key(loop.id))
    _run(wd._poll_once())  # first observation seeds liveness and returns early
    _write_finding(loop.id, 1)
    _run(wd._poll_once())  # a new finding → the convergence decision


#: The five kinds and the `kind_config` that selects each one's declared variant. `goal` is driven
#: in all three of its variants because the variant IS the axis the deleted Python branched on.
_KIND_CASES: tuple[tuple[str, dict], ...] = (
    ("general", {"verify_command": "true"}),
    ("goal", {"goal_type": "verifiable", "verify_command": "true"}),
    ("goal", {"goal_type": "open_ended"}),
    ("goal", {"goal_type": "monitor"}),
    ("research", {"goal_type": "open_ended"}),
    ("code", {}),
    ("design", {}),
)


def _case_id(case: tuple[str, dict]) -> str:
    kind, cfg = case
    return convergence_key(kind, cfg)


@pytest.mark.parametrize("case", _KIND_CASES, ids=[_case_id(c) for c in _KIND_CASES])
def test_the_watchdog_resolves_the_declared_policy_for_every_kind(monkeypatch, case):
    """The watchdog reaches `policy_for_kind` for EVERY kind, and what it gets back is the declared
    row — not a default, and not something a strategy supplied."""
    kind, cfg = case
    seen: list[tuple[str, dict]] = []
    real = W.policy_for_kind

    def _spy(k, kc):
        seen.append((k, dict(kc or {})))
        return real(k, kc)

    monkeypatch.setattr(W, "policy_for_kind", _spy)
    # Neither real mechanism may run here: this test is about RESOLUTION, and a live judge or a
    # spawned subprocess would make it slow and non-hermetic.
    monkeypatch.setattr("personalclaw.loop.gates.run_verify_command", lambda *a, **k: _coro(None))
    monkeypatch.setattr("personalclaw.loop.judge.assess_cycle", lambda *a, **k: _coro(None))

    loop = _running(kind=kind, kind_config=dict(cfg))
    _drive_one_cycle(_wd(), loop)

    assert seen, (
        f"the watchdog never resolved a policy for kind {kind!r}. The convergence decision would "
        f"then be coming from somewhere else — which is the pre-PP-16 pluggable supervisor."
    )
    assert seen[0][0] == kind and seen[0][1] == cfg
    resolved = real(kind, cfg)
    assert (
        resolved.convergence is KIND_CONVERGENCE[convergence_key(kind, cfg)]
    ), "the watchdog resolved a policy that is not the declared row — a second declaration exists"


#: Which mechanism each declared signal is allowed to reach. The matrix is asserted in BOTH
#: directions, so a run that reaches the wrong one fails as loudly as one that reaches none.
_MECHANISM_BY_SIGNAL = {
    DONE_VERIFY_COMMAND: ("command",),
    DONE_JUDGE_ASSESSMENT: ("judge",),
    DONE_NEVER: (),
    DONE_ORCHESTRATED: (),
}

#: The mechanism each CONVERGENCE KEY must reach, pinned INDEPENDENTLY of the declaration.
#:
#: Deriving this from `policy_for_kind(...).convergence.signal` was the first shape of this test and
#: it was measurably too weak: flipping `general`'s row from `verify_command` to `orchestrated`
#: (a real falsification run) left the derived expectation agreeing with the mutated row, so the
#: general leg still PASSED. A matrix that reads its own answer off the thing under test can only
#: catch a declaration/evaluator disagreement, never a WRONG declaration — which is the failure
#: that would actually ship (a kind that silently stops self-completing). These values are the
#: behaviour of the DELETED plugin, restated here as the independent expectation.
_EXPECTED_MECHANISM: dict[str, tuple[str, ...]] = {
    "general": ("command",),  # GeneralKind.is_done_signal ran verify_command
    "goal:verifiable": ("command",),  # GoalKind: goal_type == "verifiable" ran verify_command
    "goal:open_ended": ("judge",),  # GoalKind._assess_open_ended commissioned the judge
    "goal:monitor": (),  # GoalKind: goal_type == "monitor" returned False outright
    "research:open_ended": ("judge",),  # ResearchKind inherited GoalKind's open-ended branch
    "code": (),  # CodeKind.is_done_signal was `return None`; its hook owns done-ness
    "design": (),  # DesignKind.is_done_signal was `return None`; its hook owns done-ness
}


@pytest.mark.parametrize("case", _KIND_CASES, ids=[_case_id(c) for c in _KIND_CASES])
def test_the_declared_mechanism_is_the_one_that_runs(monkeypatch, case):
    """The table is load-bearing: for each kind, exactly the mechanism the DELETED PLUGIN used is
    the one the shipped poll reaches — and the declaration agrees. Both are asserted, so a wrong
    row reds even though it is internally consistent."""
    kind, cfg = case
    reached: list[str] = []

    def _command(*_a, **_k):
        reached.append("command")
        return _coro(None)  # can't-tell → the loop neither completes nor stalls

    def _judge(*_a, **_k):
        reached.append("judge")
        return _coro(None)

    monkeypatch.setattr("personalclaw.loop.gates.run_verify_command", _command)
    monkeypatch.setattr("personalclaw.loop.judge.assess_cycle", _judge)
    # The calibration canary would otherwise run its own probe before the judge; short-circuit it
    # to "trustworthy" so this test observes the assessment call, not the probe.
    monkeypatch.setattr("personalclaw.loop.instrument.probe_judge", lambda *_a, **_k: _coro(True))

    loop = _running(kind=kind, kind_config=dict(cfg))
    _drive_one_cycle(_wd(), loop)

    key = convergence_key(kind, cfg)
    signal = policy_for_kind(kind, cfg).convergence.signal
    expected = set(_EXPECTED_MECHANISM[key])
    assert set(reached) == expected, (
        f"{key} must reach {sorted(expected) or 'no mechanism'} — the mechanism the deleted plugin "
        f"used — but the poll reached {sorted(set(reached))}. Either the declared row is wrong "
        f"(it currently says {signal!r}) or a kind is still deciding in Python."
    )
    # And the declaration agrees with the independent expectation, so the row cannot drift away
    # from the behaviour above while the run still happens to reach the right mechanism.
    assert set(_MECHANISM_BY_SIGNAL[signal]) == expected, (
        f"{key} declares signal {signal!r} (→ {sorted(_MECHANISM_BY_SIGNAL[signal])}) but must use "
        f"{sorted(expected)}. The declaration is the shipped rule; fix the row, not this test."
    )


def test_an_orchestrated_kind_does_not_reach_the_point_in_time_evaluator(monkeypatch):
    """The two ORCHESTRATED kinds are the reason the seam is a declaration and not a deletion: their
    per-cycle hook owns done-ness, so the declared signal must route AROUND the evaluator's
    mechanisms rather than run one and ignore it."""
    calls: list[str] = []
    real_signal = supervisor.done_signal

    async def _spy(loop, findings, policy):
        calls.append(policy.convergence.signal)
        return await real_signal(loop, findings, policy)

    monkeypatch.setattr(supervisor, "done_signal", _spy)
    monkeypatch.setattr("personalclaw.loop.gates.run_verify_command", lambda *a, **k: _coro(None))

    # Positive control FIRST: a non-orchestrated kind must reach the evaluator, else "not reached"
    # below would be indistinguishable from a spy that never fires.
    general = _running(kind="general", kind_config={"verify_command": "true"})
    _drive_one_cycle(_wd(), general)
    assert calls == [DONE_VERIFY_COMMAND], f"positive control failed — evaluator calls: {calls}"

    calls.clear()
    code = _running(kind="code", kind_config={})
    _drive_one_cycle(_wd(), code)
    assert calls == [] or calls == [DONE_ORCHESTRATED], (
        f"a code loop reached the evaluator with {calls} — its hook owns the cycle's done-ness, so "
        f"either the hook stopped running or the declaration is wrong."
    )


def test_a_monitor_goals_two_satellite_decisions_come_from_the_policy(monkeypatch):
    """`budget_stop_is_genuine` and the stall signal were the two `getattr`-shaped hooks beside
    `is_done_signal`. Both are now policy reads, asserted at the watchdog's own call sites."""
    monkeypatch.setattr("personalclaw.loop.gates.run_verify_command", lambda *a, **k: _coro(None))
    monitor = _running(kind="goal", kind_config={"goal_type": "monitor"}, max_cycles=1)
    wd = _wd()
    _drive_one_cycle(wd, monitor)
    # max_cycles=1 with one finding → the budget path ran, and for a monitor the policy declares
    # that stop GENUINE, so the loop completes cleanly rather than error-flavoured.
    row = store.get(monitor.id)
    assert row.status == LoopStatus.COMPLETE.value, row.status
    assert supervisor.budget_stop_is_genuine(policy_for_kind("goal", {"goal_type": "monitor"}))
    # The stall signal is off for a monitor and on for everything else — read through the
    # watchdog's own predicate, so a rewiring that bypassed the policy would red here.
    assert wd._stagnation_disabled(row) is True
    assert (
        wd._stagnation_disabled(_running(kind="goal", kind_config={"goal_type": "open_ended"}))
        is False
    )


def test_every_declared_signal_has_a_mechanism_row():
    """Vacuity floor for the matrix: a signal added to the closed vocabulary without a row here
    would make `_MECHANISM_BY_SIGNAL[...]` raise rather than silently pass, but only if some test
    drives that signal. This asserts the coverage directly."""
    from personalclaw.workflows.supervisor_policy import DONE_SIGNALS

    assert set(_MECHANISM_BY_SIGNAL) == set(
        DONE_SIGNALS
    ), "the done-signal vocabulary changed — add the new signal's mechanism row and a driven case"
    driven = {policy_for_kind(k, c).convergence.signal for k, c in _KIND_CASES}
    assert driven == set(DONE_SIGNALS), (
        f"the driven cases cover {sorted(driven)} but the vocabulary is {sorted(DONE_SIGNALS)} — "
        f"an undriven signal is an unmeasured mechanism."
    )
    # And every KIND is driven, not just every signal: five kinds, all present.
    assert {k for k, _ in _KIND_CASES} == set(KINDS), set(KINDS) - {k for k, _ in _KIND_CASES}
    # The independent expectation covers exactly the driven cases — a case with no pinned mechanism
    # would raise a KeyError above rather than pass, but an ORPHAN row would rot unnoticed.
    assert set(_EXPECTED_MECHANISM) == {_case_id(c) for c in _KIND_CASES}


def _coro(value):
    async def _c(*_a, **_k):
        return value

    return _c()
