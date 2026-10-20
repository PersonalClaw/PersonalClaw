"""The host-capability fingerprint: which of the suite's optional tools this machine has, and which
of its skip switches are set, so a pass recorded with a tool never stands for a run without it.

:func:`fingerprint` gives the mapping a caller puts in ``verdict_store.KeyInputs.capabilities``:

- ``probe:<name>`` for each tool the suite probes: ``absent``, or ``<resolved path>@sha256:<digest
  of its bytes>``. A tool is found with ``shutil.which`` on the ``path`` the caller gives, never
  the caller's own ``PATH``, and its identity is read from the file. Nothing is started: no
  ``--version``, which inside pytest the container guard refuses and outside it may wake a VM.
- ``chrome``: the browser the browse proofs would use, found as ``tests/browse_chrome.py`` finds
  it (its override, then each Playwright root, the headless shell first, the newest revision
  first), from that file's own constants, read without importing it, a ``~`` in a root expanded
  against the home given. ``absent`` or its identity.
- ``switch:<NAME>`` for each of :data:`SWITCHES`: ``unset``, or ``set:<value>``. They change
  what the suite runs, so they are inputs.

**The probe list** is read from the suite, never kept by hand: :func:`scan` parses the
``tests/**/*.py`` files (it imports none) and reads every reference to ``shutil.which`` (through
``import shutil`` under any name, ``from shutil import which`` under any name, or another
module's ``shutil``), and every reference to the container runtime's :data:`CONTAINER_ASKS`.
Each call names its tools, whatever ``PATH`` it searches: a ``PATH`` a test builds may still
hold the host's own folders, and the scan cannot tell one from the other. A tool is named by:

- a string literal;
- a loop variable over a literal collection of strings, or over a module constant that is one;
- a parameter of the function it sits in, when every call of that function is one its own module
  makes by name with a string literal for that parameter (so ``_tool_or_skip("dash")`` names
  ``dash``), and no other module names the function.

A module constant is a name the module binds once, anywhere in it, to a tuple or frozenset of
strings: a name bound again (``+=``, a second assignment, a loop, a parameter, a function's own
local), a list or a set, which any statement may change in place, and every name of a module
with a star import are not constants. Anything else is an unresolved site, and so is
``shutil.which`` taken as a value (``w = shutil.which``, ``map(shutil.which, ...)``), which is
called later with any tool. :data:`COVERED_SITES` names the unresolved sites whose tools a
constant lists: ``tests/container_runtime.py``'s lookups, which its ``PROGRAMS`` cover. Any
other unresolved site, a file that does not parse, or a constant that is missing or not all
text is :class:`Refused`, so a caller never keys a pass on a list it read short. The rail
``tests/rails/test_ci_capabilities_tree.py`` holds the scan to this repository's tree. Only files
whose text names a word of :data:`PREFILTER` are parsed, since every form read spells one; a file
that does not parse and names neither is left to pytest's own collection to fail.

**What it does not catch.** The fingerprint is read once, before the jobs run. A tool replaced
between that reading and a job's run is not seen here; holding the two together belongs to the
wiring that keys a run on this mapping, not to this module. Nor does the scan follow a ``which``
reached any other way than the forms above (through ``getattr`` or ``importlib``), a constant
another module rebinds (``monkeypatch.setattr(module, "TOOLS", ...)``), or product code's own
lookups, which a test reaches through the product.

Standard library only, and no relative import, so a trusted job can run it on the runner image's
own ``python3``.
"""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import sys
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

#: A tool that is not on the path, a browser that is not found.
ABSENT = "absent"

#: A switch that is not in the environment.
UNSET = "unset"

#: The suite's skip switches. Each turns a skip into a run or a failure, or points a proof at a
#: binary, so what runs depends on it.
SWITCHES: tuple[str, ...] = (
    "PERSONALCLAW_TEST_CONTAINER_RUNTIME",
    "PERSONALCLAW_REQUIRE_INSTALL_PROOF",
    "PERSONALCLAW_REQUIRE_BROWSE_PROOF",
    "PERSONALCLAW_REQUIRE_NPM_TREE",
    "PERSONALCLAW_TEST_CHROME",
)

