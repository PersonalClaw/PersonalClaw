"""Per-type pipeline graphs — code-owned OO constructs (#30, Q3).

Each knowledge type maps to a :class:`PipelineGraph` subclass that declares its node
topology in ``build()``. These are NOT user-editable data: the graph shape + lifecycle
are owned here in code. Users tune only per-node execution parameters (enable/backend/
use-case/timeout) via config; they cannot rewire a graph.

Task A ships the text/document graphs (pure-python, no extraction model needed). The
media + full video conditional DAG land in Task B (#47) as additional subclasses.

Terminal stages (consolidate-pool → insights → chunk+embed) are NOT graph nodes —
they run once over the whole extracted-content pool after the graph completes (see
``runner.py``), because they operate on the item bundle, not a single node's input.
"""

from __future__ import annotations

from personalclaw.knowledge.pipeline.graph import NodeSpec, PipelineGraph

# The 12 native types and the graph class each routes to. Text-backed types share the
# single-passthrough graph; file/document types share the document-read graph. Media
# types (image/audio/video) get real graphs in Task B — until then they route to the
# document-read graph (which falls back to content) so they never hard-fail.
_TEXT_TYPES = {"note", "gist", "journal", "fleeting"}
_DOC_TYPES = {"pdf", "document", "sheet", "slides"}
_MEDIA_TYPES = {"image", "audio", "video"}


class PassthroughGraph(PipelineGraph):
    """note/gist/journal/fleeting → the content IS the extracted text."""

    def build(self) -> None:
        self.add(NodeSpec(node_type="passthrough", backend="native"))


class BookmarkGraph(PipelineGraph):
    """bookmark → scrape the URL → its text (one logical doc). User-pasted content
    passes through unchanged (no fetch)."""

    def build(self) -> None:
        self.add(NodeSpec(node_type="bookmark_scrape", backend="web"))


class DocumentGraph(PipelineGraph):
    """pdf/document/sheet/slides → read file text (pure-python) → consolidate."""

    def build(self) -> None:
        self.add(NodeSpec(node_type="document_read", backend="native"))
        self.add(NodeSpec(node_type="consolidate", backend="concat"))
        self.edge("document_read", "consolidate")


class ImageGraph(PipelineGraph):
    """image → exif (pure-python) ‖ ocr + vision (model-backed, skip if no model) →
    consolidate. Model-backed nodes degrade gracefully (#47). The thumbnail is made
    inline at upload (the canonical .thumb.webp the item points at), so the graph does
    not regenerate one."""

    def build(self) -> None:
        self.add(NodeSpec(node_type="exif", backend="pillow"))
        self.add(NodeSpec(node_type="ocr", backend="vision-llm", uses_use_case="image_modality"))
        self.add(NodeSpec(node_type="vision", backend="vision-llm", uses_use_case="image_modality"))
        self.add(NodeSpec(node_type="consolidate", backend="concat"))
        self.edge("ocr", "consolidate")
        self.edge("vision", "consolidate")


class AudioGraph(PipelineGraph):
    """audio → (transcription ‖ diarization) → speaker_fusion → lexicon_correction.

        audio ─┬─> transcription ─────────┐
               └─> diarization ───────────┤
                             {both} ─> speaker_fusion ─> lexicon_correction ─> [pool]

    transcription reuses ``stt``; diarization uses its own use-case. Both the diarization
    branch and speaker_fusion SKIP GRACEFULLY when no diarization model is bound (fusion
    passes the transcript through), so rich transcripts (L0) work with or without L1. The
    correction node (LEX.4) likewise no-ops when the Lexicon is empty."""

    def build(self) -> None:
        self.add(NodeSpec(node_type="transcription", backend="stt", uses_use_case="stt"))
        self.add(NodeSpec(node_type="diarization", backend="diarization", uses_use_case="diarization"))
        self.add(NodeSpec(node_type="speaker_fusion", backend="native"))
        self.add(NodeSpec(node_type="lexicon_correction", backend="lexicon"))
        self.edge("transcription", "speaker_fusion")
        self.edge("diarization", "speaker_fusion")
        self.edge("speaker_fusion", "lexicon_correction")


