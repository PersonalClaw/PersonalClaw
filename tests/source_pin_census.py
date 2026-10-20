"""How many source-text pins the tests outside ``tests/rails`` still hold: #3937's measure.

A source-text pin reads the product's source and asserts a literal in it. It passes on a comment,
a dead branch or a renamed symbol, and fails on a refactor that changes no behaviour
(``docs/maintainers/testing.md``). #3937 is done when these sites fall to half their count at its
starting commit, both counts taken by this counter under one rule. :data:`MEASURE` records them
(the baseline, the target and the latest measure), and the CI contract holds that row to this
counter (``tests/rails/test_ci_contract.py``).

The rule, over every ``.py`` file under ``tests/`` outside ``tests/rails``. A site is

- a **getsource site**: a site ``tests/getsource_census.py`` names (a ``getsource`` call, a
  reference to it not called where it stands, an import of the name), the rule the contract's
  getsource row holds to its list;
- an **in pin** or a **not-in pin**: an ``in`` / ``not in`` compare whose left side is a string
  literal (an f-string or a ``+`` of literals, by its constant parts) and whose right side is
  source text;
- a **count pin**: ``<source text>.count(<string literal>)``.

**Source text** is an expression that is, or is a name bound in the same function or at module
level to (an attribute of the test's class, read through ``self`` or ``cls``, counts too):

- a ``getsource`` call, or ``source_index.text(path)`` of a path that is not a document;
- a read of a product source path: ``<path>.read_text()``, ``open(<path>).read()``,
  ``<path>.open().read()``, or ``<f>.read()`` after ``with open(<path>) as <f>``;
- the return of a function under ``tests/`` that returns source text, or returns text derived
  from an argument when that argument is source text (a helper that reads or cuts the source);
- text derived from source text: ``.lower()``, ``.upper()``, ``.casefold()``, ``.replace()``,
  ``.strip()`` and its sides, ``.expandtabs()``, ``.splitlines()``, ``.split()``, ``str()``,
  ``dedent()``, ``"".join()``, a slice or an index, ``+``, an f-string holding it, a list,
  tuple, set or dict of it, a comprehension of it, and a loop variable over any of those.

A **product source path** is an expression, or a name bound to one, that is not a document and

- is read through a module's file: it holds ``<module>.__file__``, ``inspect.getsourcefile()``,
  ``inspect.getfile()`` or a spec's ``.origin``; or
- points into ``src/`` or ``web/``: it is rooted at an UPPERCASE name, at the package's name or
  at ``Path(...)``, and names a string that starts with ``src`` or ``web`` or holds
  ``personalclaw/``, or a name that holds ``src``, ``pkg`` or ``package``; or
- is built on such a path (``path.parent / "routes.py"``), or is a loop variable over one, a
  list of them, or their ``.glob()``, ``.rglob()`` or ``.iterdir()``.

A name imported from another module under ``tests/`` keeps what it is there: a shared module's
path, source text or helper counts where it is used. A **document** is a path that holds a string
ending in ``.md``, ``.json``, ``.toml``, ``.yaml``, ``.yml``, ``.txt``, ``.html``, ``.rst`` or
``.lock``, or a name bound only to such strings: #3937 leaves tests of documentation and generated
files alone. A test's own ``__file__`` is not a module's file. A docstring read as text
(``inspect.getdoc()``, ``__doc__``) is not source text, and a pin written in a comment or a
docstring is no compare at all. A compare repeated on one line counts once.

The getsource rule and the literal rule were fixed in #3937's first slice; this module is that
rule as recorded, with the reads its first implementation missed (through a module's file, a path
held in a name or a class, ``open()``, a helper, a shared module). Bump :data:`RULE` whenever
what counts changes, and record both numbers again (``python tests/source_pin_census.py ROOT``
prints them for a checkout; ``--list`` prints each site).
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import getsource_census

#: This counter, and the row #3937's line is read from, from the repository's root.
COUNTER = "tests/source_pin_census.py"
MEASURE = "tests/rails/baselines/source-pins.json"

#: The rule's version. A row taken under another version measured something else.
RULE = 1

#: Where a pin is allowed: the AST rails, each with its reason in its docstring.
ALLOWED = getsource_census.ALLOWED

#: The kinds of site, in the order the measure lists them.
KINDS = ("getsource", "in", "not_in", "count")

_PACKAGE = "personalclaw"
_PATH_HINTS = ("src", "pkg", "package")
_DOCUMENTS = (".md", ".json", ".toml", ".yaml", ".yml", ".txt", ".html", ".rst", ".lock")
_TEXT_METHODS = frozenset(
    ("lower", "upper", "casefold", "replace", "strip", "lstrip", "rstrip", "expandtabs")
    + ("splitlines", "split")
)
_MODULE_FILE_CALLS = frozenset({"getsourcefile", "getfile"})
#: Calls that hand back the path they are given, as a string. A ``Path`` class does too, under
#: any alias (``from pathlib import Path as _Path``): :func:`_wraps_a_path`.
_WRAPPERS = frozenset({"str", "fspath"})
_SELF = frozenset({"self", "cls"})

#: A module that cannot read source text itself: it holds none of these words.
_READS = re.compile(r"getsource|read_text|read\(|source_index|__file__|getfile|find_spec|\.origin")
_IMPORTS = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE)


@dataclass(frozen=True)
class Site:
    """One site: where it is, its kind, and the literal it pins (empty for a getsource site)."""

    path: str
    line: int
    scope: str
    kind: str
    literal: str = ""
    source: str = ""


@dataclass
class _Names:
    """What the names of one scope hold. A class attribute is kept as ``self.<name>``."""

    text: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)
    handles: set[str] = field(default_factory=set)
    strings: dict[str, set[str]] = field(default_factory=dict)
    readers: set[str] = field(default_factory=set)
    cutters: set[str] = field(default_factory=set)
    modules: dict[str, _Exports] = field(default_factory=dict)

    def copy(self) -> _Names:
        return _Names(
            set(self.text),
            set(self.paths),
            set(self.handles),
            {name: set(values) for name, values in self.strings.items()},
            set(self.readers),
            set(self.cutters),
            dict(self.modules),
        )


@dataclass(frozen=True)
class _Exports:
    """What a module under tests/ hands another: its module-level names, by what they hold."""

    text: frozenset[str] = frozenset()
    paths: frozenset[str] = frozenset()
    readers: frozenset[str] = frozenset()
    cutters: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        return bool(self.text or self.paths or self.readers or self.cutters)


# ── what an expression is ────────────────────────────────────────────────────────────────────


def _callee(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return ""


def _key(node: ast.AST) -> str:
    """The name *node* is bound under: ``x``, or ``self.x`` for an attribute of the class."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id in _SELF:
            return f"self.{node.attr}"
    return ""