#: The browse proofs' override, one of :data:`SWITCHES`. ``tests/browse_chrome.py`` must name it.
CHROME_SWITCH = "PERSONALCLAW_TEST_CHROME"

#: The container runtime's programs, and its lookups the scan cannot name by themselves: the
#: lookup over the candidates a test hands it, each read at the test's own call
#: (:data:`CONTAINER_ASKS`), and its guard's lookup of a program a process starts, which it makes
#: only for one of its programs. Both search the host's ``PATH``.
CONTAINER_MODULE = "tests/container_runtime.py"
CONTAINER_PROGRAMS = "PROGRAMS"
COVERED_SITES: Mapping[tuple[str, str], str] = {
    (CONTAINER_MODULE, "_on_path"): CONTAINER_PROGRAMS,
    (CONTAINER_MODULE, "refusal"): CONTAINER_PROGRAMS,
}

#: The container runtime's functions that look a test's candidates up on the host's ``PATH``, and
#: how each takes them: one tool per argument, or one sequence of them.
CONTAINER_ASKS: Mapping[str, str] = {
    "require": "each",
    "available": "each",
    "skip_reason": "sequence",
}

#: The browse proofs' lookup, and the constants it is read from.
CHROME_MODULE = "tests/browse_chrome.py"
_CHROME_CONSTANTS = ("CHROME_ENV", "_REVISION_DIR_PREFIXES", "_BINARY_GLOBS")

_MODULE = "<module>"
_COLLECTIONS = frozenset({"frozenset", "set", "tuple", "list"})
_FIXED = frozenset({"frozenset", "tuple"})
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)

#: The probes the scan reads, by module and function: what each takes its tools as.
_PROBES: Mapping[str, Mapping[str, str]] = {
    "shutil": {"which": "which"},
    Path(CONTAINER_MODULE).stem: CONTAINER_ASKS,
}


class Refused(ValueError):
    """The suite's probes cannot be read whole. The message says where."""


@dataclass
class Scan:
    """What the scan found: the tools named, and the sites it could not name, by file and
    function (``<module>`` at a module's top level)."""

    probes: set[str] = field(default_factory=set)
    unresolved: set[tuple[str, str]] = field(default_factory=set)


# ── reading the suite ────────────────────────────────────────────────────────────────────────


def _parse(path: Path, rel: str) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    except (SyntaxError, ValueError, UnicodeDecodeError, OSError) as exc:
        raise Refused(f"{rel} cannot be read as Python: {exc}") from None


