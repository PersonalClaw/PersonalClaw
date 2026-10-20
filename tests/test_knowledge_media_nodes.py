"""Media + video pipeline nodes + the conditional video DAG."""

from __future__ import annotations

import asyncio

import pytest

import personalclaw.knowledge.pipeline.executor as ex
import personalclaw.knowledge.pipeline.registry as reg
from personalclaw.knowledge.pipeline import ensure_nodes_registered, graph_for
from personalclaw.knowledge.pipeline.executor import PipelineExecutor
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput


def _set_resolvable(monkeypatch, fn):
    """Control whether a model serves each use-case, for the executor. The executor binds
    ``unserved_reason`` at import (``from registry import …``), so patch it in the
    EXECUTOR's namespace — patching registry's wouldn't reach it."""

    async def _unserved(use_case):
        return "" if fn(use_case) else f"no model serves the {use_case} use case"

    monkeypatch.setattr(ex, "unserved_reason", _unserved)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _nodes():
    ensure_nodes_registered()


# ── use-case wiring ──


def test_ingestion_nodes_use_default_capability_bindings():
    """Ingestion has NO dedicated use-cases — each model-backed node resolves DIRECTLY
    to the relevant default capability (image-understanding → image_modality, reasoning
    → chat, transcription → stt). There is no per-role ingestion override."""
    from personalclaw.knowledge.pipeline.nodes.media_nodes import (
        OcrNode,
        TranscriptionNode,
        VideoClassifyNode,
        VideoConsolidateNode,
        VisionNode,
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
    for uc in (
        "pdf_extraction",
        "ocr",
        "vision_understanding",
        "video_classification",
        "consolidation_reasoning",
    ):
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
    # (the post-decode correction) before consolidation; ocr/vision arms fan in directly.
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
        "transcription",
        "diarization",
        "speaker_fusion",
        "lexicon_correction",
    }


