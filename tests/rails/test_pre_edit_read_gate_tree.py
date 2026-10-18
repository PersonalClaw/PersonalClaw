"""The source-tree checks split from
``tests/test_pre_edit_read_gate.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast

import pre_edit_read_gate_shared
from pre_edit_read_gate_shared import _BUILTIN_TOOLS_SRC, _UNGATEABLE_WRITE_PATHS

from personalclaw.agents.native.builtin_tools import _READ_GATED_WRITE_TOOLS

_clean_ledger = pre_edit_read_gate_shared._clean_ledger


#: Calls that write CONTENT to a filesystem path. A handler performing one of these is a
#: write path and must be covered by the seam's registry. Deletion/rename (``unlink``,
#: ``rename``) are deliberately out: the gate is about content computed against a stale
#: version, and including ``unlink`` credited ``_t_bash`` for deleting its own temp script.
_CONTENT_WRITE_CALLS = frozenset(
    {
        "write_text",
        "write_bytes",
        "atomic_write",
        "atomic_write_bytes",
        "copyfile",
        "copy",
        "copy2",
        "replace",  # os.replace / shutil.replace — a rename-into-place IS a content write
    }
)


def _write_handlers_in_builtin_tools() -> set[str]:
    """Every ``_t_<tool>`` handler in builtin_tools.py whose body mutates a path.

    An AST walk, not a grep: a handler is attributed by the function it is lexically
    inside, so a mutating call cannot be credited to the wrong tool (and a mention in a
    docstring or comment is not a call at all).
    """
    tree = ast.parse(_BUILTIN_TOOLS_SRC.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("_t_"):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            # `str.replace` is pervasive and harmless; only a path receiver mutates.
            if name == "replace" and not (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id in {"os", "path", "shutil"}
            ):
                continue
            if name in _CONTENT_WRITE_CALLS:
                found.add(node.name[len("_t_") :])
    return found


def test_rail_every_write_path_is_covered_by_the_one_seam():
    discovered = _write_handlers_in_builtin_tools()
    # VACUITY: a scan that matches nothing would "pass" while covering nothing. If the
    # write handlers get renamed or the call vocabulary drifts, FAIL here.
    assert discovered, (
        "the write-path scan matched ZERO handlers — the rail is vacuous. Update "
        f"_CONTENT_WRITE_CALLS / the _t_ prefix against {_BUILTIN_TOOLS_SRC}."
    )
    assert {
        "write_file",
        "edit_file",
    } <= discovered, f"the two known write paths must be discoverable; got {sorted(discovered)}"
    uncovered = discovered - set(_READ_GATED_WRITE_TOOLS) - set(_UNGATEABLE_WRITE_PATHS)
    assert not uncovered, (
        f"write path(s) {sorted(uncovered)} write file content but are not in "
        "_READ_GATED_WRITE_TOOLS, so they bypass the pre-edit read gate. Add a row "
        "there (do NOT re-implement the gate in the handler)."
    )
