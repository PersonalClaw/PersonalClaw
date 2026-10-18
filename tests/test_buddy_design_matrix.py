"""The desktop buddy's design record holds together.

``docs/design/buddy/`` is the one design every buddy task builds from, and the tasks cite it by
path and anchor. Its interruption matrix is written twice: as the data the state machine's tests
read (``interruption-matrix.json``) and as the table people read (``interruption-matrix.md``).
Only a test keeps the two from drifting, and this is it. It holds the record's other promises too:

* **The matrix is whole.** 14 situations by 20 events, every cell an outcome, and exactly 56 of
  them the exact words ``cannot occur here``: the event has no source in that situation, and the
  state machine asserts it is never delivered there.
* **The table says what the data says**, cell for cell, each cell once and in its part.
* **Later work is marked, and only later work is.** v0.3.0 ships the truth and presence layers
  with no play and no drop. ``later`` lists the cells that wait for later work: the play and file
  situations, the file event, and every other cell whose outcome is a play reaction. No other cell
  may name a play reaction, a drop or a quick ask, so the shipping cells can be tested alone.
  ``later_clauses`` holds the play clause a shipping cell gains when play lands.
* **Every anchor a buddy task cites resolves** to a heading, by GitHub's heading-anchor rule.
* **The README indexes the whole record**, each section under the part it belongs to, and each
  later-work section opens with a line naming its task.
* **Every timing the state machine names** has a value, a reader and a reduced-motion answer in
  the motion guide (``docs/design/motion.md`` §9).
* **The buddy's body colour has its token's name now**, though its values wait for the chosen
  character: ``web/DESIGN.md`` names it among the neutral colours, and the character brief calls
  it by the same name, the one the character's values go under and the renderer reads.

Each check returns what is wrong, as sentences. The last test plants one defect for each check and
sees it reported, so none of them can pass by looking at nothing.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import table_rows

REPO = Path(__file__).resolve().parents[1]
FOLDER = REPO / "docs" / "design" / "buddy"
MATRIX_DATA = FOLDER / "interruption-matrix.json"
MATRIX_TABLE = FOLDER / "interruption-matrix.md"
STATE_MACHINE = FOLDER / "state-machine.md"
CHARACTER = FOLDER / "character.md"
MOTION_GUIDE = REPO / "docs" / "design" / "motion.md"
DESIGN = REPO / "web" / "DESIGN.md"

SITUATIONS = (
    "hidden",
    "peek",
    "compact",
    "expanded",
    "alert",
    "finished",
    "floating",
    "sleeping",
    "listening",
    "play",
    "dragging",
    "file",
    "muted",
    "offline",
)
EVENTS = (
    "start",
    "phase",
    "other",
    "alert",
    "answered",
    "elsewhere",
    "done",
    "failed",
    "lost",
    "back",
    "listen",
    "unlisten",
    "hover",
    "click",
    "hits",
    "file",
    "away",
    "return",
    "collapse",
    "drag",
)
KEYS = {"situations", "events", "cells", "later", "later_clauses"}
CANNOT = "cannot occur here"
#: How many cells are ``cannot occur here``. The other 224 are outcomes.
CANNOT_CELLS = 56
#: The situations and the event that exist only with later work: play reactions and the drop.
LATER_SITUATIONS = ("play", "file")
LATER_EVENTS = ("file",)
#: The design's own names for a play reaction, the drop and quick ask. A shipping cell may say
#: that play does not happen ("no play during an alert"), but it never names a reaction.
LATER_WORDS = re.compile(
    r"\bplay:|\bplay is allowed\b|\bgreet|\bpok(?:e|ed|es|ing)\b|\bannoyed\b|\bdizzy\b"
    r"|\bglance|\bdangle|\bswallow|\bfidget|\bstartled\b|\bblink|\blook around\b|\bgaze\b"
    r"|\bdrop target\b|\bthe drop\b|\bdropped\b|\bthe choice\b|\bfile\b|\bask field\b"
    r"|\bquick ask\b",
    re.IGNORECASE,
)

#: The record's files. Each task cites one of them, so none may go missing.
RECORD = (
    "README.md",
    "state-machine.md",
    "interruption-matrix.md",
    "interruption-matrix.json",
    "signals.md",
    "surfaces.md",
    "interactions.md",
    "motion.md",
    "copy.md",
    "sound.md",
    "settings.md",
    "character.md",
    "rig.md",
    "test-plan.md",
    "decisions/README.md",
)

#: Every anchor a buddy task cites in the record, as (file, anchor).
CITED_ANCHORS = (
    ("signals.md", "1-one-phase-contract"),
    ("signals.md", "2-one-list-of-what-is-waiting-on-the-owner"),
    ("signals.md", "3-one-list-of-what-is-running-now"),
    ("signals.md", "the-doorbell"),
    ("state-machine.md", "presence-rules"),
    ("state-machine.md", "the-alert-queue"),
    ("state-machine.md", "which-work-is-in-focus"),
    ("state-machine.md", "play-rules"),
    ("surfaces.md", "the-room-budget"),
    ("surfaces.md", "the-expanded-card-top-to-bottom"),
    ("surfaces.md", "many-pieces-of-work-at-once"),
    ("surfaces.md", "docking"),
    ("interactions.md", "the-drop-choice"),
    ("interactions.md", "quick-ask"),
    ("interactions.md", "keyboard-and-focus"),
    ("interactions.md", "screen-readers"),
    ("interactions.md", "pointer-and-click-through"),
    ("copy.md", "one-line-per-state"),
    ("test-plan.md", "acceptance-run"),
    ("rig.md", "starting-position"),
)

#: The README's two index headings, and the heading that opens a file's later-work part.
SHIPS = "Ships in v0.3.0"
LATER = "Later work"
#: The motion guide's section for the buddy's timings, and the columns each of its tables has.
TIMINGS = "9. The desktop buddy's timings"
TIMING_COLUMNS = ["Token", "Value", "What it times", "Read by", "Under reduced motion"]
#: The token of the buddy's warm-neutral body: the name the chosen character gives its light and
#: dark values under, and the renderer reads. Where each file says it, as (the file's name, the
#: heading it is under, a word only the entry about the body has under that heading).
BODY_TOKEN = "--color-buddy-body"
BODY_ENTRIES = (
    ("web/DESIGN.md", "Neutral", "buddy"),
    ("character.md", "Constraints", "warm-neutral"),
)

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_RULE = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")
_LINK = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)")
_TOKEN = re.compile(r"`([A-Za-z]+)`")
_COLOR = re.compile(r"`(--color-[a-z0-9-]+)`")
_TASK = re.compile(r"#\d+")

Table = tuple[str, str, str, list[str], list[list[str]]]


# ── markdown, read the way GitHub renders it ─────────────────────────────────────────────────


def _lines(text: str) -> Iterator[str]:
    """The lines of a markdown text outside fenced code."""
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced:
            yield line


def slug(title: str) -> str:
    """GitHub's anchor for a heading: its text in lower case, punctuation dropped, each space a
    hyphen. A code span or a link counts by its text."""
    text = re.sub(r"`([^`]*)`", r"\1", title)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def headings(text: str) -> list[tuple[int, str, str]]:
    """Every heading, as (level, title, anchor), in order. A repeated anchor gets ``-1``, ``-2``
    and on, as GitHub numbers them."""
    seen: dict[str, int] = {}
    found = []
    for line in _lines(text):
        match = _HEADING.match(line)
        if match:
            base = slug(match.group(2))
            count = seen.get(base, 0)
            seen[base] = count + 1
            found.append(
                (len(match.group(1)), match.group(2), base if count == 0 else f"{base}-{count}")
            )
    return found


def anchors(text: str) -> set[str]:
    return {anchor for _, _, anchor in headings(text)}


def sections(text: str) -> list[tuple[str, str, str]]:
    """A design file's sections, as (part, title, anchor): each h2 before the file's
    ``## Later work`` is a shipping section, and each h3 after it is a later-work section."""
    found = []
    later = False
    for level, title, anchor in headings(text):
        if level == 2 and title == LATER:
            later = True
        elif level == 2 and not later:
            found.append(("ship", title, anchor))
        elif level == 3 and later:
            found.append(("later", title, anchor))
    return found


