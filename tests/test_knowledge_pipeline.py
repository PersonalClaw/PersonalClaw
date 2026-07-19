"""Knowledge ingestion node-graph engine (#30) — graph + executor + runner."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from personalclaw.knowledge.pipeline import ensure_nodes_registered, graph_for
from personalclaw.knowledge.pipeline.executor import PipelineExecutor
from personalclaw.knowledge.pipeline.graph import NodeSpec, PipelineGraph, PipelineGraphError
from personalclaw.knowledge.pipeline.registry import register_node
from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.pipeline.types import NodeContext, NodeOutput
from personalclaw.knowledge.store import KnowledgeStore


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store():
    p = Path(tempfile.mkdtemp()) / "k.db"
    return KnowledgeStore(str(p))


# ── graph validation ──


class _LinearGraph(PipelineGraph):
    def build(self):
        self.add(NodeSpec(node_type="a"))
        self.add(NodeSpec(node_type="b"))
        self.edge("a", "b")


def test_graph_validates_and_topo_orders():
    g = _LinearGraph(item_type="x")
    g.build()
    g.validate()
    assert g.topo_order() == ["a", "b"]
    assert g.roots == ["a"]


def test_graph_rejects_unknown_edge():
    g = PipelineGraph(item_type="x")
    g.add(NodeSpec(node_type="a"))
    g.edge("a", "ghost")
    with pytest.raises(PipelineGraphError, match="unknown node"):
        g.validate()


def test_graph_rejects_cycle():
    g = PipelineGraph(item_type="x")
    g.add(NodeSpec(node_type="a"))
    g.add(NodeSpec(node_type="b"))
    g.edge("a", "b")
    g.edge("b", "a")
    with pytest.raises(PipelineGraphError, match="cycle"):
        g.validate()


# ── executor: topo run, conditional edges, skip ──


class _StubNode:
    def __init__(self, node_type, *, text="", classification="", fail=False):
        self.node_type = node_type
        self.backend = "stub"
        self.uses_use_case = None
        self._text = text
        self._cls = classification
        self._fail = fail

    async def run(self, inputs, ctx):
        if self._fail:
            return NodeOutput(node_type=self.node_type, backend=self.backend, success=False, error="boom")
        return NodeOutput(
            node_type=self.node_type, backend=self.backend, text=self._text,
            classification=self._cls,
        )


def _graph(specs, edges):
    g = PipelineGraph(item_type="t")
    for s in specs:
        g.add(s)
    for e in edges:
        g.edge(*e[:2], when=e[2] if len(e) > 2 else None)
    g.validate()
    return g


def test_executor_runs_linear_and_pools():
    register_node(_StubNode("root", text="hello"))
    g = _graph([NodeSpec("root", backend="stub")], [])
    res = _run(PipelineExecutor(g).run(NodeContext(item_id="i", item_type="t")))
    assert res.status == "done"
    assert res.ran == ["root"]
    assert [o.text for o in res.pooled_outputs()] == ["hello"]


def test_consolidate_single_input_not_pooled_multi_is():
    """ConsolidateNode echoes a lone input verbatim (no new content) → keep it out of
    the pool to avoid a duplicate drill-down row; a real merge of ≥2 inputs is novel
    text and stays pooled."""
    from personalclaw.knowledge.pipeline.nodes.text_nodes import ConsolidateNode

    node = ConsolidateNode()
    ctx = NodeContext(item_id="i", item_type="document")
    one = _run(node.run({"document_read": NodeOutput(node_type="document_read", text="solo")}, ctx))
    assert one.text == "solo" and one.pooled is False
    multi = _run(node.run({
        "ocr": NodeOutput(node_type="ocr", text="A"),
        "vision": NodeOutput(node_type="vision", text="B"),
    }, ctx))
    assert "A" in multi.text and "B" in multi.text and multi.pooled is True


def test_executor_conditional_branch_taken():
    register_node(_StubNode("clf", classification="visual"))
    register_node(_StubNode("vision", text="V"))
    register_node(_StubNode("ocr", text="O"))
    g = _graph(
        [NodeSpec("clf", backend="stub"), NodeSpec("vision", backend="stub"), NodeSpec("ocr", backend="stub")],
        [("clf", "vision", "visual"), ("clf", "ocr", "text")],
    )
    res = _run(PipelineExecutor(g).run(NodeContext(item_id="i", item_type="t")))
    assert "vision" in res.ran          # matched the 'visual' classification
    assert "ocr" in res.skipped         # 'text' edge not satisfied


def test_executor_disabled_node_skipped():
    register_node(_StubNode("root", text="x"))
    g = _graph([NodeSpec("root", backend="stub")], [])
    res = _run(PipelineExecutor(g, params_for=lambda nt: {"enabled": False}).run(
        NodeContext(item_id="i", item_type="t")))
    assert res.skipped == ["root"]
    assert res.status == "failed"  # nothing ran


def test_executor_failed_node_is_partial():
    register_node(_StubNode("root", text="ok"))
    register_node(_StubNode("bad", fail=True))
    g = _graph([NodeSpec("root", backend="stub"), NodeSpec("bad", backend="stub")], [("root", "bad")])
    res = _run(PipelineExecutor(g).run(NodeContext(item_id="i", item_type="t")))
    assert res.ran == ["root"]
    assert res.failed == ["bad"]
    assert res.status == "partial"


class _CountingClassifier:
    """A classifier stub that asks 'needs-denser' for the first `dense_rounds`
    invocations, then settles on `final`. Counts how many times it ran."""

    def __init__(self, node_type, *, dense_rounds, final="visual"):
        self.node_type = node_type
        self.backend = "stub"
        self.uses_use_case = None
        self._dense_rounds = dense_rounds
        self._final = final
        self.calls = 0

    async def run(self, inputs, ctx):
        self.calls += 1
        it = int((ctx.params or {}).get("loop_iteration", 0))
        if it < self._dense_rounds:
            return NodeOutput(node_type=self.node_type, backend=self.backend,
                              classification="needs-denser",
                              metadata={"dense_regions": [{"start": 0, "end": 0}]})
        return NodeOutput(node_type=self.node_type, backend=self.backend, classification=self._final)


def test_bounded_loop_reruns_body_then_converges():
    # classifier wants denser for 2 rounds, then converges → the loop body
    # (sampler + classifier) re-runs exactly twice, then the terminal branch resolves.
    register_node(_StubNode("sampler", text="frames"))
    clf = _CountingClassifier("classify", dense_rounds=2, final="visual")
    register_node(clf)
    register_node(_StubNode("vision", text="V"))
    g = PipelineGraph(item_type="video")
    for s in (NodeSpec("sampler", backend="stub"), NodeSpec("classify", backend="stub"),
              NodeSpec("vision", backend="stub")):
        g.add(s)
    g.edge("sampler", "classify")
    g.loop_edge("classify", "sampler", when="needs-denser", max_iters=3)
    g.edge("classify", "vision", when="visual")
    g.validate()
    res = _run(PipelineExecutor(g).run(NodeContext(item_id="i", item_type="video")))
    # classify ran: initial + 2 loop iterations = 3 times.
    assert clf.calls == 3
    assert res.outputs["classify"].classification == "visual"
    assert "vision" in res.ran  # terminal branch resolved after convergence


def test_bounded_loop_respects_max_iters():
    # A classifier that ALWAYS wants denser must stop at max_iters (no infinite loop).
    register_node(_StubNode("sampler", text="frames"))
    clf = _CountingClassifier("classify", dense_rounds=99, final="visual")  # never converges
    register_node(clf)
    g = PipelineGraph(item_type="video")
    for s in (NodeSpec("sampler", backend="stub"), NodeSpec("classify", backend="stub")):
        g.add(s)
    g.edge("sampler", "classify")
    g.loop_edge("classify", "sampler", when="needs-denser", max_iters=3)
    g.validate()
    res = _run(PipelineExecutor(g).run(NodeContext(item_id="i", item_type="video")))
    # initial + 3 bounded iterations = 4, then stop despite still wanting denser.
    assert clf.calls == 4
    assert res.outputs["classify"].classification == "needs-denser"


def test_scaled_timeout_grows_with_media_duration():
    # Model-backed media nodes get a duration-scaled timeout; pure-python nodes don't.
    g = _graph([NodeSpec("transcription", backend="stub", timeout_s=120.0),
                NodeSpec("av_split", backend="stub", timeout_s=120.0)], [])
    ex = PipelineExecutor(g)
    ex._dur_cache = 60.0  # 1-minute media
    ctx = NodeContext(item_id="i", item_type="video", file_path="x")
    assert ex._scaled_timeout("transcription", g.nodes["transcription"], "stt", ctx) == 240.0
    assert ex._scaled_timeout("av_split", g.nodes["av_split"], None, ctx) == 120.0  # pure-python, flat
    # 90-minute media → capped at the hard ceiling, not unbounded.
    ex._dur_cache = 5400.0
    assert ex._scaled_timeout("transcription", g.nodes["transcription"], "stt", ctx) == 3600.0
    # No media duration available → falls back to the flat spec timeout.
    ex._dur_cache = 0.0
    assert ex._scaled_timeout("transcription", g.nodes["transcription"], "stt", ctx) == 120.0


def test_executor_parallel_branch_independent_of_sibling_failure():
    # A failed node on one parallel branch must NOT block the independent sibling
    # branch (both depend only on the shared root). Mirrors av_split → {transcription
    # (fails), frame_extract (must still run)}.
    register_node(_StubNode("split", text="s"))
    register_node(_StubNode("audio", fail=True))          # e.g. transcription timeout
    register_node(_StubNode("frames", text="f"))          # e.g. frame_extract
    register_node(_StubNode("sink", text="ok"))
    g = _graph(
        [NodeSpec(n, backend="stub") for n in ("split", "audio", "frames", "sink")],
        [("split", "audio"), ("split", "frames"), ("audio", "sink"), ("frames", "sink")],
    )
    res = _run(PipelineExecutor(g).run(NodeContext(item_id="i", item_type="t")))
    assert "frames" in res.ran        # sibling branch ran despite audio failing
    assert "audio" in res.failed
    # sink fans in on (audio OR frames); frames succeeded → sink still runs.
    assert "sink" in res.ran


def test_video_classify_propagates_frames_to_vision_ocr(monkeypatch):
    """In VideoGraph, vision/ocr are DIRECT successors of video_classify, and the
    executor feeds a node only its direct predecessors' outputs. So video_classify MUST
    carry the sampled frames through in its artifacts — otherwise vision/ocr get no
    frame_extract output and fall back to the raw .mp4 (unreadable → empty extraction →
    consolidate fails). This locks the frame hand-off."""
    from personalclaw.knowledge.pipeline.nodes import media_nodes as M

    # Ingestion nodes now resolve to the default capability (image_modality), not a
    # per-role use-case — so distinguish classify vs vision by the PROMPT, and key the
    # observed images by node role via a closure list.
    seen = {}

    async def fake_complete(use_case, prompt, images=None):
        role = "classify" if "dense" in prompt.lower() or "classif" in prompt.lower() else "vision"
        seen[role] = list(images or [])
        return "visual; dense=no" if role == "classify" else "described"

    monkeypatch.setattr(M, "complete_text", fake_complete)
    frames = ["/k/i.frame_001.jpg", "/k/i.frame_002.jpg"]
    frame_out = NodeOutput(node_type="frame_extract", backend="ffmpeg", artifacts=frames, pooled=False)
    ctx = NodeContext(item_id="i", item_type="video", file_path="/k/movie.mp4")

    # classify runs on frame_extract's output → must echo the frames in its artifacts
    classify = _run(M.VideoClassifyNode().run({"frame_extract": frame_out}, ctx))
    assert classify.classification == "visual"
    assert classify.artifacts == frames, "video_classify must pass frames through"

    # vision receives ONLY classify's output (its sole direct predecessor) → must still
    # get the frames (not fall back to the .mp4).
    vision = _run(M.VisionNode().run({"video_classify": classify}, ctx))
    assert seen["vision"] and seen["vision"][0].endswith(".jpg")
    assert "/k/movie.mp4" not in seen["vision"]  # NOT the raw video
    assert vision.text == "described"


# ── runner end-to-end (text/document graphs, no model) ──


def test_runner_passthrough_note(store):
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="the body text")
    status = _run(ingest_item(store, iid))
    assert status == "done"
    pool = store.get_extracted_contents(iid)
    assert any(p["node_type"] == "passthrough" and "body text" in p["text"] for p in pool)
    assert store.get_item(iid)["processing_status"] == "done"


def test_runner_skips_persist_when_item_deleted_mid_run(store, tmp_path, monkeypatch):
    """If the item is DELETED while its pipeline runs (a user cancels a wrong upload),
    the runner must NOT persist extracted rows for the gone item, must return 'deleted',
    and must clean up any derived artifacts written after the delete-handler's sweep
    (otherwise frames/audio orphan on disk — the race behind stray <id>.frame_*.jpg)."""
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="body")
    # Simulate a delete landing DURING executor.run: get_item returns None afterward.
    import personalclaw.knowledge.pipeline.runner as R
    real_get = store.get_item
    calls = {"n": 0}
    def vanishing_get(i):
        # first call (inside run setup) real; once the run's post-check queries → gone
        calls["n"] += 1
        return None if calls["n"] > 1 else real_get(i)
    # stub the artifact cleanup to record it fired
    cleaned = {}
    monkeypatch.setattr(R, "_cleanup_orphaned_artifacts", lambda i: cleaned.setdefault("id", i))
    monkeypatch.setattr(store, "get_item", vanishing_get)
    status = _run(ingest_item(store, iid))
    assert status == "deleted"
    assert cleaned.get("id") == iid  # orphan cleanup fired for the deleted item


def test_cleanup_orphaned_artifacts_removes_derived_only(tmp_path, monkeypatch):
    """_cleanup_orphaned_artifacts unlinks '<item_id>.*' derived files in the knowledge
    files dir and nothing else (another item's files untouched, dirs untouched)."""
    import personalclaw.knowledge.pipeline.runner as R
    monkeypatch.setattr("personalclaw.knowledge.knowledge_files_dir", lambda: str(tmp_path))
    iid = "abcd1234-dead-beef-0000-000000000000"
    (tmp_path / f"{iid}.audio.wav").write_bytes(b"a")
    (tmp_path / f"{iid}.frame_001.jpg").write_bytes(b"f")
    keep = tmp_path / "0000ffff-other.frame_001.jpg"; keep.write_bytes(b"k")
    R._cleanup_orphaned_artifacts(iid)
    assert not (tmp_path / f"{iid}.audio.wav").exists()
    assert not (tmp_path / f"{iid}.frame_001.jpg").exists()
    assert keep.exists()  # another item's file untouched


def test_runner_persists_node_phases(store):
    # The ground-truth per-node phase map is persisted so the UI shows what actually
    # ran on reload (not a reconstruction). A clean note run → every node 'done'.
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="hi there")
    _run(ingest_item(store, iid))
    phases = (store.get_item(iid).get("file_metadata") or {}).get("node_phases") or {}
    assert phases.get("passthrough") == "done"
    # terminal stages recorded too
    assert phases.get("insights") in ("done", "failed")
    assert phases.get("embed") == "done"