def _root(node: ast.AST) -> ast.AST:
    """What a path expression is built on: ``ROOT`` for ``(ROOT / "src").parent``."""
    while True:
        if _key(node).startswith("self."):
            return node
        if isinstance(node, ast.BinOp):
            node = node.left
        elif isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        elif isinstance(node, ast.Call) and _callee(node) == "Path":
            return node
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            node = node.func.value
        else:
            return node


def _strings(node: ast.AST) -> Iterator[str]:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value


def _is_document(node: ast.AST, names: _Names) -> bool:
    if any(value.endswith(_DOCUMENTS) for value in _strings(node)):
        return True
    for sub in ast.walk(node):
        values = names.strings.get(sub.id) if isinstance(sub, ast.Name) else None
        if values and all(value.endswith(_DOCUMENTS) for value in values):
            return True
    return False


def _spine(node: ast.AST) -> Iterator[ast.AST]:
    """What a path expression is built from: *node*, then what each step of it is taken from
    (``(Path(m.__file__).parent / "x.py")`` is built from ``m.__file__``), never the arguments
    of a call that only takes the path as its input."""
    while True:
        yield node
        if isinstance(node, ast.BinOp):
            node = node.left
        elif isinstance(node, (ast.Attribute, ast.Subscript)):
            node = node.value
        elif isinstance(node, ast.Call) and _wraps_a_path(node):
            node = node.args[0]
        elif isinstance(node, ast.BoolOp):
            node = node.values[0]  # ``getsourcefile(m) or ""``
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            node = node.func.value
        else:
            return


def _wraps_a_path(call: ast.Call) -> bool:
    name = _callee(call)
    return bool(call.args) and (name in _WRAPPERS or name.lstrip("_").endswith("Path"))