def bullets(text: str, heading: str) -> list[str]:
    """The list items under the first heading titled *heading*, up to the next heading, each
    with its indented lines joined onto it."""
    found: list[str] = []
    inside = False
    for line in _lines(text):
        match = _HEADING.match(line)
        if match:
            if inside:
                break
            inside = match.group(2) == heading
        elif inside and line.startswith("- "):
            found.append(line[2:].strip())
        elif inside and found and line.startswith("  ") and line.strip():
            found[-1] += " " + line.strip()
    return found


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def tables(text: str) -> list[Table]:
    """Every table, as (part, the h2 above it, the heading right above it, header, rows). The
    part is ``later`` once the text has passed its ``## Later work`` heading."""
    lines = list(_lines(text))
    found: list[Table] = []
    part, section, title = "ship", "", ""
    at = 0
    while at < len(lines):
        line = lines[at]
        match = _HEADING.match(line)
        if match:
            title = match.group(2)
            if len(match.group(1)) == 2:
                section = title
                part = "later" if title == LATER or part == "later" else "ship"
        elif line.startswith("|") and at + 1 < len(lines) and _RULE.match(lines[at + 1]):
            header, rows = _cells(line), []
            at += 2
            while at < len(lines) and lines[at].startswith("|"):
                rows.append(_cells(lines[at]))
                at += 1
            found.append((part, section, title, header, rows))
            continue
        at += 1
    return found


