#!/usr/bin/env python3
"""Regenerate the ``six-month-home`` state-survival fixture and its manifest (RET-1).

**This script must NOT be run against HEAD.** It is the one generator in the repo whose
whole value depends on running against *older* code: the fixture exists to prove that a
home written by a **prior release** still loads on HEAD, so writing it with HEAD's
writers would prove nothing — every store would trivially be in the shape HEAD expects.
The version check in :func:`_require_prior_release` refuses to run on any other version,
so this cannot be satisfied by accident.

How to regenerate (from a checkout of the prior release, in its own venv)::

    git worktree add --detach /tmp/pclaw-v013 v0.1.3
    cd /tmp/pclaw-v013 && python3 -m venv .venv && .venv/bin/pip install -e .
    .venv/bin/python <this-repo>/scripts/generate_six_month_home_fixture.py

The script writes into ``<this-repo>/src/personalclaw/tests_fixtures/six-month-home/``
plus the sibling manifest, both of which are committed. It resolves the target repo from
its own location, NOT from the interpreter's ``personalclaw``, so running it from the old
checkout still updates the new one.

Every record below is written through the prior release's **real writers** — no
hand-transcribed rows, no hand-built SQLite. That is the only way the committed bytes are
a prior release's shape by construction rather than by claim.

The manifest (``six-month-home.manifest.json``) is what ``tests/test_state_survival.py``
asserts against, per store and by name. It is DATA, deliberately not derived at test time
from the fixture: a test that counted the fixture's rows and then asserted the count it
just read would pass on a fixture that had been silently emptied to zero.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "src" / "personalclaw" / "tests_fixtures"
FIXTURE = FIXTURES_DIR / "six-month-home"
MANIFEST = FIXTURES_DIR / "six-month-home.manifest.json"

# The release whose on-disk state shape this fixture captures. Bumping this means
# regenerating the fixture from that release's code — the point of the fixture is the
# gap between the writing release and HEAD, so a bump with no regeneration is a lie.
PRIOR_RELEASE = "0.1.3"

# Everything below is frozen so re-running the generator produces the same bytes rather
# than a diff of churned ids and timestamps. The dates read as a home in use across six
# months (2026-03 → 2026-07), which is what the fixture's name claims.
FROZEN_NOW = "2026-07-14T09:32:11+00:00"
LOOP_ID = "3f8a1c07"
KNOWLEDGE_IDS = (
    "5a1d0c74-0b8e-4d21-9f36-2c7ab4e10d55",
    "7c93f2ab-61d4-4e08-8a72-19be5d3c4f60",
    "9e04b71c-2f56-4a93-b1d8-63ca70e2851f",
)
EPISODIC_IDS = (
    "1b6f8e35-4c07-4d92-a58b-7e21fd0c4a63",
    "4d27a091-8b5e-4f16-93ca-05e7b2d81f47",
)

# ── the fixture's content ─────────────────────────────────────────────────────────────
# Fictional and non-personal: a self-hosted media/backup home lab plus a reading habit.
# No real names, emails, hostnames or tokens; URLs use the RFC 2606 reserved
# ``example.com`` so nothing here resolves anywhere.

KNOWLEDGE_ITEMS: tuple[dict[str, Any], ...] = (
    {
        "item_type": "note",
        "title": "Backup rotation I actually follow",
        "tags": ["backup", "homelab"],
        "content": (
            "Three tiers, because two was not enough and four never got done.\n\n"
            "Nightly: the working set to the second internal disk. Cheap, fast, and the "
            "one that has actually saved me — twice, both times a bad edit rather than a "
            "dead disk.\n"
            "Weekly: the same set to an external drive that lives unplugged. This is the "
            "tier that survives the mistakes the nightly one propagates.\n"
            "Monthly: encrypted off-site. Slow enough that I only notice it when the "
            "monthly report says it did not run.\n\n"
            "The rule that made this stick: a restore I have not tested is a backup I do "
            "not have, so the monthly report includes one randomly chosen file restored "
            "into a scratch directory and diffed."
        ),
    },
    {
        "item_type": "note",
        "title": "Why the reading queue kept growing",
        "tags": ["reading", "habits"],
        "content": (
            "The queue grew because saving was one tap and reading was forty minutes. "
            "Fixing the imbalance meant making saving cost something.\n\n"
            "Now a save has to carry one line saying what I expect to get out of it. "
            "About a third of the things I used to save do not survive writing that line, "
            "which is the whole point — they were reflexes, not intentions.\n\n"
            "What did not work: a weekly cap on saves. I hit the cap on Monday and spent "
            "the rest of the week routing around my own rule."
        ),
    },
    {
        "item_type": "bookmark",
        "title": "A filesystem checklist worth rereading",
        "tags": ["backup", "reference"],
        "url": "https://notes.example.com/filesystem-checklist",
        "content": (
            "Kept for the section on silent corruption: the argument is that a checksum "
            "you never verify is documentation, not protection, and that the interval "
            "between verifications is the real window of data loss. The rest of the piece "
            "is a tour of options I do not use, but that one section changed how I "
            "schedule the monthly pass."
        ),
    },
)

# Semantic memory keys must match the store's built-in prefixes (pref./project./user./
# lesson.) or the write is REJECTED — a silent zero-row store is exactly the failure this
# fixture exists to catch, so the generator asserts each one landed.
MEMORY_SEMANTIC: tuple[dict[str, Any], ...] = (
    {
        "key": "pref.digest_delivery_hour",
        "value": "07:00 local, before the first meeting, never in the evening",
        "confidence": 0.9,
    },
    {
        "key": "pref.summary_length",
        "value": "one paragraph; a bulleted summary of a bulleted source is a copy",
        "confidence": 0.8,
    },
    {
        "key": "project.homelab_backup_tiers",
        "value": "nightly internal, weekly unplugged external, monthly encrypted off-site",
        "confidence": 0.95,
    },
    {
        "key": "user.timezone_habit",
        "value": "schedules everything in local wall-clock time, not UTC",
        "confidence": 0.85,
    },
    {
        "key": "lesson.verify_before_reporting_success",
        "value": (
            "a job that reports success without reading back what it wrote has reported "
            "nothing; check the row count, not the exit code"
        ),
        "confidence": 0.9,
    },
)

MEMORY_EPISODIC: tuple[str, ...] = (
    "Moved the monthly off-site pass from Sunday night to Saturday morning after two "
    "months of it silently colliding with the weekly external copy.",
    "Decided the reading digest should lead with the disagreement rather than the summary; "
    "the summary is what the source already says.",
)

MEMORY_PREFERENCES_MD = """# Preferences

