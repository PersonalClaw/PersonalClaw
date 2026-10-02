"""An artifact's versions are all of its kind, so its current file, the bytes served and its
metadata always agree.

An image artifact iterated through ``artifact_update`` with text used to be filed anyway: the text
went to ``versions/v2.html`` and ``current.html`` beside ``v1.png`` / ``current.png``, the
metadata said image/png at version 2, and the page and Download kept serving v1's picture. The
text write is refused now, before anything is written, in words the agent can act on (the tool
that does make an image's next version), and every other writer of an artifact's body is held to
the same rule: the page's own save, a workflow's refresh, a document tool naming another kind's
slug, and a binary write whose body is another kind.

The Iterate panel's opening prompt is where the agent learned to reach for ``artifact_update`` on
an image at all, so it names the tool that versions each kind too.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.action_providers.artifact_update_provider import ArtifactUpdateActionProvider
from personalclaw.action_providers.base import ActionContext
from personalclaw.artifacts import registry
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.models import (
    BINARY_KINDS,
    ArtifactKindMismatch,
    mime_for_ext,
)
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.mcp_artifacts import _call_tool, next_version_instruction

_PNG = b"\x89PNG\r\n\x1a\n" + b"a small picture" * 4
_PDF = b"%PDF-1.4\n" + b"a document body" * 4
_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><circle r="4" fill="#00aaff"/></svg>'


@pytest.fixture
def provider(tmp_path) -> NativeArtifactProvider:
    return NativeArtifactProvider(root=tmp_path / "artifacts")


@pytest.fixture
def wired(provider):
    with (
        patch.object(registry, "get_provider", return_value=provider),
        patch("personalclaw.mcp_artifacts._resolve_session_key", return_value="dashboard:chat-1"),
    ):
        yield provider


def _picture(provider: NativeArtifactProvider):
    return provider.create_binary(
        name="Opening slide", data=_PNG, mime="image/png", kind="image", actor="agent"
    )


def _files(provider: NativeArtifactProvider, slug: str) -> set[str]:
    root = provider.root / slug
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def _assert_untouched(provider: NativeArtifactProvider, slug: str) -> None:
    """v1 and only v1: the file, the served bytes and the metadata all say the same thing."""
    art = provider.get(slug)
    assert art is not None
    assert (art.kind, art.mime, art.version) == ("image", "image/png", 1)
    assert provider.list_versions(slug) == [1]
    assert provider.raw_bytes(slug) == (_PNG, "image/png")
    assert _files(provider, slug) == {"meta.json", "current.png", "versions/v1.png"}


class TestTheStoreKeepsAKindsVersionsOfItsKind:
    def test_text_is_not_filed_as_an_images_next_version(self, provider) -> None:
        art = _picture(provider)
        with pytest.raises(ArtifactKindMismatch) as refused:
            provider.update(art.slug, content=_SVG, snapshot=True, actor="agent")
        assert refused.value.kind == "image"
        _assert_untouched(provider, art.slug)

    def test_a_metadata_edit_cuts_no_version_of_an_image(self, provider) -> None:
        """The agent's metadata-only update always asks for a snapshot. A binary body only changes
        through its own writers, each of which cuts its version, so there is nothing to snapshot:
        it used to cut an EMPTY ``v2.html``."""
        art = _picture(provider)
        updated = provider.update(
            art.slug, description="The opening slide", snapshot=True, actor="agent"
        )
        assert updated is not None and updated.description == "The opening slide"
        _assert_untouched(provider, art.slug)

    def test_a_binary_write_of_another_kind_is_refused(self, provider) -> None:
        art = _picture(provider)
        with pytest.raises(ArtifactKindMismatch):
            provider.update_binary(art.slug, data=_PDF, mime=mime_for_ext("pdf"), actor="agent")
        _assert_untouched(provider, art.slug)

    def test_a_binary_write_to_a_text_artifact_is_refused(self, provider) -> None:
        note = provider.create(name="Notes", content="# Notes", kind="markdown")
        with pytest.raises(ArtifactKindMismatch):
            provider.update_binary(note.slug, data=_PNG, mime="image/png", actor="agent")
        after = provider.get(note.slug)
        assert after is not None and (after.version, after.content) == (1, "# Notes")

    def test_ordinary_writes_still_land(self, provider) -> None:
        """The floor: an image's own next version, another image format included, and a text
        artifact's text, are each written as before."""
        art = _picture(provider)
        jpeg = b"\xff\xd8\xff" + b"an edited picture" * 4
        edited = provider.update_binary(art.slug, data=jpeg, mime="image/jpeg", actor="agent")
        assert edited is not None and (edited.version, edited.mime) == (2, "image/jpeg")
        assert provider.raw_bytes(art.slug) == (jpeg, "image/jpeg")
        assert provider.raw_bytes(art.slug, version=1) == (_PNG, "image/png")

        note = provider.create(name="Notes", content="# Notes", kind="markdown")
        again = provider.update(note.slug, content="# Notes, v2", snapshot=True, actor="agent")
        assert again is not None and (again.version, again.content) == (2, "# Notes, v2")