def test_runner_document_reads_file(store, tmp_path):
    ensure_nodes_registered()
    f = tmp_path / "doc.txt"
    f.write_text("file-extracted content here")
    iid = store.create_typed_item(item_type="document", title="D", content="")
    store.update_item(iid, file_path=str(f))
    store.db.commit()
    status = _run(ingest_item(store, iid))
    assert status == "done"
    # content backfilled from the file via document_read → consolidate
    item = store.get_item(iid)
    assert "file-extracted content" in item["content"]
    pool = store.get_extracted_contents(iid)
    pool_types = {p["node_type"] for p in pool}
    assert "document_read" in pool_types
    # A single-source consolidate just echoes document_read's text, so it must NOT add
    # a duplicate pool entry — the drill-down shows one extracted-content row, not two.
    assert "consolidate" not in pool_types
    # The document_read pool entry must NOT leak the internal file stem as a `title`
    # (uploads are stored UUID-named — meaningless noise in the drill-down).
    dr = next(p for p in pool if p["node_type"] == "document_read")
    assert "title" not in (dr.get("metadata") or {})
    # word_count is set from the backfilled extracted text (was 0 at create).
    assert item["word_count"] == len("file-extracted content here".split())


def test_reingest_preserves_updated_at(store):
    """Background enrichment must not bump updated_at — it tracks USER activity, so a
    re-ingest (status, insights, tags, embedding) shouldn't make an item look freshly
    edited or jump it up a recency sort. A real user edit still touches it."""
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="alpha beta gamma")
    store.db.commit()
    before = store.get_item(iid)["updated_at"]
    # Full enrichment pass (no model needed for the note graph's terminal stages here).
    _run(ingest_item(store, iid))
    after_enrich = store.get_item(iid)["updated_at"]
    assert after_enrich == before, "enrichment must not touch updated_at"
    # A genuine user edit DOES bump it.
    store.update_item(iid, title="N2")
    assert store.get_item(iid)["updated_at"] != before


