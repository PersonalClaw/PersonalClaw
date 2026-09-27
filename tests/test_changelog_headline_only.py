"""The CHANGELOG is headline-only: every entry is one line, ``- **<headline>**``, and nothing else.

Each entry used to be a bold headline followed by paragraphs of implementation story, and the
file had grown past 1.4 MB. The headline was already the change note a user reads, so the file
now keeps only that: the preamble, the version and section headings, each release's
introduction, and one line per entry. Upgrade steps, config keys and SDK notes live in the docs,
where a reader looks for them (``docs/guides/getting-started.md#updating``, CONTRIBUTING.md's
CHANGELOG section).

This rail keeps the old habit from coming back. After the first ``## `` heading a line may be:

- blank;
- a heading (``## [X.Y.Z] — DATE``, ``### Added``, …);
- a line of a release's INTRODUCTION: prose right under a ``## `` heading, before its first
  ``### `` heading or entry. The website builds each release's summary from exactly that text,
  and the in-app Updates panel shows it under the release's heading;
- an entry: ``- **<headline>**`` on ONE line, whose bold headline closes at the end of the line.

Anything else is a body in disguise, and is refused: an indented continuation line (in an
introduction too), prose anywhere but an introduction — under a ``### `` heading, between
entries, after the last one — text after the headline's closing ``**``, or an entry with no bold
headline.

The bold span is read the way the CHANGELOG's own form is written: it closes at the first
``**`` after a non-space that is not followed by a word character, a backtick or ``*``, and that
leaves the bold inside it balanced — so ``- **A → **B** C**`` is one headline, while
``- **A** and **B**`` is a headline with text after it.

🪤 ONE LEGACY SHAPE IS ACCEPTED, AND ONLY WHERE THE HISTORY HAS IT. 36 entries of 0.2.0 were
written without a bold headline, so each keeps its first sentence as its headline, and that
sentence already carries bold of its own (``- The **YOLO mode** toggle … now applies
immediately …``). That shape is accepted in a RELEASED section, as a single sentence. Under
``## [Unreleased]`` — where every new entry is written — only ``- **<headline>**`` is.

The preamble (everything before the first ``## ``) is the file's own front matter and is not an
entry, so it is not held to this form.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

#: A ``**`` that can close a bold span: after a non-space, and not followed by a word
#: character, a backtick or another ``*``.
_BOLD_CLOSE = re.compile(r"(?<=\S)\*\*(?![\w`*])")

#: A sentence boundary: ``.``, ``!`` or ``?``, whitespace, then something that starts a
#: sentence — but not after an initial, ``e.g.``, ``i.e.``, ``vs.`` or ``etc.``.
_SENTENCE_BREAK = re.compile(
    r"(?<![A-Z]\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bvs\.)(?<!\betc\.)(?<=[.!?])\s+"
    r"(?=[A-Z*`(\[\"'“])"
)

UNRELEASED = "## [Unreleased]"


def headline_end(text: str) -> int | None:
    """Where the bold headline that opens *text* (``**…``) closes, or ``None`` if it never does."""
    for match in _BOLD_CLOSE.finditer(text):
        if match.start() < 2:
            continue
        if text[2 : match.start()].count("**") % 2 == 0:
            return match.end()
    return None


def check(text: str) -> tuple[int, int, list[tuple[int, str, str]]]:
    """``(entry lines examined, introduction lines accepted, violations)``; a violation is
    ``(line number, what is wrong, the line)``. The two counts are what the vacuity floors
    compare with the file.

    A release's introduction runs from its ``## `` heading to its first ``### `` heading or entry,
    the same region the CHANGELOG's form keeps."""
    found: list[tuple[int, str, str]] = []
    examined = introduction = 0
    release = ""
    in_introduction = False
    for number, line in enumerate(text.split("\n"), 1):
        if line.startswith("## "):
            release, in_introduction = line, True
            continue
        if line.startswith("### "):
            in_introduction = False
        if not release or not line.strip() or line.startswith("#"):
            continue
        if line[:1] in (" ", "\t"):
            found.append(
                (number, "a continuation line: entries and introductions are not indented", line)
            )
            continue
        if not line.startswith("- "):
            if in_introduction:
                introduction += 1
            else:
                found.append(
                    (
                        number,
                        "prose outside a release's introduction: only headings and entries",
                        line,
                    )
                )
            continue
        in_introduction = False
        examined += 1
        entry = line[2:]
        if entry.startswith("**"):
            end = headline_end(entry)
            if end is None:
                found.append((number, "the bold headline never closes", line))
            elif end != len(entry):
                found.append((number, "text after the headline: the headline IS the entry", line))
            continue
        if release.startswith(UNRELEASED):
            found.append((number, "no bold headline: write `- **<headline>**`", line))
        elif "**" not in entry:
            found.append((number, "no bold headline: write `- **<headline>**`", line))
        elif _SENTENCE_BREAK.search(entry):
            found.append((number, "more than a headline: a legacy entry is one sentence", line))
    return examined, introduction, found


