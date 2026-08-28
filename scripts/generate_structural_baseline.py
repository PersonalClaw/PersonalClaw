#!/usr/bin/env python3
"""Committed STRUCTURAL ratchet baselines (PLATFORM-HARDENING-FLOORS PHF-14).

Structural decay is invisible until it is expensive. A file grows 300 lines a quarter and
nobody notices; a domain module reaches up into the HTTP layer and the dependency inverts
silently; a family we have already unified once gets re-derived a thirteenth time because
nothing counts. None of those show up in a unit test — the code works. Only a *census of
the shape of the tree* sees them.

This generator IS that census. It measures three structural properties of production
``src/personalclaw`` and commits them to ``structural-baseline.json`` as three INDEPENDENT
shrink-only ratchets:

  * ``structural-size`` — a per-file size ceiling no file may exceed, plus a shrink-only
    POPULATION of files in the watch band. A new giant reds; ordinary maintenance of an
    existing one does not.
  * ``structural-import-direction`` — a declared layer order. A lower layer may not import
    an upper one; each file's upward-edge count may only shrink.
  * ``structural-duplication`` — a duplicate-implementation counter for the families this
    codebase has repeatedly re-derived (HTTP error envelope, verdict types, durable write).

Each ratchet is its own gate in ``scripts/gate_report.py`` (PHF-11's aggregate), so three
independent structural failures report as THREE failures in one run — one red never hides
the other two.

⚠️  FORBIDDEN-TO-RAISE RULE (the whole point — do not weaken it): when a ratchet reds
    because a counter ROSE, the fix is to FIX THE CODE — split the file, invert the import,
    reuse the existing implementation — NEVER to regenerate this baseline to bless the
    higher number. Regenerating to make a rising count green re-hides exactly the decay this
    file exists to surface. Regeneration is legitimate ONLY when a counter LEGITIMATELY
    SHRANK, and then it must happen in that same commit.

⚠️  SHIP AT THE MEASURED POPULATION, NEVER AT ZERO. This is ``PHF-6``'s own ruling, restated
    here so this atom cannot repeat the outage it warns about: a never-run gate given teeth
    at zero reds the whole tree at once. Every threshold below is derived from a MEASUREMENT of
    the tree as it stands (max 5447 lines, 9 files in the watch band; 66 upward import edges in
    33 files; 33 duplicate sites in 29 files), not an aspiration. The ratchets forbid only
    GROWTH.

⚠️  AND A RATCHET THAT REDS ORDINARY MAINTENANCE IS AN OUTAGE, NOT A GATE. The size ratchet
    counts HOW MANY files are giant; it does not freeze each giant's exact length. Decay is a new
    2,800-line module appearing — not three lines added to the module that, by construction, gets
    maintained most (three of the config round-trip's five points live in
    ``config/loader.py``, the largest file in the repo). A ratchet that turned a boolean config
    toggle into "split a 5,447-line file first" is the gate people route around, which is exactly
    why this file does NOT ratchet function length or cyclomatic complexity either.
    ``test_an_ordinary_config_field_addition_to_the_largest_file_stays_green`` is the rail that
    keeps this true.

⚠️  EVERY THRESHOLD RECORDS ITS RATIONALE — the defect it exists to catch — in the
    ``rationale`` field beside the number, so a future session can tell a load-bearing limit
    from an arbitrary one. ``tests/test_structural_baseline.py`` asserts every threshold has
    one.

# what this deliberately does NOT ratchet
    Stated as decisions, so the omissions read as choices rather than gaps:

    * **``tests/`` file length.** A 3,000-line test module is not the comprehension hazard a
      3,000-line production module is: tests are read one function at a time and grow by
      append, by design. Ratcheting them would tax the one activity we want cheapest.
    * **Function/class length and cyclomatic complexity.** flake8 already owns per-line and
      per-import style; a complexity ratchet needs a metric everyone agrees on, and picking
      one badly produces a gate people route around. Deferred, not rejected.
    * **``web/`` (the SPA).** The design-system ratchets already own frontend structure and
      run under vitest; a second, Python-side counter over ``web/src`` would be a duplicate
      gate — exactly the defect ``structural-duplication`` measures.
    * **The apps → core import direction.** ``tests/test_apps_import_boundary.py`` already
      owns it (apps may reach core only via ``personalclaw.sdk.*``). This atom deliberately
      picks directions that test does NOT cover: INSIDE core, and the reverse direction
      (core importing its own published facade).
    * **Total line count of ``src/``.** A growing project grows; a total-lines ratchet would
      red on every feature and teach the team to regenerate baselines, which is the habit
      that kills every other ratchet in this file.
    * **Import CYCLES.** Worth having and genuinely separate: a cycle is a different defect
      (initialization order) from a direction violation (layer inversion), and it needs a
      strongly-connected-component pass rather than a per-edge rule. Its own atom.

# how to update
    Regenerate ONLY when a counter LEGITIMATELY SHRANK (a file was split, an upward import
    was inverted, a re-derivation was deleted), and do it in that SAME commit::

        python scripts/generate_structural_baseline.py

    Each such commit should be able to point at the split, the inversion, or the deletion.

# moving SIZE_WATCH_BAND_LINES is a re-authoring, not a regeneration
    The band is a threshold, not a counter, so changing it is not covered by the
    forbidden-to-raise rule — which makes it the one loophole in this file, and it is closed by
    protocol rather than by a rail. The ONLY sanctioned trigger is the cliff rail
    (``test_the_watch_band_is_not_sitting_on_a_cliff``) reporting under 100 lines of headroom,
    which means the boundary has stopped sitting at a gap. When that happens: measure the whole
    distribution, pick the boundary at a real gap, and record the measured table plus the
    reasoning in ``PLATFORM-HARDENING-FLOORS``'s execution log — 2500 -> 2800 on 2026-08-21 is the
    worked example.

    It is FORBIDDEN to move the band in response to a population RISE. That is a counter, the
    forbidden-to-raise rule owns it, and widening the band to make a new giant disappear is the
    same act as regenerating a baseline to bless a higher number. If a red names an entrant, split
    the entrant.

The render is DETERMINISTIC: files, site lists and per-rule totals are all sorted, the
output is ``json.dumps(..., indent=2, sort_keys=True)`` with a trailing newline, and it
carries no timestamps, absolute paths, or line numbers (a line number would churn on every
edit above it). A second run is byte-identical to the first.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# ── Census scope ─────────────────────────────────────────────────────────────
#
# The walk is rooted at ``src/personalclaw`` and NEVER at the repo root. That is deliberate
# and load-bearing: this repo is routinely checked out as ~200 concurrent git worktrees, and
# a walk that wandered into ``.worktrees/``, ``node_modules/``, ``.venv/`` or ``build/``
# would census ANOTHER tree — a number that drifts on every run and measures nothing. None
# of those directories can appear under ``src/personalclaw``; ``_EXCLUDED_DIR_NAMES`` is a
# belt-and-suspenders floor asserted by the ratchet's own test.
_EXCLUDED_DIR_NAMES = frozenset(
    {"__pycache__", ".venv", ".venv-client", "node_modules", "build", "dist", ".worktrees", ".git"}
)

# Vacuity floor for the census itself. MEASURED 2026-08-21: 921 production ``.py`` files
# under ``src/personalclaw``. The floor is far below that and far above a broken walk (0, or
# the handful a wrong root yields), so it catches "the rail inspected nothing" without
# churning every time a module is added or deleted. A rail that matches nothing looks clean,
# and that is the most common way a gate in this repo dies — so each ratchet ALSO asserts it
# inspected exactly as many files as the census counted (``vacuity_failures``).
MIN_CENSUS_PY_FILES = 800

RATCHET_SIZE = "structural-size"
RATCHET_IMPORT_DIRECTION = "structural-import-direction"
RATCHET_DUPLICATION = "structural-duplication"
RATCHETS = (RATCHET_SIZE, RATCHET_IMPORT_DIRECTION, RATCHET_DUPLICATION)


def _repo_root() -> Path:
    # Returns the constant resolved at import, NOT a fresh ``Path(__file__).resolve()``. This
    # is called once per file per scan (~2,700 times); re-resolving each time made the cold
    # census three times slower for nothing.
    return _REPO_ROOT


def _src_root() -> Path:
    return _repo_root() / "src" / "personalclaw"


def _is_excluded(path: Path) -> bool:
    """Is *path* inside an excluded directory **within the census root**?

    🔴 THE EXCLUSION MUST BE TESTED RELATIVE TO THE ROOT, NEVER ON THE ABSOLUTE PATH. It used to
    be ``_EXCLUDED_DIR_NAMES & set(path.parts)`` on the absolute path, and `.worktrees` is one of
    the excluded names — so every file in a git worktree under ``.worktrees/`` matched on an
    ANCESTOR directory that is not part of this repo at all. The census went to **0**, the vacuity
    floor fired, and all three structural ratchets plus ``test_gate_report`` went red: **18 tests,
    unconditionally, in every worktree** — which is the workflow this repo mandates for
    contributions.

    The exclusion's actual purpose is unchanged and is stated in
    ``test_the_walk_cannot_wander_into_a_worktree_or_a_vendor_directory``: don't count another
    agent's tree. That guarantee comes from rooting the walk at ``src/personalclaw`` (asserted
    there), not from matching ancestor names — and a name outside the root was never something
    this filter could meaningfully judge.
    """
    try:
        rel = path.relative_to(_src_root())
    except ValueError:
        # Outside the census root entirely. The walk cannot produce this, and the rail asserts
        # so; treat it as excluded rather than silently counting it.
        return True
    return bool(_EXCLUDED_DIR_NAMES & set(rel.parts))


def _src_py_files() -> list[Path]:
    """Every production ``.py`` file under ``src/personalclaw`` (sorted, vendor dirs excluded)."""
    out: list[Path] = []
    for path in _src_root().rglob("*.py"):
        if _is_excluded(path):
            continue
        out.append(path)
    return sorted(out)


_REL_CACHE: dict[Path, str] = {}


def _rel(path: Path) -> str:
    """POSIX repo-relative path string (stable across platforms), memoized.

    Three scans ask for the same 915 paths, and ``Path.resolve()`` is a syscall each time.
    """
    hit = _REL_CACHE.get(path)
    if hit is None:
        hit = path.resolve().relative_to(_repo_root()).as_posix()
        _REL_CACHE[path] = hit
    return hit


def _in_src(path: Path) -> str:
    """POSIX path relative to ``src/personalclaw`` (the layer key's namespace)."""
    return path.resolve().relative_to(_src_root()).as_posix()


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None


def census_py_files() -> int:
    """How many production files the census sees. Every ratchet must inspect exactly this
    many; a ratchet that inspects fewer has silently narrowed and is reporting a clean tree
    it never looked at."""
    return len(_src_py_files())


def census_packages() -> set[str]:
    """Every immediate sub-package of ``src/personalclaw`` that holds at least one ``.py``
    file, taken straight from disk. Compared against what the ratchets actually walked: a
    file COUNT alone cannot see a whole package dropping out of the walk (66 packages, so
    losing one still leaves the count above any plausible floor)."""
    packages: set[str] = set()
    for path in _src_root().rglob("*.py"):
        if _is_excluded(path):
            continue
        rel = path.resolve().relative_to(_src_root()).as_posix()
        packages.add(rel.split("/")[0] if "/" in rel else "")
    return packages


@dataclass(frozen=True)
class Scan:
    """One ratchet's walk: the rows it produced AND the set of files it actually inspected.

    The inspected set is returned by the SAME function that builds the rows, on purpose. A
    parallel re-count would go stale the moment someone added a filter inside a scan — the
    vacuity assertion would keep measuring the old walk and keep reading clean. Here a filter
    added inside a scan shrinks ``inspected`` in the same breath, and vacuity fires.
    """

    inspected: frozenset[str]
    rows: dict[str, Any]


# ── Ratchet 1: per-file size ceiling ─────────────────────────────────────────

# Files at or above this length are the WATCH BAND, and the ratchet counts HOW MANY there are
# (shrink-only) rather than pinning each one's length. The boundary must sit at a real GAP in the
# measured distribution, never at a round number, and this repo proved why within three days of
# the band being authored: ``agents/native/builtin_tools.py`` grew ~233 lines (AG-14 alone added
# 122) to 2467, which left the original 2500 band with **33** lines of headroom — the cliff rail
# caught it, and the band had to move.
#
# MEASURED 2026-08-21 over 921 production files. Only two boundaries in the relevant window clear
# the rail's own >= 100 requirement:
#
#     band   population   largest non-member          headroom
#     2400        11       2294  subagent.py              106
#     2500        10       2467  builtin_tools.py          33   <- the cliff that forced this move
#     2600         9       2583  workflows/engine.py       17
#     2800         9       2583  workflows/engine.py      217   <- CHOSEN
#     2900         8       2808  chat_handlers.py          92
#     3000         7       2992  handlers/files.py          8
#
# 2800 over 2400, and the usual reading of that trade is backwards. The apparent cost is that
# ``engine.py`` (2583) and ``builtin_tools.py`` (2467) are not watched — but a band member's
# growth is deliberately NOT a violation, so at 2400 those two would be GRANDFATHERED and free to
# run to the ceiling unchallenged. At 2800 they are outside, and crossing 2800 REDS. The band at
# 2800 therefore puts MORE pressure on the two fastest-growing large files in the repo, not less.
# On top of that, 2400's 106 lines of headroom is well under one feature's growth for this
# codebase (233 lines in three days, above), so it would be shipping a boundary already known to
# be one merge from redding; 2800 gives 217, 2.17x the rail's floor.
#
# KNOWN COST, stated rather than discovered later: the band's smallest member is
# ``chat_handlers.py`` at 2808, so it has only 8 lines of SHRINK margin — delete nine lines from
# it and the stale-high check asks for a regeneration. That is deliberate. The 225-line gap
# (2583 -> 2808) cannot give 200+ lines of margin in both directions, and the two remedies are not
# equally priced: a stale-high red is one command (re-run this generator) in the same commit and is
# the documented, sanctioned flow for a file leaving the giant population, whereas a cliff red asks
# for the boundary itself to be re-authored. Optimise the
# margin against GROWTH, which is the direction this ratchet exists to measure, and pay the cheap
# remedy on the other side.
SIZE_WATCH_BAND_LINES = 2800

# The absolute per-file ceiling. FORBIDDEN TO RAISE.
#
# MEASURED 2026-08-21: the max over 921 production files is 5447 (``config/loader.py``). The
# ceiling is set one 1000-line STEP above that max rather than AT it, and the reason is the
# difference between a ratchet and an outage. ``config/loader.py`` is the ceiling holder AND the
# file the config round-trip contract touches on every new field (dataclass + ``_meta`` +
# ``load()`` — three of the contract's five points live there). A ceiling pinned at 5447 gives
# that file ZERO headroom, so a one-line boolean toggle would red CI and demand a 5,447-line
# split as its price. The rail's job is to stop a STEP CHANGE — a module arriving at 6,000 lines,
# or the worst file gaining a thousand — and the watch-band population already catches every new
# giant at the band. So: 553 lines of ordinary-maintenance headroom, and nothing in the repo may
# reach 6,000.
#
# Deliberately a STEP multiple, not ``max + N``: a step keeps the rendered value STABLE while the
# max drifts by a few lines, so the byte-compare test does not demand a regeneration on every
# routine commit. Lower it one step when the max legitimately drops below the previous step —
# ``stale_high()`` asks for exactly that via ``ceiling_slack_steps``.
SIZE_CEILING_STEP_LINES = 1000
SIZE_CEILING_LINES = 6000

_SIZE_RATIONALE = (
    "A module nobody can hold in their head is where every other kind of decay hides: dead "
    "branches, a second implementation of something three functions above, a config field read "
    "in one place and written in another. Growth is invisible per commit (+40 lines reviews "
    "fine) and irreversible in aggregate. TWO rails, and the split between them is the whole "
    "design. (1) The CEILING is absolute: no file may reach 6000 lines, so no module can arrive "
    "or grow into a step change, and a new worst file reds. It sits one 1000-line step above the "
    "measured max of 5447 on purpose — pinned AT the max it would give the repo's most-"
    "maintained file zero headroom and turn a boolean config toggle into a mandatory 5,447-line "
    "split, which is the gate people route around. (2) The WATCH-BAND POPULATION is the honest "
    "decay signal: how MANY files are giant, shrink-only. A file ENTERING the 2800-line band "
    "reds (9 -> 10); splitting one is how you go green (9 -> 8). Growth WITHIN the band, below "
    "the ceiling, is ordinary maintenance of the files that by construction get maintained most, "
    "and is deliberately NOT a violation. Everything below the band is unconstrained except by "
    "the ceiling: routine work is untaxed, and the count is what has to come down. WHY 2800 AND "
    "NOT A ROUND 2500: the boundary has to sit at a real gap, and this repo proved it within "
    "three days — agents/native/builtin_tools.py grew ~233 lines (AG-14 added 122) to 2467, "
    "leaving the original 2500 band 33 lines of headroom. 2800 sits in the 225-line gap between "
    "engine.py (2583) and chat_handlers.py (2808): 217 lines of headroom, 2.17x the cliff rail's "
    "floor. It also leaves engine.py and builtin_tools.py OUTSIDE the band, where crossing 2800 "
    "reds them — at 2400 they would be grandfathered members, free to grow to the ceiling "
    "unchallenged. The full measured distribution and the accepted 8-line shrink margin on "
    "chat_handlers.py are recorded beside SIZE_WATCH_BAND_LINES."
)


def scan_sizes() -> Scan:
    """``{repo-relative path: line count}`` for every censused production file."""
    counts: dict[str, int] = {}
    inspected: set[str] = set()
    for path in _src_py_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = _rel(path)
        inspected.add(rel)
        counts[rel] = len(text.splitlines())
    return Scan(frozenset(inspected), counts)


def watch_band_headroom() -> int:
    """Lines between the largest NON-member and the watch band, measured live.

    With population counting the band boundary is the whole trigger, so this is the number that
    says whether the band sits at a gap in the distribution or on a cliff. Deliberately NOT
    stored in the baseline: it moves whenever any sub-band file grows, and pinning it would make
    the byte-compare test demand a regeneration on routine commits.
    """
    below = [n for n in scan(RATCHET_SIZE).rows.values() if n < SIZE_WATCH_BAND_LINES]
    return SIZE_WATCH_BAND_LINES - max(below) if below else SIZE_WATCH_BAND_LINES


def size_block_from(counts: dict[str, int]) -> dict[str, Any]:
    """The size baseline derived from a supplied ``{path: lines}`` map.

    Split out from ``_size_block`` so a test can drive the REAL derivation over a perturbed copy
    of the real counts — "what if ``config/loader.py`` gained six lines?" — without writing to
    ``src/`` (a stray file there would red the byte-compare gate for every suite running
    concurrently) and without poisoning the scan memo.
    """
    watched = sorted(rel for rel, n in counts.items() if n >= SIZE_WATCH_BAND_LINES)
    over = {rel: n for rel, n in sorted(counts.items()) if n > SIZE_CEILING_LINES}
    biggest = max(counts.values()) if counts else 0
    return {
        "ceiling_lines": SIZE_CEILING_LINES,
        "ceiling_step_lines": SIZE_CEILING_STEP_LINES,
        # How many full steps the ceiling could come down. 0 while the max sits inside the
        # current step; >0 means a split landed and the ceiling is now stale-high.
        "ceiling_slack_steps": max(0, (SIZE_CEILING_LINES - biggest) // SIZE_CEILING_STEP_LINES),
        "over_ceiling": over,
        "watch_band_lines": SIZE_WATCH_BAND_LINES,
        # Identities kept for legibility — a reader can see WHICH files are giant. Their
        # individual lengths are deliberately NOT asserted; only membership is.
        "watch_band_members": watched,
        "rationale": _SIZE_RATIONALE,
        "totals": {"watched_files": len(watched)},
    }


def _size_block() -> dict[str, Any]:
    """The size baseline. Every field here is STABLE under ordinary maintenance.

    Nothing in this block is a raw line count, and that is the point: a stored length would make
    the byte-compare test demand a regeneration every time any large file gained a line, and a
    baseline people regenerate routinely is a baseline nobody reads. What IS stored: the constant
    ceiling, the count and identities of the band members, and ``over_ceiling`` — empty in the
    healthy state, and populated (with the offending lengths) only when the rail is breached.
    """
    return size_block_from(scan(RATCHET_SIZE).rows)


def regressions_size(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Size-ratchet backslides, sorted. TWO shapes, both "a giant appeared":

    1. a file exceeds the committed ceiling — a step change, or a new worst file;
    2. a file that was below the watch band has ENTERED it, so the band population ROSE.

    Explicitly NOT a regression: a band member growing while staying under the ceiling. That is
    ordinary maintenance of the files that by construction get maintained most, and reding it
    would make this an outage rather than a ratchet. A band member LEAVING is a split, which is
    the whole point and is welcome (``stale_high`` then asks for a regeneration).
    """
    lines: list[str] = []
    ceiling = int(baseline["ceiling_lines"])
    band = int(baseline["watch_band_lines"])
    for rel, now in sorted(current["over_ceiling"].items()):
        lines.append(
            f"{rel}: {now} lines EXCEEDS the committed per-file ceiling of {ceiling} "
            "— split the module; do not raise the ceiling"
        )
    committed_members = set(baseline["watch_band_members"])
    current_members = set(current["watch_band_members"])
    entrants = sorted(current_members - committed_members)
    if entrants:
        lines.append(
            f"the {band}-line watch-band population ROSE "
            f"{len(committed_members)} -> {len(current_members)}; new giant(s): {entrants} "
            "— split the new file; the count may only come down"
        )
    return lines


# ── Ratchet 2: module-boundary / import-direction ────────────────────────────


@dataclass(frozen=True)
class DirectionRule:
    """One declared layer-order rule: files in ``lower`` may not import ``upper``.

    ``lower == ("*",)`` means "every package except the ones named in ``upper``" — the
    whole-core form used for the two rules whose lower layer is "all of core".
    """

    name: str
    lower: tuple[str, ...]
    upper: tuple[str, ...]
    rationale: str

    def applies_to(self, in_src: str) -> bool:
        top = in_src.split("/")[0] if "/" in in_src else ""
        if top in self.upper:
            return False  # the upper layer may of course import itself
        return self.lower == ("*",) or top in self.lower


# The layer order, lowest first. Deliberately SMALL: three rules with measured, non-zero
# populations plus one leaf-isolation rule, each naming the defect it catches. This is not a
# full layering of 66 packages — a map that large would be a design document masquerading as
# a lint, and its first false red would get the whole gate deleted.
DIRECTION_RULES: tuple[DirectionRule, ...] = (
    DirectionRule(
        name="ledger-is-a-leaf",
        lower=("ledger",),
        upper=("dashboard", "sdk", "workflows", "loop", "agents", "knowledge", "learning"),
        rationale=(
            "PP-4 extracted the run ledger OUT of workflows as a platform primitive; "
            "workflows/journal.py is now a 685-line facade OVER it. An import back up into a "
            "consumer would silently undo that extraction: the ledger would stop being usable "
            "by loops, tasks or evals (PP-5/PP-9 are exactly those consumers) and the "
            "extraction would have to be paid for twice. Measured at 0 edges — the extraction "
            "is clean today, and this rule is what keeps it that way for the cost of one line."
        ),
    ),
    DirectionRule(
        name="core-must-not-import-the-http-surface",
        lower=("*",),
        upper=("dashboard",),
        rationale=(
            "``dashboard/`` is the aiohttp + SPA surface. Domain code that imports a handler "
            "module inverts the dependency: the domain can no longer be exercised without "
            "standing up the web app, which is how a feature ends up reachable ONLY through "
            "one route and invisible to the CLI, the MCP surface and the harness. Measured 56 "
            "edges across 26 files, most of them entry-point composition (gateway, cli_*) "
            "that is legitimately downward-facing. Shrink-only GRANDFATHERS those instead of "
            "an exemption list — an allowlist is a thing that rots, a measured floor is not."
        ),
    ),
    DirectionRule(
        name="core-must-not-import-its-own-published-facade",
        lower=("*",),
        upper=("sdk",),
        rationale=(
            "``personalclaw.sdk`` is the OUTWARD facade that removable app bundles import; "
            "tests/test_apps_import_boundary.py pins the other direction (apps may reach core "
            "only through it) and that test SKIPS in a standalone clone, so this direction has "
            "never been guarded at all. When core imports its own facade, the facade becomes "
            "load-bearing INSIDE core and can no longer be reshaped for apps without breaking "
            "core — the provider-boundary tenet inverted. Measured 10 edges across 7 files."
        ),
    ),
)


def _imported_personalclaw_modules(path: Path, tree: ast.Module) -> list[str]:
    """Absolute ``personalclaw.*`` module paths imported by ``path``, relative imports resolved.

    A relative import is the shape that hides a direction violation from grep (``from
    ..dashboard import x`` never contains the string ``personalclaw.dashboard``), so it is
    resolved here rather than skipped.
    """
    parent = path.resolve().parent
    rel_parent = parent.relative_to(_src_root()).as_posix()
    pkg_parts = [] if rel_parent == "." else rel_parent.split("/")
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names if a.name.startswith("personalclaw"))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module and node.module.startswith("personalclaw"):
                    out.append(node.module)
                continue
            up = node.level - 1
            base = pkg_parts[: len(pkg_parts) - up] if up else list(pkg_parts)
            tail = [node.module] if node.module else []
            out.append(".".join(["personalclaw", *base, *tail]))
    return out


def scan_upward_edges() -> Scan:
    """``{repo-relative path: {"edges": N, "violations": ["rule -> module", ...]}}``."""
    per_file: dict[str, dict[str, Any]] = {}
    inspected: set[str] = set()
    for path in _src_py_files():
        tree = _parse(path)
        if tree is None:
            continue
        inspected.add(_rel(path))
        in_src = _in_src(path)
        found: list[str] = []
        modules = _imported_personalclaw_modules(path, tree)
        for rule in DIRECTION_RULES:
            if not rule.applies_to(in_src):
                continue
            for upper in rule.upper:
                prefix = f"personalclaw.{upper}"
                for mod in modules:
                    if mod == prefix or mod.startswith(prefix + "."):
                        found.append(f"{rule.name} -> {mod}")
        if found:
            per_file[_rel(path)] = {"edges": len(found), "violations": sorted(found)}
    return Scan(frozenset(inspected), dict(sorted(per_file.items())))


def _import_direction_block() -> dict[str, Any]:
    per_file = scan(RATCHET_IMPORT_DIRECTION).rows
    by_rule: dict[str, int] = {rule.name: 0 for rule in DIRECTION_RULES}
    for entry in per_file.values():
        for violation in entry["violations"]:
            by_rule[violation.split(" -> ", 1)[0]] += 1
    return {
        "rules": [
            {
                "name": r.name,
                "lower": list(r.lower),
                "upper": list(r.upper),
                "rationale": r.rationale,
            }
            for r in DIRECTION_RULES
        ],
        "per_file": per_file,
        "totals": {
            "edges": sum(int(e["edges"]) for e in per_file.values()),
            "files": len(per_file),
            "by_rule": dict(sorted(by_rule.items())),
        },
    }


def regressions_import_direction(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Files whose upward-edge counter ROSE, sorted. A DECREASE is an inversion landing and
    is welcome; the ratchet forbids only a NEW upward edge."""
    base_per_file: dict[str, Any] = baseline["per_file"]
    lines: list[str] = []
    for rel in sorted(current["per_file"]):
        now = int(current["per_file"][rel]["edges"])
        was = int(base_per_file.get(rel, {}).get("edges", 0))
        if now <= was:
            continue
        new = sorted(
            set(current["per_file"][rel]["violations"])
            - set(base_per_file.get(rel, {}).get("violations", []))
        )
        lines.append(
            f"{rel}: upward imports rose {was} -> {now}; new layer violation(s): {new} "
            "— invert the dependency (move the shared piece down), do not raise the count"
        )
    return lines


# ── Ratchet 3: duplicate-implementation counter ──────────────────────────────


@dataclass(frozen=True)
class DuplicateFamily:
    """One family this codebase has repeatedly re-derived: its label, the canonical
    implementation (if one exists yet), and the defect a re-derivation causes."""

    name: str
    canonical: str | None
    rationale: str


DUPLICATE_FAMILIES: tuple[DuplicateFamily, ...] = (
    DuplicateFamily(
        name="http-error-envelope-helper",
        canonical=None,
        rationale=(
            "PL-8 deleted THIRTEEN module-local ``json_error`` clones, one of which had minted "
            "a third ``WF_UPPER_SNAKE`` code vocabulary inside the HTTP envelope. Twelve "
            "survive under other names (``_err``, ``_bad``, ``_bad_request``, ``_rpc_error``, "
            '``_invalid_path``, …), each a one-statement re-derivation of the ``{"error": '
            '{"code", "message"}}`` wire shape. Every clone is a place the envelope drifts '
            "silently — a wrong status, an UPPER_SNAKE code where the wire wants "
            "lowercase_snake, a missing field — and no round-trip test can see it, because "
            "each handler's test asserts against that handler's own clone. There is "
            "deliberately NO canonical HTTP helper yet: the two-error-envelope ruling keeps "
            "``AgentError`` and the wire shape apart, and this counter measures the population "
            "rather than mandating a merge. The fix for a thirteenth is to PROMOTE one shared "
            "helper, never to add another."
        ),
    ),
    DuplicateFamily(
        name="verdict-type",
        canonical="src/personalclaw/workflows/judge_contract.py",
        rationale=(
            "PP-14's thesis: the engine implements verdict semantics several times over "
            "because the primitive was never named. WF2LOO-16 deleted ``CycleVerdict`` into "
            "``judge_contract.JudgeVerdict`` and measured that 0 of 4 loop-verdict shapes "
            "satisfied ``validate_verdict`` — four dialects, none interoperable. 23 "
            "verdict-shaped types still live outside the canonical module, each with its own "
            "pass/fail tokens and its own reason field, so no supervisor, ledger or evaluator "
            "can read a verdict without knowing which dialect produced it. This is a COUNTER, "
            "not a ban: a decision in a genuinely different domain (an IP allow/deny result) "
            "is not destined to merge — but then it should not be NAMED a verdict, and that "
            "rename shrinks this number too."
        ),
    ),
    DuplicateFamily(
        name="durable-write",
        canonical="src/personalclaw/atomic_write.py",
        rationale=(
            "DAS-9: a handler rolled its own ``mkstemp`` + ``rename`` and so vanished from the "
            "history seam — the write landed on disk and no post-write hook ever fired, which "
            "is the swallowed-write defect with a green test on both sides. ``atomic_write.py`` "
            "is the ONE implementation that keeps that seam (and fsync durability) intact. "
            "Five functions across four files still pair a temp-file creation with a rename "
            "themselves. Thin wrappers that DELEGATE to ``atomic_write`` are deliberately not "
            "counted — three exist and they are the shape we want, not a re-derivation."
        ),
    ),
)

_ENUM_BASES = frozenset({"Enum", "StrEnum", "IntEnum", "Flag", "IntFlag"})
_TEMPFILE_FACTORIES = frozenset({"mkstemp", "NamedTemporaryFile", "mkdtemp"})
_RENAME_CALLS = frozenset({"replace", "rename"})


def _returns_error_envelope(node: ast.AST) -> bool:
    """True if *node* contains a ``{"error": {"code": ..., ...}}`` dict literal.

    Structural, not name-based: the family is defined by the SHAPE it re-derives, so
    renaming ``_err`` to ``_oops`` cannot dodge the counter.
    """
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Dict):
            continue
        for key, value in zip(sub.keys, sub.values):
            if not (isinstance(key, ast.Constant) and key.value == "error"):
                continue
            if not isinstance(value, ast.Dict):
                continue
            inner = {k.value for k in value.keys if isinstance(k, ast.Constant)}
            if "code" in inner:
                return True
    return False


def _envelope_helper_sites(tree: ast.Module) -> list[str]:
    """Module-level functions whose whole body is an error-envelope construction.

    Bounded to <= 3 statements on purpose: the family is "a tiny helper that re-derives the
    envelope", not "a route handler that returns an error". A 400-line handler returning an
    envelope inline is a different (and much larger) population; counting it here would make
    the number un-actionable, because there is no sanctioned alternative for a handler yet.
    """
    sites: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = [
            s
            for s in node.body
            if not (
                isinstance(s, ast.Expr)
                and isinstance(s.value, ast.Constant)
                and isinstance(s.value.value, str)
            )
        ]
        if len(body) > 3:
            continue
        if any(isinstance(s, ast.Return) and _returns_error_envelope(s) for s in body):
            sites.append(node.name)
    return sites


def _verdict_type_sites(tree: ast.Module) -> list[str]:
    """Classes named ``Verdict`` or ``*Verdict`` — the verdict-shaped type population."""
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and (node.name == "Verdict" or node.name.endswith("Verdict"))
    ]


def _durable_write_sites(tree: ast.Module) -> list[str]:
    """Functions that BOTH create a temp file and commit it with ``os.replace``/``os.rename``.

    That pair IS the re-derivation. A function that calls ``atomic_write(...)`` — even one
    named ``_atomic_write`` — does not match, because delegating is the shape we want.
    """
    sites: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        made_temp = renamed = False
        for sub in ast.walk(node):
            if not (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)):
                continue
            if sub.func.attr in _TEMPFILE_FACTORIES:
                made_temp = True
            if (
                sub.func.attr in _RENAME_CALLS
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id == "os"
            ):
                renamed = True
        if made_temp and renamed:
            sites.append(node.name)
    return sites


