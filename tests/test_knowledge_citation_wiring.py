"""Citation wiring — a stored synthesis has to say WHICH source supports it.

Before this, the synthesis template satisfied the synthesized-kind citation rule by storing
the whole retrieved set as `citations`, while the stage prompt asked the model to cite as
`[n]` and nothing on the write path ever read an `[n]`. Two consequences, both measured:

* "which source supports this sentence" was unanswerable — the stored list said only what had
  been retrieved, which is true of every item in it equally.
* `config.knowledge.require_citations` had ZERO readers in the tree. A PATCH-allowlisted
  switch an owner could flip that changed nothing about what the store would accept.

So the tests here are about the seam, not the parser: the numbering the model reads is the
numbering the persist step resolves against, markers that resolve to nothing never reach a
reader, and an item that cited nothing is refused unless it says `unsourced: true`.

`personalclaw.knowledge.citations` (`MARKER_RE`, `SourceRef`, `Citation`, `Resolution`,
`register_sources`, `strip_markers`, `parse_markers`, `resolve`, `persist_form`,
`parse_persist_form`) is landing separately in the same change. Where it is not importable
yet, `_build_fake()` below stands in — a faithful, minimal implementation of that contract, so
these tests exercise THIS wiring rather than that parser. When the real module lands the
fixture yields it instead and the same assertions run against it unchanged.
"""

from __future__ import annotations

import json
import re
import sys
import types
from dataclasses import dataclass

import pytest

from personalclaw.action_providers.base import ActionContext
from personalclaw.action_providers.knowledge_persist_provider import (
    KnowledgePersistActionProvider,
    _coerce_source_refs,
    _resolve_citations,
    _write_item_citations,
)
from personalclaw.knowledge import semantics as sem
from personalclaw.workflows.bindings import BindingContext, resolve

# ── the frozen contract, as a stand-in ──

_MODULE_PATH = "personalclaw.knowledge.citations"


@dataclass(frozen=True)
class _SourceRef:
    marker: int
    item_id: str
    chunk_index: int = -1
    excerpt: str = ""


@dataclass(frozen=True)
class _Citation:
    marker: int
    item_id: str
    chunk_index: int = -1
    excerpt: str = ""


@dataclass(frozen=True)
class _Resolution:
    citations: tuple[_Citation, ...]
    dropped: tuple[int, ...]
    warnings: tuple[str, ...]
    text: str


def _build_fake() -> types.ModuleType:
    """The citations API, minimally and faithfully. Numbering is `enumerate(items, start=1)` —
    the same contract `_pipe_fenced_sources` has always used, which is why the two can be
    asserted against each other."""
    mod = types.ModuleType(_MODULE_PATH)
    marker_re = re.compile(r"\[(\d+)\]")

    def register_sources(items):
        refs = []
        for marker, item in enumerate(items or [], start=1):
            if isinstance(item, dict):
                item_id = str(item.get("item_id") or item.get("id") or "")
                excerpt = str(item.get("summary") or item.get("content") or "")[:160]
            else:
                item_id, excerpt = str(item), str(item)[:160]
            refs.append(_SourceRef(marker=marker, item_id=item_id, excerpt=excerpt))
        return tuple(refs)

    def strip_markers(text):
        return marker_re.sub("", text or "")

    def parse_markers(text):
        return tuple(sorted({int(n) for n in marker_re.findall(text or "")}))

    def resolve_markers(text, sources):
        by_marker = {int(s.marker): s for s in sources or []}
        kept, dropped = [], []
        for n in parse_markers(text):
            src = by_marker.get(n)
            if src is None:
                dropped.append(n)
                continue
            kept.append(
                _Citation(
                    marker=n,
                    item_id=src.item_id,
                    chunk_index=src.chunk_index,
                    excerpt=src.excerpt,
                )
            )
        out = text or ""
        for n in dropped:
            out = out.replace(f"[{n}]", "")
        return _Resolution(
            citations=tuple(kept),
            dropped=tuple(dropped),
            warnings=tuple(f"citation [{n}] names no registered source" for n in dropped),
            text=out,
        )

    def persist_form(citations):
        return [f"{int(c.marker)}:{c.item_id}" for c in citations or []]

    def parse_persist_form(values):
        out = []
        for value in values or []:
            head, _, tail = str(value).partition(":")
            if not head.isdigit():
                continue  # legacy bare "item:<id>" — skipped, per the contract
            out.append(_Citation(marker=int(head), item_id=tail))
        return tuple(out)

    mod.MARKER_RE = marker_re
    mod.SourceRef = _SourceRef
    mod.Citation = _Citation
    mod.Resolution = _Resolution
    mod.register_sources = register_sources
    mod.strip_markers = strip_markers
    mod.parse_markers = parse_markers
    mod.resolve = resolve_markers
    mod.persist_form = persist_form
    mod.parse_persist_form = parse_persist_form
    return mod


