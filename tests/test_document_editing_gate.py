"""DFE-5 — the ``document_editing`` consent gate and the lossy-edit contract's two
server-side halves.

Three clauses of the atom live on the server and are proven here rather than asserted
in the UI:

* **The switch is a GATE, not a preference.** ``dashboard.document_editing`` is OFF by
  default and ``PUT /api/artifacts/{slug}/model`` refuses while it is off — so "off
  restores today's read-only preview" holds for a client that never loaded our UI, not
  just for the one that hides its editor button.
* **A bold word survives into the file.** The editor posts a MODEL; the shipped writer
  renders it; the stored bytes must open bold in Word. Read back with python-docx, which
  is the same library Word's own formatting maps onto.
* **A lossy edit is recoverable exactly.** Every model write bumps a version, so the
  pre-edit body is one ``revert`` away — asserted by BYTE comparison, because "close
  enough" is precisely the failure a lossy re-render produces.

Plus the two config round-trip points ``test_config_roundtrip.py`` does not cover: the
PATCH allowlist entry and the Settings panel's own write path.
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw.artifacts import registry
from personalclaw.artifacts.handlers import register_artifact_routes
from personalclaw.artifacts.models import mime_for_ext
from personalclaw.artifacts.native import NativeArtifactProvider
from personalclaw.config.loader import AppConfig
from personalclaw.dashboard.handlers.core import _EDITABLE_CONFIG
from personalclaw.documents.docx_parser import parse_docx
from personalclaw.documents.model import Block, DocumentModel, Run
from personalclaw.documents.model_json import document_to_dict
from personalclaw.documents.writers.docx_writer import render_docx

DOCX_MIME = mime_for_ext("docx")


def _authored() -> DocumentModel:
    return DocumentModel(
        title="Fidelity",
        blocks=[
            Block(kind="heading", text="Overview", level=1),
            Block(kind="paragraph", runs=[Run(text="a plain word")]),
        ],
    )


def _settled_docx_bytes(model: DocumentModel | None = None) -> bytes:
    """Rendered, parsed and re-rendered, so a further parse is lossless (the incidental
    python-docx template margin loss is settled out — see the DFE-4 suite's note)."""
    parsed, _ = parse_docx(render_docx(model if model is not None else _authored()))
    return render_docx(parsed)


@pytest.fixture
def provider(tmp_path) -> NativeArtifactProvider:
    return NativeArtifactProvider(root=tmp_path / "artifacts")


@pytest.fixture
def patched_native(provider):
    with patch.object(registry, "get_provider", return_value=provider):
        yield provider


def _config(*, document_editing: bool) -> AppConfig:
    cfg = AppConfig()
    cfg.dashboard.document_editing = document_editing
    return cfg


def _editing(enabled: bool):
    """Patch ``AppConfig.load`` with a REAL default config carrying one flipped flag —
    not a mock, so the route reads the same object shape it reads in production."""
    return patch.object(AppConfig, "load", staticmethod(lambda: _config(document_editing=enabled)))


def _state() -> MagicMock:
    state = MagicMock()
    state._restricted_keys = set()
    state._sessions = {}
    return state


async def _client() -> TestClient:
    app = web.Application()
    app["state"] = _state()
    register_artifact_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


def _docx_artifact(provider):
    return provider.create_binary(
        name="Report", data=_settled_docx_bytes(), mime=DOCX_MIME, kind="docx", actor="agent"
    )


def _bolded(model: DocumentModel) -> DocumentModel:
    """What the editor's "select a word, press B" produces: the paragraph's single run
    split into three, the middle one bold."""
    para = model.blocks[1]
    text = para.runs[0].text if para.runs else para.text
    head, word, tail = text.partition("plain")
    para.runs = [Run(text=head), Run(text=word, bold=True), Run(text=tail)]
    para.text = ""  # let __post_init__ re-derive the plain view from the runs
    return DocumentModel(title=model.title, blocks=model.blocks, page=model.page)


# ── the gate ─────────────────────────────────────────────────────────────────


def test_the_flag_is_off_by_default() -> None:
    """A fresh install must not have a lossy re-render path available."""
    assert AppConfig().dashboard.document_editing is False


@pytest.mark.asyncio
async def test_a_model_write_is_refused_while_document_editing_is_off(patched_native) -> None:
    art = _docx_artifact(patched_native)
    before = patched_native.raw_bytes(art.slug)
    with _editing(False):
        client = await _client()
        try:
            resp = await client.put(
                f"/api/artifacts/{art.slug}/model",
                json={"model": document_to_dict(_bolded(_authored()))},
                headers={"If-Match": str(art.version)},
            )
            body = await resp.json()
        finally:
            await client.close()
    assert resp.status == 403
    assert body["error"]["code"] == "document_editing_off"
    # Not just refused — nothing moved. A refusal that still bumped a version would be
    # the silent-loss failure this gate exists to prevent.
    after = patched_native.get(art.slug)
    assert after is not None and after.version == art.version
    assert patched_native.raw_bytes(art.slug) == before


@pytest.mark.asyncio
async def test_the_same_write_is_accepted_once_the_flag_is_on(patched_native) -> None:
    """The vacuity half of the test above: the refusal is the FLAG's doing, not a
    malformed request that would have been refused either way."""
    art = _docx_artifact(patched_native)
    with _editing(True):
        client = await _client()
        try:
            resp = await client.put(
                f"/api/artifacts/{art.slug}/model",
                json={"model": document_to_dict(_bolded(_authored()))},
                headers={"If-Match": str(art.version)},
            )
        finally:
            await client.close()
    assert resp.status == 200, await resp.text()
    after = patched_native.get(art.slug)
    assert after is not None and after.version == art.version + 1


# ── the bold word reaches the FILE ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_bolded_word_is_bold_in_the_stored_document(patched_native) -> None:
    """The end of the user's sentence: bold a word, save, open the download in Word.
    Read back through python-docx — the run's own ``bold``, not our model's."""
    from docx import Document  # local: the read-back oracle, not a production import

    art = _docx_artifact(patched_native)
    with _editing(True):
        client = await _client()
        try:
            loaded = await (await client.get(f"/api/artifacts/{art.slug}/model")).json()
            model, _ = parse_docx(_settled_docx_bytes())
            resp = await client.put(
                f"/api/artifacts/{art.slug}/model",
                json={"model": document_to_dict(_bolded(model))},
                headers={"If-Match": str(loaded["version"])},
            )
        finally:
            await client.close()
    assert resp.status == 200, await resp.text()
    data, _ = patched_native.raw_bytes(art.slug)
    doc = Document(BytesIO(data))
    bolded = [
        run.text for para in doc.paragraphs for run in para.runs if run.bold and run.text.strip()
    ]
    assert bolded == ["plain"], f"expected only 'plain' bold, got {bolded}"


# ── a lossy edit is recoverable EXACTLY ──────────────────────────────────────


@pytest.mark.asyncio
async def test_revert_restores_the_pre_edit_bytes_exactly(patched_native) -> None:
    """§C5's third clause. Not "the document looks the same" — the same BYTES, because a
    re-render that differs by a single attribute has already lost something."""
    art = _docx_artifact(patched_native)
    original, _ = patched_native.raw_bytes(art.slug)
    with _editing(True):
        client = await _client()
        try:
            model, _ = parse_docx(original)
            resp = await client.put(
                f"/api/artifacts/{art.slug}/model",
                json={"model": document_to_dict(_bolded(model))},
                headers={"If-Match": str(art.version)},
            )
            assert resp.status == 200, await resp.text()
        finally:
            await client.close()
    edited, _ = patched_native.raw_bytes(art.slug)
    assert edited != original, "the edit did not change the file — nothing to revert"

    reverted = patched_native.revert(art.slug, art.version, actor="user")
    assert reverted is not None
    restored, _ = patched_native.raw_bytes(art.slug)
    assert restored == original


# ── the two config points test_config_roundtrip.py does not cover ────────────


def test_the_flag_is_in_the_patch_allowlist() -> None:
    """Point 4 of the round trip: without this entry the PATCH handler drops the key and
    the Settings toggle appears to work while reverting on reload."""
    assert _EDITABLE_CONFIG["dashboard.document_editing"] == {"type": "bool"}


def test_the_flag_survives_load_and_to_dict(tmp_path) -> None:
    """Points 2 and 3 read explicitly, on the value that is NOT the default — a field
    missing from ``load()``'s mapping silently reverts, which reads as "the toggle
    didn't stick"."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"dashboard": {"document_editing": True}}), encoding="utf-8")
    with patch("personalclaw.config.loader.config_path", return_value=path):
        cfg = AppConfig.load()
        assert cfg.dashboard.document_editing is True
        assert cfg.to_dict()["dashboard"]["document_editing"] is True


@pytest.mark.asyncio
async def test_the_settings_panel_write_path_accepts_the_flag(tmp_path, monkeypatch) -> None:
    """Point 4's other half — the panel the Settings toggle actually drives
    (``PUT /api/dashboard/config``), which has its OWN allowlist. A field missing there
    is a 400 "Unknown fields", i.e. a toggle that cannot be turned on at all."""
    from personalclaw.dashboard.handlers import files as files_mod

    path = tmp_path / "config.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("personalclaw.config.loader.config_path", lambda: path)

    app = web.Application()
    app["state"] = _state()
    app.router.add_route("*", "/api/dashboard/config", files_mod.api_dashboard_config)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        assert (await (await client.get("/api/dashboard/config")).json())["document_editing"] is (
            False
        )
        resp = await client.put("/api/dashboard/config", json={"document_editing": True})
        assert resp.status == 200, await resp.text()
        assert (await (await client.get("/api/dashboard/config")).json())["document_editing"] is (
            True
        )
        bad = await client.put("/api/dashboard/config", json={"document_editing": "yes"})
        assert bad.status == 400
    finally:
        await client.close()
    assert json.loads(path.read_text(encoding="utf-8"))["dashboard"]["document_editing"] is True
