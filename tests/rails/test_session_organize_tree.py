"""The source-tree checks split from
``tests/test_session_organize.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

from personalclaw import session_organize as so


def test_no_module_function_but_apply_writes_folder_or_tags():
    """An AST sweep: only `apply_proposal` may assign `session.folder_id` / `session.tags`.

    A behavioural test only covers the paths it drives. This covers the ones it doesn't —
    a future helper that "just files it while we're here" fails here rather than in
    production. Mirrors the call-site-assertion technique that caught writerless counters.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(so))
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign):
                continue
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Attribute)
                    and tgt.attr in ("folder_id", "tags")
                    and isinstance(tgt.value, ast.Name)
                    and tgt.value.id == "session"
                    and fn.name != "apply_proposal"
                ):
                    offenders.append(f"{fn.name} assigns session.{tgt.attr}")
    assert not offenders, f"a second writer for session folder/tags: {offenders}"
