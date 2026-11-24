"""No gate reads a tool family: a family is the words that describe a call, never a decision.

``tool_families.family_of`` sorts a call into read, edit, command and the rest so every surface
says it one way ("edited 3 files", "Change 2 files?"). Nothing decides by it whether a call runs,
asks or is refused (shared contracts §3.3.1): what a call may do is read from its declaration, its
kind and the owner's grants. So only the surfaces that say a call in words import the module
(:data:`WORDS`), each with why, and none of them is a module that decides (:func:`_decides`).
An import counts in every spelling the tree uses: absolute or relative (resolved against the
importing module's package), at the top or inside a function, or a load by name
(``importlib.import_module``, ``__import__``) of a name the source spells. A name built at run
time can't be read from the source and is out of this rail's reach: the tree's built names load
registered tool, provider and trigger modules. A family name taken from a surface of words,
imported or read as the module's attribute, rather than from the family module, is a read through
it and fails too.

Read from the source tree, so it runs in the rails job.
"""

from __future__ import annotations

import ast

import source_index
from table_rows import row

MODULE = "personalclaw.tool_families"

#: The modules that import ``tool_families``, each a surface that says a call in words.
WORDS: dict[str, str] = {
    "src/personalclaw/dashboard/turn_rows.py": "each tool part of a typed turn carries its family",
}

#: The modules that decide whether a call runs, asks or is refused, by their path: the approval
#: grants and confirms, the task modes, the guardrails and every gate, grant and posture module.
_DECIDING_NAMES = ("approval_confirms", "task_modes")
_DECIDING_WORDS = frozenset({"gate", "gates", "grant", "grants", "posture", "ungated"})


def _decides(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1].removesuffix(".py")
    return (
        rel.startswith("src/personalclaw/guardrails/")
        or name in _DECIDING_NAMES
        or not _DECIDING_WORDS.isdisjoint(name.split("_"))
    )


#: What a module re-exporting the family module's words would hand on: importing one of these from
#: a WORDS module is reading a family through it.
_FAMILY_NAMES = frozenset({"family_of", "CORE_FAMILIES", "KIND_FAMILIES"})

_LOADERS = frozenset({"import_module", "__import__"})


def _package(rel: str) -> list[str]:
    """The package a module at *rel* (``src/personalclaw/…/name.py``) is in, as its parts: what a
    relative import is resolved against."""
    return rel.removeprefix("src/").removesuffix(".py").split("/")[:-1]


def _module_of(rel: str) -> str:
    return ".".join(rel.removeprefix("src/").removesuffix(".py").split("/")).removesuffix(
        ".__init__"
    )


def _imported_from(node: ast.ImportFrom, rel: str) -> list[str] | None:
    """The module *node* imports from, resolved against *rel*'s package, as its parts; None for
    a relative import that climbs out of the tree."""
    named = node.module.split(".") if node.module else []
    if not node.level:
        return named
    package = _package(rel)
    if node.level - 1 >= len(package):
        return None
    return package[: len(package) - (node.level - 1)] + named


def _imports_families(tree: ast.AST, rel: str) -> bool:
    """Whether *tree*, the module at *rel*, imports the family module: at its top or inside a
    function, absolute or relative, or loaded by name (``importlib.import_module``,
    ``__import__``)."""
    target = MODULE.split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == MODULE for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom):
            base = _imported_from(node, rel)
            if base == target or (
                base is not None and any(base + [a.name] == target for a in node.names)
            ):
                return True
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            named = [k.value for k in node.keywords if k.arg == "name"]
            first = node.args[0] if node.args else (named[0] if named else None)
            if (
                name in _LOADERS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value.rsplit(".", 1)[-1] == "tool_families"
            ):
                return True
    return False


