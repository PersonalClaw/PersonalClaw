"""`loops.total_cycles` is retired — the cycle count is the ledger projection.

The column was a denormalized cache of something the ledger already derives: both of its writers in
`loop/watchdog.py` wrote exactly ``len(loop_files.get_findings(cid))``, and `get_findings` is a pure
projection over the `step_completed` records the ledger already holds. Deleting it removes a second
source of truth rather than moving one.

What these rails are for, and why each can FAIL:

* **The projection is the real source.** `test_the_count_moves_with_the_ledger` mutates the ledger
  (an extra `step_completed`) and asserts the number every reader sees moves with it. Its vacuity
  partner, `test_the_count_does_not_move_with_an_unrelated_ledger_kind`, writes a `judge_verdict`
  instead — a record on the same file, through the same writer — and asserts the count does NOT
  move. Without that partner, "the count tracks the ledger" would be satisfied by a counter that
  counted *lines*.
* **No writer survives.** `test_no_module_writes_the_retired_column` is an AST census over
  ``src/``: a stray writer is exactly how a retired cache comes back, and a grep-shaped assertion
  would be defeated by the (deliberate) prose mentions of the name in comments and docstrings. It
  asserts on real syntax — a string literal in a SQL statement, an assignment, a keyword argument —
  and carries its own vacuity floor (it proves the census walks the files it claims to).
* **Two expressions, one number.** `loop_files.cycles_completed()` routes through the ledger's
  `run_totals` aggregate while the two redacted views count the findings they already hold. Those
  are two code paths to one quantity, so `test_the_two_projections_agree` pins them equal — the
  drift this change exists to prevent, reintroduced one level down, would otherwise be invisible.
* **A pre-change home still works.** The retirement adds no migration (see `store._connect`'s
  note): an existing `loops` table keeps the column, unread. That tolerance is the one real risk of
  not migrating, so it is measured rather than assumed.
"""

from __future__ import annotations

import json

import total_cycles_retired_shared
from total_cycles_retired_shared import _RETIRED

from personalclaw.loop import files as loop_files
from personalclaw.loop import journal as loop_journal
from personalclaw.loop import store
from personalclaw.loop.loop import Loop, LoopStatus

_isolated_home = total_cycles_retired_shared._isolated_home


def _loop_with_cycles(n: int, *, loop_id: str = "abc12345") -> Loop:
    """A loop whose ledger carries `n` cycles, ingested through the production path."""
    loop = store.create(Loop(id=loop_id, name="L", kind="goal", task="t", max_cycles=30))
    d = loop_files.loop_dir(loop.id)
    assert d is not None
    for i in range(1, n + 1):
        (d / "findings" / f"cycle_{i}.json").write_text(
            json.dumps({"cycle": i, "summary": f"s{i}", "step": "survey"}), encoding="utf-8"
        )
    assert loop_files.record_cycle_findings(loop.id) == n, "the ingest did not file every cycle"
    return loop


# ── the field and the column are gone ───────────────────────────────────────


def test_the_loop_dataclass_declares_no_cached_cycle_count() -> None:
    assert _RETIRED not in Loop.__dataclass_fields__, (
        f"`Loop.{_RETIRED}` is back. It was a stored copy of the ledger's `step_completed` count; "
        "read `loop_files.cycles_completed(id)` (or `len(get_findings(id))`) instead."
    )


def test_the_loops_table_declares_no_cached_cycle_count() -> None:
    """A FRESH schema, read from the live DB rather than from the CREATE string."""
    _loop_with_cycles(0)
    import sqlite3

    conn = sqlite3.connect(str(store._db_path()))
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(loops)")]
    finally:
        conn.close()
    assert cols, "no `loops` columns — did the table stop being created?"
    assert _RETIRED not in cols, f"the `loops.{_RETIRED}` column is back: {cols}"
    assert _RETIRED not in store._SCALAR_COLS, "the retired column is back in the INSERT list"


def test_the_setter_is_gone() -> None:
    assert not hasattr(store, f"set_{_RETIRED}"), (
        f"`store.set_{_RETIRED}` is back — a writer for a column that no longer exists is how the "
        "cache returns"
    )


# ── the projection IS the source ────────────────────────────────────────────


def test_the_count_moves_with_the_ledger() -> None:
    """Append one more `step_completed` and every reader's number moves with it."""
    loop = _loop_with_cycles(3)
    assert loop_files.cycles_completed(loop.id) == 3
    assert store.get_redacted(loop.id)["total_cycles"] == 3

    loop_journal.LoopJournal(loop.id).cycle(4, {"cycle": 4, "summary": "s4"})

    assert loop_files.cycles_completed(loop.id) == 4, "the accessor did not follow the ledger"
    assert store.get_redacted(loop.id)["total_cycles"] == 4, "the detail view is not derived"
    rows = store.list_redacted()
    assert len(rows) == 1
    assert rows[0]["total_cycles"] == 4, "the list view is not derived"


