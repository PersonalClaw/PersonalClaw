"""The source-tree checks split from
``tests/test_stale_write_refusals_read_right.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import re

import source_index
from rails_repo import REPO
from sel_log_calls_shared import the_call_it_runs

_SRC = REPO / "src" / "personalclaw"


def test_no_sentence_puts_a_possessive_after_a_quoted_name() -> None:
    """``{name!r}'s`` renders ``'name''s``. Five sentences in the tree did it — the app settings
    refusal, two model-resolution fixes ("change 'Broken App''s type"), the artifact body conflict
    and a store-conformance clause — so the rule is checked over the tree, not one call site."""
    doubled = re.compile(r"!r\}['’]s\b")
    found = [
        f"{path.relative_to(_SRC)}:{n}"
        for path in source_index.package_files()
        for n, line in enumerate(source_index.text(path).splitlines(), 1)
        if doubled.search(line)
    ]
    assert not found, f"a possessive after a quoted name reads as a doubled quote: {found}"
    # The scan reads the tree: it sees the f-strings it is looking through.
    assert any("!r}" in source_index.text(p) for p in source_index.package_files())


def refusal_audits(trees: list[tuple[str, ast.AST]]) -> tuple[int, list[str]]:
    """The audited refusal blocks in *trees*, ``(path, tree)`` pairs, and each audit row among
    them that names no refusal. A write handed to ``sel_log_async`` is read as the call it runs
    (``sel_log_calls_shared``)."""

    writers = {"_sel_log", "_audit", "log_api_access", "log_tool_invocation"}
    builders = {"stale_write_refusal", "_source_file_refusal"}

    def called(node: ast.AST) -> str:
        f = node.func if isinstance(node, ast.Call) else None
        return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""

    def audits(block: list[ast.stmt]) -> list[ast.AST]:
        found = []
        for stmt in block:
            for node in ast.walk(stmt):
                if not isinstance(node, ast.Call):
                    continue
                call = the_call_it_runs(node)
                if called(call) in writers:
                    found.append(call)
                elif called(node) == "RefusedInConfigTransaction" and any(
                    k.arg == "audit" for k in node.keywords
                ):
                    found.append(node)
        return found

    blocks = 0
    wrong = []
    for where, tree in trees:
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            refusals = {
                t.id
                for node in ast.walk(fn)
                if isinstance(node, ast.Assign) and called(node.value) in builders
                for t in node.targets
                if isinstance(t, ast.Name)
            }
            guarded: list[tuple[str, list[ast.stmt]]] = []
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Name)
                    and node.test.left.id in refusals
                    and isinstance(node.test.ops[0], ast.IsNot)
                ):
                    guarded.append((node.test.left.id, node.body))
                if isinstance(node, ast.ExceptHandler) and "ArtifactStaleWrite" in ast.unparse(
                    node.type or ast.Constant(None)
                ):
                    guarded.append(("", node.body))
            for name, body in guarded:
                for call in audits(body):
                    blocks += 1
                    src = ast.unparse(call)
                    if not re.search(rf"refusal_outcome\({name or r'\w+'}\)", src):
                        wrong.append(f"{where}:{call.lineno}: {src[:90]}")
    return blocks, wrong


def test_every_audited_refusal_records_which_refusal_it_was() -> None:
    """The thirteen routes that audit a refusal each wrote their own words for it; the rule is
    that each asks the refusal (`refusal_outcome`), so none can say "stale base" of a 428 again.

    Read from the tree: a block that runs only when a refusal was built — ``if <refusal> is not
    None:`` after ``<refusal> = stale_write_refusal(…)`` (or the artifact route's
    ``_source_file_refusal``), or the ``except ArtifactStaleWrite`` arm — and writes an audit row
    must pass ``refusal_outcome(<refusal>)`` to it."""
    trees = [(str(path.relative_to(_SRC)), tree) for path, tree in source_index.package_trees()]
    blocks, wrong = refusal_audits(trees)
    assert not wrong, "an audited refusal names no refusal:\n" + "\n".join(wrong)
    # Vacuity floor: the scan found the audited refusals it is about.
    assert blocks >= 13, blocks


_HANDED = """
async def save(request, text):
    stale = stale_write_refusal(request, text, what="x")
    if stale is not None:
        await sel_log_async(_sel().log_tool_invocation, session_key="d", outcome="stale base")
        return stale
    other = stale_write_refusal(request, text, what="y")
    if other is not None:
        await sel_log_async(
            _sel().log_tool_invocation, session_key="d", outcome=refusal_outcome(other)
        )
        return other
"""


def test_a_refusal_audit_handed_to_sel_log_async_is_read_as_the_call_it_runs() -> None:
    """An audit moved off the loop is still one of the refusal's audits, and still judged: the
    handed row with its own words for the refusal is named, and the one that asks it is not."""
    blocks, wrong = refusal_audits([("planted.py", ast.parse(_HANDED))])
    assert blocks == 2, blocks
    assert [w.split(": ", 1)[0] for w in wrong] == ["planted.py:5"], wrong
