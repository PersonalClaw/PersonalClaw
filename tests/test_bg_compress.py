"""Background compression service (Context Economy §4).

At-rest, idle, persistent chats get topic-compressed on the maintenance cadence — for the
MODEL: the oldest tier becomes one summary, the middle tier capped turns, the recent tier
verbatim, all in a record beside the transcript. The transcript itself is never written.
Incognito/temporary chats are skipped; the kill switch is honored; the model's view is
deterministic for identical input (prefix stability).
"""

from __future__ import annotations

import os
import time

import pytest

from personalclaw import bg_compress
from personalclaw.history import ConversationLog, model_view, span_digest


async def _fake_prose(text, *, cap=2000, raw_ref=""):
    """Deterministic stand-in for the LLM summarizer."""
    return f"[summary of {len(text)} chars]"


@pytest.fixture(autouse=True)
def _patch_prose(monkeypatch):
    # bg_compress imports compress_prose lazily inside _summarize_oldest; patch the source.
    monkeypatch.setattr(
        "personalclaw.tool_providers.prose_compress.compress_prose", _fake_prose, raising=True
    )
    monkeypatch.setattr(bg_compress, "_record_savings", lambda *a, **k: None, raising=True)


def _big_session(log: ConversationLog, key: str, *, topics: int = 4, per_topic: int = 6):
    """A multi-topic transcript large enough to clear the size floor."""
    for t in range(topics):
        for i in range(per_topic):
            log.append(key, "user", f"topic {t} question {i} " + ("x" * 300))
            log.append(key, "assistant", f"topic {t} answer {i} " + ("y" * 300))
    return key


def _make_log(tmp_path):
    return ConversationLog(base_dir=tmp_path / "sessions")


def _age(log: ConversationLog, key: str, days: float = 30) -> None:
    old = time.time() - days * 86400
    os.utime(log._path(key), (old, old))


@pytest.mark.asyncio
async def test_compress_session_writes_a_record_and_never_the_transcript(tmp_path):
    log = _make_log(tmp_path)
    _big_session(log, "s1")
    before = log._path("s1").read_bytes()

    result = await bg_compress.compress_session(log, "s1", embed_fn=None)

    assert result is not None
    assert result["chars_out"] < result["chars_in"]
    assert log._path("s1").read_bytes() == before, "the transcript must not be written"
    record = log.read_summary("s1")
    assert record is not None and record["summary"].startswith("The first ")
    view = model_view(log.read_messages("s1"), record)
    assert view[0]["role"] == "summary"
    assert sum(len(m["content"]) for m in view) == result["chars_out"]
    assert not (log._dir / "archive").exists(), "nothing is archived: nothing was dropped"


@pytest.mark.asyncio
async def test_small_session_untouched(tmp_path):
    log = _make_log(tmp_path)
    log.append("s1", "user", "tiny")
    log.append("s1", "assistant", "reply")
    result = await bg_compress.compress_session(log, "s1", embed_fn=None)
    assert result is None  # below the size floor
    assert log.read_summary("s1") is None


@pytest.mark.asyncio
async def test_raw_ref_preserved_through_summary(tmp_path):
    log = _make_log(tmp_path)
    # An old assistant turn carries a projected result's recovery handle.
    log.append("s1", "user", "run it " + "x" * 400)
    log.append(
        "s1",
        "assistant",
        'done. full result: tool_result_get(result_id="r_abc123def456") ' + "y" * 400,
    )
    for t in range(18):
        log.append("s1", "user", f"more {t} " + "x" * 300)
        log.append("s1", "assistant", f"ok {t} " + "y" * 300)
    assert await bg_compress.compress_session(log, "s1", embed_fn=None) is not None
    view = model_view(log.read_messages("s1"), log.read_summary("s1"))
    # The span holding the handle is summarized — and the summary names the handle.
    assert view[0]["role"] == "summary"
    assert 'tool_result_get(result_id="r_abc123def456")' in view[0]["content"]


