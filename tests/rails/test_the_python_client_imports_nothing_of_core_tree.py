"""The Python client imports nothing of core (v0.4.0 B4, #5815).

The client (``packages/personalclaw-client-py``) is a leaf pilot: it builds and tests alone, in a
venv of its own (the catalogue's ``client`` job). An import of ``personalclaw`` would make it need
core installed beside it, so none of its files may name it. ``personalclaw_client``, its own
package, is not core.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rails_repo import REPO

CLIENT = Path("packages") / "personalclaw-client-py"


def core_imports(root: Path) -> list[str]:
    """Each import of ``personalclaw`` or a module under it in the client's files under *root*."""
    found: list[str] = []
    for path in sorted((root / CLIENT).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if name == "personalclaw" or name.startswith("personalclaw."):
                    found.append(f"{path.relative_to(root)}:{node.lineno} imports {name}")
    return found


def test_the_python_client_imports_nothing_of_core():
    """🔴 Red before this: nothing held it. Its files import only themselves and aiohttp."""
    files = list((REPO / CLIENT).rglob("*.py"))
    assert len(files) >= 3, f"found only {files}: this check would pass vacuously"
    assert core_imports(REPO) == []


def test_a_client_file_that_imports_core_is_named(tmp_path):
    """The check reads every file of the client, a test's included, and names each import."""
    planted = tmp_path / CLIENT / "tests" / "test_planted.py"
    planted.parent.mkdir(parents=True)
    planted.write_text(
        "import os\nimport personalclaw_client\nfrom personalclaw.sdk import documents\n",
        encoding="utf-8",
    )
    assert core_imports(tmp_path) == [f"{CLIENT}/tests/test_planted.py:3 imports personalclaw.sdk"]
