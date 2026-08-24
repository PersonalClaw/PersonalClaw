"""`LV-1` — the skill-ladder review fires at the LOOP END-OF-RUN seam, not only on chat.

Before this, `after_turn_review.run_skill_ladder_review` had exactly one production caller:
the chat after-turn path. Every unattended loop — the runs that do the most work and produce
the most reusable procedure — ended without ever proposing a skill. This module holds the
seam's four properties:

1. ONE synthesis call per RUN (never per cycle), and a second `_complete` for the same loop
   does not buy a second — `store.update_status` permits COMPLETE → COMPLETE, so the second
   pass is reachable and has to be guarded.
2. An environment-failure run enqueues ZERO proposals — asserted on the QUEUE, because a
   verdict marker saying `env_failure_claim` while a proposal sits in the queue would pass a
   verdict-only assertion.
3. The terminal status write and the `complete` publish never wait on the model call.
4. Nothing here can wedge a loop's completion.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw import after_turn_review as atr
from personalclaw.loop import store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus
from personalclaw.skills import proposals


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    """One isolated home for the three stores this seam touches: the loop store, the skill
    proposals queue, and the config the gate reads. Nothing may reach the real home."""
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.skills.loader.config_dir", lambda: tmp_path)
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: tmp_path / "config.json")
    import personalclaw.skills.marketplace as mp

    monkeypatch.setattr(mp, "SKILL_DISCOVERY_PATHS", [])
    return tmp_path


def _write_config(home, **learning):
    (home / "config.json").write_text(json.dumps({"learning": learning}))


# ── fakes (the watchdog's two collaborators) ─────────────────────────────────


class _FakeSse:
    def __init__(self):
        self.events = []

    def publish(self, key, event, data):
        self.events.append((key, event, data))


class _FakeState:
    def __init__(self):
        self._sessions = {}
        self._background_tasks: set = set()
        self.notes = []
        self._sse = _FakeSse()

    def loop_sse(self):
        return self._sse

    def push_refresh(self, *kinds):
        pass

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append((kind, title, body))


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
    return W.LoopWatchdog(_FakeState(), _FakeSvc())


def _running(**over):
    base = dict(
        id="",
        name="L",
        kind="goal",
        task="always run the migration through the staging gate before prod",
        summary="stage the migration, then promote",
        kind_config={"goal_type": "open_ended"},
        idle_secs=120,
        max_cycles=20,
    )
    base.update(over)
    loop = store.create(Loop(**base))
    store.update_status(loop.id, LoopStatus.RUNNING)
    return store.get(loop.id)


def _drain(state):
    """Await everything `_complete` scheduled. Exceptions are surfaced as values, never
    raised: a background review that raised must not be reported as a test error here —
    property 4 asserts the completion survived it."""
    tasks = list(state._background_tasks)
    if not tasks:
        return []
    return _run(asyncio.gather(*tasks, return_exceptions=True))


def _counting_review(monkeypatch, summary="Proposed skill: staging-gate"):
    """Replace the shared review with a counting recorder. Patched on the module the
    watchdog resolves at call time, so the whole chain up to the review is exercised."""
    calls = []

    async def _fake(*, session_key, user_message, assistant_text, loaded_skills, completion=None):
        calls.append(
            {
                "session_key": session_key,
                "user_message": user_message,
                "assistant_text": assistant_text,
                "loaded_skills": loaded_skills,
            }
        )
        return summary

    monkeypatch.setattr(atr, "run_skill_ladder_review", _fake)
    return calls


def _create_completion(slug="staging-gate"):
    """A ladder completion that WOULD enqueue — so an empty queue is a guardrail, not a
    model that declined."""

    async def _c(prompt: str) -> str:
        return json.dumps(
            {
                "action": "create",
                "slug": slug,
                "description": "Always route a migration through staging first.",
                "triggers": "migration, deploy",
                "procedure_md": "1. migrate staging\n2. verify\n3. promote",
                "rationale": "the run re-derived this each cycle",
            }
        )

    return _c


# ── property 1: one synthesis call per RUN ───────────────────────────────────


class TestOncePerRun:
    def test_a_multi_cycle_run_reviews_once_at_the_end(self, monkeypatch):
        """The seam is END-of-run, not per-cycle. Four supervisor polls drive four cycles;
        exactly one of them may pay for the forked review."""
        calls = _counting_review(monkeypatch)
        c = _running(kind_config={"goal_type": "monitor"}, max_cycles=3)
        wd = _wd()
        key = f"loop-{c.id}"
        monkeypatch.setattr(W.manager, "session_key", lambda lid: f"loop-{lid}")
        wd._state._sessions[key] = object()
        for cycle in range(1, 5):
            _run(wd._poll_once())
            d = store.loop_dir(c.id)
            (d / "findings" / f"cycle_{cycle:03d}.json").write_text(
                json.dumps({"cycle": cycle, "new_findings_count": 1, "sources_checked": [cycle]})
            )
        _drain(wd._state)
        assert store.get(c.id).status == LoopStatus.COMPLETE.value
        assert len(calls) == 1, f"one review per run, got {len(calls)}"

    def test_a_second_complete_does_not_buy_a_second_review(self, monkeypatch):
        """`store.update_status`'s terminal guard rejects `terminal -> different`, so
        COMPLETE -> COMPLETE is permitted and `_complete` really can run twice for one
        loop. The second must not pay for a second forked model call."""
        calls = _counting_review(monkeypatch)
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="done", genuine=False))
        _run(wd._complete(c.id, reason="done again", genuine=False))
        _drain(wd._state)
        assert len(calls) == 1

    def test_complete_to_complete_is_really_reachable(self):
        """The premise of the guard above, measured rather than assumed: if this raised,
        the guard would be dead code."""
        c = _running()
        store.update_status(c.id, LoopStatus.COMPLETE)
        store.update_status(c.id, LoopStatus.COMPLETE)  # must not raise
        assert store.get(c.id).status == LoopStatus.COMPLETE.value

    def test_at_most_one_proposal_reaches_the_queue(self):
        """End-to-end through the REAL review: the queue holds one proposal, not one per
        tier and not one per cycle."""
        c = _running()
        (store.loop_dir(c.id) / "REPORT.md").write_text(
            "# Outcome\nStaged the migration, verified in staging, then promoted to prod."
        )
        summary = _run(_wd()._run_loop_end_ladder(c.id, [], completion=_create_completion()))
        assert summary
        assert len(proposals.list_pending()) == 1

    def test_complete_schedules_exactly_one_background_task(self, monkeypatch):
        _counting_review(monkeypatch)
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="", genuine=False))
        assert len(wd._state._background_tasks) == 1
        _drain(wd._state)


# ── property 2: an environment-failure run enqueues NOTHING ──────────────────


class TestEnvironmentFailureHygiene:
    """The guardrail is `_ladder_pass`'s `is_environment_failure_claim`, reached by feeding
    it the loop's REAL texts. These assert the QUEUE, because the verdict marker and the
    queue are two surfaces and only the queue is what a later turn would load."""

    def test_env_failure_in_the_deliverable_enqueues_nothing(self):
        c = _running()
        (store.loop_dir(c.id) / "REPORT.md").write_text(
            "# Outcome\nCould not finish: permission denied writing to the release bucket."
        )
        _run(_wd()._run_loop_end_ladder(c.id, [], completion=_create_completion()))
        assert proposals.list_pending() == []
        rec = proposals.last_review()
        assert rec is not None and rec["verdict"] == "env_failure_claim"

    def test_env_failure_in_the_goal_enqueues_nothing(self):
        c = _running(task="figure out why the deploy command failed with exit code 137")
        (store.loop_dir(c.id) / "REPORT.md").write_text("# Outcome\nRoot-caused and fixed.")
        _run(_wd()._run_loop_end_ladder(c.id, [], completion=_create_completion()))
        assert proposals.list_pending() == []

    def test_a_clean_run_with_the_same_completion_does_enqueue(self):
        """The control. Without it the two assertions above pass for a fixture whose model
        simply never proposes anything — an empty queue would mean nothing."""
        c = _running()
        (store.loop_dir(c.id) / "REPORT.md").write_text(
            "# Outcome\nStaged the migration, verified, promoted."
        )
        _run(_wd()._run_loop_end_ladder(c.id, [], completion=_create_completion()))
        assert len(proposals.list_pending()) == 1


# ── property 3: completion never waits on the model ──────────────────────────


class TestCompletionNeverWaitsOnTheModel:
    def test_a_hanging_review_does_not_delay_the_status_or_the_publish(self, monkeypatch):
        """The review blocks on an Event nothing sets. `_complete` must still return with
        the terminal status written and `complete` published — i.e. it is SCHEDULED, not
        awaited. If the call were awaited this test would hang, not fail."""
        gate = asyncio.Event()
        started = asyncio.Event()

        async def _hang(**kw):
            started.set()
            await gate.wait()
            return None

        monkeypatch.setattr(atr, "run_skill_ladder_review", _hang)
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="", genuine=False))
        assert store.get(c.id).status == LoopStatus.COMPLETE.value
        assert any(e[1] == "complete" for e in wd._state._sse.events)
        # let the scheduled task actually reach the hang, then release it
        _run(asyncio.sleep(0))
        gate.set()
        _drain(wd._state)
        assert started.is_set(), "the review must really have been scheduled, not skipped"

    def test_the_publish_follows_the_status_write(self, monkeypatch):
        _counting_review(monkeypatch)
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="", genuine=False))
        events = [e[1] for e in wd._state._sse.events]
        assert events[-1] == "complete"
        _drain(wd._state)


# ── property 4: nothing here wedges completion ───────────────────────────────


class TestNeverWedgesCompletion:
    def test_a_raising_review_still_completes_the_loop(self, monkeypatch):
        async def _boom(**kw):
            raise RuntimeError("forked model exploded")

        monkeypatch.setattr(atr, "run_skill_ladder_review", _boom)
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="", genuine=False))
        assert store.get(c.id).status == LoopStatus.COMPLETE.value
        assert [r for r in _drain(wd._state) if isinstance(r, BaseException)] == []

    def test_a_raising_config_load_still_completes_the_loop(self, monkeypatch):
        def _boom():
            raise OSError("config unreadable")

        monkeypatch.setattr(W.AppConfig, "load", staticmethod(_boom))
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="", genuine=False))
        assert store.get(c.id).status == LoopStatus.COMPLETE.value
        assert any(e[1] == "complete" for e in wd._state._sse.events)

    def test_a_state_with_no_background_tasks_is_not_an_error(self, monkeypatch):
        """A watchdog built on a state that predates `_background_tasks` (or a bare test
        harness) must degrade to "no review", not to a traceback in completion."""
        _counting_review(monkeypatch)
        c = _running()
        wd = W.LoopWatchdog(object(), _FakeSvc())
        _run(wd._complete(c.id, reason="", genuine=False))
        assert store.get(c.id).status == LoopStatus.COMPLETE.value

    def test_a_missing_loop_reviews_nothing(self, monkeypatch):
        calls = _counting_review(monkeypatch)
        assert _run(_wd()._run_loop_end_ladder("no-such-loop", [])) is None
        assert calls == []


# ── the gate: same hygiene as the chat path, answered before the texts ───────


class TestGate:
    def test_skill_ladder_flag_off_fires_nothing(self, home, monkeypatch):
        _write_config(home, skill_ladder=False)
        calls = _counting_review(monkeypatch)
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="", genuine=False))
        _drain(wd._state)
        assert calls == []
        assert store.get(c.id).status == LoopStatus.COMPLETE.value

    def test_learning_disabled_fires_nothing(self, home, monkeypatch):
        _write_config(home, enabled=False)
        calls = _counting_review(monkeypatch)
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="", genuine=False))
        _drain(wd._state)
        assert calls == []

    def test_run_end_cadence_off_fires_nothing(self, home, monkeypatch):
        _write_config(home, run_end_enabled=False)
        calls = _counting_review(monkeypatch)
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="", genuine=False))
        _drain(wd._state)
        assert calls == []

    def test_a_denied_gate_never_reads_the_run_text(self, home, monkeypatch):
        """The chat path checks `decision.allowed` BEFORE anything reads the turn content,
        because a restricted session promised its content feeds no learning. Same order
        here: when the gate denies, the run's outcome text is never composed."""
        _write_config(home, enabled=False)
        read = []
        monkeypatch.setattr(
            W.LoopWatchdog, "_loop_outcome_text", lambda self, loop: read.append(loop.id) or ""
        )
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="", genuine=False))
        _drain(wd._state)
        assert read == []

    def test_the_default_config_allows_the_review(self, monkeypatch):
        """Vacuity floor for the three gate tests above: with no config written the seam
        must FIRE, or they would pass against a permanently-dead pass."""
        calls = _counting_review(monkeypatch)
        c = _running()
        wd = _wd()
        _run(wd._complete(c.id, reason="", genuine=False))
        _drain(wd._state)
        assert len(calls) == 1