def _strings(node: ast.expr | None) -> list[str] | None:
    """The strings a literal collection holds, or None when it is not one of text only."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _COLLECTIONS
        and len(node.args) == 1
        and not node.keywords
    ):
        node = node.args[0]
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    out = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return out if len(out) == len(node.elts) else None


def _bindings(tree: ast.Module) -> Counter[str]:
    """How many times each name is bound anywhere in the module, in any scope; ``*`` counts the
    star imports, which may bind any name."""
    bound: Counter[str] = Counter()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound[node.id] += 1
        elif isinstance(node, ast.arg):
            bound[node.arg] += 1
        elif isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
            bound[node.name] += 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.update(node.names)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name:
            bound[node.name] += 1
        elif isinstance(node, ast.MatchMapping) and node.rest:
            bound[node.rest] += 1
    return bound


def _fixed(value: ast.expr) -> bool:
    """Whether *value* is one no statement can change in place: a literal, a tuple, a frozenset."""
    if isinstance(value, (ast.Constant, ast.Tuple)):
        return True
    return (
        isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in _FIXED
    )


def _constants(tree: ast.Module) -> dict[str, ast.expr]:
    """Each module-level name the module binds once, anywhere in it, to a value no statement can
    change in place, and that value. A name bound again, or one a star import may bind, is none:
    what a loop over it reads may not be this value. Nor is a list, a set or a call's result."""
    bound = _bindings(tree)
    if bound["*"]:
        return {}
    out: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and bound[target.id] == 1 and _fixed(value):
            out[target.id] = value
    return out


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _enclosing(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> Iterator[ast.AST]:
    while node in parents:
        node = parents[node]
        yield node


def _function(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> ast.AST | None:
    for up in _enclosing(node, parents):
        if isinstance(up, (*_FUNCTIONS, ast.Lambda)):
            return up
    return None


def _site(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> str:
    fn = _function(node, parents)
    if isinstance(fn, _FUNCTIONS):
        return fn.name
    return "<lambda>" if fn is not None else _MODULE


@dataclass
class _Module:
    """One parsed module of the suite, and what the scan reads from it."""

    rel: str
    tree: ast.Module
    parents: dict[ast.AST, ast.AST]
    constants: dict[str, ast.expr]
    #: The functions whose parameter named a tool, from this module's own calls of them.
    helpers: set[str] = field(default_factory=set)


def _imports(tree: ast.Module) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """The names each module of :data:`_PROBES` is imported under, and the names each of its
    probes is (a star import binds them all): ``{alias: module}`` and ``{alias: (module,
    function)}``."""
    modules: dict[str, str] = {}
    functions: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in _PROBES:
                    modules[a.asname or a.name] = a.name
                elif a.asname and a.name.rsplit(".", 1)[-1] in _PROBES:
                    modules[a.asname] = a.name.rsplit(".", 1)[-1]
        elif isinstance(node, ast.ImportFrom):
            source = (node.module or "").rsplit(".", 1)[-1]
            for a in node.names:
                if source in _PROBES and a.name == "*":
                    functions.update({name: (source, name) for name in _PROBES[source]})
                elif source in _PROBES and a.name in _PROBES[source]:
                    functions[a.asname or a.name] = (source, a.name)
                elif a.name in _PROBES:
                    modules[a.asname or a.name] = a.name
    return modules, functions


def _probe(
    node: ast.AST, modules: Mapping[str, str], functions: Mapping[str, tuple[str, str]]
) -> str | None:
    """How the probe *node* names takes its tools, or None when it names no probe: a module's
    probe through the name it is imported under, through another module's attribute of that name
    (``cli.shutil.which``), or a probe imported by name."""
    if isinstance(node, ast.Attribute):
        value = node.value
        if isinstance(value, ast.Name):
            module = modules.get(value.id)
        elif isinstance(value, ast.Attribute):
            module = value.attr
        else:
            return None
        return _PROBES.get(module or "", {}).get(node.attr)
    if isinstance(node, ast.Name) and node.id in functions:
        module, name = functions[node.id]
        return _PROBES[module][name]
    return None


def _calls_of(mod: _Module, fn: ast.FunctionDef | ast.AsyncFunctionDef, param: str):
    """The value each call of *fn* in the module gives *param*, or None for a call that gives it
    none it can read; None in place of the list when the module never calls it, or reaches it
    other than by a call by name (taken as a value, or through an object or a module), since
    then it may be called with anything."""
    params = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args)]
    index = params.index(param) if param in params else None
    values: list[ast.expr | None] = []
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Attribute) and node.attr == fn.name:
            return None
        if not (isinstance(node, ast.Name) and node.id == fn.name):
            continue
        call = mod.parents.get(node)
        if not (isinstance(call, ast.Call) and call.func is node):
            return None
        given = next((k.value for k in call.keywords if k.arg == param), None)
        if given is None and index is not None and index < len(call.args):
            given = call.args[index]
        values.append(given)
    return values or None