def test_runner_persists_document_page_count_to_file_metadata(store, tmp_path, monkeypatch):
    """A document reader extracts page_count/format; the runner must persist that shape
    onto file_metadata so the detail strip + knowledge_get can show 'N pages' — else the
    reader's metadata is silently dropped after the text is pooled."""
    ensure_nodes_registered()
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    iid = store.create_typed_item(item_type="pdf", title="Report", content="")
    store.update_item(iid, file_path=str(f))
    store.db.commit()

    from personalclaw.knowledge import readers as readers_mod

    def _fake_read(self, path):
        return "Extracted PDF body text.", {"format": "pdf", "page_count": 7, "title": "report"}
    monkeypatch.setattr(readers_mod.FileReader, "read", _fake_read)

    status = _run(ingest_item(store, iid))
    assert status == "done"
    meta = store.get_item(iid)["file_metadata"]
    assert meta.get("page_count") == 7 and meta.get("format") == "pdf"
    # The reader's stem-derived `title` (internal noise) is NOT persisted to metadata.
    assert "title" not in meta


def test_runner_synthesizes_descriptor_for_textless_image(store, tmp_path):
    """An image with no OCR/vision model degrades to exif-only — without a fallback the
    item would be content-less (unsearchable, untitled). The runner synthesizes a
    descriptor from the structural metadata so the item is still identifiable."""
    pytest.importorskip("PIL")
    from PIL import Image
    img = tmp_path / "pic.png"
    Image.new("RGB", (320, 200), "white").save(str(img))
    iid = store.create_typed_item(item_type="image", title="pic.png", content="")
    store.update_item(iid, file_path=str(img), file_size=img.stat().st_size)
    store.db.commit()
    # No insights_pool/embedder → ocr+vision skip; exif (pure-python) runs.
    _run(ingest_item(store, iid))
    item = store.get_item(iid)
    # Content is no longer empty — it carries a human-readable structural descriptor.
    assert item["content"].strip()
    assert "320×200" in item["content"] and "PNG" in item["content"]
    assert item["word_count"] > 0


