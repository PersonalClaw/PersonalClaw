"""WATCHED-SOURCES WS-6 — the fetch-and-slice ingestion primitive (§5, SC#9).

Four claims, and each is asserted in the form that can actually fail:

* **Determinism** — detection is run TWICE over one input and the results compared. An
  ordering regression (a set iteration, an unsorted candidate list) is invisible to a
  single-run assertion, which is why the doubled run is the test.
* **Zero network on re-ingest** — the second fetch is given a seam that RAISES. Asserting
  "the fetcher was called once" would pass a cache that fetched and discarded; asserting
  the fetch is never REACHED is the only version of the claim that holds.
* **Slices are rows on ONE item** — asserted by counting items before and after, not just
  by finding the rows. The repo removed chunk-items deliberately and a test that only
  looks for slice rows would not notice child rows appearing beside them.
* **Thresholds are honoured** — each constant is monkeypatched and the OUTCOME is
  re-measured, so a constant the code does not actually read cannot pass.

No test opens a socket: ``fetch_source``'s ``fetch_fn`` is the primitive's only byte seam.

``pdfplumber`` and ``reportlab`` are both CORE dependencies of this project
(``pyproject.toml``; ``test_knowledge.py::test_pdf_reader_dependency_present`` and
``test_documents.py::test_pdf_is_unconditionally_available`` assert as much), so the PDF
fixtures here are real generated PDFs rather than stubs. The cascade is still tested
WITHOUT any PDF as well — ``slice_structure`` is a pure function of a plain dataclass, so
the layout-free tests below would keep working on an install where the PDF path degraded.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from personalclaw.knowledge import slicing as sl
from personalclaw.knowledge.pipeline import ensure_nodes_registered
from personalclaw.knowledge.pipeline.graphs import graph_for
from personalclaw.knowledge.pipeline.runner import ingest_item
from personalclaw.knowledge.readers import PdfLine, PdfStructure
from personalclaw.knowledge.store import KnowledgeStore


def _run(coro):
    return asyncio.run(coro)


def _count_items(store) -> int:
    return int(store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Every test writes under a throwaway home — the source cache lives in the real
    knowledge files dir, so an un-isolated run would leave cached PDFs in ~/.personalclaw."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "personalclaw.config.loader.config_dir", lambda: tmp_path / "home", raising=False
    )
    yield


@pytest.fixture
def store(tmp_path):
    return KnowledgeStore(str(tmp_path / "knowledge.db"))


# ── fixtures: a real, multi-page, arXiv-shaped PDF ────────────────────────────


def _filler(word: str, count: int) -> str:
    return " ".join(f"{word}{i}" for i in range(count))


# Each reference is ONE markdown line: `document_from_markdown` treats a wrapped
# continuation line as its own paragraph, and the writer can then lay it out away from
# its marker — which garbles the fixture, not the splitter. (`_split_entries` splits on
# marker POSITION precisely so a real PDF's wrapped entries still parse.)
#: The five bibliography entries. Each is one markdown PARAGRAPH built by implicit string
#: concatenation, because it has to reach the writer unwrapped (see above) while this file
#: stays inside the 100-column lint.
REFERENCE_ENTRIES = (
    "[1] Radford, A. et al. Learning Transferable Visual Models From Natural"
    " Language Supervision. arXiv:2103.00020, 2021.",
    "[2] Smith, J. and Doe, A. A Systematic Study Of Determinism In Extraction"
    " Pipelines. 2019. doi:10.1145/3292500.3330701",
    "[3] Nguyen, T. A Systematic Study on Determinism in Extraction Pipeline."
    " Journal of Things, 2020.",
    "[4] Okonkwo, C. Structural Cues For Section Detection Without Any Models At"
    " All. Proceedings of Things, 2022.",
    "[5] Alvarez, M. Untitled. 2018.",
    # Carries BOTH an arXiv id and a DOI — the only entry shape that makes the cascade's
    # ORDER observable. Without it, "arxiv before doi" is a claim no test can falsify.
    "[6] Vaswani, A. et al. Attention Is All You Need Again. arXiv:1706.03762,"
    " 2017. doi:10.5555/3295222.3295349",
)

PAPER_MARKDOWN = f"""# Deterministic Slicing of Scientific PDFs

## Abstract

{_filler("abs", 110)}

## 1 Introduction

{_filler("intro", 560)}

## 2 Method

{_filler("method", 760)}

## 3 Results

{_filler("results", 760)}

## 4 Discussion

{_filler("disc", 560)}

## 5 Conclusion

{_filler("concl", 150)}

## References

