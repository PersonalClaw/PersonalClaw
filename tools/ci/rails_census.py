"""Which test modules are rails: they check the source tree rather than run the product.

A **rail** reads the repository itself — the package, ``web/src``, ``docs``, the workflows,
``tests/``, any file beside them — and judges what it finds. Rails live in ``tests/rails/`` and
run once per CI run, in the ``rails`` job's three legs (:data:`LEGS`, each rail in exactly
one, held by :func:`leg_violations`); the suite's shards never collect them
(``tools/ci/shards.py``). This module decides membership from each module's source, read with
``ast`` and never imported, so the rule is code and the ``rails`` row of the CI contract table
can hold the tree to it.

A test **reads the tree** when its own body, a module-level helper or fixture it reaches, or the
parameters its decorators compute, does one of these:

- reads the suite's source index (``tests/source_index.py``), the controller sources built on
  it (``tests/controller_sources.py``) or the catalogue's steps (``tests/ci_catalogue_steps.py``);
- names ``.github`` (a workflow, an action, Dependabot's settings);
- walks a path the module binds at its top level or derives from ``__file__`` (``rglob``,
  ``os.walk``, a ``**`` glob, recursive ``glob.glob``); a walk of a path the test was handed,
  ``tmp_path`` among them, reads a tree the test made, not the repository;
- reads a file at a path the module binds at its top level or derives from ``__file__``
  (``read_text``, ``read_bytes``, ``open``), or parses product source (``ast.parse`` of
  ``inspect.getsource``);
- calls a whole-tree check's entry point on the real tree, as ``tools/ci/tree_checks.py``
  defines it (a baseline generator with no tree named, the asset census, ``harness validate``):
  the check reads the tree for the test, though the test's own source opens no file.

A test **leaves the process** when it reaches a gateway, a browser, a container, a model
endpoint or the network: it calls ``urlopen``, ``requests``, ``httpx`` or ``aiohttp``'s client,
``socket.create_connection``, spawns ``docker`` or ``podman``, or imports one of the suite's
helpers that start such things (:data:`OUTSIDE_HELPERS`). A local child (``git``, a CLI parse,
a fresh interpreter) and an in-process ``TestServer``/``TestClient`` stay inside it.

A module is a rail when at least one of its tests reads the tree and none leaves the process.
A module with a test of each kind is **mixed**, and is split: its tree half lives in
``tests/rails/test_<name>_tree.py``, its behaviour half stays, and what both use lives in a helper
module beside the suite, so no rail imports a behaviour test module. A security-marked module
(``pytestmark = pytest.mark.security``) stays where it is whatever it reads: those tests run on
every shard and are never selected out.

A name a test imports from a helper module under ``tests/`` (any module there that is not a
test module) counts as it would in the helper: a path the helper derives from ``__file__`` is a
repository path, and a function or constant there that reads the tree reads it for the test.
Helpers are read with ``ast`` too (:func:`helper_facts`), never imported.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import sys
import tomllib
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import tree_checks

#: Where rails live. Nothing else under ``tests/`` may hold one.
RAILS = "tests/rails"

#: The names pytest collects as test modules when its configuration names none.
PYTEST_FILES: tuple[str, ...] = ("test_*.py", "*_test.py")

#: The suite's test helpers that read the tree for a rail: the source index, the controller
#: sources built on it, and the catalogue's steps (``tests/ci_catalogue_steps.py``), the CI
#: definition both of CI's shapes run.
TREE_HELPERS = frozenset({"source_index", "controller_sources", "ci_catalogue_steps"})

#: Helpers that only name the repository's root (``tests/rails/rails_repo.py``): a path built
#: from one is a repository path, but importing one reads nothing.
ROOT_HELPERS = frozenset({"rails_repo"})

#: The suite's test helpers that start something outside the process: a gateway, a browser, a
#: container runtime, a model endpoint. A module that imports one leaves the process.
OUTSIDE_HELPERS = frozenset(
    {"signed_in_gateway", "browse_chrome", "container_runtime", "loopback_model_endpoint"}
)

#: Programs a test spawns that leave the process (a container runtime starts a daemon's work).
OUTSIDE_PROGRAMS = frozenset({"docker", "podman", "finch", "nerdctl"})

_WALKS = frozenset({"rglob", "walk"})
_READS = frozenset({"read_text", "read_bytes", "open"})
_NETWORK_CALLS = frozenset({"urlopen", "create_connection", "ClientSession"})
_NETWORK_MODULES = frozenset({"requests", "httpx"})
_SPAWNS = frozenset(
    {"run", "Popen", "check_output", "check_call", "call", "create_subprocess_exec"}
)


@dataclass
class Module:
    """One test module's verdict, with the reasons behind it."""

    path: str
    #: ``rail``, ``mixed``, ``suite`` (reads no tree, or every tree reader leaves the process
    #: too) or ``security`` (marked: stays put, whatever it reads).
    kind: str = "suite"
    #: Test names that read the tree, each with the first reason found.
    tree: dict[str, str] = field(default_factory=dict)
    #: Test names that leave the process, each with the first reason found.
    outside: dict[str, str] = field(default_factory=dict)
    tests: int = 0

    def line(self) -> str:
        """One line for the census report: the verdict and the reason a reader would check."""
        if self.kind == "rail":
            name, why = next(iter(self.tree.items()))
            return f"rail      {self.path}  ({len(self.tree)}/{self.tests} read the tree; {why})"
        if self.kind == "mixed":
            name, why = next(iter(self.outside.items()))
            return (
                f"mixed     {self.path}  ({len(self.tree)} read the tree, {len(self.outside)} "
                f"leave the process: {name}, {why})"
            )
        if self.kind == "security":
            why = next(iter(self.tree.values()), "")
            return f"security  {self.path}  (marked; stays put{'; ' + why if why else ''})"
        return f"suite     {self.path}"


