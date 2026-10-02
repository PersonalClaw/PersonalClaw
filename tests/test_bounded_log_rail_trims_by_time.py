"""No bounded log in core trims by position: every trim keeps the newest rows by their own time.

A bounded log let its oldest rows go by cutting the tail off what it read — ``rows[-cap:]``,
``del rows[:-cap]``, ``while len(rows) > cap: rows.pop(0)`` — and wrote what was left back. That is
right only while the rows are in the order they happened, and a merge restore or a sync adds older
rows after newer ones: the notification log kept an archive's old notes and deleted thirteen hours
of the newest. The rule now lives in one place, ``personalclaw.bounded_log`` (:func:`newest`,
:func:`trim_jsonl`, :func:`prune_table`), and this rail finds a trim that does not go through it.

**What it flags.** A function that cuts the tail off a list by position AND writes (a call whose
name says it writes, saves or persists), unless it puts the rows in time order through
``bounded_log`` first. A cut with no write is a view of something, not a trim of a log; a function
that orders the rows through the helper and then cuts (the run history's per-class quota) is the
rule applied. The same for SQL: a ``DELETE`` that keeps the highest row ids.

**What is exempt, and why.** :data:`NOT_A_LOG` names each function the shape matches that trims no
log of rows — a text cut to its last characters, a page sent to a client — each with its reason.
An entry that no longer matches fails, so the list cannot outlive what it excuses.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
HELPER = "bounded_log"

#: Functions the shape matches that trim no log of rows: ``<path under src/personalclaw>::<name>``.
NOT_A_LOG: dict[str, str] = {
    "context_management.py::cap_result_file": (
        "a result file's TEXT cut to its first and last characters, not rows"
    ),
    "dashboard/chat_handlers.py::api_chat_session_resume": (
        "the last 200 messages of a transcript sent to the client; nothing is trimmed on disk"
    ),
    "dashboard/chat_regenerate.py::api_chat_session_regenerate": (
        "a reply's alternatives, in the order the one chat made them; a transcript merges whole"
    ),
    "dashboard/chat_regenerate.py::api_chat_session_edit_resend": (
        "the turns edits replaced, in the order the one chat made them; a transcript merges whole"
    ),
    "dashboard/handlers/updates.py::api_logs": (
        "the last lines of the in-memory log ring, written to the response"
    ),
    "ledger/writer.py::store_output": (
        "an oversized output's preview: its first and last characters"
    ),
    "resilience/crashes.py::record_crash": (
        "the last five turn digests its caller passed, clipped into one crash record"
    ),
    "subagent.py::_run_inner": "a running agent's streamed text, cut to its last characters",
    "workflows/loop_convergence.py::_record_convergence": (
        "a run's own decision log in the order its one loop decided; a run's records stay on the "
        "machine that ran it, so no merge or sync writes into it"
    ),
}

_WRITES = re.compile(r"(write|^_?save|^_?persist)", re.IGNORECASE)

#: A DELETE that keeps the highest row ids — the SQL form of cutting a file's tail.
_PRUNE_BY_ID = re.compile(
    r"DELETE\s+FROM\s+\S+\s+WHERE\s+(?:id|rowid)\s+(?:<=|<|IN|NOT\s+IN)\s*\(?\s*SELECT\s+"
    r"(?:MAX\(\s*(?:id|rowid)\s*\)|(?:id|rowid)\s+FROM\s+\S+\s+ORDER\s+BY\s+(?:id|rowid)\b)",
    re.IGNORECASE | re.DOTALL,
)


def _negative(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return True
    return isinstance(node, ast.Constant) and type(node.value) in (int, float) and node.value < 0


def _len_minus(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Sub)
        and isinstance(node.left, ast.Call)
        and getattr(node.left.func, "id", "") == "len"
    )


def _position_cuts(fn: ast.AST) -> list[int]:
    """The lines where *fn* cuts the tail off a list by position."""
    cuts: list[int] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
            s = node.slice
            if s.lower is not None and s.upper is None and s.step is None and _negative(s.lower):
                cuts.append(node.lineno)  # rows[-cap:]
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Slice):
                    s = target.slice
                    if s.lower is None and s.upper is not None:
                        if _negative(s.upper) or _len_minus(s.upper):
                            cuts.append(node.lineno)  # del rows[:-cap] / del rows[: len - cap]
        elif (
            isinstance(node, ast.While)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Call)
            and getattr(node.test.left.func, "id", "") == "len"
        ):
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "pop"
                    and len(inner.args) == 1
                    and isinstance(inner.args[0], ast.Constant)
                    and inner.args[0].value == 0
                ):
                    cuts.append(node.lineno)  # while len(rows) > cap: rows.pop(0)
    return cuts


def _called(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", ""))
    return names


def _uses_helper(fn: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == HELPER
        for node in ast.walk(fn)
    )


def position_trims(source: str) -> dict[str, list[int]]:
    """Every function in *source* that trims by position and writes, by name: its cut lines."""
    found: dict[str, list[int]] = {}
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        cuts = _position_cuts(fn)
        if not cuts or _uses_helper(fn):
            continue
        if any(_WRITES.search(name) for name in _called(fn)):
            found[fn.name] = cuts
    return found


def _tree() -> dict[str, str]:
    return {
        str(path.relative_to(SRC)): path.read_text(encoding="utf-8")
        for path in sorted(SRC.rglob("*.py"))
        if path.stem != HELPER
    }


def _tree_trims() -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for rel, source in _tree().items():
        for name, cuts in position_trims(source).items():
            out[f"{rel}::{name}"] = cuts
    return out


# ── the rail ─────────────────────────────────────────────────────────────────────────────────


def test_no_bounded_log_in_core_trims_by_position() -> None:
    found = _tree_trims()
    new = {site: cuts for site, cuts in found.items() if site not in NOT_A_LOG}
    assert not new, (
        "These functions cut the tail off a list by position and write: a bounded log trimmed "
        "that way keeps whatever was written last, and a merge restore writes an archive's OLDER "
        "rows last. Keep the newest by each row's own time with bounded_log.newest / trim_jsonl "
        f"(or order the rows with bounded_log.in_time_order first): {new}"
    )


def test_every_exemption_still_names_a_function_the_shape_matches() -> None:
    found = _tree_trims()
    stale = sorted(site for site in NOT_A_LOG if site not in found)
    assert not stale, f"exempt, but no longer matched (drop them from NOT_A_LOG): {stale}"


def test_no_table_in_core_is_pruned_to_its_highest_row_ids() -> None:
    hits = [
        f"{rel}: {m.group(0)[:80]}"
        for rel, source in _tree().items()
        for m in _PRUNE_BY_ID.finditer(_string_constants(source))
    ]
    assert not hits, (
        "A merge brings another database's rows in with their own ids, which say nothing about "
        f"when: prune by time with bounded_log.prune_table. {hits}"
    )


def _string_constants(source: str) -> str:
    """Every string in *source*, adjacent literals joined as Python joins them."""
    return "\n".join(
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


# ── the rail's controls ──────────────────────────────────────────────────────────────────────


def test_the_rail_sees_the_tree_and_the_helper_at_work() -> None:
    """A scan that read nothing would pass. The tree is large and the rule is applied in many
    places: both are measured here, so a broken walk or a renamed helper reads red."""
    tree = _tree()
    functions = sum(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        for source in tree.values()
        for n in ast.walk(ast.parse(source))
    )
    assert functions > 5000, functions
    users = sorted(rel for rel, source in tree.items() if re.search(rf"\b{HELPER}\.\w+\(", source))
    assert len(users) >= 20, users
    assert len(_tree_trims()) == len(NOT_A_LOG), "every match is exempt, none goes unseen"


def test_the_rail_flags_each_way_a_log_was_cut_by_position() -> None:
    """Positive controls: the shapes the tree held before the rule, each in a few lines."""
    source = textwrap.dedent("""
        def append_note(self, note):
            self.log.append(note)
            if len(self.log) > CAP * 2:
                self.log = self.log[-CAP:]
                _rewrite_notifications(self.log)

        def maybe_trim(p):
            lines = p.read_text().splitlines()
            if len(lines) > 2 * CAP:
                atomic_write(p, "\\n".join(lines[-CAP:]) + "\\n")

        def record(book):
            book["log"].append(1)
            del book["log"][:-CAP]
            ctl._save_run()

        def take(profile):
            profile.history.append(1)
            while len(profile.history) > CAP:
                profile.history.pop(0)
            _write(profile)

        def queue(path, q):
            del q[: len(q) - CAP]
            path.write_text(str(q))
        """)
    assert set(position_trims(source)) == {"append_note", "maybe_trim", "record", "take", "queue"}


def test_the_rail_passes_a_trim_by_time_and_a_cut_that_writes_nothing() -> None:
    source = textwrap.dedent("""
        def by_time(self, note):
            self.log = bounded_log.newest(self.log + [note], CAP, at="ts")
            _rewrite_notifications(self.log)

        def ordered_then_cut(rows):
            rows = bounded_log.in_time_order(rows, at="started_at")
            _write_jsonl(PATH, rows[-CAP:])

        def a_view(rows):
            return rows[-10:]
        """)
    assert position_trims(source) == {}


def test_the_sql_rail_flags_a_prune_to_the_highest_ids() -> None:
    assert _PRUNE_BY_ID.search(
        "DELETE FROM allocation_samples WHERE id <= (SELECT MAX(id) - ? FROM allocation_samples);"
    )
    assert _PRUNE_BY_ID.search(
        "DELETE FROM memory_events WHERE id IN "
        "(SELECT id FROM memory_events ORDER BY id ASC LIMIT ?)"
    )
    assert not _PRUNE_BY_ID.search(
        'DELETE FROM "t" WHERE rowid NOT IN (SELECT rowid FROM "t" ORDER BY "ts" DESC, rowid DESC '
        "LIMIT ?)"
    )