def scan_duplicates() -> Scan:
    """``{repo-relative path: {"count": N, "sites": ["family:symbol", ...]}}``."""
    per_file: dict[str, dict[str, Any]] = {}
    inspected: set[str] = set()
    canonical = {f.name: f.canonical for f in DUPLICATE_FAMILIES}
    for path in _src_py_files():
        tree = _parse(path)
        if tree is None:
            continue
        rel = _rel(path)
        inspected.add(rel)
        sites: list[str] = []
        for family, finder in (
            ("http-error-envelope-helper", _envelope_helper_sites),
            ("verdict-type", _verdict_type_sites),
            ("durable-write", _durable_write_sites),
        ):
            if canonical.get(family) == rel:
                continue  # the canonical implementation is not a duplicate of itself
            sites.extend(f"{family}:{symbol}" for symbol in finder(tree))
        if sites:
            per_file[rel] = {"count": len(sites), "sites": sorted(sites)}
    return Scan(frozenset(inspected), dict(sorted(per_file.items())))


def _duplication_block() -> dict[str, Any]:
    per_file = scan(RATCHET_DUPLICATION).rows
    by_family: dict[str, int] = {f.name: 0 for f in DUPLICATE_FAMILIES}
    for entry in per_file.values():
        for site in entry["sites"]:
            by_family[site.split(":", 1)[0]] += 1
    return {
        "families": [
            {"name": f.name, "canonical": f.canonical, "rationale": f.rationale}
            for f in DUPLICATE_FAMILIES
        ],
        "per_file": per_file,
        "totals": {
            "sites": sum(int(e["count"]) for e in per_file.values()),
            "files": len(per_file),
            "by_family": dict(sorted(by_family.items())),
        },
    }


