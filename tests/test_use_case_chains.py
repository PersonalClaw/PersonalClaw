"""MODEL-USE-CASES-V2 S1 — sovereign vocabulary + ordered fallback chains.

The stored value of every use case is an ordered CHAIN (position 0 = default,
1..n = fallbacks). `resolution_chain` composes [override?] + chain; the seam walk
skips a breaker-OPEN entry, skips an unbuildable entry only when a later entry
exists (the stale-pin rule re-scoped), and raises when the whole chain fails.
"""

from __future__ import annotations

import json

import pytest

from personalclaw.providers import use_cases as uc


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    return tmp_path


# ── T1.1: vocabulary ─────────────────────────────────────────────────────────


def test_vocabulary_has_all_five_subcategories():
    assert uc.CHAT_SUBCATEGORIES == (
        "code_tools",
        "reasoning",
        "background",
        "orchestration",
        "loops",
    )
    for sub in uc.CHAT_SUBCATEGORIES:
        assert sub in uc.VALID_USE_CASES
        assert uc.parent_capability(sub) == "chat"


def test_capabilities_are_their_own_parent():
    assert uc.parent_capability("chat") == "chat"
    assert uc.parent_capability("embedding") == "embedding"


# ── T1.2: tolerant reads + resolution_chain ──────────────────────────────────


class TestTolerantChainReads:
    def test_bare_string_value_reads_as_one_entry_chain(self, isolated_store):
        (isolated_store / "active_models.json").write_text(
            json.dumps({"chat": "native:some-model"})
        )
        active = uc.load_active_models()
        assert active["chat"] == ["native:some-model"]

    def test_list_values_read_verbatim_in_order(self, isolated_store):
        chain = ["native:a", "native:b", "native:c"]
        uc.save_active_models({"chat": chain})
        assert uc.load_active_models()["chat"] == chain

    def test_missing_keys_behave_as_today(self, isolated_store):
        assert uc.load_active_models() == {}
        assert uc.active_model_refs("chat") == []


class TestResolutionChain:
    def test_no_override_returns_chain(self, isolated_store):
        uc.save_active_models({"chat": ["native:a", "native:b"]})
        assert uc.resolution_chain("chat") == ["native:a", "native:b"]

    def test_override_prepends(self, isolated_store):
        uc.save_active_models({"chat": ["native:a", "native:b"]})
        assert uc.resolution_chain("chat", session_override="native:x") == [
            "native:x",
            "native:a",
            "native:b",
        ]

    def test_override_already_in_chain_keeps_front_position(self, isolated_store):
        uc.save_active_models({"chat": ["native:a", "native:b"]})
        assert uc.resolution_chain("chat", session_override="native:b") == [
            "native:b",
            "native:a",
        ]

    def test_unbound_subcategory_returns_parent_chat_chain(self, isolated_store):
        uc.save_active_models({"chat": ["native:a", "native:b"]})
        assert uc.resolution_chain("loops") == ["native:a", "native:b"]

    def test_bound_subcategory_uses_own_chain(self, isolated_store):
        uc.save_active_models({"chat": ["native:a"], "background": ["native:cheap"]})
        assert uc.resolution_chain("background") == ["native:cheap"]

    def test_override_with_empty_chain(self, isolated_store):
        assert uc.resolution_chain("chat", session_override="native:x") == ["native:x"]


# ── T1.3: the chain-aware seam walk ──────────────────────────────────────────


