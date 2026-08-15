"""WF2LOO-17 — the loop judge's MODEL BINDING is independent of the worker it grades.

`loop/judge.assess_cycle`'s docstring has always claimed a third-party check "stronger
than the worker's model", but every judge call site resolved `resolve_provider_for_use_case
("loops")` — and `loops` is the LOOP WORKER's own axis. The judge was independent in
session and prompt and *correlated in model*: the exact "correlated reviewer mistakes"
failure mode, and the opposite of reserving the strongest model for judgment.

These rails hold the claim true:

* the judge axis is its own config field (`loops.judge_use_case`, default `reasoning`),
* all three judge call sites resolve THAT axis, never the worker's,
* the rails discriminate — they go red when the field is repointed at `loops`, so a
  configuration where both axes happen to resolve alike cannot make them pass vacuously,
* the degraded path is untouched: a judge whose provider cannot start still returns
  None (defer, NEVER a false complete) and still logs WARNING,
* and the config field is wired through all four points — including a REAL PATCH,
  because `test_config_roundtrip.py` cannot see the `_EDITABLE_CONFIG` allowlist
  (its allowlist assertions are hardcoded to `evals.*` keys, so deleting this entry
  leaves that file 6/6 green — point 4 needs its own rail or it ships unverified).
"""

import json
import logging
from dataclasses import fields
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

# The two entries a worker axis and a judge axis resolve to. They MUST differ — every
# independence assertion below is meaningless if the fixture hands both axes one entry.
WORKER_ENTRY = "worker-model-weak"
JUDGE_ENTRY = "judge-model-strong"
_AXIS_ENTRIES = {"loops": WORKER_ENTRY, "reasoning": JUDGE_ENTRY}


class _FakeProvider:
    """A provider that remembers which axis entry resolved it."""

    def __init__(self, entry: str) -> None:
        self.entry = entry

    async def start(self):
        pass

    async def shutdown(self):
        pass

    async def reject_tool(self, _rid):
        pass

    async def respond_permission(self, _event, allow=False):
        pass

    async def stream(self, prompt):
        from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK

        class _E:
            def __init__(self, kind, text=""):
                self.kind = kind
                self.text = text
                self.request_id = None

        yield _E(
            EVENT_TEXT_CHUNK,
            '{"done": false, "done_reason": "not yet", "marginal_value": 2, '
            '"quality_score": 3, "regressed": false}',
        )
        yield _E(EVENT_COMPLETE)


@pytest.fixture
def axis_recorder(monkeypatch):
    """Record every axis handed to the provider bridge; resolve it to a distinct entry.

    Mirrors real resolution semantics at the layer under test: an axis maps to an
    active-model ENTRY, and two different axes may map to two different entries.
    """
    seen: list[str] = []

    def _resolve(use_case, **_kwargs):
        seen.append(use_case)
        return _FakeProvider(_AXIS_ENTRIES.get(use_case, f"{use_case}-unmapped"))

    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.resolve_provider_for_use_case", _resolve
    )
    return seen


@pytest.fixture
def tmp_cfg(tmp_path):
    """An isolated config.json (never the real home) whose `loops` block is writable."""
    cfg_path = tmp_path / "config.json"

    def _write(loops: dict | None = None) -> None:
        body: dict = {"agents": {}, "default_agent": "personalclaw"}
        if loops is not None:
            body["loops"] = loops
        cfg_path.write_text(json.dumps(body), encoding="utf-8")

    _write({})
    with patch("personalclaw.config.loader.config_path", return_value=cfg_path):
        yield cfg_path, _write


async def _drive_judges() -> None:
    """Drive all three judge call sites with their production (default) factories."""
    from personalclaw.loop import gates as gates_mod
    from personalclaw.loop import judge as judge_mod

    verdict = await judge_mod.assess_cycle("goal", "dod", {"cycle": 1, "summary": "s"}, [])
    assert verdict is not None, "primary judge did not complete"
    skeptic = await judge_mod.assess_cycle_skeptic("goal", "dod", {"cycle": 1, "summary": "s"}, [])
    assert skeptic is not None, "skeptic judge did not complete"
    raw = await gates_mod.judge_verdict("PASS or FAIL?")
    assert raw, "gate judge produced no text"


# ── Independence ─────────────────────────────────────────────────────────────


