"""No rail holds a count of the repository's sites to a fixed number.

A rail that compares a count with a fixed number goes red on whichever of two pull requests
merges second, though neither broke it: each fixed one site and added another, and the number
each was measured against no longer holds once both land. Such a rail lists its sites instead,
one line each with its reason, in a site baseline (``tests/site_baseline.py``). The comparisons
still in ``tests/rails`` are listed, one line each, in :data:`BASELINE`, which only shrinks: a
converted rail's line is deleted, and a comparison the list does not hold fails the CI contract
row that reads this module (``tests/rails/test_ci_contract.py``). What stays listed for good is
an absolute cap, a limit that does not count sites, with the reason it is one.

The rule, over every ``.py`` file under ``tests/rails``. A site is a comparison in an ``assert``
that holds a **count** to an **integer** from above:

- the count is a call of ``len`` or ``sum``, or a name its function or module binds to one;
- the integer is a name a module binds to an integer literal (the comparison's own module, or a
  helper module it imports the name from or reads it off), whatever its value, since a number
  given a module's name is one somebody means to move; or an integer literal, or a name the
  comparison's function binds to one, when the count is of the repository (below);
- from above: ``count < N``, ``count <= N`` or ``count == N``, or the same written the other way
  round, alone or as one link of a chain (``0 < count <= N``).

A count is **of the repository** when what it counts reaches the tree: it names ``REPO``, a
module's ``__file__``, or anything of ``source_index`` or ``rails_repo``, directly, through a name
or function of its own module that does, or through a helper module under ``tests/`` or
``tests/rails`` that does. What a list, tuple, set or dict written out counts is its entries, and
what a comprehension counts is what it walks and keeps (its ``for`` and ``if`` clauses), not what
it makes of each item. A container a function fills (``append``, ``add``, ``extend``,
``update``, ``x[k] = v``, ``+=``) counts what it is filled with and what every loop around the
filling walks. So a count of a fixture the test planted, of an AST node's fields, or of
the test's own table is not of the repository, even when its entries read the tree, and holding
it to a literal is a test's oracle, not a ratchet. A function that reaches the tree only through
a default argument reaches it at every call.

Not sites either:

- a floor, ``count >= N`` or ``count > N``: a scan that found fewer than N measured nothing;
- a literal that holds the count to at most one (``== 0``, ``== 1``, ``< 2``): none and exactly
  one are the rule itself, not a number a pull request moves, and two pull requests that each
  add a site each fail on their own.

Each site is named ``path::scope::<the comparison>`` (``site_baseline.code_site``), repeats in one
function as ``#2``, ``#3``.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from functools import cache
from pathlib import Path

from site_baseline import code_site, numbered

#: The comparisons still allowed, from the repository's root.
BASELINE = "tests/rails/baselines/count-ratchets.sites.jsonl"

#: Where the rule looks.
RAILS = "tests/rails"

#: Where an imported helper module may sit, from the repository's root.
HELPERS = ("tests", RAILS)

#: The modules every name of which reads the tree.
READERS = frozenset({"source_index", "rails_repo"})

#: Names that read the tree wherever they stand.
TREE_NAMES = frozenset({"REPO", "__file__"})

_COUNTS = frozenset({"len", "sum"})

#: Calls that hold as many items as their argument, so they count what it counts.
_SAME_COUNT = frozenset({"sorted", "list", "tuple", "set", "frozenset", "reversed", "enumerate"})

#: Each operator that bounds the left side from above, and the same bound with the sides swapped.
_ABOVE = {ast.Lt: -1, ast.LtE: 0, ast.Eq: 0}
_ABOVE_SWAPPED = {ast.Gt: -1, ast.GtE: 0, ast.Eq: 0}

#: Calls that add to the container they are called on.
_GROWS = frozenset({"append", "appendleft", "add", "extend", "insert", "update", "setdefault"})

#: Of those, the ones that add every item of their argument rather than one.
_GROWS_BY_MANY = frozenset({"extend", "update"})

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DISPLAYS = (ast.List, ast.Tuple, ast.Set)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)


def _is_count(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _COUNTS
    )


def _int(node: ast.AST | None) -> int | None:
    if isinstance(node, ast.Constant) and type(node.value) is int:
        return node.value
    return None


def _bound(node: ast.AST) -> list[str]:
    """The names an assignment target binds."""
    return [name.id for name in ast.walk(node) if isinstance(name, ast.Name)]


def _bindings(scope: ast.AST, top_only: bool) -> list[tuple[ast.AST, list[str], bool]]:
    """Each binding in *scope*: the value, the names it binds, and whether the value is the
    names' own (an assignment) rather than what they walk (a ``for``, a ``with``). With
    *top_only*, only *scope*'s own statements (a module's top level)."""
    nodes = scope.body if top_only else list(ast.walk(scope))  # type: ignore[attr-defined]
    out: list[tuple[ast.AST, list[str], bool]] = []
    for node in nodes:
        if isinstance(node, ast.Assign):
            out.append((node.value, [n for t in node.targets for n in _bound(t)], True))
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            out.append((node.value, _bound(node.target), True))
        elif isinstance(node, ast.NamedExpr):
            out.append((node.value, _bound(node.target), True))
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
            out.append((node.iter, _bound(node.target), False))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            out += [
                (item.context_expr, _bound(item.optional_vars), False)
                for item in node.items
                if item.optional_vars is not None
            ]
    return out


def _growths(scope: ast.AST, top_only: bool) -> list[tuple[ast.AST, list[str], bool]]:
    """Each place *scope* adds to a container, as a binding of the container's name to a value
    that counts what the addition does: ``found.append(v)``, ``seen.add(v)`` or
    ``by_name[k] = v`` adds one each time it runs, so it counts what every loop around it walks
    and every ``if`` around it keeps, as a comprehension would; ``found.extend(v)``,
    ``by_name.update(v)`` or ``found += v`` adds what *v* counts, as many times. With *top_only*,
    not inside a function or class (a module's top level)."""
    out: list[tuple[ast.AST, list[str], bool]] = []

    def runs(around: tuple[tuple[str, ast.AST], ...]) -> ast.AST:
        """A comprehension that makes one item each time the code *around* lets it run."""
        loops: list[ast.comprehension] = []
        for kind, node in around:
            if kind == "for" or not loops:
                walked = node if kind == "for" else ast.List(elts=[ast.Constant(0)], ctx=ast.Load())
                loops.append(
                    ast.comprehension(
                        target=ast.Name("_", ast.Store()), iter=walked, ifs=[], is_async=0
                    )
                )
            if kind == "if":
                loops[-1].ifs.append(node)
        if not loops:
            return ast.List(elts=[ast.Constant(0)], ctx=ast.Load())
        return ast.ListComp(elt=ast.Constant(0), generators=loops)

    def grown(name: str, many: ast.AST | None, around: tuple[tuple[str, ast.AST], ...]) -> None:
        counted = [ast.Starred(runs(around), ast.Load())]
        if many is not None:
            counted.append(ast.Starred(many, ast.Load()))
        out.append((ast.Tuple(elts=counted, ctx=ast.Load()), [name], True))

    def visit(node: ast.AST, around: tuple[tuple[str, ast.AST], ...]) -> None:
        if top_only and isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            visit(node.iter, around)
            for child in [*node.body, *node.orelse]:
                visit(child, (*around, ("for", node.iter)))
            return
        if isinstance(node, (ast.While, ast.If)):
            visit(node.test, around)
            for child in [*node.body, *node.orelse]:
                visit(child, (*around, ("if", node.test)))
            return
        if isinstance(node, _COMPREHENSIONS):
            for walked in node.generators:
                around = (*around, ("for", walked.iter), *(("if", kept) for kept in walked.ifs))
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _GROWS
            and isinstance(node.func.value, ast.Name)
        ):
            many = node.args[0] if node.func.attr in _GROWS_BY_MANY and node.args else None
            grown(node.func.value.id, many, around)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    grown(target.value.id, None, around)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            grown(node.target.id, node.value, around)
        for child in ast.iter_child_nodes(node):
            visit(child, around)

    for child in ast.iter_child_nodes(scope):
        visit(child, ())
    return out


