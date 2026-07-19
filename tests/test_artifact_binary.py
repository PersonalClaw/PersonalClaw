"""Binary (kind:image) artifact storage — the IG3 net-new infra.

A generated image lands as a versioned binary artifact: bytes on disk
(current.<ext> + versions/vN.<ext>), content = the raw-URL ref (never base64),
served via /api/artifacts/{slug}/raw. The text path must stay byte-identical.
"""

from __future__ import annotations

import pytest

from personalclaw.artifacts.models import is_binary_kind, ext_for_mime
from personalclaw.artifacts.native import NativeArtifactProvider

_PNG = b"\x89PNG\r\n\x1a\n" + b"fakeimagedata" * 4


class TestBinaryArtifact:
    def test_create_binary_stores_bytes_and_ref(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create_binary(name="A Cat", data=_PNG, mime="image/png", actor="agent")
        assert art.kind == "image"
        assert art.mime == "image/png"
        # content is the raw REF, never the bytes (no base64-in-content)
        assert art.content == f"/api/artifacts/{art.slug}/raw"
        # bytes are on disk as current.png (not .html)
        assert (tmp_path / art.slug / "current.png").read_bytes() == _PNG
        assert (tmp_path / art.slug / "versions" / "v1.png").read_bytes() == _PNG
        # NOT written as text
        assert not (tmp_path / art.slug / "current.html").exists()

    def test_raw_bytes_roundtrip(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create_binary(name="cat", data=_PNG, mime="image/png")
        got = prov.raw_bytes(art.slug)
        assert got is not None
        data, mime = got
        assert data == _PNG and mime == "image/png"

    def test_get_returns_ref_not_bytes(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create_binary(name="cat", data=_PNG, mime="image/png")
        fetched = prov.get(art.slug)
        assert fetched is not None
        assert fetched.content == f"/api/artifacts/{art.slug}/raw"
        assert fetched.mime == "image/png"
        assert fetched.live_dirty is False

    def test_update_binary_bumps_version(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create_binary(name="cat", data=_PNG, mime="image/png")
        new = b"\x89PNG\r\n\x1a\n" + b"editeddata" * 8
        updated = prov.update_binary(art.slug, data=new, actor="agent")
        assert updated is not None and updated.version == 2
        # live serves the new bytes; v1 snapshot keeps the original
        assert prov.raw_bytes(art.slug)[0] == new
        assert prov.raw_bytes(art.slug, version=1)[0] == _PNG
        assert prov.raw_bytes(art.slug, version=2)[0] == new
        assert prov.list_versions(art.slug) == [1, 2]

    def test_jpeg_uses_jpg_extension(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create_binary(name="photo", data=_PNG, mime="image/jpeg")
        assert (tmp_path / art.slug / "current.jpg").exists()

    def test_text_create_refuses_binary_kind(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        with pytest.raises(ValueError):
            prov.create(name="x", content="<html>", kind="image")

    def test_raw_bytes_none_for_text_artifact(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create(name="doc", content="# hi", kind="markdown")
        assert prov.raw_bytes(art.slug) is None

    def test_delete_removes_binary(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create_binary(name="cat", data=_PNG, mime="image/png")
        assert prov.delete(art.slug) is True
        assert prov.get(art.slug) is None
        assert prov.raw_bytes(art.slug) is None

    def test_list_includes_image_kind(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        prov.create_binary(name="cat", data=_PNG, mime="image/png")
        imgs = prov.list(kind="image")
        assert len(imgs) == 1 and imgs[0].kind == "image"
        assert imgs[0].content is None  # list omits content


class TestBinaryRevert:
    """Revert restores a historical BODY server-side — the bytes, not a client ref.

    The bug this guards: routing a binary revert through update(content=ref) wrote
    the raw-URL string as text + never restored the image body, so the reverted
    version 404'd and the live bytes stayed stale."""

    def test_revert_restores_binary_body_as_new_version(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        v1 = b"\x89PNG\r\n\x1a\n" + b"original" * 4
        v2 = b"\x89PNG\r\n\x1a\n" + b"editedXX" * 4
        art = prov.create_binary(name="leaf", data=v1, mime="image/png", actor="agent")
        prov.update_binary(art.slug, data=v2, actor="agent")
        # revert to v1 → a NEW v3 whose bytes equal v1
        reverted = prov.revert(art.slug, 1, actor="user")
        assert reverted is not None and reverted.version == 3
        assert prov.list_versions(art.slug) == [1, 2, 3]
        # live + v3 bytes == v1; v2 snapshot is untouched
        assert prov.raw_bytes(art.slug)[0] == v1
        assert prov.raw_bytes(art.slug, version=3)[0] == v1
        assert prov.raw_bytes(art.slug, version=2)[0] == v2
        # the new version's body is a real binary file (not a text ref)
        assert (tmp_path / art.slug / "versions" / "v3.png").read_bytes() == v1
        assert not (tmp_path / art.slug / "current.html").exists()

    def test_revert_emits_reverted_event_by_user(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create_binary(name="leaf", data=_PNG, mime="image/png", actor="agent")
        prov.update_binary(art.slug, data=_PNG + b"x", actor="agent")
        prov.revert(art.slug, 1, actor="user")
        ev = prov.get(art.slug).events[-1]
        assert ev.type == "reverted" and ev.from_version == 1 and ev.version == 3
        assert ev.by == "user"

    def test_revert_missing_version_returns_none(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create_binary(name="leaf", data=_PNG, mime="image/png")
        assert prov.revert(art.slug, 9, actor="user") is None

    def test_revert_text_artifact_restores_content(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create(name="doc", content="# v1", kind="markdown")
        prov.update(art.slug, content="# v2", snapshot=True)
        reverted = prov.revert(art.slug, 1, actor="user")
        assert reverted is not None and reverted.version == 3
        assert reverted.content == "# v1"
        assert prov.get(art.slug, version=3).content == "# v1"

    def test_update_refuses_reverted_event_type(self, tmp_path):
        prov = NativeArtifactProvider(root=tmp_path)
        art = prov.create(name="doc", content="# hi", kind="markdown")
        with pytest.raises(ValueError):
            prov.update(art.slug, content="x", snapshot=True, event_type="reverted")

    def test_revert_across_mime_change_keeps_source_type(self, tmp_path):
        """v1 PNG, v2 JPEG; reverting to v1 restores PNG bytes + image/png mime
        (the version's disk extension is the per-version source of truth)."""
        prov = NativeArtifactProvider(root=tmp_path)
        png = b"\x89PNG\r\n\x1a\n" + b"pngdata" * 4
        jpg = b"\xff\xd8\xff\xe0" + b"jpgdata" * 4
        art = prov.create_binary(name="pic", data=png, mime="image/png", actor="agent")
        prov.update_binary(art.slug, data=jpg, mime="image/jpeg", actor="agent")
        # historical reads report each version's OWN mime, not the current one
        assert prov.raw_bytes(art.slug, version=1) == (png, "image/png")
        assert prov.raw_bytes(art.slug, version=2) == (jpg, "image/jpeg")
        reverted = prov.revert(art.slug, 1, actor="user")
        assert reverted.mime == "image/png"
        assert prov.raw_bytes(art.slug) == (png, "image/png")
        assert (tmp_path / art.slug / "current.png").exists()


class TestMimeHelpers:
    def test_ext_for_mime(self):
        assert ext_for_mime("image/png") == "png"
        assert ext_for_mime("image/jpeg") == "jpg"
        assert ext_for_mime("image/webp") == "webp"
        assert ext_for_mime("application/weird") == "png"  # default

    def test_is_binary_kind(self):
        assert is_binary_kind("image") is True
        assert is_binary_kind("markdown") is False
        assert is_binary_kind("") is False