def _through_a_module_file(node: ast.AST) -> bool:
    for step in _spine(node):
        if isinstance(step, ast.Attribute) and step.attr == "__file__":
            return True
        if isinstance(step, ast.Call) and _callee(step) in _MODULE_FILE_CALLS:
            return True
        if isinstance(step, ast.Attribute) and step.attr == "origin":
            spec = step.value
            if (isinstance(spec, ast.Call) and _callee(spec) == "find_spec") or (
                isinstance(spec, ast.Attribute) and spec.attr == "__spec__"
            ):
                return True
    return False


def _into_src_or_web(node: ast.AST) -> bool:
    root = _root(node)
    if isinstance(root, ast.Name):
        rooted = root.id.lstrip("_").isupper() or root.id == _PACKAGE
    else:
        rooted = isinstance(root, ast.Call) and _callee(root) == "Path"
    if not rooted:
        return False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if sub.value.startswith(("src", "web")) or f"{_PACKAGE}/" in sub.value:
                return True
        ident = sub.id if isinstance(sub, ast.Name) else getattr(sub, "attr", "")
        if ident and (ident == _PACKAGE or any(h in ident.lower() for h in _PATH_HINTS)):
            return True
    return False


def _is_path(node: ast.AST, names: _Names) -> bool:
    """Whether *node* is a path into the product (a document still answers yes here)."""
    for step in _spine(node):
        if _key(step) in names.paths:
            return True
    return _through_a_module_file(node) or _into_src_or_web(node)


def _source_path(node: ast.AST, names: _Names) -> bool:
    return _is_path(node, names) and not _is_document(node, names)


def _source_paths(node: ast.AST, names: _Names) -> bool:
    """Whether a loop over *node* yields product source paths."""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_source_paths(e, names) for e in node.elts)
    if isinstance(node, (ast.GeneratorExp, ast.ListComp)):
        return _source_paths(node.elt, names)
    if isinstance(node, ast.Call) and node.args and _callee(node) in {"sorted", "list", "tuple"}:
        return _source_paths(node.args[0], names)
    return _source_path(node, names)  # a path, or a glob over one: ``SRC.rglob("*.py")``


def _opens_source(call: ast.Call, names: _Names) -> bool:
    if _callee(call) != "open":
        return False
    func = call.func
    if isinstance(func, ast.Attribute) and not (
        isinstance(func.value, ast.Name) and func.value.id in {"io", "builtins", "codecs"}
    ):
        return _source_path(func.value, names)
    return bool(call.args) and _source_path(call.args[0], names)


def _is_text(node: ast.AST, names: _Names) -> bool:
    """Whether *node* is source text under the module docstring's rule."""
    key = _key(node)
    if key:
        return key in names.text
    if isinstance(node, ast.Call):
        return _call_is_text(node, names)
    if isinstance(node, ast.Subscript):
        return _is_text(node.value, names)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_is_text(e, names) for e in node.elts)
    if isinstance(node, ast.Dict):
        return any(_is_text(v, names) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_text(node.left, names) or _is_text(node.right, names)
    if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        return _is_text(node.elt, _bind(node.generators, names))
    if isinstance(node, ast.DictComp):
        return _is_text(node.value, _bind(node.generators, names))
    if isinstance(node, ast.JoinedStr):
        return any(
            isinstance(v, ast.FormattedValue) and _is_text(v.value, names) for v in node.values
        )
    return False


def _call_is_text(call: ast.Call, names: _Names) -> bool:
    name = _callee(call)
    func = call.func
    if name == "getsource":
        return True
    if isinstance(func, ast.Attribute):
        owner = func.value
        if name == "text" and isinstance(owner, ast.Name) and owner.id == "source_index":
            return not (call.args and _is_document(call.args[0], names))
        if name == "read_text":
            return _source_path(owner, names)
        if name == "read":
            return _key(owner) in names.handles or (
                isinstance(owner, ast.Call) and _opens_source(owner, names)
            )
        if name in _TEXT_METHODS:
            return _is_text(owner, names)
        if name == "join":
            return bool(call.args) and _is_text(call.args[0], names)
        exports = names.modules.get(owner.id) if isinstance(owner, ast.Name) else None
        if exports is not None:
            return name in exports.readers or (
                name in exports.cutters
                and any(_is_text(a, names) or _source_path(a, names) for a in call.args)
            )
    own = isinstance(func, ast.Name) or (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id in _SELF
    )
    if own and name in names.readers:
        return True
    if own and name in names.cutters:
        return any(_is_text(a, names) or _source_path(a, names) for a in call.args)
    if name in {"dedent", "str"}:
        return any(_is_text(a, names) for a in call.args)
    return False


# ── what the names of a scope hold ───────────────────────────────────────────────────────────


def _targets(node: ast.AST) -> list[str]:
    if _key(node):
        return [_key(node)]
    return [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]


def _string_values(node: ast.AST) -> set[str] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)) and node.elts:
        if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
            return {e.value for e in node.elts}  # type: ignore[union-attr]
    return None