# ── the matrix ───────────────────────────────────────────────────────────────────────────────


def load_matrix() -> dict[str, Any]:
    return json.loads(MATRIX_DATA.read_text(encoding="utf-8"))


def all_cells(data: dict[str, Any]) -> dict[tuple[str, str], str]:
    return {(s, e): words for s, row in data["cells"].items() for e, words in row.items()}


def short(label: str) -> str:
    """A situation's short name: the words before its label's colon. A table that lists cells
    from several situations names each by it."""
    return label.split(":", 1)[0]


def matrix_problems(data: dict[str, Any]) -> list[str]:
    """What is wrong with the data's shape and its cells, each a sentence."""
    if set(data) != KEYS:
        return [f"the data's keys are {sorted(data)}, not {sorted(KEYS)}"]
    found = []
    if tuple(data["situations"]) != SITUATIONS:
        found.append(f"the situations are {list(data['situations'])}, not {list(SITUATIONS)}")
    if tuple(data["events"]) != EVENTS:
        found.append(f"the events are {list(data['events'])}, not {list(EVENTS)}")
    for kind in ("situations", "events"):
        labels = list(data[kind].values())
        if not all(isinstance(label, str) and label.strip() for label in labels):
            found.append(f"one of the {kind} has no label")
        if len(set(labels)) != len(labels):
            found.append(f"two of the {kind} share a label")
    shorts = [short(label) for label in data["situations"].values()]
    if len(set(shorts)) != len(shorts):
        found.append("two situations share a short name")
    if tuple(data["cells"]) != SITUATIONS:
        found.append(f"the cells' situations are {list(data['cells'])}, not {list(SITUATIONS)}")
    cannot = 0
    for situation, row in data["cells"].items():
        if tuple(row) != EVENTS:
            found.append(f"{situation}: its events are {list(row)}, not the {len(EVENTS)} events")
        for event, words in row.items():
            if not isinstance(words, str) or not words.strip():
                found.append(f"{situation}/{event}: the outcome is empty")
            elif words == CANNOT:
                cannot += 1
            elif re.search(r"cannot\s+occur", words, re.IGNORECASE):
                found.append(f"{situation}/{event}: {words!r} is not the exact words {CANNOT!r}")
    if cannot != CANNOT_CELLS:
        found.append(f"{cannot} cells are {CANNOT!r}, not {CANNOT_CELLS}")
    return found