{chr(10).join(chr(10).join(("", entry)) for entry in REFERENCE_ENTRIES)}
"""


def _paper_bytes() -> bytes:
    from personalclaw.documents import get_writer
    from personalclaw.documents.from_markup import document_from_markdown

    return get_writer("pdf")(document_from_markdown(PAPER_MARKDOWN))


@pytest.fixture(scope="module")
def paper_pdf_bytes() -> bytes:
    """Generated once — the writer is deterministic for a fixed model, and the whole
    suite depends on the same bytes producing the same sections."""
    return _paper_bytes()


@pytest.fixture
def paper_pdf(tmp_path, paper_pdf_bytes):
    path = tmp_path / "paper.pdf"
    path.write_bytes(paper_pdf_bytes)
    return path


# ── §5 source sniffing ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,kind,identifier",
    [
        ("arXiv:2103.00020", sl.SOURCE_ARXIV, "2103.00020"),
        ("arXiv:2103.00020v7", sl.SOURCE_ARXIV, "2103.00020"),
        ("https://arxiv.org/abs/2103.00020v2", sl.SOURCE_ARXIV, "2103.00020"),
        ("https://arxiv.org/pdf/2103.00020", sl.SOURCE_ARXIV, "2103.00020"),
        ("2103.00020", sl.SOURCE_ARXIV, "2103.00020"),
        ("doi:10.1145/3292500.3330701", sl.SOURCE_DOI, "10.1145/3292500.3330701"),
        ("https://doi.org/10.1038/s41586-021-03819-2", sl.SOURCE_DOI, "10.1038/s41586-021-03819-2"),
        ("https://example.test/report.pdf", sl.SOURCE_PDF, ""),
        ("https://example.test/blog/post", sl.SOURCE_URL, ""),
    ],
)
def test_sniffing_normalizes_each_reference_shape(raw, kind, identifier):
    ref = sl.sniff_source(raw)
    assert ref is not None and ref.kind == kind and ref.identifier == identifier


def test_an_arxiv_id_is_version_insensitive_and_resolves_to_one_url():
    """§5: version-insensitive. Two versions of one paper must reach the same cache key,
    or the cache misses on every citation that carries a different vN suffix."""
    a = sl.sniff_source("arXiv:2103.00020v1")
    b = sl.sniff_source("arXiv:2103.00020v9")
    assert a is not None and b is not None and a.url == b.url


@pytest.mark.parametrize("raw", ["", "   ", "not a reference", "ftp://x.test/a.pdf", "1234.56789"])
def test_a_non_reference_sniffs_to_none_rather_than_raising(raw):
    """A malformed paste must be a 'no', not a traceback in an ingest node. `1234.56789`
    is the important row: a bare arXiv-SHAPED number with no arXiv context is a page range
    or a version string far more often than it is a paper."""
    if raw == "1234.56789":
        # The bare-id form IS accepted when the whole string is nothing but an id (that is
        # the documented affordance) — this row pins the shape that must NOT be inferred
        # from a number appearing inside other text.
        assert sl.sniff_source("see figure 1234.56789 for detail") is None
        return
    assert sl.sniff_source(raw) is None


# ── §5 cascaded section detection ─────────────────────────────────────────────


def test_the_font_tier_detects_a_generated_papers_sections(paper_pdf):
    result = sl.slice_document(file_path=str(paper_pdf))
    roles = [s.role for s in result.sections]
    for expected in (
        sl.ROLE_ABSTRACT,
        sl.ROLE_INTRODUCTION,
        sl.ROLE_METHOD,
        sl.ROLE_RESULTS,
        sl.ROLE_DISCUSSION,
        sl.ROLE_CONCLUSION,
        sl.ROLE_REFERENCES,
    ):
        assert expected in roles, f"{expected} not detected; got {roles}"
    assert result.strategies == (sl.STRATEGY_FONT,)
    assert result.page_count > KEEP_SPAN, "fixture must exceed the kept-pages floor"


#: The fixture has to be longer than first-3 + last-2 or the floor covers every page and
#: `meta`/`body` become indistinguishable from `full` — a fixture that cannot fail.
KEEP_SPAN = sl.KEEP_FIRST_PAGES + sl.KEEP_LAST_PAGES


def test_detection_is_deterministic_across_runs(paper_pdf):
    """The claim §5 rests on. Two runs over identical bytes must be identical — including
    section ORDER, which a set-backed candidate collection would scramble."""
    first = sl.slice_document(file_path=str(paper_pdf))
    second = sl.slice_document(file_path=str(paper_pdf))
    assert first.sections == second.sections
    assert first.slices == second.slices
    assert first.references == second.references
    assert [s.start for s in first.sections] == sorted(s.start for s in first.sections)


#: A tiny self-contained detection run, printed as a stable digest. Executed in a CHILD
#: process so the parent's PYTHONHASHSEED can be varied — an in-process double run cannot
#: catch hash-order dependence, because string hashing is fixed for a process's lifetime.
_HASH_SEED_PROBE = """
import hashlib, sys
from personalclaw.knowledge import slicing as sl
text = (
    "Abstract\\nclaim\\n1 Introduction\\nprior\\n2 Method\\nhow\\n3 Results\\nwhat\\n"
    "4 Discussion\\nso\\n5 Conclusion\\ndone\\nReferences\\n"
    "[1] A. Someone. A Paper About Several Things. arXiv:2103.00020, 2021.\\n"
    "[2] B. Other. Another Paper About Other Things. doi:10.1/x\\n"
)
r = sl.slice_structure(sl.structure_from_text(text))
blob = repr((r.sections, r.slices, r.references)).encode()
sys.stdout.write(hashlib.sha256(blob).hexdigest())
"""


def test_detection_is_deterministic_across_processes_with_different_hash_seeds(tmp_path):
    """The determinism claim with real teeth.

    Two runs inside ONE process share a string-hash seed, so a detector that iterated a
    set of titles would produce the SAME order twice and the in-process test above would
    pass a genuinely non-deterministic implementation. Varying ``PYTHONHASHSEED`` across
    child processes is the only way to observe that class of defect.
    """
    import os
    import subprocess
    import sys

    digests = []
    for seed in ("0", "1", "524287"):
        env = {
            **os.environ,
            "PYTHONHASHSEED": seed,
            "PYTHONPATH": str(_SRC_ROOT),
            "PERSONALCLAW_HOME": str(tmp_path / f"home-{seed}"),
        }
        done = subprocess.run(
            [sys.executable, "-c", _HASH_SEED_PROBE],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        assert done.returncode == 0, done.stderr
        digests.append(done.stdout.strip())
    assert len(set(digests)) == 1, f"detection varied with the hash seed: {digests}"
    assert digests[0], "the probe produced no digest — it did not run"


_SRC_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent / "src"


def test_the_outline_tier_outranks_the_font_tier_at_the_same_offset():
    """Tiers 1 and 2 are UNIONED, and a heading both tiers find is ONE section attributed
    to the outline — the document's own declaration beats a typographic inference."""
    structure = PdfStructure(
        pages=("Abstract\nwe did things\n1 Introduction\nprior work",),
        lines=(
            PdfLine(page=0, text="Abstract", size=14.0, char_count=8),
            PdfLine(page=0, text="we did things", size=10.0, char_count=13),
            PdfLine(page=0, text="1 Introduction", size=14.0, char_count=14),
            PdfLine(page=0, text="prior work", size=10.0, char_count=10),
        ),
        outline=("Abstract", "1 Introduction"),
    )
    sections = sl.detect_sections(structure)
    assert [s.title for s in sections] == ["Abstract", "1 Introduction"]
    assert {s.strategy for s in sections} == {sl.STRATEGY_OUTLINE}