def regressions_duplication(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Files whose duplicate-implementation counter ROSE, sorted. A DECREASE means a
    re-derivation was deleted or folded into the canonical, and is welcome."""
    base_per_file: dict[str, Any] = baseline["per_file"]
    lines: list[str] = []
    for rel in sorted(current["per_file"]):
        now = int(current["per_file"][rel]["count"])
        was = int(base_per_file.get(rel, {}).get("count", 0))
        if now <= was:
            continue
        new = sorted(
            set(current["per_file"][rel]["sites"])
            - set(base_per_file.get(rel, {}).get("sites", []))
        )
        lines.append(
            f"{rel}: re-derived implementations rose {was} -> {now}; new site(s): {new} "
            "— reuse the canonical implementation, do not raise the count"
        )
    return lines


# ── Inventory, vacuity, ratchet dispatch ─────────────────────────────────────


def build_inventory() -> dict[str, Any]:
    """Render all three structural ratchet baselines as a deterministic, JSON-safe dict.

    The live census count is deliberately NOT stored here. It changes whenever any module is
    added or deleted, so pinning it would make the byte-compare test demand a regeneration on
    routine commits — and a baseline people regenerate routinely is a baseline nobody reads.
    Vacuity is enforced at RUN time instead (``vacuity_failures``), against
    ``MIN_CENSUS_PY_FILES`` and against the census the run itself performed.
    """
    return {
        "generated_from": "scripts/generate_structural_baseline.py",
        "ratchets": list(RATCHETS),
        RATCHET_SIZE: _size_block(),
        RATCHET_IMPORT_DIRECTION: _import_direction_block(),
        RATCHET_DUPLICATION: _duplication_block(),
    }


def build_baseline() -> str:
    """Render the inventory as a deterministic JSON string (sorted, trailing newline)."""
    return json.dumps(build_inventory(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


_SCAN_CACHE: dict[str, tuple[tuple[object, object], Scan]] = {}


def scan(ratchet: str) -> Scan:
    """One ratchet's scan, memoized for the life of the process.

    Each scan walks 915 files and costs ~2s; without a memo the six-gate aggregate would
    re-walk the tree a dozen times (three ratchets x inventory + vacuity). The tree does not
    change while a gate runs, so memoizing is safe — but the cache KEY carries the two
    functions a falsification test monkeypatches (``_parse`` and ``_src_py_files``), so a
    narrowed walk invalidates the cache instead of quietly serving a stale clean result. A
    cache that outlived its inputs would be its own vacuity bug, which is the last thing this
    file should ship.
    """
    builders = {
        RATCHET_SIZE: scan_sizes,
        RATCHET_IMPORT_DIRECTION: scan_upward_edges,
        RATCHET_DUPLICATION: scan_duplicates,
    }
    if ratchet not in builders:
        raise ValueError(f"unknown structural ratchet {ratchet!r}; known: {list(RATCHETS)}")
    key = (_parse, _src_py_files)
    cached = _SCAN_CACHE.get(ratchet)
    if cached is not None and cached[0] == key:
        return cached[1]
    result = builders[ratchet]()
    _SCAN_CACHE[ratchet] = (key, result)
    return result


def vacuity_failures(ratchet: str | None = None) -> list[str]:
    """Vacuity assertions, as failure lines (one per broken ratchet).

    A rail that matches nothing looks clean, and that is the most common way a gate in this
    repo dies: a wrong root, a glob that stopped matching, a swallowed parse error, and the
    ratchet reports a spotless tree it never looked at. THREE checks, all keyed on the census:

    1. the census itself must see at least ``MIN_CENSUS_PY_FILES`` files (a broken walk);
    2. each ratchet must have inspected EXACTLY as many files as the census counted — fewer
       means it narrowed silently, and the count comes from the ratchet's OWN scan, not a
       parallel re-walk that could go stale;
    3. each ratchet must have touched every sub-package the census found on disk. A file count
       alone cannot see a whole package leaving the walk: with 66 packages, dropping one still
       leaves the total comfortably above any plausible floor.

    Pass ``ratchet`` to scope the result to one gate — that is how each gate reports its own
    vacuity failure through the aggregate instead of taking the other two down with it.
    """
    census = census_py_files()
    packages = census_packages()
    names = RATCHETS if ratchet is None else (ratchet,)
    failures: list[str] = []
    for name in names:
        if census < MIN_CENSUS_PY_FILES:
            failures.append(
                f"{name}: VACUITY — the census saw only {census} production .py files "
                f"(floor {MIN_CENSUS_PY_FILES}). The walk is broken, so a clean result means "
                "nothing. Fix the walk before trusting this gate."
            )
            continue
        inspected = scan(name).inspected
        if len(inspected) != census:
            failures.append(
                f"{name}: VACUITY — inspected {len(inspected)} of the {census} files the "
                "census counted. The ratchet narrowed silently; a rail that matches nothing "
                "looks clean."
            )
            continue
        # ``src/personalclaw/<pkg>/...`` → ``<pkg>``; ``src/personalclaw/mod.py`` → ``""``.
        seen_packages = {
            (parts[2] if len(parts) > 3 else "") for parts in (rel.split("/") for rel in inspected)
        }
        missed = sorted(packages - seen_packages)
        if missed:
            failures.append(
                f"{name}: VACUITY — inspected no file from {missed}. A whole sub-package left "
                "the walk while the file count stayed plausible."
            )
    return failures


def ratchet_failures(ratchet: str, baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Every failure line for ONE ratchet: its vacuity failures first, then its backslides.

    Scoped to a single ratchet on purpose — that is what lets ``scripts/gate_report.py``
    register the three as three INDEPENDENT gates, so three simultaneous structural failures
    report as three rather than the first one hiding the rest.
    """
    if ratchet not in RATCHETS:
        raise ValueError(f"unknown structural ratchet {ratchet!r}; known: {list(RATCHETS)}")
    failures = vacuity_failures(ratchet)
    comparer = {
        RATCHET_SIZE: regressions_size,
        RATCHET_IMPORT_DIRECTION: regressions_import_direction,
        RATCHET_DUPLICATION: regressions_duplication,
    }[ratchet]
    return failures + comparer(baseline[ratchet], current[ratchet])


def stale_high(ratchet: str, baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Counters whose COMMITTED value is above the current one — a legitimate shrink that was
    never regenerated, leaving the ratchet's floor looser than reality. Not a code defect;
    the fix is to regenerate in the commit that did the shrinking."""
    lines: list[str] = []
    base, now = baseline[ratchet], current[ratchet]
    if ratchet == RATCHET_SIZE:
        slack = int(now["ceiling_slack_steps"])
        if slack > 0:
            lower_to = int(base["ceiling_lines"]) - slack * int(base["ceiling_step_lines"])
            lines.append(
                f"ceiling: the largest file dropped {slack} full step(s) below the committed "
                f"ceiling of {base['ceiling_lines']} — lower SIZE_CEILING_LINES to {lower_to}"
            )
        departed = sorted(set(base["watch_band_members"]) - set(now["watch_band_members"]))
        if departed:
            lines.append(
                f"watch band: committed {len(base['watch_band_members'])} members > current "
                f"{len(now['watch_band_members'])}; {departed} left the band (a split landed)"
            )
        return lines
    key = "edges" if ratchet == RATCHET_IMPORT_DIRECTION else "count"
    for rel, entry in sorted(base["per_file"].items()):
        was = int(entry[key])
        has = int(now["per_file"].get(rel, {}).get(key, 0))
        if was > has:
            lines.append(f"{rel}: committed {was} > current {has}")
    return lines


def baseline_path() -> Path:
    """Repo-root location of the committed ``structural-baseline.json``."""
    return _repo_root() / "structural-baseline.json"


def main() -> None:
    path = baseline_path()
    path.write_text(build_baseline(), encoding="utf-8")
    inventory = build_inventory()
    print(f"wrote {path}")
    print(f"  census: {census_py_files()} production .py files (floor {MIN_CENSUS_PY_FILES})")
    size = inventory[RATCHET_SIZE]
    print(
        f"  {RATCHET_SIZE}: ceiling {size['ceiling_lines']} lines "
        f"({len(size['over_ceiling'])} over it, {size['ceiling_slack_steps']} step(s) of slack), "
        f"{size['totals']['watched_files']} files in the {size['watch_band_lines']}-line band "
        f"(live headroom {watch_band_headroom()})"
    )
    imports = inventory[RATCHET_IMPORT_DIRECTION]["totals"]
    print(
        f"  {RATCHET_IMPORT_DIRECTION}: {imports['edges']} upward edges in "
        f"{imports['files']} files {imports['by_rule']}"
    )
    dup = inventory[RATCHET_DUPLICATION]["totals"]
    print(
        f"  {RATCHET_DUPLICATION}: {dup['sites']} re-derived sites in {dup['files']} files "
        f"{dup['by_family']}"
    )


if __name__ == "__main__":
    main()
