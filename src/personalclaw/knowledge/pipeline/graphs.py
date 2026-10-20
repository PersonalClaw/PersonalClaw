"""Per-type pipeline graphs — code-owned OO constructs.

Each knowledge type maps to a :class:`PipelineGraph` subclass that declares its node
topology in ``build()``. These are NOT user-editable data: the graph shape + lifecycle
are owned here in code. Users tune only per-node execution parameters (enable/backend/
use-case/timeout) via config; they cannot rewire a graph.

Terminal stages (consolidate-pool → insights → chunk+embed) are NOT graph nodes —
they run once over the whole extracted-content pool after the graph completes (see
``runner.py``), because they operate on the item bundle, not a single node's input.
"""

from __future__ import annotations

from personalclaw.knowledge.pipeline.graph import NodeSpec, PipelineGraph
from personalclaw.knowledge_providers.base import ENRICHMENT_FULL, ENRICHMENT_RAW

# `decision` is listed EXPLICITLY rather than left to the
# `DocumentGraph` fallback in `graph_for`: a decision has no file, so the fallback would
# route it through the document reader and degrade to its raw content by accident. The
# change's contract is that it rides the Passthrough graph, and a default that happens to
# produce a similar result is not that contract.
_TEXT_TYPES = {"note", "gist", "journal", "fleeting", "decision"}
_DOC_TYPES = {"pdf", "document", "sheet", "slides"}


class PassthroughGraph(PipelineGraph):
    """note/gist/journal/fleeting → the content IS the extracted text."""

    def build(self) -> None:
        self.add(NodeSpec(node_type="passthrough", backend="native"))


class BookmarkGraph(PipelineGraph):
    """bookmark → scrape the URL (or fetch-and-cache a paper) → slice. User-pasted
    content passes through unchanged (no fetch).

    The slicer is a leaf here for the same reason as in :class:`DocumentGraph`: a bookmark
    to an arXiv paper is a document, and the whole point of the slicer is that the same
    deterministic cut applies however the bytes arrived.
    """

    def build(self) -> None:
        self.add(NodeSpec(node_type="bookmark_scrape", backend="web"))
        self.add(NodeSpec(node_type="document_slice", backend="native"))
        self.edge("bookmark_scrape", "document_slice")


class DocumentGraph(PipelineGraph):
    """pdf/document/sheet/slides → read file text (pure-python) → consolidate, ‖ slice,
    with a CONDITIONAL scan branch for a PDF that carries no text layer:

        document_read ─┬─> consolidate
                       ├─> document_slice
                       └─(no-text-layer)─> pdf_rasterize ─> ocr ─> consolidate

    ``document_slice`` hangs off the reader as a LEAF and deliberately
    does NOT feed ``consolidate``: consolidate header-concats every upstream it has, so
    routing the slices through it would append three derived views of the document to the
    document itself — tripling the consolidated text the insights/embed stages read.

    The scan branch is guarded by ``document_read``'s classification, not by a check inside
    the OCR node. That placement is what makes "no double-OCR" structural: a PDF whose text
    layer is non-empty is never classified ``no-text-layer``, the edge is never traversed,
    and no OCR backend is constructed — as opposed to an OCR node that runs and then decides
    to do nothing, which is the same cost and a far weaker guarantee.
    """

    def build(self) -> None:
        self.add(NodeSpec(node_type="document_read", backend="native"))
        self.add(NodeSpec(node_type="consolidate", backend="concat"))
        self.add(NodeSpec(node_type="document_slice", backend="native"))
        self.add(NodeSpec(node_type="pdf_rasterize", backend="pypdfium2"))
        self.add(NodeSpec(node_type="ocr", backend="vision-llm", uses_use_case="image_modality"))
        self.edge("document_read", "consolidate")
        self.edge("document_read", "document_slice")
        self.edge("document_read", "pdf_rasterize", when="no-text-layer")
        self.edge("pdf_rasterize", "ocr")
        self.edge("ocr", "consolidate")