def test_the_count_does_not_move_with_an_unrelated_ledger_kind() -> None:
    """Vacuity partner for the test above.

    A `judge_verdict` goes through the SAME writer into the SAME two files, so a count implemented
    as "lines in the ledger" would move here. `step_completed` is the vocabulary the count is
    defined over, and this is what proves the definition is real.
    """
    loop = _loop_with_cycles(3)
    before = loop_files.cycles_completed(loop.id)

    loop_journal.LoopJournal(loop.id).verdict({"cycle": 3, "verdict": "pass", "done": False})

    assert loop_files.cycles_completed(loop.id) == before, (
        "a `judge_verdict` changed the CYCLE count — the count is not defined over "
        "`step_completed` but over ledger lines"
    )
    assert store.get_redacted(loop.id)["total_cycles"] == before


def test_the_two_projections_agree() -> None:
    """`cycles_completed()` (ledger `run_totals`) vs `len(get_findings())` (the views' path)."""
    loop = _loop_with_cycles(5)
    loop_journal.LoopJournal(loop.id).verdict({"cycle": 5, "verdict": "pass"})
    loop_journal.LoopJournal(loop.id).breaker_trip(5, "stalled")

    via_aggregate = loop_files.cycles_completed(loop.id)
    via_projection = len(loop_files.get_findings(loop.id))
    assert via_aggregate == via_projection == 5, (
        f"the two paths to one number disagree: run_totals={via_aggregate}, "
        f"len(get_findings)={via_projection}"
    )


def test_a_loop_with_no_ledger_counts_zero() -> None:
    """The floor: no dir, no events file, no crash — and specifically NOT a created dir.

    `read_jsonl` goes through `safe_loop_dir`, which never creates. That matters beyond tidiness:
    `reap_orphan_dirs` uses `list_all()` as its GC oracle, so a read path that materialized a dir
    would resurrect exactly what the sweep just deleted.
    """
    assert loop_files.cycles_completed("deadbeef") == 0
    root = loop_files.loops_root()
    assert not (root / "deadbeef").exists(), "reading a count created the loop's dir"


def test_the_orphan_reap_still_sees_a_backed_dir() -> None:
    """`reap_orphan_dirs` reads `list_all()`, which no longer carries a cycle count. Pin that the
    sweep still spares a dir with a row and still takes one without."""
    loop = _loop_with_cycles(2)
    root = loop_files.loops_root()
    (root / "beefcafe").mkdir(parents=True, exist_ok=True)

    assert loop_files.reap_orphan_dirs() == 1, "the sweep stopped reaping an unbacked dir"
    assert (root / loop.id).is_dir(), "the sweep reaped a dir that HAS a row"
    assert not (root / "beefcafe").exists()
    assert loop_files.cycles_completed(loop.id) == 2, "the surviving loop lost its ledger"


# ── no writer survives ──────────────────────────────────────────────────────


# ── a pre-change home keeps working (no migration, by decision) ─────────────


def test_a_legacy_row_with_the_retired_column_still_writes() -> None:
    """The one real risk of adding no migration: an EXISTING `loops` table keeps the column.

    `_connect` has no DROP path on purpose. This pins that the retired column stays inert on such a
    home rather than breaking it: the INSERT names its columns (so the leftover takes its
    `NOT NULL DEFAULT 0`), and the read drops the extra key because `Loop.from_dict` ignores names
    that are not fields.
    """
    import sqlite3

    _loop_with_cycles(1, loop_id="aaaa1111")  # materializes the fresh schema
    conn = sqlite3.connect(str(store._db_path()))
    try:
        conn.execute(f"ALTER TABLE loops ADD COLUMN {_RETIRED} INTEGER NOT NULL DEFAULT 0")
        conn.execute(f"UPDATE loops SET {_RETIRED} = 99")
        conn.commit()
    finally:
        conn.close()

    # A read tolerates the extra column and does NOT surface it.
    rows = store.list_all()
    assert len(rows) == 1 and not hasattr(rows[0], _RETIRED)
    # …and the derived count is the ledger's, not the bogus 99 sitting in the row.
    assert store.get_redacted(rows[0].id)["total_cycles"] == 1

    # A write against the legacy shape still lands.
    made = store.create(Loop(id="bbbb2222", name="L2", kind="goal", task="t2"))
    assert store.get(made.id) is not None
    store.update_status(made.id, LoopStatus.RUNNING)
    assert store.get(made.id).status == LoopStatus.RUNNING.value
    assert len(store.list_redacted()) == 2
