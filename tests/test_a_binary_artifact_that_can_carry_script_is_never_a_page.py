"""A binary artifact's bytes are shown as themselves only when the store knows their type.

``GET /api/artifacts/{slug}/raw`` served an artifact's bytes with the MIME stored beside them, and
the write guard checked that MIME only when it named a binary kind: a generated image whose
source answered ``text/html``, or an ``image/svg+xml`` one, was filed as an image and then served
as a page on the dashboard's own origin, where it could run script. Now:

- the raw route serves a stored type the store knows (``artifacts.models.kind_for_mime``) as
  itself, and anything else as ``application/octet-stream``, a download;
- every answer but a PDF's carries a sandbox policy, so even opened in a tab of its own the bytes
  run nothing; an image or video element ignores it, and a PDF viewer needs to run;
- the binary write path refuses a MIME it can't name a binary kind for, and a generated file is
  named by its bytes when its source's ``Content-Type`` says something else.
"""

from __future__ import annotations

import pytest
from aiohttp.test_utils import make_mocked_request

from personalclaw.artifacts import handlers
from personalclaw.artifacts.models import ArtifactKindMismatch
from personalclaw.artifacts.native import NativeArtifactProvider

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PAGE = b"<!doctype html><script>fetch('/api/memory/export')</script>"
SANDBOX = "default-src 'none'; sandbox"


class _Store:
    def __init__(self, data: bytes, mime: str) -> None:
        self._answer = (data, mime)

    def raw_bytes(self, slug: str, *, version: int | None = None):
        return self._answer


async def _raw(monkeypatch, data: bytes, mime: str):
    monkeypatch.setattr(handlers, "_provider", lambda request: _Store(data, mime))
    request = make_mocked_request("GET", "/api/artifacts/x/raw", match_info={"slug": "x"})
    return await handlers.api_artifact_raw(request)


@pytest.mark.parametrize(
    "mime", ["text/html", "image/svg+xml", "application/xhtml+xml", "text/xml", ""]
)
@pytest.mark.asyncio
async def test_a_stored_type_the_store_doesnt_know_is_a_download_that_runs_nothing(
    monkeypatch, mime
):
    """🔴 Red before: served as itself, a page on the dashboard's origin."""
    resp = await _raw(monkeypatch, PAGE, mime)
    assert resp.content_type == "application/octet-stream"
    assert resp.headers["Content-Disposition"].startswith("attachment")
    assert resp.headers["Content-Security-Policy"] == SANDBOX
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("mime", ["image/png", "image/webp", "video/mp4"])
@pytest.mark.asyncio
async def test_a_known_image_or_video_is_shown_inline_under_the_sandbox_policy(monkeypatch, mime):
    """🔴 Red before: no policy of its own, so opened as a tab it took the dashboard's."""
    resp = await _raw(monkeypatch, PNG, mime)
    assert resp.content_type == mime
    assert "Content-Disposition" not in resp.headers
    assert resp.headers["Content-Security-Policy"] == SANDBOX
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_a_pdf_is_shown_inline_without_the_sandbox_so_its_viewer_runs(monkeypatch):
    resp = await _raw(monkeypatch, b"%PDF-1.7\n", "application/pdf")
    assert resp.content_type == "application/pdf"
    assert "Content-Security-Policy" not in resp.headers
    assert "Content-Disposition" not in resp.headers


@pytest.fixture
def store(tmp_path):
    return NativeArtifactProvider(root=tmp_path / "artifacts")


@pytest.mark.parametrize("mime", ["text/html", "image/svg+xml", "application/octet-stream"])
def test_a_binary_write_refuses_a_type_it_cant_name_a_kind_for(store, mime):
    """🔴 Red before: the check was skipped when the MIME named no binary kind."""
    with pytest.raises(ArtifactKindMismatch):
        store.create_binary(name="picture", data=PAGE, mime=mime, kind="image")
    art = store.create_binary(name="picture", data=PNG, mime="image/png", kind="image")
    with pytest.raises(ArtifactKindMismatch):
        store.update_binary(art.slug, data=PAGE, mime=mime)
    assert store.raw_bytes(art.slug)[1] == "image/png"


def test_an_update_that_names_no_type_keeps_the_artifacts_own(store):
    """The control: an update with no MIME keeps the stored one, as before."""
    art = store.create_binary(name="picture", data=PNG, mime="image/png", kind="image")
    assert store.update_binary(art.slug, data=PNG) is not None
    assert store.raw_bytes(art.slug)[1] == "image/png"


@pytest.mark.parametrize(
    "said,data,named",
    [
        ("text/html", PNG, "image/png"),
        ("application/octet-stream", PNG, "image/png"),
        ("image/png; charset=binary", PNG, "image/png"),
        ("image/png", PNG, "image/png"),
        ("text/html", PAGE, "text/html"),
    ],
    ids=["html-said-png-bytes", "octet-stream", "with-params", "as-said", "a-page-stays-a-page"],
)
def test_a_generated_image_is_named_by_its_bytes(monkeypatch, said, data, named):
    """🔴 Red before: a fetched image took its source's ``Content-Type`` as given. A page stays
    named a page, which the write then refuses."""
    from personalclaw import mcp_artifacts

    class _Result:
        url = "https://images.example/x"
        b64 = ""
        mime = ""
        local_path = ""

    monkeypatch.setattr(mcp_artifacts, "_fetch_generated", lambda url, **kw: (data, said))
    assert mcp_artifacts._materialize_image(_Result()) == (data, named)