def test_runner_fixes_stale_word_count_on_reingest(store, tmp_path):
    """An item that already has content but a stale word_count (e.g. a file item from
    before word_count tracking) gets it corrected on re-ingest."""
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="alpha beta gamma delta")
    # Simulate the legacy bug: content present but word_count wrong.
    store.db.execute("UPDATE items SET word_count = 0 WHERE id = ?", (iid,))
    store.db.commit()
    _run(ingest_item(store, iid))
    assert store.get_item(iid)["word_count"] == 4


def test_runner_emits_progress(store):
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="body")
    events = []
    _run(ingest_item(store, iid, publish=lambda ev, data: events.append((ev, data.get("node"), data.get("phase")))))
    names = [e[0] for e in events]
    assert "ingest_started" in names
    assert "ingest_complete" in names
    assert ("node", "passthrough", "done") in events


class _FakePool:
    """Minimal LLM pool stub: returns a fixed insights JSON for any prompt."""

    def __init__(self, payload: str):
        self._payload = payload

    async def send(self, prompt: str, timeout: float | None = None) -> str:
        return self._payload


def test_runner_seeds_ai_tags_from_topics(store):
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="the body text about caching")
    pool = _FakePool('{"summary": "s", "topics": ["caching", "redis", "lru"]}')
    status = _run(ingest_item(store, iid, insights_pool=pool))
    assert status == "done"
    assert store.get_item(iid)["tags"] == ["caching", "redis", "lru"]


def test_runner_marks_failed_on_mid_pipeline_error(store, monkeypatch):
    """An unhandled error in a terminal stage marks the item `failed` — never leaves
    it stranded in `processing` (which the in-memory queue could not recover)."""
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="body text")

    import personalclaw.knowledge.pipeline.runner as runner_mod

    async def _boom(*a, **k):
        raise RuntimeError("LLM socket closed")
    monkeypatch.setattr(runner_mod, "_run_insights", _boom)

    status = _run(ingest_item(store, iid))
    assert status == "failed"
    item = store.get_item(iid)
    assert item["processing_status"] == "failed"
    assert "LLM socket closed" in (item.get("processing_error") or "")


def test_runner_marks_partial_when_insights_model_unavailable(store, monkeypatch):
    """If the insights stage can't reach a model (cold/unavailable pool — a graceful
    failure, not an exception), the item must NOT be marked 'done' with stale/empty
    insights. It's downgraded to 'partial' with a reason, so the gap is visible."""
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="body text")

    import personalclaw.knowledge.pipeline.runner as runner_mod

    async def _insights_unavailable(*a, **k):
        return False  # model errored but was caught — graceful no-op
    monkeypatch.setattr(runner_mod, "_run_insights", _insights_unavailable)

    status = _run(ingest_item(store, iid))
    assert status == "partial"
    item = store.get_item(iid)
    assert item["processing_status"] == "partial"
    assert "insights" in (item.get("processing_error") or "").lower()