def test_the_unioned_tiers_are_ordered_by_offset_not_by_tier():
    """The candidate sort is load-bearing, and only a structure where the tiers INTERLEAVE
    can show it.

    Here the outline names two sections and the font tier finds a third BETWEEN them, so
    the concatenation order (all outline hits, then all font hits) is not the document
    order. Dropping the sort produces sections out of order with duplicate offsets — and
    every span is then computed from the wrong neighbour, silently mis-cutting every slice.
    """
    page = "Abstract\nclaim\n2 Method\nhow we did it\nConclusion\nit worked\n"
    structure = PdfStructure(
        pages=(page,),
        lines=(
            PdfLine(page=0, text="Abstract", size=14.0, char_count=8),
            PdfLine(page=0, text="claim", size=10.0, char_count=5),
            PdfLine(page=0, text="2 Method", size=14.0, char_count=8),
            PdfLine(page=0, text="how we did it", size=10.0, char_count=13),
            PdfLine(page=0, text="Conclusion", size=14.0, char_count=10),
            PdfLine(page=0, text="it worked", size=10.0, char_count=9),
        ),
        outline=("Abstract", "Conclusion"),
    )
    sections = sl.detect_sections(structure)
    assert [s.title for s in sections] == ["Abstract", "2 Method", "Conclusion"]
    assert [s.strategy for s in sections] == [
        sl.STRATEGY_OUTLINE,
        sl.STRATEGY_FONT,
        sl.STRATEGY_OUTLINE,
    ]
    starts = [s.start for s in sections]
    assert starts == sorted(starts) and len(set(starts)) == 3
    # Every span must hand off to the next heading — an out-of-order candidate list makes
    # these ends point at the wrong neighbour.
    assert [s.end for s in sections[:-1]] == starts[1:]


def test_the_header_tier_fires_only_when_the_first_two_found_nothing():
    """A text document has no outline and no font sizes, so the fallback is the only tier
    with anything to say — and it proposes ONLY headings it can name, never a guess."""
    result = sl.slice_structure(
        sl.structure_from_text(
            "Abstract\nthe short version\nMethod\nhow we did it\nSome Random Line\nmore\n"
            "Conclusion\nit worked\nReferences\n[1] Someone. A Paper About Things. 2020."
        )
    )
    assert result.strategies == (sl.STRATEGY_HEADER,)
    assert [s.role for s in result.sections] == [
        sl.ROLE_ABSTRACT,
        sl.ROLE_METHOD,
        sl.ROLE_CONCLUSION,
        sl.ROLE_REFERENCES,
    ]
    assert "Some Random Line" not in [s.title for s in result.sections]


