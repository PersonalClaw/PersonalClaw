"""Regenerating an answer keeps every earlier answer, each whole, as its versions.

Measured before this was written (dogfood, a scratch twin with a model that answers with text, one
tool call, then more text): after ten Regenerates the answer's switcher still read 2/2, and the
previous version was one paragraph, the text after the call. The versions are kept on the answer's
FIRST message (the first segment a fresh answer settles takes them, ``chat_runner._flush_segment``),
but Regenerate read them from the answer's LAST message, which a tool-using answer never holds them
on, and kept that message's text alone as the answer it replaced. So each Regenerate dropped every
earlier answer and kept one paragraph of the last.

Now Regenerate reads the versions from the message that holds them, and keeps the answer it
replaces as its whole text, every segment of it, in the place the answer had among its versions.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

pytestmark = pytest.mark.asyncio


async def _stashed(tmp_path, monkeypatch, answer: list[tuple[str, str, dict]]) -> list[str]:
    """Regenerate a chat whose last answer is *answer* (role, content, extra fields); the versions
    it keeps for the fresh answer, as their texts."""
    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: tmp_path)
    state = _make_state(tmp_path)
    session = state.get_or_create_session("s1")
    session.append("user", "Read my notes, please.")
    for role, content, extra in answer:
        meta = extra.pop("meta", None)
        session.append(role, content, **({"meta": meta} if meta else {}))
        session.messages[-1].update(extra)
    session.drain()
    stashed: list[dict] = []

    async def _capture(_state, _session, user_msg, regenerate_hint="", **kw):
        stashed.extend(list(session._pending_variants))

    with patch("personalclaw.dashboard.chat_regenerate.run_chat", new=_capture):
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/regenerate")
            assert resp.status == 200, await resp.text()
            await asyncio.sleep(0)
    return [v["content"] for v in stashed]


async def test_a_tool_using_answer_is_kept_whole_beside_every_earlier_one(tmp_path, monkeypatch):
    """🔴 Red before: the earlier answer was dropped, and the one replaced kept only its last
    paragraph."""
    kept = await _stashed(
        tmp_path,
        monkeypatch,
        [
            (
                "assistant",
                "Looking.",
                {
                    "variants": [
                        {"content": "An earlier answer.", "ts": "t0"},
                        {"content": "Looking.", "ts": "t1"},
                    ],
                    "variant_idx": 1,
                },
            ),
            (
                "tool",
                "read_file",
                {
                    "meta": {
                        "tool_call_id": "t1",
                        "kind": "read",
                        "input": "{}",
                        "done": True,
                        "output": "ok",
                    }
                },
            ),
            ("assistant", "Found it.", {}),
        ],
    )

    assert kept == ["An earlier answer.", "Looking.\n\nFound it."]


async def test_an_answer_of_one_message_keeps_its_versions_as_before(tmp_path, monkeypatch):
    kept = await _stashed(
        tmp_path,
        monkeypatch,
        [
            (
                "assistant",
                "Third.",
                {
                    "variants": [
                        {"content": "First."},
                        {"content": "Second."},
                        {"content": "Third."},
                    ],
                    "variant_idx": 2,
                },
            ),
        ],
    )

    assert kept == ["First.", "Second.", "Third."]
