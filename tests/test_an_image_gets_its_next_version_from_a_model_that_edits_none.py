"""An image gets its next version from an image model that makes new images and edits none.

With such a model an image artifact could not get a second version. ``image_generate`` made a
next version only by sending the image to the model to edit; the model refused after the call was
approved, and the agent saved a separate image, so the original stayed at v1 and its
``/raw?version=2`` answered 404. The Iterate panel's opening prompt asked for exactly that edit.

A new image can now be saved as an existing image's next version (``slug``), an edit (``edit``)
goes only to a model that edits images, and the agent is told which way the bound model takes:
in the Iterate panel's opening prompt, in each image's result, and in the refusal of an edit,
which comes before anyone is asked to approve it. The Iterate snapshot also carries the prompt
the current version was made from, which is all a new image can keep the rest of the picture by.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.agents.native.runtime import NativeAgentRuntime
from personalclaw.agents.native.tools import InProcessMcpToolProvider
from personalclaw.agents.provider import AgentRuntimeDefinition
from personalclaw.artifacts import retakes
from personalclaw.image_gen.provider import (
    ImageGenError,
    ImageGenModel,
    ImageGenProvider,
    ImageResult,
)
from personalclaw.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    EVENT_TEXT_CHUNK,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    TOOL_META_NOT_RUN,
    AgentEvent,
)

_PNG = b"\x89PNG\r\n\x1a\n"
_ITERATE = "dashboard:chat-iterate"
_MADE_IN = "dashboard:chat-made"
_SLIDE = (
    "Flat vector illustration for an opening slide: two identical empty speech bubbles stacked, "
    "the lower one at 40% opacity, on a plain cream background."
)
_BLUE = _SLIDE + " Both bubbles are blue."


class _Painter(ImageGenProvider):
    """A scripted image model. Each image's bytes say how it was made: the prompt of a new one, or
    the edit's prompt and the image it was sent. ``edits`` is what it lists itself as doing, and
    ``lists`` whether its listing names its model at all."""

    def __init__(self, *, edits: bool, lists: bool = True) -> None:
        self.edits = edits
        self.lists = lists
        self.made: list[str] = []
        self.edited: list[str] = []

    @property
    def name(self) -> str:
        return "painter"

    @property
    def display_name(self) -> str:
        return "Painter"

    async def is_available(self) -> bool:
        return True

    async def list_models(self) -> list[ImageGenModel]:
        name = "painter-1" if self.lists else "another-model"
        return [ImageGenModel(name=name, supports_edit=self.edits)]

    async def generate(self, prompt, *, model="", size="", n=1, **opts) -> list[ImageResult]:
        self.made.append(prompt)
        return [_png(b"made: " + prompt.encode())]

    async def edit(self, prompt, *, source_image, mask="", model="", size="", n=1, **opts):
        if not self.edits:
            raise ImageGenError("This instance makes new images from a prompt and edits none.")
        self.edited.append(prompt)
        sent = Path(source_image).read_bytes()[len(_PNG) :]
        return [_png(b"edited: " + prompt.encode() + b" | from " + sent)]


def _png(body: bytes) -> ImageResult:
    return ImageResult(b64=base64.b64encode(_PNG + body).decode(), mime="image/png")


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr("personalclaw.config.loader.config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def library(home, monkeypatch):
    from personalclaw.artifacts import native
    from personalclaw.artifacts import registry as art_reg

    prov = native.NativeArtifactProvider(root=home / "artifacts")
    monkeypatch.setattr(art_reg, "get_provider", lambda name="native": prov)
    return prov


def _bind(monkeypatch, painter: _Painter) -> _Painter:
    from personalclaw.image_gen import registry as ig_reg

    monkeypatch.setattr(ig_reg, "active_image_gen", lambda: (painter, "painter-1"))
    return painter


def _tool(args: dict[str, Any], session: str = _MADE_IN) -> str:
    """One ``image_generate`` call made from chat *session*, and its result."""
    from personalclaw import mcp_core
    from personalclaw.mcp_artifacts import _call_tool

    token = mcp_core.set_current_session_key(session)
    try:
        return _call_tool("image_generate", args)
    finally:
        mcp_core.reset_current_session_key(token)


def _the_slide(library, *, recorded: bool = False):
    """The opening slide at v1, made by ``image_generate`` in a chat; *recorded* writes the call
    to that chat's transcript, as a dashboard chat does."""
    args = {"prompt": _SLIDE, "name": "Opening slide: duplicate SMS bubbles"}
    out = _tool(args)
    (slide,) = library.list(kind="image")
    if recorded:
        _record(_MADE_IN, args, out, "call-v1")
    return slide