class TestChainWalk:
    """Drive resolve_provider_for_use_case through chains via a stubbed config
    registry — the walk logic (breaker skip, unbuildable skip, exhausted raise)
    is what's under test, not provider construction."""

    def _stub_registry(self, monkeypatch, buildable: dict[str, object]):
        """_resolve_from_config_registry returns buildable[provider_hint] or None.
        Also widens _known_provider_names so the test refs survive store pruning."""
        from personalclaw.providers import provider_bridge as pb

        def fake_resolve(capability, **kw):
            hint = kw.get("provider_hint", "")
            return buildable.get(hint)

        monkeypatch.setattr(pb, "_resolve_from_config_registry", fake_resolve)
        monkeypatch.setattr(
            uc,
            "_known_provider_names",
            lambda: {"p1", "p2", "p3", "dead", "dead1", "dead2"},
        )

    def _reset_breakers(self):
        from personalclaw.guardrails.breaker import reset_breakers

        reset_breakers()

    def test_first_entry_wins_when_buildable(self, isolated_store, monkeypatch):
        from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

        self._reset_breakers()
        uc.save_active_models({"reasoning": ["p1:m1", "p2:m2"]})
        sentinel1, sentinel2 = object(), object()
        self._stub_registry(monkeypatch, {"p1": sentinel1, "p2": sentinel2})
        assert resolve_provider_for_use_case("reasoning") is sentinel1

    def test_breaker_open_skips_to_next_entry(self, isolated_store, monkeypatch):
        from personalclaw.guardrails.breaker import get_breaker
        from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

        self._reset_breakers()
        uc.save_active_models({"reasoning": ["p1:m1", "p2:m2"]})
        sentinel2 = object()
        self._stub_registry(monkeypatch, {"p1": object(), "p2": sentinel2})
        b = get_breaker("p1")
        for _ in range(b.threshold):
            b.record_failure()
        assert b.is_open()
        assert resolve_provider_for_use_case("reasoning") is sentinel2
        self._reset_breakers()

    def test_breaker_open_on_last_entry_still_tries_it(self, isolated_store, monkeypatch):
        """A one-entry chain with an OPEN breaker still ATTEMPTS the build — skip
        only routes around an entry when a later one exists (never skip into the
        exhausted-raise when the provider might actually build)."""
        from personalclaw.guardrails.breaker import get_breaker
        from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

        self._reset_breakers()
        uc.save_active_models({"reasoning": ["p1:m1"]})
        sentinel = object()
        self._stub_registry(monkeypatch, {"p1": sentinel})
        b = get_breaker("p1")
        for _ in range(b.threshold):
            b.record_failure()
        assert resolve_provider_for_use_case("reasoning") is sentinel
        self._reset_breakers()

    def test_unbuildable_mid_chain_skips_with_later_entry(self, isolated_store, monkeypatch):
        from personalclaw.providers.provider_bridge import resolve_provider_for_use_case

        self._reset_breakers()
        uc.save_active_models({"reasoning": ["dead:m1", "p2:m2"]})
        sentinel2 = object()
        self._stub_registry(monkeypatch, {"p2": sentinel2})
        assert resolve_provider_for_use_case("reasoning") is sentinel2

    def test_one_entry_dead_chain_raises_stale_pin(self, isolated_store, monkeypatch):
        """The stale-pin rule is preserved: a single dead pinned ref still raises
        (never silently falls back past the user's selection)."""
        from personalclaw.providers.provider_bridge import (
            ProviderResolutionError,
            resolve_provider_for_use_case,
        )

        self._reset_breakers()
        uc.save_active_models({"reasoning": ["dead:m1"]})
        self._stub_registry(monkeypatch, {})
        with pytest.raises(ProviderResolutionError, match="dead"):
            resolve_provider_for_use_case("reasoning")

    def test_fully_dead_chain_raises(self, isolated_store, monkeypatch):
        from personalclaw.providers.provider_bridge import (
            ProviderResolutionError,
            resolve_provider_for_use_case,
        )

        self._reset_breakers()
        uc.save_active_models({"reasoning": ["dead1:m1", "dead2:m2"]})
        self._stub_registry(monkeypatch, {})
        with pytest.raises(ProviderResolutionError):
            resolve_provider_for_use_case("reasoning")


# ── T2.1-T2.3: consumer axes ─────────────────────────────────────────────────


