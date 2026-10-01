"""Every step an ingest did not run records its own outcome: what happened, why, and the fix.

Measured in live use: a screen recording ingested with no model bound to Image · Modality and
no OCR engine listed Video classify, OCR, Vision and Intents as "skipped", and its only reason
line was "Skipped (optional steps unavailable): video_classify". Nothing said why OCR and
Vision were skipped, or what would make them run; the item's eight extracted frames were never
read. The persisted phase map held bare words (``"skipped"``), and ``processing_error`` named
one step of four.

These drive a whole ingest through the real graph, executor and runner (only the steps that
need real media or a real model are stubbed) and read what the item then records per step:
``file_metadata.node_phases[<step>]`` is ``{status, reason, fix, needs}``. Three outcomes are
told apart: a step skipped for want of something the owner can add (``skipped``, with a fix),
a step that ran and failed (``failed``, with the error), and a step that does not apply to this
item (``not_applicable``, with no fix).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import personalclaw.knowledge.pipeline.registry as reg
from personalclaw.knowledge.pipeline import ensure_nodes_registered, graph_for
from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.pipeline.types import NodeOutput
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.providers.image_input import NO_IMAGE_MODEL, ImageReader

FIXTURES = Path(__file__).parent / "fixtures"
MODELS = "#/settings/models"
OCR_APPS = "#/apps?view=store&stag=ocr"
TRANSCRIPT = "Tag the release, then publish it from the release page."


class _Embedder:
    """A bound embedding provider, so nothing here is about embeddings."""

    def embed_for_item(self, title, summary, content):
        return [0.1, 0.2, 0.3, 0.4]

    def embed(self, text):
        return [0.1, 0.2, 0.3, 0.4]

    def is_available(self):
        return True


@pytest.fixture(autouse=True)
def _models(monkeypatch):
    """Speech-to-text and Chat are served; nothing is bound to Image · Modality or Speaker
    diarization, the chat model takes no images, and no OCR engine is installed. Patched at the
    platform's probes (not at the executor), so the sentences under test are the real ones."""
    ensure_nodes_registered()
    state = {
        "reader": ImageReader(reason=NO_IMAGE_MODEL),
        "ocr": False,
        "served": {"stt", "chat"},
        "bound": {},
    }

    async def _reader():
        return state["reader"]

    monkeypatch.setattr("personalclaw.providers.image_input.image_reader", _reader)
    monkeypatch.setattr(
        "personalclaw.providers.provider_bridge.can_resolve_use_case",
        lambda uc: uc in state["served"],
    )
    monkeypatch.setattr(
        "personalclaw.providers.use_cases.active_model_refs",
        lambda uc: [state["bound"][uc]] if uc in state["bound"] else [],
    )
    monkeypatch.setattr("personalclaw.ocr.registry.ocr_available", lambda: state["ocr"])
    monkeypatch.setattr(
        "personalclaw.knowledge.pipeline.nodes.media_nodes._lexicon_bias_terms",
        AsyncMock(return_value=None),
    )
    return state


def _stub(monkeypatch, item_type: str, **runs) -> None:
    graph = graph_for(item_type)
    for node_type, fn in runs.items():
        monkeypatch.setattr(reg.get_node(node_type, graph.nodes[node_type].backend), "run", fn)


def _stub_video(monkeypatch, tmp_path: Path) -> None:
    """The split, the frames, the transcript and the consolidation: the steps that need real
    media or a real model and are served here."""
    wav = tmp_path / "v.audio.wav"
    wav.write_bytes(b"\x00" * 64)
    frames = []
    for n in range(1, 9):
        frame = tmp_path / f"v.frame_{n:03d}.jpg"
        frame.write_bytes(b"\xff\xd8\xff")
        frames.append(str(frame))

    async def _split(inputs, ctx):
        return NodeOutput(
            node_type="av_split",
            backend="ffmpeg",
            pooled=False,
            metadata={"audio": str(wav), "video": ctx.file_path},
            artifacts=[str(wav)],
        )

    async def _frames(inputs, ctx):
        return NodeOutput(
            node_type="frame_extract", backend="ffmpeg", pooled=False, artifacts=frames
        )

    async def _transcribe(inputs, ctx):
        return NodeOutput(node_type="transcription", backend="stt", text=TRANSCRIPT)

    async def _consolidate(inputs, ctx):
        merged = "\n".join(o.text for o in inputs.values() if o and o.text)
        return NodeOutput(node_type="video_consolidate", backend="reasoning-llm", text=merged)

    _stub(
        monkeypatch,
        "video",
        av_split=_split,
        frame_extract=_frames,
        transcription=_transcribe,
        video_consolidate=_consolidate,
    )