def test_speaker_fusion_assigns_by_max_overlap():
    # Deterministic fusion: each word gets the speaker whose turn overlaps it most,
    # splitting a segment when the speaker changes mid-segment.
    from personalclaw.knowledge.pipeline.nodes import media_nodes as mn

    transcript = {
        "text": "hello there",
        "segments": [
            {
                "start": 0.0,
                "end": 2.0,
                "text": "hello there",
                "speaker": None,
                "words": [
                    {"start": 0.0, "end": 0.5, "word": "hello", "prob": 0.9},
                    {"start": 1.2, "end": 2.0, "word": "there", "prob": 0.9},
                ],
            }
        ],
    }
    turns = [
        {"start": 0.0, "end": 0.8, "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "speaker": "SPEAKER_01"},
    ]
    fused = mn._fuse_speakers(transcript, turns)
    speakers = [(s["speaker"], s["text"]) for s in fused["segments"]]
    assert speakers == [("SPEAKER_00", "hello"), ("SPEAKER_01", "there")]
    assert mn._speaker_attributed_text(fused) == "SPEAKER_00: hello\nSPEAKER_01: there"


def test_speaker_fusion_passthrough_without_turns():
    # No diarization turns (no model) → transcript passes through unchanged (transcription
    # works with or without diarization).
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
        return NodeOutput(
            node_type="av_split",
            backend="ffmpeg",
            pooled=False,
            metadata={"audio": str(tmp_path / "a.wav"), "video": ctx.file_path},
            artifacts=[str(tmp_path / "a.wav")],
        )

    async def _frames(inputs, ctx):
        f = tmp_path / "f1.jpg"
        f.write_text("x")
        return NodeOutput(
            node_type="frame_extract", backend="ffmpeg", pooled=False, artifacts=[str(f)]
        )

    async def _classify(inputs, ctx):
        return NodeOutput(
            node_type="video_classify",
            backend="vision-llm",
            classification="text-heavy",
            pooled=False,
        )

    async def _ocr(inputs, ctx):
        return NodeOutput(
            node_type="ocr", backend="vision-llm", text="OCR TEXT", classification="text-heavy"
        )

    async def _vision(inputs, ctx):
        return NodeOutput(node_type="vision", backend="vision-llm", text="VISION TEXT")

    async def _transcribe(inputs, ctx):
        return NodeOutput(node_type="transcription", backend="stt", text="SPOKEN WORDS")

    async def _diarize(inputs, ctx):
        # Every use case resolves here, so diarization is a bound model too: stubbed like the
        # others (the real node, with no diarization model actually bound, fails and says so).
        return NodeOutput(
            node_type="diarization",
            backend="diarization",
            pooled=False,
            metadata={"speaker_turns": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]},
        )

    async def _consolidate(inputs, ctx):
        got = sorted(inputs.keys())
        return NodeOutput(
            node_type="video_consolidate",
            backend="reasoning-llm",
            text="MERGED",
            metadata={"got": got},
        )

    for nt, fn in [
        ("av_split", _split),
        ("frame_extract", _frames),
        ("video_classify", _classify),
        ("ocr", _ocr),
        ("vision", _vision),
        ("transcription", _transcribe),
        ("diarization", _diarize),
        ("video_consolidate", _consolidate),
    ]:
        node = reg.get_node(nt, graph_for("video").nodes[nt].backend)
        monkeypatch.setattr(node, "run", fn)

    g = graph_for("video")
    ctx = NodeContext(item_id="v1", item_type="video", file_path=str(tmp_path / "vid.mp4"))
    res = _run(PipelineExecutor(g).run(ctx))
    # text-heavy verdict → ocr ran, the vision branch was NOT TAKEN. Not `skipped`: an
    # either/or branch not applying is not a degradation, so it lands in `not_taken` and
    # leaves `status` at "done" — see `ExecutionResult.not_taken`. The live timeline still
    # shows it as skipped (`_notify(..., "skipped")`), so the UI fact is unchanged.
    assert "ocr" in res.ran
    assert "vision" in res.not_taken
    assert "vision" not in res.skipped
    assert res.status == "done"
    assert "transcription" in res.ran
    assert res.outputs["video_consolidate"].text == "MERGED"
    # consolidate saw transcription + ocr (not vision)
    assert "ocr" in res.outputs["video_consolidate"].metadata["got"]


def test_audio_partial_when_no_stt(monkeypatch):
    # No model for stt → transcription skips → the downstream lexicon_correction has no
    # transcript to act on and skips too → nothing ran → failed (item survives).
    _set_resolvable(monkeypatch, lambda uc: uc is None)
    g = graph_for("audio")
    res = _run(
        PipelineExecutor(g).run(NodeContext(item_id="a1", item_type="audio", file_path="/x.wav"))
    )
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
    res = _run(
        PipelineExecutor(g).run(NodeContext(item_id="i1", item_type="image", file_path=str(img)))
    )
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
    not a failure (the item should land 'done', not an alarming 'failed'). It pools nothing and
    says ``no_speech``, which the runner puts on the item so the item can say why it has no
    transcript. (This test used to patch the flat ``transcribe_audio``, which the node never
    calls, and passed on the ``None`` a missing model returned.)"""
    from personalclaw.knowledge.pipeline.nodes.media_nodes import TranscriptionNode
    from personalclaw.stt.provider import TranscriptResult

    async def _silent(_audio, **_kw):
        return TranscriptResult(text="", duration=4.0)

    monkeypatch.setattr("personalclaw.transcribe.transcribe_audio_detailed", _silent)
    node = TranscriptionNode()
    ctx = NodeContext(item_id="x", item_type="audio", file_path="/tmp/silent.wav")
    out = _run(node.run({}, ctx))
    assert out.success is True
    assert out.text == ""
    assert out.pooled is False
    assert out.metadata == {"no_speech": True}


def test_a_transcription_with_no_transcript_and_no_reason_fails_it_is_not_silence(monkeypatch):
    """🔴 A six-minute screen recording: its narration came back with nothing (``None``) and the
    node answered success with an empty text, so the item read "Transcription done" beside no
    transcript. Driven through the real ``transcribe_audio_detailed`` with a bound provider
    that gives back nothing, as the local model did when it ran out of time."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from personalclaw.knowledge.pipeline.nodes.media_nodes import TranscriptionNode
    from personalclaw.transcribe import NO_TRANSCRIPT_NO_REASON

    provider = MagicMock()
    provider.transcribe_detailed = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "personalclaw.knowledge.pipeline.nodes.media_nodes._lexicon_bias_terms",
        AsyncMock(return_value=None),
    )
    ctx = NodeContext(item_id="v1", item_type="video", file_path="/tmp/screencast.mov")
    split = NodeOutput(
        node_type="av_split",
        backend="ffmpeg",
        pooled=False,
        metadata={"audio": "/tmp/v1.audio.wav"},
    )
    with (
        patch(
            "personalclaw.providers.use_cases.load_use_case_settings",
            return_value={"enabled": True},
        ),
        patch("personalclaw.security.is_sensitive_path", return_value=False),
        patch("personalclaw.stt.registry.active_stt", return_value=(provider, "turbo")),
    ):
        out = _run(TranscriptionNode().run({"av_split": split}, ctx))
    assert (out.success, out.error) == (False, NO_TRANSCRIPT_NO_REASON)
    assert provider.transcribe_detailed.await_args.args[0] == "/tmp/v1.audio.wav"


