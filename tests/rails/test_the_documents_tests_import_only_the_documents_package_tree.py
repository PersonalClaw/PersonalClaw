"""The documents package's own tests import nothing of core but the package (v0.4.0 B4, #5815).

``tests/documents`` is the catalogue's ``documents`` job: it runs in an environment holding only the
package's libraries, with ``--confcutdir`` keeping the suite's conftest out. An import of another
unit of ``personalclaw``, or of a suite helper from elsewhere in ``tests/`` (by its package name or
by the bare name the suite's path makes importable), works in the shards and fails in the job. So
each one is named here. ``personalclaw.documents`` and ``tests.documents`` are the folder's own.
"""

from __future__ import annotations

import ast
from pathlib import Path

from rails_repo import REPO

FOLDER = Path("tests") / "documents"


def _own(name: str, mine: str) -> bool:
    return name == mine or name.startswith(mine + ".")


def _suite_helpers(root: Path) -> set[str]:
    """The bare module names the suite's path makes importable outside the folder."""
    names: set[str] = set()
    for top in (root / "tests", root / "tests" / "rails"):
        names |= {p.stem for p in top.glob("*.py")}
        names |= {p.name for p in top.iterdir() if p.is_dir() and (p / "__init__.py").is_file()}
    return names - {"conftest"}


def outside_imports(root: Path) -> list[str]:
    """Each import in the folder's files of core beyond the package, or of the suite beyond it."""
    helpers = _suite_helpers(root)
    own = {p.stem for p in (root / FOLDER).rglob("*.py")}
    found: list[str] = []
    for path in sorted((root / FOLDER).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
                if node.module in ("personalclaw", "tests"):
                    names = [f"{node.module}.{alias.name}" for alias in node.names]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top == "personalclaw" and not _own(name, "personalclaw.documents"):
                    found.append(f"{path.relative_to(root)}:{node.lineno} imports {name}")
                elif top == "tests" and not _own(name, "tests.documents"):
                    found.append(f"{path.relative_to(root)}:{node.lineno} imports {name}")
                elif top in helpers and top not in own:
                    found.append(f"{path.relative_to(root)}:{node.lineno} imports {name}")
    return found


def test_the_documents_tests_import_only_the_documents_package():
    """🔴 Red before this: the folder did not exist, and nothing held what its tests import."""
    files = list((REPO / FOLDER).rglob("test_*.py"))
    assert len(files) >= 8, f"found only {files}: this check would pass vacuously"
    assert outside_imports(REPO) == []


def test_an_import_of_core_or_the_suite_from_the_folder_is_named(tmp_path):
    """Core beyond the package, a suite module by its package name and a suite helper by its bare
    name are each named; the package, the folder's own modules and pytest are not."""
    (tmp_path / "tests" / "rails").mkdir(parents=True)
    (tmp_path / "tests" / "table_rows.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "rails" / "rails_repo.py").write_text("", encoding="utf-8")
    planted = tmp_path / FOLDER / "test_planted.py"
    planted.parent.mkdir(parents=True)
    (planted.parent / "shared_rows.py").write_text("", encoding="utf-8")
    planted.write_text(
        "import pytest\n"
        "from personalclaw.documents.model import Block\n"
        "from personalclaw import documents\n"
        "from tests.documents import shared_rows\n"
        "import shared_rows as rows\n"
        "from personalclaw.security import redact_credentials\n"
        "from personalclaw import config\n"
        "from tests import test_decks\n"
        "from table_rows import row\n"
        "import rails_repo\n",
        encoding="utf-8",
    )
    assert outside_imports(tmp_path) == [
        f"{FOLDER}/test_planted.py:6 imports personalclaw.security",
        f"{FOLDER}/test_planted.py:7 imports personalclaw.config",
        f"{FOLDER}/test_planted.py:8 imports tests.test_decks",
        f"{FOLDER}/test_planted.py:9 imports table_rows",
        f"{FOLDER}/test_planted.py:10 imports rails_repo",
    ]