@pytest.mark.asyncio
async def test_pass_skips_incognito_and_active(tmp_path):
    log = _make_log(tmp_path)
    # persistent + idle → eligible
    _big_session(log, "keep")
    # incognito → skipped even when idle+large
    _big_session(log, "secret")
    log.update_metadata("secret", {"memory_mode": "incognito"})
    # active (fresh mtime) → skipped
    _big_session(log, "active")

    # Age the two we want eligible/skipped-for-idle far into the past; leave "active" fresh.
    for k in ("keep", "secret"):
        _age(log, k)
    log._meta_cache.clear()

    stats = await bg_compress.run_bg_compression_pass(log, embed_fn=None, max_sessions=10)
    touched = {s["key"] for s in stats}
    assert "keep" in touched
    assert "secret" not in touched  # incognito
    assert "active" not in touched  # not idle
    assert log.read_summary("secret") is None


@pytest.mark.asyncio
async def test_a_current_record_is_not_summarized_again(tmp_path, monkeypatch):
    log = _make_log(tmp_path)
    _big_session(log, "s1")
    _age(log, "s1")
    calls: list[str] = []

    async def _counting(text, *, cap=2000, raw_ref=""):
        calls.append(text)
        return "[summary]"

    monkeypatch.setattr("personalclaw.tool_providers.prose_compress.compress_prose", _counting)
    first = await bg_compress.run_bg_compression_pass(log, embed_fn=None)
    second = await bg_compress.run_bg_compression_pass(log, embed_fn=None)

    assert [s["key"] for s in first] == ["s1"]
    assert second == [], "an unchanged chat with a current record costs nothing"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_record_whose_chat_is_gone_is_pruned(tmp_path):
    log = _make_log(tmp_path)
    _big_session(log, "s1")
    assert await bg_compress.compress_session(log, "s1", embed_fn=None) is not None
    # Removed by something other than delete_session (a subagent cleanup, a restore).
    log._path("s1").unlink()

    await bg_compress.run_bg_compression_pass(log, embed_fn=None)

    assert not log.summary_path("s1").exists()


def test_the_window_keeps_the_summary_it_would_cut(tmp_path):
    log = _make_log(tmp_path)
    _big_session(log, "s1")
    messages = log.read_messages("s1")
    record = {
        "summary": "S",
        "summarized": 4,
        "reduced": 8,
        "reduced_cap": 600,
        "digest": span_digest(messages[:8]),
    }
    log.write_summary("s1", record)

    window = log.history_for_model("s1", 5)

    assert len(window) == 5
    assert window[0] == {"role": "summary", "content": "S"}
    assert [m["content"] for m in window[1:]] == [m["content"] for m in messages[-4:]]


@pytest.mark.asyncio
async def test_kill_switch_stops_pass(tmp_path, monkeypatch):
    log = _make_log(tmp_path)
    _big_session(log, "s1")
    _age(log, "s1")
    log._meta_cache.clear()

    class _Tools:
        bg_compress_enabled = False
        bg_compress_idle_days = 7.0

    class _Cfg:
        tools = _Tools()

    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", staticmethod(lambda: _Cfg()))
    stats = await bg_compress.run_bg_compression_pass(log, embed_fn=None)
    assert stats == []
    assert log.read_summary("s1") is None


@pytest.mark.asyncio
async def test_prefix_stability_deterministic(tmp_path):
    """Invariant 2: compressing the same transcript twice from identical inputs yields a
    byte-identical view for the model (the summary is deterministic here)."""
    log_a = ConversationLog(base_dir=tmp_path / "a")
    log_b = ConversationLog(base_dir=tmp_path / "b")
    for log in (log_a, log_b):
        _big_session(log, "s1")
    await bg_compress.compress_session(log_a, "s1", embed_fn=None)
    await bg_compress.compress_session(log_b, "s1", embed_fn=None)
    a = log_a.history_for_model("s1", 100)
    b = log_b.history_for_model("s1", 100)
    assert a == b
    assert a[0]["role"] == "summary"