class TestJudgeBindingIsIndependentOfWorkerBinding:
    def test_worker_and_judge_axes_resolve_to_different_entries(self, tmp_path, monkeypatch):
        """Ground the premise in the REAL store: `loops` and `reasoning` are separately
        bindable, so "different entries" is a fact about the product, not a test fiction."""
        from personalclaw.providers import use_cases as uc

        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        (tmp_path / "active_models.json").write_text(
            json.dumps({"loops": [WORKER_ENTRY], "reasoning": [JUDGE_ENTRY]}), encoding="utf-8"
        )
        assert uc.active_model_refs("loops") == [WORKER_ENTRY]
        assert uc.active_model_refs("reasoning") == [JUDGE_ENTRY]
        assert uc.active_model_refs("loops") != uc.active_model_refs("reasoning")

    @pytest.mark.asyncio
    async def test_all_three_judge_call_sites_resolve_the_judge_entry(
        self, axis_recorder, tmp_cfg
    ) -> None:
        """assess_cycle / assess_cycle_skeptic / gates.judge_verdict each resolve the
        JUDGE axis. The worker's entry must never be handed to a judge."""
        assert WORKER_ENTRY != JUDGE_ENTRY, "vacuous fixture: both axes map to one entry"
        await _drive_judges()
        assert len(axis_recorder) == 3, f"expected 3 judge resolutions, saw {axis_recorder}"
        assert axis_recorder == ["reasoning", "reasoning", "reasoning"], axis_recorder
        assert "loops" not in axis_recorder, "a judge is still riding the WORKER's axis"
        resolved = {_AXIS_ENTRIES[a] for a in axis_recorder}
        assert resolved == {JUDGE_ENTRY}
        assert WORKER_ENTRY not in resolved

    @pytest.mark.asyncio
    async def test_rail_discriminates_when_judge_is_pinned_to_the_worker_axis(
        self, axis_recorder, tmp_cfg
    ) -> None:
        """The proof that the rail above is not a constant: pin `loops.judge_use_case`
        to the worker's axis and the SAME drive resolves the worker's entry. So the
        assertion tracks the field, and would go red if a call site regressed."""
        _cfg_path, write = tmp_cfg
        write({"judge_use_case": "loops"})
        await _drive_judges()
        assert axis_recorder == ["loops", "loops", "loops"], axis_recorder
        assert {_AXIS_ENTRIES[a] for a in axis_recorder} == {WORKER_ENTRY}

    def test_judge_use_case_helper_reads_config(self, tmp_cfg) -> None:
        from personalclaw.loop.judge import judge_use_case

        _cfg_path, write = tmp_cfg
        write({})
        assert judge_use_case() == "reasoning"
        write({"judge_use_case": "code_tools"})
        assert judge_use_case() == "code_tools"

    def test_unknown_axis_falls_back_to_reasoning_not_the_worker_axis(self, tmp_cfg) -> None:
        """Fail-SAFE, not fail-open: a typo must not silently hand judgment back to the
        binding that produced the work."""
        from personalclaw.loop.judge import judge_use_case

        _cfg_path, write = tmp_cfg
        for bad in ("not-an-axis", "", "   ", "LOOPS"):
            write({"judge_use_case": bad})
            assert judge_use_case() == "reasoning", bad


# ── Degraded path: provably unchanged ────────────────────────────────────────


class TestDegradedPathUnchanged:
    """A judge whose provider cannot start must still defer (None / "") and still log
    WARNING. The axis change must not convert a can't-judge into a false complete."""

    @pytest.fixture
    def dead_bridge(self, monkeypatch):
        def _boom(use_case, **_kwargs):
            raise RuntimeError(f"no provider for {use_case}")

        monkeypatch.setattr(
            "personalclaw.providers.provider_bridge.resolve_provider_for_use_case", _boom
        )

    @pytest.mark.asyncio
    async def test_primary_judge_defers_and_warns(self, dead_bridge, tmp_cfg, caplog) -> None:
        from personalclaw.loop import judge as judge_mod

        with caplog.at_level(logging.WARNING, logger="personalclaw.loop.judge"):
            verdict = await judge_mod.assess_cycle("goal", "dod", {"cycle": 1, "summary": "s"}, [])
        assert verdict is None  # defer — NEVER a false complete
        msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("degraded" in m for m in msgs), msgs
        # and the WARNING names the binding to go check, so the degradation is diagnosable
        assert any("reasoning" in m and "loops.judge_use_case" in m for m in msgs), msgs

    @pytest.mark.asyncio
    async def test_skeptic_judge_defers_and_warns(self, dead_bridge, tmp_cfg, caplog) -> None:
        from personalclaw.loop import judge as judge_mod

        with caplog.at_level(logging.WARNING, logger="personalclaw.loop.judge"):
            verdict = await judge_mod.assess_cycle_skeptic(
                "goal", "dod", {"cycle": 1, "summary": "s"}, []
            )
        assert verdict is None
        assert any(
            "no refutation available" in r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )

    @pytest.mark.asyncio
    async def test_gate_judge_returns_empty_and_warns(self, dead_bridge, tmp_cfg, caplog) -> None:
        from personalclaw.loop import gates as gates_mod

        with caplog.at_level(logging.WARNING, logger="personalclaw.loop.gates"):
            raw = await gates_mod.judge_verdict("PASS or FAIL?")
        assert raw == ""
        assert any(
            "judge provider unavailable" in r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )
        # "" is neither a pass nor a rendered verdict — a dead judge cannot advance a stage
        assert gates_mod.verdict_is_pass(raw) is False
        assert gates_mod.verdict_rendered(raw) is False

    @pytest.mark.asyncio
    async def test_config_unreadable_still_yields_reasoning_not_a_crash(self, monkeypatch) -> None:
        """The helper degrades, it does not raise: an unreadable config must not take the
        judge out of the loop entirely (which would strand every cycle unassessed)."""
        from personalclaw.loop.judge import judge_use_case

        def _boom():
            raise OSError("config unreadable")

        monkeypatch.setattr("personalclaw.config.loader.config_path", _boom)
        assert judge_use_case() == "reasoning"