def _record(session: str, args: dict[str, Any], out: str, call_id: str) -> None:
    """One approved ``image_generate`` call in chat *session*'s transcript, as a dashboard chat
    records it: the call's row with its input, then the row its result lands on once it is
    approved, with its output, both under the call's id."""
    from personalclaw.history import ConversationLog

    log = ConversationLog()
    log.append(session, "user", "Make a flat illustration for my opening slide.")
    rows = [
        {"tool_call_id": call_id, "purpose": "", "input": json.dumps(args)},
        {"tool_call_id": call_id, "purpose": "", "done": True, "output": out},
    ]
    with log._path(session).open("a", encoding="utf-8") as fh:
        for meta in rows:
            fh.write(json.dumps({"role": "tool", "content": "image_generate", "meta": meta}) + "\n")


# ── a scripted turn ─────────────────────────────────────────────────────────────────────────


class _Model:
    """Replays scripted turns, one per inference."""

    supports_tools = True
    _model = "scripted"

    def __init__(self, turns: list[list[AgentEvent]]) -> None:
        self._turns = turns
        self.calls = 0

    async def complete(self, messages, *, tools=None, model=None, reasoning_effort=""):
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev


def _call(args: dict[str, Any]) -> list[AgentEvent]:
    return [
        AgentEvent(
            kind=EVENT_TOOL_CALL,
            tool_call_id="i1",
            title="image_generate",
            tool_input=json.dumps(args),
        ),
        AgentEvent(kind=EVENT_COMPLETE),
    ]


async def _turn(message: str, args: dict[str, Any]) -> list[AgentEvent]:
    """The Iterate chat's turn for *message*: the agent calls ``image_generate`` with *args*, and
    every approval it asks for is allowed."""
    done = [AgentEvent(kind=EVENT_TEXT_CHUNK, text="done"), AgentEvent(kind=EVENT_COMPLETE)]
    rt = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="T", provider="native", model="scripted"),
        model_provider=_Model([_call(args), done]),
        tool_providers=[
            InProcessMcpToolProvider(
                module="personalclaw.mcp_artifacts",
                provider_name="personalclaw-artifacts",
                display="PersonalClaw Artifacts",
            )
        ],
        session_key=_ITERATE,
    )
    await rt.start()
    seen: list[AgentEvent] = []

    async def pump() -> None:
        async for ev in rt.stream(message):
            seen.append(ev)
            if ev.kind == EVENT_PERMISSION_REQUEST:
                await rt.approve_tool(ev.request_id)

    await asyncio.wait_for(pump(), timeout=10)
    return seen


def _of(seen: list[AgentEvent], kind: str) -> list[AgentEvent]:
    return [e for e in seen if e.kind == kind]


def _artifacts_app(library) -> web.Application:
    """The artifact routes the Artifacts page and a chat's images call, over *library*."""
    from personalclaw.artifacts import registry
    from personalclaw.artifacts.handlers import register_artifact_routes

    app = web.Application()
    state = MagicMock()
    state._sessions = {}
    app["state"] = state
    register_artifact_routes(app)
    assert registry.get_provider("native") is library
    return app


async def _served(library, slug: str, version: int) -> tuple[int, bytes]:
    """What the Artifacts page's ``/raw?version=N`` read answers."""
    async with TestClient(TestServer(_artifacts_app(library))) as client:
        resp = await client.get(f"/api/artifacts/{slug}/raw?version={version}")
        return resp.status, await resp.read()


# ── the next version, made new from a prompt ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_make_the_bubbles_blue_is_v2_of_the_same_image(library, monkeypatch) -> None:
    """🔴 Before: there was no way to save a new image as the next version, so it was a second
    image, and the slide's ``raw?version=2`` answered 404."""
    painter = _bind(monkeypatch, _Painter(edits=False))
    slide = _the_slide(library)

    seen = await _turn(
        "Make the bubbles blue, keep everything else.", {"prompt": _BLUE, "slug": slide.slug}
    )

    assert len(_of(seen, EVENT_PERMISSION_REQUEST)) == 1
    [result] = _of(seen, EVENT_TOOL_RESULT)
    said = str(result.tool_output)
    assert result.tool_meta.get("ok") is not False, said
    assert f"→ version 2 (slug: {slide.slug})" in said
    assert f"/api/artifacts/{slide.slug}/raw?version=2" in said
    assert [(a.slug, a.version) for a in library.list(kind="image")] == [(slide.slug, 2)]
    assert library.raw_bytes(slide.slug, version=1)[0] == _PNG + b"made: " + _SLIDE.encode()
    assert library.raw_bytes(slide.slug, version=2)[0] == _PNG + b"made: " + _BLUE.encode()
    assert (painter.made, painter.edited) == ([_SLIDE, _BLUE], [])
    assert await _served(library, slide.slug, 2) == (200, _PNG + b"made: " + _BLUE.encode())


