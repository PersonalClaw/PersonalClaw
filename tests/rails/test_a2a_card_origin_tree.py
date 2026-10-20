"""The source-tree checks split from
``tests/test_a2a_card_origin.py``.

They run once per CI run, in the rails job. The module named above says what they
hold to.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import source_index
from a2a_card_origin_shared import _GUESSED_PORT
from rails_repo import REPO

SRC = REPO / "src" / "personalclaw"


def _literal_port_sites(root: Path) -> set[tuple[str, int]]:
    """``(module, lineno)`` of every non-docstring string literal naming ``:10000``.

    The AST carries no comments, so prose about the old bug cannot satisfy this. Module,
    class and function docstrings ARE ``Constant`` nodes, so they are collected and
    excluded explicitly — otherwise every module that explains #2539 would read as an
    offender and the rail would have to be deleted to go green.
    """
    found: set[tuple[str, int]] = set()
    needle = f":{_GUESSED_PORT}"
    # The repository's own package is the suite's one source index; a fixture root is walked here.
    if root == SRC:
        trees = source_index.package_trees()
    else:
        trees = []
        for py in sorted(root.rglob("*.py")):
            try:
                trees.append((py, ast.parse(py.read_text(encoding="utf-8"))))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - defensive
                continue
    for py, tree in trees:
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                first = (getattr(node, "body", None) or [None])[0]
                if (
                    isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)
                ):
                    docstrings.add(id(first.value))
        rel = py.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and needle in node.value
                and id(node) not in docstrings
            ):
                found.add((rel, node.lineno))
    return found


class TestNoSiteGuessesThePortFromALiteral:
    """``test_gateway_base_owner.py``'s rail scans SYMBOL reads; #2620 was a LITERAL.

    That is the enumerated-rail blind spot the issue asked to have checked: the existing
    rail was green throughout, and correctly so — the offending line read no resolver
    name at all. This closes the shape rather than the instance.
    """

    def test_no_module_hard_codes_the_gateway_port_in_a_url(self) -> None:
        offenders = sorted(_literal_port_sites(SRC))
        assert not offenders, (
            "these string literals name the gateway's default port directly instead of "
            "asking personalclaw.gateway_base; on a multi-instance host the address they "
            f"publish is a DIFFERENT instance (#2539/#2620): {offenders}"
        )

    def test_the_scan_reds_on_a_planted_literal(self, tmp_path) -> None:
        """Detection direction. A rail that cannot fail is not a rail."""
        (tmp_path / "sneaky.py").write_text(
            '"""A docstring mentioning http://127.0.0.1:10000 must NOT count."""\n'
            "def card():\n"
            '    return "http://127.0.0.1:10000/a2a"\n',
            encoding="utf-8",
        )
        planted = _literal_port_sites(tmp_path)
        assert planted == {("sneaky.py", 3)}, planted

    def test_the_scan_is_not_vacuous(self) -> None:
        """The corpus is real and the docstring exclusion is doing work, not everything.

        Counted by a different mechanism than the AST walk — a plain textual count — so a
        walker that silently stopped parsing cannot satisfy both. The textual count is
        deliberately much higher than the AST count: most occurrences are the comments and
        docstrings that explain #2539, which is exactly what the exclusion removes.
        """
        pattern = re.compile(rf":{_GUESSED_PORT}\b")
        text_hits = sum(
            len(pattern.findall(source_index.text(py))) for py in source_index.package_files()
        )
        assert text_hits >= 5, f"only {text_hits} textual occurrences — the scan lost its corpus"
        assert len(_literal_port_sites(SRC)) == 0
