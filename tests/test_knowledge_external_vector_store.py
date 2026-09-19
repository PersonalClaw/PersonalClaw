"""KBVS-1 — the external chunk-vector backend seam.

The atom's deliverable is the SEAM, not a vendor client, so these tests exercise it with a
dependency-free in-repo backend. The vendor half (a real Qdrant) is proven in the apps repo
by ``vector-store-qdrant/test_provider.py``, which drives the same contract through
``qdrant-client``.

The four clauses, each with the assertion that can falsify it:

1. **Core carries no vendor client.** ``test_core_carries_no_vector_vendor_client`` greps
   ``src/personalclaw/`` for six vendor names. This rail can fail — see the file's own note.
2. **Default unchanged.** With nothing bound, the external arm is not merely empty but never
   consulted (``test_nothing_bound_never_consults_the_external_arm``), and the local path's
   results are unchanged (``test_nothing_bound_results_match_the_local_path``).
3. **Incremental indexing.** Ingesting one more document upserts exactly that document's
   chunks (``test_adding_one_document_indexes_only_that_document``).
4. **Ships as a provider.** ``test_vector_store_is_a_registered_provider_type`` pins the
   manifest type + type handler + SDK facade.
"""

from __future__ import annotations

import re
import struct
import subprocess
from pathlib import Path

import pytest