class VideoGraph(PipelineGraph):
    """The conditional DAG with an adaptive re-sampling loop (§5 + the vision):

        av_split ─> transcription ──────────────────────────┐
                 └> frame_extract ─> video_classify          │
                          ▲              │ (needs-denser)     │
                          └──── loop ────┘  ×max_iters        │
                                         ├─(text-heavy)→ ocr ─┤
                                         └─(visual)────→ vision┤
        {transcription, ocr|vision} ─────────────────────> video_consolidate

    av_split + frame_extract are pure-python (ffmpeg); the rest model-backed (skip
    gracefully with no model). video_classify inspects the sampled frames and, when a
    content-heavy segment (screen-share/diagram/whiteboard) is under-sampled, emits
    classification 'needs-denser' + the dense-region timestamps; the bounded loop
    back-edge re-runs frame_extract → video_classify, sampling those regions densely
    (sparse elsewhere), until coverage is sufficient or max_iters is reached.
    """

    def build(self) -> None:
        self.add(NodeSpec(node_type="av_split", backend="ffmpeg"))
        self.add(NodeSpec(node_type="transcription", backend="stt", uses_use_case="stt"))
        self.add(NodeSpec(node_type="diarization", backend="diarization", uses_use_case="diarization"))
        self.add(NodeSpec(node_type="speaker_fusion", backend="native"))
        self.add(NodeSpec(node_type="lexicon_correction", backend="lexicon"))
        self.add(NodeSpec(node_type="frame_extract", backend="ffmpeg"))
        self.add(NodeSpec(node_type="video_classify", backend="vision-llm", uses_use_case="image_modality"))
        self.add(NodeSpec(node_type="ocr", backend="vision-llm", uses_use_case="image_modality"))
        self.add(NodeSpec(node_type="vision", backend="vision-llm", uses_use_case="image_modality"))
        self.add(NodeSpec(node_type="video_consolidate", backend="reasoning-llm", uses_use_case="chat"))
        # fan-out from the split: transcription ‖ diarization (audio arm) + frame_extract.
        self.edge("av_split", "transcription")
        self.edge("av_split", "diarization")
        self.edge("av_split", "frame_extract")
        # audio arm: (transcription ‖ diarization) → speaker_fusion → lexicon_correction.
        self.edge("transcription", "speaker_fusion")
        self.edge("diarization", "speaker_fusion")
        self.edge("speaker_fusion", "lexicon_correction")
        self.edge("frame_extract", "video_classify")
        # adaptive re-sampling: classifier asks for denser frames around content-heavy
        # regions → re-run frame_extract → video_classify, bounded to 3 iterations.
        self.loop_edge("video_classify", "frame_extract", when="needs-denser", max_iters=3)
        # conditional branch on the classifier's verdict (adaptive routing)
        self.edge("video_classify", "ocr", when="text-heavy")
        self.edge("video_classify", "vision", when="visual")
        self.edge("video_classify", "vision", when="talking-head")
        # fan-in reasoning consolidation (transcript arm flows through fusion+correction)
        self.edge("lexicon_correction", "video_consolidate")
        self.edge("ocr", "video_consolidate")
        self.edge("vision", "video_consolidate")


_GRAPH_BY_TYPE: dict[str, type[PipelineGraph]] = {
    **{t: PassthroughGraph for t in _TEXT_TYPES},
    **{t: DocumentGraph for t in _DOC_TYPES},
    "bookmark": BookmarkGraph,
    "image": ImageGraph,
    "audio": AudioGraph,
    "video": VideoGraph,
}


def graph_for(item_type: str) -> PipelineGraph:
    """Return the validated PipelineGraph for *item_type*.

    Text → passthrough; pdf/doc/sheet/slides → document-read; image/audio/video →
    their media graphs (#47). Unknown types fall back to the document graph (which
    degrades to the item's raw content when there's no readable file).
    """
    cls = _GRAPH_BY_TYPE.get(item_type, DocumentGraph)
    g = cls(item_type=item_type)
    g.build()
    g.validate()
    return g
