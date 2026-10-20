"""An item's text is what its graph's last step made of the rest.

Measured by driving a two-voice voice memo through a real ingest: diarization found both
speakers and speaker_fusion labelled the transcript with them, yet the item's text had no
speakers in it. The runner took the FIRST text any step finished with, which for a recording is
the raw transcription: every later step (the speakers, the Lexicon's corrections) was computed,
stored in the extracted-content pool, and never became the item's text. A video's text was
likewise one text, its narration or its slides' text, never both; and its description step
never saw the narration at all.

These drive whole ingests (the real graph, runner and media nodes; only the model calls
stubbed) and read the item's text.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import personalclaw.knowledge.pipeline.executor as ex
import personalclaw.knowledge.pipeline.registry as reg
import personalclaw.lexicon.service as lexicon_service
from personalclaw.diarization import SpeakerTurn
from personalclaw.knowledge.pipeline import ensure_nodes_registered, graph_for
from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.pipeline.types import NodeOutput
from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
from personalclaw.lexicon.service import LexiconService
from personalclaw.lexicon.store import LexiconStore
from personalclaw.stt.provider import TranscriptResult, TranscriptSegment, TranscriptWord

SLIDE_TEXT = "Step 0 of 9. Check that the main branch is green before you tag a release."


class _Embedder:
    def embed_for_item(self, title, summary, content):
        return [0.1, 0.2, 0.3, 0.4]

    def embed(self, text):
        return [0.1, 0.2, 0.3, 0.4]

    def is_available(self):
        return True


def _serve(monkeypatch, *unserved: str) -> None:
    async def _reason(use_case):
        return f"no model serves {use_case}" if use_case in unserved else ""

    monkeypatch.setattr(ex, "unserved_reason", _reason)


@pytest.fixture(autouse=True)
def _nodes(monkeypatch):
    ensure_nodes_registered()
    monkeypatch.setattr(
        "personalclaw.knowledge.pipeline.nodes.media_nodes._lexicon_bias_terms",
        AsyncMock(return_value=None),
    )


@pytest.fixture
def lexicon(tmp_path, monkeypatch) -> LexiconService:
    """A Lexicon holding one term, so the correction step does its work (with none it passes
    the transcript through untouched)."""
    svc = LexiconService(LexiconStore(str(tmp_path / "lexicon.db")))
    svc.add_manual_term("Harbor Tiling", entity_type="org")
    monkeypatch.setattr(lexicon_service, "_service", svc)
    return svc


def _said(*segments: tuple[float, str]) -> TranscriptResult:
    """A transcript of *segments* ``(start, text)``, a word a second, as a recogniser returns."""
    out: list[TranscriptSegment] = []
    for start, text in segments:
        words = [
            TranscriptWord(start + i, start + i + 0.9, f" {w}", 0.95)
            for i, w in enumerate(text.split())
        ]
        out.append(TranscriptSegment(start, words[-1].end, text, words=words))
    return TranscriptResult(
        text=" ".join(t for _, t in segments), duration=out[-1].end, segments=out
    )


def _transcribes(result):
    provider = MagicMock()
    provider.transcribe_detailed = AsyncMock(return_value=result)
    return (
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


def _ingest(store, item_id, binding) -> dict:
    with binding[0], binding[1], binding[2]:
        asyncio.run(ingest_item(store, item_id, insights_pool=None, embedder=_Embedder()))
    return store.get_item(item_id)


def test_a_two_voice_recording_keeps_its_speakers(monkeypatch, tmp_path, lexicon):
    """🔴 Red before: the item's text was the raw transcription, without the speakers."""
    _serve(monkeypatch)
    monkeypatch.setattr(
        "personalclaw.diarize.diarize_audio",
        AsyncMock(
            return_value=[SpeakerTurn(0.0, 4.2, "SPEAKER_01"), SpeakerTurn(4.3, 9.1, "SPEAKER_00")]
        ),
    )
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    item_id = _item(store, tmp_path, "audio", "two voices.m4a", "audio/mp4")

    item = _ingest(
        store,
        item_id,
        _transcribes(_said((0.0, "OK, release week."), (4.5, "Good, I write the notes."))),
    )

    assert item["content"] == "SPEAKER_01: OK, release week.\nSPEAKER_00: Good, I write the notes."