def test_runner_insights_failure_not_masked_by_optional_skips(store, tmp_path, monkeypatch):
    """When a graph already goes 'partial' from benign optional-node skips (e.g. an image
    with no ocr/vision model) AND the insights stage also fails, the insights failure must
    still surface — it must NOT be hidden behind the benign 'Skipped (…)' message that the
    list UI suppresses. The reason string must LEAD with the insights failure."""
    pytest.importorskip("PIL")
    from PIL import Image
    ensure_nodes_registered()
    img = tmp_path / "pic.png"
    Image.new("RGB", (320, 200), "white").save(str(img))
    iid = store.create_typed_item(item_type="image", title="pic.png", content="")
    store.update_item(iid, file_path=str(img), file_size=img.stat().st_size)
    store.db.commit()

    import personalclaw.knowledge.pipeline.runner as runner_mod

    async def _insights_unavailable(*a, **k):
        return False
    monkeypatch.setattr(runner_mod, "_run_insights", _insights_unavailable)

    status = _run(ingest_item(store, iid))
    assert status == "partial"
    item = store.get_item(iid)
    err = item.get("processing_error") or ""
    # The actionable insights failure must lead — never be suppressed by the benign prefix.
    assert err.startswith("insights:")
    assert not err.startswith("Skipped (optional steps unavailable):")


def test_runner_sets_ai_title_and_promotes_for_files(store):
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="pdf", title="report.pdf", content="quarterly numbers")
    pool = _FakePool('{"title": "Q3 Revenue Report", "summary": "s"}')
    _run(ingest_item(store, iid, insights_pool=pool))
    item = store.get_item(iid)
    assert item["ai_title"] == "Q3 Revenue Report"
    assert item["title"] == "Q3 Revenue Report"  # file titles are always promoted
    # `title` is not leaked into the insights category bundle.
    assert "title" not in (item.get("insights") or {})


def test_runner_keeps_user_note_title(store):
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="My Title", content="the body text")
    pool = _FakePool('{"title": "AI Suggested Title", "summary": "s"}')
    _run(ingest_item(store, iid, insights_pool=pool))
    item = store.get_item(iid)
    assert item["ai_title"] == "AI Suggested Title"
    assert item["title"] == "My Title"  # a user-authored note title is never overwritten


def test_runner_does_not_ai_title_journals(store):
    """A journal is a date-driven record — it keeps its date heading and carries NO
    ai_title, so the detail page never offers to overwrite it with an AI suggestion."""
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="journal", title="June 18, 2026", content="today I shipped X")
    pool = _FakePool('{"title": "Shipping Feature X", "summary": "s"}')
    _run(ingest_item(store, iid, insights_pool=pool))
    item = store.get_item(iid)
    assert item["title"] == "June 18, 2026"  # date heading preserved
    assert not item.get("ai_title")  # no AI title stored → no magic-wand affordance


def test_runner_promotes_ai_title_over_content_prefix(store):
    """A note created with a blank title is seeded title=content[:60] by the handler.
    Enrichment must promote the AI title over that truncated-content placeholder."""
    ensure_nodes_registered()
    body = ("A quick test note about distributed consensus and Raft leader election "
            "and the failure modes therein.")
    # Mirror the handler: blank title → seeded with the content's first 60 chars.
    iid = store.create_typed_item(item_type="note", title=body[:60].strip(), content=body)
    pool = _FakePool('{"title": "Raft Leader Election Notes", "summary": "s"}')
    _run(ingest_item(store, iid, insights_pool=pool))
    item = store.get_item(iid)
    assert item["title"] == "Raft Leader Election Notes"  # placeholder replaced


def test_runner_does_not_clobber_user_tags(store):
    ensure_nodes_registered()
    iid = store.create_typed_item(
        item_type="note", title="N", content="the body text", tags=["mine"]
    )
    pool = _FakePool('{"summary": "s", "topics": ["auto-topic"]}')
    _run(ingest_item(store, iid, insights_pool=pool))
    assert store.get_item(iid)["tags"] == ["mine"]


def test_reenrich_refreshes_stale_ai_tags_and_summary(store):
    """A content edit re-runs enrichment; AI-seeded tags/summary must refresh to the
    new content instead of going stale (the bug: a kubernetes note edited to be about
    bread kept its kubernetes tags)."""
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="about kubernetes")
    _run(ingest_item(store, iid, insights_pool=_FakePool('{"summary": "k8s note", "topics": ["kubernetes", "helm"]}')))
    assert store.get_item(iid)["tags"] == ["kubernetes", "helm"]
    # Edit content + re-ingest with new enrichment output.
    store.update_item(iid, content="about sourdough bread")
    store.db.commit()
    _run(ingest_item(store, iid, insights_pool=_FakePool('{"summary": "bread note", "topics": ["sourdough", "baking"]}')))
    item = store.get_item(iid)
    assert item["tags"] == ["sourdough", "baking"]  # refreshed, not stale
    assert item["summary"] == "bread note"


def test_reenrich_preserves_user_edited_tags(store):
    """If the user changed the tags after the first enrichment, a re-ingest must NOT
    overwrite them (they no longer match the previous AI topics)."""
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="about kubernetes")
    _run(ingest_item(store, iid, insights_pool=_FakePool('{"summary": "s", "topics": ["kubernetes"]}')))
    # User curates the tags.
    store.update_item(iid, tags=["my-curated-tag"])
    store.db.commit()
    _run(ingest_item(store, iid, insights_pool=_FakePool('{"summary": "s", "topics": ["something-else"]}')))
    assert store.get_item(iid)["tags"] == ["my-curated-tag"]  # preserved


def test_friendly_fetch_error_messages():
    """A failed bookmark fetch records a human-readable reason + a kind, not a raw socket
    errno — so the UI's tooltip is understandable and reachability problems are classed
    'unreachable' (retryable) vs an unexpected 'error'."""
    import socket

    import httpx

    from personalclaw.knowledge.connectors.web_url import _friendly_fetch_error

    msg, kind = _friendly_fetch_error(socket.gaierror(8, "nodename nor servname provided, or not known"))
    assert "could not be resolved" in msg and kind == "unreachable"
    msg, kind = _friendly_fetch_error(httpx.ConnectError("x"))
    assert "unreachable" in msg and kind == "unreachable"
    msg, kind = _friendly_fetch_error(httpx.TimeoutException("x"))
    assert "timed out" in msg and kind == "unreachable"
    # Unknown errors still degrade to a readable wrapper (no raw traceback leak) + 'error'.
    msg, kind = _friendly_fetch_error(ValueError("odd"))
    assert msg.startswith("Couldn't fetch the page") and kind == "error"