def table_problems(data: dict[str, Any], text: str) -> list[str]:
    """Where the table and the data disagree, each a sentence. Every cell is shown once, in the
    part it belongs to, in the data's words, and so is every later clause."""
    situation_by_label = {label: sid for sid, label in data["situations"].items()}
    situation_by_short = {short(label): sid for sid, label in data["situations"].items()}
    event_by_label = {label: eid for eid, label in data["events"].items()}
    later = {tuple(pair) for pair in data["later"]}
    found: list[str] = []
    shown: dict[tuple[str, str], str] = {}
    clauses: dict[tuple[str, str], str] = {}
    for part, _, title, header, rows in tables(text):
        if header == ["Event", "Outcome"]:
            if title not in situation_by_label:
                found.append(f"the table under {title!r} sits under no situation's label")
                continue
            entries = [(situation_by_label[title], row) for row in rows]
        elif header in (["Situation", "Event", "Outcome"], ["Situation", "Event", "Adds"]):
            entries = []
            for row in rows:
                if row[0] not in situation_by_short:
                    found.append(f"the table under {title!r} names no situation {row[0]!r}")
                else:
                    entries.append((situation_by_short[row[0]], row[1:]))
        else:
            found.append(f"the table under {title!r} has the header {header}, which is no matrix's")
            continue
        into = clauses if header[-1] == "Adds" else shown
        for situation, row in entries:
            if len(row) != 2 or row[0] not in event_by_label:
                found.append(f"{situation}: the row {row} names no event")
                continue
            cell = (situation, event_by_label[row[0]])
            name = "/".join(cell)
            if cell in into:
                found.append(f"{name}: shown twice")
                continue
            into[cell] = row[1]
            belongs = "later" if into is clauses or cell in later else "ship"
            if part != belongs:
                found.append(f"{name}: shown in the {part} part, and it belongs in the {belongs}")
    for cell, words in sorted(all_cells(data).items()):
        name = "/".join(cell)
        if cell not in shown:
            found.append(f"{name}: missing from the table")
        elif shown[cell] != words:
            found.append(f"{name}: the table says {shown[cell]!r}, the data {words!r}")
    wanted = {(s, e): words for s, row in data["later_clauses"].items() for e, words in row.items()}
    for cell in sorted(set(wanted) | set(clauses)):
        name = "/".join(cell)
        if cell not in clauses:
            found.append(f"{name}: its later clause is missing from the table")
        elif cell not in wanted:
            found.append(f"{name}: the table adds a later clause the data does not have")
        elif clauses[cell] != wanted[cell]:
            found.append(
                f"{name}: the table's clause is {clauses[cell]!r}, the data's {wanted[cell]!r}"
            )
    return found


def later_problems(data: dict[str, Any]) -> list[str]:
    """What is wrong with the later-work marks, each a sentence."""
    cells = all_cells(data)
    pairs = [tuple(pair) for pair in data["later"]]
    later = set(pairs)
    found = [f"later lists {list(pair)}, which is no cell" for pair in pairs if pair not in cells]
    if len(later) != len(pairs):
        found.append("later lists a cell twice")
    for (situation, event), words in sorted(cells.items()):
        name = f"{situation}/{event}"
        whole = situation in LATER_SITUATIONS or event in LATER_EVENTS
        named = bool(LATER_WORDS.search(words))
        if (situation, event) in later:
            if not whole and not named:
                found.append(f"{name}: in later, but {words!r} names no play, drop or quick ask")
        elif whole:
            group = situation if situation in LATER_SITUATIONS else event
            found.append(f"{name}: every {group} cell is later work, and this one is not in later")
        elif named:
            found.append(f"{name}: {words!r} names later work, and the cell is not in later")
    for situation, row in data["later_clauses"].items():
        for event, clause in row.items():
            name = f"{situation}/{event}"
            if (situation, event) not in cells:
                found.append(f"later_clauses names {name}, which is no cell")
            elif (situation, event) in later:
                found.append(f"{name}: a later cell gains no clause, its whole outcome waits")
            if not LATER_WORDS.search(clause):
                found.append(f"{name}: the clause {clause!r} names no play, drop or quick ask")
    return found


# ── the record around it ─────────────────────────────────────────────────────────────────────


