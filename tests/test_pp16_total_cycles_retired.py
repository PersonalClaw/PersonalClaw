"""`loops.total_cycles` is retired — the cycle count is the ledger projection (PP-16 seam 4a).

The column was a denormalized cache of something the ledger already derives: both of its writers in
`loop/watchdog.py` wrote exactly ``len(store.get_findings(cid))``, and `get_findings` is a pure
projection over the `step_completed` records `PP-5` already ships. Deleting it removes a second
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
* **Two expressions, one number.** `store.cycles_completed()` routes through the ledger's
  `run_totals` aggregate while the two redacted views count the findings they already hold. Those
  are two code paths to one quantity, so `test_the_two_projections_agree` pins them equal — the
  drift this atom exists to prevent, reintroduced one level down, would otherwise be invisible.
* **A pre-change home still works.** The retirement adds no migration (see `store._connect`'s
  note): an existing `loops` table keeps the column, unread. That tolerance is the one real risk of
  not migrating, so it is measured rather than assumed.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from personalclaw.loop import journal as loop_journal
from personalclaw.loop import store
from personalclaw.loop.loop import Loop, LoopStatus

_SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"

#: The retired column/field name. One spelling, used by every rail below.
_RETIRED = "total_cycles"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every rail here writes a loop row + a ledger. Keep both out of the real home."""
    monkeypatch.setenv("PERSONALCLAW_HOME", str(tmp_path / "home"))


def _loop_with_cycles(n: int, *, loop_id: str = "abc12345") -> Loop:
    """A loop whose ledger carries `n` cycles, ingested through the production path."""
    loop = store.create(Loop(id=loop_id, name="L", kind="goal", task="t", max_cycles=30))
    d = store.loop_dir(loop.id)
    assert d is not None
    for i in range(1, n + 1):
        (d / "findings" / f"cycle_{i}.json").write_text(
            json.dumps({"cycle": i, "summary": f"s{i}", "step": "survey"}), encoding="utf-8"
        )
    assert store.record_cycle_findings(loop.id) == n, "the ingest did not file every cycle"
    return loop


# ── the field and the column are gone ───────────────────────────────────────


def test_the_loop_dataclass_declares_no_cached_cycle_count() -> None:
    assert _RETIRED not in Loop.__dataclass_fields__, (
        f"`Loop.{_RETIRED}` is back. It was a stored copy of the ledger's `step_completed` count; "
        "read `store.cycles_completed(id)` (or `len(get_findings(id))`) instead."
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
    assert store.cycles_completed(loop.id) == 3
    assert store.get_redacted(loop.id)["total_cycles"] == 3

    loop_journal.LoopJournal.open(loop.id).cycle(4, {"cycle": 4, "summary": "s4"})

    assert store.cycles_completed(loop.id) == 4, "the accessor did not follow the ledger"
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
    before = store.cycles_completed(loop.id)

    loop_journal.LoopJournal.open(loop.id).verdict({"cycle": 3, "verdict": "pass", "done": False})

    assert store.cycles_completed(loop.id) == before, (
        "a `judge_verdict` changed the CYCLE count — the count is not defined over "
        "`step_completed` but over ledger lines"
    )
    assert store.get_redacted(loop.id)["total_cycles"] == before


def test_the_two_projections_agree() -> None:
    """`cycles_completed()` (ledger `run_totals`) vs `len(get_findings())` (the views' path)."""
    loop = _loop_with_cycles(5)
    loop_journal.LoopJournal.open(loop.id).verdict({"cycle": 5, "verdict": "pass"})
    loop_journal.LoopJournal.open(loop.id).breaker_trip(5, "stalled")

    via_aggregate = store.cycles_completed(loop.id)
    via_projection = len(store.get_findings(loop.id))
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
    assert store.cycles_completed("deadbeef") == 0
    root = store._loops_root()
    assert not (root / "deadbeef").exists(), "reading a count created the loop's dir"


def test_the_orphan_reap_still_sees_a_backed_dir() -> None:
    """`reap_orphan_dirs` reads `list_all()`, which no longer carries a cycle count. Pin that the
    sweep still spares a dir with a row and still takes one without."""
    loop = _loop_with_cycles(2)
    root = store._loops_root()
    (root / "beefcafe").mkdir(parents=True, exist_ok=True)

    assert store.reap_orphan_dirs() == 1, "the sweep stopped reaping an unbacked dir"
    assert (root / loop.id).is_dir(), "the sweep reaped a dir that HAS a row"
    assert not (root / "beefcafe").exists()
    assert store.cycles_completed(loop.id) == 2, "the surviving loop lost its ledger"


# ── no writer survives ──────────────────────────────────────────────────────


def _python_sources() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if p.is_file())


