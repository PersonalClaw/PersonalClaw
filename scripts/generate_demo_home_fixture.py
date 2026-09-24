#!/usr/bin/env python3
"""Regenerate the SQLite halves of the ``demo-home`` seed fixture.

``--seed`` is a bare ``shutil.copytree`` with no hydration hook, so every store a
seeded home is expected to serve has to already exist as a file in the fixture.
Most of ``demo-home`` is therefore hand-authored JSON/markdown and needs no tool.
Two surfaces cannot be: **knowledge** and **loops** are SQLite-only.

- Knowledge lives solely in ``workspace/knowledge/knowledge.db``
  (:func:`personalclaw.knowledge.store.knowledge_db_path`), whose schema includes an
  FTS5 virtual table. There is no boot-time re-ingest from any file, so markdown
  under ``workspace/knowledge/`` would simply never be read.
- A loop's row lives in ``loop/loops.db``. The file dir is the optional half: the
  boot-time ``reap_orphan_dirs()`` sweep **deletes** any ``loop/<8hex>/`` directory
  with no backing DB row, so a text-only loop fixture is silently wiped on first boot.

So this script builds those two ``.db`` files by driving the **real writers**
(``KnowledgeStore.create_typed_item`` and ``loop.store.create``) against a throwaway
home, then copies the results into the fixture tree. The shape is therefore correct
by construction rather than hand-transcribed, and re-running this after a schema
change is how the fixture is kept current.

Run it from the repo root, then commit the changed ``.db`` files::

    PYTHONPATH=src python scripts/generate_demo_home_fixture.py

``tests/test_seed_demo_home.py`` boots the fixture and asserts a non-zero count per
surface, so a schema change that invalidates these files fails loudly rather than
shipping a demo home that boots empty.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "src" / "personalclaw" / "tests_fixtures" / "demo-home"

# The fixture's authored project ids (see ``demo-home/projects/``). The loop is
# scoped to "Reading Pipeline" so the Loops surface shows a real project link
# rather than an orphan.
PROJECT_READING_PIPELINE = "p-2d6f5c83"

# Fixed so re-running the generator produces the same fixture rather than a diff of
# churned ids/timestamps. Dates line up with the authored memory history
# (``workspace/memory/history/2026-08-1{2,4}.md``).
LOOP_ID = "a17c3f92"


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp()


# ── the demo content ──────────────────────────────────────────────────────────
# Believable, non-personal, non-proprietary: a fictional reading-digest side project
# and a home NAS. No real names, emails, hostnames or tokens. URLs use the RFC 2606
# reserved ``example.com`` so nothing here resolves to a real site.

KNOWLEDGE_ITEMS = [
    {
        "item_type": "note",
        "title": "What makes a weekly digest actually get read",
        "tags": ["digest", "writing"],
        "content": (
            "Three things separate a digest I reread from one I skip.\n\n"
            "1. One claim per entry. If a summary needs two sentences to say what the "
            "piece argues, the piece probably argues two things and should be split.\n"
            "2. Lead with the disagreement. The useful part of a long read is usually "
            "where it breaks with the consensus, not its restatement of the setup.\n"
            "3. Keep the link's promise. If the headline says a number, the summary "
            "carries the number, otherwise the digest is a worse index than the inbox "
            "it replaced.\n\n"
            "Open question: whether to keep a 200-word cap per entry. It forces the "
            "edit, but the pieces that genuinely hold two claims end up mangled by it."
        ),
    },
    {
        "item_type": "bookmark",
        "title": "SQLite as an application file format",
        "url": "https://example.com/articles/sqlite-as-an-application-file-format",
        "tags": ["sqlite", "architecture"],
        "summary": (
            "Argues a single-file database beats a directory of ad-hoc files for "
            "application state: atomic writes, one thing to back up, queryable "
            "without bespoke parsing."
        ),
        "content": (
            "Kept for the durability argument, not the pitch. The part worth reusing: "
            "a pile of small JSON files has no transaction boundary, so a crash "
            "mid-write leaves a state nothing can validate. Relevant to the digest "
            "store, which is currently exactly that pile."
        ),
    },
    {
        "item_type": "note",
        "title": "Home NAS backup checklist",
        "tags": ["home-server", "backups"],
        "content": (
            "Runs monthly, takes about twenty minutes.\n\n"
            "- Verify last night's snapshot actually restores. A snapshot that has "
            "never been restored is a guess, not a backup.\n"
            "- Check free space trend, not free space. The absolute number looks fine "
            "right up to the week it doesn't.\n"
            "- Confirm the offsite copy finished. It fails quietly when the upstream "
            "connection drops mid-transfer.\n"
            "- Re-read the restore notes. If they aren't good enough to follow while "
            "tired and annoyed, they aren't good enough."
        ),
    },
    {
        "item_type": "note",
        "title": "Picking an embedding model for a small personal corpus",
        "tags": ["embeddings", "reading-pipeline"],
        "content": (
            "The corpus is a few thousand notes and saved articles, so retrieval "
            "quality matters more than throughput, and everything runs locally.\n\n"
            "Findings so far: the smallest models lose the distinction between two "
            "notes on the same topic written months apart, which is exactly the "
            "distinction the digest needs. A mid-sized model fixed that and still "
            "indexes the whole corpus in a few minutes. Dimensionality mattered less "
            "than expected; chunk boundaries mattered much more."
        ),
    },
    {
        "item_type": "bookmark",
        "title": "Why RSS outlived its obituaries",
        "url": "https://example.com/posts/why-rss-outlived-its-obituaries",
        "tags": ["reading", "rss"],
        "summary": (
            "A defence of feeds as a reading primitive: no ranking, no account, and "
            "the reader decides what counts as new."
        ),
        "content": (
            "Useful framing for the pipeline: the feed is the transport, the digest is "
            "the edit. Conflating the two is what made the first version unreadable — "
            "it published everything the feed produced."
        ),
    },
]

# The loop's six cycles, two per plan phase. These are the LEDGER, not decoration: since PP-16
# seam 4a retired the `loops.total_cycles` column, a loop's cycle count IS its `step_completed`
# count, so a fixture that wants to show a six-cycle run has to ship six cycles. The version this
# replaced stored `total_cycles=6` on the row with an EMPTY ledger — six cycles claimed, zero
# recorded — which is exactly the cache/projection divergence that retirement removes, and it made
# the demo cockpit render "6 cycles ran — no per-cycle detail recorded for this loop."
# `step` keys each cycle to its plan phase (``journal._node_id`` reads it), so the mined trajectory
# has real structure rather than six identical nodes.
LOOP_CYCLES = [
    {
        "step": "survey",
        "summary": "Reviewed four weeks of digests and tagged every entry that got skipped.",
        "key_insight": (
            "Skipped entries cluster: 11 of 14 were two-claim summaries. The digest is not too "
            "long, it is under-split."
        ),
        "evidence": (
            "28 entries across 4 issues. 14 skipped on re-read. Failure tags: two-claim "
            "summary 11, no number where the headline had one 6, setup restated 5."
        ),
        "next_step": "Check whether the two-claim entries share a source type.",
    },
    {
        "step": "survey",
        "summary": "Ranked the failure modes and confirmed one dominates.",
        "key_insight": (
            "Two-claim summaries are 40% of entries and 79% of skips — every other mode is "
            "noise next to it."
        ),
        "evidence": (
            "Ranked: two-claim 11/14 skips, dropped number 6, restated setup 5, missing "
            "disagreement 3. Long-read sources produce two-claim entries at 3x the rate of posts."
        ),
        "next_step": "Derive rules from the entries that were NOT skipped.",
    },
    {
        "step": "synthesize",
        "summary": "Drafted seven candidate rules from the entries that survived re-reading.",
        "key_insight": (
            "Four of the seven drafts restate one another — the rule set wants to be about "
            "splitting, not about length."
        ),
        "evidence": (
            "Drafted 7. Pairwise check found 4 overlaps (three of them variants of 'one claim "
            "per entry'). 3 rules are independent and each is checkable from a single entry."
        ),
        "next_step": "Collapse the overlaps and re-check each rule against a skipped entry.",
    },
    {
        "step": "synthesize",
        "summary": "Collapsed to four rules, each checkable by reading one entry.",
        "key_insight": (
            "The 200-word cap is not a rule, it is a symptom of the split rule being unstated — "
            "drop the cap and keep the split."
        ),
        "evidence": (
            "Final four: one claim per entry; lead with the disagreement; carry the headline's "
            "number; no restated setup. Each applied to all 14 skipped entries without needing "
            "a second rule to decide."
        ),
        "next_step": "Re-edit a real past digest against the four rules.",
    },
    {
        "step": "verify",
        "summary": "Re-edited the oldest of the four digests against the rule set.",
        "key_insight": (
            "The split rule turned 7 entries into 11 and the issue got SHORTER to read — the "
            "cap was fighting the edit, not enforcing it."
        ),
        "evidence": (
            "7 entries → 11. Word count 1,340 → 1,190. Every change traces to exactly one rule: "
            "split 4, disagreement-first 3, restored number 2, cut restated setup 5."
        ),
        "next_step": "Check the rules against an issue that was already considered good.",
    },
    {
        "step": "verify",
        "summary": "Applied the rules to a digest that had NOT been flagged, as a control.",
        "key_insight": (
            "Three of four rules changed nothing on the good issue — the rule set describes "
            "what the good entries were already doing, so it is a rule set and not a taste."
        ),
        "evidence": (
            "Control issue: 9 entries, 2 changes (both restored numbers). Split and "
            "disagreement-first fired zero times, which is the result that makes the set "
            "credible rather than merely agreeable."
        ),
        "next_step": "Rules stand. Publish the set and re-read next week's issue against it.",
    },
]

LOOP_PLAN = [
    {
        "phase": "survey",
        "title": "Survey the current digest output",
        "objective": "Establish what the digest does badly today, with examples.",
        "exit_criteria": [
            "Four weeks of digests reviewed",
            "Each weak entry labelled with a concrete failure mode",
        ],
        "deliverable": "A list of failure modes ranked by how often they occur.",
    },
    {
        "phase": "synthesize",
        "title": "Derive the rules the good entries follow",
        "objective": "Turn the failure modes into a short, checkable style rule set.",
        "exit_criteria": [
            "Every rule is checkable by reading one entry",
            "No rule restates another",
        ],
        "deliverable": "A style rule set short enough to remember.",
    },
    {
        "phase": "verify",
        "title": "Re-edit a past digest against the rules",
        "objective": "Confirm the rules improve a real digest rather than just reading well.",
        "exit_criteria": [
            "One past digest fully re-edited",
            "Each change traced to a specific rule",
        ],
        "deliverable": "A before/after digest and the rules that survived contact.",
    },
]


def build(home: Path) -> None:
    """Write the knowledge items + the one loop into ``home`` via the real writers."""
    # PERSONALCLAW_HOME must be set before importing personalclaw: several stores
    # resolve config_dir() at import time and freeze it.
    from personalclaw.knowledge.store import KnowledgeStore, knowledge_db_path
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop, LoopStatus

    store = KnowledgeStore(str(knowledge_db_path(home)))
    made = 0
    for spec in KNOWLEDGE_ITEMS:
        item_id = store.create_typed_item(**spec)
        if not item_id:
            raise RuntimeError(f"knowledge item refused: {spec['title']!r}")
        made += 1
    print(f"knowledge: wrote {made} items")

    loop = Loop(
        id=LOOP_ID,
        name="Weekly reading digest quality pass",
        kind="research",
        task=(
            "Work out why the weekly reading digest is skippable and produce a style "
            "rule set that makes it worth rereading."
        ),
        project_id=PROJECT_READING_PIPELINE,
        summary=(
            "Review recent digests, name the failure modes, and derive checkable "
            "editing rules from the entries that work."
        ),
        plan=LOOP_PLAN,
        phase_status={"survey": "done", "synthesize": "done", "verify": "done"},
        # Terminal on purpose. A seeded 'running' or 'planning' loop is re-armed at
        # gateway boot and would spend real model calls on someone's demo machine.
        status=LoopStatus.COMPLETE.value,
        created_at=_ts("2026-08-11T09:12:00"),
        completed_at=_ts("2026-08-14T17:40:00"),
        elapsed_seconds=8_940.0,
        max_cycles=30,
        autopilot=True,
        success_criteria=(
            "A style rule set that a past digest can be re-edited against, with each "
            "edit traceable to a rule."
        ),
    )
    loop_store.create(loop)
    _write_loop_ledger(loop.id)
    print(f"loops: wrote 1 loop ({loop.id}, status={loop.status}) + {len(LOOP_CYCLES)} cycles")


def _write_loop_ledger(loop_id: str) -> None:
    """Emit the loop's six cycles through the REAL ledger writer (``LoopJournal.cycle``).

    Driven through the production emitter for the same reason the DB halves are: the shape is
    correct by construction. Two details are fixture-specific:

    * **The clock is frozen per cycle.** ``LedgerWriter._append`` stamps ``ts`` from
      ``ledger.writer.now()``. Left alone, every regeneration would churn six timestamps in a
      committed file. The stamps are spaced across the loop's real ``created_at``→``completed_at``
      window so the cockpit's per-cycle trail reads as a run that took three days.
    * **``findings/`` files are NOT written.** They are the worker's ingest SOURCE, not a store
      (``store.get_findings`` projects off the ledger), so shipping them would put a second copy
      of every cycle in the fixture for no reader.
    """
    from personalclaw.ledger import writer as ledger_writer
    from personalclaw.loop.journal import LoopJournal

    stamps = [
        "2026-08-11T10:40:00Z",
        "2026-08-11T15:05:00Z",
        "2026-08-12T09:20:00Z",
        "2026-08-12T16:55:00Z",
        "2026-08-14T11:10:00Z",
        "2026-08-14T17:35:00Z",
    ]
    assert len(stamps) == len(LOOP_CYCLES), "one frozen timestamp per cycle"
    real_now = ledger_writer.now
    try:
        for i, (cycle, stamp) in enumerate(zip(LOOP_CYCLES, stamps), start=1):
            ledger_writer.now = lambda s=stamp: s  # type: ignore[assignment]
            LoopJournal.open(loop_id).cycle(i, {"cycle": i, **cycle})
    finally:
        ledger_writer.now = real_now  # type: ignore[assignment]


def _checkpoint(db: Path) -> None:
    """Fold the WAL back into the .db file.

    Both stores open with ``PRAGMA journal_mode=WAL``, so the writes above live in a
    sidecar ``-wal`` until checkpointed. Copying the bare ``.db`` without this ships a
    fixture that boots EMPTY — the failure this whole atom exists to avoid.
    """
    if not db.exists():
        raise FileNotFoundError(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def _assert_no_absolute_paths(db: Path, needle: str) -> None:
    """Fail if the generation home's path leaked into the committed bytes.

    Knowledge items reached through a ``file_path`` store an ABSOLUTE path, so a
    file-backed item would bake this machine's layout into the wheel and 404 on a
    user's home. The demo items are text/url only; this asserts that stays true.
    """
    blob = db.read_bytes()
    if needle.encode() in blob:
        raise SystemExit(f"{db.name} contains the generation path {needle!r} — refusing to ship it")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="Build into a temp home and report counts without touching the fixture.",
    )
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="demo-home-gen-"))
    home = tmp / "home"
    # Start from the authored text tree so the generated DBs sit in a home that
    # already has the projects/tasks the loop refers to.
    shutil.copytree(FIXTURE, home)
    # …but NOT this script's own previous output. Every writer below is additive
    # (``create_typed_item`` appends, ``loop_store.create`` INSERTs a fixed id), so building on
    # top of the committed artifacts duplicated the five knowledge items and failed outright on
    # ``UNIQUE constraint failed: loops.id``. The docstring promises re-running the generator is
    # how the fixture is kept current, so the generated half is discarded first and rebuilt from
    # the authored text tree alone. Anything listed here MUST be produced below.
    for generated in (
        home / "workspace" / "knowledge" / "knowledge.db",
        home / "loop" / "loops.db",
        home / "loop" / LOOP_ID,
    ):
        if generated.is_dir():
            shutil.rmtree(generated)
        elif generated.exists():
            generated.unlink()
    os.environ["PERSONALCLAW_HOME"] = str(home)

    try:
        build(home)
        knowledge_db = home / "workspace" / "knowledge" / "knowledge.db"
        loops_db = home / "loop" / "loops.db"
        for db in (knowledge_db, loops_db):
            _checkpoint(db)
            _assert_no_absolute_paths(db, str(tmp))

        if args.check:
            print(f"--check: built {knowledge_db} and {loops_db}, fixture untouched")
            return 0

        (FIXTURE / "workspace" / "knowledge").mkdir(parents=True, exist_ok=True)
        (FIXTURE / "loop").mkdir(parents=True, exist_ok=True)
        shutil.copy2(knowledge_db, FIXTURE / "workspace" / "knowledge" / "knowledge.db")
        shutil.copy2(loops_db, FIXTURE / "loop" / "loops.db")
        # status.json is written by loop_store.create() and is the worker's interface; the two
        # jsonl files are the loop's LEDGER, which is where its cycle count now lives (PP-16 seam
        # 4a — `total_cycles` is no longer a column, so an empty ledger means a zero-cycle loop).
        # The dir survives the boot-time orphan reap because the DB row exists.
        dest = FIXTURE / "loop" / LOOP_ID
        for name in ("status.json", "journal.jsonl", "events.jsonl"):
            src = home / "loop" / LOOP_ID / name
            if not src.exists():
                raise SystemExit(f"generator produced no {name} for loop {LOOP_ID}")
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest / name)
        print(f"fixture updated: {FIXTURE}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
