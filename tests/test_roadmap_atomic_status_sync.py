"""The per-atom tables in ``docs/roadmap/atomic/<CODE>.md`` must agree with ``dag.json``.

``dag.json`` is the machine truth an atom's own commit flips; the ``<CODE>.md`` tables are
what a human reads. Nothing coupled them, and they drifted badly: measured on
``6c54c4c1``, **79 rows carried a status mark contradicting `dag.json`** (67 of them plain
``todo`` boxes on atoms that were done, spread over 30 of the 71 files) and **17 atom ids
appeared twice** in their own table — `LMMV-5`/`LMMV-6` were listed once as shipped and
again, lower down, as not started. Both classes read as "this work has not happened".

`test_roadmap_dag_derived.py` guards `dag.json`'s own derived block, so a stale table
never made it red. These three assertions close that gap:

1. a row's mark carries ✅ if and only if `dag.json` says the atom is ``done``;
2. no atom id appears in more than one row of its table;
3. the leading "``N atoms``" count in a file's summary line equals that plan's atom count.

Deliberately NOT asserted: the prose *narrative* after that count ("5 done, 3 todo …").
It is free text with no canonical form, so railing it would either force a template on
every plan or pass on any sentence at all. Rule 3 pins the one number in it that has a
single right answer, and the narrative stays a human-maintained claim.

The 🟡 mark stays legal for an atom `dag.json` calls ``todo``: `DC-3` uses it to say
"implementation landed, atom still open for its on-device walk-through", which is more
informative than an empty box and does not contradict the machine status. It becomes a
failure the moment that atom flips to ``done`` without the row being updated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ATOMIC_DIR = REPO_ROOT / "docs" / "roadmap" / "atomic"
DAG = ATOMIC_DIR / "dag.json"

#: A table row: ``| `ATOM-1` | <mark> | title | deps | done when |``
_ROW = re.compile(r"^\|\s*`([A-Z0-9]+-\d+)`\s*\|\s*([^|]*?)\s*\|")

#: The summary line's leading atom count, e.g. ``7 atoms: 6 done, 1 todo.``
_COUNT = re.compile(r"^(\d+)\s+atoms?\b", re.MULTILINE)

_DONE_MARK = "✅"  # ✅


def _dag() -> dict:
    return json.loads(DAG.read_text(encoding="utf-8"))


def _status_by_atom(dag: dict) -> dict[str, str]:
    return {a["id"]: a["status"] for plan in dag["plans"] for a in plan["atoms"]}


def _atom_count_by_code(dag: dict) -> dict[str, int]:
    return {plan["code"]: len(plan["atoms"]) for plan in dag["plans"]}


def _rows(path: Path) -> list[tuple[int, str, str]]:
    """``(line_number, atom_id, mark)`` for every table row in one atomic file."""
    out: list[tuple[int, str, str]] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        m = _ROW.match(line)
        if m:
            out.append((n, m.group(1), m.group(2)))
    return out


def _atomic_files() -> list[Path]:
    return sorted(p for p in ATOMIC_DIR.glob("*.md") if p.name != "dag.json")


def test_the_scan_reaches_every_plans_table() -> None:
    """Vacuity floor: a matcher that matched nothing would make all of this pass."""
    files = _atomic_files()
    assert len(files) >= 60, f"only {len(files)} atomic files found — wrong directory?"

    rows = [r for f in files for r in _rows(f)]
    assert len(rows) >= 600, f"row matcher found only {len(rows)} rows across {len(files)} files"

    # And it reaches the ids the DAG knows about, rather than matching some other table.
    known = set(_status_by_atom(_dag()))
    matched = {aid for _, aid, _ in rows}
    overlap = matched & known
    assert len(overlap) >= 600, f"only {len(overlap)} matched rows name a known atom"


def test_every_row_mark_agrees_with_the_dag_status() -> None:
    status = _status_by_atom(_dag())
    wrong: list[str] = []
    checked = 0
    for f in _atomic_files():
        for n, aid, mark in _rows(f):
            st = status.get(aid)
            if st is None:
                continue  # rule covered by test_no_row_names_an_unknown_atom
            checked += 1
            if (st == "done") != (_DONE_MARK in mark):
                wrong.append(f"{f.name}:{n} `{aid}` dag={st} mark={mark!r}")
    assert checked >= 600, f"only {checked} rows were compared — the rail is inert"
    assert not wrong, "atomic table marks contradict dag.json:\n  " + "\n  ".join(wrong)


def test_no_atom_is_listed_twice_in_its_table() -> None:
    dupes: list[str] = []
    for f in _atomic_files():
        seen: dict[str, int] = {}
        for n, aid, _ in _rows(f):
            if aid in seen:
                dupes.append(f"{f.name}:{n} `{aid}` (first at line {seen[aid]})")
            else:
                seen[aid] = n
    assert not dupes, "duplicate atom rows:\n  " + "\n  ".join(dupes)


def test_no_row_names_an_unknown_atom() -> None:
    """A row for an atom the DAG never heard of is a typo or a deleted atom."""
    known = set(_status_by_atom(_dag()))
    strays = [
        f"{f.name}:{n} `{aid}`"
        for f in _atomic_files()
        for n, aid, _ in _rows(f)
        if aid not in known
    ]
    assert not strays, "rows naming an atom absent from dag.json:\n  " + "\n  ".join(strays)


def test_every_atom_in_the_dag_has_exactly_one_row() -> None:
    """The other direction: a done atom with no row is invisible to a reader."""
    status = _status_by_atom(_dag())
    listed = {aid for f in _atomic_files() for _, aid, _ in _rows(f)}
    missing = sorted(set(status) - listed)
    assert not missing, f"{len(missing)} atoms have no table row: {missing[:12]}"


def test_the_leading_atom_count_matches_the_dag() -> None:
    """Only the number is railed; the sentence around it stays prose (see module docstring)."""
    counts = _atom_count_by_code(_dag())
    checked = 0
    wrong: list[str] = []
    for f in _atomic_files():
        code = f.stem
        if code not in counts:
            continue
        m = _COUNT.search(f.read_text(encoding="utf-8"))
        if not m:
            continue
        checked += 1
        claimed = int(m.group(1))
        if claimed != counts[code]:
            wrong.append(f"{f.name}: says {claimed} atoms, dag has {counts[code]}")
    assert checked >= 6, f"only {checked} files carry a leading atom count — matcher may be stale"
    assert not wrong, "stale atom counts:\n  " + "\n  ".join(wrong)