from personalclaw.knowledge.chunking import Chunk
from personalclaw.knowledge.retrieval import _VECTOR_MIN_SIMILARITY, HybridRetriever
from personalclaw.knowledge.store import KnowledgeStore
from personalclaw.vector_stores import registry as vs_registry
from personalclaw.vector_stores.base import (
    VectorHit,
    VectorRecord,
    VectorStoreInfo,
    VectorStoreProvider,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DIM = 4


def _v(*vals: float) -> bytes:
    return struct.pack(f"{len(vals)}f", *vals)


def _cosine(a, b) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return 0.0 if not na or not nb else dot / (na * nb)


class FakeExternalStore(VectorStoreProvider):
    """A VectorStoreProvider backed by a plain dict — the seam's test double.

    It is a real implementation of the contract (descending-similarity ordering included),
    not a mock, so a contract change breaks it the way it would break an app. ``calls``
    records every method invocation so a test can assert the arm was — or was NOT — consulted.
    """

    name = "fake-external"

    def __init__(self, *, fail: bool = False) -> None:
        self.rows: dict[str, VectorRecord] = {}
        self.calls: list[tuple[str, object]] = []
        self.fail = fail

    def upsert(self, records) -> int:
        self.calls.append(("upsert", [r.chunk_id for r in records]))
        if self.fail:
            raise RuntimeError("store unreachable")
        for r in records:
            self.rows[r.chunk_id] = r
        return len(records)

    def delete_item(self, item_id: str) -> int:
        self.calls.append(("delete_item", item_id))
        if self.fail:
            raise RuntimeError("store unreachable")
        gone = [cid for cid, r in self.rows.items() if r.item_id == item_id]
        for cid in gone:
            del self.rows[cid]
        return len(gone)

    def query(self, vector, *, k: int):
        self.calls.append(("query", k))
        if self.fail:
            raise RuntimeError("store unreachable")
        scored = [
            VectorHit(chunk_id=r.chunk_id, item_id=r.item_id, similarity=_cosine(vector, r.vector))
            for r in self.rows.values()
        ]
        scored.sort(key=lambda h: h.similarity, reverse=True)
        return scored[:k]

    def describe(self) -> VectorStoreInfo:
        return VectorStoreInfo(
            backend="fake",
            collection="test",
            dimension=_DIM,
            count=len(self.rows),
            reachable=not self.fail,
            detail="in-memory test double",
        )


@pytest.fixture()
def store(tmp_path):
    s = KnowledgeStore(str(tmp_path / "kbvs1.db"))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _clean_registry():
    """Every test starts and ends with nothing bound — the default state.

    Autouse, because the registry is module-level: a test that registered a backend and
    returned would silently bind it for every later test in the session, including the
    parity tests whose whole point is that nothing is bound.
    """
    for name in vs_registry.list_providers():
        vs_registry.unregister_provider(name)
    vs_registry._warned_sets.clear()
    yield
    for name in vs_registry.list_providers():
        vs_registry.unregister_provider(name)
    vs_registry._warned_sets.clear()


def _bind(provider) -> None:
    vs_registry.register_provider(provider.name, provider)


def _add_doc(store, title: str, body: str, *, vectors: list[tuple[float, ...]]) -> str:
    """Create an item whose chunks carry *vectors*. Returns the item id.

    The item's OWN whole-item vector is left NULL so the only vector signal in play is the
    chunk one — otherwise the whole-item arm (which stays local by design) could supply the
    hit a test means to attribute to the chunk arm.
    """
    iid = store.create_typed_item(item_type="note", title=title, content=body)
    chunks = [
        Chunk(
            text=f"{title} passage {i}",
            section=f"s{i}",
            line_start=i * 2 + 1,
            line_end=i * 2 + 2,
            chunk_index=i,
            embedding=_v(*vec),
        )
        for i, vec in enumerate(vectors)
    ]
    store.replace_chunks(iid, chunks)
    return iid


def _embedder(vec):
    return lambda _q: list(vec)


# ── clause 1: the vendor client is not in core ────────────────────────────────────────

#: The six names the atom's rail (1) enumerates.
_VENDORS = re.compile(r"qdrant|pgvector|chromadb|weaviate|pinecone|milvus", re.IGNORECASE)


def _prose_lines(path: Path) -> set[int]:
    """Every line of *path* that is a comment or part of a docstring.

    Computed, not guessed. The obvious spelling of this rail — grep, then skip lines that
    ``startswith("#")`` — is vacuous in one direction and wrong in the other: it misses a
    vendor name on the second line of a wrapped docstring (flagging prose as code), and it
    exempts an ``# import qdrant_client`` that a contributor commented out and a later one
    uncommented. Comment lines come from ``tokenize``; docstring spans come from ``ast``, so a
    multi-line docstring is exempt across all of its lines and a non-docstring string literal
    is exempt across none of them.
    """
    import ast as _ast
    import io
    import tokenize

    src = path.read_text(encoding="utf-8")
    lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                lines.add(tok.start[0])
    except (tokenize.TokenError, IndentationError, SyntaxError):  # pragma: no cover
        return lines
    try:
        tree = _ast.parse(src)
    except SyntaxError:  # pragma: no cover
        return lines
    for node in _ast.walk(tree):
        if not isinstance(
            node, (_ast.Module, _ast.ClassDef, _ast.FunctionDef, _ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, _ast.Expr) and isinstance(first.value, _ast.Constant):
            if isinstance(first.value.value, str):
                lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def test_core_carries_no_vector_vendor_client():
    """No vendor vector-store name in CODE anywhere under ``src/personalclaw/``.

    🔴 THIS RAIL WAS PROVEN ABLE TO FAIL. Adding ``_QDRANT_DEFAULT_PORT = 6333`` to
    ``knowledge/retrieval.py`` — the realistic drift, a vendor-shaped constant rather than an
    import — reds it naming ``retrieval.py:74``. (An ``import qdrant_client`` reds it too, but
    louder and less usefully: the module is not installed, so collection dies with
    ``ModuleNotFoundError`` before the assertion runs.)

    ``--untracked`` is load-bearing. Plain ``git grep`` searches TRACKED files, so a vendor
    client dropped into a brand-new module — precisely how a client would arrive — is invisible
    to it until the moment it is committed, which is after review.

    The allowed hits are prose: ``memory_service.py``'s VISION quote ("Qdrant primary →
    filesystem plain-text fallback"), and the seam's own docs, which must be able to name the
    stores they exist to support.
    """
    out = subprocess.run(  # noqa: S603
        ["git", "grep", "-nIiE", "--untracked", _VENDORS.pattern, "--", "src/personalclaw/"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    assert out, "the grep found nothing at all — the rail is not looking where it thinks it is"

    prose_cache: dict[str, set[int]] = {}
    offenders = []
    for line in out:
        # path:lineno:text — split on the first two colons only, the text may hold more.
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        rel, lineno_s, _body = parts
        if not lineno_s.isdigit():
            continue
        if rel.endswith(".py"):
            if rel not in prose_cache:
                prose_cache[rel] = _prose_lines(_REPO_ROOT / rel)
            if int(lineno_s) in prose_cache[rel]:
                continue  # a comment or docstring line naming a vendor in prose
        offenders.append(line)
    assert not offenders, (
        "core must carry no vector-store vendor client — the client belongs to the app "
        "bundle (docs/architecture/provider-boundary.md). Offending lines:\n" + "\n".join(offenders)
    )


def test_a_core_import_of_a_vendor_client_is_not_reachable():
    """The positive form of the same claim: nothing importable from core pulls a vendor in.

    Asserted on the seam modules specifically, because a vendor import that lands there is the
    plausible mistake — the ABC is where a well-meaning contributor would "just add a default
    Qdrant implementation".
    """
    import personalclaw.vector_stores.base as base
    import personalclaw.vector_stores.registry as reg

    for mod in (base, reg):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                assert not _VENDORS.search(stripped), f"{mod.__name__}: vendor import {stripped!r}"


# ── clause 2: nothing bound ⇒ the local path, untouched ───────────────────────────────


def test_nothing_bound_never_consults_the_external_arm(store, monkeypatch):
    """Not "returns empty" — NEVER CALLED. A backend consulted and ignored would be an
    unbound install paying a query's latency, and would make the opt-in a fiction."""
    _add_doc(store, "Vector notes", "alpha beta", vectors=[(1.0, 0.0, 0.0, 0.0)])

    calls: list[str] = []

    def _tripwire():
        calls.append("consulted")
        return None

    monkeypatch.setattr("personalclaw.knowledge.retrieval.active_vector_store", _tripwire)
    retriever = HybridRetriever(store, embedder=_embedder((1.0, 0.0, 0.0, 0.0)))
    hits = retriever.search("vector notes", limit=5)

    assert hits, "the local path must still answer"
    assert calls == ["consulted"], "the resolver is asked once per query and returns None"
    assert all(vs_registry.active_provider() is None for _ in range(2))


def test_nothing_bound_results_match_the_local_path(store):
    """The local chunk arm still produces the hit and still cites the winning passage.

    The full before/after equality against ``origin/main`` is measured out-of-band by
    ``scripts/`` — see the PR body; what is pinned HERE is that the arm attribution and the
    locator a caller reads are the local ones.
    """
    _add_doc(
        store,
        "Local only",
        "alpha beta gamma",
        vectors=[(0.0, 1.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)],
    )
    retriever = HybridRetriever(store, embedder=_embedder((1.0, 0.0, 0.0, 0.0)))
    hits = retriever.search("nothing in common", limit=5)
    assert len(hits) == 1
    assert "vector" in hits[0]["match_type"]
    # chunk_index 1 is the (1,0,0,0) chunk — its section, not chunk 0's.
    assert hits[0]["section"] == "s1"


def test_two_backends_bound_refuses_and_uses_the_local_path(store, caplog):
    """Ambiguity is refused, loudly, not resolved arbitrarily."""
    a, b = FakeExternalStore(), FakeExternalStore()
    b.name = "fake-external-2"
    _bind(a)
    _bind(b)
    with caplog.at_level("WARNING"):
        assert vs_registry.active_provider() is None
    assert "refusing to pick one" in caplog.text
    assert "fake-external" in caplog.text and "fake-external-2" in caplog.text


# ── the seam itself: a bound backend serves the vector arm ────────────────────────────


def test_bound_backend_serves_the_vector_arm_and_the_local_index_does_not(store):
    """THE atom's proof, in the seam's own terms.

    Two documents. The external store is given ONLY document B's vector; the local chunk
    table holds both. A query aimed at B's vector returns B — and the assertion that makes
    this about the SEAM rather than about luck is the negative one: document A, whose vector
    is an equally good local match, does NOT come back through the vector arm, because the
    local chunk index was never consulted.
    """
    q = (1.0, 0.0, 0.0, 0.0)
    a_id = _add_doc(store, "Alpha doc", "aaa", vectors=[q])
    b_id = _add_doc(store, "Bravo doc", "bbb", vectors=[q])

    ext = FakeExternalStore()
    # Only B is in the external store: drop A's rows the write-through put there.
    _bind(ext)
    store.replace_chunks(
        b_id,
        [
            Chunk(
                text="bbb", section="sB", line_start=1, line_end=2, chunk_index=0, embedding=_v(*q)
            )
        ],
    )
    assert {r.item_id for r in ext.rows.values()} == {b_id}

    retriever = HybridRetriever(store, embedder=_embedder(q))
    vec_arm = retriever._vector_search("zzz-no-keyword-match", limit=10)

    assert ("query", 10 * 4) in [(c[0], c[1]) for c in ext.calls], "the backend was queried"
    ranked = [iid for iid, _ in vec_arm]
    assert b_id in ranked, "the external store's hit reached the vector arm"
    assert a_id not in ranked, (
        "document A is only in the LOCAL chunk index; its presence would prove the vec0 path "
        "still ran alongside the external one"
    )


def test_a_hit_from_the_external_store_fuses_through_rrf(store):
    """End to end: ingest → index externally → query → the fused result carries the passage.

    ``match_type`` containing ``vector`` is what proves the hit arrived through the vector arm
    and was fused, rather than being found by FTS5 on the query terms.
    """
    q = (0.0, 0.0, 1.0, 0.0)
    ext = FakeExternalStore()
    _bind(ext)
    iid = _add_doc(store, "Externally indexed", "zulu yankee", vectors=[(0.0, 0.0, 0.0, 1.0), q])
    assert len(ext.rows) == 2, "ingest indexed both chunks into the external store"

    retriever = HybridRetriever(store, embedder=_embedder(q))
    hits = retriever.search("nothing matches these terms", limit=5)

    assert [h["id"] for h in hits] == [iid]
    assert hits[0]["match_type"] == "vector"
    assert hits[0]["section"] == "s1", "the locator comes from the LOCAL chunk row"


def test_core_applies_its_own_floor_to_the_backends_similarity(store):
    """The backend supplies the cosine; core supplies the floor. A below-floor external hit
    must not rank — otherwise binding a store silently re-tunes a calibrated threshold."""
    ext = FakeExternalStore()
    _bind(ext)
    # Orthogonal to the query ⇒ cosine 0.0, far below the floor.
    _add_doc(store, "Orthogonal", "qqq", vectors=[(0.0, 1.0, 0.0, 0.0)])
    assert _VECTOR_MIN_SIMILARITY > 0.0

    retriever = HybridRetriever(store, embedder=_embedder((1.0, 0.0, 0.0, 0.0)))
    assert retriever._vector_search("qqq", limit=5) == []


def test_archived_and_deleted_candidates_are_filtered_by_core(store):
    """A stale extra in the external store cannot resurrect an archived item.

    The liveness join is core's, shared with the vec0 walk, so "what can a search see" has one
    answer regardless of which backend generated the candidate.
    """
    q = (1.0, 0.0, 0.0, 0.0)
    ext = FakeExternalStore()
    _bind(ext)
    iid = _add_doc(store, "To be archived", "ttt", vectors=[q])
    assert ext.rows

    store.db.execute("UPDATE items SET is_archived = 1 WHERE id = ?", (iid,))
    store.db.commit()

    retriever = HybridRetriever(store, embedder=_embedder(q))
    assert retriever._vector_search("ttt", limit=5) == []
    assert retriever._vector_search(
        "ttt", limit=5, include_archived=True
    ), "include_archived must still reach it — the filter is core's usual one, not a new rule"


def test_an_unreachable_backend_degrades_and_does_not_substitute(store, caplog):
    """Bound + broken ⇒ the chunk arm contributes nothing, at WARNING; the other arms answer.

    The negative assertion is the important one: the item IS in the local chunk index and an
    exact-cosine match for the query, so a hit attributed to ``vector`` here would mean core
    quietly answered from a shadow index while the user's store was down.
    """
    q = (1.0, 0.0, 0.0, 0.0)
    _add_doc(store, "Findable by keyword", "sentinelword", vectors=[q])
    _bind(FakeExternalStore(fail=True))

    retriever = HybridRetriever(store, embedder=_embedder(q))
    with caplog.at_level("WARNING"):
        hits = retriever.search("sentinelword", limit=5)

    assert hits, "FTS5 still answers — a broken index must never fail a search"
    assert "vector" not in hits[0]["match_type"], "no silent fallback to the local vec0 index"
    assert "external vector store" in caplog.text and "failed" in caplog.text


# ── clause 3: incremental indexing + the write-through sites ──────────────────────────


def test_adding_one_document_indexes_only_that_document(store):
    """A re-poll that added one document must index that document's chunks and nothing else.

    True by construction rather than by optimization: the write-through hangs off
    ``replace_chunks``, which is per item, and there is no corpus-walking reindex path at all.
    """
    ext = FakeExternalStore()
    _bind(ext)
    first = _add_doc(store, "First", "one", vectors=[(1.0, 0.0, 0.0, 0.0)])
    ext.calls.clear()

    second = _add_doc(store, "Second", "two", vectors=[(0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0)])

    touched = {
        cid
        for verb, payload in ext.calls
        if verb == "upsert"
        for cid in payload  # type: ignore[union-attr]
    }
    assert touched == {cid for cid, r in ext.rows.items() if r.item_id == second}
    assert all(payload == second for verb, payload in ext.calls if verb == "delete_item")
    assert {r.item_id for r in ext.rows.values()} == {first, second}, "the first doc survived"


def test_a_rechunk_replaces_rather_than_orphans(store):
    """A re-chunk mints fresh chunk ids, so upsert alone would leave the old generation behind
    as permanent orphan candidates. Delete-then-upsert, mirroring the local table's own
    delete-then-insert."""
    ext = FakeExternalStore()
    _bind(ext)
    iid = _add_doc(store, "Rechunked", "first version", vectors=[(1.0, 0.0, 0.0, 0.0)])
    old_ids = set(ext.rows)

    store.replace_chunks(
        iid,
        [
            Chunk(
                text="second version",
                section="s0",
                line_start=1,
                line_end=2,
                chunk_index=0,
                embedding=_v(0.0, 1.0, 0.0, 0.0),
            )
        ],
    )
    assert set(ext.rows).isdisjoint(old_ids), "the previous generation's vectors are gone"
    assert len(ext.rows) == 1


def test_deleting_an_item_drops_its_vectors(store):
    ext = FakeExternalStore()
    _bind(ext)
    iid = _add_doc(store, "Doomed", "xxx", vectors=[(1.0, 0.0, 0.0, 0.0)])
    assert ext.rows
    store.delete_item(iid)
    assert not ext.rows


def test_clear_chunks_drops_its_vectors(store):
    ext = FakeExternalStore()
    _bind(ext)
    iid = _add_doc(store, "Cleared", "yyy", vectors=[(1.0, 0.0, 0.0, 0.0)])
    assert ext.rows
    store.clear_chunks(iid)
    assert not ext.rows


def test_an_index_write_failure_never_fails_the_chunk_write(store):
    """The same rule ``ChunkVectorIndex`` follows: a shadow index must not be able to lose a
    chunk. The local rows must be there even though the external write raised."""
    _bind(FakeExternalStore(fail=True))
    iid = _add_doc(store, "Written anyway", "zzz", vectors=[(1.0, 0.0, 0.0, 0.0)])
    assert len(store.get_chunks(iid)) == 1


def test_a_chunk_with_no_embedding_is_not_indexed(store):
    """Mid-backfill, chunks exist with a NULL embedding. Nothing to index, and the retrieval
    join excludes them anyway — so sending them would be a write with no reader."""
    ext = FakeExternalStore()
    _bind(ext)
    iid = store.create_typed_item(item_type="note", title="Unembedded", content="body")
    store.replace_chunks(
        iid, [Chunk(text="body", section=None, line_start=1, line_end=1, chunk_index=0)]
    )
    assert store.get_chunks(iid), "the local chunk row exists"
    assert not ext.rows, "but there is no vector to index"


# ── clause 4: it ships as a provider ─────────────────────────────────────────────────


def test_vector_store_is_a_registered_provider_type():
    """Manifest type + runtime handler + SDK facade, together.

    The #47 rule: a ``PROVIDER_TYPES`` entry without a handler makes every manifest of that
    type fail validation at install; a handler without the entry does the same. The equality
    is guarded globally by ``test_manifest_types_match_handlers`` — this pins the specific
    pair so a removal names KBVS-1.
    """
    from personalclaw.apps.manifest import PROVIDER_TYPES
    from personalclaw.providers.registry import VectorStoreTypeHandler
    from personalclaw.sdk.vector_store import VectorStoreProvider as SdkProvider

    assert "vector_store" in PROVIDER_TYPES
    assert VectorStoreTypeHandler is not None
    assert SdkProvider is VectorStoreProvider, "the SDK is a facade, not a fork"


def test_the_contract_is_small_enough_to_implement(store):
    """Four methods, and a subclass missing any of them cannot be instantiated.

    An ABC whose abstractmethods drift is how an app that was valid yesterday breaks silently
    today, so the set is pinned by name.
    """
    assert VectorStoreProvider.__abstractmethods__ == frozenset(
        {"upsert", "delete_item", "query", "describe"}
    )
    with pytest.raises(TypeError):

        class Incomplete(VectorStoreProvider):  # noqa: D401
            def upsert(self, records):
                return 0

        Incomplete()  # type: ignore[abstract]


def test_the_record_and_hit_types_are_frozen():
    """Immutable, so a backend cannot mutate a record core still holds a reference to."""
    r = VectorRecord(chunk_id="c", item_id="i", chunk_index=0, vector=[1.0])
    h = VectorHit(chunk_id="c", item_id="i", similarity=1.0)
    for obj in (r, h):
        with pytest.raises(Exception):  # noqa: B017, PT011 - FrozenInstanceError
            obj.item_id = "other"  # type: ignore[misc]