def _resolve(arg: ast.expr | None, call: ast.Call, mod: _Module) -> list[str] | None:
    """The tools *arg* names, or None when the scan cannot say."""
    if isinstance(arg, ast.Constant):
        return [arg.value] if isinstance(arg.value, str) else None
    if not isinstance(arg, ast.Name):
        return None
    for up in _enclosing(call, mod.parents):
        loops: list[tuple[ast.expr, ast.expr]] = []
        if isinstance(up, (ast.For, ast.AsyncFor)):
            loops = [(up.target, up.iter)]
        elif isinstance(up, (ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.DictComp)):
            loops = [(g.target, g.iter) for g in up.generators]
        for target, source in loops:
            if isinstance(target, ast.Name) and target.id == arg.id:
                return _collection(source, mod)
        if isinstance(up, _FUNCTIONS):
            names = {a.arg for a in (*up.args.posonlyargs, *up.args.args, *up.args.kwonlyargs)}
            if arg.id not in names:
                return None
            values = _calls_of(mod, up, arg.id)
            if values is None:
                return None
            found = [v.value for v in values if isinstance(v, ast.Constant)]
            if len(found) != len(values) or not all(isinstance(v, str) for v in found):
                return None
            mod.helpers.add(up.name)
            return [str(v) for v in found]
        if isinstance(up, ast.Lambda):
            return None
    return None


def _collection(source: ast.expr | None, mod: _Module) -> list[str] | None:
    """The tools a collection names: a literal one, or a module constant that is one."""
    if isinstance(source, ast.Name) and source.id in mod.constants:
        return _strings(mod.constants[source.id])
    return _strings(source)


def _tools(call: ast.Call, takes: str, mod: _Module) -> list[list[str] | None]:
    """What each tool *call* is given names, by how its probe takes them."""
    if takes == "which":
        arg = (
            call.args[0]
            if call.args
            else next((k.value for k in call.keywords if k.arg == "cmd"), None)
        )
        return [_resolve(arg, call, mod)]
    if takes == "sequence":
        arg = (
            call.args[0]
            if call.args
            else next((k.value for k in call.keywords if k.arg == "candidates"), None)
        )
        return [_collection(arg, mod)]
    if call.keywords:
        return [None]
    return [
        _collection(a.value, mod) if isinstance(a, ast.Starred) else _resolve(a, call, mod)
        for a in call.args
    ]


def _read_module(rel: str, tree: ast.Module, found: Scan) -> _Module | None:
    """Every probe in one module into *found*; the module, when it holds one."""
    modules, functions = _imports(tree)
    refs = [
        (node, takes)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Attribute, ast.Name))
        and isinstance(node.ctx, ast.Load)
        and (takes := _probe(node, modules, functions)) is not None
    ]
    if not refs:
        return None
    parents = _parents(tree)
    mod = _Module(rel, tree, parents, _constants(tree))
    for node, takes in refs:
        call = parents.get(node)
        if not (isinstance(call, ast.Call) and call.func is node):
            # Taken as a value, so called later with any tool.
            found.unresolved.add((rel, _site(node, parents)))
            continue
        for names in _tools(call, takes, mod):
            if names is None:
                found.unresolved.add((rel, _site(call, parents)))
            else:
                found.probes.update(names)
    return mod


def _named_elsewhere(name: str, rel: str, texts: Mapping[str, str]) -> bool:
    """Whether a module other than *rel* names *name*: imports it, calls it or reaches it as an
    attribute. A module that names it in its text and does not parse is taken to."""
    for other, text in texts.items():
        if other == rel or name not in text:
            continue
        try:
            tree = ast.parse(text, filename=other)
        except (SyntaxError, ValueError):
            return True
        for node in ast.walk(tree):
            if (
                (isinstance(node, ast.Name) and node.id == name)
                or (isinstance(node, ast.Attribute) and node.attr == name)
                or (isinstance(node, ast.alias) and name in (node.name, node.asname))
            ):
                return True
    return False