def test_transcription_no_audio_still_fails(monkeypatch):
    """No audio at all is still a genuine failure (distinct from empty transcript)."""
    from personalclaw.knowledge.pipeline.nodes.media_nodes import TranscriptionNode

    node = TranscriptionNode()
    ctx = NodeContext(item_id="x", item_type="audio", file_path="")  # no audio
    out = _run(node.run({}, ctx))
    assert out.success is False and out.error == "no audio"


def test_a_transcription_its_provider_could_not_run_fails_with_the_providers_reason(monkeypatch):
    """A provider that says why it could not transcribe fails the node with that reason. The same
    failure used to come back as ``None``, and the item landed "done" with an empty transcript."""
    from personalclaw.knowledge.pipeline.nodes.media_nodes import TranscriptionNode
    from personalclaw.sdk.stt import SttError  # raised by an app's provider, as an app raises it

    reason = "No credentials were found for this account. Sign in, then try again."

    async def _refused(_audio, **_kw):
        raise SttError(reason)

    monkeypatch.setattr("personalclaw.transcribe.transcribe_audio_detailed", _refused)
    node = TranscriptionNode()
    ctx = NodeContext(item_id="x", item_type="audio", file_path="/tmp/meeting.wav")

    out = _run(node.run({}, ctx))

    assert (out.success, out.error) == (False, reason)


# ── diarization: a bound model that cannot answer is a failed step, not "no speakers" ──


def _diarize_with(monkeypatch, provider):
    """Bind *provider* as the active diarization model."""
    monkeypatch.setattr(
        "personalclaw.diarization.registry.active_diarization",
        lambda: (provider, "fake-diarizer"),
    )
    monkeypatch.setattr("personalclaw.security.is_sensitive_path", lambda _p: False)


def test_a_diarization_that_gives_back_nothing_fails_with_a_reason(monkeypatch):
    """🔴 A two-voice clip: the provider could not read the file and answered ``None``,
    and the node reported success with no turns, so the transcript had no speakers and no line
    anywhere said why."""
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.diarize import NO_TURNS_NO_REASON
    from personalclaw.knowledge.pipeline.nodes.media_nodes import DiarizationNode

    provider = MagicMock()
    provider.diarize = AsyncMock(return_value=None)
    _diarize_with(monkeypatch, provider)
    ctx = NodeContext(item_id="a1", item_type="audio", file_path="/tmp/snippet.m4a")
    out = _run(DiarizationNode().run({}, ctx))
    assert (out.success, out.error) == (False, NO_TURNS_NO_REASON)


