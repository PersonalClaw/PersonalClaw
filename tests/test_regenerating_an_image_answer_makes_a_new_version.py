"""Regenerate on an answer that made an image saves the retake as that image's next version.

Regenerate replays the turn, and the model generates its image again from a prompt it
rewrites. ``image_generate`` named the retake after the new prompt, so the library gained a
second image at v1 beside the first — where the user asked for another take of THIS image,
and the detail page should have read v2 with the first kept as v1.

The regenerate route now records the images the replaced answer made, the tool lands the
turn's generation on them in order, and the record ends with the replayed turn.
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_app, _make_state

from personalclaw.artifacts import retakes
from personalclaw.image_gen.provider import ImageGenModel, ImageGenProvider, ImageResult

SESSION = "dashboard:s1"


class _Painter(ImageGenProvider):
    """A fake image model that paints each call a different solid colour."""

    def __init__(self):
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake"

    async def is_available(self) -> bool:
        return True

    async def list_models(self):
        return [ImageGenModel(name="fake-1")]

    async def generate(self, prompt, *, model="", size="", n=1, **opts):
        self.calls += 1
        png = b"\x89PNG\r\n\x1a\n" + f"colour-{self.calls}".encode()
        return [ImageResult(b64=base64.b64encode(png).decode(), mime="image/png")]

    async def edit(self, prompt, *, source_image, mask="", model="", size="", n=1, **opts):
        return await self.generate(prompt)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def painter_library(home, monkeypatch):
    """The artifact library and a painter; the tool resolves its session as it does in use."""
    from personalclaw.artifacts import native
    from personalclaw.artifacts import registry as art_reg
    from personalclaw.image_gen import registry as ig_reg

    prov = native.NativeArtifactProvider(root=home / "artifacts")
    monkeypatch.setattr(art_reg, "get_provider", lambda name="native": prov)
    painter = _Painter()
    monkeypatch.setattr(ig_reg, "active_image_gen", lambda: (painter, "fake-1"))
    return prov


@pytest.fixture
def library(painter_library, monkeypatch):
    """The same, with every tool call made in one fixed session."""
    monkeypatch.setattr("personalclaw.mcp_artifacts._resolve_session_key", lambda: SESSION)
    return painter_library


def _generate(prompt: str) -> str:
    from personalclaw.mcp_artifacts import _call_tool_inner

    return _call_tool_inner("image_generate", {"prompt": prompt})


# ── the tool ────────────────────────────────────────────────────────────────


def test_a_retaken_image_lands_as_its_next_version(library):
    _generate("A flat illustration for an opening slide")
    (first,) = library.list(kind="image")
    retakes.open_retakes(SESSION, [first.slug])

    out = _generate("A minimalist flat illustration for an opening slide")

    images = library.list(kind="image")
    assert [a.slug for a in images] == [first.slug], "the retake made a second image"
    assert images[0].version == 2
    assert f"version 2 (slug: {first.slug})" in out
    assert f"/api/artifacts/{first.slug}/raw?version=2" in out
    data, _mime = library.raw_bytes(first.slug)
    assert data.endswith(b"colour-2"), "v2 holds the retake, not the first image"


def test_without_a_retake_a_generation_is_a_new_image(library):
    _generate("A red bicycle")
    _generate("A blue kettle")
    assert len(library.list(kind="image")) == 2


def test_a_retake_is_used_once(library):
    _generate("A lighthouse")
    (first,) = library.list(kind="image")
    retakes.open_retakes(SESSION, [first.slug])

    _generate("A lighthouse at dusk")
    _generate("And a harbour beside it")

    by_slug = {a.slug: a.version for a in library.list(kind="image")}
    assert by_slug[first.slug] == 2
    assert len(by_slug) == 2, "a second generation in the replayed turn is its own image"


def test_a_retake_of_an_image_since_deleted_makes_a_new_one(library):
    retakes.open_retakes(SESSION, ["an-image-that-was-deleted"])
    out = _generate("A garden")
    (only,) = library.list(kind="image")
    assert only.version == 1 and only.slug != "an-image-that-was-deleted"
    assert "Generated image" in out


# ── the record ──────────────────────────────────────────────────────────────


def test_a_record_left_by_a_gateway_that_is_gone_is_not_taken(home, monkeypatch):
    """A crash mid-regenerate leaves the record behind; a later turn must not retake."""
    retakes.open_retakes(SESSION, ["an-image"])
    monkeypatch.setattr(retakes, "pid_is_alive", lambda pid: False)
    assert retakes.take_retake(SESSION) == ""
    monkeypatch.setattr(retakes, "pid_is_alive", lambda pid: True)
    assert retakes.take_retake(SESSION) == "", "the stale record is gone, not just skipped"


def test_closing_ends_the_record(home):
    retakes.open_retakes(SESSION, ["one", "two"])
    assert retakes.take_retake(SESSION) == "one"
    retakes.close_retakes(SESSION)
    assert retakes.take_retake(SESSION) == ""


def test_the_images_an_answer_made_are_read_from_its_tool_rows():
    from personalclaw.mcp_artifacts import images_made_in

    rows = [
        {"role": "assistant", "content": "Here it is."},
        {
            "role": "tool",
            "meta": {"output": "Generated image 'A mug' (slug: a-mug) via fake:fake-1.\n\n…"},
        },
        {"role": "tool", "meta": {"output": "Read 12 lines from notes.md"}},
        {
            "role": "tool",
            "meta": {"output": "Edited image artifact 'A kettle' → version 3 (slug: a-kettle).\n"},
        },
    ]
    assert images_made_in(rows) == ["a-mug", "a-kettle"]


# ── the route, through a real turn ──────────────────────────────────────────
#
# The replayed turn runs for real here, and its runtime calls the real image tool the way the
# native runtime does: under the session key the turn created that runtime with, which is the
# key the tool resolves. The chat's own name is not that key, and a record kept under the name
# was never found by the tool, so a regenerate in the product still made a second image.


def _runtime_that_paints(keys: list[str], *, paints: bool):
    """``sessions.get_or_create`` for the turn: its runtime runs the image tool under its key.

    The turn's chores (its title, its follow-ups) are calls of their own (``chores.run_chore``)
    and take no runtime from the session manager.
    """
    from personalclaw import mcp_core
    from personalclaw.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent

    async def _get_or_create(key, **_kw):
        keys.append(key)

        async def _events():
            text = "Nothing to draw this time."
            if paints:
                token = mcp_core.set_current_session_key(key)
                try:
                    text = await asyncio.to_thread(
                        _generate, "A minimalist flat illustration for an opening slide"
                    )
                finally:
                    mcp_core.reset_current_session_key(token)
            yield LLMEvent(kind=EVENT_TEXT_CHUNK, text=text)
            yield LLMEvent(kind=EVENT_COMPLETE, stop_reason="end_turn")

        client = AsyncMock()
        client.provider_id = "native"
        client.model_substitution = None
        client.stream = MagicMock(side_effect=lambda *a, **kw: _events())
        # Synchronous reads the turn makes of its runtime: an AsyncMock would hand back coroutines.
        client.context_usage_pct = MagicMock(return_value=None)
        client.supports_native_commands = False
        return client, True, False

    return _get_or_create


def _answered_with_an_image(home, monkeypatch, *, paints: bool):
    """A chat whose last answer made an image, and a turn runner whose runtime can paint."""
    from personalclaw import mcp_core
    from personalclaw.hooks import ToolHookResult

    monkeypatch.setattr("personalclaw.dashboard.state.config_dir", lambda: home)
    monkeypatch.delenv("PERSONALCLAW_SESSION_KEY", raising=False)

    async def _chore(prompt, *, usage, validate=None, memory_mode=None):
        return "A flat illustration"

    # The turn's chores answer here, so no model is resolved for them.
    monkeypatch.setattr("personalclaw.chores.run_chore", _chore)
    state = _make_state(home)
    keys: list[str] = []
    state.sessions.get_or_create = AsyncMock(side_effect=_runtime_that_paints(keys, paints=paints))
    state.sessions.record_failure = AsyncMock()
    state.sessions.check_context_usage = MagicMock()
    builder = MagicMock()
    builder.hooks.on_tool_call.return_value = ToolHookResult.allow()
    builder.build_message.return_value = ("Make a flat illustration for my opening slide.", None)
    state.context_builder = builder
    hook_store = MagicMock()
    hook_store.fire_for_ids = AsyncMock(return_value=[])
    state._hook_store = hook_store
    state.broadcast_ws = MagicMock()
    state.push_sessions_update = MagicMock()

    session = state.get_or_create_session("s1")
    session._trust = True
    token = mcp_core.set_current_session_key("dashboard:s1")
    try:
        made = _generate("A flat illustration for an opening slide")
    finally:
        mcp_core.reset_current_session_key(token)
    session.append("user", "Make a flat illustration for my opening slide.")
    session.append("tool", "Image generate", meta={"output": made})
    session.append("assistant", "Here is the illustration for your opening slide.")
    session.drain()
    return state, keys


async def _regenerate(state):
    with patch("personalclaw.dashboard.chat_runner.sel", MagicMock()):
        async with TestClient(TestServer(_make_app(state))) as client:
            resp = await client.post("/api/chat/sessions/s1/regenerate")
            assert resp.status == 200
            await asyncio.gather(*list(state._background_tasks))


@pytest.mark.asyncio
async def test_a_regenerated_image_answer_saves_the_next_version_of_its_image(
    home, painter_library, monkeypatch
):
    state, keys = _answered_with_an_image(home, monkeypatch, paints=True)
    (first,) = painter_library.list(kind="image")

    await _regenerate(state)

    assert keys, "the replayed turn never asked for its runtime"
    images = painter_library.list(kind="image")
    assert [a.slug for a in images] == [first.slug], "the retake made a second image"
    assert images[0].version == 2
    data, _mime = painter_library.raw_bytes(first.slug)
    assert data.endswith(b"colour-2"), "v2 holds the retake, not the first image"


@pytest.mark.asyncio
async def test_the_retake_ends_with_the_replayed_turn(home, painter_library, monkeypatch):
    """A turn that made no image this time leaves nothing a LATER turn could retake."""
    state, keys = _answered_with_an_image(home, monkeypatch, paints=False)

    await _regenerate(state)

    assert keys, "the replayed turn never asked for its runtime"
    assert [retakes.take_retake(k) for k in keys] == [""] * len(keys)