@pytest.fixture(autouse=True)
def kcit(monkeypatch):
    """The citations module every lazy import in the wiring will see.

    Installed into `sys.modules` AND onto the package, because both import spellings are used
    (`from personalclaw.knowledge import citations`). Yields the REAL module untouched once it
    exists, so this file becomes an end-to-end test of the shipped parser without an edit.
    """
    import personalclaw.knowledge as pkg

    try:  # pragma: no cover — one branch or the other, depending on merge order
        from personalclaw.knowledge import citations as real

        return real
    except ImportError:
        fake = _build_fake()
        monkeypatch.setitem(sys.modules, _MODULE_PATH, fake)
        monkeypatch.setattr(pkg, "citations", fake, raising=False)
        return fake


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """`PERSONALCLAW_HOME` so the knowledge store resolves inside tmp. Patching `config_dir`
    alone would miss the modules that bound it at import."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PERSONALCLAW_HOME", str(home))
    return home


def _knob(monkeypatch, *, required: bool) -> None:
    """Patch the config object the check reads, rather than writing a config file and hoping
    the loader picks it up — an unread file would make these assertions pass either way."""
    from personalclaw.config.loader import AppConfig, KnowledgeConfig

    class _Cfg:
        knowledge = KnowledgeConfig(require_citations=required)

    monkeypatch.setattr(AppConfig, "load", staticmethod(lambda: _Cfg()))


ITEMS = [
    {"item_id": "k-1", "title": "Cold start", "content": "p50 held at 40ms."},
    {"item_id": "k-2", "title": "p99", "content": "As shown in [1], the p99 rose to 900ms."},
    {"item_id": "k-3", "title": "Rollout", "content": "Shipped on the 14th."},
]


# ── the headline claim: an output that cites nothing is refused ──


def test_an_output_that_cites_nothing_is_refused(monkeypatch):
    """The atom's whole point. A synthesis whose prose names no source is indistinguishable
    from a confident guess once it is being retrieved as fact."""
    _knob(monkeypatch, required=True)
    check = sem.check_persist(
        kind="insight",
        title="Latency review",
        content="The p99 regressed and the rollout is the cause.",
        citations=[],
        marker_citations=[],
    )
    assert not check.ok
    assert "cited nothing" in check.error
    assert "[n]" in check.error and "unsourced" in check.error
    # And it still tells the caller WHICH item it would have been, so a retry knows whether it
    # is creating or updating.
    assert check.logical_key and check.content_hash


def test_the_same_output_passes_when_it_says_it_is_unsourced(monkeypatch):
    """`unsourced: true` is the escape hatch, and it stays explicit in both modes."""
    _knob(monkeypatch, required=True)
    assert sem.check_persist(
        kind="insight",
        title="Latency review",
        content="The p99 regressed and the rollout is the cause.",
        citations=[],
        marker_citations=[],
        unsourced=True,
    ).ok


def test_storing_the_whole_retrieved_set_no_longer_satisfies_the_rule(monkeypatch):
    """What the template used to do: `citations = every item I recalled`. Non-empty, so the old
    presence check passed — while "which source supports this sentence" stayed unanswerable."""
    _knob(monkeypatch, required=True)
    whole_set = ["1:k-1", "2:k-2", "3:k-3"]
    refused = sem.check_persist(
        kind="insight",
        title="Latency review",
        content="The p99 regressed.",
        citations=whole_set,
        marker_citations=[],
    )
    assert not refused.ok and "cited nothing" in refused.error
    # The contrast that names what changed: the SAME non-empty list, with no marker pass having
    # run, is still accepted — that is the legacy/manual path, and it is the only one left.
    assert sem.check_persist(
        kind="insight",
        title="Latency review",
        content="The p99 regressed.",
        citations=whole_set,
    ).ok


def test_one_resolved_marker_is_enough(kcit, monkeypatch):
    """The bar is attributability, not volume: one sentence traceable to one source clears it."""
    _knob(monkeypatch, required=True)
    assert sem.check_persist(
        kind="insight",
        title="Latency review",
        content="The p99 rose to 900ms [2].",
        citations=["2:k-2"],
        marker_citations=[kcit.Citation(marker=2, item_id="k-2")],
    ).ok


@pytest.mark.parametrize("kind", sorted(sem.SYNTHESIZED_KINDS))
def test_every_synthesized_kind_is_covered(kind, monkeypatch):
    """`insight`, `report`, `overview` — the requirement is per-kind, not per-template."""
    _knob(monkeypatch, required=True)
    assert not sem.check_persist(
        kind=kind, title="T", content="c", citations=["1:k-1"], marker_citations=[]
    ).ok


def test_an_observed_kind_is_untouched(monkeypatch):
    """A `fact` is not a synthesis, and a marker pass over one must not start gating it."""
    _knob(monkeypatch, required=True)
    assert sem.check_persist(kind="fact", title="T", content="c", marker_citations=[]).ok


# ── the knob is live ──


def test_the_require_citations_knob_is_read(monkeypatch):
    """It shipped with a default, a label and a PATCH allowlist entry, and no readers."""
    _knob(monkeypatch, required=True)
    assert sem.citations_required() is True
    _knob(monkeypatch, required=False)
    assert sem.citations_required() is False


def test_the_knob_fails_to_on_when_config_is_unreadable(monkeypatch):
    """A control that keeps unsourced synthesis out of the store must not be relaxed by an
    unreadable config file."""
    from personalclaw.config.loader import AppConfig

    def _boom():
        raise OSError("config unreadable")

    monkeypatch.setattr(AppConfig, "load", staticmethod(_boom))
    assert sem.citations_required() is True


def test_the_knob_off_falls_back_to_the_presence_check(monkeypatch):
    """Off means today's rule, not no rule: any citation evidence counts, and an item with none
    at all is still refused. `unsourced: true` remains the way to say "I have nothing"."""
    _knob(monkeypatch, required=False)
    assert sem.check_persist(
        kind="insight", title="T", content="c", citations=["1:k-1"], marker_citations=[]
    ).ok
    lax = sem.check_persist(
        kind="insight", title="T", content="c", citations=[], marker_citations=[]
    )
    assert not lax.ok and "needs `citations`" in lax.error


def test_no_existing_caller_changes_behaviour(monkeypatch):
    """`marker_citations` defaults to None — "no marker pass ran", which is a different state
    from "one ran and resolved nothing" and must not collapse into it."""
    _knob(monkeypatch, required=True)
    assert sem.check_persist(kind="insight", title="Why", content="c", citations=["t-1"]).ok
    assert not sem.check_persist(kind="insight", title="Why", content="c").ok


# ── the numbering: one set of markers, two consumers ──


def test_fenced_sources_strips_an_items_own_markers_before_numbering():
    """Item 2 is itself a synthesis carrying `[1]`. Left in, that inherited marker reads as a
    citation of THIS turn's source 1 — a marker resolving to the wrong item is worse than none,
    because it looks answerable."""
    out = resolve("{{inputs.k | fenced_sources}}", BindingContext(inputs={"k": ITEMS}))
    assert re.findall(r"\[(\d+)\]", out) == ["1", "2", "3"]
    assert "As shown in , the p99 rose" in out  # the body survives; only the marker went


def test_source_refs_mirrors_the_numbering_the_model_reads():
    """The whole seam: `[3]` in the prompt and ref 3 in the persist step must name one item."""
    fenced = resolve("{{inputs.k | fenced_sources}}", BindingContext(inputs={"k": ITEMS}))
    refs = resolve("{{inputs.k | source_refs}}", BindingContext(inputs={"k": ITEMS}))
    assert [r["marker"] for r in refs] == [int(n) for n in re.findall(r"\[(\d+)\]", fenced)]
    assert [r["item_id"] for r in refs] == ["k-1", "k-2", "k-3"]
    assert set(refs[0]) == {"marker", "item_id", "chunk_index", "excerpt"}
    assert json.dumps(refs)  # crosses into an action config as JSON, so it must serialize


def test_source_refs_suppresses_the_default_sibling_view():
    """`fenced_sources` opts out of the bounded view. If `source_refs` did not, the two pipes
    would enumerate different lists and every marker past the bound would name the wrong item."""
    outputs = [{"findings": [{"item_id": f"k-{n}", "content": "x"} for n in range(60)]}]
    refs = resolve(
        "{{siblings.main.output | source_refs}}",
        BindingContext(sibling_outputs={"main": outputs}),
    )
    assert [r["marker"] for r in refs] == list(range(1, 61))


def test_an_empty_retrieval_registers_no_sources():
    assert resolve("{{inputs.k | source_refs}}", BindingContext(inputs={"k": []})) == []


# ── the template ──


def _store_node_config() -> dict:
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.models import Node, walk

    spec = read_template("knowledge-synthesis")
    root = spec.root if isinstance(spec.root, Node) else Node.from_dict(spec.root)
    for _p, node in walk(root):
        cfg = node.config or {}
        if cfg.get("provider") == "knowledge-persist":
            return dict(cfg.get("with") or {})
    raise AssertionError("the synthesis template has no knowledge-persist node")


def test_the_template_no_longer_stores_the_whole_retrieved_set():
    """It bound `citations` to every recalled item id, which records what was retrieved and can
    never say which source supports which sentence."""
    with_cfg = _store_node_config()
    assert "citations" not in with_cfg
    assert with_cfg["citation_sources"] == "{{nodes.recall.output.items | source_refs}}"


def test_the_templates_two_halves_number_the_same_set():
    """The prompt says "cite as [n]" over `fenced_sources`; the persist step resolves against
    `source_refs`. Both read `nodes.recall.output.items`, so `[n]` means one thing."""
    from personalclaw.workflows.bundled_defs import read_template
    from personalclaw.workflows.models import Node, walk

    spec = read_template("knowledge-synthesis")
    root = spec.root if isinstance(spec.root, Node) else Node.from_dict(spec.root)
    prompts = " ".join(str((n.config or {}).get("prompt", "") or "") for _p, n in walk(root))
    assert "nodes.recall.output.items | fenced_sources" in prompts
    assert "cite as [n]" in prompts
    ctx = BindingContext(node_outputs={"recall": {"items": ITEMS}})
    refs = resolve(_store_node_config()["citation_sources"], ctx)
    assert [r["item_id"] for r in refs] == ["k-1", "k-2", "k-3"]


# ── the provider derives citations by parsing ──


def test_the_provider_derives_citations_from_the_markers():
    """Not from the set it was handed: only markers that appear in the prose become citations."""
    refs = resolve("{{inputs.k | source_refs}}", BindingContext(inputs={"k": ITEMS}))
    cited = _resolve_citations(
        {"citation_sources": refs},
        body="The p99 rose to 900ms [2]. The rollout shipped on the 14th [3].",
        summary="",
    )
    assert [int(c.marker) for c in cited.records or []] == [2, 3]
    assert cited.stored == ["2:k-2", "3:k-3"]
    assert cited.warnings == []


def test_a_dangling_marker_is_stripped_from_what_gets_stored():
    """A `[9]` in a stored article promises provenance the store cannot keep, and a reader has
    no way to tell it from a good one."""
    refs = resolve("{{inputs.k | source_refs}}", BindingContext(inputs={"k": ITEMS}))
    cited = _resolve_citations(
        {"citation_sources": refs},
        body="The p99 rose [2]. Costs fell [9].",
        summary="",
    )
    assert "[9]" not in cited.content
    assert "[2]" in cited.content
    assert cited.stored == ["2:k-2"]
    assert cited.warnings and "[9]" in cited.warnings[0]


def test_a_summary_that_cites_is_resolved_too():
    """The summary is the field retrieval shows first, so a dangling marker there is the one a
    reader actually hits."""
    refs = resolve("{{inputs.k | source_refs}}", BindingContext(inputs={"k": ITEMS}))
    cited = _resolve_citations(
        {"citation_sources": refs},
        body="Nothing cited here.",
        summary="p99 regressed [1] and costs fell [8].",
    )
    assert "[8]" not in cited.summary and "[1]" in cited.summary
    assert [int(c.marker) for c in cited.records or []] == [1]


def test_the_legacy_path_is_left_alone():
    """A hand-written `citations` list with no `citation_sources`: taken at face value, no pass
    ran, so `records` stays None and the check keeps its presence rule."""
    cited = _resolve_citations({"citations": ["notebook p14"]}, body="text [4]", summary="")
    assert cited.records is None
    assert cited.stored == ["notebook p14"]
    assert cited.content == "text [4]"  # nothing parsed, so nothing rewritten


def test_a_ref_that_cannot_name_a_source_is_dropped():
    """A marker resolving to a blank item id would read as a real citation on retrieval."""
    refs = _coerce_source_refs(
        [
            {"marker": 1, "item_id": "k-1"},
            {"marker": 2, "item_id": "   "},
            {"marker": 0, "item_id": "k-3"},
            {"marker": "x", "item_id": "k-4"},
        ]
    )
    assert [(r.marker, r.item_id) for r in refs] == [(1, "k-1")]


def test_per_marker_records_are_offered_to_the_store(kcit):
    """`set_item_citations` is the citations module's surface. Guarded, because an
    `AttributeError` here would fail a write whose row already landed correctly."""
    calls = []

    class _Store:
        def set_item_citations(self, item_id, citations):
            calls.append((item_id, list(citations)))

    records = [kcit.Citation(marker=1, item_id="k-1")]
    _write_item_citations(_Store(), "abc123", records)
    assert calls == [("abc123", records)]

    class _Older:
        """A store that predates the per-marker table."""

    _write_item_citations(_Older(), "abc123", records)  # must not raise

    class _Broken:
        def set_item_citations(self, item_id, citations):
            raise RuntimeError("no such table")

    _write_item_citations(_Broken(), "abc123", records)  # must not raise either


# ── end to end, through the real provider ──


async def _persist(cfg: dict) -> tuple[bool, dict, str]:
    result = await KnowledgePersistActionProvider().execute(
        cfg, ActionContext(event="workflow_node", payload={})
    )
    payload = json.loads(result.stdout) if result.stdout else {}
    return result.success, payload, result.error


def _row(item_id: str) -> tuple[str, str, dict]:
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path

    store = KnowledgeStore(db_path=str(knowledge_db_path()))
    rows = list(
        store.db.execute(
            "SELECT content, summary, file_metadata FROM items WHERE id = ?", (item_id,)
        )
    )
    assert rows, f"item {item_id} not found"
    return str(rows[0][0]), str(rows[0][1]), json.loads(rows[0][2] or "{}")


@pytest.mark.asyncio
async def test_a_write_stores_what_the_prose_cited(isolated_home, monkeypatch):
    _knob(monkeypatch, required=True)
    refs = resolve("{{inputs.k | source_refs}}", BindingContext(inputs={"k": ITEMS}))
    ok, payload, error = await _persist(
        {
            "kind": "insight",
            "title": "Latency review",
            "content": "The p99 rose to 900ms [2], while p50 held [1]. Costs fell [9].",
            "summary": "p99 regressed [2].",
            "citation_sources": refs,
        }
    )
    assert ok, error
    content, summary, metadata = _row(payload["item_id"])
    # Only what resolved, and in marker order — not the three items that were retrieved.
    assert metadata["citations"] == ["1:k-1", "2:k-2"]
    # The write-back: the dangling marker never reaches a reader, in either field.
    assert "[9]" not in content and "[2]" in content
    assert "[2]" in summary
    assert payload["citation_warnings"] and "[9]" in payload["citation_warnings"][0]


@pytest.mark.asyncio
async def test_a_write_that_cites_nothing_is_refused_end_to_end(isolated_home, monkeypatch):
    """The retrieved set is present and the prose cites none of it — the exact shape the old
    template produced on every run."""
    _knob(monkeypatch, required=True)
    refs = resolve("{{inputs.k | source_refs}}", BindingContext(inputs={"k": ITEMS}))
    ok, _payload, error = await _persist(
        {
            "kind": "insight",
            "title": "Latency review",
            "content": "The p99 regressed and the rollout is the cause.",
            "citation_sources": refs,
        }
    )
    assert not ok
    assert "cited nothing" in error and "[n]" in error


@pytest.mark.asyncio
async def test_the_unsourced_opt_out_still_writes(isolated_home, monkeypatch):
    _knob(monkeypatch, required=True)
    refs = resolve("{{inputs.k | source_refs}}", BindingContext(inputs={"k": ITEMS}))
    ok, payload, error = await _persist(
        {
            "kind": "insight",
            "title": "Hunch",
            "content": "Nothing here is attributable and I am saying so.",
            "citation_sources": refs,
            "unsourced": True,
        }
    )
    assert ok, error
    _content, _summary, metadata = _row(payload["item_id"])
    assert metadata["citations"] == []  # honest: it cited nothing and claims nothing


@pytest.mark.asyncio
async def test_a_rewrite_cannot_keep_the_previous_writes_citations(isolated_home, monkeypatch):
    """Same logical key, new body that cites less. A stale list left standing would attribute
    the new text to sources it never named."""
    _knob(monkeypatch, required=True)
    refs = resolve("{{inputs.k | source_refs}}", BindingContext(inputs={"k": ITEMS}))
    ok, first, error = await _persist(
        {
            "kind": "insight",
            "title": "Latency review",
            "content": "p50 held [1]. p99 rose [2].",
            "citation_sources": refs,
        }
    )
    assert ok, error
    assert _row(first["item_id"])[2]["citations"] == ["1:k-1", "2:k-2"]

    ok, second, error = await _persist(
        {
            "kind": "insight",
            "title": "Latency review",
            "content": "Only p99 matters [2].",
            "citation_sources": refs,
        }
    )
    assert ok, error
    assert second["item_id"] == first["item_id"]
    assert _row(second["item_id"])[2]["citations"] == ["2:k-2"]
