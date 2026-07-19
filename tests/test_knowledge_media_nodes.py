"""Media + video pipeline nodes + the conditional video DAG (#47)."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

import personalclaw.knowledge.pipeline.executor as ex
import personalclaw.knowledge.pipeline.registry as reg
from personalclaw.knowledge.pipeline import ensure_nodes_registered, graph_for
from personalclaw.knowledge.pipeline.executor import PipelineExecutor
from personalclaw.knowledge.pipeline.types import NodeContext


def _set_resolvable(monkeypatch, fn):
    """Control use-case resolvability for the executor. The executor binds
    ``can_resolve_use_case`` at import (``from registry import …``), so patch it in
    the EXECUTOR's namespace — patching registry's wouldn't reach it."""
    monkeypatch.setattr(ex, "can_resolve_use_case", fn)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _nodes():
    ensure_nodes_registered()


# ── use-case wiring (Q1 hybrid) ──


def test_ingestion_nodes_use_default_capability_bindings():
    """Ingestion has NO dedicated use-cases — each model-backed node resolves DIRECTLY
    to the relevant default capability (image-understanding → image_modality, reasoning
    → chat, transcription → stt). There is no per-role ingestion override."""
    from personalclaw.knowledge.pipeline.nodes.media_nodes import (
        OcrNode, VisionNode, VideoClassifyNode, VideoConsolidateNode, TranscriptionNode,
    )
    from personalclaw.providers.use_cases import VALID_USE_CASES

    assert OcrNode.uses_use_case == "image_modality"
    assert VisionNode.uses_use_case == "image_modality"
    assert VideoClassifyNode.uses_use_case == "image_modality"
    assert VideoConsolidateNode.uses_use_case == "chat"
    assert TranscriptionNode.uses_use_case == "stt"
    # every use-case a node points at is a real capability
    for uc in ("image_modality", "chat", "stt"):
        assert uc in VALID_USE_CASES
    # the old ingestion use-cases are GONE (removed dead override rows)
    for uc in ("pdf_extraction", "ocr", "vision_understanding", "video_classification", "consolidation_reasoning"):
        assert uc not in VALID_USE_CASES


def test_use_case_parent_no_ingestion_fallback():
    from personalclaw.providers.use_cases import parent_capability

    # chat sub-categories still fall back to chat; everything else is its own parent.
    assert parent_capability("reasoning") == "chat"
    assert parent_capability("code_tools") == "chat"
    assert parent_capability("image_modality") == "image_modality"
    assert parent_capability("stt") == "stt"


# ── graph shapes ──


def test_video_graph_is_conditional_dag():
    g = graph_for("video")
    g.validate()  # acyclic
    conds = [(e.from_node, e.to_node, e.when) for e in g.edges if e.when]
    assert ("video_classify", "ocr", "text-heavy") in conds
    assert ("video_classify", "vision", "visual") in conds
    # fan-in to consolidate — the transcript arm now flows through lexicon_correction
    # (LEX.4) before consolidation; ocr/vision arms fan in directly.
    preds = {e.from_node for e in g.predecessors("video_consolidate")}
    assert {"lexicon_correction", "ocr", "vision"} <= preds
    assert "lexicon_correction" in set(g.nodes)


def test_image_and_audio_graphs():
    # Thumbnail is made inline at upload (not a graph node); the graph extracts.
    assert {"exif", "ocr", "vision", "consolidate"} == set(graph_for("image").nodes)
    # audio → (transcription ‖ diarization) → speaker_fusion → lexicon_correction. The
    # diarization + fusion + correction nodes all skip gracefully when their model/lexicon
    # is absent, so they're always in the graph.
    assert set(graph_for("audio").nodes) == {
        "transcription", "diarization", "speaker_fusion", "lexicon_correction"}


