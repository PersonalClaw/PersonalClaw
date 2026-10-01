"""A recording is never "done" without its transcript, and says why it has none.

Measured in live use: a six-minute screen recording, whose narration av_split turned into a
360 s WAV with speech in it, came back from the bound speech-to-text model with nothing
(``None``). The pipeline read that as a recording with no speech in it, so the item's
Transcription step read "done" and its only text was the first slide's OCR. Nothing, anywhere,
said the narration had not been transcribed.

These drive a whole ingest (the real graph, runner and transcription node; only the other
model-backed steps stubbed) and read what the item then says: its status, its status line, its
per-step phases and its ``file_metadata``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import personalclaw.knowledge.pipeline.executor as ex
import personalclaw.knowledge.pipeline.registry as reg
from personalclaw.knowledge.pipeline import ensure_nodes_registered, graph_for
from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.pipeline.types import NodeOutput
from personalclaw.knowledge.searchability import NO_EXTRACTABLE_TEXT
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.stt.provider import TranscriptResult
from personalclaw.transcribe import NO_TRANSCRIPT_NO_REASON

SLIDE_TEXT = "Step 0 of 9. Check that the main branch is green before you tag a release."


class _Embedder:
    """A bound embedding provider, so no verdict here is about embeddings."""

    def embed_for_item(self, title, summary, content):
        return [0.1, 0.2, 0.3, 0.4]

    def embed(self, text):
        return [0.1, 0.2, 0.3, 0.4]

    def is_available(self):
        return True


@pytest.fixture(autouse=True)
def _graph(monkeypatch):
    """Every model use case is served except diarization (none is bound), and the Lexicon's
    bias hook is out of the way: neither is what these measure."""
    ensure_nodes_registered()

    async def _unserved(use_case):
        return "no model serves the diarization use case" if use_case == "diarization" else ""

    monkeypatch.setattr(ex, "unserved_reason", _unserved)
    monkeypatch.setattr(
        "personalclaw.knowledge.pipeline.nodes.media_nodes._lexicon_bias_terms",
        AsyncMock(return_value=None),
    )


def _stt_answers(result):
    """Bind a speech-to-text provider whose detailed transcription answers *result*."""
    provider = MagicMock()
    provider.transcribe_detailed = AsyncMock(return_value=result)
    return provider, (
        patch(
            "personalclaw.providers.use_cases.load_use_case_settings",
            return_value={"enabled": True},
        ),
        patch("personalclaw.security.is_sensitive_path", return_value=False),
        patch("personalclaw.stt.registry.active_stt", return_value=(provider, "turbo")),
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


def _stub_the_video_arm(monkeypatch, tmp_path: Path) -> None:
    """The frame arm of the video graph, stubbed: split, frames, a text-heavy verdict, the
    first slide's OCR, and a consolidation that keeps what it was given."""
    wav = tmp_path / "v.audio.wav"
    wav.write_bytes(b"\x00" * 64)
    frame = tmp_path / "v.frame_001.jpg"
    frame.write_bytes(b"\xff\xd8\xff")

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
            node_type="frame_extract", backend="ffmpeg", pooled=False, artifacts=[str(frame)]
        )

    async def _classify(inputs, ctx):
        return NodeOutput(
            node_type="video_classify",
            backend="vision-llm",
            classification="text-heavy",
            artifacts=[str(frame)],
            pooled=False,
        )

    async def _ocr(inputs, ctx):
        return NodeOutput(node_type="ocr", backend="vision-llm", text=SLIDE_TEXT)

    async def _consolidate(inputs, ctx):
        merged = "\n".join(o.text for o in inputs.values() if o and o.text)
        return NodeOutput(node_type="video_consolidate", backend="reasoning-llm", text=merged)

    graph = graph_for("video")
    for node_type, fn in (
        ("av_split", _split),
        ("frame_extract", _frames),
        ("video_classify", _classify),
        ("ocr", _ocr),
        ("video_consolidate", _consolidate),
    ):
        monkeypatch.setattr(reg.get_node(node_type, graph.nodes[node_type].backend), "run", fn)


def _phases(item: dict) -> dict:
    """Each step's recorded status, from the item's per-step outcome map."""
    phases = (item.get("file_metadata") or {}).get("node_phases") or {}
    return {step: outcome.get("status") for step, outcome in phases.items()}