#: Every form the scan reads (``import shutil`` under any name, ``from shutil import which``
#: under any name, ``<module>.shutil.which``, and the same of the container runtime) spells one of
#: these words, so a file whose text holds neither holds no probe and is not parsed. On this tree
#: that leaves about one file in seventeen, parsed in a fraction of the time every file takes. The
#: rail holds this reading and a parse of every file under ``tests/`` to the same probes.
PREFILTER: tuple[str, ...] = ("shutil", Path(CONTAINER_MODULE).stem)


def _read(path: Path, rel: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (ValueError, UnicodeDecodeError, OSError) as exc:
        raise Refused(f"{rel} cannot be read as Python: {exc}") from None


def scan(root: Path, *, prefilter: bool = True) -> Scan:
    """Every probe of a tool in ``root/tests``, by AST.

    With *prefilter*, a file whose text holds no word of :data:`PREFILTER` is skipped unparsed;
    without it, every file is parsed (the rail's reference reading)."""
    tests = root / "tests"
    if not tests.is_dir():
        raise Refused(f"{root} has no tests folder to read the probes from")
    found = Scan()
    texts: dict[str, str] = {}
    read: list[_Module] = []
    for path in sorted(tests.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        text = texts[rel] = _read(path, rel)
        if prefilter and not any(word in text for word in PREFILTER):
            continue
        try:
            tree = ast.parse(text, filename=rel)
        except (SyntaxError, ValueError) as exc:
            raise Refused(f"{rel} cannot be read as Python: {exc}") from None
        mod = _read_module(rel, tree, found)
        if mod is not None:
            read.append(mod)
    # A helper's parameter is read from its own module's calls; another module that names it may
    # call it with any tool.
    for mod in read:
        for helper in sorted(mod.helpers):
            if _named_elsewhere(helper, mod.rel, texts):
                found.unresolved.add((mod.rel, helper))
    return found


def _constant_strings(root: Path, rel: str, name: str) -> list[str]:
    path = root / rel
    if not path.is_file():
        raise Refused(f"{rel} is missing, so {name} cannot be read")
    value = _constants(_parse(path, rel)).get(name)
    strings = _strings(value) if value is not None else None
    if not strings:
        raise Refused(f"{rel} holds no {name} of text only")
    return strings


def probe_list(root: Path) -> tuple[str, ...]:
    """The tools the suite probes, sorted; :class:`Refused` when a probe cannot be named."""
    found = scan(root)
    for (rel, _), constant in sorted(COVERED_SITES.items()):
        found.probes.update(_constant_strings(root, rel, constant))
    left = sorted(found.unresolved - set(COVERED_SITES))
    if left:
        where = "; ".join(f"{rel} in {function}" for rel, function in left)
        raise Refused(f"a probe whose tool the scan cannot name: {where}")
    bad = sorted(p for p in found.probes if not p or os.sep in p or "/" in p)
    if bad:
        raise Refused(f"a probe names no plain program: {bad}")
    return tuple(sorted(found.probes))


# ── a tool's identity ────────────────────────────────────────────────────────────────────────


def identity(path: str | os.PathLike[str]) -> str:
    """``<resolved path>@sha256:<digest>`` of the file *path* names. Reads it; never runs it."""
    real = Path(path).resolve()
    digest = hashlib.sha256()
    try:
        with open(real, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise Refused(f"{real} cannot be read for its digest: {exc}") from None
    return f"{real}@sha256:{digest.hexdigest()}"


# ── the browse proofs' browser ───────────────────────────────────────────────────────────────


def _falsey(value: str | None) -> bool:
    return (value or "").strip().lower() in ("", "0", "false", "no", "off")


def _expanded(value: str, home: Path) -> Path:
    """``Path(value).expanduser()`` as the job's steps expand it: a leading ``~`` is *home*, the
    job's, never the caller's own ``HOME``; ``~user`` is that user's, from the password
    database."""
    if value == "~" or value.startswith(("~/", "~" + os.sep)):
        return home / value[2:]
    if not value.startswith("~"):
        return Path(value)
    try:
        return Path(value).expanduser()
    except RuntimeError as exc:
        raise Refused(f"{value} names a home that cannot be found: {exc}") from None


@dataclass(frozen=True)
class ChromeLookup:
    """``tests/browse_chrome.py``'s lookup, from its own constants."""

    env: str
    prefixes: tuple[str, ...]
    globs: tuple[str, ...]

    @classmethod
    def read(cls, root: Path) -> ChromeLookup:
        path = root / CHROME_MODULE
        if not path.is_file():
            raise Refused(f"{CHROME_MODULE} is missing, so the browser lookup cannot be read")
        constants = _constants(_parse(path, CHROME_MODULE))
        missing = [name for name in _CHROME_CONSTANTS if name not in constants]
        if missing:
            raise Refused(f"{CHROME_MODULE} holds no {', '.join(missing)}")
        env = constants["CHROME_ENV"]
        if not (isinstance(env, ast.Constant) and env.value == CHROME_SWITCH):
            raise Refused(f"{CHROME_MODULE}'s CHROME_ENV is not {CHROME_SWITCH}")
        prefixes = _strings(constants["_REVISION_DIR_PREFIXES"])
        globs = _strings(constants["_BINARY_GLOBS"])
        if not prefixes or not globs:
            raise Refused(f"{CHROME_MODULE}'s _REVISION_DIR_PREFIXES or _BINARY_GLOBS is not text")
        return cls(CHROME_SWITCH, tuple(prefixes), tuple(globs))

    def roots(self, environ: Mapping[str, str], home: Path, platform: str) -> tuple[Path, ...]:
        """``browsers_roots``: Playwright's own override, then the platform's default."""
        roots: list[Path] = []
        configured = environ.get("PLAYWRIGHT_BROWSERS_PATH")
        if not _falsey(configured):
            roots.append(_expanded(str(configured), home))
        if platform == "darwin":
            roots.append(home / "Library" / "Caches" / "ms-playwright")
        elif platform == "win32":
            local = environ.get("LOCALAPPDATA")
            if local:
                roots.append(Path(local) / "ms-playwright")
        else:
            cache = environ.get("XDG_CACHE_HOME")
            base = _expanded(cache, home) if cache else home / ".cache"
            roots.append(base / "ms-playwright")
        return tuple(dict.fromkeys(roots))

    def find(self, roots: tuple[Path, ...], environ: Mapping[str, str]) -> str | None:
        """``find_chrome``: the override when it can run, then the first binary under a root."""
        override = environ.get(self.env)
        if override and os.access(override, os.X_OK):
            return override
        for root in roots:
            if not root.is_dir():
                continue
            for prefix in self.prefixes:
                for revision in sorted(root.glob(f"{prefix}*"), reverse=True):
                    for pattern in self.globs:
                        for binary in sorted(revision.glob(pattern)):
                            if binary.is_file() and os.access(binary, os.X_OK):
                                return str(binary)
        return None


# ── the fingerprint ──────────────────────────────────────────────────────────────────────────


def fingerprint(
    root: Path,
    *,
    path: str,
    environ: Mapping[str, str],
    home: Path,
    platform: str = sys.platform,
) -> dict[str, str]:
    """The capabilities a run on this host has, as ``KeyInputs.capabilities`` takes them.

    *path* is the ``PATH`` the job's steps get, *environ* the environment they get, *home* their
    home: the caller's own are never read. Nothing found is started."""
    out: dict[str, str] = {}
    for name in probe_list(root):
        found = shutil.which(name, path=path) if path else None
        out[f"probe:{name}"] = identity(found) if found else ABSENT
    lookup = ChromeLookup.read(root)
    chrome = lookup.find(lookup.roots(environ, home, platform), environ)
    out["chrome"] = identity(chrome) if chrome else ABSENT
    for switch in SWITCHES:
        out[f"switch:{switch}"] = f"set:{environ[switch]}" if switch in environ else UNSET
    return dict(sorted(out.items()))