def _item(store: KnowledgeStore, home: Path, item_type: str, name: str, mime: str) -> str:
    media = Path(knowledge_db_path(home)).parent / name
    media.write_bytes(b"\x00" * 64)
    item_id = store.create_typed_item(
        item_type=item_type,
        title=name,
        content="",
        extra={
            "file_path": str(media),
            "mime_type": mime,
            "file_size": 64,
            "processing_status": "queued",
        },
    )
    assert item_id
    return item_id


def _phases(store: KnowledgeStore, item_id: str) -> dict:
    return (store.get_item(item_id).get("file_metadata") or {}).get("node_phases") or {}


def _fix_hrefs(outcome: dict) -> list[str]:
    return [f.get("href") for f in outcome.get("fix") or []]


def _ingest_video(monkeypatch, tmp_path) -> tuple[KnowledgeStore, str]:
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    _stub_video(monkeypatch, tmp_path)
    item_id = _item(store, tmp_path, "video", "release walkthrough.mov", "video/quicktime")
    asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))
    return store, item_id


def test_a_video_with_no_image_model_and_no_ocr_engine_says_why_each_step_was_skipped(
    monkeypatch, tmp_path
):
    """🔴 Red before: the phase map held bare words and only video_classify was named."""
    store, item_id = _ingest_video(monkeypatch, tmp_path)
    phases = _phases(store, item_id)

    skipped = {nt for nt, o in phases.items() if o.get("status") == "skipped"}
    assert {"video_classify", "ocr", "vision", "diarization"} <= skipped, phases
    for node_type in skipped:
        outcome = phases[node_type]
        assert outcome.get("reason", "").strip(), (node_type, outcome)
        assert outcome.get("fix"), (node_type, outcome)
        assert all(f.get("text", "").strip() for f in outcome["fix"]), (node_type, outcome)

    # The classifier names what it is missing and where to add it.
    classify = phases["video_classify"]
    assert classify["reason"] == NO_IMAGE_MODEL
    assert _fix_hrefs(classify) == [MODELS]
    assert "Image · Modality" in classify["fix"][0]["text"]
    assert classify["needs"] == ["image_modality"]

    # OCR and Vision were never offered a frame: each says so, and why, in its own words.
    for node_type in ("ocr", "vision"):
        outcome = phases[node_type]
        assert "Video classify" in outcome["reason"], outcome
        assert "no image model is set up" in outcome["reason"], outcome
        assert _fix_hrefs(outcome) == [MODELS], outcome
        assert outcome["needs"] == ["image_modality"], outcome

    # Speaker diarization's own missing model, not the image model.
    diarize = phases["diarization"]
    assert "Speaker diarization" in diarize["reason"], diarize
    assert "Speaker diarization" in diarize["fix"][0]["text"]

    # The steps that ran say so; the item is partial and carries no catch-all skip line.
    for node_type in ("av_split", "transcription", "frame_extract", "video_consolidate"):
        assert phases[node_type]["status"] == "done", (node_type, phases[node_type])
    item = store.get_item(item_id)
    assert item["processing_status"] == "partial"
    assert "optional steps unavailable" not in (item.get("processing_error") or "")


def test_a_step_with_nothing_to_do_is_not_applicable_and_offers_no_fix(monkeypatch, tmp_path):
    """No intents are defined: intent matching had nothing to look for. That is neither a
    missing capability nor a failure."""
    store, item_id = _ingest_video(monkeypatch, tmp_path)
    intents = _phases(store, item_id)["intents"]
    assert intents["status"] == "not_applicable", intents
    assert intents.get("reason", "").strip(), intents
    assert not intents.get("fix"), intents


def test_an_image_names_both_ways_to_read_its_text(monkeypatch, tmp_path):
    """An image's OCR can run on an image model OR an installed OCR engine, so its fix names
    both; Vision has only the image model."""
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))

    async def _exif(inputs, ctx):
        return NodeOutput(
            node_type="exif", backend="pillow", pooled=False, metadata={"width": 4, "height": 4}
        )

    _stub(monkeypatch, "image", exif=_exif)
    item_id = _item(store, tmp_path, "image", "whiteboard.png", "image/png")
    asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))
    phases = _phases(store, item_id)

    ocr = phases["ocr"]
    assert ocr["status"] == "skipped", ocr
    assert NO_IMAGE_MODEL in ocr["reason"] and "OCR engine" in ocr["reason"], ocr
    assert _fix_hrefs(ocr) == [MODELS, OCR_APPS], ocr
    assert set(ocr["needs"]) == {"image_modality", "ocr_engine"}, ocr

    vision = phases["vision"]
    assert vision["status"] == "skipped" and vision["reason"] == NO_IMAGE_MODEL, vision
    assert _fix_hrefs(vision) == [MODELS], vision