def test_a_one_voice_memo_carries_no_speaker_label(monkeypatch, tmp_path, lexicon):
    """The control: a label tells voices apart, so a memo with one voice has none."""
    _serve(monkeypatch)
    monkeypatch.setattr(
        "personalclaw.diarize.diarize_audio",
        AsyncMock(return_value=[SpeakerTurn(0.0, 9.0, "SPEAKER_00")]),
    )
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    item_id = _item(store, tmp_path, "audio", "one voice.m4a", "audio/mp4")

    item = _ingest(
        store,
        item_id,
        _transcribes(_said((0.0, "Okay, note for the talk."), (4.5, "Keep the demo short."))),
    )

    assert item["content"] == "Okay, note for the talk. Keep the demo short."


def test_a_correction_the_user_taught_reaches_the_items_text(monkeypatch, tmp_path, lexicon):
    """🔴 Red before: the correction was applied in the correction step's own output, stored in
    the pool, and the item's text kept the word as it was heard."""
    _serve(monkeypatch, "diarization")
    lexicon.learn_correction("Acu", "Aku", always=True)
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    item_id = _item(store, tmp_path, "audio", "memo.m4a", "audio/mp4")

    item = _ingest(store, item_id, _transcribes(_said((0.0, "ask Acu about it"))))

    assert item["content"] == "ask Aku about it"


def _stub_the_frames(monkeypatch, tmp_path: Path) -> None:
    """The video graph's frame arm, stubbed: split, frames, a text-heavy verdict, a slide's OCR."""
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

    graph = graph_for("video")
    for node_type, fn in (
        ("av_split", _split),
        ("frame_extract", _frames),
        ("video_classify", _classify),
        ("ocr", _ocr),
    ):
        monkeypatch.setattr(reg.get_node(node_type, graph.nodes[node_type].backend), "run", fn)


NARRATION = "Step one. Make sure main is green."


def test_a_videos_description_is_made_from_its_narration_too(monkeypatch, tmp_path):
    """🔴 Red before: the description step read a "transcription" input it does not have (the
    transcript reaches it through the correction step), so it described the slides alone; and
    the item's text was not the description anyway."""
    _serve(monkeypatch, "diarization")
    _stub_the_frames(monkeypatch, tmp_path)
    asked: list[str] = []

    async def _describe(use_case, prompt, **kwargs):
        asked.append(prompt)
        return "A release walkthrough: keep main green, then tag."

    monkeypatch.setattr(
        "personalclaw.knowledge.pipeline.nodes.media_nodes.complete_text", _describe
    )
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    item_id = _item(store, tmp_path, "video", "release screencast.mov", "video/quicktime")

    item = _ingest(store, item_id, _transcribes(_said((0.0, NARRATION))))

    [prompt] = [p for p in asked if SLIDE_TEXT in p]
    assert NARRATION in prompt
    assert item["content"] == "A release walkthrough: keep main green, then tag."


def test_a_video_with_no_model_to_describe_it_holds_its_narration_and_its_slides(
    monkeypatch, tmp_path
):
    """🔴 Red before: with no chat model the description step is skipped, and the item's text
    was the first text recorded, the narration, with the slides' text left out. (When the
    narration failed, the first slide's OCR was the item's only text.)"""
    _serve(monkeypatch, "diarization", "chat")
    _stub_the_frames(monkeypatch, tmp_path)
    store = KnowledgeStore(str(knowledge_db_path(tmp_path)))
    item_id = _item(store, tmp_path, "video", "release screencast.mov", "video/quicktime")

    item = _ingest(store, item_id, _transcribes(_said((0.0, NARRATION))))

    assert item["content"] == f"{NARRATION}\n\n{SLIDE_TEXT}"