# ── the run's own texts (one deliverable resolution, shared) ─────────────────


class TestRunTexts:
    def test_the_goal_is_the_user_side_and_the_deliverable_is_the_outcome(self, monkeypatch):
        calls = _counting_review(monkeypatch)
        c = _running()
        (store.loop_dir(c.id) / "REPORT.md").write_text("# Outcome\nThe report body.")
        _run(_wd()._run_loop_end_ladder(c.id, ["deploy-flow"]))
        assert len(calls) == 1
        assert calls[0]["user_message"] == c.task
        assert "The report body." in calls[0]["assistant_text"]
        assert calls[0]["session_key"] == c.session_key
        assert calls[0]["loaded_skills"] == ["deploy-flow"]

    def test_the_deliverable_is_resolved_in_the_bound_workspace(self, monkeypatch, tmp_path):
        """Same workspace-first resolution the artifact graduation uses — shared, not a
        second copy that can drift."""
        ws = tmp_path / "ws"
        ws.mkdir()
        calls = _counting_review(monkeypatch)
        c = _running(
            workspace_dir=str(ws),
            kind_config={"goal_type": "open_ended", "primary_deliverable": "SPEC.md"},
        )
        (ws / "SPEC.md").write_text("# SPEC\nThe locked contract.")
        _run(_wd()._run_loop_end_ladder(c.id, []))
        assert "The locked contract." in calls[0]["assistant_text"]

    def test_no_deliverable_falls_back_to_the_planner_summary(self, monkeypatch):
        calls = _counting_review(monkeypatch)
        c = _running(kind_config={"goal_type": "verifiable", "verify_command": "true"})
        _run(_wd()._run_loop_end_ladder(c.id, []))
        assert calls[0]["assistant_text"] == "stage the migration, then promote"

    def test_a_runaway_deliverable_is_capped(self, monkeypatch):
        """A monitor's MONITOR_LOG.md grows without bound and this text becomes a prompt."""
        calls = _counting_review(monkeypatch)
        c = _running(kind_config={"goal_type": "monitor"})
        big = "x" * (W._LADDER_TEXT_LIMIT * 3)
        (store.loop_dir(c.id) / "MONITOR_LOG.md").write_text(big)
        _run(_wd()._run_loop_end_ladder(c.id, []))
        assert len(calls[0]["assistant_text"]) == W._LADDER_TEXT_LIMIT

    def test_a_goalless_loop_reviews_nothing(self, monkeypatch):
        calls = _counting_review(monkeypatch)
        c = _running(task="")
        assert _run(_wd()._run_loop_end_ladder(c.id, [])) is None
        assert calls == []
