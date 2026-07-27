"""§2.4 — the prose-model compressor (background paths only).

Contract: bounded summary; ANY model failure degrades to the deterministic ``log``
projector; the raw_ref recovery line survives every path; savings recorded under
the ``prose`` compressor key. It is never wired into project_output's synchronous
dispatch — locked here by asserting the module is absent from the projector table.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from personalclaw.tool_providers.prose_compress import compress_prose


def _isolate_store(tmp_path, monkeypatch):
    import personalclaw.config.loader as cfg

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)


@pytest.mark.asyncio
async def test_small_input_passes_through():
    assert await compress_prose("short text", cap=2000) == "short text"


@pytest.mark.asyncio
async def test_model_summary_used_when_it_succeeds(tmp_path, monkeypatch):
    _isolate_store(tmp_path, monkeypatch)
    long = "the quick brown fox jumps over the lazy dog. " * 500
    with patch(
        "personalclaw.llm_helpers.one_shot_completion",
        new=AsyncMock(return_value="A concise summary of fox activity."),
    ) as m:
        out = await compress_prose(long, cap=2000)
    assert out == "A concise summary of fox activity."
    m.assert_awaited_once()
    # the call went through the background use case (never the chat runtime)
    assert m.await_args.kwargs.get("use_case") == "background"


@pytest.mark.asyncio
async def test_model_failure_degrades_to_log_projector(tmp_path, monkeypatch):
    _isolate_store(tmp_path, monkeypatch)
    long = "line of prose\n" * 3000 + "ERROR the important bit\n" + "line of prose\n" * 100
    with patch(
        "personalclaw.llm_helpers.one_shot_completion",
        new=AsyncMock(side_effect=RuntimeError("no model")),
    ):
        out = await compress_prose(long, cap=2000)
    assert len(out) < len(long)
    assert "ERROR the important bit" in out  # the log projector kept the signal


@pytest.mark.asyncio
async def test_empty_summary_degrades(tmp_path, monkeypatch):
    _isolate_store(tmp_path, monkeypatch)
    long = "x words here\n" * 3000
    with patch("personalclaw.llm_helpers.one_shot_completion", new=AsyncMock(return_value="   ")):
        out = await compress_prose(long, cap=1000)
    assert out and len(out) < len(long)  # fallback produced a bounded result


@pytest.mark.asyncio
async def test_overlong_summary_treated_as_failure(tmp_path, monkeypatch):
    _isolate_store(tmp_path, monkeypatch)
    long = "y words here\n" * 3000
    with patch(
        "personalclaw.llm_helpers.one_shot_completion",
        new=AsyncMock(return_value="z" * 10_000),  # "summary" bigger than 2×cap
    ):
        out = await compress_prose(long, cap=1000)
    assert len(out) <= 1000 + 200  # deterministic fallback, bounded


@pytest.mark.asyncio
async def test_raw_ref_line_survives_both_paths(tmp_path, monkeypatch):
    _isolate_store(tmp_path, monkeypatch)
    long = "prose " * 3000
    with patch(
        "personalclaw.llm_helpers.one_shot_completion",
        new=AsyncMock(return_value="Summary."),
    ):
        out = await compress_prose(long, cap=2000, raw_ref="r_abc123def456")
    assert 'tool_result_get(result_id="r_abc123def456")' in out
    with patch(
        "personalclaw.llm_helpers.one_shot_completion",
        new=AsyncMock(side_effect=RuntimeError("down")),
    ):
        out = await compress_prose(long, cap=2000, raw_ref="r_abc123def456")
    assert 'tool_result_get(result_id="r_abc123def456")' in out


@pytest.mark.asyncio
async def test_savings_recorded_under_prose_key(tmp_path, monkeypatch):
    _isolate_store(tmp_path, monkeypatch)
    import personalclaw.tool_providers.savings as savings_mod

    monkeypatch.setattr(savings_mod, "config_dir", lambda: tmp_path)
    long = "words " * 5000
    with patch(
        "personalclaw.llm_helpers.one_shot_completion",
        new=AsyncMock(return_value="Short summary."),
    ):
        await compress_prose(long, cap=2000)
    data = savings_mod._load()
    assert any(k.endswith("|prose") for k in data.get("rows", {}))


def test_never_in_synchronous_dispatch():
    """The prose compressor must NOT be a project_output projector (the tool-dispatch
    path cannot await an LLM) — background callers invoke it explicitly."""
    from personalclaw.tool_providers.projection import _PROJECTORS

    assert "prose" not in _PROJECTORS
    for fn in _PROJECTORS.values():
        assert "prose" not in fn.__module__