def test_runner_surfaces_failed_node_error(store, monkeypatch):
    """When a node fails (e.g. a bookmark scrape 404), the item records WHY in
    processing_error — not a bare 'failed' with no reason."""
    ensure_nodes_registered()

    async def _fail_fetch(self, source):
        return "", {"error": "HTTP 404 Not Found", "url": source["uri"]}

    monkeypatch.setattr(
        "personalclaw.knowledge.connectors.web_url.WebUrlConnector.fetch", _fail_fetch
    )
    iid = store.create_typed_item(item_type="bookmark", title="", url="https://example.com/nope")
    status = _run(ingest_item(store, iid))
    assert status == "failed"
    item = store.get_item(iid)
    assert item["processing_status"] == "failed"
    assert "404" in (item.get("processing_error") or "")
    assert "bookmark_scrape" in (item.get("processing_error") or "")


def test_runner_entity_stage_populates_graph(store):
    """The terminal entity stage extracts entities+relations from the consolidated
    text into the entity graph (entities + mentions + relations), keyed by item_id."""
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="Redis caches sessions for the API.")
    pool = _FakePool(
        '{"entities": [{"name": "Redis", "type": "technology"}, {"name": "API", "type": "service"}],'
        ' "relations": [{"source": "API", "target": "Redis", "type": "uses"}]}'
    )
    status = _run(ingest_item(store, iid, insights_pool=pool))
    assert status == "done"
    names = {r["name"] for r in store.db.execute("SELECT name FROM entities").fetchall()}
    assert {"Redis", "API"} <= names
    mentions = store.db.execute("SELECT COUNT(*) FROM mentions WHERE item_id = ?", (iid,)).fetchone()[0]
    assert mentions == 2
    rels = store.db.execute("SELECT COUNT(*) FROM entity_relations WHERE source_item_id = ?", (iid,)).fetchone()[0]
    assert rels == 1


def test_runner_entity_stage_reingest_does_not_dup(store):
    """Re-ingesting clears the item's prior entity rows first (no duplication)."""
    ensure_nodes_registered()
    iid = store.create_typed_item(item_type="note", title="N", content="Redis caches sessions.")
    pool = _FakePool('{"entities": [{"name": "Redis", "type": "technology"}], "relations": []}')
    _run(ingest_item(store, iid, insights_pool=pool))
    _run(ingest_item(store, iid, insights_pool=pool))
    mentions = store.db.execute("SELECT COUNT(*) FROM mentions WHERE item_id = ?", (iid,)).fetchone()[0]
    assert mentions == 1


def test_bookmark_scrape_node_fetches_and_titles(store, monkeypatch):
    """A URL-only bookmark is scraped → content + derived url_title/url_description
    land on the ONE item (no source row, no chunks)."""
    ensure_nodes_registered()

    async def _fake_fetch(self, source):
        return "# Example Domain\n\nThis domain is for examples.", {"url": source["uri"]}

    monkeypatch.setattr(
        "personalclaw.knowledge.connectors.web_url.WebUrlConnector.fetch", _fake_fetch
    )
    iid = store.create_typed_item(item_type="bookmark", title="", url="https://example.com/")
    status = _run(ingest_item(store, iid))
    assert status == "done"
    item = store.get_item(iid)
    assert "Example Domain" in (item["content"] or "")
    assert item["url_title"] == "Example Domain"  # leading "# " stripped
    assert item["url_description"]
    # Exactly one row — no chunk/source fan-out.
    assert store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1


def test_bookmark_unreachable_url_marks_unreachable_not_failed(store, monkeypatch):
    """A bookmark whose page can't be fetched (network/DNS/timeout/HTTP error) is a
    retryable, NON-failure state: the URL is saved + clickable. The item lands
    'unreachable' (distinct from 'failed', which means an unexpected fault), so the UI
    can show 'Unreachable · Retry'."""
    ensure_nodes_registered()

    async def _fail_fetch(self, source):
        return "", {"error": "Couldn't reach the site (it may not exist or is unreachable).",
                    "error_kind": "unreachable", "url": source["uri"]}
    monkeypatch.setattr(
        "personalclaw.knowledge.connectors.web_url.WebUrlConnector.fetch", _fail_fetch
    )
    iid = store.create_typed_item(item_type="bookmark", title="", url="https://nope.invalid/x")
    status = _run(ingest_item(store, iid))
    assert status == "unreachable"
    item = store.get_item(iid)
    assert item["processing_status"] == "unreachable"
    assert "reach" in (item.get("processing_error") or "").lower()
    # The URL is preserved — the bookmark is still a usable saved link.
    assert item["url"] == "https://nope.invalid/x"


def test_bookmark_scrape_promotes_url_title_over_raw_url(store, monkeypatch):
    """The handler seeds a bookmark's title with the raw URL (no title known yet).
    Once scraped, the page's real title is promoted to the displayed title so the
    Library shows it instead of the URL."""
    ensure_nodes_registered()

    async def _fake_fetch(self, source):
        return "# Example Domain\n\nThis domain is for examples.", {"url": source["uri"]}

    monkeypatch.setattr(
        "personalclaw.knowledge.connectors.web_url.WebUrlConnector.fetch", _fake_fetch
    )
    # Mirror the handler: title is seeded to the URL when nothing else is known.
    iid = store.create_typed_item(
        item_type="bookmark", title="https://example.com/", url="https://example.com/"
    )
    _run(ingest_item(store, iid))
    item = store.get_item(iid)
    assert item["title"] == "Example Domain"  # promoted off the raw URL
    assert item["url_title"] == "Example Domain"