def anchor_problems(folder: Path, cited: Iterable[tuple[str, str]]) -> list[str]:
    """Each cited anchor that no heading in its file has, as a sentence."""
    found = []
    for name, anchor in cited:
        path = folder / name
        if not path.is_file():
            found.append(f"{name}: no such file")
        elif anchor not in anchors(path.read_text(encoding="utf-8")):
            found.append(f"{name}#{anchor}: no heading in it has that anchor")
    return found


def index_problems(folder: Path) -> list[str]:
    """What the README's index misses, each a sentence. It links every file of the record and
    every section, a shipping section under its ``Ships in v0.3.0`` heading and a later-work
    section under its ``Later work`` heading, and every link it has resolves."""
    root = folder.resolve()
    text = (root / "README.md").read_text(encoding="utf-8")
    titles = [title for level, title, _ in headings(text) if level == 2]
    found = []
    if SHIPS not in titles or LATER not in titles or titles.index(SHIPS) > titles.index(LATER):
        found.append(f"the README has no {SHIPS!r} heading and a {LATER!r} heading after it")
    linked: set[tuple[str, str, str]] = set()
    section = ""
    for line in _lines(text):
        match = _HEADING.match(line)
        if match and len(match.group(1)) == 2:
            section = match.group(2)
        for target in _LINK.findall(line):
            if "://" in target or target.startswith(("#", "/", "mailto:")):
                continue
            place, _, anchor = target.partition("#")
            path = (root / place).resolve()
            if not path.is_relative_to(root):
                continue
            if not path.is_file():
                found.append(f"the README links {target}, which is no file")
            elif anchor and anchor not in anchors(path.read_text(encoding="utf-8")):
                found.append(f"the README links {target}, and no heading has that anchor")
            else:
                linked.add((section, path.relative_to(root).as_posix(), anchor))
    files = sorted(p.name for p in root.iterdir() if p.is_file() and p.name != "README.md")
    for name in [*files, "decisions/README.md"]:
        if not any(place == name for _, place, _ in linked):
            found.append(f"the README does not link {name}")
    for name in files:
        if not name.endswith(".md"):
            continue
        for part, title, anchor in sections((root / name).read_text(encoding="utf-8")):
            under = SHIPS if part == "ship" else LATER
            if (under, name, anchor) not in linked:
                found.append(f"the README's {under!r} index does not link {name}#{anchor}")
    return found


def task_line_problems(folder: Path) -> list[str]:
    """Each later-work section that does not open with a line naming its task, and each part of
    a file that follows its later work, as a sentence."""
    paths = sorted(path for path in folder.glob("*.md") if path.name != "README.md")
    found = [] if paths else [f"{folder} holds no design file"]
    for path in paths:
        lines = list(_lines(path.read_text(encoding="utf-8")))
        later = False
        for at, line in enumerate(lines):
            match = _HEADING.match(line)
            if not match:
                continue
            level, title = len(match.group(1)), match.group(2)
            if level == 2 and later:
                found.append(f"{path.name}: {title!r} follows its later work, which comes last")
            elif level == 2:
                later = title == LATER
            elif level == 3 and later:
                first = next((text for text in lines[at + 1 :] if text.strip()), "")
                if not (first.startswith("Later work") and _TASK.search(first)):
                    found.append(
                        f"{path.name}: {title!r} does not open with a line naming its task"
                    )
    return found


def timing_problems(machine: str, guide: str) -> list[str]:
    """Where the state machine's timing names and the motion guide's values disagree, each a
    sentence. The names are the backticked tokens in the first column of every table under a
    heading ending in "timing names"; every one has exactly one full row in the guide's §9."""
    named = {
        token
        for _, _, title, _, rows in tables(machine)
        if title.lower().endswith("timing names")
        for row in rows
        for token in _TOKEN.findall(row[0])
    }
    found = [] if named else ["the state machine names no timing"]
    rows: dict[str, list[str]] = {}
    for _, section, title, header, table in tables(guide):
        if section != TIMINGS:
            continue
        if header != TIMING_COLUMNS:
            found.append(f"a table under {title!r} has the columns {header}, not {TIMING_COLUMNS}")
            continue
        for row in table:
            tokens = _TOKEN.findall(row[0])
            if len(tokens) != 1:
                found.append(f"the row {row[0]!r} names {len(tokens)} tokens, not one")
            elif tokens[0] in rows:
                found.append(f"`{tokens[0]}` has two rows")
            else:
                rows[tokens[0]] = row
                if len(row) != len(TIMING_COLUMNS) or not all(row):
                    found.append(f"`{tokens[0]}`: a cell of its row is empty")
    found += [f"`{token}` is named, and has no row in §9" for token in sorted(named - set(rows))]
    found += [f"`{token}` has a row in §9, and is not named" for token in sorted(set(rows) - named)]
    return found


