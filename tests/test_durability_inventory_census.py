"""Every home location the CODE names is classified by the durability inventory.

`durability/inventory.py` is the single manifest of what PersonalClaw's state IS, and every
backup reads it: `snapshot._everything_paths` captures what it declares, and `audit_home()` — the
`durability.inventory` Doctor probe — reports what it does not. A location nobody classified is
therefore silently absent from `personalclaw snapshot`, the command the pre-1.0 release notes tell
users to run BEFORE upgrading. Every location has to land in one of three classes, each a decision
someone wrote down beside the row:

* **claimed and snapshotted** — an entry in `INVENTORY`;
* **deliberately excluded, with the reason** — a credential entry (`credential=True`: claimed,
  never captured), or a row in `IGNORED` (runtime scratch, per-install identity);
* **regenerable** — a `derived=True` entry (claimed, rebuilt rather than restored).

There is no fourth class. This file used to hold one — `_UNDECLARED_DEBT`, a pin list of "real
state nobody decided about" — and a pin is exactly how `incident.json` stayed out of every
snapshot for a release while its test passed.

**Why a census of the source, and why it reads the AST.** Hand-written lists of paths only ever
catch the stores someone remembered. The first version of this census was four regexes over the
source text, and a validator's week of normal use found five real stores it could not see
(settings B17, day 8's "Durability degraded"), each through a spelling the regexes did not know:

1. `Path(config_dir()) / "capture"` — the call is wrapped, so `config_dir() /` never matched
   (`inbound/capture_store.py`); thirteen sites are spelled this way.
2. `root = Path(base_dir) if base_dir else config_dir()` then `root / "trigger-idle"` — the home is
   the FALLBACK branch of an expression, not the whole right-hand side (`triggers/idle_poll.py`);
   a dozen trigger sidecars are spelled this way.
3. `record_routing_stats(rec, home=config_dir())` in one module and `Path(home) / _STATS_FILE`
   three calls deep in another — the home travels through PARAMETERS (`routing/stats.py`).
4. `self._path.with_name(f"{self._path.stem}.{ts}.bak{self._path.suffix}")` under a directory the
   SEL resolves as `Path.home() / ".personalclaw"` rather than through `config_dir()` at all.
5. `incident.json`, which the regexes did see — and which sat on the debt pin.

So this walks every module's syntax tree and follows the home as a VALUE: through `config_dir()`
and `Path.home() / ".personalclaw"`, wrapping calls (`Path()`, `str()`, `.resolve()`), both
branches of `a or b` / `a if c else b`, local and module names, `self.` attributes, function
return values, and parameters across module boundaries (a call that passes a home path marks the
callee's parameter, iterated to a fixed point). Segments resolve from literals, module and class
constants (imported ones included), tuple constants unpacked into `joinpath`, `Path` constants,
and f-strings, whose runtime parts become a placeholder the inventory is asked about.

**What it still cannot see, stated rather than implied.** A segment that is WHOLLY a runtime value
at the top of the home (`config_dir() / name`) has no static part to classify.
`test_the_blind_spot_is_bounded` pins how many of those exist, so a new one is a visible change;
the defence for them is `audit_home()` against a real home, wired as the Doctor probe.

**"Accounted for" is the inventory's question, not this file's.** A location is settled when
`inv.is_accounted` says so. Deciding a location means editing the inventory, never this file.
"""

from __future__ import annotations

import ast
import functools
import pathlib
from dataclasses import dataclass, field

from personalclaw.durability import inventory as inv

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "personalclaw"

#: A runtime part of an otherwise static name — an f-string's `{pid}`, a timestamp. The census asks
#: the inventory about the name with this in place, so `session_pid_{pid}.txt` is settled by the
#: `session_pid_*` glob exactly as the real `session_pid_4711.txt` is.
_DYN = "\x00"
_SHOWN = "{…}"

#: Calls that return their first argument's path unchanged, and methods that return their
#: receiver's. `str(home)` and `Path(home)` name the same location `home` does.
_WRAPS = frozenset({"Path", "PurePath", "PosixPath", "str", "fspath"})
_SAME = frozenset({"resolve", "expanduser", "absolute"})

Rel = tuple[str, ...]