#: Callees that own a SAME-NAMED counter of their own: `tick.TickState` is a pure, never-persisted
#: snapshot whose `total_cycles` field is FED from the projection each call. A keyword argument into
#: one of these is not a write of the retired cache, and the shadowed name is exactly why this
#: census resolves the callee instead of matching the keyword alone. Asserted to be live below.
_TICK_STATE_CALLEES = frozenset({"TickState", "tick_state_from_snapshot"})


def _callee_name(call: ast.Call) -> str:
    fn = call.func
    if isinstance(fn, ast.Name):
        return fn.id
    if isinstance(fn, ast.Attribute):
        return fn.attr
    return ""


def _writes_of(tree: ast.AST) -> list[str]:
    """Every syntactic WRITE of the retired name in `tree`, as a human-readable reason.

    Deliberately syntax-shaped rather than textual: the name is mentioned on purpose in several
    comments and docstrings (they explain the retirement), and a grep would red on those forever.
    Four shapes can bring the cache back and all four are real syntax:

    * a SQL string literal that assigns it (``SET total_cycles =``, ``ADD COLUMN total_cycles``),
    * the bare column name passed as a positional ARGUMENT — the shape the deleted writer actually
      had (``_simple_set(loop_id, "total_cycles", n)``), found only because planting that exact
      line during falsification showed the SQL-marker arm alone did not catch it,
    * an assignment/annotation binding it as an attribute or dataclass field,
    * a keyword argument named for it, EXCEPT into a `_TICK_STATE_CALLEES` builder.

    Deliberately NOT flagged: a Subscript assignment (``view["total_cycles"] = len(findings)``).
    That is the DERIVED API key the redacted views publish, which is the whole point of the
    retirement — flagging it would red on the fix.
    """
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if _RETIRED in low and any(
                marker in low for marker in ("set ", "add column", "insert into", "update ")
            ):
                out.append(f"line {node.lineno}: SQL string writes {_RETIRED!r}")
        elif isinstance(node, ast.Call):
            if _callee_name(node) in _TICK_STATE_CALLEES:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == _RETIRED:
                    out.append(
                        f"line {node.lineno}: bare column name {_RETIRED!r} passed to "
                        f"{_callee_name(node) or '<expr>'}()"
                    )
            for kw in node.keywords:
                if kw.arg == _RETIRED:
                    out.append(
                        f"line {node.lineno}: keyword {_RETIRED}= into "
                        f"{_callee_name(node) or '<expr>'}()"
                    )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == _RETIRED:
                out.append(f"line {node.lineno}: annotated assignment to {_RETIRED}")
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == _RETIRED:
                    out.append(f"line {node.lineno}: assignment to {_RETIRED}")
                elif isinstance(tgt, ast.Attribute) and tgt.attr == _RETIRED:
                    out.append(f"line {node.lineno}: assignment to .{_RETIRED}")
    return out


#: `loop/tick.py` DECLARES the shadow: `TickState.total_cycles` is its own dataclass field, so the
#: annotated-assignment arm reds on it forever. Its CALLERS are covered precisely by
#: `_TICK_STATE_CALLEES` instead of being exempted wholesale, which is the point — `sdlc.py` feeds
#: that snapshot and is still fully censused for every other shape. Asserted live below.
_TICK_STATE_OWNS_THE_NAME = "loop/tick.py"