def test_each_image_s_result_says_how_its_next_version_is_made(library, monkeypatch) -> None:
    _bind(monkeypatch, _Painter(edits=False))
    out = _tool({"prompt": _SLIDE})
    (slide,) = library.list(kind="image")
    assert (
        f"To make its next version, call image_generate with slug='{slide.slug}' and a prompt "
        "that describes the whole picture with the change made: the model chosen under Image · "
        "Generation makes new images from a prompt and edits none"
    ) in out
    assert "edit=true" not in out


# ── an edit, only for a model that edits ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_edit_for_a_model_that_edits_none_is_refused_before_anyone_is_asked(
    library, monkeypatch
) -> None:
    """🔴 Before: the edit was put to the owner, she allowed it, and then the model refused it."""
    painter = _bind(monkeypatch, _Painter(edits=False))
    slide = _the_slide(library)

    seen = await _turn(
        "Make the bubbles blue.",
        {"prompt": "Make the bubbles blue", "slug": slide.slug, "edit": True},
    )

    assert _of(seen, EVENT_PERMISSION_REQUEST) == []
    [result] = _of(seen, EVENT_TOOL_RESULT)
    said = str(result.tool_output)
    assert result.tool_meta.get(TOOL_META_NOT_RUN) == "refused_by_tool"
    assert (
        "The model chosen under Image · Generation (painter:painter-1) makes new images from a "
        "prompt and edits none, so nothing was sent."
    ) in said
    assert (
        f"To make the next version of '{slide.slug}', call image_generate with slug='{slide.slug}' "
        "and a prompt that describes the whole picture with the change made"
    ) in said
    assert (painter.made, painter.edited) == ([_SLIDE], [])
    assert library.get(slide.slug).version == 1


def test_a_model_that_edits_changes_the_image_it_is_sent(library, monkeypatch) -> None:
    painter = _bind(monkeypatch, _Painter(edits=True))
    slide = _the_slide(library)

    out = _tool({"prompt": "Make the bubbles blue", "slug": slide.slug, "edit": True})

    assert f"Edited image artifact '{slide.name}' → version 2 (slug: {slide.slug})." in out
    assert painter.edited == ["Make the bubbles blue"]
    edited = library.raw_bytes(slide.slug, version=2)[0]
    assert edited == _PNG + b"edited: Make the bubbles blue | from made: " + _SLIDE.encode()
    assert f"slug='{slide.slug}', edit=true and a prompt saying what to change" in out


@pytest.mark.parametrize(
    ("args", "refused"),
    [
        ({"prompt": "blue", "edit": True}, "edit=true changes an existing image: pass its slug"),
        ({"prompt": "blue", "slug": "no-such-image"}, "'no-such-image' is not an existing image"),
        ({"prompt": "blue", "slug": "notes"}, "'notes' is a markdown artifact, so an image cannot"),
    ],
)
def test_a_next_version_needs_an_image_to_be_the_next_version_of(
    library, monkeypatch, args, refused
) -> None:
    painter = _bind(monkeypatch, _Painter(edits=True))
    library.create(name="Notes", content="# Notes", kind="markdown")
    out = _tool(args)
    assert out.startswith("Error:") and refused in out, out
    assert (painter.made, painter.edited) == ([], [])
    assert library.list(kind="image") == []


# ── the Iterate panel ───────────────────────────────────────────────────────────────────────


def _iterate(slug: str):
    from personalclaw import investigate

    ctx = asyncio.run(investigate.resolve("artifact", slug, MagicMock()))
    assert ctx is not None
    return ctx


