"""Every id comes from ``personalclaw.ids``, and nothing else in the package mints one.

Shared contracts §2.1 gives every id one shape, ``<prefix>_`` and 26 Crockford base32 characters,
and one helper that mints it. A second minter drifts: another alphabet, another order, another
length, and two ids of one kind stop sorting or comparing alike. So this rail fails on:

- the Crockford alphabet, in either case, anywhere but ``ids.py``: it is how a ULID is spelled;
- a ULID library imported anywhere in the package;
- a string built from a ``PREFIXES`` member and an underscore anywhere but ``ids.py``: an f-string
  whose literal part is exactly ``<prefix>_`` before one formatted value (``f"msg_{x}"``), or
  ``"<prefix>_" + x``. A longer literal is a different string (``f"run_once({x})"``,
  ``f"job_id={x}"``), and the pairing codes a person types use another alphabet
  (``auth/enrollment``, ``auth/pairing``), so neither is caught.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import source_index

from personalclaw import ids

MINTER = "src/personalclaw/ids.py"
ALPHABETS = ("0123456789abcdefghjkmnpqrstvwxyz", "0123456789ABCDEFGHJKMNPQRSTVWXYZ")
ULID_LIBRARIES = ("ulid", "ulid2", "ulid_py")


def _findings(files: Iterable[tuple[str, ast.Module]]) -> list[str]:
    found: list[str] = []
    for rel, tree in files:
        if rel == MINTER:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if any(a in node.value for a in ALPHABETS):
                    found.append(f"{rel}:{node.lineno}: the Crockford alphabet")
            elif isinstance(node, ast.Import | ast.ImportFrom):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                if any(n.split(".")[0] in ULID_LIBRARIES for n in names):
                    found.append(f"{rel}:{node.lineno}: a ULID library")
            elif isinstance(node, ast.JoinedStr):
                parts = node.values
                if (
                    len(parts) == 2
                    and isinstance(parts[0], ast.Constant)
                    and isinstance(parts[1], ast.FormattedValue)
                    and _prefix_of(parts[0].value)
                ):
                    found.append(f'{rel}:{node.lineno}: an id built as f"{parts[0].value}{{…}}"')
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                if isinstance(node.left, ast.Constant) and _prefix_of(node.left.value):
                    found.append(f"{rel}:{node.lineno}: an id built as {node.left.value!r} + …")
    return found


def _prefix_of(literal: object) -> bool:
    return isinstance(literal, str) and literal.endswith("_") and literal[:-1] in ids.PREFIXES


def _package() -> list[tuple[str, ast.Module]]:
    trees = source_index.package_trees()
    assert len(trees) > 500, f"walked {len(trees)} modules — vacuous"
    return [(source_index.rel(Path(path)), tree) for path, tree in trees]


def test_no_module_but_ids_mints_an_id() -> None:
    assert _findings(_package()) == []


def test_the_minter_itself_is_walked_and_skipped() -> None:
    rels = {rel for rel, _ in _package()}
    assert MINTER in rels, "the minter is not where this rail looks for it"


def test_every_way_of_minting_is_seen_and_nothing_else_is() -> None:
    """The positive control: each planted minting is found, beside strings that only look alike."""
    planted = ast.parse(
        "a = f'msg_{x}'\n"
        "b = 'job_' + y\n"
        "c = '0123456789abcdefghjkmnpqrstvwxyz'\n"
        "d = 'ABCD0123456789ABCDEFGHJKMNPQRSTVWXYZ'\n"
        "import ulid\n"
        "from ulid2 import generate\n"
        "e = f'run_once({x})'\n"
        "f = f'job_id={x}'\n"
        "g = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'\n"
        "h = f'cron_{x}'\n"
        "i = 'chat_' + y\n"
        "j = f'msg_{a}_{b}'\n"
    )
    found = _findings([("src/personalclaw/planted.py", planted)])
    lines = sorted(int(f.split(": ", 1)[0].rsplit(":", 1)[1]) for f in found)
    assert lines == [1, 2, 3, 4, 5, 6], found
    assert _findings([(MINTER, planted)]) == [], "the minter is the one place minting is allowed"
