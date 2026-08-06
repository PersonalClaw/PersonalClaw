"""MODEL-ROUTING-TELEMETRY §1.3 / MRT-1c — the rolling routing-stats fold.

The router must not scan model_calls.jsonl per call, so routing_stats.json is an incremental
EMA fold keyed (use_case -> query_class -> ref), updated post-attempt by the audit path and
rebuildable from the JSONL. These lock the fold math (EMA, first-sample seed, score collapse
without feedback), the skip of unclassified rows, and the rebuild.
"""

from __future__ import annotations

import json

from personalclaw.routing import stats


def _row(**kw):
    base = dict(
        use_case="reasoning",
        query_class="summarize",
        provider="ollama-models",
        model="qwen3:8b",
        passed=True,
        latency_ms=2000.0,
        dollars_est=0.0,
    )
    base.update(kw)
    return base


class TestFoldMath:
    def test_first_sample_seeds_observed_values(self):
        s = {"use_cases": {}}
        stats.fold_record(s, _row(passed=True, latency_ms=2100.0), now="t")
        row = s["use_cases"]["reasoning"]["summarize"]["ollama-models:qwen3:8b"]
        assert row["n"] == 1 and row["success_rate"] == 1.0 and row["avg_ms"] == 2100.0

    def test_ema_blends_subsequent_samples(self):
        s = {"use_cases": {}}
        stats.fold_record(s, _row(passed=True, latency_ms=2000.0), now="t")
        stats.fold_record(s, _row(passed=False, latency_ms=4000.0), now="t")
        row = s["use_cases"]["reasoning"]["summarize"]["ollama-models:qwen3:8b"]
        assert row["n"] == 2
        # EMA(alpha=0.2): 0.8*1.0 + 0.2*0.0 = 0.8 ; 0.8*2000 + 0.2*4000 = 2400
        assert row["success_rate"] == 0.8 and row["avg_ms"] == 2400.0

    def test_score_collapses_onto_success_without_feedback(self):
        s = {"use_cases": {}}
        stats.fold_record(s, _row(passed=True), now="t")
        row = s["use_cases"]["reasoning"]["summarize"]["ollama-models:qwen3:8b"]
        # feedback_n=0 → score == success_rate (not 0.6*success), so an unrated ref isn't docked.
        assert row["feedback_n"] == 0 and row["score"] == row["success_rate"] == 1.0

    def test_ref_uses_active_models_spelling_with_colon_model(self):
        s = {"use_cases": {}}
        stats.fold_record(s, _row(provider="ollama-models", model="gpt-oss:20b"), now="t")
        assert "ollama-models:gpt-oss:20b" in s["use_cases"]["reasoning"]["summarize"]

    def test_unclassified_rows_are_skipped(self):
        s = {"use_cases": {}}
        stats.fold_record(s, _row(query_class=""), now="t")  # no class → can't attribute
        stats.fold_record(s, _row(use_case=""), now="t")  # no use_case
        assert s["use_cases"] == {}


class TestPersistence:
    def test_load_missing_is_empty_fold(self, tmp_path):
        s = stats.load_stats(tmp_path)
        assert s["use_cases"] == {} and s["version"] == stats.STATS_VERSION

    def test_record_routing_stats_round_trips(self, tmp_path):
        stats.record_routing_stats(_row(), home=tmp_path, now="t")
        s = stats.load_stats(tmp_path)
        assert s["use_cases"]["reasoning"]["summarize"]["ollama-models:qwen3:8b"]["n"] == 1

    def test_corrupt_file_degrades_to_empty(self, tmp_path):
        (tmp_path / "routing_stats.json").write_text("{not json", encoding="utf-8")
        assert stats.load_stats(tmp_path)["use_cases"] == {}


class TestRebuild:
    def test_rebuild_from_jsonl(self, tmp_path):
        audit = tmp_path / "model_calls.jsonl"
        rows = [
            _row(passed=True, latency_ms=2000.0),
            _row(passed=True, latency_ms=2200.0),
            {
                "use_case": "chat",
                "query_class": "short_chat",
                "provider": "P",
                "model": "m",
                "passed": True,
                "latency_ms": 100.0,
                "dollars_est": 0.001,
            },
            {"garbage": True},  # tolerated
        ]
        audit.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        folded = stats.rebuild(tmp_path, audit_path=audit)
        assert folded == 3  # the 3 classifiable rows; garbage skipped
        s = stats.load_stats(tmp_path)
        assert s["use_cases"]["reasoning"]["summarize"]["ollama-models:qwen3:8b"]["n"] == 2
        assert "short_chat" in s["use_cases"]["chat"]

    def test_rebuild_missing_jsonl_writes_empty(self, tmp_path):
        assert stats.rebuild(tmp_path, audit_path=tmp_path / "nope.jsonl") == 0
        assert stats.load_stats(tmp_path)["use_cases"] == {}


class TestLiveHookThroughGuard:
    """The audit path folds the same attempt into routing_stats.json (MRT-1c wiring)."""

    def test_a_guarded_call_folds_into_stats(self, tmp_path, monkeypatch):
        monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
        from personalclaw.guardrails.model_call import ModelCallGuard
        from personalclaw.llm.base import EVENT_COMPLETE
        from tests.test_guardrails_model_call import FakeProvider

        guard = ModelCallGuard(
            FakeProvider(text="ok"),
            use_case="reasoning",
            provider_name="ollama-models",
            model="qwen3:8b",
        )

        async def _drive():
            await guard.start()
            agen = guard.stream("summarize this, tl;dr")  # classifies as "summarize"
            async for ev in agen:
                if ev.kind == EVENT_COMPLETE:
                    break
            await agen.aclose()

        import asyncio

        asyncio.get_event_loop().run_until_complete(_drive())
        s = stats.load_stats(tmp_path)
        assert "ollama-models:qwen3:8b" in s["use_cases"]["reasoning"]["summarize"]