def body_token_problems(texts: dict[str, str]) -> list[str]:
    """Where the buddy's body colour goes without its token's name, or under another, each a
    sentence. *texts* holds each file of ``BODY_ENTRIES`` by its name: in each, the entry about
    the body names one ``--color-`` token, and that token is ``BODY_TOKEN``."""
    found = []
    for name, heading, word in BODY_ENTRIES:
        entries = [item for item in bullets(texts[name], heading) if word in item.lower()]
        named = sorted({token for item in entries for token in _COLOR.findall(item)})
        if len(entries) != 1:
            found.append(
                f"{name}: {len(entries)} entries under {heading!r} are the body's, not one"
            )
        elif named != [BODY_TOKEN]:
            found.append(f"{name}: the buddy's body goes by {named}, not by `{BODY_TOKEN}`")
    return found


def _body_texts() -> dict[str, str]:
    return {
        "web/DESIGN.md": DESIGN.read_text(encoding="utf-8"),
        "character.md": CHARACTER.read_text(encoding="utf-8"),
    }


# ── the record as committed ──────────────────────────────────────────────────────────────────


def test_every_file_of_the_record_exists() -> None:
    assert [name for name in RECORD if not (FOLDER / name).is_file()] == []


def test_the_matrix_is_every_situation_by_every_event() -> None:
    assert matrix_problems(load_matrix()) == []


def test_the_table_says_what_the_data_says_cell_for_cell() -> None:
    assert table_problems(load_matrix(), MATRIX_TABLE.read_text(encoding="utf-8")) == []


def test_later_work_is_marked_and_no_shipping_cell_names_it() -> None:
    assert later_problems(load_matrix()) == []


def test_every_anchor_a_buddy_task_cites_resolves() -> None:
    assert len(CITED_ANCHORS) == 20, "an anchor was dropped from the table"
    assert anchor_problems(FOLDER, CITED_ANCHORS) == []


def test_the_readme_indexes_every_file_and_section_under_its_part() -> None:
    assert index_problems(FOLDER) == []


def test_each_later_work_section_opens_with_a_line_naming_its_task() -> None:
    assert task_line_problems(FOLDER) == []


def test_every_named_timing_has_a_value_a_reader_and_a_reduced_motion_answer() -> None:
    machine = STATE_MACHINE.read_text(encoding="utf-8")
    assert timing_problems(machine, MOTION_GUIDE.read_text(encoding="utf-8")) == []


def test_the_buddys_body_token_is_named_before_its_values() -> None:
    assert body_token_problems(_body_texts()) == []


def test_anchors_follow_githubs_rule() -> None:
    assert slug("1. One phase contract") == "1-one-phase-contract"
    assert slug("The expanded card, top to bottom") == "the-expanded-card-top-to-bottom"
    assert slug("Pointer and click-through") == "pointer-and-click-through"
    assert slug("9. The desktop buddy's timings") == "9-the-desktop-buddys-timings"
    assert slug("The `gaze` spring") == "the-gaze-spring"
    assert anchors("## Rules\n\n```\n## Not a heading\n```\n\n### Rules\n") == {"rules", "rules-1"}


# ── each check, shown to fire ────────────────────────────────────────────────────────────────


def _edited(edit: Callable[[dict[str, Any]], object]) -> dict[str, Any]:
    data = copy.deepcopy(load_matrix())
    edit(data)
    return data


def _body_texts_with(name: str, token: str) -> dict[str, str]:
    """The body's files as committed, with *name* calling the body's token *token* instead."""
    texts = _body_texts()
    texts[name] = texts[name].replace(f"`{BODY_TOKEN}`", token)
    return texts