class TestTheAgentIsToldWhatDoesMakeTheNextVersion:
    def test_artifact_update_with_text_on_an_image_names_image_generate(self, wired) -> None:
        art = _picture(wired)
        out = _call_tool("artifact_update", {"slug": art.slug, "content": _SVG})
        assert out.startswith("Error:"), out
        assert f"image_generate with slug='{art.slug}'" in out
        assert "artifact_save" in out
        _assert_untouched(wired, art.slug)

    def test_a_metadata_update_on_an_image_still_works(self, wired) -> None:
        art = _picture(wired)
        out = _call_tool("artifact_update", {"slug": art.slug, "tags": ["slides"]})
        assert not out.startswith("Error:"), out
        got = wired.get(art.slug)
        assert got is not None and got.tags == ["slides"]
        assert wired.list_versions(art.slug) == [1]

    def test_a_document_tool_naming_another_kinds_slug_is_refused(self, wired) -> None:
        art = _picture(wired)
        out = _call_tool(
            "document_create", {"name": "Deck notes", "markdown": "# Notes", "slug": art.slug}
        )
        assert out.startswith("Error:"), out
        assert "image" in out and "docx" in out
        _assert_untouched(wired, art.slug)

    @pytest.mark.parametrize("image_edits", [True, False, None])
    @pytest.mark.parametrize("kind", sorted(BINARY_KINDS - {"video"}))
    def test_every_binary_kind_says_how_its_next_version_is_made(self, kind, image_edits) -> None:
        """One phrase per binary kind, naming a tool and this slug, never `artifact_update`."""
        said = next_version_instruction(kind, "the-slug", image_edits)
        assert said.startswith("call "), (kind, said)
        assert "the-slug" in said, said
        assert "artifact_update" not in said, said

    def test_a_video_says_that_no_tool_makes_its_next_version(self, wired) -> None:
        """No tool edits a video, so the refusal says what is true rather than naming one."""
        assert next_version_instruction("video", "a-clip", None) == ""
        clip = wired.create_binary(name="A clip", data=b"\x00" * 32, mime="video/mp4", kind="video")
        out = _call_tool("artifact_update", {"slug": clip.slug, "content": "text"})
        assert out.startswith("Error:"), out
        assert "video_generate saves a new one" in out
        from personalclaw.investigate import _resolve_artifact

        ctx = asyncio.run(_resolve_artifact(clip.slug, MagicMock()))
        assert ctx is not None and "video_generate saves a new one" in ctx.opening_prompt

    def test_a_text_kind_is_versioned_by_artifact_update(self) -> None:
        assert "artifact_update" in next_version_instruction("markdown", "notes", None)


class TestEveryOtherWriterOfABodyIsHeldToIt:
    def test_the_pages_body_save_on_an_image_is_refused_409(self, provider) -> None:
        art = _picture(provider)

        async def drive() -> tuple[int, dict]:
            app = web.Application()
            state = MagicMock()
            state._restricted_keys = set()
            state._sessions = {}
            app["state"] = state
            register_artifact_routes(app)
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                detail = await (await client.get(f"/api/artifacts/{art.slug}")).json()
                resp = await client.patch(
                    f"/api/artifacts/{art.slug}",
                    json={"content": _SVG, "snapshot": True, "event_type": "iterated"},
                    headers={"If-Match": detail["content_revision"]},
                )
                return resp.status, await resp.json()
            finally:
                await client.close()

        with patch.object(registry, "get_provider", return_value=provider):
            status, body = asyncio.run(drive())
        assert status == 409, body
        assert body["error"]["code"] == "kind_is_binary"
        _assert_untouched(provider, art.slug)

    def test_a_workflows_refresh_of_an_image_fails_and_writes_nothing(self, provider) -> None:
        art = _picture(provider)
        with patch.object(registry, "get_provider", return_value=provider):
            result = asyncio.run(
                ArtifactUpdateActionProvider().execute(
                    {"slug": art.slug, "content": _SVG}, ActionContext(event="manual")
                )
            )
        assert result.success is False
        assert "image" in (result.error or "")
        _assert_untouched(provider, art.slug)