def _dotted(node: ast.AST) -> str | None:
    """The dotted name a ``Name`` or ``Attribute`` chain spells (``a.b.c``), else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _reads_through_words(tree: ast.AST, rel: str) -> list[str]:
    """The family names *tree* takes from a WORDS module rather than from the family module:
    imported by name, or read as an attribute of the module (``turn_rows.family_of``) under any
    name the module is bound to."""
    words = {_module_of(path) for path in WORDS}
    bound = set(words)  # ``import personalclaw.dashboard.turn_rows`` reads it by its full name
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound |= {a.asname for a in node.names if a.asname and a.name in words}
        elif isinstance(node, ast.ImportFrom) and (base := _imported_from(node, rel)) is not None:
            if ".".join(base) in words:
                found += [a.name for a in node.names if a.name in _FAMILY_NAMES]
            bound |= {a.asname or a.name for a in node.names if ".".join(base + [a.name]) in words}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FAMILY_NAMES:
            if _dotted(node.value) in bound:
                found.append(node.attr)
    return found


def _importers() -> set[str]:
    importers = set()
    for path in source_index.package_files():
        rel = source_index.rel(path)
        if path.name != "tool_families.py" and _imports_families(source_index.tree(path), rel):
            importers.add(rel)
    return importers


def test_only_the_surfaces_of_words_import_the_family_module() -> None:
    importers = _importers()
    assert importers, "nothing imports tool_families: the scan found no importer — vacuous"
    assert sorted(importers - set(WORDS)) == [], (
        "a module imports tool_families without a row in WORDS: a family is words, so say which "
        "surface it describes, and never decide by it"
    )
    assert sorted(set(WORDS) - importers) == [], "a WORDS row names a module that imports nothing"


def test_no_module_that_decides_is_a_surface_of_words() -> None:
    assert [rel for rel in WORDS if _decides(rel)] == []
    assert not _decides("src/personalclaw/gateway.py"), "a gateway is not a gate"
    for gate in (
        "src/personalclaw/approval_grants.py",
        "src/personalclaw/task_modes.py",
        "src/personalclaw/guardrails/policy.py",
        "src/personalclaw/agents/native/read_gate.py",
        "src/personalclaw/computer_use/gate.py",
        "src/personalclaw/browse/grant.py",
        "src/personalclaw/loop/posture.py",
    ):
        assert _decides(gate), gate


def test_no_module_reads_a_family_through_a_surface_of_words() -> None:
    """A WORDS module that imports ``family_of`` hands it on to anything importing it from there:
    that reads a family as surely as importing the family module does, and the scan of importers
    would not see it."""
    through = {
        rel: names
        for path in source_index.package_files()
        if (names := _reads_through_words(source_index.tree(path), rel := source_index.rel(path)))
    }
    assert through == {}, (
        "these import a family name from a surface of words rather than from tool_families: "
        "import it from tool_families, with a row in WORDS"
    )


def test_the_scan_sees_each_spelling_of_the_import(subtests) -> None:
    """The positive control: a planted import, in each spelling the tree uses, is found."""
    top, nested = "src/personalclaw/task_modes.py", "src/personalclaw/guardrails/policy.py"
    for rel, source in (
        (top, "from personalclaw.tool_families import family_of\n"),
        (top, "import personalclaw.tool_families\n"),
        (top, "import personalclaw.tool_families as families\n"),
        (top, "from personalclaw import tool_families\n"),
        (top, "def gate():\n    from personalclaw.tool_families import family_of\n"),
        (top, "from .tool_families import family_of\n"),
        (top, "from . import tool_families\n"),
        (nested, "from ..tool_families import family_of\n"),
        (nested, "from .. import tool_families\n"),
        (top, "import importlib\nimportlib.import_module('personalclaw.tool_families')\n"),
        (top, "from importlib import import_module\nimport_module('personalclaw.tool_families')\n"),
        (top, "__import__('personalclaw.tool_families')\n"),
        (top, "import importlib\nimportlib.import_module(name='personalclaw.tool_families')\n"),
    ):
        with row(subtests, rel=rel, source=source):
            assert _imports_families(ast.parse(source), rel)
    for rel, source in (
        (top, "from personalclaw import turns\n"),
        (top, "from .turns import Family\n"),
        (nested, "from .tool_families import family_of\n"),  # guardrails.tool_families: none
        (top, "import importlib\nimportlib.import_module('personalclaw.turns')\n"),
    ):
        with row(subtests, rel=rel, source=source, found=False):
            assert not _imports_families(ast.parse(source), rel)


def test_a_family_read_through_a_surface_of_words_is_seen() -> None:
    """The positive control for the re-export scan."""
    rel = "src/personalclaw/task_modes.py"
    planted = "from personalclaw.dashboard.turn_rows import family_of, Row\n"
    assert _reads_through_words(ast.parse(planted), rel) == ["family_of"]
    relative = "from .dashboard.turn_rows import CORE_FAMILIES\n"
    assert _reads_through_words(ast.parse(relative), rel) == ["CORE_FAMILIES"]
    for source in (
        "from personalclaw.dashboard import turn_rows\nturn_rows.family_of\n",
        "from .dashboard import turn_rows as rows\nrows.KIND_FAMILIES\n",
        "import personalclaw.dashboard.turn_rows as rows\nrows.family_of\n",
        "import personalclaw.dashboard.turn_rows\npersonalclaw.dashboard.turn_rows.family_of\n",
    ):
        assert _reads_through_words(ast.parse(source), rel), source
    unrelated = "from personalclaw import turns\nturns.family_of\n"
    assert _reads_through_words(ast.parse(unrelated), rel) == []
    assert _reads_through_words(ast.parse("from personalclaw.turns import Family\n"), rel) == []