class ImageGraph(PipelineGraph):
    """image → exif (pure-python) ‖ ocr + vision (model-backed, skip if no model) →
    consolidate. Model-backed nodes degrade gracefully. The thumbnail is made
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
    passes the transcript through), so rich transcripts work with or without diarization.
    The correction node likewise no-ops when the Lexicon is empty."""

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
    """The conditional DAG with an adaptive re-sampling loop:

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

    The structural half of the watched sources' no-AI contract. Every LLM-backed node is
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


class RawDocumentGraph(PipelineGraph):
    """A ``raw``-enrichment source's document FILE (a note, PDF, sheet or deck a watched folder
    took) → its text, read with no model: the document reader, then consolidate, ‖ slice.

    :class:`DocumentGraph` without its scan branch. The reader and the slicer are pure python, so
    the no-AI contract holds by the same absence :class:`FeedItemGraph` keeps it by: a page with no
    text layer is read only by OCR, which is a model, so that node is not here. A scanned PDF from
    a raw source has no text, and its item says so (``unsearchable``)."""

    def build(self) -> None:
        self.add(NodeSpec(node_type="document_read", backend="native"))
        self.add(NodeSpec(node_type="consolidate", backend="concat"))
        self.add(NodeSpec(node_type="document_slice", backend="native"))
        self.edge("document_read", "consolidate")
        self.edge("document_read", "document_slice")


class RawImageGraph(PipelineGraph):
    """A ``raw``-enrichment source's picture → what is read of it with no model: its exif (its
    size and format). OCR and a description of it are a model's reading, so they are not here;
    the item is described by what exif found."""

    def build(self) -> None:
        self.add(NodeSpec(node_type="exif", backend="pillow"))


_GRAPH_BY_TYPE: dict[str, type[PipelineGraph]] = {
    **{t: PassthroughGraph for t in _TEXT_TYPES},
    **{t: DocumentGraph for t in _DOC_TYPES},
    "bookmark": BookmarkGraph,
    "image": ImageGraph,
    "audio": AudioGraph,
    "video": VideoGraph,
}

#: The graphs of a ``raw`` source's items that are files: their kind's readers with no model.
#: Every other raw item (a feed entry, a page, a code file's text) is :class:`FeedItemGraph`.
_RAW_GRAPH_BY_TYPE: dict[str, type[PipelineGraph]] = {
    **{t: RawDocumentGraph for t in _DOC_TYPES},
    "image": RawImageGraph,
}


def graph_for(item_type: str, *, enrichment: str = ENRICHMENT_FULL) -> PipelineGraph:
    """Return the validated PipelineGraph for *item_type* under *enrichment*.

    Text → passthrough; pdf/doc/sheet/slides → document-read; image/audio/video →
    their media graphs. Unknown types fall back to the document graph (which
    degrades to the item's raw content when there's no readable file).

    ``enrichment`` is the owning WatchedSource's no-AI setting.
    :data:`~personalclaw.knowledge_providers.base.ENRICHMENT_RAW` overrides the type map: the
    type's own graph may contain LLM nodes (a raw source of images would otherwise route through
    OCR + vision), and the guarantee is that a raw item reaches no model at all, whatever it is.
    A file a raw source took (a watched folder's document or picture) is read by its kind's
    pure-python readers alone (:class:`RawDocumentGraph`, :class:`RawImageGraph`); every other raw
    item is :class:`FeedItemGraph`.
    """
    cls: type[PipelineGraph]
    if enrichment == ENRICHMENT_RAW:
        cls = _RAW_GRAPH_BY_TYPE.get(item_type, FeedItemGraph)
    else:
        cls = _GRAPH_BY_TYPE.get(item_type, DocumentGraph)
    g = cls(item_type=item_type)
    g.build()
    g.validate()
    return g


def file_graph_for(item_type: str) -> PipelineGraph:
    """The graph that reads a FILE of *item_type*, for a caller that has only the file.

    A text type's own graph hands on the item's stored content and reads no file. A code file
    is one (a ``gist``): the library reads its text into the item when it is uploaded, and the
    gist's graph hands that on. Given only the file there is no content to hand on, so a file of
    a text type is read by the document graph, whose reader reads it as text by the rule every
    reader of a file as text shares (``readers.file_text``): a binary file named as code is not
    read, and the text it does read is the reader's, which the content scan reads."""
    return graph_for("document" if item_type in _TEXT_TYPES else item_type)