class TestConsumerAxes:
    @pytest.mark.asyncio
    async def test_one_shot_ingestion_collapses_to_background(self):
        from unittest.mock import AsyncMock, patch

        from personalclaw import llm_helpers

        provider = AsyncMock()
        provider.start = AsyncMock()
        provider.shutdown = AsyncMock()
        with (
            patch(
                "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
                return_value=provider,
            ) as resolve,
            patch("personalclaw.llm_helpers.stream_and_collect", AsyncMock(return_value="ok")),
        ):
            out = await llm_helpers.one_shot_completion("hi", use_case="ingestion")
        assert out == "ok"
        assert resolve.call_args.args[0] == "background"

    @pytest.mark.asyncio
    async def test_one_shot_loops_axis_honored(self):
        from unittest.mock import AsyncMock, patch

        from personalclaw import llm_helpers

        provider = AsyncMock()
        with (
            patch(
                "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
                return_value=provider,
            ) as resolve,
            patch("personalclaw.llm_helpers.stream_and_collect", AsyncMock(return_value="ok")),
        ):
            await llm_helpers.one_shot_completion("hi", use_case="loops")
        assert resolve.call_args.args[0] == "loops"

    def test_loop_judges_resolve_the_judge_axis_not_the_worker_axis(self):
        """WF2LOO-17 inverts what this rail pins.

        It used to assert the judges resolved ``"loops"`` — the WORKER's axis — which
        is precisely the defect: a judge grading on the binding that produced the work
        it grades (correlated reviewer mistakes). All three judge call sites now resolve
        ``judge_use_case()`` (``loops.judge_use_case``, default ``reasoning``), and the
        worker's literal axis must not reappear at any of them.
        """
        from pathlib import Path

        from personalclaw.loop import gates, judge

        gates_src = Path(gates.__file__).read_text(encoding="utf-8")
        judge_src = Path(judge.__file__).read_text(encoding="utf-8")
        assert 'resolve_provider_for_use_case("loops")' not in gates_src
        assert 'resolve_provider_for_use_case("loops")' not in judge_src
        assert "resolve_provider_for_use_case(judge_use_case())" in gates_src
        # Both assess_cycle and assess_cycle_skeptic.
        assert judge_src.count("resolve_provider_for_use_case(judge_use_case())") == 2

    def test_background_session_factory_passes_axis(self):
        from pathlib import Path

        from personalclaw import session

        src = Path(session.__file__).read_text(encoding="utf-8")
        assert 'model_axis="background"' in src

    def test_model_less_subagent_spawn_passes_orchestration_axis(self):
        # Read the module FILE (inspect.getsource on the class raises "is a
        # built-in class" on CI's 3.12 when linecache misses under xdist).
        from pathlib import Path

        from personalclaw import subagent

        src = Path(subagent.__file__).read_text(encoding="utf-8")
        assert 'extra_kwargs["model_axis"] = "orchestration"' in src

    def test_guard_extends_to_all_noninteractive_axes(self, isolated_store, monkeypatch):
        """Resolving each non-interactive axis threads _guard_use_case (breaker +
        audit see the true axis)."""
        from personalclaw.providers import provider_bridge as pb

        seen: dict[str, str] = {}

        def fake_resolve(capability, **kw):
            seen[kw.get("provider_hint", "")] = kw.get("_guard_use_case", "")
            return object()

        monkeypatch.setattr(pb, "_resolve_from_config_registry", fake_resolve)
        monkeypatch.setattr(uc, "_known_provider_names", lambda: {"p1"})
        for axis in ("reasoning", "background", "loops", "orchestration"):
            seen.clear()
            uc.save_active_models({axis: ["p1:m1"]})
            pb.resolve_provider_for_use_case(axis)
            assert seen.get("p1") == axis, f"{axis} not guard-wrapped"


