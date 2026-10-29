"""The committed shared contracts keep every section a task cites, and nothing the copy drops.

A task cites ``docs/architecture/shared-contracts.md`` by section number (§0.3), so each number from
§0.1 to §17.16 stays where it is, §0.4 is the one left unused, and nothing past §17 exists. Each
subsection from §1.1 on starts ``Status: planned`` until the change that builds it removes the line;
§0 says how to use the document and carries none. The committed copy holds no trace comment and no
line naming who owns or reads a section: it says what a section defines, not who builds it. Each
check is shown red on a planted copy, so a check that stopped matching cannot pass in silence.
"""

from __future__ import annotations

import re

import source_index
from rails_repo import REPO

from personalclaw import ids

DOC = REPO / "docs" / "architecture" / "shared-contracts.md"

#: §0's subsections: it says how to use the document, and §0.4 is the one number left unused.
SECTION_0 = ("0.1", "0.2", "0.3", "0.5", "0.6")
#: How many numbered subsections each later section holds; §3.3 holds §3.3.1 besides.
SUBSECTIONS = {
    1: 11, 2: 5, 3: 12, 4: 7, 5: 9, 6: 7, 7: 8, 8: 9, 9: 11,
    10: 7, 11: 6, 12: 4, 13: 6, 14: 4, 15: 4, 16: 4, 17: 16,
}  # fmt: skip

_TOP = re.compile(r"^## (\d+)\. ", re.M)
_SUB = re.compile(r"^#{3,4} (\d+(?:\.\d+)+) ", re.M)
_OWNERS = re.compile(r"^\*\*(?:Owners?|Consumers):\*\*", re.M)


def _numbers() -> list[str]:
    """Every subsection's number, in order: 136 of them."""
    numbers = list(SECTION_0)
    for top, count in SUBSECTIONS.items():
        numbers += [f"{top}.{n}" for n in range(1, count + 1)]
    numbers.insert(numbers.index("3.3") + 1, "3.3.1")
    return numbers


def problems(text: str) -> list[str]:
    """What is wrong with *text* as the committed shared contracts; empty when nothing is."""
    found = []
    tops = _TOP.findall(text)
    if tops != [str(n) for n in range(18)]:
        found.append(f"the top-level sections are §{', §'.join(tops)}, not §0 to §17")
    numbers = _SUB.findall(text)
    if numbers != _numbers():
        missing = sorted(set(_numbers()) - set(numbers))
        extra = sorted(set(numbers) - set(_numbers()))
        found.append(f"the subsections miss {missing}, add {extra}, or are out of order")
    lines = text.split("\n")
    for n, line in enumerate(lines):
        if not line.startswith("Status:"):
            continue
        head = _SUB.match(lines[n - 2]) if n >= 2 and not lines[n - 1] else None
        if line != "Status: planned":
            found.append(f"line {n + 1}: {line!r} is not `Status: planned`")
        elif not head or head.group(1).startswith("0."):
            found.append(f"line {n + 1}: a Status line not right under a subsection from §1.1 on")
    if "<!--" in text:
        found.append("an HTML comment")
    if _OWNERS.search(text):
        found.append("a line naming who owns or reads a section")
    return found


def _committed() -> str:
    return source_index.text(DOC)


def test_the_committed_contracts_keep_their_sections_and_drop_the_rest():
    assert problems(_committed()) == []


def test_every_subsection_from_1_1_is_planned_or_built():
    """The line is removed by the change that builds the section, so its count only falls; §0's
    subsections are the document's own and are never planned."""
    text = _committed()
    planned = len(re.findall(r"^Status: planned$", text, re.M))
    assert len(_numbers()) == 136
    assert planned <= len(_numbers()) - len(SECTION_0)


def test_each_check_is_red_on_a_planted_copy():
    text = _committed()
    first_status = re.compile(r"^Status: planned$", re.M)
    under_0_1 = re.compile(r"^(### 0\.1 .*\n)", re.M)
    plants = [
        (re.sub(r"^### 5\.3 .*\n", "", text, count=1, flags=re.M), "the subsections miss ['5.3']"),
        (text.replace("### 0.5 ", "### 0.4 Themes\n\n### 0.5 ", 1), "add ['0.4']"),
        (text + "\n## 18. Decisions\n", "not §0 to §17"),
        (first_status.sub("Status: built", text, count=1), "is not `Status: planned`"),
        (under_0_1.sub(r"\1\nStatus: planned\n", text, count=1), "a Status line not right under"),
        (text + "\n<!-- planning trace -->\n", "an HTML comment"),
        (text + "\n**Owner:** someone\n", "who owns or reads"),
    ]
    for planted, said in plants:
        assert planted != text, f"the plant for {said!r} changed nothing"
        assert any(said in problem for problem in problems(planted)), f"no check said {said!r}"


def _id_table(text: str) -> list[str]:
    """The rows of §2.1's id table."""
    section = text[text.index("### 2.1 ") : text.index("### 2.2 ")]
    return [line for line in section.split("\n") if line.startswith("| ") and "`" in line]


def test_prefixes_are_the_shared_table():
    """``ids.PREFIXES`` is §2.1's closed set, each row's prefixes from its Format cell (a row may
    list several: `addr_`, `bind_`, `snd_`, `shr_<ULID>`), plus the mini apps' ``ma``, which
    another release allocates. A revision number is an integer, so its row gives none."""
    table = set()
    for line in _id_table(_committed()):
        table |= set(re.findall(r"`([a-z]+)_(?:<ULID>)?`", line.split("|")[2]))
    assert table, "read no prefix from §2.1's table — vacuous"
    assert ids.PREFIXES == table | {"ma"}