# ── one module ──────────────────────────────────────────────────────────────────────────────


def _is_security_marked(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Attribute) and sub.attr == "security":
                    return True
    return False


class _Module:
    """The module's top-level bindings, what each function references, and its imports."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        self.classes: dict[str, ast.ClassDef] = {}
        #: Names bound at the top level to something other than a function or a class.
        self.constants: set[str] = set()
        #: Local names that stand for a tree helper or an outside helper, by what they are.
        self.aliases: dict[str, str] = {}
        #: Local names bound by ``from m import name``: their module's leaf and original name.
        self.imported: dict[str, tuple[str, str]] = {}
        #: Local names that stand for a helper module's repository path, or its tree reader
        #: (with the reason it reads), filled by :meth:`know` from :func:`helper_facts`.
        self.helper_paths: set[str] = set()
        self.helper_readers: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[node.name] = node
            elif isinstance(node, ast.ClassDef):
                self.classes[node.name] = node
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for sub in ast.walk(target):
                        if isinstance(sub, ast.Name):
                            self.constants.add(sub.id)
        for each in ast.walk(tree):
            if isinstance(each, ast.Import):
                for alias in each.names:
                    leaf = alias.name.rsplit(".", 1)[-1]
                    self.aliases[alias.asname or leaf] = leaf
            elif isinstance(each, ast.ImportFrom) and each.module:
                leaf = each.module.rsplit(".", 1)[-1]
                for alias in each.names:
                    self.aliases[alias.asname or alias.name] = leaf
                    self.imported[alias.asname or alias.name] = (leaf, alias.name)

    def know(self, facts: Mapping[str, HelperFacts]) -> None:
        """Read the names this module imports from a helper the way the helper uses them."""
        for local, (leaf, name) in self.imported.items():
            found = facts.get(leaf)
            if found is None:
                continue
            if name in found.paths:
                self.helper_paths.add(local)
            if name in found.readers:
                self.helper_readers[local] = f"{leaf}.{name}: {found.readers[name]}"

    def helper(self, name: str) -> str | None:
        """The tree or outside helper a local name stands for, if any."""
        leaf = self.aliases.get(name)
        return leaf if leaf in TREE_HELPERS | OUTSIDE_HELPERS else None


def _call_name(call: ast.Call) -> tuple[str, str]:
    """``(owner, name)`` for ``owner.name(...)``, ``("", name)`` for ``name(...)``."""
    func = call.func
    if isinstance(func, ast.Attribute):
        owner = func.value
        while isinstance(owner, ast.Attribute):
            owner = owner.value
        return (owner.id if isinstance(owner, ast.Name) else "", func.attr)
    return ("", func.id) if isinstance(func, ast.Name) else ("", "")


def _repository_path(expr: ast.AST, module: _Module, params: set[str]) -> bool:
    """Whether *expr* names a path the module binds at its top level or derives from
    ``__file__``, rather than one the function was handed."""
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Name):
            if sub.id == "__file__":
                return True
            if sub.id in params:
                return False
            if sub.id in module.constants or module.helper(sub.id) in TREE_HELPERS:
                return True
            if module.aliases.get(sub.id) in ROOT_HELPERS or sub.id in module.helper_paths:
                return True
        if isinstance(sub, ast.Attribute) and sub.attr == "__file__":
            return True
    return False


def _own_reasons(node: ast.AST, module: _Module, params: set[str]) -> tuple[str | None, str | None]:
    """``(tree reason, outside reason)`` from *node*'s own source, not what it calls."""
    tree_why: str | None = None
    outside_why: str | None = None
    docstrings = {
        id(each.body[0].value)
        for each in ast.walk(node)
        if isinstance(each, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and each.body
        and isinstance(each.body[0], ast.Expr)
        and isinstance(each.body[0].value, ast.Constant)
    }
    for sub in ast.walk(node):
        if id(sub) in docstrings:
            continue  # prose about a path reads nothing
        if isinstance(sub, ast.Name) and sub.id in module.helper_readers:
            tree_why = tree_why or f"reads {module.helper_readers[sub.id]}"
        elif isinstance(sub, ast.Name) and module.helper(sub.id):
            helper = module.helper(sub.id)
            if helper in TREE_HELPERS:
                tree_why = tree_why or f"reads {helper}"
            elif helper in OUTSIDE_HELPERS:
                outside_why = outside_why or f"uses {helper}"
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if ".github" in sub.value.split("/") or sub.value.startswith(".github"):
                tree_why = tree_why or "reads .github"
        elif isinstance(sub, ast.Call):
            owner, name = _call_name(sub)
            func = sub.func
            receiver = func.value if isinstance(func, ast.Attribute) else None
            if name in _WALKS and receiver is not None:
                start = receiver if name == "rglob" else (sub.args[0] if sub.args else None)
                if name == "walk" and owner != "os":
                    start = None
                if start is not None and _repository_path(start, module, params):
                    tree_why = tree_why or f"walks the repository ({name})"
            elif name in ("glob", "iglob") and receiver is not None and sub.args:
                pattern = sub.args[0]
                recursive = any(k.arg == "recursive" for k in sub.keywords)
                deep = isinstance(pattern, ast.Constant) and "**" in str(pattern.value)
                start = pattern if owner == "glob" else receiver
                if (recursive or deep) and _repository_path(start, module, params):
                    tree_why = tree_why or "walks the repository (glob)"
            elif name == "parse" and owner == "ast" and sub.args:
                # A parse of source the test read from the repository, or of a live module's
                # source; a parse of a string the test wrote reads no tree.
                for inner in ast.walk(sub.args[0]):
                    if not isinstance(inner, ast.Call):
                        continue
                    inner_owner, inner_name = _call_name(inner)
                    inner_func = inner.func
                    inner_receiver = (
                        inner_func.value if isinstance(inner_func, ast.Attribute) else None
                    )
                    if inner_name == "getsource":
                        tree_why = tree_why or "parses product source"
                    elif (
                        inner_name in _READS
                        and inner_receiver is not None
                        and _repository_path(inner_receiver, module, params)
                    ) or (
                        inner_name == "open"
                        and inner_owner == ""
                        and inner.args
                        and _repository_path(inner.args[0], module, params)
                    ):
                        tree_why = tree_why or "parses a repository file"
            if name in _NETWORK_CALLS or owner in _NETWORK_MODULES:
                outside_why = outside_why or f"calls {owner + '.' if owner else ''}{name}"
            if name in _SPAWNS and owner in ("subprocess", "asyncio") and sub.args:
                argv = sub.args[0]
                if (
                    isinstance(argv, (ast.List, ast.Tuple))
                    and argv.elts
                    and isinstance(argv.elts[0], ast.Constant)
                    and str(argv.elts[0].value) in OUTSIDE_PROGRAMS
                ):
                    outside_why = outside_why or f"spawns {str(argv.elts[0].value)}"
    return tree_why, outside_why


def _params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = node.args
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    every += [a for a in (args.vararg, args.kwarg) if a is not None]
    return {a.arg for a in every}


class _Reach:
    """What each function reaches: its own reasons, then those of every module-level function,
    fixture and class it names, and (for a method) of the methods of its class it calls."""

    def __init__(self, module: _Module) -> None:
        self.module = module
        self.memo: dict[int, tuple[str | None, str | None]] = {}

    def of(self, node: ast.AST, cls: ast.ClassDef | None = None) -> tuple[str | None, str | None]:
        key = id(node)
        if key in self.memo:
            return self.memo[key]
        self.memo[key] = (None, None)  # a cycle reaches nothing more
        params = (
            _params(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else set()
        )
        tree_why, outside_why = _own_reasons(node, self.module, params)
        for name in self._named(node, cls, params):
            target, owner = self._target(name, cls)
            if target is None:
                continue
            t, o = self.of(target, owner)
            tree_why, outside_why = tree_why or t, outside_why or o
        self.memo[key] = (tree_why, outside_why)
        return tree_why, outside_why

    def _named(self, node: ast.AST, cls: ast.ClassDef | None, params: set[str]) -> Iterator[str]:
        """Names *node* reaches: names it loads, fixtures it asks for, ``self.x`` methods."""
        seen: set[str] = set()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                for sub in ast.walk(decorator):
                    if isinstance(sub, ast.Name) and sub.id not in seen:
                        seen.add(sub.id)
                        yield sub.id
            for name in sorted(params):
                if name not in seen:
                    seen.add(name)
                    yield name
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id not in seen:
                seen.add(sub.id)
                yield sub.id
            elif (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.value, ast.Name)
                and sub.value.id in ("self", "cls")
                and f"self.{sub.attr}" not in seen
            ):
                seen.add(f"self.{sub.attr}")
                yield f"self.{sub.attr}"

    def _target(
        self, name: str, cls: ast.ClassDef | None
    ) -> tuple[ast.AST | None, ast.ClassDef | None]:
        if name.startswith("self."):
            if cls is None:
                return None, None
            attr = name[5:]
            for item in cls.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == attr:
                    return item, cls
            return None, None
        if name in self.module.functions:
            return self.module.functions[name], None
        if name in self.module.classes:
            return self.module.classes[name], self.module.classes[name]
        if name in self.module.constants:
            for node in self.module.tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(
                        isinstance(s, ast.Name) and s.id == name
                        for t in targets
                        for s in ast.walk(t)
                    ):
                        return node, None
        return None, None


def _tests(
    tree: ast.Module,
) -> Iterator[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, ast.ClassDef | None]]:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            yield node.name, node, None
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(
                    item, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and item.name.startswith("test"):
                    yield f"{node.name}.{item.name}", item, node


def classify(rel: str, source: str, helpers: Mapping[str, HelperFacts] | None = None) -> Module:
    """The verdict on one test module, from its source, reading what it imports from the
    helpers in *helpers* (:func:`helper_facts`) as those helpers use it."""
    tree = ast.parse(source, filename=rel)
    module = _Module(tree)
    module.know(helpers or {})
    reach = _Reach(module)
    found = Module(path=rel)
    class_level: dict[int, tuple[str | None, str | None]] = {}
    for name, node, cls in _tests(tree):
        found.tests += 1
        t, o = reach.of(node, cls)
        if cls is not None:
            # A class's own fixtures and attributes serve all its tests.
            if id(cls) not in class_level:
                own = [
                    item
                    for item in cls.body
                    if not (
                        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and item.name.startswith("test")
                    )
                ]
                ct: str | None = None
                co: str | None = None
                for item in own:
                    it, io = reach.of(item, cls)
                    ct, co = ct or it, co or io
                class_level[id(cls)] = (ct, co)
            ct, co = class_level[id(cls)]
            t, o = t or ct, o or co
        if t:
            found.tree[name] = t
        if o:
            found.outside[name] = o
    # A test that calls a whole-tree check's entry point on the real tree reads the tree through
    # the check, though its own source opens no file (tools/ci/tree_checks.py).
    for check, node_id in sorted(tree_checks.module_copies(rel, source)):
        name = node_id.split("::", 1)[1].replace("::", ".")
        found.tree.setdefault(name, f"runs {check} on the real tree")
    if _is_security_marked(tree):
        found.kind = "security"
    elif found.tree and not found.outside:
        found.kind = "rail"
    elif found.tree and set(found.outside) != set(found.tree) | set(found.outside):
        # Some tests leave the process and some read the tree without leaving: two halves.
        tree_only = set(found.tree) - set(found.outside)
        found.kind = "mixed" if tree_only else "suite"
    else:
        found.kind = "suite"
    return found


# ── the tree ────────────────────────────────────────────────────────────────────────────────


def python_files(root: Path) -> tuple[str, ...]:
    """The file names pytest collects as test modules under ``root``: ``python_files`` in
    ``root/pyproject.toml``, read as ``tools/ci/shards.py`` reads it, or pytest's defaults
    (:data:`PYTEST_FILES`) when the file or the setting is absent. A setting that is not a list
    names nothing, so the census finds no module and fails rather than reading too few."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return PYTEST_FILES
    options: object = tomllib.loads(path.read_text(encoding="utf-8"))
    for key in ("tool", "pytest", "ini_options"):
        options = options.get(key, {}) if isinstance(options, Mapping) else {}
    named = options.get("python_files", PYTEST_FILES) if isinstance(options, Mapping) else ()
    return tuple(str(p) for p in named) if isinstance(named, (list, tuple)) else ()


def _is_test_module(name: str, patterns: Iterable[str]) -> bool:
    return name.endswith(".py") and any(fnmatch.fnmatch(name, p) for p in patterns)


def test_modules(root: Path) -> Iterator[Path]:
    """Every test module under ``root/tests``, in path order: each ``.py`` file whose name
    pytest collects (:func:`python_files`), so ``foo_test.py`` is one as ``test_foo.py`` is, and
    ``--shard-list`` and this census agree on what a leg must list. Only the parts below
    ``root`` are filtered, so a checkout that sits inside a dot-folder is still read whole."""
    patterns = python_files(root)
    for path in sorted((root / "tests").rglob("*.py")):
        parts = path.relative_to(root).parts
        if "__pycache__" in parts or any(p.startswith(".") for p in parts):
            continue
        if _is_test_module(path.name, patterns):
            yield path


@dataclass(frozen=True)
class HelperFacts:
    """What one helper module's top-level names are, for the tests that import them."""

    #: Names bound to a path the helper derives from ``__file__`` or another such path.
    paths: frozenset[str]
    #: Names whose code reads the tree, each with the first reason found.
    readers: Mapping[str, str]


def _facts_of(source: str, facts: Mapping[str, HelperFacts]) -> HelperFacts:
    tree = ast.parse(source)
    module = _Module(tree)
    module.know(facts)
    reach = _Reach(module)
    paths: set[str] = set()
    readers: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {s.id for t in targets for s in ast.walk(t) if isinstance(s, ast.Name)}
            if _repository_path(node.value, module, set()):
                paths |= names
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = {node.name}
        else:
            continue
        why, _outside = reach.of(node)
        if why:
            readers.update(dict.fromkeys(names, why))
    return HelperFacts(frozenset(paths), readers)


def helper_facts(root: Path) -> dict[str, HelperFacts]:
    """For each helper module under ``root/tests`` (a ``.py`` file that is neither a test module
    (:func:`test_modules`) nor a ``conftest.py``), by module name, what its names are. A helper
    that imports another is read in the light of that one, so a chain of helpers resolves."""
    patterns = python_files(root)
    sources = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted((root / "tests").rglob("*.py"))
        if "__pycache__" not in path.parts
        and not _is_test_module(path.name, patterns)
        and path.name != "conftest.py"
    }
    facts: dict[str, HelperFacts] = {}
    for _ in range(4):
        known = {stem: _facts_of(source, facts) for stem, source in sources.items()}
        if known == facts:
            break
        facts = known
    return facts