def test_extract_html_metadata_prefers_og_then_title():
    from personalclaw.knowledge.connectors.base import extract_html_metadata

    # OpenGraph wins over <title> / meta description.
    og = ('<head><title>Fallback</title>'
          '<meta property="og:title" content="OG Title">'
          '<meta property="og:description" content="OG desc"></head>')
    assert extract_html_metadata(og) == {"title": "OG Title", "description": "OG desc"}
    # Falls back to <title> (whitespace-collapsed) + meta description.
    plain = '<title> Welcome  to  Python.org </title><meta name="description" content="The home">'
    assert extract_html_metadata(plain) == {"title": "Welcome to Python.org", "description": "The home"}
    # content attr before the identifying attr; HTML entities unescaped; empty page.
    assert extract_html_metadata('<meta content="D" name="description">') == {"description": "D"}
    assert extract_html_metadata("<title>Tom &amp; Jerry</title>") == {"title": "Tom & Jerry"}
    assert extract_html_metadata("<body>hi</body>") == {}


def test_html_to_text_strips_site_chrome():
    """A bookmark scrape must capture the page's CONTENT, not its site chrome — html2text
    keeps <nav>/<header>/<footer> text by default, so e.g. a GitHub repo page would lead
    with 'Skip to content / Sign in / …'. html_to_text strips that before converting."""
    from personalclaw.knowledge.connectors.base import html_to_text

    gh = ('<html><body><header><nav>Skip to content Sign in</nav></header>'
          '<main><h1>python/cpython</h1><p>The Python programming language.</p></main>'
          '<footer>(c) GitHub</footer></body></html>')
    out = html_to_text(gh)
    assert "python/cpython" in out and "The Python programming language." in out
    assert "Skip to content" not in out and "Sign in" not in out and "GitHub" not in out

    # No <main>/<article> region → strip page-frame header/footer + nav too.
    plain = ('<html><body><nav>NAV</nav><header>SITE</header>'
             '<div><h1>Title</h1><p>Body text.</p></div><footer>FOOT</footer></body></html>')
    out2 = html_to_text(plain)
    assert "Title" in out2 and "Body text." in out2
    assert "NAV" not in out2 and "SITE" not in out2 and "FOOT" not in out2

    # An ARTICLE's own <header> (its title/byline) is kept — only page chrome is dropped.
    art = ('<html><body><main><header><h1>Article Title</h1></header>'
           '<nav>related links</nav><p>Article body.</p></main></body></html>')
    out3 = html_to_text(art)
    assert "Article Title" in out3 and "Article body." in out3
    assert "related links" not in out3  # nav inside main still stripped


def test_bookmark_scrape_prefers_page_metadata_over_body(store, monkeypatch):
    """The scrape node uses the page's real <title>/meta description (from the
    connector) instead of guessing from the body text's first line."""
    ensure_nodes_registered()

    async def _fake_fetch(self, source):
        # Body text would yield a poor first-line title; metadata is authoritative.
        return ("Some boilerplate nav text first\n\nReal article body.",
                {"url": source["uri"], "page_title": "The Real Title",
                 "page_description": "A proper meta description."})

    monkeypatch.setattr(
        "personalclaw.knowledge.connectors.web_url.WebUrlConnector.fetch", _fake_fetch
    )
    iid = store.create_typed_item(
        item_type="bookmark", title="https://x.test/", url="https://x.test/"
    )
    _run(ingest_item(store, iid))
    item = store.get_item(iid)
    assert item["url_title"] == "The Real Title"
    assert item["url_description"] == "A proper meta description."
    assert item["title"] == "The Real Title"  # promoted off the raw URL


def test_bookmark_scrape_keeps_user_title(store, monkeypatch):
    """A user-authored bookmark title is never clobbered by the scraped page title."""
    ensure_nodes_registered()

    async def _fake_fetch(self, source):
        return "# Example Domain\n\nExamples.", {"url": source["uri"]}

    monkeypatch.setattr(
        "personalclaw.knowledge.connectors.web_url.WebUrlConnector.fetch", _fake_fetch
    )
    iid = store.create_typed_item(
        item_type="bookmark", title="My saved page", url="https://example.com/"
    )
    _run(ingest_item(store, iid))
    assert store.get_item(iid)["title"] == "My saved page"


def test_bookmark_scrape_preserves_user_content(store):
    """A bookmark the user pasted content into is NOT overwritten by a scrape."""
    ensure_nodes_registered()
    iid = store.create_typed_item(
        item_type="bookmark", title="B", content="my own notes", url="https://example.com/"
    )
    _run(ingest_item(store, iid))
    assert store.get_item(iid)["content"] == "my own notes"


def test_runner_persists_exif_metadata_onto_item(store, tmp_path):
    ensure_nodes_registered()
    # A real 1x1 PNG so the pure-python exif (Pillow) node succeeds.
    try:
        from PIL import Image
    except ImportError:
        import pytest as _pytest
        _pytest.skip("Pillow not installed")
    img = tmp_path / "px.png"
    Image.new("RGB", (3, 5), "white").save(img)
    iid = store.create_typed_item(item_type="image", title="px.png")
    store.update_item(iid, file_path=str(img))
    store.db.commit()
    _run(ingest_item(store, iid))
    meta = store.get_item(iid).get("file_metadata") or {}
    assert meta.get("width") == 3 and meta.get("height") == 5 and meta.get("format") == "PNG"