# ── The four config wiring points ────────────────────────────────────────────


class TestConfigWiring:
    def test_point_1_dataclass_and_meta(self) -> None:
        from personalclaw.config.loader import LoopsConfig

        f = {x.name: x for x in fields(LoopsConfig)}["judge_use_case"]
        assert f.default == "reasoning"
        assert f.metadata.get("label")
        assert f.metadata.get("help")

    def test_point_2_load_reads_it(self, tmp_cfg) -> None:
        from personalclaw.config.loader import AppConfig

        _cfg_path, write = tmp_cfg
        write({"judge_use_case": "orchestration"})
        assert AppConfig.load().loops.judge_use_case == "orchestration"
        write({})
        assert AppConfig.load().loops.judge_use_case == "reasoning"

    def test_point_3_to_dict_emits_it(self, tmp_cfg) -> None:
        from personalclaw.config.loader import AppConfig

        _cfg_path, write = tmp_cfg
        write({"judge_use_case": "code_tools"})
        assert AppConfig.load().to_dict()["loops"]["judge_use_case"] == "code_tools"

    def test_point_4_editable_config_allowlist_entry(self) -> None:
        """The allowlist entry itself. `test_config_roundtrip.py` cannot see this —
        its `_EDITABLE_CONFIG` assertions are hardcoded to `evals.*` keys, so deleting
        this entry leaves that file fully green."""
        from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG

        spec = _EDITABLE_CONFIG.get("loops.judge_use_case")
        assert spec is not None, "loops.judge_use_case is not PATCH-able"
        assert spec["type"] == "enum"
        assert "reasoning" in spec["values"]
        # Every offered value must be a real use case, else the picker offers a
        # value load() will silently replace with `reasoning`.
        from personalclaw.providers.use_cases import VALID_USE_CASES

        assert set(spec["values"]) <= set(VALID_USE_CASES)


class TestPatchRail:
    """Point 4 driven END TO END: write via the real PATCH handler, read the value back
    off DISK, and reload AppConfig from it. A live reader of a key no write path can set
    is the worst shape of "wired" — this is what catches it."""

    @staticmethod
    def _app() -> web.Application:
        from personalclaw.dashboard.handlers import api_personalclaw_config_patch

        app = web.Application()
        app.router.add_patch("/api/config/personalclaw", api_personalclaw_config_patch)
        return app

    @pytest.mark.asyncio
    async def test_patch_persists_and_reloads(self, tmp_cfg) -> None:
        from personalclaw.config.loader import AppConfig

        cfg_path, _write = tmp_cfg
        assert AppConfig.load().loops.judge_use_case == "reasoning"
        async with TestClient(TestServer(self._app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw",
                json={"path": "loops.judge_use_case", "value": "code_tools"},
            )
            assert resp.status == 200, await resp.text()
        on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
        assert on_disk["loops"]["judge_use_case"] == "code_tools"
        assert AppConfig.load().loops.judge_use_case == "code_tools"

    @pytest.mark.asyncio
    async def test_patch_rejects_a_non_use_case(self, tmp_cfg) -> None:
        cfg_path, _write = tmp_cfg
        async with TestClient(TestServer(self._app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw",
                json={"path": "loops.judge_use_case", "value": "stt"},
            )
            assert resp.status == 400
        assert "judge_use_case" not in json.loads(cfg_path.read_text(encoding="utf-8")).get(
            "loops", {}
        )

    @pytest.mark.asyncio
    async def test_patched_value_reaches_the_judge_call_sites(self, axis_recorder, tmp_cfg) -> None:
        """The full round trip: a PATCH written through the API changes which axis the
        judges actually resolve. Without this, points 4 and the call sites are verified
        separately and the seam between them is untested."""
        _cfg_path, _write = tmp_cfg
        async with TestClient(TestServer(self._app())) as c:
            resp = await c.patch(
                "/api/config/personalclaw",
                json={"path": "loops.judge_use_case", "value": "loops"},
            )
            assert resp.status == 200, await resp.text()
        await _drive_judges()
        assert axis_recorder == ["loops", "loops", "loops"], axis_recorder
