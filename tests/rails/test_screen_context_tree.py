"""The source-tree checks split from
``tests/test_screen_context.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

import screen_context_shared

from personalclaw.dashboard import screen_context

_clean_slots = screen_context_shared._clean_slots


class TestSlot:
    def test_module_holds_no_file_write_path(self):
        """The ephemerality guarantee, asserted structurally.

        A future edit that gave this module a write path would be the leak, so the
        module is checked for one. It catches the change at the moment it is made
        rather than after some later test happens to notice bytes on disk.

        Asserted over the **AST**, not the source text. A substring scan cannot tell
        a call from prose, so it reddens on a docstring that merely NAMES a
        filesystem API — meaning documenting this guarantee would break the test
        asserting it. Walking the tree looks at calls and imports only, so the
        module's docstrings stay free to discuss disk at length.
        """
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path(screen_context.__file__).read_text())

        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name:
                    called.add(name)
        writes = called & {
            "open",
            "write_bytes",
            "write_text",
            "atomic_write",
            "mkdir",
            "touch",
            "unlink",
            "rename",
            "replace",
        }
        assert not writes, f"screen_context gained a disk call: {sorted(writes)}"

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        # No filesystem/db module is even reachable from here, so a write path cannot
        # be smuggled in behind an alias the call scan above would not recognise.
        fs = imported & {"pathlib", "os", "io", "shutil", "tempfile", "sqlite3", "pickle"}
        assert not fs, f"screen_context gained a filesystem import: {sorted(fs)}"
