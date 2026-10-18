"""The source-tree checks split from
``tests/test_bounded_log_rail_trims_by_time.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import re

import source_index
from bounded_log_rail_trims_by_time_shared import _PRUNE_BY_ID, HELPER, _parsed, position_trims
from rails_repo import REPO

SRC = REPO / "src" / "personalclaw"


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


def _tree() -> dict[str, str]:
    """Every package module's text but the helper's, from the suite's one source index."""
    return {
        str(path.relative_to(SRC)): source_index.text(path)
        for path in source_index.package_files()
        if path.stem != HELPER
    }


def _module(rel: str) -> ast.Module:
    """The tree of the package module *rel*, from the suite's one source index."""
    return source_index.tree(SRC / rel)


def _tree_trims() -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for rel in _tree():
        for name, cuts in position_trims(_module(rel)).items():
            out[f"{rel}::{name}"] = cuts
    return out


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
        for rel in _tree()
        for m in _PRUNE_BY_ID.finditer(_string_constants(_module(rel)))
    ]
    assert not hits, (
        "A merge brings another database's rows in with their own ids, which say nothing about "
        f"when: prune by time with bounded_log.prune_table. {hits}"
    )


def _string_constants(source: str | ast.Module) -> str:
    """Every string in *source* (text, or its tree), adjacent literals joined as Python joins
    them."""
    return "\n".join(
        node.value
        for node in ast.walk(_parsed(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def test_the_rail_sees_the_tree_and_the_helper_at_work() -> None:
    """A scan that read nothing would pass. The tree is large and the rule is applied in many
    places: both are measured here, so a broken walk or a renamed helper reads red."""
    tree = _tree()
    functions = sum(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        for rel in tree
        for n in ast.walk(_module(rel))
    )
    assert functions > 5000, functions
    users = sorted(rel for rel, source in tree.items() if re.search(rf"\b{HELPER}\.\w+\(", source))
    assert len(users) >= 20, users
    assert len(_tree_trims()) == len(NOT_A_LOG), "every match is exempt, none goes unseen"
