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
from personalclaw.knowledge_providers.base import ENRICHMENT_FULL, ENRICHMENT_RAW

# The 13 native types and the graph class each routes to. Text-backed types share the
# single-passthrough graph; file/document types share the document-read graph. Media
# types (image/audio/video) get real graphs in Task B — until then they route to the
# document-read graph (which falls back to content) so they never hard-fail.
#
# `decision` (PROACTIVE-ASSISTANT §2.1) is listed EXPLICITLY rather than left to the
# `DocumentGraph` fallback in `graph_for`: a decision has no file, so the fallback would
# route it through the document reader and degrade to its raw content by accident. The
# atom's contract is that it rides the Passthrough graph, and a default that happens to
# produce a similar result is not that contract.
_TEXT_TYPES = {"note", "gist", "journal", "fleeting", "decision"}
_DOC_TYPES = {"pdf", "document", "sheet", "slides"}
_MEDIA_TYPES = {"image", "audio", "video"}


class PassthroughGraph(PipelineGraph):
    """note/gist/journal/fleeting → the content IS the extracted text."""

    def build(self) -> None:
        self.add(NodeSpec(node_type="passthrough", backend="native"))


class BookmarkGraph(PipelineGraph):
    """bookmark → scrape the URL (or fetch-and-cache a paper) → slice. User-pasted
    content passes through unchanged (no fetch).

    The slicer is a leaf here for the same reason as in :class:`DocumentGraph`: a bookmark
    to an arXiv paper is a document, and the whole point of §5 is that the same
    deterministic cut applies however the bytes arrived.
    """

    def build(self) -> None:
        self.add(NodeSpec(node_type="bookmark_scrape", backend="web"))
        self.add(NodeSpec(node_type="document_slice", backend="native"))
        self.edge("bookmark_scrape", "document_slice")


class DocumentGraph(PipelineGraph):
    """pdf/document/sheet/slides → read file text (pure-python) → consolidate, ‖ slice.

    ``document_slice`` (WATCHED-SOURCES §5) hangs off the reader as a LEAF and deliberately
    does NOT feed ``consolidate``: consolidate header-concats every upstream it has, so
    routing the slices through it would append three derived views of the document to the
    document itself — tripling the consolidated text the insights/embed stages read.
    """

    def build(self) -> None:
        self.add(NodeSpec(node_type="document_read", backend="native"))
        self.add(NodeSpec(node_type="consolidate", backend="concat"))
        self.add(NodeSpec(node_type="document_slice", backend="native"))
        self.edge("document_read", "consolidate")
        self.edge("document_read", "document_slice")


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
        self.add(
            NodeSpec(node_type="diarization", backend="diarization", uses_use_case="diarization")
        )
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
        self.add(
            NodeSpec(node_type="diarization", backend="diarization", uses_use_case="diarization")
        )
        self.add(NodeSpec(node_type="speaker_fusion", backend="native"))
        self.add(NodeSpec(node_type="lexicon_correction", backend="lexicon"))
        self.add(NodeSpec(node_type="frame_extract", backend="ffmpeg"))
        self.add(
            NodeSpec(
                node_type="video_classify", backend="vision-llm", uses_use_case="image_modality"
            )
        )
        self.add(NodeSpec(node_type="ocr", backend="vision-llm", uses_use_case="image_modality"))
        self.add(NodeSpec(node_type="vision", backend="vision-llm", uses_use_case="image_modality"))
        self.add(
            NodeSpec(node_type="video_consolidate", backend="reasoning-llm", uses_use_case="chat")
        )
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


class FeedItemGraph(PipelineGraph):
    """A ``raw``-enrichment source item → the fetched feed content IS the extracted text.

    The structural half of WATCHED-SOURCES §6.3's no-AI contract. Every LLM-backed node is
    ABSENT from this graph rather than present-and-disabled, so no config edit, node-param
    override, or future backend registration can re-enable a model for a raw source. The
    single node is pure-python, which leaves the deterministic terminal work intact: the FTS
    row (written at item create), the local embedding, and dedup all still run, so a raw
    item is fully searchable by keyword AND vector. "Raw" means no model — not no index.

    Deliberately NOT a reuse of :class:`PassthroughGraph`, whose topology is identical
    today. Passthrough is note/gist/journal/fleeting's shape and is free to gain a
    model-backed node the day the text types want one; this graph may never have one.
    Sharing the class would make a hard guarantee an accident of another type's topology.
    """

    def build(self) -> None:
        self.add(NodeSpec(node_type="passthrough", backend="native"))


_GRAPH_BY_TYPE: dict[str, type[PipelineGraph]] = {
    **{t: PassthroughGraph for t in _TEXT_TYPES},
    **{t: DocumentGraph for t in _DOC_TYPES},
    "bookmark": BookmarkGraph,
    "image": ImageGraph,
    "audio": AudioGraph,
    "video": VideoGraph,
}


def graph_for(item_type: str, *, enrichment: str = ENRICHMENT_FULL) -> PipelineGraph:
    """Return the validated PipelineGraph for *item_type* under *enrichment*.

    Text → passthrough; pdf/doc/sheet/slides → document-read; image/audio/video →
    their media graphs (#47). Unknown types fall back to the document graph (which
    degrades to the item's raw content when there's no readable file).

    ``enrichment`` is the owning WatchedSource's no-AI setting (WATCHED-SOURCES §6.3).
    :data:`~personalclaw.knowledge_providers.base.ENRICHMENT_RAW` overrides the type map
    entirely and returns :class:`FeedItemGraph` — the type's own graph may contain LLM
    nodes (a raw source of images would otherwise route through OCR + vision), and the
    guarantee is that a raw item reaches no model at all, whatever it is.
    """
    cls: type[PipelineGraph]
    if enrichment == ENRICHMENT_RAW:
        cls = FeedItemGraph
    else:
        cls = _GRAPH_BY_TYPE.get(item_type, DocumentGraph)
    g = cls(item_type=item_type)
    g.build()
    g.validate()
    return g
