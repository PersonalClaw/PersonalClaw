"""The source-tree checks split from
``tests/test_total_cycles_retired.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
from pathlib import Path

import source_index
import total_cycles_retired_shared
from rails_repo import REPO
from total_cycles_retired_shared import _RETIRED

_isolated_home = total_cycles_retired_shared._isolated_home


_SRC = REPO / "src" / "personalclaw"


def _python_sources() -> list[Path]:
    """Every package module, from the suite's one source index."""
    return list(source_index.package_files())


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
        tree = source_index.tree(path)
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
    tick = source_index.tree(_SRC / _TICK_STATE_OWNS_THE_NAME)
    assert _writes_of(tick), (
        f"{_TICK_STATE_OWNS_THE_NAME} is exempted from the census but contains no write of "
        f"{_RETIRED!r} — the exemption is stale and should be deleted"
    )
    # …and a censused module really does feed that snapshot by keyword, so the callee exemption is
    # load-bearing rather than hypothetical.
    sdlc = source_index.tree(_SRC / "loop" / "kinds" / "sdlc.py")
    feeders = [
        node
        for node in ast.walk(sdlc)
        if isinstance(node, ast.Call)
        and _callee_name(node) in _TICK_STATE_CALLEES
        and any(kw.arg == _RETIRED for kw in node.keywords)
    ]
    assert feeders, (
        "no module feeds a TickState `total_cycles` by keyword any more — `_TICK_STATE_CALLEES` is "
        "now a dead exemption and should be deleted"
    )