def test_a_video_whose_narration_came_back_with_nothing_says_transcription_failed(
    monkeypatch, tmp_path
):
    """🔴 Red before: status ``done``, Transcription ``done``, no reason anywhere."""
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    _stub_the_video_arm(monkeypatch, tmp_path)
    item_id = _item(store, tmp_path, "video", "release screencast.mov", "video/quicktime")
    provider, binding = _stt_answers(None)
    with binding[0], binding[1], binding[2]:
        asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))

    item = store.get_item(item_id)
    assert provider.transcribe_detailed.await_count == 1, "the narration was never handed over"
    assert _phases(item).get("transcription") == "failed"
    assert item["processing_status"] == "partial"
    assert f"transcription: {NO_TRANSCRIPT_NO_REASON}" in (item.get("processing_error") or "")
    # The slide's text is still the item's: a failed narration does not cost the frames.
    assert SLIDE_TEXT in (item.get("content") or "")
    assert "no_speech" not in (item.get("file_metadata") or {})


def test_a_video_whose_narration_was_transcribed_is_done_with_it(monkeypatch, tmp_path):
    """The control: the same video, with a transcript, is done and its words are the item's."""
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    _stub_the_video_arm(monkeypatch, tmp_path)
    item_id = _item(store, tmp_path, "video", "release screencast.mov", "video/quicktime")
    spoken = "Step one. Make sure main is green."
    _provider, binding = _stt_answers(TranscriptResult(text=spoken, duration=360.0))
    with binding[0], binding[1], binding[2]:
        asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))

    item = store.get_item(item_id)
    assert _phases(item).get("transcription") == "done"
    assert spoken in (item.get("content") or "")
    assert "no_speech" not in (item.get("file_metadata") or {})


def test_a_recording_with_no_speech_says_so_and_is_not_called_a_scan(monkeypatch, tmp_path):
    """🔴 Red before: the empty transcript pooled as a "lying extractor", so a silent memo was
    told it "had no text that could be extracted (a scan or an image-only PDF)", with OCR
    advice, and nothing on the item said there was no speech in it."""
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    item_id = _item(store, tmp_path, "audio", "room tone.m4a", "audio/mp4")
    _provider, binding = _stt_answers(TranscriptResult(text="", duration=12.0))
    with binding[0], binding[1], binding[2]:
        asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))

    item = store.get_item(item_id)
    meta = item.get("file_metadata") or {}
    assert meta.get("no_speech") is True
    assert _phases(item).get("transcription") == "done"
    assert meta.get("unsearchable_reason") != NO_EXTRACTABLE_TEXT
    assert "scan" not in (item.get("processing_error") or "")


def test_an_audio_item_whose_transcription_failed_is_told_that_not_scan_advice(
    monkeypatch, tmp_path
):
    """🔴 Red before: ``done`` / ``unsearchable`` with the scan sentence; the transcription's
    failure was invisible behind it."""
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    item_id = _item(store, tmp_path, "audio", "morning memo.m4a", "audio/mp4")
    _provider, binding = _stt_answers(None)
    with binding[0], binding[1], binding[2]:
        asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))

    item = store.get_item(item_id)
    assert _phases(item).get("transcription") == "failed"
    assert item["processing_status"] == "failed"
    assert (item.get("processing_error") or "").startswith(
        f"transcription: {NO_TRANSCRIPT_NO_REASON}"
    )
    assert "scan" not in (item.get("processing_error") or "")


def test_a_recording_that_failed_twice_leads_with_why_it_has_no_transcript(monkeypatch, tmp_path):
    """🔴 Found by uploading a damaged memo: both the transcription and the diarization failed,
    the diarization failed FIRST, so its long reason led the status line and the 500-character
    cap cut off the transcription's, the one that says why there is no transcript. The reasons
    now follow the graph's step order, whichever step failed first."""
    from personalclaw.diarization import DiarizationError

    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    item_id = _item(store, tmp_path, "audio", "damaged memo.m4a", "audio/mp4")

    async def _served(use_case):
        return ""

    monkeypatch.setattr(ex, "unserved_reason", _served)
    long_reason = "ONNX diarization could not read this recording. " + "It said: " + "x" * 480
    monkeypatch.setattr(
        "personalclaw.diarize.diarize_audio",
        AsyncMock(side_effect=DiarizationError(long_reason)),
    )

    async def _slow_nothing(*args, **kwargs):
        await asyncio.sleep(0.2)  # the diarization has failed by the time this answers
        return None

    provider, binding = _stt_answers(None)
    provider.transcribe_detailed = _slow_nothing
    with binding[0], binding[1], binding[2]:
        asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))

    item = store.get_item(item_id)
    assert _phases(item).get("diarization") == "failed"
    assert _phases(item).get("transcription") == "failed"
    error = item.get("processing_error") or ""
    assert error.startswith(f"transcription: {NO_TRANSCRIPT_NO_REASON}; diarization: ONNX")