def test_a_diarization_error_is_the_steps_reason(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.knowledge.pipeline.nodes.media_nodes import DiarizationNode
    from personalclaw.sdk.diarization import DiarizationError  # as an app raises it

    reason = "Diarization could not read this recording: ffmpeg is not installed."
    provider = MagicMock()
    provider.diarize = AsyncMock(side_effect=DiarizationError(reason))
    _diarize_with(monkeypatch, provider)
    ctx = NodeContext(item_id="a1", item_type="audio", file_path="/tmp/snippet.m4a")
    out = _run(DiarizationNode().run({}, ctx))
    assert (out.success, out.error) == (False, reason)


def test_an_unexpected_diarization_failure_keeps_its_own_words(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.diarize import DIARIZATION_FAILED
    from personalclaw.knowledge.pipeline.nodes.media_nodes import DiarizationNode

    provider = MagicMock()
    provider.diarize = AsyncMock(side_effect=RuntimeError("Format not recognised."))
    _diarize_with(monkeypatch, provider)
    out = _run(
        DiarizationNode().run(
            {}, NodeContext(item_id="a1", item_type="audio", file_path="/tmp/snippet.m4a")
        )
    )
    assert out.success is False
    assert out.error == f"{DIARIZATION_FAILED} Details: Format not recognised."


def test_speaker_turns_reach_fusion_and_label_the_transcript(monkeypatch):
    """The control: a provider that answers turns labels the transcript. Diarization is
    structural (``pooled=False``): its product is the turns, never text."""
    from unittest.mock import AsyncMock, MagicMock

    from personalclaw.diarization.provider import SpeakerTurn
    from personalclaw.knowledge.pipeline.nodes import media_nodes as mn

    provider = MagicMock()
    provider.diarize = AsyncMock(
        return_value=[SpeakerTurn(0.0, 4.0, "SPEAKER_00"), SpeakerTurn(4.0, 9.0, "SPEAKER_01")]
    )
    _diarize_with(monkeypatch, provider)
    ctx = NodeContext(item_id="a1", item_type="audio", file_path="/tmp/snippet.m4a")
    turns = _run(mn.DiarizationNode().run({}, ctx))
    assert turns.success and turns.pooled is False
    transcript = NodeOutput(
        node_type="transcription",
        text="release week good",
        metadata={
            "transcript": {
                "text": "release week good",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 9.0,
                        "text": "release week good",
                        "words": [
                            {"start": 0.5, "end": 1.5, "word": " release", "prob": 0.9},
                            {"start": 1.6, "end": 2.2, "word": " week", "prob": 0.9},
                            {"start": 5.0, "end": 5.6, "word": " good", "prob": 0.9},
                        ],
                    }
                ],
            }
        },
    )
    fused = _run(
        mn.SpeakerFusionNode().run({"transcription": transcript, "diarization": turns}, ctx)
    )
    assert fused.text == "SPEAKER_00: release week\nSPEAKER_01: good"


def test_an_empty_pass_through_pools_nothing(monkeypatch):
    """With no transcript to fuse or correct (no speech, or a transcription that failed), the
    pass-through steps contribute nothing to the text pool: pooling their empty output flagged
    an audio item as a scan with no text layer."""
    from personalclaw.knowledge.pipeline.nodes import media_nodes as mn

    ctx = NodeContext(item_id="a1", item_type="audio", file_path="/tmp/silent.m4a")
    silent = NodeOutput(
        node_type="transcription", text="", pooled=False, metadata={"no_speech": True}
    )
    fused = _run(mn.SpeakerFusionNode().run({"transcription": silent}, ctx))
    corrected = _run(mn.LexiconCorrectionNode().run({"speaker_fusion": fused}, ctx))
    assert (fused.success, fused.pooled, corrected.success, corrected.pooled) == (
        True,
        False,
        True,
        False,
    )
