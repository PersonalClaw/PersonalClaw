"""Outside tests, only ``html_documents.py`` reaches ``document_from_safe_html`` (B4, #5815).

``documents.from_markup.document_from_safe_html`` converts HTML that is already safe: it sanitizes
nothing and masks no credential. ``html_documents.document_from_html`` is the one entry for
untrusted HTML, and it sanitizes and redacts before it hands the markup on. Any other Python file
that imports, calls or names the safe entry could feed it untrusted HTML, so each is named here.
Every Python file outside a ``tests`` folder is read, and the entry's own definition is allowed.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

from rails_repo import REPO

ENTRY = "document_from_safe_html"
SANITIZER = "src/personalclaw/html_documents.py"
DEFINITION = "src/personalclaw/documents/from_markup.py"
SKIPPED = {"node_modules", "__pycache__", "site-packages", "tests"}


def python_files(root: Path) -> list[Path]:
    """Each ``.py`` file under *root*, outside ``tests`` folders, hidden folders and installs."""
    found: list[Path] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED and not d.startswith("."))
        found += [Path(folder) / f for f in sorted(files) if f.endswith(".py")]
    return found


def _names_the_entry(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == ENTRY
    if isinstance(node, ast.Attribute):
        return node.attr == ENTRY
    if isinstance(node, ast.alias):
        return ENTRY in (node.name.rpartition(".")[2], node.asname)
    if isinstance(node, ast.Constant):
        return node.value == ENTRY
    return False


def safe_entry_uses(root: Path) -> list[str]:
    """Each use of the entry in a file under *root*, other than the sanitizer's."""
    found: list[str] = []
    for path in python_files(root):
        rel = path.relative_to(root).as_posix()
        # Every file is parsed: adjacent literals ("document_from_" "safe_html") are one constant
        # to the parser, but no line of the file spells the whole name.
        if rel == SANITIZER:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == ENTRY and not (rel == DEFINITION and node in tree.body):
                    found.append(f"{rel}:{node.lineno} defines {ENTRY}")
            elif _names_the_entry(node):
                found.append(f"{rel}:{getattr(node, 'lineno', 0)} names {ENTRY}")
    return found


def test_only_the_sanitizer_reaches_the_safe_html_entry():
    """🔴 Red before this: the safe entry did not exist, and nothing held who may call it."""
    definition = ast.parse((REPO / DEFINITION).read_text(encoding="utf-8"))
    defined = {node.name for node in definition.body if isinstance(node, ast.FunctionDef)}
    assert ENTRY in defined, f"the entry moved out of {DEFINITION}: point DEFINITION at it"
    files = {p.relative_to(REPO).as_posix() for p in python_files(REPO)}
    assert {SANITIZER, DEFINITION} <= files, "this check would not read the entry's own files"
    assert len(files) >= 1000, f"read only {len(files)} files: this check would pass vacuously"
    assert safe_entry_uses(REPO) == []


def test_a_file_other_than_the_sanitizer_that_reaches_the_safe_entry_is_named(tmp_path):
    """An import, an attribute call, a name looked up by string (whole, or in two adjacent literals)
    and a second definition are each named; the sanitizer's own import, the definition and a
    ``tests`` folder are not."""
    files = {
        SANITIZER: "from personalclaw.documents.from_markup import document_from_safe_html\n",
        DEFINITION: '"""Mentions document_from_safe_html in prose."""\n'
        "def document_from_safe_html(safe):\n    return safe\n",
        "src/personalclaw/planted.py": "from personalclaw.documents import from_markup as fm\n"
        "x = fm.document_from_safe_html('<p>')\n",
        "scripts/planted.py": "from personalclaw.documents.from_markup import "
        "document_from_safe_html as convert\n",
        "tools/planted.py": "import importlib\n"
        "m = importlib.import_module('personalclaw.documents.from_markup')\n"
        "f = getattr(m, 'document_from_safe_html')\n",
        # No line of this file spells the whole name.
        "harness/planted.py": "import fm\ng = getattr(fm, 'document_from_' 'safe_html')\n",
        "packages/x/planted.py": "def document_from_safe_html(html):\n    return html\n",
        "packages/x/tests/test_planted.py": "from personalclaw.documents.from_markup import "
        "document_from_safe_html\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")

    assert safe_entry_uses(tmp_path) == [
        f"harness/planted.py:2 names {ENTRY}",
        f"packages/x/planted.py:1 defines {ENTRY}",
        f"scripts/planted.py:1 names {ENTRY}",
        f"src/personalclaw/planted.py:2 names {ENTRY}",
        f"tools/planted.py:3 names {ENTRY}",
    ]
