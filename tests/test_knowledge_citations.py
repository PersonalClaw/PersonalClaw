"""Per-marker citations (WF2KNO-11) -- the marker, not the retrieval batch, is the record.

Before this module, the bundled synthesis template stored the whole retrieved set as its
"citations" while the model was asked for `[n]` markers that nothing parsed. These tests pin
the four ways that gap could be closed wrongly: renumbering by appearance, letting a dangling
marker reach the reader, eating ordinary bracketed text, and losing the marker on a
round-trip through a string column.
"""

from __future__ import annotations

from personalclaw.knowledge.citations import (
    EXCERPT_MAX,
    MARKER_RE,
    Citation,
    SourceRef,
    parse_markers,
    parse_persist_form,
    persist_form,
    register_sources,
    resolve,
    strip_markers,
)

# ── the marker NUMBER is the key ──────────────────────────────────────────────


def test_markers_key_on_number_not_order_of_appearance():
    """The claim that would silently mis-attribute everything if we got it wrong: a model
    citing [3] before [1] means sources 3 and 1, and must never be renumbered to 1 and 2."""
    assert parse_markers("Latency rose [3] while cost fell [1].") == (1, 3)


def test_duplicate_markers_collapse_to_one_citation():
    """A source cited in three sentences is one source, not three."""
    assert parse_markers("[2] and again [2] and once more [2]") == (2,)


def test_resolve_keys_each_citation_to_the_source_the_prompt_numbered():
    sources = register_sources(
        [
            {"item_id": "alpha", "content": "first"},
            {"item_id": "beta", "content": "second"},
            {"item_id": "gamma", "content": "third"},
        ]
    )
    res = resolve("Third says so [3]; first agrees [1].", sources)
    assert [(c.marker, c.item_id) for c in res.citations] == [(1, "alpha"), (3, "gamma")]
    assert res.dropped == ()
    assert res.warnings == ()


def test_a_marker_out_of_range_does_not_shift_the_ones_below_it():
    """Guards the tempting shortcut of matching markers positionally against the resolved
    subset: [1] must stay alpha even when [9] is thrown away."""
    sources = register_sources([{"item_id": "alpha"}, {"item_id": "beta"}])
    res = resolve("Claim [9]. Other claim [1].", sources)
    assert [(c.marker, c.item_id) for c in res.citations] == [(1, "alpha")]


# ── a dangling marker never reaches the reader ────────────────────────────────


def test_an_unregistered_marker_is_dropped_warned_and_removed_from_the_text():
    sources = register_sources([{"item_id": "alpha", "content": "only source"}])
    res = resolve("Supported [1] but this part is invented [7].", sources)
    assert res.dropped == (7,)
    assert "[7]" not in res.text
    assert "[1]" in res.text, "a resolved marker must survive -- the reader looks it up"
    assert len(res.warnings) == 1
    assert "[7]" in res.warnings[0]


def test_removing_a_dropped_marker_repairs_the_whitespace_hole():
    res = resolve("The claim [4] holds.", register_sources([]))
    assert res.text == "The claim holds."


def test_a_dropped_marker_before_punctuation_leaves_no_gap():
    res = resolve("The claim holds [4].", register_sources([]))
    assert res.text == "The claim holds."


def test_text_with_no_dropped_markers_is_returned_unchanged():
    """No dropped marker means no repair pass, so double spaces the author wrote survive."""
    sources = register_sources([{"item_id": "alpha"}])
    original = "Two  spaces here [1]  and there."
    assert resolve(original, sources).text == original


# ── strip_markers must not eat ordinary bracketed text ────────────────────────


def test_strip_markers_removes_every_citation():
    assert strip_markers("A [1] and B [12] and C [3].") == "A and B and C."


def test_strip_markers_leaves_a_markdown_link_intact():
    """The rule this exists for: a link label that happens to be a number is a LINK. Eating
    it both invents an attribution and mangles the URL into loose parens."""
    text = "See [1](https://example.test/doc) for detail."
    assert strip_markers(text) == text


def test_strip_markers_leaves_non_numeric_and_empty_brackets_alone():
    text = "A [TODO] item, an empty [] pair, and an [x] box."
    assert strip_markers(text) == text


def test_strip_markers_leaves_a_four_digit_bracket_alone():
    """[2026] is a year or a line number, not a citation -- the 1-3 digit cap is the guard."""
    text = "Filed in [2026] under policy."
    assert strip_markers(text) == text
    assert MARKER_RE.search(text) is None