class TestInnerModelAxis:
    """The native runtime's INNER model resolves under the governing sub-category
    axis (the plan's flagged risk: it previously hardcoded "chat", making every
    sub-category binding cosmetic for native agents)."""

    def _capture_inner(self, monkeypatch):
        from personalclaw.providers import provider_bridge as pb

        captured: dict[str, str] = {}
        real = pb.resolve_provider_for_use_case

        def spy(use_case, **kw):
            if kw.get("_force_model_axis"):
                captured["inner_axis"] = use_case
                raise RuntimeError("stop after capture")
            return real(use_case, **kw)

        monkeypatch.setattr(pb, "resolve_provider_for_use_case", spy)
        return captured

    def test_code_tools_session_resolves_code_tools_chain(self, isolated_store, monkeypatch):
        from personalclaw.providers import provider_bridge as pb

        captured = self._capture_inner(monkeypatch)
        with pytest.raises(RuntimeError, match="stop after capture"):
            pb._build_native_runtime(
                use_case="code_tools",
                session_key="s",
                agent=None,
                model_override=None,
                cwd=None,
                model_axis="code_tools",
            )
        assert captured["inner_axis"] == "code_tools"

    def test_background_axis_threads_through(self, isolated_store, monkeypatch):
        from personalclaw.providers import provider_bridge as pb

        captured = self._capture_inner(monkeypatch)
        with pytest.raises(RuntimeError, match="stop after capture"):
            pb._build_native_runtime(
                use_case="chat",
                session_key="_bg",
                agent="personalclaw-lite",
                model_override=None,
                cwd=None,
                model_axis="background",
            )
        assert captured["inner_axis"] == "background"

    def test_no_axis_defaults_to_chat(self, isolated_store, monkeypatch):
        from personalclaw.providers import provider_bridge as pb

        captured = self._capture_inner(monkeypatch)
        with pytest.raises(RuntimeError, match="stop after capture"):
            pb._build_native_runtime(
                use_case="chat",
                session_key="s",
                agent=None,
                model_override=None,
                cwd=None,
            )
        assert captured["inner_axis"] == "chat"


# ── T2.4: call-failure chain advance in one_shot_completion ──────────────────


class TestCallFailureAdvance:
    def _providers(self, behaviors):
        """Build AsyncMock providers whose stream_and_collect behavior is keyed by
        the model_override ref they were resolved with."""
        from unittest.mock import AsyncMock

        built = {}
        for ref, result in behaviors.items():
            p = AsyncMock()
            p._ref = ref
            built[ref] = (p, result)
        return built

    @pytest.mark.asyncio
    async def test_entry0_failure_advances_to_entry1(self, isolated_store, monkeypatch):
        from unittest.mock import AsyncMock, patch

        from personalclaw import llm_helpers

        monkeypatch.setattr(uc, "_known_provider_names", lambda: {"p1", "p2"})
        uc.save_active_models({"background": ["p1:m1", "p2:m2"]})

        providers = self._providers({"p1:m1": RuntimeError("boom"), "p2:m2": "recovered"})

        def fake_resolve(use_case, **kw):
            return providers[kw["model_override"]][0]

        async def fake_stream(provider, prompt):
            result = providers[provider._ref][1]
            if isinstance(result, Exception):
                raise result
            return result

        with (
            patch(
                "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
                side_effect=fake_resolve,
            ),
            patch(
                "personalclaw.llm_helpers.stream_and_collect", AsyncMock(side_effect=fake_stream)
            ),
        ):
            out = await llm_helpers.one_shot_completion("hi", use_case="background")
        assert out == "recovered"

    @pytest.mark.asyncio
    async def test_whole_chain_failure_surfaces_one_error(self, isolated_store, monkeypatch):
        from unittest.mock import AsyncMock, patch

        from personalclaw import llm_helpers

        monkeypatch.setattr(uc, "_known_provider_names", lambda: {"p1", "p2"})
        uc.save_active_models({"background": ["p1:m1", "p2:m2"]})

        providers = self._providers(
            {"p1:m1": RuntimeError("boom1"), "p2:m2": RuntimeError("boom2")}
        )

        def fake_resolve(use_case, **kw):
            return providers[kw["model_override"]][0]

        async def fake_stream(provider, prompt):
            raise providers[provider._ref][1]

        with (
            patch(
                "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
                side_effect=fake_resolve,
            ),
            patch(
                "personalclaw.llm_helpers.stream_and_collect", AsyncMock(side_effect=fake_stream)
            ),
            pytest.raises(RuntimeError, match="fallback chain failed"),
        ):
            await llm_helpers.one_shot_completion("hi", use_case="background")

    @pytest.mark.asyncio
    async def test_output_contract_error_does_not_advance(self, isolated_store, monkeypatch):
        """A schema miss means the model RESPONDED — never burn the fallback chain
        on it (the chain exists for provider outages)."""
        from unittest.mock import AsyncMock, patch

        from personalclaw import llm_helpers
        from personalclaw.guardrails.failure import OutputContractError

        monkeypatch.setattr(uc, "_known_provider_names", lambda: {"p1", "p2"})
        uc.save_active_models({"background": ["p1:m1", "p2:m2"]})
        calls: list[str] = []

        def fake_resolve(use_case, **kw):
            from unittest.mock import AsyncMock

            calls.append(kw["model_override"])
            return AsyncMock()

        with (
            patch(
                "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
                side_effect=fake_resolve,
            ),
            patch(
                "personalclaw.llm_helpers.stream_and_collect",
                AsyncMock(return_value="not json"),
            ),
            pytest.raises(OutputContractError),
        ):
            await llm_helpers.one_shot_completion("hi", use_case="background", output_type=dict)
        assert calls == ["p1:m1"]  # entry 1 never tried

    @pytest.mark.asyncio
    async def test_single_entry_chain_takes_plain_path(self, isolated_store, monkeypatch):
        """A one-entry chain behaves exactly as the single binding always did (no
        wrapper, no advance)."""
        from unittest.mock import AsyncMock, patch

        from personalclaw import llm_helpers

        monkeypatch.setattr(uc, "_known_provider_names", lambda: {"p1"})
        uc.save_active_models({"background": ["p1:m1"]})
        with (
            patch(
                "personalclaw.providers.provider_bridge.resolve_provider_for_use_case",
                return_value=AsyncMock(),
            ) as resolve,
            patch("personalclaw.llm_helpers.stream_and_collect", AsyncMock(return_value="ok")),
        ):
            out = await llm_helpers.one_shot_completion("hi", use_case="background")
        assert out == "ok"
        # plain path: resolved once with NO model_override
        assert resolve.call_count == 1
        assert not resolve.call_args.kwargs.get("model_override")