def test_the_iterate_panel_offers_a_new_image_for_a_model_that_edits_none(
    library, monkeypatch
) -> None:
    """🔴 Before: the opening prompt asked for an edit whatever the model, so the change went
    to a second image."""
    _bind(monkeypatch, _Painter(edits=False))
    slide = _the_slide(library, recorded=True)

    ctx = _iterate(slide.slug)

    assert (
        f"call image_generate with slug='{slide.slug}' and a prompt that describes the whole "
        "picture with the change made: the model chosen under Image · Generation makes new images "
        "from a prompt and edits none, so it never sees the current one"
    ) in ctx.opening_prompt
    assert "edit=true" not in ctx.opening_prompt
    assert f"v1 was made from the prompt: {_SLIDE}" in ctx.snapshot


def test_the_iterate_panel_offers_an_edit_for_a_model_that_edits(library, monkeypatch) -> None:
    _bind(monkeypatch, _Painter(edits=True))
    slide = _the_slide(library)
    opening = _iterate(slide.slug).opening_prompt
    assert f"slug='{slide.slug}', edit=true and a prompt saying what to change" in opening


def test_a_model_that_says_nothing_of_editing_is_offered_a_new_image(library, monkeypatch) -> None:
    """A listing that does not name the bound model says nothing either way, so only what every
    model does is offered, and nothing is claimed about the model."""
    _bind(monkeypatch, _Painter(edits=True, lists=False))
    slide = _the_slide(library)
    opening = _iterate(slide.slug).opening_prompt
    assert f"call image_generate with slug='{slide.slug}' and a prompt that describes" in opening
    assert "edit=true" not in opening and "edits none" not in opening


def test_the_snapshot_names_the_prompt_of_the_version_it_shows(library, monkeypatch) -> None:
    _bind(monkeypatch, _Painter(edits=False))
    slide = _the_slide(library, recorded=True)
    args = {"prompt": _BLUE, "slug": slide.slug}
    _record(_ITERATE, args, _tool(args, session=_ITERATE), "call-v2")

    snapshot = _iterate(slide.slug).snapshot

    assert f"v2 was made from the prompt: {_BLUE}" in snapshot
    assert "v1 was made from the prompt" not in snapshot


def test_artifact_update_on_an_image_names_the_way_the_model_takes(library, monkeypatch) -> None:
    from personalclaw.mcp_artifacts import _call_tool

    _bind(monkeypatch, _Painter(edits=False))
    slide = _the_slide(library)
    out = _call_tool("artifact_update", {"slug": slide.slug, "content": "<svg/>"})
    assert out.startswith("Error:"), out
    assert f"call image_generate with slug='{slide.slug}' and a prompt that describes" in out
    assert "edits none" in out


# ── a regenerated answer ────────────────────────────────────────────────────────────────────


def test_a_regenerated_answer_lands_each_image_on_its_own(library, monkeypatch) -> None:
    """The answer made the slide's next version and a second image. Its replay names the slide
    again, then makes the second image anew: that one is the second image's next version, not a
    further version of the slide."""
    _bind(monkeypatch, _Painter(edits=False))
    slide = _the_slide(library)
    _tool({"prompt": "A harbour at dusk", "name": "Harbour"})
    harbour = next(a for a in library.list(kind="image") if a.slug != slide.slug)
    retakes.open_retakes(_MADE_IN, [slide.slug, harbour.slug])

    _tool({"prompt": _BLUE, "slug": slide.slug})
    _tool({"prompt": "A harbour at dawn"})

    by_slug = {a.slug: a.version for a in library.list(kind="image")}
    assert by_slug == {slide.slug: 2, harbour.slug: 2}
    assert retakes.take_retake(_MADE_IN) == ""


# ── a deleted image, made again ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_deleted_image_is_made_again_from_its_prompt_not_its_name(
    library, monkeypatch
) -> None:
    """The chat's Regenerate on an image whose artifact was deleted remakes it from the prompt the
    chat's rows record. 🔴 Before: it read the call's arguments from the row its result is on,
    which for an approved call holds none, so it remade the image from the caption the page
    sends, which is the image's name."""
    painter = _bind(monkeypatch, _Painter(edits=False))
    slide = _the_slide(library, recorded=True)
    assert library.delete(slide.slug)

    async with TestClient(TestServer(_artifacts_app(library))) as client:
        resp = await client.post(
            f"/api/artifacts/{slide.slug}/regenerate",
            json={"session": _MADE_IN, "prompt": slide.name},
        )
        status, body = resp.status, await resp.json()

    assert status == 200, body
    assert painter.made == [_SLIDE, _SLIDE]
    assert library.raw_bytes(slide.slug, version=1)[0] == _PNG + b"made: " + _SLIDE.encode()