- Reading digest arrives at 07:00 local. Never in the evening; an evening digest becomes
  a Saturday backlog.
- One paragraph per entry. A bulleted summary of a bulleted source is a copy, not a
  summary.
- Lead with where a piece disagrees with the consensus. The setup is the part I can skip.
- Schedules are in local wall-clock time. If a job's hour matters, say the zone out loud.
"""

MEMORY_PROJECTS_MD = """# Projects

## Home lab
Three-tier backup (nightly internal, weekly unplugged external, monthly encrypted
off-site). The monthly pass restores one randomly chosen file and diffs it — a restore I
have not tested is a backup I do not have.

## Reading pipeline
A queue with a cost: every save carries one line saying what I expect from it. About a
third of saves do not survive writing that line.
"""

MEMORY_HISTORY_MD = {
    "2026-03-02": """# 2026-03-02

#### 09:14
Set up the three backup tiers. The weekly external one is the tier I keep skipping, so it
is now the one with a report.

#### 18:40
Reading queue is at 140 items. That is not a queue, it is a landfill.
""",
    "2026-07-11": """# 2026-07-11

#### 08:05
Six months in. The monthly restore-and-diff has caught exactly one real problem — a
truncated file the nightly copy had already propagated twice.

#### 21:22
Reading queue down to 31. The one-line-why rule did that, not discipline.
""",
}

# ScheduleRun history: two jobs, five runs, including one honest failure. A history where
# every run succeeded is not a six-month home, it is a demo.
SCHEDULE_RUNS: tuple[dict[str, Any], ...] = (
    {
        "run_id": "a41c7e0b2d95",
        "job_id": "monthly-offsite-verify",
        "trigger": "scheduled",
        "started_at": "2026-05-02T06:00:00+00:00",
        "duration_ms": 918_432,
        "status": "success",
        "summary": "off-site pass complete; restored 1 file and diffed clean",
    },
    {
        "run_id": "b72f9d18c604",
        "job_id": "monthly-offsite-verify",
        "trigger": "scheduled",
        "started_at": "2026-06-06T06:00:00+00:00",
        "duration_ms": 240_115,
        "status": "failure",
        "summary": "off-site target unreachable; nothing was written",
        "error": "connection refused after 3 attempts",
    },
    {
        "run_id": "c05e3a7b91df",
        "job_id": "monthly-offsite-verify",
        "trigger": "manual",
        "started_at": "2026-06-06T19:12:00+00:00",
        "duration_ms": 1_022_774,
        "status": "success",
        "summary": "re-ran by hand after the target came back; restore diff clean",
    },
    {
        "run_id": "d38b6c24f70a",
        "job_id": "weekly-reading-digest",
        "trigger": "scheduled",
        "started_at": "2026-07-05T14:00:00+00:00",
        "duration_ms": 61_508,
        "status": "success",
        "summary": "digest sent; 6 entries, 2 dropped for no one-line why",
    },
    {
        "run_id": "e91d47a0b3c8",
        "job_id": "weekly-reading-digest",
        "trigger": "scheduled",
        "started_at": "2026-07-12T14:00:00+00:00",
        "duration_ms": 58_942,
        "status": "success",
        "summary": "digest sent; 4 entries",
    },
)

# entity_settings/*.json — two files, both carrying values that differ from the shipped
# defaults. A fixture that stored the defaults could not tell "my setting survived" from
# "the setting was reset and happens to match".
ENTITY_SETTINGS: dict[str, dict[str, Any]] = {
    "inbox": {"auto_cleanup_enabled": False, "retention_days": 400},
    "notifications": {"digest_enabled": True, "digest_hour": 7, "quiet_hours_enabled": True},
}

# config.json — fields chosen because they are plain, long-lived and USER-CHOSEN. Every
# one is asserted by name after boot, so a rename that drops the value fails loudly.
CONFIG_VALUES: dict[str, Any] = {
    "timezone": "America/Los_Angeles",
    "observe_max_messages": 250,
    "observe_ttl_hours": 96,
}


FROZEN_DT = datetime.fromisoformat(FROZEN_NOW)
FROZEN_EPOCH = FROZEN_DT.timestamp()


class _FrozenDatetime(datetime):
    """``datetime`` whose ``now``/``utcnow`` are pinned to :data:`FROZEN_NOW`."""

    @classmethod
    def now(cls, tz: Any = None) -> Any:  # type: ignore[override]
        return FROZEN_DT if tz is not None else FROZEN_DT.replace(tzinfo=None)

    @classmethod
    def utcnow(cls) -> Any:
        return FROZEN_DT.replace(tzinfo=None)


class _FrozenTime:
    """Stand-in for the ``time`` module with a pinned ``time()``.

    Everything else is delegated, so a store that also calls ``time.monotonic()`` or
    ``time.sleep()`` inside the frozen window still works.
    """

    def __init__(self, real: Any) -> None:
        self._real = real

    def time(self) -> float:
        return FROZEN_EPOCH

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _ids(*explicit: str) -> Iterator[str]:
    """Yield the given ids, then a deterministic tail.

    The tail matters: a writer that mints one more id than expected would otherwise raise
    ``StopIteration`` from inside a store, surfacing as an unrelated failure. Yielding a
    derived-but-stable value instead keeps the run reproducible AND diagnosable — the
    per-store row-count assertions in each builder are what catch a miscount.
    """
    yield from explicit
    counter = 0
    while True:
        counter += 1
        yield f"00000000-0000-4000-8000-{counter:012d}"


@contextlib.contextmanager
def _frozen(*modules: Any, ids: Iterator[str] | None = None) -> Iterator[None]:
    """Pin the clock (and optionally the id source) of each module during a write.

    The stores stamp ``datetime.now()`` / ``time.time()`` and mint ``uuid4()`` through
    module-level names, so without this every regeneration churns timestamps and ids
    *inside committed binary files* — a fixture nobody can review as a diff, and a manifest
    that has to be rewritten on every run whether or not anything changed. Patching the
    module attribute (rather than the stdlib) keeps the freeze scoped to the writer.
    """
    saved: list[tuple[Any, str, Any]] = []
    for module in modules:
        for name, replacement in (
            ("datetime", _FrozenDatetime),
            ("time", None),
            ("uuid4", None),
        ):
            real = getattr(module, name, None)
            if real is None:
                continue
            if name == "time":
                replacement = _FrozenTime(real)
            elif name == "uuid4":
                if ids is None:
                    continue
                replacement = lambda: next(ids)  # noqa: E731 — one-liner by design
            saved.append((module, name, real))
            setattr(module, name, replacement)
    try:
        yield
    finally:
        for module, name, real in reversed(saved):
            setattr(module, name, real)


def _require_prior_release() -> None:
    """Refuse to run on anything but ``PRIOR_RELEASE``.

    Without this the generator is one ``python`` away from silently writing HEAD's shape
    into a fixture whose entire claim is that it is NOT HEAD's shape — and nothing
    downstream could tell, because a HEAD-written home passes every survival assertion
    trivially.
    """
    import personalclaw

    got = getattr(personalclaw, "__version__", "")
    if got != PRIOR_RELEASE:
        raise SystemExit(
            f"refusing to generate: this must run against PersonalClaw {PRIOR_RELEASE}'s "
            f"code, but the importable personalclaw is {got or '<unknown>'} "
            f"({personalclaw.__file__}). See this script's docstring for the two "
            "commands that set up a prior-release checkout."
        )


# ── the six stores ────────────────────────────────────────────────────────────────────


def _build_knowledge(home: Path) -> dict[str, Any]:
    from personalclaw.knowledge import store as knowledge_store

    db_path = home / "workspace" / "knowledge" / "knowledge.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = knowledge_store.KnowledgeStore(str(db_path))
    written: list[dict[str, Any]] = []
    with _frozen(knowledge_store, ids=_ids(*KNOWLEDGE_IDS)):
        for item in KNOWLEDGE_ITEMS:
            item_id = store.create_typed_item(**item)
            written.append({"id": item_id, "title": item["title"], "item_type": item["item_type"]})

    rows = store.db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if rows != len(KNOWLEDGE_ITEMS):
        raise SystemExit(f"knowledge: wrote {len(KNOWLEDGE_ITEMS)} items, store holds {rows}")
    return {
        "path": "workspace/knowledge/knowledge.db",
        "count_sql": "SELECT COUNT(*) FROM items",
        "count": rows,
        "named_record": written[0],
        "records": written,
    }


def _build_memory_db(home: Path) -> dict[str, Any]:
    from personalclaw import memory_record as memory_record_mod
    from personalclaw import vector_memory as vector_memory_mod
    from personalclaw.memory_record import MemoryKind, MemoryRecord
    from personalclaw.vector_memory import VectorMemoryStore

    store = VectorMemoryStore(db_path=home / "memory.db")
    # ``init()`` is inside the freeze because it stamps ``schema_version.applied_at`` for
    # every migration it applies — nine wall-clock strings that would otherwise churn the
    # committed binary on every regeneration.
    with _frozen(vector_memory_mod, memory_record_mod, ids=_ids(*EPISODIC_IDS)):
        store.init()
        records = [
            MemoryRecord(
                id=row["key"],
                kind=MemoryKind.LESSON if row["key"].startswith("lesson.") else MemoryKind.SEMANTIC,
                value=row["value"],
                confidence=row["confidence"],
                source="user_explicit",
            )
            for row in MEMORY_SEMANTIC
        ] + [
            MemoryRecord(id="", kind=MemoryKind.EPISODIC, text=text, source="user_explicit")
            for text in MEMORY_EPISODIC
        ]
        store.put(records)

    semantic = store.db.execute("SELECT COUNT(*) FROM semantic_memory").fetchone()[0]
    episodic = store.db.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]
    if semantic != len(MEMORY_SEMANTIC):
        # set_semantic returns a reject code rather than raising, so a key that fails the
        # allowlist or the confidence floor is dropped in silence. Catch it here.
        raise SystemExit(
            f"memory.db: wrote {len(MEMORY_SEMANTIC)} semantic rows, store holds {semantic} "
            "— a key was rejected by the allowlist or the confidence floor"
        )
    if episodic != len(MEMORY_EPISODIC):
        raise SystemExit(
            f"memory.db: wrote {len(MEMORY_EPISODIC)} episodic rows, store holds {episodic}"
        )
    named = MEMORY_SEMANTIC[0]
    store.close()
    return {
        "path": "memory.db",
        "count_sql": "SELECT COUNT(*) FROM semantic_memory",
        "count": semantic,
        "episodic_count": episodic,
        "named_record": {"key": named["key"], "value": named["value"]},
        "keys": [row["key"] for row in MEMORY_SEMANTIC],
    }


def _build_memory_markdown(home: Path) -> dict[str, Any]:
    from personalclaw.memory import MemoryStore

    store = MemoryStore()
    store.init()
    store.write_preferences(MEMORY_PREFERENCES_MD)
    store.write_projects(MEMORY_PROJECTS_MD)
    history = home / "workspace" / "memory" / "history"
    history.mkdir(parents=True, exist_ok=True)
    for day, text in MEMORY_HISTORY_MD.items():
        (history / f"{day}.md").write_text(text, encoding="utf-8")

    files = sorted(
        str(p.relative_to(home)) for p in (home / "workspace" / "memory").rglob("*.md")
    )
    return {
        "path": "workspace/memory",
        "count": len(files),
        "files": files,
        "named_record": {
            "file": "workspace/memory/preferences.md",
            "contains": "A bulleted summary of a bulleted source is a copy",
        },
    }


def _build_loops(home: Path) -> dict[str, Any]:
    from personalclaw.loop import store as loop_store
    from personalclaw.loop.loop import Loop, LoopStatus

    plan = [
        {
            "phase": "measure",
            "title": "Measure the current rotation",
            "objective": "Establish what actually runs versus what is scheduled.",
            "exit_criteria": ["Every tier has a dated last-success", "Gaps named, not guessed"],
        },
        {
            "phase": "close",
            "title": "Close the weekly gap",
            "objective": "Make the tier that keeps getting skipped report for itself.",
            "exit_criteria": ["The weekly pass reports on skip as well as success"],
        },
        {
            "phase": "verify",
            "title": "Prove a restore",
            "objective": "Restore one randomly chosen file and diff it.",
            "exit_criteria": ["One restored file diffed clean", "The diff is in the report"],
        },
    ]
    loop = Loop(
        id=LOOP_ID,
        name="Backup rotation that reports on itself",
        kind="goal",
        task=(
            "Make the three backup tiers report their own skips, and prove a restore "
            "rather than assuming one."
        ),
        summary="Turn a rotation I keep skipping into one that tells me when I skipped it.",
        plan=plan,
        phase_status={"measure": "done", "close": "done", "verify": "done"},
        # Terminal on purpose: the gateway RE-ARMS a seeded running/planning loop at boot,
        # which would spend real model calls on the machine of whoever seeded the fixture.
        status=LoopStatus.COMPLETE.value,
        created_at=datetime.fromisoformat("2026-06-08T16:20:00+00:00").timestamp(),
        completed_at=datetime.fromisoformat("2026-06-13T11:05:00+00:00").timestamp(),
        elapsed_seconds=15_420.0,
        max_cycles=30,
        autopilot=True,
        success_criteria="A restore diffed clean, with the diff visible in the monthly report.",
    )
    with _frozen(loop_store):
        loop_store.create(loop)

    conn = sqlite3.connect(str(home / "loop" / "loops.db"))
    try:
        rows = conn.execute("SELECT COUNT(*) FROM loops").fetchone()[0]
    finally:
        conn.close()
    if rows != 1:
        raise SystemExit(f"loops: expected 1 row, store holds {rows}")
    return {
        "path": "loop/loops.db",
        "count_sql": "SELECT COUNT(*) FROM loops",
        "count": rows,
        "named_record": {
            "id": LOOP_ID,
            "name": loop.name,
            "status": loop.status,
            "plan_phases": [p["phase"] for p in plan],
            "success_criteria": loop.success_criteria,
        },
    }


def _build_run_history(home: Path) -> dict[str, Any]:
    import asyncio

    from personalclaw.schedule_history import ScheduleRun, ScheduleRunStore

    store = ScheduleRunStore(home)

    async def _write() -> None:
        for row in SCHEDULE_RUNS:
            started = datetime.fromisoformat(row["started_at"]).timestamp()
            await store.append(
                ScheduleRun(
                    run_id=row["run_id"],
                    job_id=row["job_id"],
                    trigger=row["trigger"],
                    started_at=started,
                    finished_at=started + row["duration_ms"] / 1000.0,
                    duration_ms=row["duration_ms"],
                    status=row["status"],
                    summary=row["summary"],
                    trace=row["summary"],
                    error=row.get("error", ""),
                )
            )

    asyncio.run(_write())

    per_job: dict[str, int] = {}
    for row in SCHEDULE_RUNS:
        per_job[row["job_id"]] = per_job.get(row["job_id"], 0) + 1
    index = home / "cron-history" / "_index.jsonl"
    index_rows = sum(1 for line in index.read_text(encoding="utf-8").splitlines() if line.strip())
    if index_rows != len(SCHEDULE_RUNS):
        raise SystemExit(f"run history: index holds {index_rows} of {len(SCHEDULE_RUNS)} runs")
    named = SCHEDULE_RUNS[1]  # the failure — the row a "reset to defaults" would lose
    return {
        "path": "cron-history",
        "count": index_rows,
        "per_job": per_job,
        "named_record": {
            "job_id": named["job_id"],
            "run_id": named["run_id"],
            "status": named["status"],
            "summary": named["summary"],
            "error": named["error"],
        },
    }


def _build_entity_settings(home: Path) -> dict[str, Any]:
    from personalclaw.providers.entity_routes import _save_entity_settings

    for entity, settings in ENTITY_SETTINGS.items():
        _save_entity_settings(entity, settings)

    files = sorted(p.name for p in (home / "entity_settings").glob("*.json"))
    if files != sorted(f"{e}.json" for e in ENTITY_SETTINGS):
        raise SystemExit(f"entity_settings: wrote {files}")
    return {
        "path": "entity_settings",
        "count": len(files),
        "files": files,
        "named_record": {
            "entity": "inbox",
            "settings": ENTITY_SETTINGS["inbox"],
        },
    }


def _build_config(home: Path) -> dict[str, Any]:
    from personalclaw.config.loader import AppConfig

    cfg = AppConfig.load()
    for key, value in CONFIG_VALUES.items():
        setattr(cfg, key, value)
    cfg.save()

    path = home / "config.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    # ``AppConfig.save`` imports ``datetime`` inside the method, so the module-attribute
    # freeze cannot reach its ``meta.lastTouchedAt`` stamp. Pin it here instead — the write
    # itself still went through the real writer, and a wall-clock stamp is the one field in
    # the file that is neither state nor shape.
    raw.setdefault("meta", {})["lastTouchedAt"] = FROZEN_NOW
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    touched = (raw.get("meta") or {}).get("lastTouchedVersion", "")
    if touched != PRIOR_RELEASE:
        raise SystemExit(
            f"config.json records lastTouchedVersion={touched!r}, expected {PRIOR_RELEASE!r}"
        )
    for key, value in CONFIG_VALUES.items():
        if raw.get(key) != value:
            raise SystemExit(f"config.json: {key} round-tripped as {raw.get(key)!r}, not {value!r}")
    return {
        "path": "config.json",
        "count": len(raw),
        "last_touched_version": touched,
        # The prior release's own top-level key set. This is the fixture's SHAPE, and it is
        # what lets the survival test prove mechanically that the fixture was not
        # regenerated against HEAD: HEAD's writer emits a measurably different set (it
        # retired `auto_update`/`inbound` and added eighteen blocks), so two identical sets
        # would mean the fixture is HEAD's output wearing an older release's name.
        "top_level_keys": sorted(k for k in raw if k != "meta"),
        "named_record": dict(CONFIG_VALUES),
    }


def _checkpoint(db: Path) -> None:
    """Fold the WAL back into the ``.db`` file.

    Every SQLite store here opens with ``PRAGMA journal_mode=WAL``, so the writes above
    sit in a ``-wal`` sidecar until checkpointed. Committing the bare ``.db`` without this
    ships a fixture that boots EMPTY — precisely the silent-emptying this fixture exists
    to catch, which would make the survival test pass against nothing.
    """
    if not db.exists():
        raise FileNotFoundError(db)
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.commit()
    finally:
        conn.close()


def _drop_derived_state(home: Path) -> None:
    """Delete every path the prior release's own inventory marks ``derived=True``.

    Derived state is rebuilt from the stores that ARE declared (``memory_index.db`` and
    ``memory.faiss`` are refolded from ``memory.db``), so shipping it puts a second,
    staler copy of the same rows in the fixture — and the search index in particular bakes
    the generation home's absolute path into its bytes.

    Read off the inventory rather than hand-listed on purpose: a hand-written exclusion
    list cannot see a derived store added after it was written, and the fixture would
    quietly start shipping one.
    """
    from personalclaw.durability.inventory import INVENTORY

    for entry in INVENTORY:
        if not getattr(entry, "derived", False):
            continue
        target = home / entry.path
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _reject_leaked_paths(root: Path, needle: str) -> None:
    """Fail if the generation home's absolute path leaked into any committed byte.

    A baked-in ``/private/var/folders/...`` resolves on exactly one machine, so it is
    both a portability bug and a privacy leak in package data.
    """
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if needle.encode() in path.read_bytes():
            raise SystemExit(
                f"{path.relative_to(root)} embeds the generation path {needle!r} — "
                "refusing to ship it"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate the six-month-home fixture (RET-1).")
    ap.add_argument(
        "--check",
        action="store_true",
        help="Build into a temp home and print the manifest; leave the fixture untouched.",
    )
    args = ap.parse_args()

    _require_prior_release()

    tmp = Path(tempfile.mkdtemp(prefix="six-month-home-gen-"))
    home = tmp / "home"
    home.mkdir(parents=True)
    os.environ["PERSONALCLAW_HOME"] = str(home)

    try:
        stores = {
            "knowledge_db": _build_knowledge(home),
            "memory_db": _build_memory_db(home),
            "memory_markdown": _build_memory_markdown(home),
            "loops": _build_loops(home),
            "run_history": _build_run_history(home),
            "entity_settings": _build_entity_settings(home),
            "config_json": _build_config(home),
        }
        _drop_derived_state(home)
        for db in sorted(home.rglob("*.db")):
            _checkpoint(db)
        # Sidecars are not state; a committed ``-wal`` is a file whose rows the reader
        # may or may not replay depending on the ``.db`` it lands beside.
        for sidecar in sorted(home.rglob("*.db-wal")) + sorted(home.rglob("*.db-shm")):
            sidecar.unlink()
        # Machine-local, process-lifetime state a fixture must never carry: the bound
        # socket + pid, the machine identity, the gateway's local auth secret, and the
        # advisory lock files the JSONL writers create (a lock is a live-process artifact,
        # not state, and a committed one would ship as a zero-byte file with no reader).
        for stray in ("gateway.runtime.json", "machine_id", ".local_secret"):
            with contextlib.suppress(FileNotFoundError):
                (home / stray).unlink()
        for lock in sorted(home.rglob("*.lock")):
            lock.unlink()

        # The marker every fixture carries, byte-identical with the shipped ones so
        # ``--seed six-month-home`` is the same mechanism, not a parallel path.
        shutil.copy2(FIXTURES_DIR / "empty" / "fixture.yaml", home / "fixture.yaml")
        _reject_leaked_paths(home, str(tmp))

        manifest = {
            "_comment": (
                "Committed expectations for the six-month-home fixture. Regenerate with "
                "scripts/generate_six_month_home_fixture.py against PersonalClaw "
                f"{PRIOR_RELEASE}. tests/test_state_survival.py asserts against THIS "
                "file, never against counts re-derived from the fixture."
            ),
            "fixture": FIXTURE.name,
            "written_by_release": PRIOR_RELEASE,
            "generated_at": FROZEN_NOW,
            "stores": stores,
        }

        if args.check:
            print(json.dumps(manifest, indent=2, sort_keys=True))
            print(f"--check: built {home}, fixture untouched", file=sys.stderr)
            return 0

        if FIXTURE.exists():
            shutil.rmtree(FIXTURE)
        shutil.copytree(home, FIXTURE)
        MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"fixture updated: {FIXTURE}")
        print(f"manifest updated: {MANIFEST}")
        for name, entry in stores.items():
            print(f"  {name}: count={entry['count']} path={entry['path']}")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