# ── T1.4: the PUT accepts ordered chains for every use case ──────────────────


class TestChainPut:
    @pytest.fixture
    def client_app(self):
        from aiohttp import web

        from personalclaw.dashboard.handlers.model_registry import api_models_active_set

        app = web.Application()
        app.router.add_put("/api/models/active/{use_case}", api_models_active_set)
        return app

    @pytest.mark.asyncio
    async def test_multi_entry_chain_persists_in_order(
        self, isolated_store, client_app, monkeypatch
    ):
        from aiohttp.test_utils import TestClient, TestServer

        monkeypatch.setattr(uc, "_known_provider_names", lambda: {"p1", "p2", "p3"})
        async with TestClient(TestServer(client_app)) as c:
            resp = await c.put(
                "/api/models/active/reasoning",
                json={"models": ["p1:m1", "p2:m2", "p3:m3"]},
            )
            assert resp.status == 200
        assert uc.load_active_models()["reasoning"] == ["p1:m1", "p2:m2", "p3:m3"]

    @pytest.mark.asyncio
    async def test_chain_length_capped(self, isolated_store, client_app, monkeypatch):
        from aiohttp.test_utils import TestClient, TestServer

        monkeypatch.setattr(uc, "_known_provider_names", lambda: {"p1"})
        async with TestClient(TestServer(client_app)) as c:
            resp = await c.put(
                "/api/models/active/reasoning",
                json={"models": [f"p1:m{i}" for i in range(21)]},
            )
            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_unknown_provider_prefix_still_rejected(
        self, isolated_store, client_app, monkeypatch
    ):
        from aiohttp.test_utils import TestClient, TestServer

        monkeypatch.setattr(uc, "_known_provider_names", lambda: {"p1"})
        async with TestClient(TestServer(client_app)) as c:
            resp = await c.put(
                "/api/models/active/reasoning",
                json={"models": ["p1:m1", "ghost:m2"]},
            )
            assert resp.status == 400
