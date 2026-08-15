"""WF2LOO-18 — the loops stall detector no longer depends on the worker's own report.

`check_stagnation` used to read ONE field, `new_findings_count`, which the worker writes,
and defaulted it to `1` ("progressing") when absent. A worker reporting any nonzero count
was immune to stagnation detection forever, and a worker that never wrote the field was
immune by default — so a confidently-looping worker producing fresh junk tripped nothing
(`_MAX_CONSECUTIVE_ERRORS` only counts turn FAILURES).

These rails hold three properties:

* two signals the worker CANNOT author — byte-identical cycle content, and an identical
  set of recorded calls/sources — stall a loop even while it claims five new findings
  every cycle,
* the self-report is KEPT (it is the cheapest signal and informative when honest) but is
  no longer sufficient and no longer a veto, and its ABSENCE no longer reads as progress,
* the window is a real config knob wired through all four points — including a live PATCH,
  because `test_config_roundtrip.py` cannot see the `_EDITABLE_CONFIG` allowlist (deleting
  that entry leaves it green), and the patched value must reach the detector.

Both detectors delegate to the WORKFLOW engine's rules rather than re-deriving them
(`resilience.check_breaker`'s byte-identical-output detection and
`loop_middleware.call_fingerprint`); `TestReusesTheEngineRules` pins that, so a future
re-implementation on the loops side goes red instead of quietly drifting.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.loop import manager, store
from personalclaw.loop import watchdog as W
from personalclaw.loop.loop import Loop, LoopStatus

# ── harness ──────────────────────────────────────────────────────────────────


class _FakeSession:
    def __init__(self, key):
        self.key = key
        self.messages = []
        self._trust = True

    @property
    def running(self):
        return False


class _FakeState:
    def __init__(self):
        self._sessions = {}
        self.notes = []
        from personalclaw.dashboard.sse import SseRegistry

        self._sse = SseRegistry()

    def loop_sse(self):
        return self._sse

    def push_refresh(self, *kinds):
        pass

    def notify(self, kind, title, body, *, meta=None):
        self.notes.append((kind, title, body))


class _FakeNudge:
    def __init__(self, session_name):
        self.id, self.session_name, self.active, self.cycle_count = "N1", session_name, True, 0


class _FakeSvc:
    def __init__(self):
        self._loops = {}

    def get_by_session(self, session_name):
        return self._loops.setdefault(session_name, _FakeNudge(session_name))

    async def remove(self, loop_id):
        pass


@pytest.fixture
def cfg_file(tmp_path, monkeypatch):
    """An isolated config.json (never the real home) the watchdog reads its window from."""
    path = tmp_path / "config.json"

    def _write(loops: dict | None = None) -> None:
        body: dict = {"agents": {}, "default_agent": "personalclaw"}
        if loops is not None:
            body["loops"] = loops
        path.write_text(json.dumps(body), encoding="utf-8")

    _write({})
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: path)
    return path, _write


@pytest.fixture
def loop_home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.loop.store.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def attention(monkeypatch):
    """Capture the inbox item a stall raises — and keep the real inbox store out of it."""
    raised: list[dict] = []

    def _emit(_state, **kw):
        raised.append(kw)

    monkeypatch.setattr("personalclaw.inbox.emit_attention_item", _emit)
    return raised


class _Driver:
    """Drive the REAL watchdog poll over a real loop dir: write a finding, poll, repeat."""

    def __init__(self, *, kind_config=None, seed=True):
        self.wd = W.LoopWatchdog(_FakeState(), _FakeSvc())
        loop = store.create(
            Loop(
                id="",
                name="L",
                kind="goal",
                task="find the regression",
                kind_config=kind_config or {"goal_type": "open_ended"},
                idle_secs=120,
                max_cycles=50,
            )
        )
        store.update_status(loop.id, LoopStatus.RUNNING)
        self.id = loop.id
        key = manager.session_key(self.id)
        self.wd._state._sessions[key] = _FakeSession(key)
        self.events: list[tuple[str, dict]] = []
        self.wd._state.loop_sse().publish = lambda _k, ev, data: self.events.append((ev, data))
        if seed:
            asyncio.run(self.wd._poll_once())  # seed liveness

    async def acycle(self, **finding) -> None:
        """One worker cycle: write the finding, then let the supervisor observe it. Async so
        an already-running loop (the PATCH rail's test client) can drive it too."""
        n = len(store.get_findings(self.id)) + 1
        d = store.loop_dir(self.id)
        (d / "findings" / f"cycle_{n:03d}.json").write_text(json.dumps({"cycle": n, **finding}))
        await self.wd._poll_once()

    def cycle(self, **finding) -> None:
        asyncio.run(self.acycle(**finding))

    @property
    def status(self) -> str:
        return store.get(self.id).status

    def reason(self) -> str:
        return next((d.get("reason", "") for ev, d in self.events if ev == "stagnant"), "")


def _findings(n: int, **finding) -> list[dict]:
    return [{"cycle": i, **finding} for i in range(1, n + 1)]


# ── the signals the worker cannot author ─────────────────────────────────────


class TestWorkerCannotAuthorProgress:
    def test_identical_content_stalls_a_loop_claiming_five_new_findings(
        self, loop_home, cfg_file, attention
    ):
        """The atom's acceptance case: the worker reports a nonzero count EVERY cycle while
        emitting byte-identical content. Under the old detector this loop ran forever."""
        d = _Driver()
        for _ in range(W.DEFAULT_STAGNATION_WINDOW):
            d.cycle(
                new_findings_count=5,
                summary="Investigated the latency regression.",
                key_insight="It is the cache.",
                evidence="handlers.py:88",
            )
        assert d.status == LoopStatus.STAGNANT.value
        assert "byte-identical" in d.reason()
        assert [a["dedup_key"] for a in attention] == [f"loop:{d.id}:stagnant"]

    def test_fresh_content_keeps_the_loop_running(self, loop_home, cfg_file, attention):
        """The discriminator: the detector must not stall a loop that is doing real work.
        Without this, a detector that always trips would pass the test above."""
        d = _Driver()
        for i in range(W.DEFAULT_STAGNATION_WINDOW + 3):
            d.cycle(
                new_findings_count=1,
                summary=f"Ruled out cause {i}.",
                sources_checked=[f"https://example.test/{i}"],
            )
        assert d.status == LoopStatus.RUNNING.value
        assert attention == []

    def test_identical_sources_stall_a_reworded_report(self, loop_home, cfg_file, attention):
        """Content hashing alone misses the worker that re-words its prose every cycle while
        re-reading the same three pages. The call fingerprints catch it."""
        d = _Driver()
        for i in range(W.DEFAULT_STAGNATION_WINDOW):
            d.cycle(
                new_findings_count=3,
                summary=f"Cycle {i}: still reviewing the same material, freshly phrased.",
                sources_checked=["https://a.test", "https://b.test"],
            )
        assert d.status == LoopStatus.STAGNANT.value
        assert "same sources" in d.reason()

    def test_source_order_is_not_progress(self):
        """Re-reading the same pages in a different order is the same work — the fingerprint
        set is order-independent, so shuffling cannot buy another window of cycles."""
        rotations = [["a", "b", "c"], ["c", "a", "b"], ["b", "c", "a"], ["a", "c", "b"]]
        recent = [
            {"cycle": i, "summary": f"reworded {i}", "sources_checked": rotations[i % 4]}
            for i in range(4)
        ]
        assert "same sources" in W.check_stagnation(recent, window=4)

    def test_a_kind_recording_no_calls_is_not_stalled_by_an_empty_window(
        self, loop_home, cfg_file, attention
    ):
        """Vacuity guard. `design`/`general`/`sdlc` findings carry no call records, so every
        cycle's fingerprint list is empty — and an all-empty window is trivially "identical".
        Treating that as a stall would stall every loop of those kinds on cycle N."""
        d = _Driver()
        for i in range(W.DEFAULT_STAGNATION_WINDOW + 2):
            d.cycle(summary=f"Advanced the design: step {i}.", key_insight=f"k{i}")
        assert d.status == LoopStatus.RUNNING.value

    def test_a_monitor_goal_still_never_stagnates(self, loop_home, cfg_file, attention):
        """The kind-level exemption is untouched: a quiet cycle is a valid no-op for a
        monitor, even byte-identically quiet."""
        d = _Driver(kind_config={"goal_type": "monitor"})
        for _ in range(W.DEFAULT_STAGNATION_WINDOW + 2):
            d.cycle(new_findings_count=0, summary="Nothing changed.")
        assert d.status == LoopStatus.RUNNING.value


# ── the self-report: kept, no longer sufficient ──────────────────────────────


class TestSelfReportIsKeptButNotSufficient:
    def test_an_honest_zero_still_stalls(self):
        """The cheap first signal is KEPT. Content differs every cycle here, so only the
        self-report can produce this verdict — deleting it would red this test."""
        recent = _findings(5, new_findings_count=0)
        recent = [{**f, "summary": f"looked again ({f['cycle']})"} for f in recent]
        assert W.check_stagnation(recent) == "the worker reported no new findings for 5 cycles"

    def test_absence_no_longer_reads_as_progress(self):
        """The defect: `f.get("new_findings_count", 1)` made SILENCE mean "progressing", so
        a worker that stopped reporting was immune. One explicit zero plus four silent
        cycles (with differing content) now stalls; it did not before."""
        recent = [{"cycle": 1, "summary": "s1", "new_findings_count": 0}] + [
            {"cycle": i, "summary": f"s{i}"} for i in range(2, 6)
        ]
        assert W.check_stagnation(recent) == "the worker reported no new findings for 5 cycles"

    def test_a_field_the_kind_never_writes_does_not_stall_on_its_own(self):
        """The other half of "absence": silence must not be evidence of a stall EITHER, or
        every kind that never prompts for the count (design/general/sdlc) stalls at cycle N
        while making real progress. Silence is no claim — the content signal decides."""
        recent = [{"cycle": i, "summary": f"real work {i}"} for i in range(1, 6)]
        assert W.check_stagnation(recent) == ""

    def test_a_written_null_count_is_still_a_claim_of_nothing(self):
        """`{"new_findings_count": null}` was read as 0 by the old `or 0`. It still is —
        this change only strengthens detection, it never weakens a case that already
        tripped."""
        recent = [{"cycle": i, "summary": f"s{i}", "new_findings_count": None} for i in range(1, 6)]
        assert W.check_stagnation(recent)

    def test_an_unparseable_count_is_no_claim_rather_than_progress(self):
        """ "many" is not a number. It must not resolve to "progressing" (the old default
        would have), and it cannot alone establish a stall either."""
        recent = [{"cycle": i, "summary": f"s{i}", "new_findings_count": "many"} for i in range(5)]
        assert W.check_stagnation(recent) == ""
        claimed = [{**f, "new_findings_count": "many", "summary": "same"} for f in recent]
        assert "byte-identical" in W.check_stagnation(claimed)

    def test_a_claimed_count_cannot_veto_the_content_signal(self):
        """The old rule let ANY nonzero count end the check. The worker's claim is now
        consulted last and only when the two observed signals found nothing."""
        for claim in (1, 5, 999):
            recent = _findings(5, new_findings_count=claim, summary="identical", evidence="e")
            assert "byte-identical" in W.check_stagnation(recent), claim

    def test_the_counter_is_excluded_from_the_content_hash(self):
        """A worker cannot buy immunity by incrementing a counter beside unchanged output —
        `new_findings_count` and `cycle` are bookkeeping, not work product."""
        recent = [
            {"cycle": i, "new_findings_count": i, "summary": "identical", "evidence": "e"}
            for i in range(1, 6)
        ]
        assert "byte-identical" in W.check_stagnation(recent)


# ── reuse, not re-implementation ─────────────────────────────────────────────


class TestReusesTheEngineRules:
    def test_content_identity_is_decided_by_the_engine_breaker(self, monkeypatch):
        """The atom requires the byte-identical rule be REUSED, not re-derived. Spy on
        `resilience.check_breaker`: if a future edit inlines the comparison on the loops
        side, the two rules can drift apart and this goes red."""
        from personalclaw.workflows import resilience

        seen: list[str] = []
        real = resilience.check_breaker

        def _spy(node, state):
            seen.append(node.id)
            return real(node, state)

        monkeypatch.setattr(resilience, "check_breaker", _spy)
        assert "byte-identical" in W.check_stagnation(_findings(5, summary="same"))
        assert seen == ["loop:content"], seen

    def test_call_identity_uses_the_engine_fingerprint(self, monkeypatch):
        from personalclaw.workflows import loop_middleware

        seen: list[str] = []
        real = loop_middleware.call_fingerprint

        def _spy(tool, args):
            seen.append(tool)
            return real(tool, args)

        monkeypatch.setattr(loop_middleware, "call_fingerprint", _spy)
        recent = [
            {"cycle": i, "summary": f"reworded {i}", "sources_checked": ["u1"]} for i in range(5)
        ]
        assert "same sources" in W.check_stagnation(recent)
        assert set(seen) == {"sources_checked"}, seen


# ── the window is a real config field ────────────────────────────────────────


class TestWindowIsConfigurable:
    def test_load_clamps_and_fails_safe(self, cfg_file):
        from personalclaw.config.loader import AppConfig

        _path, write = cfg_file
        assert AppConfig.load().loops.stagnation_window == 5
        for raw, expect in ((3, 3), (1, 2), (0, 2), (999, 50), ("nonsense", 5), (None, 5)):
            write({"stagnation_window": raw})
            assert AppConfig.load().loops.stagnation_window == expect, raw

    def test_a_shorter_window_stalls_the_loop_sooner(self, loop_home, cfg_file, attention):
        """Point 2 driven through the watchdog: the value in config.json is what decides
        when the loop stalls. A hardcoded window would ignore it and stay RUNNING here."""
        _path, write = cfg_file
        write({"stagnation_window": 3})
        d = _Driver()
        d.cycle(new_findings_count=4, summary="same")
        d.cycle(new_findings_count=4, summary="same")
        assert d.status == LoopStatus.RUNNING.value, "stalled BEFORE the configured window"
        d.cycle(new_findings_count=4, summary="same")
        assert d.status == LoopStatus.STAGNANT.value
        assert "3x" in d.reason()

    def test_a_longer_window_buys_more_cycles(self, loop_home, cfg_file, attention):
        _path, write = cfg_file
        write({"stagnation_window": 8})
        d = _Driver()
        for _ in range(7):
            d.cycle(new_findings_count=4, summary="same")
        assert d.status == LoopStatus.RUNNING.value
        d.cycle(new_findings_count=4, summary="same")
        assert d.status == LoopStatus.STAGNANT.value

    def test_the_field_is_declared_with_ui_metadata(self):
        """Point 1: the dataclass field carries `_meta`, so the generated config surface can
        label it rather than showing a bare key."""
        from dataclasses import fields

        from personalclaw.config.loader import LoopsConfig

        meta = {f.name: f.metadata for f in fields(LoopsConfig)}["stagnation_window"]
        assert meta.get("label") == "Stagnation Window"
        assert "2" in str(meta.get("help", "")), "the floor belongs in the help text"


class TestPatchRail:
    """Point 4 driven END TO END: write via the real PATCH handler, read the value back off
    DISK, reload AppConfig, and then prove the patched window reaches the detector.
    `test_config_roundtrip.py` cannot see this entry — deleting it leaves that file green."""

    @staticmethod
    def _app() -> web.Application:
        from personalclaw.dashboard.handlers import api_personalclaw_config_patch

        app = web.Application()
        app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
        return app

    @pytest.mark.asyncio
    async def test_patch_persists_and_reloads(self, cfg_file) -> None:
        from personalclaw.config.loader import AppConfig

        cfg_path, _write = cfg_file
        assert AppConfig.load().loops.stagnation_window == 5
        async with TestClient(TestServer(self._app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw", json={"path": "loops.stagnation_window", "value": 3}
            )
            assert resp.status == 200, await resp.text()
        on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert on_disk["loops"]["stagnation_window"] == 3
        assert AppConfig.load().loops.stagnation_window == 3

    @pytest.mark.asyncio
    async def test_patch_refuses_a_window_below_the_floor(self, cfg_file) -> None:
        cfg_path, _write = cfg_file
        async with TestClient(TestServer(self._app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw", json={"path": "loops.stagnation_window", "value": 1}
            )
            assert resp.status == 400
        assert "stagnation_window" not in json.loads(cfg_path.read_text(encoding="utf-8")).get(
            "loops", {}
        )

    @pytest.mark.asyncio
    async def test_a_patched_window_reaches_the_running_watchdog(
        self, loop_home, cfg_file, attention
    ) -> None:
        """The seam between the write path and the reader. Verifying them separately leaves
        exactly the shape WF2LOO-17 measured: a live reader of a key nothing can set."""
        async with TestClient(TestServer(self._app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw", json={"path": "loops.stagnation_window", "value": 2}
            )
            assert resp.status == 200, await resp.text()
        d = _Driver(seed=False)
        await d.wd._poll_once()  # seed liveness
        await d.acycle(new_findings_count=9, summary="same")
        assert d.status == LoopStatus.RUNNING.value
        await d.acycle(new_findings_count=9, summary="same")
        assert d.status == LoopStatus.STAGNANT.value
        assert "2x" in d.reason()
