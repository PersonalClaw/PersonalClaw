"""What a behaviour module and its source-tree checks share.

The behaviour module:
``tests/test_durability_inventory_census.py``

Its checks:
``tests/rails/test_durability_inventory_census_tree.py``

This module sits beside the suite so that no rail imports a behaviour test
module.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field

from personalclaw.durability import inventory as inv

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


def _module(name: str, shown: str, tree: ast.Module) -> _Module:
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


def _census_of(files: dict[str, ast.Module]) -> _HomeCensus:
    """Census a tree given as `{"personalclaw/x.py": parsed module}` — the real one or a planted
    one."""
    modules: dict[str, _Module] = {}
    for shown, tree in files.items():
        parts = list(pathlib.PurePosixPath(shown).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        mod = _module(".".join(parts), shown, tree)
        modules[mod.name] = mod
    return _HomeCensus(modules)


def _accounted(location: str) -> bool:
    return inv.is_accounted(location.replace(_DYN, "0"))


def _shown(location: str) -> str:
    return location.replace(_DYN, _SHOWN)


def _unaccounted(census: _HomeCensus) -> list[str]:
    return sorted(loc for loc in census.found if not _accounted(loc))