def _copy_of_the_record(tmp_path: Path, name: str, extra: str) -> Path:
    """A copy of the record with *extra* appended to *name*."""
    folder = tmp_path / "buddy"
    shutil.copytree(FOLDER, folder)
    path = folder / name
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    return folder


def test_each_check_reports_a_planted_defect(subtests: Any, tmp_path: Path) -> None:
    data = load_matrix()
    table = MATRIX_TABLE.read_text(encoding="utf-8")
    machine = STATE_MACHINE.read_text(encoding="utf-8")
    guide = MOTION_GUIDE.read_text(encoding="utf-8")
    control = f"| {data['events']['start']} | {data['cells']['compact']['start']} |"
    assert control in table, "the control's row is no longer in the table"
    planted: list[tuple[str, Callable[[], list[str]], str]] = [
        (
            "a situation lost an event",
            lambda: matrix_problems(_edited(lambda d: d["cells"]["peek"].pop("drag"))),
            "peek: its events are",
        ),
        (
            "a cell says it cannot occur in other words",
            lambda: matrix_problems(
                _edited(lambda d: d["cells"]["offline"].update(start="Cannot occur here."))
            ),
            "offline/start: 'Cannot occur here.' is not the exact words",
        ),
        (
            "one more cell cannot occur",
            lambda: matrix_problems(_edited(lambda d: d["cells"]["compact"].update(start=CANNOT))),
            "57 cells are",
        ),
        (
            "one cell changed in the data alone",
            lambda: table_problems(
                _edited(lambda d: d["cells"]["compact"].update(start="compact")), table
            ),
            "compact/start: the table says",
        ),
        (
            "one cell changed in the table alone",
            lambda: table_problems(
                data, table.replace(control, f"| {data['events']['start']} | x |")
            ),
            "compact/start: the table says 'x'",
        ),
        (
            "a play outcome planted outside later",
            lambda: later_problems(
                _edited(lambda d: d["cells"]["compact"].update(hover="play: poked"))
            ),
            "compact/hover: 'play: poked' names later work",
        ),
        (
            "a file cell dropped from later",
            lambda: later_problems(_edited(lambda d: d["later"].remove(["peek", "file"]))),
            "peek/file: every file cell is later work",
        ),
        (
            "a shipping cell hidden in later",
            lambda: later_problems(_edited(lambda d: d["later"].append(["compact", "start"]))),
            "compact/start: in later, but",
        ),
        (
            "a cited anchor with no heading",
            lambda: anchor_problems(FOLDER, [("signals.md", "no-such-heading")]),
            "signals.md#no-such-heading: no heading",
        ),
        (
            "a section the README does not index",
            lambda: index_problems(
                _copy_of_the_record(tmp_path / "index", "copy.md", "\n## A new section\n")
            ),
            "does not link copy.md#a-new-section",
        ),
        (
            "a later section that names no task",
            lambda: task_line_problems(
                _copy_of_the_record(
                    tmp_path / "task", "copy.md", "\n## Later work\n\n### An idea\n\nSoon.\n"
                )
            ),
            "copy.md: 'An idea' does not open with a line naming its task",
        ),
        (
            "a timing named with no value",
            lambda: timing_problems(
                machine + "\n### Planted timing names\n\n| Token | Meaning |\n|---|---|\n"
                "| `plantedWait` | a wait |\n",
                guide,
            ),
            "`plantedWait` is named, and has no row in §9",
        ),
        (
            "the body's token left unnamed until its values come",
            lambda: body_token_problems(_body_texts_with("web/DESIGN.md", "its token")),
            "web/DESIGN.md: the buddy's body goes by [], not by",
        ),
        (
            "the brief calling the body's token by another name",
            lambda: body_token_problems(_body_texts_with("character.md", "`--color-buddy-shell`")),
            "character.md: the buddy's body goes by ['--color-buddy-shell']",
        ),
    ]
    assert len(planted) == 14, "a planted defect was dropped from the table"
    for name, check, expected in planted:
        with table_rows.row(subtests, defect=name):
            reported = check()
            assert any(expected in line for line in reported), (name, reported)