def test_the_body_font_size_is_char_weighted_not_line_counted():
    """Line-counted, a paper with many short headings and few long body lines elects a
    HEADING size as 'body' and then detects no headings at all."""
    lines = (
        PdfLine(page=0, text="H1", size=14.0, char_count=2),
        PdfLine(page=0, text="H2", size=14.0, char_count=2),
        PdfLine(page=0, text="H3", size=14.0, char_count=2),
        PdfLine(page=0, text="a long paragraph of body text " * 4, size=10.0, char_count=480),
    )
    assert sl.body_font_size(lines) == 10.0


def test_a_role_cue_resolves_references_before_a_looser_pattern():
    """Ordering claim: `references` is resolved early because it is the one section whose
    text must be STRIPPED, and mis-roling it leaks citations into `body`."""
    assert sl.role_for("References") == sl.ROLE_REFERENCES
    assert sl.role_for("Bibliography") == sl.ROLE_REFERENCES
    assert sl.role_for("6 Related Work") == sl.ROLE_DISCUSSION
    assert sl.role_for("4 Analysis") == sl.ROLE_RESULTS
    assert sl.role_for("Acknowledgements") == sl.ROLE_OTHER


def test_the_heading_size_ratio_is_the_constant_the_code_reads(monkeypatch):
    """Raising the ratio above the fixture's heading/body ratio must find NO font
    headings — proving the threshold in the constants block is the live one."""
    structure = PdfStructure(
        pages=("Abstract\nbody body body\n",),
        lines=(
            PdfLine(page=0, text="Abstract", size=11.0, char_count=8),
            PdfLine(page=0, text="body body body", size=10.0, char_count=14),
        ),
    )
    assert [s.title for s in sl.detect_sections(structure)] == ["Abstract"]
    monkeypatch.setattr(sl, "HEADING_SIZE_RATIO", 3.0)
    # No font heading survives; the header tier then names the one line it recognizes.
    assert {s.strategy for s in sl.detect_sections(structure)} == {sl.STRATEGY_HEADER}


def test_a_long_large_font_run_is_not_a_heading(monkeypatch):
    monkeypatch.setattr(sl, "HEADING_MAX_CHARS", 10)
    structure = PdfStructure(
        pages=("A Very Long Pull Quote Set In Display Type\nbody body\n",),
        lines=(
            PdfLine(
                page=0, text="A Very Long Pull Quote Set In Display Type", size=18.0, char_count=41
            ),
            PdfLine(page=0, text="body body", size=10.0, char_count=9),
        ),
    )
    assert sl.detect_sections(structure) == ()


# ── §5 purpose-cut slices ─────────────────────────────────────────────────────


def test_brief_body_and_meta_are_cut_to_their_roles(paper_pdf):
    result = sl.slice_document(file_path=str(paper_pdf))
    brief = result.slice_for(sl.SLICE_BRIEF)
    body = result.slice_for(sl.SLICE_BODY)
    meta = result.slice_for(sl.SLICE_META)
    assert brief and body and meta

    # brief: the claim, not the machinery.
    assert "abs0" in brief.text
    assert "method0" not in brief.text and "results0" not in brief.text
    # body: the machinery, and never the bibliography.
    assert "method0" in body.text and "results0" in body.text
    assert "arXiv:2103.00020" not in body.text, "references must be stripped from body"
    # meta: the front matter only.
    assert "Deterministic Slicing" in meta.text
    assert len(meta.text) < len(result.full_text)


def test_brief_is_clamped_into_the_fraction_band(paper_pdf):
    result = sl.slice_document(file_path=str(paper_pdf))
    brief = result.slice_for(sl.SLICE_BRIEF)
    assert brief is not None
    assert sl.BRIEF_MIN_FRACTION <= brief.fraction <= sl.BRIEF_MAX_FRACTION, brief.fraction


def test_the_brief_ceiling_is_the_constant_the_code_reads(monkeypatch, paper_pdf):
    monkeypatch.setattr(sl, "BRIEF_MAX_FRACTION", 0.02)
    brief = sl.slice_document(file_path=str(paper_pdf)).slice_for(sl.SLICE_BRIEF)
    assert brief is not None and brief.fraction <= 0.02


def test_the_brief_floor_tops_up_a_slice_too_small_to_be_a_brief(monkeypatch):
    """No abstract/intro/conclusion heading is detectable here, so the role-selected brief
    is EMPTY; the floor is what keeps it from being useless."""
    text = "Method\n" + ("m " * 4000) + "\nResults\n" + ("r " * 4000)
    monkeypatch.setattr(sl, "BRIEF_MIN_FRACTION", 0.20)
    monkeypatch.setattr(sl, "BRIEF_MAX_FRACTION", 0.50)
    brief = sl.slice_structure(sl.structure_from_text(text)).slice_for(sl.SLICE_BRIEF)
    assert brief is not None and brief.fraction >= 0.20