def test_a_restripped_synthesis_cannot_collide_with_this_turns_numbering():
    """The end-to-end reason strip_markers exists: a prior synthesis re-offered as a source
    must not carry its own [1] into a fence where [1] means something else."""
    prior = "Cost fell in Q3 [1] per the ledger [2]."
    sources = register_sources([{"item_id": "fresh", "content": strip_markers(prior)}])
    assert parse_markers(sources[0].excerpt) == ()


# ── register_sources matches the prompt's numbering exactly ───────────────────


def test_numbering_starts_at_one_matching_pipe_fenced_sources():
    refs = register_sources([{"item_id": "a"}, {"item_id": "b"}, {"item_id": "c"}])
    assert [r.marker for r in refs] == [1, 2, 3]


def test_a_plain_string_source_still_consumes_its_marker_number():
    """A string has no stored id, but skipping it would renumber every source after it."""
    refs = register_sources(["loose text", {"item_id": "b"}])
    assert [(r.marker, r.item_id) for r in refs] == [(1, ""), (2, "b")]
    assert refs[0].excerpt == "loose text"


def test_register_sources_accepts_either_item_id_or_id():
    refs = register_sources([{"id": "legacy-key"}])
    assert refs[0].item_id == "legacy-key"


def test_register_sources_falls_back_through_content_summary_excerpt():
    refs = register_sources(
        [{"item_id": "a", "summary": "the summary"}, {"item_id": "b", "excerpt": "the excerpt"}]
    )
    assert [r.excerpt for r in refs] == ["the summary", "the excerpt"]


def test_chunk_zero_is_a_real_chunk_not_the_whole_item():
    """`int(raw or -1)` would relabel every item's first chunk as "whole item"."""
    refs = register_sources([{"item_id": "a", "chunk_index": 0}, {"item_id": "b"}])
    assert [r.chunk_index for r in refs] == [0, -1]


def test_excerpt_is_whitespace_collapsed_and_capped():
    refs = register_sources([{"item_id": "a", "content": "line one\n\n   line   two"}])
    assert refs[0].excerpt == "line one line two"
    long = register_sources([{"item_id": "b", "content": "x" * 900}])
    assert len(long[0].excerpt) <= EXCERPT_MAX
    assert long[0].excerpt.endswith("...")


def test_resolve_carries_the_chunk_and_excerpt_through():
    sources = register_sources([{"item_id": "a", "chunk_index": 4, "content": "  body  "}])
    (citation,) = resolve("Claim [1].", sources).citations
    assert (citation.chunk_index, citation.excerpt) == (4, "body")


# ── the persisted string form ─────────────────────────────────────────────────


def test_persist_form_round_trips_marker_item_id_and_chunk():
    citations = [
        Citation(marker=1, item_id="alpha", chunk_index=-1, excerpt="dropped in string form"),
        Citation(marker=12, item_id="beta", chunk_index=0),
    ]
    encoded = persist_form(citations)
    assert encoded == ["cite:1:-1:alpha", "cite:12:0:beta"]
    decoded = parse_persist_form(encoded)
    assert [(c.marker, c.item_id, c.chunk_index) for c in decoded] == [
        (1, "alpha", -1),
        (12, "beta", 0),
    ]


def test_persist_form_survives_an_item_id_containing_a_colon():
    """The id is the unbounded tail, so a colon in it cannot shift the integer fields."""
    (decoded,) = parse_persist_form(persist_form([Citation(marker=3, item_id="ns:sub:id")]))
    assert (decoded.marker, decoded.item_id) == (3, "ns:sub:id")


def test_parse_persist_form_skips_the_legacy_bare_item_form():
    """A store written before this module holds bare ids. Raising on them would make every
    pre-existing synthesis unreadable rather than merely under-attributed."""
    decoded = parse_persist_form(["item:old-one", "abc123", "cite:2:-1:new-one", ""])
    assert [(c.marker, c.item_id) for c in decoded] == [(2, "new-one")]


def test_parse_persist_form_skips_a_malformed_cite_value():
    assert parse_persist_form(["cite:notanumber:-1:a", "cite:1:x:b", "cite:1"]) == ()


def test_source_ref_and_citation_are_hashable_frozen_records():
    """Frozen so a resolution cannot be edited after the fact -- an attribution that can be
    mutated in place is not evidence."""
    assert len({SourceRef(marker=1, item_id="a"), SourceRef(marker=1, item_id="a")}) == 1
    assert len({Citation(marker=1, item_id="a"), Citation(marker=2, item_id="a")}) == 2