def _bindings(nodes: Iterable[ast.AST]) -> list[tuple[list[str], ast.AST, str]]:
    """Each binding among *nodes*: its names, its value, and how it binds (``=``, ``for``,
    ``with``)."""
    found: list[tuple[list[str], ast.AST, str]] = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            found.append(([n for t in node.targets for n in _targets(t)], node.value, "="))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and node.value:
            found.append((_targets(node.target), node.value, "="))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            found.append((_targets(node.target), node.iter, "for"))
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            found.append((_targets(node.optional_vars), node.context_expr, "with"))
    return found


def _bind(nodes: Iterable[ast.AST], names: _Names) -> _Names:
    """*names* with what *nodes* bind added, to a fixed point (a name bound from a name bound
    further down)."""
    bound = names.copy()
    bindings = _bindings(nodes)
    for targets, value, _how in bindings:
        strings = _string_values(value) if _how == "for" else None
        if strings is None and isinstance(value, ast.Constant) and isinstance(value.value, str):
            strings = {value.value}
        for target in targets if strings else ():
            bound.strings.setdefault(target, set()).update(strings or ())
    while True:
        before = (len(bound.text), len(bound.paths), len(bound.handles))
        for targets, value, how in bindings:
            if how == "with" and isinstance(value, ast.Call) and _opens_source(value, bound):
                bound.handles.update(targets)
            elif how == "for":
                over_text = _is_text(value, bound) or (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and value.func.attr in {"items", "values"}
                    and _is_text(value.func.value, bound)
                )
                if over_text:
                    bound.text.update(targets)
                elif _source_paths(value, bound):
                    bound.paths.update(targets)
            elif _is_text(value, bound):
                bound.text.update(targets)
            elif not isinstance(value, ast.Constant) and _source_path(value, bound):
                if not (isinstance(value, ast.Call) and _callee(value) in {"read", "read_bytes"}):
                    bound.paths.update(targets)
        if (len(bound.text), len(bound.paths), len(bound.handles)) == before:
            return bound


def _parameters(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = fn.args
    return [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs) if a.arg not in _SELF]


def _returns_text(fn: ast.FunctionDef | ast.AsyncFunctionDef, names: _Names) -> bool:
    bound = _bind(ast.walk(fn), names)
    return any(
        isinstance(node, ast.Return) and node.value is not None and _is_text(node.value, bound)
        for node in ast.walk(fn)
    )


def _helpers(functions: list[ast.FunctionDef | ast.AsyncFunctionDef], names: _Names) -> _Names:
    """*names* with the functions that return source text (readers) and those that return text
    derived from an argument that is source text (cutters)."""
    names = names.copy()
    returning = [fn for fn in functions if any(isinstance(n, ast.Return) for n in ast.walk(fn))]
    while True:
        before = (len(names.readers), len(names.cutters))
        for fn in returning:
            if fn.name in names.readers:
                continue
            if _returns_text(fn, names):
                names.readers.add(fn.name)
                names.cutters.discard(fn.name)
            elif fn.name not in names.cutters and _parameters(fn):
                handed = names.copy()
                handed.text.update(_parameters(fn))
                handed.paths.update(_parameters(fn))
                if _returns_text(fn, handed):
                    names.cutters.add(fn.name)
        if (len(names.readers), len(names.cutters)) == before:
            return names


def _module_statements(tree: ast.Module) -> list[ast.AST]:
    """Every node at module level, outside any function or class."""
    found: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _class_names(cls: ast.ClassDef, names: _Names) -> _Names:
    """*names* with the class's own attributes, as ``self.<name>``."""
    body = [s for s in cls.body if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))]
    plain = _bind([n for s in body for n in ast.walk(s)], names)
    bound = names.copy()
    bound.text |= {f"self.{n}" for n in plain.text - names.text}
    bound.paths |= {f"self.{n}" for n in plain.paths - names.paths}
    return bound


# ── a tree of test modules ───────────────────────────────────────────────────────────────────