def test_a_text_layer_pdf_does_not_need_its_scan_steps(tmp_path):
    """The scan branch (rasterize → OCR) does not apply to a PDF that has a text layer: those
    steps are not applicable, never "skipped" with a fix for something the document never
    needed."""
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    dest = Path(knowledge_db_path(tmp_path)).parent / "text_layer.pdf"
    shutil.copy(FIXTURES / "text_layer.pdf", dest)
    item_id = store.create_typed_item(
        item_type="document",
        title="text_layer.pdf",
        content="",
        extra={
            "file_path": str(dest),
            "mime_type": "application/pdf",
            "file_size": dest.stat().st_size,
            "processing_status": "queued",
        },
    )
    asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))
    phases = _phases(store, item_id)

    assert phases["document_read"]["status"] == "done", phases["document_read"]
    for node_type in ("pdf_rasterize", "ocr"):
        outcome = phases[node_type]
        assert outcome["status"] == "not_applicable", (node_type, outcome)
        assert outcome.get("reason", "").strip(), (node_type, outcome)
        assert not outcome.get("fix"), (node_type, outcome)


def test_a_failed_step_records_its_error_as_its_reason(monkeypatch, tmp_path):
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    _stub_video(monkeypatch, tmp_path)

    async def _deaf(inputs, ctx):
        return NodeOutput(
            node_type="transcription",
            backend="stt",
            success=False,
            error="The speech-to-text model could not read this audio.",
        )

    _stub(monkeypatch, "video", transcription=_deaf)
    item_id = _item(store, tmp_path, "video", "screencast.mov", "video/quicktime")
    asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))

    outcome = _phases(store, item_id)["transcription"]
    assert outcome["status"] == "failed", outcome
    assert outcome["reason"] == "The speech-to-text model could not read this audio."
    assert not outcome.get("fix"), outcome


def _graph(store: KnowledgeStore, item_id: str) -> dict:
    from personalclaw.dashboard.handlers import knowledge as H

    app = web.Application()
    app["state"] = SimpleNamespace(knowledge_store=store)
    request = make_mocked_request("GET", "/", app=app, match_info={"id": item_id})
    return json.loads(asyncio.run(H.get_item_graph(request)).body)


def test_the_item_offers_a_rerun_once_the_missing_model_is_set_up(monkeypatch, tmp_path, _models):
    """The shape the item page draws marks a skipped step ``ready`` once what it needed is
    there, so the page can offer to run the item again. Read live, not at ingest."""
    store, item_id = _ingest_video(monkeypatch, tmp_path)

    before = _graph(store, item_id)["node_phases"]
    assert before["video_classify"]["ready"] is False
    assert before["ocr"]["ready"] is False

    # She chooses a model for Image · Modality, as the fix says.
    _models["bound"]["image_modality"] = "local:vision-model"
    _models["served"].add("image_modality")
    after = _graph(store, item_id)["node_phases"]
    for node_type in ("video_classify", "ocr", "vision"):
        assert after[node_type]["ready"] is True, (node_type, after[node_type])
    # Diarization still has no model, so it is still not ready.
    assert after["diarization"]["ready"] is False
    # Steps that were not skipped carry no readiness at all.
    assert "ready" not in after["transcription"]


def test_an_installed_ocr_engine_readies_an_images_ocr_but_not_its_vision(
    monkeypatch, tmp_path, _models
):
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))

    async def _exif(inputs, ctx):
        return NodeOutput(node_type="exif", backend="pillow", pooled=False, metadata={})

    _stub(monkeypatch, "image", exif=_exif)
    item_id = _item(store, tmp_path, "image", "receipt.png", "image/png")
    asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))

    _models["ocr"] = True
    phases = _graph(store, item_id)["node_phases"]
    assert phases["ocr"]["ready"] is True
    assert phases["vision"]["ready"] is False