def census(root: Path) -> list[Module]:
    """The verdict on every test module under ``root/tests``."""
    facts = helper_facts(root)
    return [
        classify(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"), facts)
        for path in test_modules(root)
    ]


def misplaced(modules: Iterable[Module]) -> list[str]:
    """Rails outside :data:`RAILS`, mixed modules anywhere, and modules under :data:`RAILS`
    that are not rails, each as one line naming the module and why."""
    bad: list[str] = []
    for found in modules:
        inside = found.path.startswith(RAILS + "/")
        if found.kind == "rail" and not inside:
            bad.append(f"{found.path} is a rail outside {RAILS}/: {found.line().split('(', 1)[1]}")
        elif found.kind == "mixed":
            bad.append(f"{found.path} is mixed; split it: {found.line().split('(', 1)[1]}")
        elif inside and found.kind != "rail":
            bad.append(f"{found.path} is under {RAILS}/ but is not a rail ({found.kind})")
    return bad


def violations(root: Path) -> list[str]:
    """Everything wrong with ``root``'s rails: :func:`misplaced` over its census. A census
    with no test modules checked nothing, so it is a violation, never a pass."""
    modules = census(root)
    if not modules:
        return [f"{root / 'tests'} holds no test modules, so the census checked nothing"]
    return misplaced(modules)


#: The rails job's legs: each names the rail modules one leg runs, one per line, and
#: the catalogue's rails entry runs leg ``i`` as its shard ``i``, with ``--shard-list
#: tools/ci/rails-leg-<i>.txt``.
LEGS: tuple[str, ...] = tuple(f"tools/ci/rails-leg-{i}.txt" for i in (1, 2, 3))


def leg_violations(root: Path) -> list[str]:
    """Everything wrong with the rails job's legs: a list that is missing or names nothing,
    a rail module in no leg or in two, and a listed file that is not a test module under
    :data:`RAILS`. Together the legs are exactly the rail modules, each once."""
    rails = {
        rel
        for rel in (path.relative_to(root).as_posix() for path in test_modules(root))
        if rel.startswith(RAILS + "/")
    }
    bad: list[str] = []
    seen: dict[str, list[str]] = {}
    for leg in LEGS:
        try:
            lines = (root / leg).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            bad.append(f"{leg} cannot be read ({type(exc).__name__}), so its leg runs nothing")
            continue
        listed = [line.strip() for line in lines if line.strip()]
        if not listed:
            bad.append(f"{leg} names no file, so its leg runs nothing")
        for rel in listed:
            seen.setdefault(rel, []).append(leg)
    for rel in sorted(rails - set(seen)):
        bad.append(f"{rel} is in no leg of the rails job ({', '.join(LEGS)})")
    for rel, legs in sorted(seen.items()):
        if len(legs) > 1:
            bad.append(f"{rel} is listed {len(legs)} times ({', '.join(legs)}): it runs once")
        if rel not in rails:
            bad.append(f"{rel} is listed in {legs[0]} and is no test module under {RAILS}/")
    return bad


def main(argv: list[str] | None = None) -> int:
    given = list(sys.argv[1:] if argv is None else argv)
    if given[:1] == ["select"]:
        # The rails a change can break, from what each rail read (tools/ci/rails_select.py).
        from . import rails_select

        return rails_select.main(given[1:])
    parser = argparse.ArgumentParser(prog="python -m tools.ci rails")
    parser.add_argument("command", choices=["census", "check", "select"])
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true", help="the census as JSON")
    args = parser.parse_args(given)
    modules = census(Path(args.root))
    if args.command == "census":
        if args.json:
            print(json.dumps([m.__dict__ for m in modules], indent=1))
        else:
            for found in modules:
                if found.kind != "suite":
                    print(found.line())
        return 0
    bad = violations(Path(args.root)) + leg_violations(Path(args.root))
    for line in bad:
        print(line, file=sys.stderr)
    return 1 if bad else 0