def test_no_module_writes_the_retired_column() -> None:
    files = _python_sources()
    assert len(files) > 500, f"only {len(files)} sources walked — the census lost the tree"

    scanned = 0
    offenders: dict[str, list[str]] = {}
    for path in files:
        rel = path.relative_to(_SRC).as_posix()
        if rel == _TICK_STATE_OWNS_THE_NAME:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        scanned += 1
        if hits := _writes_of(tree):
            offenders[rel] = hits

    assert scanned > 500, f"only {scanned} files parsed"
    assert not offenders, (
        f"these modules WRITE the retired `{_RETIRED}`, which is how a deleted cache comes back:\n"
        + "\n".join(f"  {rel}: {'; '.join(hits)}" for rel, hits in sorted(offenders.items()))
    )


def test_the_write_census_can_actually_fail() -> None:
    """Vacuity floor for the census: prove `_writes_of` reports each shape it claims to.

    Without this, a detector that matched nothing would make the test above pass forever — which is
    precisely the failure mode "a retired cache came back" needs it not to have.
    """
    planted = (
        "x = 1\n"
        f'conn.execute("UPDATE loops SET {_RETIRED} = ? WHERE id = ?")\n'
        # The deleted writer's EXACT shape. Found by planting it during falsification: the
        # SQL-marker arm alone did not catch a bare column name handed to a generic setter, so the
        # census reported clean against a resurrected `store.set_total_cycles`.
        f'_simple_set(loop_id, "{_RETIRED}", int(total))\n'
        f"store.set_it(loop_id, {_RETIRED}=4)\n"
        f"loop.{_RETIRED} = 7\n"
        f"{_RETIRED}: int = 0\n"
    )
    hits = _writes_of(ast.parse(planted))
    assert len(hits) == 5, f"the detector found {len(hits)} of the 5 planted writes: {hits}"
    assert not _writes_of(
        ast.parse("y = 2  # total_cycles is retired\n")
    ), "the detector fires on a COMMENT — it would red on the retirement's own prose forever"
    # The DERIVED payload key must NOT be flagged: the views publishing it is the fix, not a defect.
    assert not _writes_of(
        ast.parse(f'view["{_RETIRED}"] = len(view["findings"])\n')
    ), "the detector flags the derived API key — it would red on the retirement's own code"

    # The callee exemption is NARROW: the same keyword into anything else is still a write.
    exempt = f"tick.tick_state_from_snapshot(step_index=0, {_RETIRED}=len(findings))\n"
    assert not _writes_of(ast.parse(exempt)), "the TickState exemption stopped applying"
    assert _writes_of(
        ast.parse(f"store.create_loop({_RETIRED}=len(findings))\n")
    ), "the exemption leaked: a keyword into a non-TickState callee must still be flagged"

    # And neither exemption is a dead entry. `tick.py` really does declare the shadow field…
    tick = ast.parse((_SRC / _TICK_STATE_OWNS_THE_NAME).read_text(encoding="utf-8"))
    assert _writes_of(tick), (
        f"{_TICK_STATE_OWNS_THE_NAME} is exempted from the census but contains no write of "
        f"{_RETIRED!r} — the exemption is stale and should be deleted"
    )
    # …and a censused module really does feed that snapshot by keyword, so the callee exemption is
    # load-bearing rather than hypothetical.
    sdlc = (_SRC / "loop" / "kinds" / "sdlc.py").read_text(encoding="utf-8")
    feeders = [
        node
        for node in ast.walk(ast.parse(sdlc))
        if isinstance(node, ast.Call)
        and _callee_name(node) in _TICK_STATE_CALLEES
        and any(kw.arg == _RETIRED for kw in node.keywords)
    ]
    assert feeders, (
        "no module feeds a TickState `total_cycles` by keyword any more — `_TICK_STATE_CALLEES` is "
        "now a dead exemption and should be deleted"
    )


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