def test_the_kept_pages_floor_retains_front_matter_detection_did_not_select(paper_pdf):
    """§5's floor. The abstract is a BRIEF role, so nothing in `body`'s role list selects
    it; it is present only because the first-3-pages floor keeps it. Zeroing the floor is
    the falsification, and it is asserted here rather than described."""
    with_floor = sl.slice_document(file_path=str(paper_pdf)).slice_for(sl.SLICE_BODY)
    assert with_floor is not None and "abs0" in with_floor.text

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sl, "KEEP_FIRST_PAGES", 0)
        mp.setattr(sl, "KEEP_LAST_PAGES", 0)
        without = sl.slice_document(file_path=str(paper_pdf)).slice_for(sl.SLICE_BODY)
    assert without is not None
    assert "abs0" not in without.text
    assert len(without.text) < len(with_floor.text)


def test_the_kept_pages_floor_never_reaches_into_the_bibliography(paper_pdf):
    """'pre-bibliography' is load-bearing: the LAST pages of a paper are its references,
    so a naive last-2-pages floor would re-import exactly what body strips."""
    result = sl.slice_document(file_path=str(paper_pdf))
    body = result.slice_for(sl.SLICE_BODY)
    assert body is not None
    assert result.bibliography_start < len(result.full_text)
    assert "doi:10.1145" not in body.text and "Radford" not in body.text


def test_the_full_slice_is_retrievable_but_never_a_persisted_row(paper_pdf):
    """§5 names four roles; `full` is the item's own content, so it is reachable without
    doubling every paper's storage with a byte-identical row."""
    result = sl.slice_document(file_path=str(paper_pdf))
    full = result.slice_for(sl.SLICE_FULL)
    assert full is not None and full.text == result.full_text
    assert sl.SLICE_FULL not in sl.PERSISTED_SLICES
    assert all(row["node_type"] != "slice:full" for row in sl.slice_rows(result))


def test_slices_never_emit_a_byte_of_the_document_twice(paper_pdf):
    """Slices are built from merged char RANGES for this reason: a body section
    overlapping a kept page would otherwise be concatenated twice — a doubled token bill
    for whoever reads the slice."""
    result = sl.slice_document(file_path=str(paper_pdf))
    body = result.slice_for(sl.SLICE_BODY)
    assert body is not None
    assert body.text.count("method0 ") == 1
    assert body.text.count("results0 ") == 1


# ── §5 deterministic reference extraction ─────────────────────────────────────


def test_the_reference_cascade_keys_each_entry_by_its_strongest_tier(paper_pdf):
    result = sl.slice_document(file_path=str(paper_pdf))
    keys = {r.key for r in result.references}
    tiers = {r.tier for r in result.references}
    assert tiers == {sl.TIER_ARXIV, sl.TIER_DOI, sl.TIER_TITLE, sl.TIER_AUTHOR_YEAR}
    assert "arXiv:2103.00020" in keys
    assert "doi:10.1145/3292500.3330701" in keys
    assert "author_year:alvarez:2018" in keys
    assert any(k.startswith("title:structural cues") for k in keys)


def test_an_entry_carrying_both_identifiers_is_keyed_by_the_stronger_one(paper_pdf):
    """The cascade's ORDER, asserted rather than described. Entry [6] carries an arXiv id
    AND a DOI; keying it by the DOI would mean the order in the code is not the order in
    the docstring — and it is the arXiv id this codebase can actually re-fetch."""
    result = sl.slice_document(file_path=str(paper_pdf))
    both = [r for r in result.references if "1706.03762" in r.raw]
    assert len(both) == 1
    assert both[0].tier == sl.TIER_ARXIV
    assert both[0].key == "arXiv:1706.03762"


def test_the_title_tier_merges_one_work_cited_twice_in_two_formats(paper_pdf):
    """Entries [2] and [3] are the same paper, one with a DOI and one without, and their
    titles differ in the MIDDLE as well as the tail ("Study of … Pipelines" vs "Study on …
    Pipeline") — so neither exact equality nor a prefix comparison would catch the merge.
    The fuzzy sliding window is the deterministic replacement for asking a model "same
    paper?"."""
    result = sl.slice_document(file_path=str(paper_pdf))
    matching = [r for r in result.references if "determinism in extraction" in r.title.lower()]
    assert len(matching) == 1, [r.key for r in result.references]
    assert matching[0].tier == sl.TIER_DOI, "the identified form must win the merge"


def test_the_title_match_ratio_is_the_constant_the_code_reads(monkeypatch, paper_pdf):
    """At ratio 1.0 the two formats no longer merge, so the same bibliography yields one
    MORE reference — the outcome, not the constant, is what is measured."""
    baseline = len(sl.slice_document(file_path=str(paper_pdf)).references)
    monkeypatch.setattr(sl, "TITLE_MATCH_RATIO", 1.0)
    assert len(sl.slice_document(file_path=str(paper_pdf)).references) == baseline + 1