def test_a_library_from_before_records_each_phase_as_an_outcome(tmp_path):
    """An item processed before steps recorded their outcomes held bare words and a catch-all
    skip line. Opening the store rewrites them once, in place: every phase becomes an outcome,
    and the catch-all line goes (the phases now carry it)."""
    db = str(knowledge_db_path(tmp_path))
    store = KnowledgeStore(db)
    item_id = store.create_typed_item(item_type="note", title="old", content="text")
    store.update_item(
        item_id,
        processing_status="partial",
        processing_error="Skipped (optional steps unavailable): video_classify, ocr",
        file_metadata={
            "width": 4,
            "node_phases": {"av_split": "done", "ocr": "skipped", "insights": "failed"},
        },
        touch=False,
    )
    store.db.commit()
    other = store.create_typed_item(item_type="note", title="mixed", content="text")
    store.update_item(
        other,
        processing_status="partial",
        processing_error=(
            "insights: model unavailable (insights not refreshed — try regenerating); "
            "Skipped (optional steps unavailable): vision"
        ),
        touch=False,
    )
    store.db.commit()

    reopened = KnowledgeStore(db)
    item = reopened.get_item(item_id)
    phases = item["file_metadata"]["node_phases"]
    assert phases["av_split"] == {"status": "done"}
    assert phases["insights"] == {"status": "failed"}
    assert phases["ocr"]["status"] == "skipped" and phases["ocr"]["reason"].strip()
    assert item["file_metadata"]["width"] == 4
    assert not item.get("processing_error")
    assert reopened.get_item(other)["processing_error"] == (
        "insights: model unavailable (insights not refreshed — try regenerating)"
    )

    # Idempotent: a second open finds nothing left to rewrite.
    again = KnowledgeStore(db).get_item(item_id)["file_metadata"]["node_phases"]
    assert again == phases


def test_a_chat_attachment_image_still_says_no_image_model_is_set_up(tmp_path):
    """The attachment path reads the same outcomes: an image nothing can read is described as
    not read for want of an image model, as before."""
    from personalclaw.knowledge.extract import UNREAD_NO_IMAGE_MODEL, extract_file

    image = tmp_path / "chart.png"
    from PIL import Image

    Image.new("RGB", (8, 8), "white").save(image, format="PNG")
    got = asyncio.run(extract_file(str(image), "image/png"))
    assert got.read is False
    assert got.unread == UNREAD_NO_IMAGE_MODEL


def test_a_skipped_step_offers_no_link_outside_the_app():
    """Every fix a step can carry points inside the app (a ``#/`` route) — the page renders it
    as a link, so nothing but an in-app route may ever be stored there."""
    from personalclaw.knowledge.pipeline import outcomes

    for fix in (outcomes.model_fix("image_modality"), outcomes.ocr_engine_fix()):
        assert fix.href.startswith("#/"), fix


def test_the_probes_patched_here_are_the_ones_the_executor_reads():
    """A positive control: the reason sentence comes from the registry's probe, so patching the
    platform's image reader must change it. Without this, a registry that stopped asking the
    image reader would leave the assertions above reading a constant."""
    with patch(
        "personalclaw.providers.image_input.image_reader",
        AsyncMock(return_value=ImageReader(reason="An image reader is busy.")),
    ):
        assert asyncio.run(reg.unserved_reason("image_modality")) == "An image reader is busy."


def test_investigating_the_item_reads_each_skipped_step_and_its_fix(monkeypatch, tmp_path):
    """The catch-all skip line used to reach Investigate through the status line. Each step's
    own outcome reaches it now, with its reason and fix; a step that ran or does not apply
    adds nothing."""
    from personalclaw import investigate as inv

    store, item_id = _ingest_video(monkeypatch, tmp_path)
    ctx = asyncio.run(
        inv.resolve("knowledge_item", item_id, SimpleNamespace(knowledge_store=store))
    )
    steps = [line for line in ctx.snapshot.splitlines() if line.startswith("Step ")]
    assert steps[0] == (
        "Step Diarization skipped: No Speaker diarization model is set up. "
        "Fix: Choose a model for Speaker diarization in Settings → Models."
    ), steps
    assert any(
        line.startswith("Step Video classify skipped: No image model is set up.") for line in steps
    )
    assert any(line.startswith("Step OCR skipped: It needs Video classify first") for line in steps)
    assert any(
        line.startswith("Step Vision skipped: It needs Video classify first") for line in steps
    )
    assert not any("Intents" in line or "Transcription" in line for line in steps), steps


def test_a_step_that_waits_on_several_names_one_way_to_fix_them(monkeypatch, tmp_path, _models):
    """With no Speech-to-text model either, the video's consolidation waits on three skipped
    arms. Any one of them would let it run, so its fix is one sentence naming the three models
    with "or" — not three "Choose a model…" links to the same page."""
    _models["served"] = {"chat"}
    store, item_id = _ingest_video(monkeypatch, tmp_path)
    consolidate = _phases(store, item_id)["video_consolidate"]
    assert consolidate["status"] == "skipped", consolidate
    assert consolidate["fix"] == [
        {
            "text": (
                "Choose a model for Speech-to-text, Speaker diarization or Image · Modality "
                "in Settings → Models"
            ),
            "href": MODELS,
        }
    ], consolidate
    assert set(consolidate["needs"]) == {"stt", "diarization", "image_modality"}