def violations(text: str) -> list[tuple[int, str, str]]:
    """The violations :func:`check` finds in *text*."""
    return check(text)[2]


def introduction_lines(text: str) -> int:
    """How many prose lines sit in the file's release introductions — counted apart from
    :func:`check`, so a checker that admits prose elsewhere, or skips an introduction, cannot
    vouch for itself."""
    lines = text.split("\n")
    count = 0
    for at, line in enumerate(lines):
        if not line.startswith("## "):
            continue
        for row in lines[at + 1 :]:
            if row.startswith(("## ", "### ", "- ")):
                break
            count += bool(row.strip()) and not row.startswith("#")
    return count


def entry_count(text: str) -> int:
    """How many entry lines the file holds after its first release heading — counted apart from
    :func:`check`, so a checker that skips entries cannot vouch for itself."""
    lines = text.split("\n")
    first = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
    return sum(1 for line in lines[first:] if line.startswith("- "))


def _read() -> str:
    assert CHANGELOG.is_file(), "CHANGELOG.md is missing — this rail has nothing to check"
    return CHANGELOG.read_text(encoding="utf-8")


# ── the rail ─────────────────────────────────────────────────────────────────────────────────


def test_every_changelog_entry_is_one_headline_line() -> None:
    found = violations(_read())
    assert not found, (
        f"{len(found)} CHANGELOG line(s) break the headline-only form:\n"
        + "\n".join(f"  CHANGELOG.md:{n}: {why}\n    {line[:160]}" for n, why, line in found[:20])
        + "\n\nEvery entry is ONE line, `- **<headline>**`. Put upgrade steps, config keys and SDK "
        "notes in the docs (see CONTRIBUTING.md#changelog), not under the headline."
    )


# ── the vacuity floor ────────────────────────────────────────────────────────────────────────


def test_the_rail_reads_a_real_changelog_with_releases_and_entries() -> None:
    """A rail that parses nothing passes everything, so the population it checked is asserted.

    Two releases at least (the release cut and the installer's floor read them too), a section
    heading, and every ``- `` line after the first release heading counted as an entry — the
    floor is what the file holds, measured, not a number typed in here.
    """
    text = _read()
    releases = [line for line in text.split("\n") if line.startswith("## [")]
    assert len(releases) >= 2, f"CHANGELOG.md has {len(releases)} release heading(s)"
    assert any(line.startswith("### ") for line in text.split("\n")), "no section heading"
    examined, _, _ = check(text)
    assert examined == entry_count(
        text
    ), f"the rail examined {examined} of the file's {entry_count(text)} entries — it skips some"
    assert examined > 100, (
        f"only {examined} entries were checked; the rail is reading the wrong file or a parse "
        "that finds nothing"
    )


def test_the_legacy_shape_is_real_and_confined_to_released_sections() -> None:
    """The one exception exists in the file, so the rule that admits it is exercised rather than
    claimed — and none of it sits under ``[Unreleased]``."""
    text = _read()
    release, legacy, unreleased = "", 0, 0
    for line in text.split("\n"):
        if line.startswith("## "):
            release = line
        elif release and line.startswith("- ") and not line.startswith("- **"):
            legacy += 1
            unreleased += release.startswith(UNRELEASED)
    assert legacy > 0, "no legacy entry is left — drop the exception from this rail"
    assert unreleased == 0, f"{unreleased} entries under [Unreleased] have no bold headline"


def test_the_introductions_are_real_and_the_rail_admits_exactly_them() -> None:
    """The release introductions exist in the file, so the rule that admits them is exercised
    rather than claimed, and the rail counts every one of their lines as admitted. Refusing prose
    anywhere else is the planted controls' job: the real file has none to admit."""
    text = _read()
    _, admitted, _ = check(text)
    expected = introduction_lines(text)
    assert expected > 0, "no release introduction is left — the website's summaries are empty"
    assert admitted == expected, f"the rail admitted {admitted} introduction lines of {expected}"


# ── the planted violations ───────────────────────────────────────────────────────────────────

_HEADLINE = "- **A planted headline.**"


def _plant(text: str, lines: list[str], *, under: str = UNRELEASED) -> tuple[str, int]:
    """*text* with *lines* inserted right after the first entry of the section *under*, and the
    1-based number of the first planted line."""
    rows = text.split("\n")
    start = next(i for i, row in enumerate(rows) if row.startswith(under))
    at = next(i for i in range(start + 1, len(rows)) if rows[i].startswith("- ")) + 1
    return "\n".join(rows[:at] + lines + rows[at:]), at + 1


