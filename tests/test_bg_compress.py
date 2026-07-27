"""Background compression service (Context Economy §4).

At-rest, idle, persistent sessions get topic-compressed on the maintenance cadence:
oldest tier prose-summarized, middle tier reduced, recent tier verbatim; every
dropped span archived (reason=bg_compress); incognito/temporary skipped; kill-switch
honored; prefix-stability invariants held.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from personalclaw import bg_compress
from personalclaw.history import ConversationLog, _archive_dir


async def _fake_prose(text, *, cap=2000, raw_ref=""):
    """Deterministic stand-in for the LLM summarizer."""
    s = f"[summary of {len(text)} chars]"
    if raw_ref:
        s += f'\n[full output: tool_result_get(result_id="{raw_ref}")]'
    return s


@pytest.fixture(autouse=True)
def _patch_prose(monkeypatch):
    monkeypatch.setattr(bg_compress, "compress_prose", _fake_prose, raising=False)
    # bg_compress imports compress_prose lazily inside _summarize_oldest; patch the source.
    monkeypatch.setattr(
        "personalclaw.tool_providers.prose_compress.compress_prose", _fake_prose, raising=True
    )


def _big_session(log: ConversationLog, key: str, *, topics: int = 4, per_topic: int = 6):
    """A multi-topic transcript large enough to clear the size floor."""
    for t in range(topics):
        for i in range(per_topic):
            log.append(key, "user", f"topic {t} question {i} " + ("x" * 300))
            log.append(key, "assistant", f"topic {t} answer {i} " + ("y" * 300))
    return key


def _make_log(tmp_path):
    return ConversationLog(base_dir=tmp_path / "sessions")


@pytest.mark.asyncio
async def test_compress_session_shrinks_and_archives(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.history.config_dir", lambda: tmp_path, raising=False)
    log = _make_log(tmp_path)
    _big_session(log, "s1")
    before = log._read_messages("s1")
    chars_before = sum(len(m.get("content", "")) for m in before)

    result = await bg_compress.compress_session(log, "s1", embed_fn=None)
    assert result is not None
    assert result["chars_out"] < result["chars_in"] == chars_before

    after = log._read_messages("s1")
    chars_after = sum(len(m.get("content", "")) for m in after)
    assert chars_after < chars_before
    # A bg_compress summary message is present.
    assert any(m.get("cls") == "bg_compress_summary" for m in after)
    # Dropped lines were archived under reason=bg_compress (reversibility).
    adir = _archive_dir(log._dir)
    archives = list(adir.glob("*.jsonl")) if adir.exists() else []
    assert archives, "expected an archive file"
    header = json.loads(archives[0].read_text().splitlines()[0])
    assert header["reason"] == "bg_compress"


@pytest.mark.asyncio
async def test_small_session_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.history.config_dir", lambda: tmp_path, raising=False)
    log = _make_log(tmp_path)
    log.append("s1", "user", "tiny")
    log.append("s1", "assistant", "reply")
    result = await bg_compress.compress_session(log, "s1", embed_fn=None)
    assert result is None  # below the size floor


@pytest.mark.asyncio
async def test_raw_ref_preserved_through_summary(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.history.config_dir", lambda: tmp_path, raising=False)
    log = _make_log(tmp_path)
    # An old assistant turn carries a projected result's recovery handle.
    log.append("s1", "user", "run it " + "x" * 400)
    log.append(
        "s1",
        "assistant",
        'done. full result: tool_result_get(result_id="r_abc123def456") ' + "y" * 400,
    )
    for t in range(4):
        log.append("s1", "user", f"more {t} " + "x" * 300)
        log.append("s1", "assistant", f"ok {t} " + "y" * 300)
    await bg_compress.compress_session(log, "s1", embed_fn=None)
    after = log._read_messages("s1")
    blob = "\n".join(m.get("content", "") for m in after)
    # The raw_ref survives somewhere (summary refs line or an untouched recent turn).
    assert "r_abc123def456" in blob


@pytest.mark.asyncio
async def test_pass_skips_incognito_and_active(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.history.config_dir", lambda: tmp_path, raising=False)
    log = _make_log(tmp_path)
    # persistent + idle → eligible
    _big_session(log, "keep")
    # incognito → skipped even when idle+large
    _big_session(log, "secret")
    log.update_metadata("secret", {"memory_mode": "incognito"})
    # active (fresh mtime) → skipped
    _big_session(log, "active")

    # Age the two we want eligible/skipped-for-idle far into the past; leave "active" fresh.
    old = time.time() - 30 * 86400
    for k in ("keep", "secret"):
        p = log._path(k)
        os.utime(p, (old, old))
    log._meta_cache.clear()

    monkeypatch.setattr(bg_compress, "_record_savings", lambda *a, **k: None, raising=True)
    stats = await bg_compress.run_bg_compression_pass(log, embed_fn=None, max_sessions=10)
    touched = {s["key"] for s in stats}
    assert "keep" in touched
    assert "secret" not in touched  # incognito
    assert "active" not in touched  # not idle


@pytest.mark.asyncio
async def test_kill_switch_stops_pass(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.history.config_dir", lambda: tmp_path, raising=False)
    log = _make_log(tmp_path)
    _big_session(log, "s1")
    old = time.time() - 30 * 86400
    os.utime(log._path("s1"), (old, old))
    log._meta_cache.clear()

    class _Tools:
        bg_compress_enabled = False
        bg_compress_idle_days = 7.0

    class _Cfg:
        tools = _Tools()

    monkeypatch.setattr("personalclaw.config.loader.AppConfig.load", staticmethod(lambda: _Cfg()))
    stats = await bg_compress.run_bg_compression_pass(log, embed_fn=None)
    assert stats == []


@pytest.mark.asyncio
async def test_prefix_stability_deterministic(tmp_path, monkeypatch):
    """Invariant 2: compressing the same transcript twice from identical inputs
    yields byte-identical rebuilt content (the summary is deterministic here)."""
    monkeypatch.setattr("personalclaw.history.config_dir", lambda: tmp_path, raising=False)
    log_a = ConversationLog(base_dir=tmp_path / "a")
    log_b = ConversationLog(base_dir=tmp_path / "b")
    for log in (log_a, log_b):
        _big_session(log, "s1")
    await bg_compress.compress_session(log_a, "s1", embed_fn=None)
    await bg_compress.compress_session(log_b, "s1", embed_fn=None)
    a = [m.get("content") for m in log_a._read_messages("s1")]
    b = [m.get("content") for m in log_b._read_messages("s1")]
    assert a == b