def test_an_unidentifiable_entry_is_counted_not_given_an_invented_key():
    """A fabricated citation key is worse than an admitted gap: a later linking pass
    (KNOWLEDGE-SYNTHESIS) would treat it as a real work."""
    refs, unkeyed = sl.extract_references("References\n[1] " + ("x" * 40) + "\n", len(""))
    assert refs == () and unkeyed == 1


def test_references_are_extracted_but_never_linked(paper_pdf):
    """§5 stops at extraction — cross-item linking is KNOWLEDGE-SYNTHESIS's step, so a
    reference record must carry no item id and no resolved target."""
    metadata = sl.reference_metadata(sl.slice_document(file_path=str(paper_pdf)))
    assert metadata["references"]
    for record in metadata["references"]:
        assert set(record) == {"key", "tier", "title", "year"}


def test_a_document_with_no_bibliography_extracts_nothing_and_strips_nothing():
    result = sl.slice_structure(sl.structure_from_text("Abstract\nshort\nMethod\nhow"))
    assert result.references == ()
    assert result.bibliography_start == len(result.full_text)


# ── §5 sha256 source cache (SC#9's zero-network clause) ───────────────────────


class _Fetcher:
    """A scripted byte seam. Records every URL it is asked for, so 'no network' can be
    asserted as 'not reached' rather than inferred from a count."""

    def __init__(self, body: bytes):
        self.body = body
        self.urls: list[str] = []

    async def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        return self.body


async def _exploding_fetch(url: str) -> bytes:
    raise AssertionError(f"network reached for {url} — the sha256 cache did not serve it")


def test_a_fetched_source_is_cached_under_the_knowledge_files_dir(paper_pdf_bytes):
    from personalclaw.knowledge import knowledge_files_dir

    ref = sl.sniff_source("arXiv:2103.00020")
    assert ref is not None
    fetched = _run(sl.fetch_source(ref, fetch_fn=_Fetcher(paper_pdf_bytes)))
    assert fetched.from_cache is False
    assert fetched.path.is_file() and fetched.path.suffix == ".pdf"
    assert fetched.path.is_relative_to(knowledge_files_dir()), "§5: no new cache root"
    assert fetched.path.name == f"sha256-{fetched.sha256}.pdf"


def test_a_re_ingest_is_served_from_the_cache_with_zero_network(paper_pdf_bytes):
    """SC#9's clause, asserted the only way it can fail honestly: the second fetch's seam
    RAISES, so a cache that fetched-then-discarded reds instead of passing."""
    ref = sl.sniff_source("arXiv:2103.00020")
    assert ref is not None
    first = _run(sl.fetch_source(ref, fetch_fn=_Fetcher(paper_pdf_bytes)))
    second = _run(sl.fetch_source(ref, fetch_fn=_exploding_fetch))
    assert second.from_cache is True
    assert second.sha256 == first.sha256 and second.path == first.path


def test_the_cache_is_reachable_from_the_reference_not_only_the_content(paper_pdf_bytes):
    """Content-addressing alone cannot serve a re-ingest — the hash needs the bytes we are
    trying not to fetch. The ref→content pointer is what makes the zero-network path
    possible, so it must exist on disk beside the original."""
    ref = sl.sniff_source("https://arxiv.org/abs/2103.00020")
    assert ref is not None
    _run(sl.fetch_source(ref, fetch_fn=_Fetcher(paper_pdf_bytes)))
    pointers = list(sl.source_cache_dir().glob("ref-*.json"))
    assert len(pointers) == 1
    record = json.loads(pointers[0].read_text())
    assert record["sha256"] and record["suffix"] == ".pdf" and record["kind"] == sl.SOURCE_ARXIV


def test_two_references_to_identical_bytes_share_one_original(paper_pdf_bytes):
    """The originals are keyed by CONTENT, so a paper reachable by two URLs is stored once."""
    a = sl.sniff_source("https://arxiv.org/pdf/2103.00020")
    b = sl.sniff_source("https://example.test/mirror/paper.pdf")
    assert a is not None and b is not None
    first = _run(sl.fetch_source(a, fetch_fn=_Fetcher(paper_pdf_bytes)))
    second = _run(sl.fetch_source(b, fetch_fn=_Fetcher(paper_pdf_bytes)))
    assert first.path == second.path
    assert len(list(sl.source_cache_dir().glob("sha256-*"))) == 1


def test_a_pointer_whose_original_vanished_is_a_miss_not_a_dangling_path(paper_pdf_bytes):
    ref = sl.sniff_source("arXiv:2103.00020")
    assert ref is not None
    fetched = _run(sl.fetch_source(ref, fetch_fn=_Fetcher(paper_pdf_bytes)))
    fetched.path.unlink()
    assert sl.cached_source(ref) is None
    again = _run(sl.fetch_source(ref, fetch_fn=_Fetcher(paper_pdf_bytes)))
    assert again.from_cache is False and again.path.is_file()