def _ints(bindings: list[tuple[ast.AST, list[str], bool]]) -> dict[str, int]:
    return {
        names[0]: value
        for node, names, own in bindings
        if own and len(names) == 1 and (value := _int(node)) is not None
    }


class _Module:
    """One module under ``tests/``: what it imports, what it binds, and which of its top-level
    names reach the tree."""

    def __init__(self, root: Path, path: Path) -> None:
        self.root = root
        self.tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        #: A bound name and the helper module it is (``import x``) or comes from (``from x``).
        self.modules: dict[str, str] = {}
        self.imported: dict[str, tuple[str, str]] = {}
        self.reaching: set[str] = set(TREE_NAMES)
        definitions: dict[str, list[ast.AST]] = {}
        for node in self.tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    bound = alias.asname or top
                    if top in READERS:
                        self.reaching.add(bound)
                    else:
                        self.modules[bound] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if node.module.split(".")[0] in READERS:
                        self.reaching.add(bound)
                    else:
                        self.imported[bound] = (node.module, alias.name)
            elif isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
                definitions.setdefault(node.name, []).append(node)
        top = _bindings(self.tree, top_only=True) + _growths(self.tree, top_only=True)
        for value, names, _ in top:
            for name in names:
                definitions.setdefault(name, []).append(value)
        self.ints = _ints(top)
        self.counts = {
            name for value, names, own in top if own and _is_count(value) for name in names
        }
        for bound, (module, name) in self.imported.items():
            helper = _helper(root, module)
            if helper is not None and name in helper.reaching:
                self.reaching.add(bound)
            if helper is not None and name in helper.ints:
                self.ints.setdefault(bound, helper.ints[name])
        changed = True
        while changed:
            changed = False
            for name, nodes in definitions.items():
                if name not in self.reaching and any(self.reaches(node) for node in nodes):
                    self.reaching.add(name)
                    changed = True
        #: The top-level names whose count reaches the tree: a name bound to a table written
        #: out counts its entries, whatever they read.
        self.counted = set(self.reaching)
        values: dict[str, list[tuple[ast.AST, bool]]] = {}
        for value, names, own in top:
            for name in names:
                values.setdefault(name, []).append((value, own))
        changed = True
        while changed:
            changed = False
            for name, bound in values.items():
                if name in self.counted and not any(
                    _counts_the_tree(self, value, set(), set(), set()) if own else True
                    for value, own in bound
                ):
                    self.counted.discard(name)
                    changed = True

    def tokens(self, node: ast.AST) -> Iterator[str]:
        """The names *node* refers to: a bare name, ``module.name`` for a name read off an
        imported helper module, and ``__file__`` for any module's file."""
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                yield child.id
            elif isinstance(child, ast.Attribute):
                if child.attr == "__file__":
                    yield "__file__"
                if isinstance(child.value, ast.Name) and child.value.id in self.modules:
                    yield f"{child.value.id}.{child.attr}"

    def reaches(self, node: ast.AST, local: set[str] | None = None) -> bool:
        reaching = self.reaching | (local or set())
        for token in self.tokens(node):
            if token in reaching:
                return True
            module, _, name = token.partition(".")
            if name and module in self.modules:
                helper = _helper(self.root, self.modules[module])
                if helper is not None and name in helper.reaching:
                    return True
        return False

    def helper_int(self, node: ast.AST) -> int | None:
        """The integer a ``helper.NAME`` read off an imported helper module is bound to."""
        if not (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in self.modules
        ):
            return None
        helper = _helper(self.root, self.modules[node.value.id])
        return None if helper is None else helper.ints.get(node.attr)


