"""The SSO / OIDC design note cites code by NAME, and every name it cites still exists (ledger 286).

``docs/architecture/sso-oidc-integration.md`` promises that "every claim below cites the file and
line it was read from". Measured after #3727, most of its ~60 ``file.py:NNN`` citations pointed
somewhere else: ``token_auth.py:497`` was cited as ``generate_token()`` and landed in a comment
block, and the login handler it quoted no longer mints through ``generate_token`` at all. A line
number goes stale with the next edit above it, and nothing reads a design note, so nothing says.

So the note now cites ``path::symbol`` — a function, class or constant by name — and this file
checks each one against the tree. A rename or deletion reds here; a line shift cannot break it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NOTE = REPO / "docs" / "architecture" / "sso-oidc-integration.md"

#: `path::symbol` inside backticks — the one citation form the note uses.
_CITATION = re.compile(r"`((?:src|web|desktop|docs|tests)/[\w./-]+)::([A-Za-z_][\w.]*)`")
#: A citation by line number (`token_auth.py:497`, `auth.py:230-247`, `modes.py:46`), or a bare
#: one continuing the file named before it (`:1268`): the forms that went stale, and must not
#: come back.
_LINE_CITATION = re.compile(r"\b[\w./-]+\.(?:py|ts|tsx|js|md):\d+|`:\d+(?:-\d+)?`")


def _python_names(path: Path) -> set[str]:
    """Every function, class and assigned name in *path*, dotted for members (`Class.method`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(prefix + child.name)
                visit(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        names.add(prefix + target.id)
            elif isinstance(child, (ast.If, ast.Try, ast.With)):
                visit(child, prefix)

    visit(tree, "")
    return names


def _script_names(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    pattern = r"(?:function|const|let|class|interface|type)\s+([A-Za-z_$][\w$]*)"
    return set(re.findall(pattern, source))


def _citations() -> list[tuple[str, str]]:
    return _CITATION.findall(NOTE.read_text(encoding="utf-8"))


def test_the_note_cites_code_by_name():
    """Vacuity floor: a note that cited nothing would pass every other check here."""
    assert len(_citations()) >= 30, f"only {len(_citations())} `path::symbol` citations"


def test_the_note_cites_nothing_by_line_number():
    stale = sorted(set(_LINE_CITATION.findall(NOTE.read_text(encoding="utf-8"))))
    assert not stale, f"cite the function, class or constant instead: {stale}"


def test_every_cited_name_still_exists():
    missing = []
    for rel, symbol in _citations():
        path = REPO / rel
        if not path.is_file():
            missing.append(f"{rel} (no such file)")
            continue
        names = _python_names(path) if path.suffix == ".py" else _script_names(path)
        if symbol not in names:
            missing.append(f"{rel}::{symbol}")
    assert not missing, "\n".join(missing)


def test_the_checker_discriminates(tmp_path):
    """Positive control: a real name resolves and a made-up one does not, so a green above is
    not the checker failing to look."""
    real = REPO / "src" / "personalclaw" / "dashboard" / "token_auth.py"
    assert "mint_session" in _python_names(real)
    assert "TokenStateManager.is_nonce_valid" in _python_names(real)
    assert "no_such_function_anywhere" not in _python_names(real)
    fake = tmp_path / "x.py"
    fake.write_text("if True:\n    NAME = 1\n", encoding="utf-8")
    assert "NAME" in _python_names(fake)
