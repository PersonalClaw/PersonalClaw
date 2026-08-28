"""DFE-2: markdown inline formatting round-trips into `Run`s, and `_strip_inline` is gone.

The compatibility proof lives in `OLD_STRIP_INLINE`: every expected string there was
MEASURED by running `origin/main`'s `_strip_inline` before it was deleted, so the round-trip
test is evidence that the seven converted call sites still hand users the same text — not
just evidence that the new parser agrees with itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from personalclaw.documents.from_markup import (
    deck_from_markdown,
    document_from_markdown,
    inline_text,
    parse_inline,
)


def _flat(text: str) -> list[tuple[str, bool, bool, bool, str]]:
    return [(r.text, r.bold, r.italic, r.code, r.link) for r in parse_inline(text)]


# --------------------------------------------------------------------------------------
# The four inline forms
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("src", ["**bold**", "__bold__"])
def test_bold_becomes_one_bold_run(src: str) -> None:
    assert _flat(src) == [("bold", True, False, False, "")]


@pytest.mark.parametrize("src", ["*italic*", "_italic_"])
def test_italic_becomes_one_italic_run(src: str) -> None:
    assert _flat(src) == [("italic", False, True, False, "")]


def test_code_span_becomes_one_code_run() -> None:
    assert _flat("`code`") == [("code", False, False, True, "")]


def test_link_text_is_the_run_and_the_url_rides_link() -> None:
    assert _flat("[label](https://example.com)") == [
        ("label", False, False, False, "https://example.com")
    ]


def test_a_bare_url_is_not_a_link() -> None:
    assert _flat("see https://example.com/a_b now") == [
        ("see https://example.com/a_b now", False, False, False, "")
    ]


def test_an_empty_url_is_not_a_link() -> None:
    # `link=""` is the contract's "not a link", so `[label]()` must not claim to be one.
    assert _flat("[label]()") == [("label", False, False, False, "")]


def test_an_empty_label_is_not_a_link_and_keeps_its_brackets() -> None:
    assert _flat("[](url)") == [("[](url)", False, False, False, "")]


def test_a_link_label_carries_its_own_formatting() -> None:
    assert _flat("[**bold link**](u)") == [("bold link", True, False, False, "u")]


def test_mixed_line_splits_into_one_run_per_span() -> None:
    assert _flat("a **b** c *d* e `f` g [h](u) i") == [
        ("a ", False, False, False, ""),
        ("b", True, False, False, ""),
        (" c ", False, False, False, ""),
        ("d", False, True, False, ""),
        (" e ", False, False, False, ""),
        ("f", False, False, True, ""),
        (" g ", False, False, False, ""),
        ("h", False, False, False, "u"),
        (" i", False, False, False, ""),
    ]


# --------------------------------------------------------------------------------------
# Nesting: SUPPORTED, by recursing into the span's content
# --------------------------------------------------------------------------------------


def test_italic_nested_inside_bold_is_supported() -> None:
    assert _flat("**bold with *italic* inside**") == [
        ("bold with ", True, False, False, ""),
        ("italic", True, True, False, ""),
        (" inside", True, False, False, ""),
    ]


def test_bold_nested_inside_italic_is_supported() -> None:
    assert _flat("*italic with **bold** inside*") == [
        ("italic with ", False, True, False, ""),
        ("bold", True, True, False, ""),
        (" inside", False, True, False, ""),
    ]


def test_triple_marker_is_bold_and_italic_at_once() -> None:
    assert _flat("***both***") == [("both", True, True, False, "")]


def test_code_inside_bold_stays_code_and_stays_bold() -> None:
    assert _flat("**a `b` c**") == [
        ("a ", True, False, False, ""),
        ("b", True, False, True, ""),
        (" c", True, False, False, ""),
    ]


# --------------------------------------------------------------------------------------
# Code spans are literal
# --------------------------------------------------------------------------------------


def test_code_span_contents_are_literal() -> None:
    assert _flat("`**not bold**`") == [("**not bold**", False, False, True, "")]


def test_a_code_span_keeps_lone_asterisks_and_brackets() -> None:
    assert _flat("`a * b [c](d)`") == [("a * b [c](d)", False, False, True, "")]


def test_a_delimiter_inside_a_code_span_does_not_close_emphasis() -> None:
    # The `**` inside the span must not be mistaken for the bold closer.
    assert _flat("**a `x**y` b**") == [
        ("a ", True, False, False, ""),
        ("x**y", True, False, True, ""),
        (" b", True, False, False, ""),
    ]


def test_an_empty_backtick_pair_is_not_a_code_span() -> None:
    assert _flat("a `` b") == [("a `` b", False, False, False, "")]


# --------------------------------------------------------------------------------------
# Unmatched markers stay literal — dropping user content is the unacceptable failure
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "**unclosed",
        "text ** more",
        "no *closer here",
        "trailing `backtick",
        "[t](",
        "[t]",
        "empty **** marker",
        "empty ** ** marker",
        "2 * 3 * 4",
        "snake_case_name here",
        "a_b_c_d",
        "# not a heading here",
    ],
)
def test_unmatched_or_ordinary_markers_stay_literal(src: str) -> None:
    assert _flat(src) == [(src, False, False, False, "")]


def test_a_partial_link_keeps_its_leftover_bracket() -> None:
    assert _flat("half [a](b) and [c") == [
        ("half ", False, False, False, ""),
        ("a", False, False, False, "b"),
        (" and [c", False, False, False, ""),
    ]


# --------------------------------------------------------------------------------------
# Empty input pins to NO runs
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("src", ["", "   ", "\t\n ", "\n"])
def test_blank_input_yields_no_runs(src: str) -> None:
    assert parse_inline(src) == []


def test_edge_whitespace_is_trimmed_like_the_old_stripper_did() -> None:
    assert _flat("  **b** and c  ") == [
        ("b", True, False, False, ""),
        (" and c", False, False, False, ""),
    ]


# --------------------------------------------------------------------------------------
# Compatibility proof: MEASURED outputs of origin/main's `_strip_inline`
# --------------------------------------------------------------------------------------

# Every value below was produced by RUNNING `origin/main`'s `_strip_inline` (the four
# `re.sub` calls plus a trailing `.strip()`) on the key. They are measurements, not
# guesses, and they are what the seven converted call sites used to emit.
OLD_STRIP_INLINE: dict[str, str] = {
    "plain text": "plain text",
    "**bold**": "bold",
    "*italic*": "italic",
    "_italic_": "italic",
    "__bold__": "bold",
    "`code`": "code",
    "[label](https://example.com)": "label",
    "a **b** c *d* e `f` g [h](u) i": "a b c d e f g h i",
    "**bold with *italic* inside**": "bold with italic inside",
    "*italic with **bold** inside*": "italic with bold inside",
    "`a * b`": "a * b",
    "**unclosed": "**unclosed",
    "text ** more": "text ** more",
    "[t](": "[t](",
    "[t]": "[t]",
    "half [a](b) and [c": "half a and [c",
    "  leading and trailing  ": "leading and trailing",
    "": "",
    "   ": "",
    "\t\n ": "",
    "https://example.com/a_b": "https://example.com/a_b",
    "***both***": "both",
    "**a** and **b**": "a and b",
    "*a* and *b*": "a and b",
    "`x` and `y`": "x and y",
    "[a](u1) and [b](u2)": "a and b",
    "no *closer here": "no *closer here",
    "trailing `backtick": "trailing `backtick",
    "[](url)": "[](url)",
    "[label]()": "label",
    "a\\*not italic\\*b": "a\\not italic\\b",
    "**bold** at start": "bold at start",
    "end with **bold**": "end with bold",
    "|cell **b**|": "|cell b|",
    "# not a heading here": "# not a heading here",
    "**multi word bold** rest": "multi word bold rest",
    "[**bold link**](u)": "bold link",
}

# The six inputs where the new parser DELIBERATELY differs. Both columns are measured:
# `old` from `origin/main`'s function, `new` from the parser under test. In every case the
# new output KEEPS characters the old one silently ate — the one case that is not a pure
# bug fix is the code span, which is the atom's own "code spans are literal" requirement.
DELIBERATE_DIVERGENCE: list[tuple[str, str, str]] = [
    # The old stripper ran its bold/italic substitutions BEFORE the code substitution, so
    # a code span's markers were eaten. The atom requires them to survive.
    ("`**not bold**`", "not bold", "**not bold**"),
    # An emphasis opener followed by whitespace is not an opener; arithmetic keeps its `*`.
    ("2 * 3 * 4", "2  3  4", "2 * 3 * 4"),
    ("empty ** ** marker", "empty   marker", "empty ** ** marker"),
    # An `_` inside a word is not emphasis; identifiers keep their underscores.
    ("snake_case_name here", "snakecasename here", "snake_case_name here"),
    ("a_b_c_d", "abc_d", "a_b_c_d"),
    # A run of 4+ delimiters is nobody's emphasis, so none of it is consumed.
    ("empty **** marker", "empty ** marker", "empty **** marker"),
]


@pytest.mark.parametrize(("src", "expected"), sorted(OLD_STRIP_INLINE.items()))
def test_joined_run_text_matches_the_measured_old_output(src: str, expected: str) -> None:
    assert inline_text(parse_inline(src)) == expected


@pytest.mark.parametrize(("src", "old", "new"), DELIBERATE_DIVERGENCE)
def test_deliberate_divergences_are_pinned(src: str, old: str, new: str) -> None:
    assert inline_text(parse_inline(src)) == new
    assert new != old  # the row is only a divergence if the values actually differ


def _is_subsequence(needle: str, haystack: str) -> bool:
    it = iter(haystack)
    return all(c in it for c in needle)


@pytest.mark.parametrize(
    "src",
    sorted(OLD_STRIP_INLINE) + [row[0] for row in DELIBERATE_DIVERGENCE],
)
def test_no_measured_character_is_ever_dropped(src: str) -> None:
    """Everything the old stripper emitted still appears, in order, in the new text.

    This is the general no-dropped-content rail: it holds across the agreeing cases AND
    the divergences, because every divergence only ever ADDS back characters.
    """
    if src in OLD_STRIP_INLINE:  # a measured "" is a real value, not a miss
        old = OLD_STRIP_INLINE[src]
    else:
        old = next(r[1] for r in DELIBERATE_DIVERGENCE if r[0] == src)
    new = inline_text(parse_inline(src))
    assert _is_subsequence(old, new), f"{old!r} is not preserved in {new!r}"
    assert _is_subsequence(new, src), f"{new!r} invents text not present in {src!r}"


# --------------------------------------------------------------------------------------
# The clean break: `_strip_inline` does not survive anywhere
# --------------------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]

#: Every tree that SHIPS, the file types to read in it, and names that are genuinely present
#: there. `docs/` and this file are excluded deliberately: the plan and the compatibility
#: table above MUST keep naming `_strip_inline` for the deletion to stay auditable.
_SHIPPED_SOURCE = (
    (
        _REPO / "src" / "personalclaw",
        ("*.py",),
        ("parse_inline", "inline_text", "document_from_markdown", "class Block"),
    ),
    (_REPO / "web" / "src", ("*.ts", "*.tsx"), ("displayText",)),
)


def test_strip_inline_is_cited_nowhere_in_shipped_source() -> None:
    """The old name is gone from the whole product, not just the package that held it.

    Scoping this to `documents/` — where the function lived — is the narrow reading that let
    `web/src/pages/knowledge/readingOutline.ts` go on citing `_strip_inline` for its stripping
    behaviour long after that behaviour was INVERTED into run parsing. A dangling citation of a
    deleted function is how the old mental model comes back, so the rail spans both trees.
    """
    for root, globs, present_names in _SHIPPED_SOURCE:
        assert root.is_dir(), f"shipped source tree not found at {root}"
        files = sorted(path for pattern in globs for path in root.rglob(pattern))
        assert len(files) >= 3, f"only {len(files)} files scanned under {root} — too narrow"
        blob = "\n".join(p.read_text(encoding="utf-8") for p in files)
        # Vacuity floor, PER TREE: the same read must find names that ARE there. Without it a
        # typo'd needle, a wrong path or an empty blob keeps this green forever while the
        # citation sits in the file — exactly the false green that hid the web/ one.
        for present in present_names:
            assert present in blob, f"vacuity guard failed: {present!r} not found under {root}"
        assert "_strip_inline" not in blob, f"`_strip_inline` is still cited under {root}"


# --------------------------------------------------------------------------------------
# The converted call sites
# --------------------------------------------------------------------------------------


def test_a_paragraph_carries_runs_and_derives_its_text() -> None:
    block = document_from_markdown("Some **bold** and `code` here.").blocks[0]
    assert block.kind == "paragraph"
    assert [(r.text, r.bold, r.code) for r in block.runs] == [
        ("Some ", False, False),
        ("bold", True, False),
        (" and ", False, False),
        ("code", False, True),
        (" here.", False, False),
    ]
    # `text` comes from the model's derivation, not from a second computation here.
    assert block.text == "Some bold and code here."


def test_a_heading_carries_runs() -> None:
    blocks = document_from_markdown("# Doc\n\n## A *stressed* heading").blocks
    heading = blocks[0]
    assert heading.kind == "heading"
    assert heading.level == 2
    assert [(r.text, r.italic) for r in heading.runs] == [
        ("A ", False),
        ("stressed", True),
        (" heading", False),
    ]
    assert heading.text == "A stressed heading"


def test_an_h1_title_is_plain_text() -> None:
    assert document_from_markdown("# A **bold** title\n\nbody").title == "A bold title"


def test_bullets_and_numbered_items_stay_plain_strings() -> None:
    blocks = document_from_markdown("- a **b**\n- `c`\n\n1. d *e*\n2. [f](u)").blocks
    assert blocks[0].kind == "bullets"
    assert blocks[0].items == ["a b", "c"]
    assert blocks[1].kind == "numbered"
    assert blocks[1].items == ["d e", "f"]


def test_table_cells_stay_plain_strings() -> None:
    md = "| **H1** | H2 |\n|---|---|\n| `x` | [y](u) |"
    table = document_from_markdown(md).blocks[0]
    assert table.kind == "table"
    assert table.rows == [["H1", "H2"], ["x", "y"]]


def test_a_code_fence_is_still_verbatim() -> None:
    block = document_from_markdown("```\na **b** c\n```").blocks[0]
    assert block.kind == "code"
    assert block.text == "a **b** c"  # never inline-parsed


def test_deck_titles_and_bodies_stay_plain_strings() -> None:
    deck = deck_from_markdown("# My **deck**\n\n## Slide *one*\n\n- a `b` c")
    assert deck.title == "My deck"
    assert deck.slides[0].title == "Slide one"
    # DFE-8 replaced `Slide.body: list[str]` with `bullets: list[Bullet]` so a bullet can
    # carry its indent depth. The claim here is unchanged and is still the point of this
    # file: inline runs (**bold**, *em*, `code`) are FLATTENED to plain text on the way in.
    assert [b.text for b in deck.slides[0].bullets] == ["a b c"]