@dataclass
class _Module:
    name: str
    shown: str
    tree: ast.Module
    consts: dict[str, str] = field(default_factory=dict)
    tuples: dict[str, tuple[str, ...]] = field(default_factory=dict)
    class_consts: dict[str, dict[str, str]] = field(default_factory=dict)
    imports: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = field(default_factory=dict)


@dataclass
class _Unit:
    """One function body, or a module's top level — what the fixed point re-reads."""

    mod: _Module
    cls: str | None
    key: tuple[str, str]
    nodes: list[ast.AST]
    params: list[str]
    kwonly: set[str]
    names: dict[str, set[Rel]] = field(default_factory=dict)
    #: every expression a local name is assigned, and the literal items a loop binds it to
    values: dict[str, list[ast.AST]] = field(default_factory=dict)


def _str(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _static_path(node: ast.AST | None) -> str | None:
    """`Path("onboarding") / "staged"` as a module constant — a path spelled from literals."""
    if (text := _str(node)) is not None:
        return text
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) in _WRAPS and node.args:
        return _static_path(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = _static_path(node.left), _static_path(node.right)
        return f"{left}/{right}" if left is not None and right is not None else None
    return None


def _is_config_dir(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        getattr(node.func, "id", None) == "config_dir"
        or getattr(node.func, "attr", None) == "config_dir"
    )


def _parse(name: str, shown: str, text: str) -> _Module | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    mod = _Module(name, shown, tree)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = _static_path(node.value)
            items = node.value.elts if isinstance(node.value, (ast.Tuple, ast.List)) else None
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if value is not None:
                    mod.consts[target.id] = value
                elif items is not None and all(_str(e) is not None for e in items):
                    mod.tuples[target.id] = tuple(_str(e) for e in items)  # type: ignore[misc]
        elif isinstance(node, ast.ClassDef):
            consts: dict[str, str] = {}
            for sub in node.body:
                if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                    targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and (v := _str(sub.value)) is not None:
                            consts[target.id] = v
                elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mod.funcs[f"{node.name}.{sub.name}"] = sub
            mod.class_consts[node.name] = consts
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod.funcs[node.name] = node
    package = name.rsplit(".", 1)[0] if not shown.endswith("__init__.py") else name
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package.split(".")
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                source = ".".join(base + ([node.module] if node.module else []))
            else:
                source = node.module or ""
            if source.startswith("personalclaw"):
                for alias in node.names:
                    mod.imports[alias.asname or alias.name] = (source, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("personalclaw"):
                    mod.imports[alias.asname or alias.name.split(".")[0]] = (alias.name, None)
    return mod


class _HomeCensus:
    """Follows the home through the source tree. See the module docstring for the rules."""

    def __init__(self, modules: dict[str, _Module]) -> None:
        self.mods = modules
        self.units: dict[tuple[str, str], _Unit] = {}
        self.params: dict[tuple[str, str], dict[str, set[Rel]]] = {}
        self.returns: dict[tuple[str, str], set[Rel]] = {}
        self.attrs: dict[tuple[str, str, str], set[Rel]] = {}
        self.globals: dict[tuple[str, str], set[Rel]] = {}
        self.callers: dict[tuple[str, str], set[tuple[str, str]]] = {}
        #: home-relative location → modules that name it
        self.found: dict[str, set[str]] = {}
        #: `<base>/<unresolvable segment>` → modules; a base of "" is the top of the home
        self.blind: dict[str, set[str]] = {}
        self._queue: list[tuple[str, str]] = []
        self._queued: set[tuple[str, str]] = set()
        for mod in modules.values():
            top = [
                s
                for s in mod.tree.body
                if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            self._add(mod, None, (mod.name, "<module>"), top, None)
            for fkey, fn in mod.funcs.items():
                self._add(
                    mod,
                    fkey.split(".")[0] if "." in fkey else None,
                    (mod.name, fkey),
                    fn.body,
                    fn.args,
                )
        while self._queue:
            key = self._queue.pop()
            self._queued.discard(key)
            self._visit(self.units[key])

    # ── static facts ───────────────────────────────────────────────────────────────────
    def _add(self, mod, cls, key, body, args: ast.arguments | None) -> None:
        params = [a.arg for a in (args.posonlyargs + args.args)] if args else []
        kwonly = {a.arg for a in args.kwonlyargs} if args else set()
        unit = _Unit(mod, cls, key, [n for stmt in body for n in ast.walk(stmt)], params, kwonly)
        self.units[key] = unit
        for name in params + sorted(kwonly):
            unit.values.setdefault(name, []).append(ast.Name(id="<runtime>"))
        for node in unit.nodes:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value:
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        unit.values.setdefault(target.id, []).append(node.value)
            if isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.target, ast.Name):
                it = node.iter
                if isinstance(it, (ast.Tuple, ast.List, ast.Set)):
                    items: list[ast.AST] = list(it.elts)
                elif isinstance(it, ast.Name) and it.id in mod.tuples:
                    items = [ast.Constant(value=v) for v in mod.tuples[it.id]]
                else:
                    items = [ast.Name(id="<runtime>")]
                unit.values.setdefault(node.target.id, []).extend(items)
            if isinstance(node, ast.Call):
                callee = self._callee(unit, node.func)
                if callee is not None:
                    self.callers.setdefault(callee[0], set()).add(key)
                if _is_config_dir(node):
                    self._enqueue(key)
            if isinstance(node, ast.BinOp) and self._home_root(unit, node):
                self._enqueue(key)

    def _const(self, mod: _Module, name: str, depth: int = 0) -> str | None:
        if name in mod.consts:
            return mod.consts[name]
        if name in mod.imports and depth < 4:
            source, attr = mod.imports[name]
            target = self.mods.get(source)
            if target is not None and attr is not None:
                return self._const(target, attr, depth + 1)
        return None

    def _callee(self, unit: _Unit, func: ast.AST) -> tuple[tuple[str, str], int] | None:
        """(function key, positional offset) of a call target defined in the tree, or None."""
        mod = unit.mod
        if isinstance(func, ast.Name):
            candidates = [(mod, func.id)]
            if func.id in mod.imports:
                source, attr = mod.imports[func.id]
                candidates.append((self.mods.get(source), attr))  # type: ignore[arg-type]
            for target, attr in candidates:
                if target is None or attr is None:
                    continue
                if attr in target.funcs:
                    return (target.name, attr), 0
                if f"{attr}.__init__" in target.funcs:
                    return (target.name, f"{attr}.__init__"), 1
            return None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            owner = func.value.id
            if owner in ("self", "cls") and unit.cls is not None:
                if f"{unit.cls}.{func.attr}" in mod.funcs:
                    return (mod.name, f"{unit.cls}.{func.attr}"), 1
            if owner in mod.imports:
                source, attr = mod.imports[owner]
                target = self.mods.get(source if attr is None else f"{source}.{attr}")
                if target is not None and func.attr in target.funcs:
                    return (target.name, func.attr), 0
        return None

    # ── segments ───────────────────────────────────────────────────────────────────────
    def _texts(
        self, unit: _Unit, node: ast.AST, seen: frozenset[str] = frozenset()
    ) -> set[str] | None:
        """The static texts a path segment can be, runtime parts as `_DYN`; None if it is wholly
        a runtime value. An empty text names nothing (joining it is a no-op), so it is dropped:
        `rel = ""` on a fallback branch must not make the home itself look like the store."""
        if (text := _str(node)) is not None:
            return {text} if text else None
        if isinstance(node, ast.JoinedStr):
            outs = {""}
            for value in node.values:
                if isinstance(value, ast.Constant):
                    outs = {o + str(value.value) for o in outs}
                    continue
                inner = value.value if isinstance(value, ast.FormattedValue) else value
                piece = self._texts(unit, inner, seen) or self._path_part(unit, inner)
                outs = {o + p for o in outs for p in (piece or {_DYN})}
            return {o for o in outs if o.replace(_DYN, "")} or None
        if isinstance(node, (ast.BoolOp, ast.IfExp)):
            branches = node.values if isinstance(node, ast.BoolOp) else [node.body, node.orelse]
            found = set().union(*((self._texts(unit, b, seen) or set()) for b in branches))
            return found or None
        if isinstance(node, ast.Name):
            if node.id in unit.values:  # a local: every value it is ever given
                if node.id in seen:
                    return None
                inner = seen | {node.id}
                found = set().union(
                    *((self._texts(unit, v, inner) or set()) for v in unit.values[node.id])
                )
                return found or None
            value = self._const(unit.mod, node.id)
            return {value} if value else None
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            owner, mod = node.value.id, unit.mod
            value = None
            if owner in ("self", "cls") and unit.cls is not None:
                value = mod.class_consts.get(unit.cls, {}).get(node.attr)
            elif owner in mod.class_consts:
                value = mod.class_consts[owner].get(node.attr)
            elif owner in mod.imports:
                source, attr = mod.imports[owner]
                target = self.mods.get(source if attr is None else f"{source}.{attr}")
                value = self._const(target, node.attr) if target is not None else None
            return {value} if value is not None else None
        return None

    def _path_part(self, unit: _Unit, node: ast.AST) -> set[str] | None:
        """`<home path>.stem` / `.name` / `.suffix` inside an f-string — the SEL archive's name."""
        if not (isinstance(node, ast.Attribute) and node.attr in ("stem", "name", "suffix")):
            return None
        leaves = {rel[-1] for rel in self.home(unit, node.value) if rel}
        if not leaves:
            return None
        pure = [pathlib.PurePosixPath(leaf) for leaf in leaves]
        return {getattr(p, node.attr) for p in pure}

    def _join(self, unit: _Unit, bases: set[Rel], segment: ast.AST) -> set[Rel]:
        texts = self._texts(unit, segment)
        if texts is None:
            for base in bases:
                where = "/".join(base) + "/" + ast.unparse(segment)
                self.blind.setdefault(where, set()).add(unit.mod.shown)
            return set()
        out: set[Rel] = set()
        for text in texts:
            if text.startswith("/"):
                continue  # an absolute segment leaves the home
            parts = tuple(p for p in text.replace("\\", "/").split("/") if p and p != ".")
            out |= {base + parts for base in bases}
        return out

    # ── home expressions ───────────────────────────────────────────────────────────────
    def _home_root(self, unit: _Unit, node: ast.BinOp) -> bool:
        """`Path.home() / ".personalclaw"` — the SEL's own resolver and a dozen fallbacks."""
        left = node.left
        return (
            isinstance(node.op, ast.Div)
            and isinstance(left, ast.Call)
            and getattr(left.func, "attr", None) == "home"
            and ".personalclaw" in (self._texts(unit, node.right) or ())
        )

    def home(self, unit: _Unit, node: ast.AST) -> set[Rel]:
        """Every home-relative location `node` can evaluate to (empty: not a home path)."""
        if _is_config_dir(node):
            return {()}
        if isinstance(node, ast.Call):
            f = node.func
            name = getattr(f, "id", None) or getattr(f, "attr", None) or ""
            if name in _WRAPS and node.args:
                return self.home(unit, node.args[0])
            if isinstance(f, ast.Attribute) and name in _SAME:
                return self.home(unit, f.value)
            if isinstance(f, ast.Attribute) and name == "joinpath":
                return self._join_all(unit, self.home(unit, f.value), node.args)
            if isinstance(f, ast.Attribute) and name == "join" and node.args:
                return self._join_all(unit, self.home(unit, node.args[0]), node.args[1:])
            if isinstance(f, ast.Attribute) and name in ("with_name", "with_suffix") and node.args:
                return self._sibling(unit, f.value, name, node.args[0])
            callee = self._callee(unit, f)
            return set(self.returns.get(callee[0], set())) if callee else set()
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if self._home_root(unit, node):
                return {()}
            bases = self.home(unit, node.left)
            return self._join(unit, bases, node.right) if bases else set()
        if isinstance(node, ast.BoolOp):
            return set().union(*(self.home(unit, v) for v in node.values))
        if isinstance(node, ast.IfExp):
            return self.home(unit, node.body) | self.home(unit, node.orelse)
        if isinstance(node, ast.Name):
            if node.id in unit.names:
                return set(unit.names[node.id])
            return set(self.globals.get((unit.mod.name, node.id), set()))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in ("self", "cls") and unit.cls is not None:
                prop = self.returns.get((unit.mod.name, f"{unit.cls}.{node.attr}"), set())
                return set(self.attrs.get((unit.mod.name, unit.cls, node.attr), set())) | prop
        return set()

    def _join_all(self, unit: _Unit, bases: set[Rel], args: list[ast.expr]) -> set[Rel]:
        for arg in args:
            if not bases:
                break
            if isinstance(arg, ast.Starred) and isinstance(arg.value, ast.Name):
                for part in unit.mod.tuples.get(arg.value.id, ()) or [None]:
                    if part is None:
                        return self._join(unit, bases, arg)
                    bases = {b + (part,) for b in bases}
                continue
            bases = self._join(unit, bases, arg)
        return bases

    def _sibling(self, unit: _Unit, of: ast.AST, how: str, arg: ast.AST) -> set[Rel]:
        rels = {r for r in self.home(unit, of) if r}
        if how == "with_name":
            return self._join(unit, {r[:-1] for r in rels}, arg)
        suffixes = self._texts(unit, arg) or set()
        return {
            r[:-1] + (str(pathlib.PurePosixPath(r[-1]).with_suffix("")) + s,)
            for r in rels
            for s in suffixes
        }

    # ── the fixed point ────────────────────────────────────────────────────────────────
    def _enqueue(self, key: tuple[str, str]) -> None:
        if key not in self._queued:
            self._queued.add(key)
            self._queue.append(key)

    def _grow(self, table: dict, key, rels: set[Rel], wake) -> None:
        current = table.setdefault(key, set())
        if not rels <= current:
            current |= rels
            for k in wake:
                self._enqueue(k)

    def _visit(self, unit: _Unit) -> None:
        mod = unit.mod
        for param, rels in self.params.get(unit.key, {}).items():
            unit.names.setdefault(param, set()).update(rels)
        for _ in range(4):  # a chain of local names settles within a few passes
            changed = False
            for node in unit.nodes:
                if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                    continue
                if node.value is None or not (rels := self.home(unit, node.value)):
                    continue
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        current = unit.names.setdefault(target.id, set())
                        if not rels <= current:
                            current |= rels
                            changed = True
                        if unit.key[1] == "<module>":
                            wake = [(mod.name, k) for k in mod.funcs]
                            self._grow(self.globals, (mod.name, target.id), rels, wake)
                    elif (
                        isinstance(target, ast.Attribute)
                        and getattr(target.value, "id", None) == "self"
                        and unit.cls is not None
                    ):
                        wake = [(mod.name, k) for k in mod.funcs if k.startswith(unit.cls + ".")]
                        self._grow(self.attrs, (mod.name, unit.cls, target.attr), rels, wake)
            if not changed:
                break
        for node in unit.nodes:
            if isinstance(node, (ast.BinOp, ast.Call)):
                for rel in self.home(unit, node):
                    if rel:
                        self.found.setdefault("/".join(rel), set()).add(mod.shown)
            if isinstance(node, ast.Return) and node.value is not None:
                if rels := self.home(unit, node.value):
                    wake = set(self.callers.get(unit.key, set()))
                    if unit.cls is not None:  # a property, read as `self.<name>`
                        wake |= {(mod.name, k) for k in mod.funcs if k.startswith(unit.cls + ".")}
                    self._grow(self.returns, unit.key, rels, wake)
            if isinstance(node, ast.Call) and (callee := self._callee(unit, node.func)):
                key, offset = callee
                target = self.units.get(key)
                if target is None:
                    continue
                table = self.params.setdefault(key, {})
                for index, arg in enumerate(node.args):
                    if index + offset < len(target.params) and (rels := self.home(unit, arg)):
                        self._grow(table, target.params[index + offset], rels, [key])
                for kw in node.keywords:
                    if kw.arg in target.params or kw.arg in target.kwonly:
                        if rels := self.home(unit, kw.value):
                            self._grow(table, kw.arg, rels, [key])


def _census_of(files: dict[str, str]) -> _HomeCensus:
    """Census a tree given as `{"personalclaw/x.py": source}` — the real one or a planted one."""
    modules: dict[str, _Module] = {}
    for shown, text in files.items():
        parts = list(pathlib.PurePosixPath(shown).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if (mod := _parse(".".join(parts), shown, text)) is not None:
            modules[mod.name] = mod
    return _HomeCensus(modules)


@functools.cache
def _real() -> _HomeCensus:
    return _census_of(
        {
            p.relative_to(_SRC.parent).as_posix(): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(_SRC.rglob("*.py"))
        }
    )


def _accounted(location: str) -> bool:
    return inv.is_accounted(location.replace(_DYN, "0"))


def _shown(location: str) -> str:
    return location.replace(_DYN, _SHOWN)


def _unaccounted(census: _HomeCensus) -> list[str]:
    return sorted(loc for loc in census.found if not _accounted(loc))


def _root_blind(census: _HomeCensus) -> list[str]:
    return sorted(where for where in census.blind if where.startswith("/"))


# ── floors: the census sees each spelling (a scan that finds nothing passes everything) ──


def test_the_census_is_not_vacuous():
    census = _real()
    tops = {loc.split("/", 1)[0] for loc in census.found}
    assert len(census.found) >= 250, f"only {len(census.found)} home locations censused"
    assert len(tops) >= 120, f"only {len(tops)} top-level home locations censused"
    assert len({e.path.split("/", 1)[0] for e in inv.all_entries()}) >= 90, "inventory read empty"


def _planted(**modules: str) -> _HomeCensus:
    return _census_of({f"personalclaw/{name}.py": text for name, text in modules.items()})


def test_each_spelling_that_hid_a_real_store_is_read():
    """One planted module per spelling the regex census could not see (module docstring 1-4)."""
    census = _planted(
        loader="def config_dir():\n    ...\n",
        wrapped=(
            "from pathlib import Path\nfrom personalclaw.loader import config_dir\n"
            'def d():\n    return Path(config_dir()) / "wrapped_store"\n'
        ),
        fallback=(
            "from pathlib import Path\nfrom personalclaw.loader import config_dir\n"
            "def d(base_dir=None):\n"
            "    root = Path(base_dir) if base_dir else config_dir()\n"
            '    return root / "fallback_store"\n'
        ),
        caller=(
            "from personalclaw.loader import config_dir\nfrom personalclaw.callee import record\n"
            "def go():\n    record({}, home=config_dir())\n"
        ),
        callee=(
            "from pathlib import Path\n"
            '_FILE = "param_store.json"\n'
            "def _path(home):\n    return Path(home) / _FILE\n"
            "def save(home, data):\n    _path(home).write_text(data)\n"
            "def record(row, *, home):\n    save(home, str(row))\n"
        ),
        sel=(
            "from pathlib import Path\n"
            "def _default_dir():\n    return Path.home() / '.personalclaw'\n"
            "class Log:\n"
            "    def __init__(self, base_dir=None):\n"
            "        self._dir = base_dir or _default_dir()\n"
            "        self._path = self._dir / 'events.jsonl'\n"
            "    def rotate(self, ts):\n"
            "        return self._path.with_name("
            "f'{self._path.stem}.{ts}.bak{self._path.suffix}')\n"
        ),
    )
    found = {_shown(loc) for loc in census.found}
    assert {
        "wrapped_store",
        "fallback_store",
        "param_store.json",
        "events.jsonl",
        "events.{…}.bak.jsonl",
    } <= found, found


def test_the_older_spellings_are_still_read():
    """The four the regex census knew: a literal, a constant, an f-string prefix, a bound home."""
    census = _planted(
        loader="def config_dir():\n    ...\n",
        consts='GOV = "governance"\n',
        old=(
            "import os\nfrom personalclaw.loader import config_dir\n"
            "from personalclaw.consts import GOV\n"
            '_NAME = "const_store"\n'
            "def a(pid, key):\n"
            '    config_dir() / "literal_store"\n'
            "    config_dir() / _NAME\n"
            '    config_dir() / f"session_pid_{pid}.txt"\n'
            "    base = config_dir()\n"
            '    base / f".stop-{key}"\n'
            '    os.path.join(config_dir(), "joined_store")\n'
            "    config_dir() / GOV / 'ceiling.json'\n"
        ),
    )
    found = {_shown(loc) for loc in census.found}
    assert {
        "literal_store",
        "const_store",
        "session_pid_{…}.txt",
        ".stop-{…}",
        "joined_store",
        "governance/ceiling.json",
    } <= found, found


def test_a_name_not_bound_to_the_home_is_not_followed():
    """Or every `path / "x"` in the tree becomes a phantom home location and the ratchet drowns."""
    census = _planted(
        loader="def config_dir():\n    ...\n",
        other='def f(root):\n    return root / "not_a_home_path"\n',
    )
    assert census.found == {}


def test_the_five_stores_a_week_of_use_found_are_visible_to_the_real_census():
    """Settings B17 and day 8: after normal use `audit_home()` named these as in NO snapshot. Each
    is here so a census that loses the spelling behind it fails by name, not by silence."""
    found = {_shown(loc) for loc in _real().found}
    for location in (
        "capture",  # Path(config_dir()) / "capture"
        "trigger-idle",  # root = Path(base_dir) if base_dir else config_dir()
        "routing_stats.json",  # home=config_dir() passed three calls deep
        "incident.json",  # was pinned as debt
        "sel_archive/security_events.{…}.jsonl",  # the rotated audit log
    ):
        assert location in found, f"the census no longer sees {location}"


def test_a_new_unclassified_writer_is_caught():
    """The rail itself, driven: a writer added without an inventory decision fails the ratchet."""
    census = _planted(
        loader="def config_dir():\n    ...\n",
        writer=(
            "from pathlib import Path\nfrom personalclaw.loader import config_dir\n"
            "def save(text):\n"
            '    (Path(config_dir()) / "brand_new_store" / "state.json").write_text(text)\n'
        ),
    )
    assert _unaccounted(census) == ["brand_new_store", "brand_new_store/state.json"]


def test_the_prefix_globs_match_a_real_filename_not_only_the_censused_prefix(tmp_path):
    """`session_pid_*` and `.stop-*` have to satisfy TWO readers: this census, which asks with a
    placeholder where the runtime part was, and `audit_home()`, which sees the real file on disk.
    Driven against real names so a glob written for one reader alone fails here.

    `tmp_path`, and `audit_home` takes the home as an argument — nothing here can reach
    `config_dir()`, which CREATES the home it resolves.
    """
    home = tmp_path
    (home / "session_pid_4711.txt").write_text("chat-1", encoding="utf-8")
    (home / ".stop-chat_1").write_text("", encoding="utf-8")
    result = inv.audit_home(home)
    assert result.unclaimed == [], f"the prefix globs miss the real filenames: {result.unclaimed}"
    for location in (f"session_pid_{_DYN}.txt", f".stop-{_DYN}"):
        assert _accounted(location), f"{_shown(location)} stopped matching its glob"


def test_themes_is_declared_so_snapshot_carries_a_custom_theme():
    """Issue 647. A theme saved from Settings › Design lands in `config_dir()/themes/<slug>.json`
    and the manifest did not know the directory existed, so `personalclaw snapshot` dropped
    every one and a restore came back without them."""
    claim = inv.claim_for("themes/nurse-handoff-night.json")
    assert claim is not None and claim.id == "themes"


# ── the ratchet ──


def test_every_censused_location_is_classified():
    """A home location the code names must be claimed, deliberately excluded, or regenerable —
    each a decision recorded in `durability/inventory.py`. There is no pin list to add to."""
    census = _real()
    missing = _unaccounted(census)
    assert not missing, (
        "these home locations are in no inventory class, so `personalclaw snapshot` will not "
        "carry them and the Doctor will report them: "
        + ", ".join(f"{_shown(loc)} (named in {sorted(census.found[loc])[:2]})" for loc in missing)
    )


def test_the_blind_spot_is_bounded():
    """What this scan cannot see, recorded as a number rather than left implied.

    A segment that is wholly a runtime value at the top of the home (`config_dir() / name`) is
    invisible to any static scan. Pinned EXACTLY, not with slack: slack in this bound is precisely
    how many new dynamic home paths can arrive without anyone noticing. Each of the five was read
    at its call site: the memory vault's CONFIGURED path (`memory_vault.py`, whose default is
    declared), a pack lockfile's recorded path (`packs/update.py`), the eval gate staging a
    caller-supplied artifact (`evals/gate.py`), the ablation harness digesting paths it was handed
    (`evals/ablation.py`), and the denylist check over a computed set of secret names
    (`security.py`) — a read, never a writer.
    """
    blind = _root_blind(_real())
    assert len(blind) <= 5, (
        "more home paths are now built from runtime values than when this was measured, so the "
        f"census covers proportionally less: {blind}"
    )