_BUSY: set[tuple[Path, str]] = set()


def _helper(root: Path, module: str) -> _Module | None:
    """The helper module *module* under *root*'s :data:`HELPERS`, if it is one; ``None`` for a
    module outside them, and for one already being read (an import cycle)."""
    relative = module.replace(".", "/") + ".py"
    path = next(
        (root / folder / relative for folder in HELPERS if (root / folder / relative).is_file()),
        None,
    )
    key = (root, module)
    if path is None or key in _BUSY:
        return None
    _BUSY.add(key)
    try:
        return _read(root, path)
    finally:
        _BUSY.discard(key)


@cache
def _read(root: Path, path: Path) -> _Module:
    return _Module(root, path)


def _counts_the_tree(
    module: _Module, node: ast.AST, local: set[str], counted: set[str], bound: set[str]
) -> bool:
    """Whether what *node* counts reaches the tree: *local* are the names the function binds to a
    value that reaches it, *counted* those whose count does, *bound* every name it binds."""

    def counts(child: ast.AST) -> bool:
        return _counts_the_tree(module, child, local, counted, bound)

    if isinstance(node, _DISPLAYS):
        return any(counts(e.value) for e in node.elts if isinstance(e, ast.Starred))
    if isinstance(node, ast.Dict):
        return any(counts(v) for k, v in zip(node.keys, node.values) if k is None)
    if isinstance(node, _COMPREHENSIONS):
        return any(
            counts(walked.iter) or any(module.reaches(kept, local) for kept in walked.ifs)
            for walked in node.generators
        )
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _COUNTS | _SAME_COUNT
        and node.args
    ):
        return counts(node.args[0])
    if isinstance(node, ast.Name):
        return node.id in (counted if node.id in bound else module.counted)
    return module.reaches(node, local)