class _Tree:
    """The test modules under one root, each parsed once, and what each one exports."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.text: dict[str, str] = {}
        for path in sorted((root / "tests").rglob("*.py")):
            if "__pycache__" not in path.parts:
                self.text[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
        self._trees: dict[str, ast.Module] = {}
        self._names: dict[str, _Names] = {}
        self._exports: dict[str, _Exports] = {}

    def tree(self, rel: str) -> ast.Module:
        if rel not in self._trees:
            self._trees[rel] = ast.parse(self.text[rel], filename=rel)
        return self._trees[rel]

    def module(self, dotted: str) -> str:
        """The file under tests/ that ``import <dotted>`` loads, or ``""``."""
        parts = dotted.split(".")
        if parts[0] == "tests":
            parts = parts[1:]
        for base in ("tests", "tests/rails"):
            rel = "/".join([base, *parts]) + ".py"
            if rel in self.text:
                return rel
        return ""

    def exports(self, rel: str) -> _Exports:
        if rel not in self._exports:
            self._exports[rel] = _Exports()  # an import cycle reads the module as exporting none
            names = self.names(rel)
            top = {
                n.name
                for n in self.tree(rel).body
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            self._exports[rel] = _Exports(
                frozenset(n for n in names.text if "." not in n),
                frozenset(n for n in names.paths if "." not in n),
                frozenset(names.readers & top),
                frozenset(names.cutters & top),
            )
        return self._exports[rel]

    def _imported(self, rel: str) -> _Names:
        names = _Names()
        for node in ast.walk(self.tree(rel)):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                other = self.module(node.module)
                exports = self.exports(other) if other and other != rel else _Exports()
                for alias in node.names:
                    name, local = alias.name, alias.asname or alias.name
                    for held, into in (
                        (exports.text, names.text),
                        (exports.paths, names.paths),
                        (exports.readers, names.readers),
                        (exports.cutters, names.cutters),
                    ):
                        if name in held:
                            into.add(local)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    other = self.module(alias.name)
                    if other and other != rel and (alias.asname or "." not in alias.name):
                        names.modules[alias.asname or alias.name] = self.exports(other)
        return names

    def names(self, rel: str) -> _Names:
        """What the module's own names hold at module level, its helpers included."""
        if rel not in self._names:
            tree = self.tree(rel)
            statements = _module_statements(tree)
            names = _bind(statements, self._imported(rel))
            functions = [
                n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            ]
            self._names[rel] = _bind(statements, _helpers(functions, names))
        return self._names[rel]

    def may_read_source(self, rel: str) -> bool:
        """Whether the module can hold a literal pin: it reads source itself, or imports a
        module under tests/ that hands it a path, source text or a helper."""
        text = self.text[rel]
        if _READS.search(text):
            return True
        for match in _IMPORTS.finditer(text):
            other = self.module(match[1] or match[2])
            if other and other != rel and self.exports(other):
                return True
        return False


def _scopes(tree: ast.Module, names: _Names) -> Iterator[tuple[str, ast.AST, _Names]]:
    """Each scope a site is named for, the node its sites are in, and what its names hold."""

    def in_class(cls: ast.ClassDef, prefix: str, outer: _Names) -> Iterator:
        scope = f"{prefix}{cls.name}"
        inner = _class_names(cls, outer)
        for stmt in cls.body:
            if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                yield f"{scope}.{stmt.name}", stmt, _bind(ast.walk(stmt), inner)
            elif isinstance(stmt, ast.ClassDef):
                yield from in_class(stmt, f"{scope}.", inner)
            else:
                yield scope, stmt, outer

    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
            yield stmt.name, stmt, _bind(ast.walk(stmt), names)
        elif isinstance(stmt, ast.ClassDef):
            yield from in_class(stmt, "", names)
        else:
            yield "<module>", stmt, names


def _literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = [v.value for v in node.values if isinstance(v, ast.Constant)]
        return "".join(str(p) for p in parts) if parts else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        if left is not None and right is not None:
            return left + right
    return None