@pytest.mark.parametrize(
    ("planted", "why"),
    [
        ([_HEADLINE, "  A body paragraph, indented under its headline."], "continuation"),
        ([_HEADLINE, "", "    A body paragraph after a blank line."], "continuation"),
        ([_HEADLINE, "A lazy continuation, not indented at all."], "prose"),
        (["- **A headline.** And the body on the same line."], "after the headline"),
        (["- **`a.b` changed** and so did **this**."], "after the headline"),
        (["- **A headline that never closes."], "never closes"),
        (["- A plain entry with no bold at all."], "no bold headline"),
        (["- The legacy **shape**, written today, under Unreleased."], "no bold headline"),
        (["* A different bullet."], "prose"),
        (["[Unreleased]: https://example.test/compare/v0.2.0...HEAD"], "prose"),
    ],
)
def test_a_planted_body_reds(planted: list[str], why: str) -> None:
    text, at = _plant(_read(), planted)
    found = violations(text)
    assert found, f"planted {planted!r} passed the rail"
    assert all(at <= n < at + len(planted) for n, _, _ in found), f"outside the plant: {found}"
    assert any(why in reason for _, reason, _ in found), found


def _plant_under_heading(text: str, lines: list[str], heading: str) -> tuple[str, int]:
    """*text* with *lines* inserted right after the first line that starts with *heading*, and
    the 1-based number of the first planted line."""
    rows = text.split("\n")
    at = next(i for i, row in enumerate(rows) if row.startswith(heading)) + 1
    return "\n".join(rows[:at] + lines + rows[at:]), at + 1


_INTRODUCTION = [
    "",
    "What this release is about, in a short paragraph that",
    "wraps onto a second line, with **bold** and a [link](https://example.test).",
    "",
    "> **Note:** a quoted note belongs to the introduction too.",
]


@pytest.mark.parametrize("heading", [UNRELEASED, "## [0"])
def test_an_introduction_right_under_a_release_heading_passes(heading: str) -> None:
    text, _ = _plant_under_heading(_read(), _INTRODUCTION, heading)
    assert violations(text) == []
    assert check(text)[1] == introduction_lines(_read()) + 3


@pytest.mark.parametrize(
    ("heading", "planted"),
    [
        ("### ", ["", "Prose under a section heading, before its first entry."]),
        ("### ", ["", "> A quoted note under a section heading."]),
        ("## [0", ["", "An introduction.", "  indented under it: a continuation line."]),
    ],
)
def test_prose_in_the_wrong_place_reds(heading: str, planted: list[str]) -> None:
    text, at = _plant_under_heading(_read(), planted, heading)
    found = violations(text)
    assert [n for n, _, _ in found] == [at + len(planted) - 1], found
    assert any(why in found[0][1] for why in ("prose outside", "continuation")), found


def test_an_introduction_ends_at_the_first_entry_and_restarts_at_the_next_release() -> None:
    """Without a ``### `` heading the first entry ends the introduction; each ``## `` heading
    starts its own."""
    doc = "\n".join(
        [
            "## [1.1.0] — 2026-10-01",
            "",
            "The 1.1 introduction.",
            "",
            "- **An entry straight under the heading.**",
            "Prose after that entry.",
            "",
            "## [1.0.0] — 2026-09-01",
            "",
            "The 1.0 introduction, admitted: a new release, a new introduction.",
            "",
            "### Added",
            "",
            "- **An entry.**",
            "",
            "Prose after the last entry.",
        ]
    )
    assert [(n, why) for n, why, _ in violations(doc)] == [
        (6, "prose outside a release's introduction: only headings and entries"),
        (16, "prose outside a release's introduction: only headings and entries"),
    ]
    assert check(doc)[1] == introduction_lines(doc) == 2


def test_the_legacy_shape_is_one_sentence_even_in_a_released_section() -> None:
    released = next(line for line in _read().split("\n") if line.startswith("## [0"))
    ok, _ = _plant(_read(), ["- The **legacy** shape is one sentence."], under=released)
    assert violations(ok) == []
    bad, _ = _plant(_read(), ["- The **legacy** shape. Then a body sentence."], under=released)
    assert [why for _, why, _ in violations(bad)] == [
        "more than a headline: a legacy entry is one sentence"
    ]
    plain, _ = _plant(_read(), ["- A released entry with no bold."], under=released)
    assert [why for _, why, _ in violations(plain)] == [
        "no bold headline: write `- **<headline>**`"
    ]


def test_a_headline_with_nested_bold_is_one_headline() -> None:
    """``**A → **B** C**`` closes after C: the inner bold is balanced, so there is no text after
    the headline. Without the balance rule this real shape would be refused."""
    assert headline_end("**A → **B** C**") == len("**A → **B** C**")
    assert headline_end("**A** and **B**") == len("**A**")
    text, _ = _plant(_read(), ["- **A → **B** C**"])
    assert violations(text) == []


def test_the_preamble_is_not_an_entry() -> None:
    """The front matter before the first release heading may be prose; the form starts there."""
    assert (
        violations("# Changelog\n\nAll notable changes.\n  indented note\n\n## [Unreleased]\n")
        == []
    )