def test_speaker_fusion_assigns_by_max_overlap():
    # Deterministic fusion (L1.3): each word gets the speaker whose turn overlaps it most,
    # splitting a segment when the speaker changes mid-segment.
    from personalclaw.knowledge.pipeline.nodes import media_nodes as mn

    transcript = {"text": "hello there", "segments": [
        {"start": 0.0, "end": 2.0, "text": "hello there", "speaker": None, "words": [
            {"start": 0.0, "end": 0.5, "word": "hello", "prob": 0.9},
            {"start": 1.2, "end": 2.0, "word": "there", "prob": 0.9}]}]}
    turns = [{"start": 0.0, "end": 0.8, "speaker": "SPEAKER_00"},
             {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"}]
    fused = mn._fuse_speakers(transcript, turns)
    speakers = [(s["speaker"], s["text"]) for s in fused["segments"]]
    assert speakers == [("SPEAKER_00", "hello"), ("SPEAKER_01", "there")]
    assert mn._speaker_attributed_text(fused) == "SPEAKER_00: hello\nSPEAKER_01: there"


def test_speaker_fusion_passthrough_without_turns():
    # No diarization turns (no model) → transcript passes through unchanged (L0 works
    # with or without L1).
    from personalclaw.knowledge.pipeline.nodes import media_nodes as mn

    transcript = {"text": "x", "segments": [{"start": 0, "end": 1, "text": "x", "words": []}]}
    fused = mn._fuse_speakers(transcript, [])
    assert fused["segments"][0].get("speaker") is None


# ── executor over the video DAG with stubbed models ──


def test_video_dag_routes_conditional_branch(monkeypatch, tmp_path):
    # All use-cases resolvable; stub each model-backed node's run via the registry.
    _set_resolvable(monkeypatch, lambda uc: True)

    from personalclaw.knowledge.pipeline.types import NodeOutput

    # Stub ffmpeg-backed structural nodes to emit fake audio + frames.
    async def _split(inputs, ctx):
        return NodeOutput(node_type="av_split", backend="ffmpeg", pooled=False,
                          metadata={"audio": str(tmp_path / "a.wav"), "video": ctx.file_path},
                          artifacts=[str(tmp_path / "a.wav")])

    async def _frames(inputs, ctx):
        f = tmp_path / "f1.jpg"; f.write_text("x")
        return NodeOutput(node_type="frame_extract", backend="ffmpeg", pooled=False, artifacts=[str(f)])

    async def _classify(inputs, ctx):
        return NodeOutput(node_type="video_classify", backend="vision-llm", classification="text-heavy", pooled=False)

    async def _ocr(inputs, ctx):
        return NodeOutput(node_type="ocr", backend="vision-llm", text="OCR TEXT", classification="text-heavy")

    async def _vision(inputs, ctx):
        return NodeOutput(node_type="vision", backend="vision-llm", text="VISION TEXT")

    async def _transcribe(inputs, ctx):
        return NodeOutput(node_type="transcription", backend="stt", text="SPOKEN WORDS")

    async def _consolidate(inputs, ctx):
        got = sorted(inputs.keys())
        return NodeOutput(node_type="video_consolidate", backend="reasoning-llm", text="MERGED", metadata={"got": got})

    for nt, fn in [("av_split", _split), ("frame_extract", _frames), ("video_classify", _classify),
                   ("ocr", _ocr), ("vision", _vision), ("transcription", _transcribe),
                   ("video_consolidate", _consolidate)]:
        node = reg.get_node(nt, graph_for("video").nodes[nt].backend)
        monkeypatch.setattr(node, "run", fn)

    g = graph_for("video")
    ctx = NodeContext(item_id="v1", item_type="video", file_path=str(tmp_path / "vid.mp4"))
    res = _run(PipelineExecutor(g).run(ctx))
    # text-heavy verdict → ocr ran, vision skipped
    assert "ocr" in res.ran
    assert "vision" in res.skipped
    assert "transcription" in res.ran
    assert res.outputs["video_consolidate"].text == "MERGED"
    # consolidate saw transcription + ocr (not vision)
    assert "ocr" in res.outputs["video_consolidate"].metadata["got"]


def test_audio_partial_when_no_stt(monkeypatch):
    # No model for stt → transcription skips → the downstream lexicon_correction has no
    # transcript to act on and skips too → nothing ran → failed (item survives).
    _set_resolvable(monkeypatch, lambda uc: uc is None)
    g = graph_for("audio")
    res = _run(PipelineExecutor(g).run(NodeContext(item_id="a1", item_type="audio", file_path="/x.wav")))
    assert "transcription" in res.skipped
    assert not res.ran  # neither transcription nor its dependent correction ran
    assert res.status == "failed"  # nothing ran, but no exception raised


def test_image_partial_with_thumbnail_only(monkeypatch, tmp_path):
    # exif/thumbnail (pure-python) run on a real image; ocr/vision skip (no model)
    # → at least one node ran → status 'partial', item NOT hard-failed.
    _set_resolvable(monkeypatch, lambda uc: uc is None)
    img = tmp_path / "p.png"
    try:
        from PIL import Image
        Image.new("RGB", (8, 8)).save(img)
    except Exception:
        pytest.skip("Pillow not available")
    g = graph_for("image")
    res = _run(PipelineExecutor(g).run(NodeContext(item_id="i1", item_type="image", file_path=str(img))))
    assert "exif" in res.ran
    assert "ocr" in res.skipped and "vision" in res.skipped
    assert res.status == "partial"


def test_guess_mime_canonical_web_types():
    """Override Python mimetypes' legacy/nonstandard audio/video MIMEs with the
    canonical web types so inline <audio>/<video> playback works (e.g. .m4a must be
    audio/mp4, not the unplayable audio/mp4a-latm; .wav audio/wav, not audio/x-wav)."""
    from personalclaw.knowledge.media import guess_mime

    assert guess_mime("clip.wav") == "audio/wav"
    assert guess_mime("voice.m4a") == "audio/mp4"
    assert guess_mime("song.flac") == "audio/flac"
    assert guess_mime("rec.ogg") == "audio/ogg"
    assert guess_mime("movie.mov") == "video/quicktime"
    # Non-overridden types pass through unchanged.
    assert guess_mime("a.mp3") == "audio/mpeg"
    assert guess_mime("a.png") == "image/png"
    assert guess_mime("a.pdf") == "application/pdf"


def test_transcription_empty_is_success_not_failure(monkeypatch):
    """A silent / no-speech audio yields an empty transcript — that's a VALID result,
    not a failure (the item should land 'done', not an alarming 'failed')."""
    from personalclaw.knowledge.pipeline.nodes.media_nodes import TranscriptionNode

    async def _empty(_audio):
        return ""
    monkeypatch.setattr("personalclaw.transcribe.transcribe_audio", _empty)
    node = TranscriptionNode()
    ctx = NodeContext(item_id="x", item_type="audio", file_path="/tmp/silent.wav")
    out = _run(node.run({}, ctx))
    assert out.success is True
    assert out.text == ""


def test_transcription_no_audio_still_fails(monkeypatch):
    """No audio at all is still a genuine failure (distinct from empty transcript)."""
    from personalclaw.knowledge.pipeline.nodes.media_nodes import TranscriptionNode

    node = TranscriptionNode()
    ctx = NodeContext(item_id="x", item_type="audio", file_path="")  # no audio
    out = _run(node.run({}, ctx))
    assert out.success is False and out.error == "no audio"
