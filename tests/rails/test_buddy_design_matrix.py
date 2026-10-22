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
* **The buddy has no colour of its own.** Its orb takes the agent orb's colour, the scheme's
  primary, while an agent is alive, and a neutral the design system already has where nothing
  is. ``web/DESIGN.md`` says so among the neutral colours and the character brief among
  its constraints, each pointing at the agent orb's design and naming only tokens ``tokens.css``
  defines; no file of the record, nor ``web/DESIGN.md``, names a colour token of the buddy's own.
* **What the buddy shares with the agent orb, and what it adds, each have one home.** The agent
  orb's design says what the two share, and the rig's candidates list what the buddy adds. The
  brief's first constraint links both, and the originality checks in the brief and in the test
  plan link the agent orb's component, whose dots the buddy shares on purpose. None of them keeps
  a list of its own, which would drift from the one it copies.
* **Every pose is drawn, and only the poses the brief names.** The census reads the v0.3.0 set
  from the brief's own words (the family table's phases, the focused card's orb for each family,
  presence, the answers, and the tray's and the dock's stills at each size, with their template
  versions) and holds ``poses/`` to it: a source for each, none for anything else, and a motion
  frame beside each pose that moves, whose ``.svg`` is its reduced-motion still.
* **Each source is drawn in parts a renderer moves, in the tokens, and round.** One whole orb with
  its pivot holds the dots, the badge and the wash; every colour is a token ``tokens.css`` has (a
  template's is black, which the menu bar recolours); and no transform squashes or stretches it.
* **At 18 px the four families are told apart** by the orb's density and arrangement and by its
  badge, in colour and as templates, and every tray still as a template differs from the others
  by its badge or its dots. Waiting's badge holds it apart from active by its weight too: drawn
  at full strength, with several times the ink of active's mark, so a still waiting orb (the
  tray's, or under reduced motion) never reads as an active one. The contact sheets show every
  pose at 18, 24, 48 and 96 px and every still at its size, in light and dark, each with its words
  beside it, for the owner to look at.

Each check returns what is wrong, as sentences. The last test plants one defect for each check and
sees it reported, so none of them can pass by looking at nothing.
"""

from __future__ import annotations

import collections
import copy
import itertools
import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

import source_index
import table_rows
from rails_repo import REPO

FOLDER = REPO / "docs" / "design" / "buddy"
MATRIX_DATA = FOLDER / "interruption-matrix.json"
MATRIX_TABLE = FOLDER / "interruption-matrix.md"
STATE_MACHINE = FOLDER / "state-machine.md"
MOTION_GUIDE = REPO / "docs" / "design" / "motion.md"
DESIGN = REPO / "web" / "DESIGN.md"
TOKENS = REPO / "web" / "src" / "design" / "tokens.css"

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
#: The buddy's orb takes the agent orb's colour while an agent is alive, and mints none of its
#: own. Where each file says so, as (the file's name, the heading it is under, a word only the
#: entry about the buddy's colours has under that heading). Each entry links the agent orb's
#: design.
COLOUR_ENTRIES = (
    ("web/DESIGN.md", "Neutral", "buddy"),
    ("character.md", "Constraints", "colours"),
)
AGENT_ORB = "agent-orb.md"
#: A colour token of the buddy's own, under any name.
OWN_COLOUR = re.compile(r"--color-buddy[a-z0-9-]*")
#: The entries that weigh the buddy against the agent orb, as (the file's name, the heading it is
#: under, words only that entry has under that heading, the links it must have, as written).
COMPARISONS = (
    (
        "character.md",
        "Constraints",
        "drawn the way the agent orb is",
        ("../agent-orb.md#phase-to-state", "rig.md#candidates"),
    ),
    ("character.md", "Constraints", "**original:**", ("../agent-orb.md#the-component",)),
    ("test-plan.md", "Pixels", "side by side", ("../agent-orb.md#the-component",)),
)

#: The drawn poses: a source for every pose and still the brief's v0.3.0 set names, rendered from
#: the pose table (``web/src/buddyRig/poseSet.ts``) on the agent orb's drawing, with the contact
#: sheets in ``sheets/`` beside them. A pose's ``.svg`` is its reduced-motion still, and a pose
#: that moves has a ``.motion.svg`` too, the instant its motion is drawn at.
POSES = FOLDER / "poses"
CHARACTER = FOLDER / "character.md"
POSES_HEADING = "The poses"
FAMILIES = ("quiet", "active", "waiting", "blocked")
#: The brief's lists of the rest of the set, by the bold words each opens with.
CARD = "The focused status card's orb,"
LISTS = (("Presence:", "presence"), ("Answers:", "answer"))
STILLS = "Still orbs for the tray and the dock:"
MOTION = ".motion.svg"
#: The sizes the contact sheet draws every pose at, and its two modes.
SHEET_SIZES = (18, 24, 48, 96)
MODES = ("light", "dark")
#: The size the four families are told apart at by the orb's density and arrangement alone.
SMALLEST = 18
#: How far apart two orbs' dots are to be told apart: this many times as many dots, or this share
#: of the orb's size in how far they reach from its centre or where their middle sits.
DENSITY_APART = 1.25
ARRANGEMENT_APART = 0.05
#: How many times the ink of active's badge waiting's carries at the smallest size: its badge's
#: dots' area, measured on the drawing.
BADGE_APART = 4.0
#: The properties that carry a colour, and the one ink a template image is drawn in, which the
#: menu bar recolours.
COLOUR_PROPERTIES = ("fill", "stroke", "stop-color", "color")
TEMPLATE_INK = "black"

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_RULE = re.compile(r"^\|(\s*:?-+:?\s*\|)+\s*$")
_LINK = re.compile(r"\[[^\]]*\]\(\s*([^)\s]+)")
_TOKEN = re.compile(r"`([A-Za-z]+)`")
_COLOR = re.compile(r"`(--color-[a-z0-9-]+)`")
_DEFINED = re.compile(r"^\s*(--color-[a-z0-9-]+)\s*:", re.MULTILINE)
_TASK = re.compile(r"#\d+")
_PAREN = re.compile(r"\s*\([^)]*\)")
_ITEMS = re.compile(r",\s*|\s+and\s+")
_BELONGS = re.compile(r"(.+?) (?:is|are) (\w+)")
_VAR = re.compile(r"^var\((--color-[a-z0-9-]+)\)$")
#: A transform that keeps the orb round: moves, turns, and scales that take one factor.
_ROUND = re.compile(r"^(?:\s*(?:translate\([^)]*\)|rotate\([^)]*\)|scale\(\s*[-+\d.e]+\s*\)))*\s*$")

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
    return json.loads(source_index.text(MATRIX_DATA))


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


def colour_problems(texts: dict[str, str], defined: set[str]) -> list[str]:
    """Where the buddy's colours are not the agent orb's, each a sentence. *texts* holds the
    record's files and ``web/DESIGN.md`` by name, and none of them names a colour token of the
    buddy's own. In each file of ``COLOUR_ENTRIES``, one entry speaks of the buddy's colours: it
    links the agent orb's design, and every ``--color-`` token it names is in *defined*, the
    tokens ``tokens.css`` defines."""
    found = [
        f"{name}: the buddy names a colour of its own, {sorted(set(OWN_COLOUR.findall(text)))}"
        for name, text in texts.items()
        if OWN_COLOUR.search(text)
    ]
    for name, heading, word in COLOUR_ENTRIES:
        entries = [item for item in bullets(texts[name], heading) if word in item.lower()]
        if len(entries) != 1:
            found.append(
                f"{name}: {len(entries)} entries under {heading!r} are the buddy's colours, not one"
            )
            continue
        targets = [target.partition("#")[0] for target in _LINK.findall(entries[0])]
        if not any(Path(target).name == AGENT_ORB for target in targets):
            found.append(f"{name}: the buddy's colours do not point at the agent orb's {AGENT_ORB}")
        unknown = sorted(set(_COLOR.findall(entries[0])) - defined)
        if unknown:
            found.append(f"{name}: the buddy's colours name {unknown}, which tokens.css lacks")
    return found


def comparison_problems(texts: dict[str, str]) -> list[str]:
    """Where an entry that weighs the buddy against the agent orb does not point at the places
    that say what the two share and what the buddy adds, each a sentence. *texts* holds the
    record's files by name. Each entry of ``COMPARISONS`` is the one item under its heading with
    its words, and it has every link the table names."""
    found = []
    for name, heading, words, wanted in COMPARISONS:
        entries = [item for item in bullets(texts[name], heading) if words in item.lower()]
        if len(entries) != 1:
            found.append(f"{name}: {len(entries)} entries under {heading!r} say {words!r}, not one")
            continue
        links = set(_LINK.findall(entries[0]))
        found += [
            f"{name}: the entry that says {words!r} does not link {link}"
            for link in wanted
            if link not in links
        ]
    return found


def _record_texts() -> dict[str, str]:
    texts = {name: source_index.text(FOLDER / name) for name in RECORD}
    texts["web/DESIGN.md"] = DESIGN.read_text(encoding="utf-8")
    return texts


def _defined_colours() -> set[str]:
    return set(_DEFINED.findall(TOKENS.read_text(encoding="utf-8")))


# ── the poses, as drawn ──────────────────────────────────────────────────────────────────────


class Named(NamedTuple):
    """A source the brief's v0.3.0 set names. A *pose* is drawn at the contact sheet's four sizes;
    a *still* is one of the tray's and the dock's, at one size, in colour or as a template."""

    kind: str
    family: str
    size: int = 0
    template: bool = False


class Look(NamedTuple):
    """What a drawn orb shows at a glance: how many dots, how far they reach from the orb's
    centre and how far their middle sits below it (each in orb sizes), the badge's mark, the ink
    its badge carries (its dots' area, in orb sizes squared), and whether any of the badge is
    drawn under full strength."""

    dots: int
    reach: float
    height: float
    badge: str
    badge_ink: float = 0.0
    badge_faint: bool = False


def _slug(words: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", words.strip().lower()).strip("-")


def _named(text: str) -> list[str]:
    """The names a list of the brief gives, to the end of its first sentence: each parenthesis
    dropped, split at commas and at "and", and a leading "the" dropped."""
    first = _PAREN.sub("", text).split(". ")[0].rstrip(".")
    return [_slug(re.sub(r"^the\s+", "", part)) for part in _ITEMS.split(first) if part.strip()]


def _labelled(items: list[str], label: str) -> str | None:
    """The text after *label*, the bold words one list item opens with, or ``None``."""
    found = [item[len(label) + 4 :] for item in items if item.startswith(f"**{label}**")]
    return found[0].strip() if len(found) == 1 else None


def brief_sources(brief: str) -> tuple[dict[str, Named], list[str]]:
    """Every source the brief's v0.3.0 set names, by its file's stem, and each part of the set the
    brief no longer says in a way this can read, as a sentence. Read from the brief's own words
    under "The poses": the family table's phases; the focused card's orb for each family; the
    presence and answer lists; and the tray's and the dock's stills at each size it gives, with a
    template version of each when it asks for them."""
    found: list[str] = []
    rows = [
        row
        for _, section, _, header, table in tables(brief)
        if section == POSES_HEADING and header[:2] == ["Family", "Phases"]
        for row in table
    ]
    families = [row[0].lower() for row in rows]
    if families != list(FAMILIES):
        found.append(f"the brief's family table lists {families}, not the four families")
    named: dict[str, Named] = {}
    for row in rows:
        for phase in row[1].split(", "):
            named[f"{row[0].lower()}-{_slug(phase)}"] = Named("pose", row[0].lower())
    items = bullets(brief, POSES_HEADING)
    card = _labelled(items, CARD)
    if card is None or "for each family" not in card:
        found.append(f"the brief no longer gives {CARD!r} for each family")
    else:
        named.update({f"card-{family}": Named("pose", family) for family in families})
    for label, prefix in LISTS:
        text = _labelled(items, label)
        if text is None:
            found.append(f"the brief no longer lists {label!r}")
            continue
        named.update({f"{prefix}-{name}": Named("pose", "") for name in _named(text)})
    text = _labelled(items, STILLS)
    if text is None or ", at " not in text:
        found.append(f"the brief no longer lists {STILLS!r} with their sizes")
        return named, found
    stills, _, rest = text.partition(", at ")
    sizes = [int(size) for size in re.findall(r"\d+", rest.split(" px")[0])]
    template = "template" in rest.split(". ")[0]
    of: dict[str, str] = {}
    for clause in rest.partition("pose: ")[2].split(". ")[0].split("; "):
        match = _BELONGS.match(clause.strip())
        if match:
            of.update({_slug(name): match.group(2) for name in _ITEMS.split(match.group(1))})
    for still in [_slug(name) for name in stills.split(", ")]:
        if still not in of:
            found.append(f"the brief does not say which family the {still} still belongs to")
        for size in sizes:
            for flat in (False, True) if template else (False,):
                stem = f"tray-{still}-{size}" + ("-template" if flat else "")
                named[stem] = Named("still", of.get(still, ""), size, flat)
    return named, found


def _svg(path: Path) -> ET.Element:
    return ET.fromstring(path.read_text(encoding="utf-8"))


def _local(element: ET.Element) -> str:
    return element.tag.rpartition("}")[2]


def _parts(root: ET.Element, part: str) -> list[ET.Element]:
    return [element for element in root.iter() if element.get("data-part") == part]


def _stem(name: str) -> tuple[str, bool]:
    """A source file's pose and whether it is the pose's motion frame."""
    if name.endswith(MOTION):
        return name[: -len(MOTION)], True
    return name.removesuffix(".svg"), False


def census_problems(brief: str, folder: Path) -> list[str]:
    """Where the drawn sources and the brief's v0.3.0 set disagree, each a sentence: a pose or a
    still the brief names with no source, a source of a pose the brief does not name, a source
    that says it is another, and a pose that moves without its motion frame (or a motion frame
    beside a pose that holds still). Every pose's ``.svg`` is its reduced-motion still."""
    named, found = brief_sources(brief)
    files = sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
    have = set(files)
    for stem in sorted(named):
        if f"{stem}.svg" not in have:
            found.append(f"{stem}: the brief names it, and it has no source")
    for name in files:
        stem, motion = _stem(name)
        if not name.endswith(".svg"):
            found.append(f"poses/{name} is no pose source: every source is an SVG")
            continue
        if stem not in named:
            found.append(f"poses/{name} is a pose the brief does not name")
            continue
        root = _svg(folder / name)
        frame = "motion" if motion else "still"
        if root.get("data-pose") != stem or root.get("data-frame") != frame:
            found.append(
                f"poses/{name} says it is {root.get('data-pose')}'s {root.get('data-frame')} frame"
            )
        if motion:
            continue
        moves = root.get("data-moves") == "true"
        if moves and named[stem].kind == "still":
            found.append(f"poses/{name}: a tray or dock still never moves")
        elif moves and f"{stem}{MOTION}" not in have:
            found.append(f"{stem} moves, and has no motion frame")
        elif not moves and f"{stem}{MOTION}" in have:
            found.append(f"poses/{stem}{MOTION}: {stem} holds still, so it has no motion frame")
    return found


def source_problems(folder: Path, defined: set[str]) -> list[str]:
    """Where a source is not drawn in the parts a renderer moves, keeps a colour out of the
    tokens, or squashes the orb, each a sentence. A source has one whole orb, with its pivot and
    size, holding its dots (circles, and the lines of a drawing that has them), its badge when it
    has one and its wash; a template has no wash, since one colour cannot show it. Every colour is
    a ``--color-`` token ``tokens.css`` defines, set in a style, and a template's is black, which
    the menu bar recolours. Every transform keeps the orb round: a move, a turn, or one scale for
    both axes, and every dot is a circle."""
    paths = sorted(folder.glob("*.svg"))
    found: list[str] = [] if paths else [f"{folder.name}/ holds no source"]
    for path in paths:
        name = f"poses/{path.name}"
        root = _svg(path)
        template = root.get("data-template") == "true"
        orbs, dots = _parts(root, "orb"), _parts(root, "dots")
        if len(orbs) != 1 or len(dots) != 1 or dots[0] not in list(orbs[0].iter()):
            found.append(f"{name}: not one whole orb holding its dots")
        elif len((orbs[0].get("data-pivot") or "").split()) != 2 or not orbs[0].get("data-orb"):
            found.append(f"{name}: its whole orb names no pivot and size")
        if dots and {_local(e) for e in dots[0].iter()} - {"g", "circle", "line"}:
            found.append(f"{name}: its dots hold something other than dots and lines")
        if bool(root.get("data-badge")) != bool(_parts(root, "badge")):
            found.append(f"{name}: its badge and the badge it says it has disagree")
        if template and _parts(root, "wash"):
            found.append(f"{name}: a template has no wash")
        for element in root.iter():
            if _local(element) == "ellipse":
                found.append(f"{name}: draws an ellipse, and the orb's dots are round")
            for attr in COLOUR_PROPERTIES:
                if element.get(attr) is not None:
                    found.append(f"{name}: names a colour as its {attr} attribute, not a style")
            for declaration in (element.get("style") or "").split(";"):
                prop, _, value = (part.strip() for part in declaration.partition(":"))
                if prop not in COLOUR_PROPERTIES:
                    continue
                token = _VAR.match(value)
                if template and value != TEMPLATE_INK:
                    found.append(f"{name}: a template is drawn in {TEMPLATE_INK}, not {value!r}")
                elif not template and not (token and token.group(1) in defined):
                    found.append(f"{name}: its {prop} is {value!r}, not a token tokens.css has")
            transform = element.get("transform")
            if transform is not None and not _ROUND.match(transform):
                found.append(f"{name}: {transform!r} squashes or stretches the orb")
    return found


def look_of(path: Path) -> Look:
    """What the source at *path* shows at a glance, measured from its parts."""
    root = _svg(path)
    orb = _parts(root, "orb")[0]
    cx, cy = (float(v) for v in (orb.get("data-pivot") or "0 0").split())
    size = float(orb.get("data-orb") or 1)
    dots = [
        (float(e.get("cx", 0)), float(e.get("cy", 0)))
        for e in _parts(root, "dots")[0].iter()
        if _local(e) == "circle"
    ]
    badge = [e for part in _parts(root, "badge") for e in part.iter()]
    ink = sum(math.pi * float(e.get("r", 0)) ** 2 for e in badge if _local(e) == "circle") / size**2
    faint = any(float(e.get("opacity", 1)) < 1 for e in badge)
    mark = root.get("data-badge") or ""
    if not dots:
        return Look(0, 0.0, 0.0, mark, ink, faint)
    reach = sum(((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in dots) / len(dots) / size
    height = (sum(y for _, y in dots) / len(dots) - cy) / size
    return Look(len(dots), reach, height, mark, ink, faint)


def _apart(a: Look, b: Look) -> bool:
    """Whether two orbs differ by their dots' density or arrangement."""
    dense = max(a.dots, b.dots) >= DENSITY_APART * max(1, min(a.dots, b.dots))
    arranged = max(abs(a.reach - b.reach), abs(a.height - b.height)) >= ARRANGEMENT_APART
    return dense or arranged


def small_size_problems(brief: str, folder: Path) -> list[str]:
    """Where two families' stills can be mistaken at the tray's smallest sizes, each a sentence.
    At 18 px each family's first still in the brief (in colour and as a template) differs from
    each other family's by its dots' density or arrangement, and by its badge, and waiting's
    badge is drawn at full strength with ``BADGE_APART`` times the ink of active's. As templates,
    at every size, each of the stills differs from every other by its badge or its dots, since a
    template carries the state in shape alone."""
    named, found = brief_sources(brief)
    stills = {stem: n for stem, n in named.items() if n.kind == "still"}
    for flat in (False, True):
        picks: dict[str, str] = {}
        for stem, n in stills.items():
            if n.size == SMALLEST and n.template == flat:
                picks.setdefault(n.family, stem)
        as_what = " as templates" if flat else ""
        if sorted(picks) != sorted(FAMILIES):
            found.append(f"at {SMALLEST} px{as_what}, the stills cover {sorted(picks)}")
            continue
        absent = [stem for stem in picks.values() if not (folder / f"{stem}.svg").is_file()]
        if absent:
            found.append(f"at {SMALLEST} px{as_what}, there is nothing to measure for {absent}")
            continue
        looks = {family: look_of(folder / f"{picks[family]}.svg") for family in FAMILIES}
        for a, b in itertools.combinations(FAMILIES, 2):
            if not _apart(looks[a], looks[b]):
                found.append(
                    f"at {SMALLEST} px{as_what}, {a} and {b} have the same density and arrangement"
                )
            if looks[a].badge == looks[b].badge:
                found.append(f"at {SMALLEST} px{as_what}, {a} and {b} have the same badge")
        waiting, active = looks["waiting"], looks["active"]
        if waiting.badge_faint:
            found.append(f"at {SMALLEST} px{as_what}, waiting's badge is drawn under full strength")
        if waiting.badge_ink < BADGE_APART * active.badge_ink:
            weight = waiting.badge_ink / active.badge_ink if active.badge_ink else 0.0
            found.append(
                f"at {SMALLEST} px{as_what}, waiting's badge carries {weight:.1f} times the ink of"
                f" active's, under {BADGE_APART:g}: a still waiting orb reads as an active one"
            )
    sizes = sorted({n.size for n in stills.values()})
    for size in sizes:
        drawn = {
            stem: look_of(folder / f"{stem}.svg")
            for stem, n in stills.items()
            if n.template and n.size == size and (folder / f"{stem}.svg").is_file()
        }
        for a, b in itertools.combinations(sorted(drawn), 2):
            if drawn[a].badge == drawn[b].badge and not _apart(drawn[a], drawn[b]):
                found.append(f"as templates at {size} px, {a} and {b} look the same")
    return found


def sheet_problems(brief: str, folder: Path) -> list[str]:
    """Where the contact sheets do not show what the brief names, each a sentence: every pose in
    light and dark at 18, 24, 48 and 96 px, and every still in light and dark at its own size,
    each with its words beside it; and nothing the brief does not name."""
    named, found = brief_sources(brief)
    cells: dict[str, set[tuple[str, int]]] = {}
    for path in sorted((folder / "sheets").glob("*.svg")):
        for element in _svg(path).iter():
            pose = element.get("data-sheet-pose")
            if pose is None:
                continue
            words = element.get("data-words") or ""
            mode, size = element.get("data-mode") or "", int(element.get("data-size") or 0)
            texts = [(t.text or "").strip() for t in element.iter() if _local(t) == "text"]
            if not words or words not in texts:
                found.append(f"sheets/{path.name}: {pose} at {size} px in {mode} has no words")
            cells.setdefault(pose, set()).add((mode, size))
    for stem, n in sorted(named.items()):
        sizes = SHEET_SIZES if n.kind == "pose" else (n.size,)
        missing = sorted(
            {(mode, size) for mode in MODES for size in sizes} - cells.get(stem, set()),
            key=lambda cell: (cell[1], cell[0]),
        )
        if missing:
            shown = ", ".join(f"{size} px in {mode}" for mode, size in missing)
            found.append(f"the contact sheet does not show {stem} at {shown}")
    found += [
        f"the contact sheet shows {pose}, which the brief does not name"
        for pose in sorted(set(cells) - set(named))
    ]
    return found


# ── the record as committed ──────────────────────────────────────────────────────────────────


def test_every_file_of_the_record_exists() -> None:
    assert [name for name in RECORD if not (FOLDER / name).is_file()] == []


def test_the_matrix_is_every_situation_by_every_event() -> None:
    assert matrix_problems(load_matrix()) == []


def test_the_table_says_what_the_data_says_cell_for_cell() -> None:
    assert table_problems(load_matrix(), source_index.text(MATRIX_TABLE)) == []


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
    machine = source_index.text(STATE_MACHINE)
    assert timing_problems(machine, source_index.text(MOTION_GUIDE)) == []


def test_the_buddy_takes_the_agent_orbs_colour_and_names_none_of_its_own() -> None:
    defined = _defined_colours()
    assert {"--color-primary", "--color-on-surface-var"} <= defined, "tokens.css was misread"
    assert colour_problems(_record_texts(), defined) == []


def test_the_buddy_points_at_what_it_shares_with_the_agent_orb_and_what_it_adds() -> None:
    wanted = sorted({link.partition("#") for *_, links in COMPARISONS for link in links})
    assert anchor_problems(FOLDER, [(place, anchor) for place, _, anchor in wanted]) == []
    assert comparison_problems(_record_texts()) == []


def test_every_pose_and_still_the_brief_names_has_a_source_and_no_other() -> None:
    brief = source_index.text(CHARACTER)
    named, unread = brief_sources(brief)
    assert unread == []
    kinds = collections.Counter(n.kind for n in named.values())
    assert kinds == {"pose": 15 + 4 + 7 + 3, "still": 6 * 3 * 2}, "the brief was misread"
    assert census_problems(brief, POSES) == []


def test_every_source_is_drawn_in_parts_in_the_tokens_and_never_squashes_the_orb() -> None:
    assert source_problems(POSES, _defined_colours()) == []


def test_at_18_px_the_four_families_differ_by_density_arrangement_and_badge() -> None:
    assert small_size_problems(source_index.text(CHARACTER), POSES) == []


def test_the_contact_sheet_shows_every_pose_at_its_sizes_in_light_and_dark_with_words() -> None:
    assert sheet_problems(source_index.text(CHARACTER), POSES) == []


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


def _record_texts_with(name: str, old: str, new: str) -> dict[str, str]:
    """The record's texts as committed, with every *old* in *name* replaced by *new*."""
    texts = _record_texts()
    assert old in texts[name], f"{old!r} is no longer in {name}, so nothing would be planted"
    texts[name] = texts[name].replace(old, new)
    return texts


def _copy_of_the_record(tmp_path: Path, name: str, extra: str) -> Path:
    """A copy of the record with *extra* appended to *name*."""
    folder = tmp_path / "buddy"
    shutil.copytree(FOLDER, folder)
    path = folder / name
    path.write_text(path.read_text(encoding="utf-8") + extra, encoding="utf-8")
    return folder


def _copy_of_the_poses(tmp_path: Path, edit: Callable[[Path], object]) -> Path:
    """A copy of the drawn poses with *edit* applied to it."""
    folder = tmp_path / "poses"
    shutil.copytree(POSES, folder)
    edit(folder)
    return folder


def _replaced(path: Path, old: str, new: str) -> None:
    """*path* with its first *old* replaced by *new*."""
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} is no longer in {path.name}, so nothing would be planted"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _faint_badge(path: Path) -> None:
    """*path* with its badge drawn the way the waiting badges were below 24 px before #3830's
    review: the mark's own dots, a dot's width apart, in the tone."""
    text = path.read_text(encoding="utf-8")
    faint = "".join(f'<circle cx="14" cy="{y}" r="0.5"/>' for y in (-0.12, 1.08, 2.28, 4.68))
    planted = re.sub(r'(<g data-part="badge"[^>]*>).*?(</g>)', rf"\g<1>{faint}\g<2>", text, count=1)
    assert planted != text, f"{path.name} has no badge, so nothing would be planted"
    path.write_text(planted, encoding="utf-8")


def test_each_check_reports_a_planted_defect(subtests: Any, tmp_path: Path) -> None:
    data = load_matrix()
    table = source_index.text(MATRIX_TABLE)
    machine = source_index.text(STATE_MACHINE)
    guide = source_index.text(MOTION_GUIDE)
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
            "a colour of the buddy's own, named in any file of the record",
            lambda: colour_problems(
                _record_texts_with(
                    "surfaces.md",
                    "# Buddy surfaces\n",
                    "# Buddy surfaces\n\n`--color-buddy-pill`\n",
                ),
                _defined_colours(),
            ),
            "surfaces.md: the buddy names a colour of its own, ['--color-buddy-pill']",
        ),
        (
            "the design system's entry no longer pointing at the agent orb",
            lambda: colour_problems(
                _record_texts_with("web/DESIGN.md", "agent-orb.md", "patterns.md"),
                _defined_colours(),
            ),
            "web/DESIGN.md: the buddy's colours do not point at the agent orb's",
        ),
        (
            "the brief naming a neutral the design system does not have",
            lambda: colour_problems(
                _record_texts_with(
                    "character.md", "`--color-on-surface-var`", "`--color-on-surface-warm`"
                ),
                _defined_colours(),
            ),
            "character.md: the buddy's colours name ['--color-on-surface-warm']",
        ),
        (
            "the brief's first constraint no longer pointing at what the buddy adds",
            lambda: comparison_problems(
                _record_texts_with("character.md", "(rig.md#candidates)", "(rig.md)")
            ),
            "character.md: the entry that says 'drawn the way the agent orb is' does not link"
            " rig.md#candidates",
        ),
    ]
    brief = source_index.text(CHARACTER)
    planted += [
        (
            "a pose the brief names, with its source gone",
            lambda: census_problems(
                brief,
                _copy_of_the_poses(
                    tmp_path / "gone", lambda f: (f / "presence-waking.svg").unlink()
                ),
            ),
            "presence-waking: the brief names it, and it has no source",
        ),
        (
            "a source of a pose the brief does not name",
            lambda: census_problems(
                brief,
                _copy_of_the_poses(
                    tmp_path / "extra",
                    lambda f: shutil.copy(f / "presence-peek.svg", f / "presence-hidden.svg"),
                ),
            ),
            "poses/presence-hidden.svg is a pose the brief does not name",
        ),
        (
            "a pose that moves, with its motion frame gone",
            lambda: census_problems(
                brief,
                _copy_of_the_poses(
                    tmp_path / "still", lambda f: (f / f"active-thinking{MOTION}").unlink()
                ),
            ),
            "active-thinking moves, and has no motion frame",
        ),
        (
            "the brief naming one more presence pose",
            lambda: census_problems(brief.replace(" floating,", " floating, hidden,", 1), POSES),
            "presence-hidden: the brief names it, and it has no source",
        ),
        (
            "a literal colour in a source",
            lambda: source_problems(
                _copy_of_the_poses(
                    tmp_path / "ink",
                    lambda f: _replaced(
                        f / "active-thinking.svg", "var(--color-orb-alive)", "#e5533d"
                    ),
                ),
                _defined_colours(),
            ),
            "poses/active-thinking.svg: its fill is '#e5533d', not a token",
        ),
        (
            "the whole orb squashed",
            lambda: source_problems(
                _copy_of_the_poses(
                    tmp_path / "squash",
                    lambda f: _replaced(
                        f / "active-thinking.svg",
                        'transform="translate(0 0)"',
                        'transform="scale(1 0.8)"',
                    ),
                ),
                _defined_colours(),
            ),
            "poses/active-thinking.svg: 'scale(1 0.8)' squashes or stretches the orb",
        ),
        (
            "two families alike at 18 px",
            lambda: small_size_problems(
                brief,
                _copy_of_the_poses(
                    tmp_path / "alike",
                    lambda f: shutil.copy(f / "tray-needs-you-18.svg", f / "tray-error-18.svg"),
                ),
            ),
            "at 18 px, waiting and blocked have the same density and arrangement",
        ),
        (
            "waiting's badge as faint as active's at 18 px",
            lambda: small_size_problems(
                brief,
                _copy_of_the_poses(
                    tmp_path / "faint", lambda f: _faint_badge(f / "tray-needs-you-18.svg")
                ),
            ),
            "at 18 px, waiting's badge carries",
        ),
        (
            "the contact sheet missing a pose in dark",
            lambda: sheet_problems(
                brief,
                _copy_of_the_poses(
                    tmp_path / "sheet",
                    lambda f: _replaced(
                        f / "sheets" / "families.svg",
                        'data-sheet-pose="quiet-idle" data-mode="dark" data-size="18"',
                        'data-sheet-pose="quiet-idle" data-mode="dim" data-size="18"',
                    ),
                ),
            ),
            "the contact sheet does not show quiet-idle at 18 px in dark",
        ),
    ]
    assert len(planted) == 25, "a planted defect was dropped from the table"
    for name, check, expected in planted:
        with table_rows.row(subtests, defect=name):
            reported = check()
            assert any(expected in line for line in reported), (name, reported)