class _Function:
    """What one function (or a module's top level) binds: the names whose value reaches the
    tree, the names whose count does, the names bound to a count, and those bound to an
    integer."""

    def __init__(self, module: _Module, scope: ast.AST) -> None:
        at_top = scope is module.tree
        bindings = _bindings(scope, top_only=False) + _growths(scope, top_only=False)
        self.bound = set() if at_top else {name for _, names, _ in bindings for name in names}
        self.counts = module.counts | {
            name for value, names, own in bindings if own and _is_count(value) for name in names
        }
        #: The module's named integers, and the function's own, which are literals with a label.
        self.named = {name: v for name, v in module.ints.items() if name not in self.bound}
        self.ints = {} if at_top else _ints(bindings)
        self.local: set[str] = set()
        self.counted: set[str] = set()
        changed = True
        while changed:
            changed = False
            for value, names, own in bindings:
                if not set(names) <= self.local and module.reaches(value, self.local):
                    self.local |= set(names)
                    changed = True
                counts = (
                    _counts_the_tree(module, value, self.local, self.counted, self.bound)
                    if own
                    else module.reaches(value, self.local)
                )
                if counts and not set(names) <= self.counted:
                    self.counted |= set(names)
                    changed = True


def _held(module: _Module, scope: _Function, compare: ast.Compare) -> bool:
    """Whether *compare*, or one link of its chain, holds a count to an integer from above that
    the rule makes a site."""
    sides = [compare.left, *compare.comparators]
    for left, op, right in zip(sides, compare.ops, sides[1:]):
        for count, number, bounds in ((left, right, _ABOVE), (right, left, _ABOVE_SWAPPED)):
            if type(op) not in bounds:
                continue
            if not (_is_count(count) or (isinstance(count, ast.Name) and count.id in scope.counts)):
                continue
            value = _int(number)
            if isinstance(number, ast.Name) and number.id in scope.ints:
                value = scope.ints[number.id]
            if value is not None:
                if value + bounds[type(op)] > 1 and _counts_the_tree(
                    module, count, scope.local, scope.counted, scope.bound
                ):
                    return True
            elif (isinstance(number, ast.Name) and number.id in scope.named) or (
                module.helper_int(number) is not None
            ):
                return True
    return False


def _asserts(tree: ast.AST, scope: str = "<module>", function: ast.AST | None = None):
    """Every ``assert`` under *tree* in source order, with its scope's qualified name and the
    innermost function it is in (``None`` at a module's or class's top)."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assert):
            yield node, scope, function
        if isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
            inner = node.name if scope == "<module>" else f"{scope}.{node.name}"
            yield from _asserts(node, inner, node if isinstance(node, _FUNCTIONS) else function)
        else:
            yield from _asserts(node, scope, function)


def module_sites(root: Path, rel: str) -> list[str]:
    """The sites in the module at *rel* under *root*, in source order."""
    module = _read(root, root / rel)
    names: list[str] = []
    scopes: dict[int, _Function] = {}
    for statement, scope, function in _asserts(module.tree):
        holder = function if function is not None else module.tree
        if id(holder) not in scopes:
            scopes[id(holder)] = _Function(module, holder)
        for compare in ast.walk(statement.test):
            if isinstance(compare, ast.Compare) and _held(module, scopes[id(holder)], compare):
                names.append(code_site(rel, scope, compare))
    return numbered(names)


def sites(root: Path) -> list[str]:
    """Every site under *root*'s ``tests/rails``, by path."""
    root = Path(root)
    found: list[str] = []
    for path in sorted((root / RAILS).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        found += module_sites(root, path.relative_to(root).as_posix())
    return found