class TestEachVersionIsReadAtItsOwnAddress:
    """A page draws a binary artifact from its ``content`` reference, and keeps the image one URL
    gave it for as long as the page lives, whatever the response's cache headers say. The current
    read handed out one unversioned ``/raw`` for every version, so an open artifact re-read its
    next version and went on drawing the old picture while the store served the new one."""

    def test_every_read_names_the_version_it_shows(self, provider) -> None:
        art = _picture(provider)
        first = f"/api/artifacts/{art.slug}/raw?version=1"
        assert art.content == first
        fetched = provider.get(art.slug)
        assert fetched is not None and fetched.content == first

        cropped = _PNG + b"cropped"
        edited = provider.update_binary(art.slug, data=cropped, mime="image/png", actor="agent")
        assert edited is not None and edited.content == f"/api/artifacts/{art.slug}/raw?version=2"
        fetched = provider.get(art.slug)
        assert fetched is not None and fetched.content == edited.content

        reverted = provider.revert(art.slug, 1)
        assert (
            reverted is not None and reverted.content == f"/api/artifacts/{art.slug}/raw?version=3"
        )
        # Each address serves the version it names, so no two versions share one.
        assert provider.raw_bytes(art.slug, version=1) == (_PNG, "image/png")
        assert provider.raw_bytes(art.slug, version=2) == (cropped, "image/png")
        assert provider.raw_bytes(art.slug, version=3) == (_PNG, "image/png")

    def test_the_page_s_read_hands_it_the_new_version_s_address(self, provider) -> None:
        """Through the routes the Artifacts page reads: the detail it re-reads on a change names the
        new version, and that address serves the new bytes."""
        art = _picture(provider)
        cropped = _PNG + b"cropped"

        async def drive() -> tuple[str, str, bytes]:
            app = web.Application()
            state = MagicMock()
            state._restricted_keys = set()
            state._sessions = {}
            app["state"] = state
            register_artifact_routes(app)
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                before = (await (await client.get(f"/api/artifacts/{art.slug}")).json())["content"]
                provider.update_binary(art.slug, data=cropped, mime="image/png", actor="agent")
                after = (await (await client.get(f"/api/artifacts/{art.slug}")).json())["content"]
                served = await (await client.get(after)).read()
                return before, after, served
            finally:
                await client.close()

        with patch.object(registry, "get_provider", return_value=provider):
            before, after, served = asyncio.run(drive())
        assert before == f"/api/artifacts/{art.slug}/raw?version=1"
        assert after == f"/api/artifacts/{art.slug}/raw?version=2"
        assert served == cropped


class TestTheIteratePanelNamesTheToolForTheKind:
    def _opening(self, provider, slug: str) -> str:
        from personalclaw.investigate import _resolve_artifact

        with patch.object(registry, "get_provider", return_value=provider):
            ctx = asyncio.run(_resolve_artifact(slug, MagicMock()))
        assert ctx is not None
        return ctx.opening_prompt

    def test_an_image_is_iterated_with_image_generate(self, provider) -> None:
        art = _picture(provider)
        prompt = self._opening(provider, art.slug)
        assert f"image_generate with slug='{art.slug}'" in prompt
        assert "artifact_update" not in prompt

    def test_a_text_artifact_is_iterated_with_artifact_update(self, provider) -> None:
        note = provider.create(name="Notes", content="# Notes", kind="markdown")
        assert "artifact_update" in self._opening(provider, note.slug)

    def test_a_document_is_iterated_with_its_document_tool(self, provider) -> None:
        doc = provider.create_binary(
            name="Report", data=b"PK\x03\x04" + b"x" * 16, mime=mime_for_ext("docx"), kind="docx"
        )
        from personalclaw.investigate import _resolve_artifact

        with patch.object(registry, "get_provider", return_value=provider):
            ctx = asyncio.run(_resolve_artifact(doc.slug, MagicMock()))
        assert ctx is not None
        assert f"document_create with slug='{doc.slug}'" in ctx.opening_prompt
        # A binary body is a raw reference, never content to read: every binary kind says so.
        assert "Binary artifact; body served at:" in ctx.snapshot
        assert "Current content" not in ctx.snapshot


def test_the_refusal_is_the_store_s_own_value_error() -> None:
    """A caller that already turns a bad write into a 400 keeps working; one that cares catches
    this first."""
    assert issubclass(ArtifactKindMismatch, ValueError)
    assert json.loads(json.dumps({"k": str(ArtifactKindMismatch("s", "image", "text"))}))