def test_a_non_pdf_body_is_cached_without_claiming_to_be_a_pdf():
    """The raw-PDF sniff reads the CONTENT: a DOI that resolves to a publisher HTML page
    must not be stored as `.pdf` and then handed to the PDF reader."""
    ref = sl.sniff_source("doi:10.1145/3292500.3330701")
    assert ref is not None
    fetched = _run(sl.fetch_source(ref, fetch_fn=_Fetcher(b"<html><body>paywall</body></html>")))
    assert fetched.path.suffix == ".bin"
    assert sl.is_pdf_bytes(b"<html>") is False and sl.is_pdf_bytes(b"%PDF-1.7 ...") is True


def test_an_empty_response_is_refused_rather_than_cached_as_a_document():
    ref = sl.sniff_source("https://example.test/paper.pdf")
    assert ref is not None
    with pytest.raises(sl.SourceFetchError):
        _run(sl.fetch_source(ref, fetch_fn=_Fetcher(b"")))
    assert list(sl.source_cache_dir().glob("ref-*.json")) == []


def test_the_default_fetch_seam_is_net_fetch_under_the_source_policy(monkeypatch):
    """§8/SC#11: no socket outside `net.fetch`. Pinned by asserting the policy the default
    seam hands it — a hand-rolled client would not be audited or host-classified."""
    seen: dict = {}

    async def _fake_fetch(url, *, policy=None, **kw):
        seen["url"] = url
        seen["policy"] = policy

        class _R:
            status = 200
            body = b"%PDF-1.4 x"

        return _R()

    monkeypatch.setattr("personalclaw.net.client.fetch", _fake_fetch)
    ref = sl.sniff_source("arXiv:2103.00020")
    assert ref is not None
    _run(sl.fetch_source(ref))
    assert seen["url"] == "https://arxiv.org/pdf/2103.00020"
    assert seen["policy"].name == "source"


# ── SC#9 end to end: an arXiv PDF ingests ────────────────────────────────────


def test_an_arxiv_pdf_ingests_into_slice_rows_on_the_one_item(store, paper_pdf_bytes):
    """SC#9, whole. A bookmarked arXiv URL is fetched through the cache, sliced, and its
    references extracted — all on ONE item, with no chunk or child rows anywhere."""
    ensure_nodes_registered()
    fetcher = _Fetcher(paper_pdf_bytes)
    monkey = pytest.MonkeyPatch()
    monkey.setattr("personalclaw.knowledge.slicing._default_fetch", fetcher, raising=True)
    try:
        item_id = store.create_typed_item(
            item_type="bookmark", title="", url="https://arxiv.org/abs/2103.00020"
        )
        before = _count_items(store)
        status = _run(ingest_item(store, item_id))
    finally:
        monkey.undo()

    assert status == "done", store.get_item(item_id).get("processing_error")
    pool = store.get_extracted_contents(item_id)
    kinds = [row["node_type"] for row in pool]
    assert "slice:brief" in kinds and "slice:body" in kinds and "slice:meta" in kinds
    # ONE item — the slices are rows on it, not chunk-items beside it.
    assert _count_items(store) == before == 1
    assert all(row["item_id"] == item_id for row in pool)
    assert store.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0

    references = (store.get_item(item_id).get("file_metadata") or {}).get("references") or []
    assert {r["tier"] for r in references} >= {sl.TIER_ARXIV, sl.TIER_DOI}
    sections = (store.get_item(item_id).get("file_metadata") or {}).get("sections") or []
    assert {s["role"] for s in sections} >= {sl.ROLE_ABSTRACT, sl.ROLE_METHOD, sl.ROLE_REFERENCES}
    assert fetcher.urls == ["https://arxiv.org/pdf/2103.00020"]


def test_saving_the_same_paper_twice_opens_no_socket_the_second_time(store, paper_pdf_bytes):
    """The re-ingest half of SC#9 through the real pipeline: a SECOND item pointing at the
    same paper reaches the fetch path with an empty content column, so it can only reach
    `done` from the sha256 cache — and the seam it would otherwise use raises.

    Deliberately a second ITEM rather than a second ingest of the same one: re-ingesting
    one item short-circuits on its stored content (see the test below) and would prove
    nothing about the cache.
    """
    ensure_nodes_registered()
    url = "https://arxiv.org/abs/2103.00020"
    first = store.create_typed_item(item_type="bookmark", title="", url=url)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("personalclaw.knowledge.slicing._default_fetch", _Fetcher(paper_pdf_bytes))
        assert _run(ingest_item(store, first)) == "done"

    second = store.create_typed_item(item_type="bookmark", title="", url=url)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("personalclaw.knowledge.slicing._default_fetch", _exploding_fetch)
        assert _run(ingest_item(store, second)) == "done", store.get_item(second).get(
            "processing_error"
        )
    kinds = [row["node_type"] for row in store.get_extracted_contents(second)]
    assert "slice:brief" in kinds and "slice:body" in kinds and "slice:meta" in kinds
    assert (store.get_item(second).get("file_metadata") or {}).get("references")