def test_runner_records_skip_reason_on_partial(store, tmp_path, monkeypatch):
    """An image with no model pool skips its vision/ocr nodes → partial. The reason
    must be persisted (not left blank) so the detail UI explains the badge after a
    reload, when the live per-node SSE phases are gone."""
    try:
        from PIL import Image
    except ImportError:
        import pytest as _pytest
        _pytest.skip("Pillow not installed")
    ensure_nodes_registered()
    # "No model pool" is the premise — pin the vision/ocr use-cases unresolvable so the
    # test is hermetic (doesn't depend on the dev box's live active_models.json, which
    # may have image_modality bound). Without this the nodes try to resolve a real
    # provider and the skip path never runs.
    import personalclaw.knowledge.pipeline.executor as _ex
    _orig_can = _ex.can_resolve_use_case
    # OCR + vision now resolve DIRECTLY to image_modality (no dedicated ingestion
    # use-case), so make image_modality "no model" to force the optional-step skip.
    monkeypatch.setattr(_ex, "can_resolve_use_case",
                        lambda uc: False if uc == "image_modality" else _orig_can(uc))
    img = tmp_path / "px.png"
    Image.new("RGB", (4, 4), "white").save(img)
    iid = store.create_typed_item(item_type="image", title="px.png")
    store.update_item(iid, file_path=str(img))
    store.db.commit()
    status = _run(ingest_item(store, iid, insights_pool=None))  # no model → vision/ocr skip
    item = store.get_item(iid)
    assert status == "partial"
    err = item.get("processing_error") or ""
    assert err.startswith("Skipped (optional steps unavailable):")
    assert "vision" in err or "ocr" in err


def test_graph_for_known_types():
    assert graph_for("note").item_type == "note"
    assert "document_read" in graph_for("pdf").nodes
    assert "passthrough" in graph_for("gist").nodes


# ── P12 TIER-2 semantic dedup — the WIRED path (store prefilter + runner stage) ──

class _StubEmbedder:
    """Minimal embedder for dedup tests — available; embed_for_item unused (we write the
    vector directly). ``is_available`` gates the runner's _dedup stage."""
    def __init__(self, available=True):
        self._a = available

    def is_available(self):
        return self._a


def _seed_item(store, *, title, vec, item_type="note", **extra):
    """Create a typed item and write its embedding BLOB directly (bypassing the embedder)."""
    from personalclaw.knowledge.embedder import floats_to_bytes
    iid = store.create_typed_item(item_type=item_type, title=title, content=title, **extra)
    store.db.execute("UPDATE items SET embedding = ? WHERE id = ?", (floats_to_bytes(vec), iid))
    store.db.commit()
    return iid


def test_find_fuzzy_dup_candidates_filters_type_self_and_no_embedding(store):
    v = [1.0, 0.0, 0.0, 0.0]
    keep = _seed_item(store, title="Architecture Overview", vec=v, item_type="note")
    _seed_item(store, title="Other Note", vec=v, item_type="note")            # same type → candidate
    _seed_item(store, title="A Bookmark", vec=v, item_type="bookmark")        # diff type → excluded
    # An embedding-less same-type item → excluded (no vector to compare).
    store.create_typed_item(item_type="note", title="No Vector Note", content="x")
    cands = store.find_fuzzy_dup_candidates(keep)
    titles = {c["title"] for c in cands}
    assert "Other Note" in titles
    assert "A Bookmark" not in titles              # type filter
    assert "Architecture Overview" not in titles   # excludes self
    assert "No Vector Note" not in titles           # embedding IS NULL filter
    # candidates carry a DECODED vector (the resolver needs raw floats)
    assert all(isinstance(c["embedding"], list) and c["embedding"] for c in cands)


def test_dedup_archives_format_recall_loser_on_confirmed_dup(store):
    from personalclaw.knowledge.pipeline.runner import _dedup
    v = [1.0, 0.0, 0.0, 0.0]
    # Existing rich copy (done, high word_count) + a new thin near-identical copy.
    rich = _seed_item(store, title="Architecture Overview", vec=v, item_type="note",
                      extra={"processing_status": "done", "word_count": 900})
    thin = _seed_item(store, title="Architecture Overview.pdf", vec=v, item_type="note",
                      extra={"processing_status": "partial", "word_count": 50})
    res = _dedup(store, thin, _StubEmbedder())
    assert res is not None, "a confirmed fuzzy dup should fire"
    # Format-recall keeps the richer copy → the THIN one is archived, the rich one stays.
    assert res["loser_id"] == thin and res["winner_id"] == rich
    assert store.get_item(thin)["is_archived"] is True
    assert store.get_item(rich)["is_archived"] is False


def test_dedup_respects_the_series_date_gate(store):
    """THE HEADLINE GUARD, end-to-end: two same-title near-identical-vector items with
    DIFFERENT recurring-series dates must BOTH survive (never collapse a report series)."""
    from personalclaw.knowledge.pipeline.runner import _dedup
    v = [1.0, 0.0, 0.0, 0.0]
    d1 = _seed_item(store, title="Weekly Report 2026-07-01", vec=v, item_type="note")
    d2 = _seed_item(store, title="Weekly Report 2026-07-08", vec=v, item_type="note")
    res = _dedup(store, d2, _StubEmbedder())
    assert res is None, "differing series dates → NOT a dup"
    assert store.get_item(d1)["is_archived"] is False
    assert store.get_item(d2)["is_archived"] is False


def test_dedup_noop_without_embedder(store):
    """No embedder / unavailable → the stage is inert (behaves exactly as pre-P12)."""
    from personalclaw.knowledge.pipeline.runner import _dedup
    v = [1.0, 0.0, 0.0, 0.0]
    _seed_item(store, title="Architecture Overview", vec=v, item_type="note")
    thin = _seed_item(store, title="Architecture Overview.pdf", vec=v, item_type="note")
    assert _dedup(store, thin, None) is None                       # no embedder
    assert _dedup(store, thin, _StubEmbedder(available=False)) is None  # unavailable
    assert store.get_item(thin)["is_archived"] is False