def _pins(node: ast.AST, names: _Names) -> Iterator[tuple[int, str, str, ast.AST]]:
    """Each literal pin under *node*: its line, kind, literal and the text it reads."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Compare):
            left = sub.left
            for op, right in zip(sub.ops, sub.comparators, strict=True):
                if isinstance(op, ast.In | ast.NotIn):
                    literal = _literal(left)
                    if literal is not None and _is_text(right, names):
                        kind = "in" if isinstance(op, ast.In) else "not_in"
                        yield sub.lineno, kind, literal, right
                left = right
        elif (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "count"
            and sub.args
            and _literal(sub.args[0]) is not None
            and _is_text(sub.func.value, names)
        ):
            yield sub.lineno, "count", _literal(sub.args[0]) or "", sub.func.value


def module_pins(tree: _Tree, rel: str) -> list[Site]:
    """The literal pins in one module under *tree*, in scope order."""
    if not tree.may_read_source(rel):
        return []
    found: list[Site] = []
    seen: set[tuple[int, str, str, str]] = set()
    for scope, node, names in _scopes(tree.tree(rel), tree.names(rel)):
        for line, kind, literal, text in _pins(node, names):
            source = " ".join(ast.unparse(text).split())
            if (line, kind, literal, source) not in seen:
                seen.add((line, kind, literal, source))
                found.append(Site(rel, line, scope, kind, literal, source))
    return found


def sites(root: Path) -> list[Site]:
    """Every site under *root*'s ``tests/`` outside ``tests/rails``: the getsource sites first,
    then the literal pins, by path."""
    found = []
    for name in getsource_census.sites(root):
        rel, scope, source = name.split("::", 2)
        found.append(Site(rel, 0, scope, "getsource", "", source))
    tree = _Tree(root)
    for rel in tree.text:
        if not rel.startswith(ALLOWED):
            found += module_pins(tree, rel)
    return found


def tally(found: Iterable[Site]) -> dict[str, int]:
    """*found*, in all and by kind: the numbers :data:`MEASURE` records."""
    kinds = Counter(site.kind for site in found)
    return {"sites": sum(kinds.values()), **{kind: kinds[kind] for kind in KINDS}}


def measure(root: Path) -> dict[str, int]:
    """The sites under *root*, in all and by kind."""
    return tally(sites(root))


# ── the recorded row ─────────────────────────────────────────────────────────────────────────

_SHA = re.compile(r"[0-9a-f]{40}")
_COUNTS = ("sites", *KINDS)


def row_problems(root: Path) -> list[str]:
    """What is wrong with *root*'s :data:`MEASURE` row, as the CI contract reads it: the row is
    this counter's, under its current rule, each count is the sum of its kinds at a full commit
    sha, and the target is half the baseline."""
    path = root / MEASURE
    if not path.is_file():
        return [f"{MEASURE} is missing: #3937's line has no recorded baseline"]
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{MEASURE} is not JSON: {exc}"]
    if not isinstance(recorded, dict):
        return [f"{MEASURE} holds {type(recorded).__name__}, not one row"]
    problems: list[str] = []
    if recorded.get("counter") != COUNTER:
        problems.append(f"the row names {recorded.get('counter')!r}, not this counter")
    if recorded.get("rule") != RULE:
        problems.append(
            f"the row was taken under rule {recorded.get('rule')!r}, the counter's rule is {RULE}:"
            " count both commits again with python tests/source_pin_census.py"
        )
    for side in ("baseline", "measured"):
        taken = recorded.get(side)
        if not isinstance(taken, dict):
            problems.append(f"the row has no {side}")
            continue
        if not isinstance(taken.get("sha"), str) or not _SHA.fullmatch(taken["sha"]):
            problems.append(f"the {side} names no full commit sha: {taken.get('sha')!r}")
        counts = {name: taken.get(name) for name in _COUNTS}
        if not all(isinstance(n, int) and not isinstance(n, bool) for n in counts.values()):
            problems.append(f"the {side} lacks a count: {counts}")
        elif counts["sites"] != sum(counts[kind] for kind in KINDS):
            problems.append(f"the {side}'s {counts['sites']} sites are not the sum of its kinds")
    baseline = recorded.get("baseline")
    sites_then = baseline.get("sites") if isinstance(baseline, dict) else None
    if isinstance(sites_then, int) and recorded.get("target") != sites_then // 2:
        problems.append(
            f"the target {recorded.get('target')!r} is not half the baseline's {sites_then}"
            f" sites ({sites_then // 2})"
        )
    return problems


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    found = sites(root)
    if "--list" in argv:
        for site in found:
            literal = site.literal.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")
            print(f"{site.path}\t{site.line}\t{site.scope}\t{site.kind}\t{literal}\t{site.source}")
    print(json.dumps(tally(found)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