def test_re_ingesting_one_fetched_paper_reuses_its_stored_text_and_re_slices_it(
    store, paper_pdf_bytes
):
    """Regenerating a fetched paper needs no network — but NOT because of the cache.

    The scrape node's pre-existing "user content wins" short-circuit fires on the text the
    first ingest stored, so the fetch seam is never reached at all. Recorded as its own
    test because the distinction matters: this path re-slices FLATTENED text, so the font
    tier has nothing to measure and the header tier carries the detection. The slices are
    still rebuilt (and replaced, not appended), which is what makes the degradation
    acceptable rather than silent.
    """
    ensure_nodes_registered()
    item_id = store.create_typed_item(
        item_type="bookmark", title="", url="https://arxiv.org/abs/2103.00020"
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("personalclaw.knowledge.slicing._default_fetch", _Fetcher(paper_pdf_bytes))
        assert _run(ingest_item(store, item_id)) == "done"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("personalclaw.knowledge.slicing._default_fetch", _exploding_fetch)
        assert _run(ingest_item(store, item_id)) == "done"
    kinds = [row["node_type"] for row in store.get_extracted_contents(item_id)]
    assert kinds.count("slice:brief") == 1, "a re-ingest must replace rows, not append"


def test_an_uploaded_pdf_gets_the_same_slices_as_a_fetched_one(store, paper_pdf):
    """The primitive is shared, not bolted onto the fetch path: an uploaded PDF (the
    surface a user has today) goes through the same cascade."""
    ensure_nodes_registered()
    item_id = store.create_typed_item(item_type="pdf", title="Paper", content="")
    store.update_item(item_id, file_path=str(paper_pdf))
    store.db.commit()
    assert _run(ingest_item(store, item_id)) == "done"
    kinds = [row["node_type"] for row in store.get_extracted_contents(item_id)]
    assert {"document_read", "slice:brief", "slice:body", "slice:meta"} <= set(kinds)


def test_a_plain_document_yields_no_slices_and_still_completes(store, tmp_path):
    """A .txt is not a paper. 'No canonical sections' is a true answer, so the item must
    finish `done` — marking it partial would downgrade every non-paper in the library."""
    ensure_nodes_registered()
    path = tmp_path / "notes.txt"
    path.write_text("just some notes about nothing in particular")
    item_id = store.create_typed_item(item_type="document", title="N", content="")
    store.update_item(item_id, file_path=str(path))
    store.db.commit()
    assert _run(ingest_item(store, item_id)) == "done"
    kinds = [row["node_type"] for row in store.get_extracted_contents(item_id)]
    assert not any(k.startswith("slice:") for k in kinds)


def test_a_plain_web_bookmark_still_takes_the_html_scraper(store, monkeypatch):
    """The document branch is a ROUTING decision on the URL — a blog post must be
    unaffected, or WS-6 would have broken every existing bookmark."""
    ensure_nodes_registered()
    from personalclaw.knowledge.connectors import web_url as web_url_mod

    async def _fake(self, spec):
        return "the blog post body", {"page_title": "A Blog Post"}

    monkeypatch.setattr(web_url_mod.WebUrlConnector, "fetch", _fake)

    async def _no_fetch(url):
        raise AssertionError("a plain page must not reach the source fetcher")

    monkeypatch.setattr("personalclaw.knowledge.slicing._default_fetch", _no_fetch)
    item_id = store.create_typed_item(
        item_type="bookmark", title="", url="https://example.test/blog/post"
    )
    assert _run(ingest_item(store, item_id)) == "done"
    assert "the blog post body" in (store.get_item(item_id).get("content") or "")


# ── the engine's multi-row mechanism + its consumers ─────────────────────────


def test_the_slicer_is_a_graph_leaf_so_slices_never_reach_consolidate():
    """Routing slices through `consolidate` would header-concat three derived views onto
    the document itself, tripling the text insights/embed read."""
    for item_type in ("pdf", "bookmark"):
        graph = graph_for(item_type)
        assert "document_slice" in graph.nodes
        assert graph.successors("document_slice") == []


def test_a_pool_concatenating_consumer_excludes_slice_rows(store, paper_pdf):
    """A slice is a VIEW of text already in the pool. Any consumer that concatenates the
    whole pool must skip them or it sends the document two or three times."""
    from personalclaw.dashboard.handlers.knowledge import _consolidated_text

    ensure_nodes_registered()
    item_id = store.create_typed_item(item_type="pdf", title="Paper", content="")
    store.update_item(item_id, file_path=str(paper_pdf))
    store.db.commit()
    _run(ingest_item(store, item_id))
    item = store.get_item(item_id)
    text = _consolidated_text(store, item)
    assert "method0 " in text
    assert text.count("method0 ") == 1, "the document must appear once, not once per slice"
    assert sl.is_slice_row("slice:brief") and not sl.is_slice_row("document_read")
