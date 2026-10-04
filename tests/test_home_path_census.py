"""Every write a restore, an import or a sync makes into the home takes its path from home_path.

A replace restore, a merge restore, an import and a sync's pull put what an archive or another
machine brings into the home, and the one rule they share is that nothing of it is written through
a link the home holds (``durability.home_paths``). The check is one function,
``home_paths.home_path``, and ``landing`` is its form for a door that goes on to its next item. A
write that takes its path from anywhere else skips the check.

The scan reads the modules those doors live in, finds every write — a file copied, moved, written,
removed or made, a folder made, a database opened, a store's file rewritten, and a call of a writer
that is handed a path — and holds each to where its path came from:

* in a function of :data:`DOORS`, every write's path is home_path's (a call of it, a name bound
  only to what it returned, or a parameter the door names as handed one, which every call of the
  door in these modules then passes such a path for), or the archive's own side (a name the door
  names as the archive's, or a scratch folder);
* a writer of :data:`HAND_READ` writes at the paths it is handed and at the files kept beside
  them, which home_path checks too; it is read by hand, and every call of it is held to the rule;
* every other function of these modules that writes is in :data:`ELSEWHERE`, which says where it
  writes instead.

A new write in these modules that takes its path from anywhere else fails here, and so does a new
function that writes without being in a table.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"


@dataclass(frozen=True)
class Door:
    """A function that puts what an archive or another machine brings into the home.

    ``checked``: the parameters that hold a path home_path gave, which every call passes one for.
    ``homes``: the parameters that are the home a door starts its checks from, which every call
    passes as it is, never as a path built on the spot. ``archive``: the names of the archive's
    own side — its unpacked copy, a file of it, a scratch folder — which a door writes freely."""

    what: str
    checked: tuple[str, ...] = ()
    homes: tuple[str, ...] = ()
    archive: tuple[str, ...] = ()


#: The doors, by module and function (a nested one as ``outer.inner``).
DOORS: dict[tuple[str, str], Door] = {
    ("snapshot.py", "_do_replace"): Door(
        "a replace restore", homes=("pc",), archive=("snap", "src")
    ),
    ("snapshot.py", "_backup_and_copy"): Door(
        "a replace restore's named components", ("backup",), ("pc",), ("snap",)
    ),
    ("snapshot.py", "_do_merge"): Door("a merge restore", homes=("pc",), archive=("snap", "src")),
    ("snapshot.py", "_copytree_safe"): Door("a folder a replace puts back", ("dst",)),
    ("snapshot.py", "_merge_memory"): Door("memories merged in", ("dst_db",), (), ("src_db",)),
    ("snapshot.py", "_merge_sqlite_attach"): Door(
        "a database merged in", ("dst_db",), (), ("src_db",)
    ),
    ("snapshot.py", "_merge_tables"): Door("a database merged in", ("dst_db",), (), ("src_db",)),
    ("snapshot.py", "_merge_crons"): Door("the legacy crons merged in", ("dst_path",)),
    ("snapshot.py", "_merge_triggers"): Door("automations merged in", ("dst_path",)),
    ("snapshot.py", "_merge_event_triggers"): Door("event triggers merged in", ("dst_path",)),
    ("snapshot.py", "_merge_notifications"): Door("notifications merged in", ("dst_path",)),
    ("snapshot.py", "_merge_keyed_jsonl"): Door("a log merged in", ("dst",)),
    ("snapshot.py", "_merge_feedback"): Door("feedback merged in", ("dst",)),
    ("snapshot.py", "_merge_json_map"): Door("a map merged in", ("dst",)),
    ("snapshot.py", "_merge_run_history"): Door("run history merged in", ("dst_dir",)),
    ("snapshot.py", "_merge_security_events"): Door(
        "the audit log merged in", homes=("pc",), archive=("snap", "src")
    ),
    ("snapshot.py", "_merge_records"): Door("a store of records merged in", homes=("pc",)),
    ("snapshot.py", "_repoint_library_documents"): Door(
        "the library pointed at what a merge brought", homes=("pc",)
    ),
    ("durability/restore_items.py", "copy_tree_no_overwrite"): Door(
        "a folder a merge or an import brings in", homes=("home",), archive=("src", "item")
    ),
    ("durability/restore_items.py", "merged_or_brought_in"): Door(
        "a file a merge or an import merges or brings in", homes=("home",), archive=("snap",)
    ),
    ("portability.py", "apply_import_zip"): Door(
        "an import", archive=("snap", "work", "zip_path", "sp")
    ),
    ("durability/reconcile.py", "reconcile_entry"): Door("a sync's pull", homes=("home",)),
    ("durability/reconcile.py", "bring_in_folder"): Door(
        "a folder store a merge or an import brings in", homes=("home",)
    ),
    ("durability/reconcile.py", "bring_in"): Door(
        "a store of records a merge or an import brings in", homes=("home",)
    ),
    ("durability/reconcile.py", "take_in"): Door("the conflict review's write", ("dest",)),
    ("durability/reconcile.py", "_delete_here"): Door("the conflict review's delete", ("dest",)),
    ("durability/writeback.py", "apply_rows"): Door("a sync's rows written back", ("dest",)),
    ("durability/writeback.py", "_apply_entity_dir"): Door("a folder store's rows", ("root",)),
    ("durability/writeback.py", "_apply_json_file"): Door("a one-file store's row", ("dest",)),
    ("durability/writeback.py", "_apply_jsonl"): Door("a log's rows", ("dest",)),
    ("durability/db_merge.py", "make_db_merger._merge"): Door(
        "a sync's database", archive=("shard_dir", "src")
    ),
    ("durability/db_merge.py", "_apply_db_merge"): Door(
        "a sync's database", ("dst",), (), ("src",)
    ),
    ("durability/conflict_resolve.py", "resolve_conflict"): Door(
        "the conflict review", homes=("home",)
    ),
    ("durability/sync_cycle.py", "_forget_the_retired_side_log"): Door(
        "a sync's removal of a retired file", homes=("home",)
    ),
    ("schedule_history.py", "ScheduleRunStore.merge_in"): Door(
        "run history a merge brings in", archive=("src_dir", "src")
    ),
}

#: The writers a door hands a path to, and the parameters it is in. Each writes only there and at
#: the files kept beside it (a database's log), which home_path checked with it; read by hand.
HAND_READ: dict[tuple[str, str], tuple[str, ...]] = {
    ("durability/home_paths.py", "put_file"): ("dst",),
    ("durability/sqlite_files.py", "bring_in"): ("target",),
    ("durability/sqlite_files.py", "move_aside"): ("live", "backup"),
}

#: Every other function of the door modules that writes, and where: never what an archive or
#: another machine brings into the home.
ELSEWHERE: dict[tuple[str, str], str] = {
    ("snapshot.py", "snapshot_main"): "the staged snapshot, a scratch folder, and its archive",
    ("snapshot.py", "_write_archive"): "the snapshot archive, where the user asked for it",
    ("snapshot.py", "restore_plan"): "the snapshot, unpacked into a scratch folder",
    ("snapshot.py", "restore_merge"): "the snapshot, unpacked; the home is _do_merge's",
    ("snapshot.py", "_restore_export_archive"): "the home made; what comes in is the import's",
    ("snapshot.py", "restore_main"): "the snapshot unpacked, and the home made",
    ("portability.py", "_wal_checkpoint"): "a database of the home an export reads",
    ("portability.py", "_backup_sqlite"): "a database copied into an export, through scratch",
    ("portability.py", "_strip_excluded_from_staged"): "the archive's staged copy",
    ("durability/sqlite_files.py", "copy_file"): "a file of the home copied into a snapshot",
    ("durability/pull_engine.py", "_materialize"): "the pull's scratch folder",
}

#: The modules the doors live in: every function of them that writes is in a table.
MODULES = (
    "snapshot.py",
    "portability.py",
    "durability/sqlite_files.py",
    "durability/restore_items.py",
    "durability/reconcile.py",
    "durability/writeback.py",
    "durability/db_merge.py",
    "durability/pull_engine.py",
    "durability/conflict_resolve.py",
    "durability/sync_cycle.py",
)

#: The one check, and its form for a door that goes on to its next item.
GUARDS = frozenset({"home_path", "landing"})

#: Calls that write, by name, and the argument positions and keywords that say where.
_CALLS: dict[str, tuple[tuple[int, str], ...]] = {
    "shutil.copy": ((1, "dst"),),
    "shutil.copy2": ((1, "dst"),),
    "shutil.copyfile": ((1, "dst"),),
    "shutil.copytree": ((1, "dst"),),
    "shutil.move": ((0, "src"), (1, "dst")),
    "shutil.rmtree": ((0, "path"),),
    "os.replace": ((0, "src"), (1, "dst")),
    "os.rename": ((0, "src"), (1, "dst")),
    "os.remove": ((0, "path"),),
    "os.unlink": ((0, "path"),),
    "os.mkdir": ((0, "path"),),
    "os.makedirs": ((0, "name"),),
    "os.link": ((1, "dst"),),
    "os.symlink": ((1, "dst"),),
    "sqlite3.connect": ((0, "database"),),
    "record_files.rewrite": ((0, "path"),),
    "bounded_log.merge_jsonl": ((1, "dst"),),
}
#: The home's own writers, however they were imported.
_WRITERS = frozenset(
    {"atomic_write", "atomic_write_bytes", "atomic_json_write", "open_streamed", "private_file"}
) | {"write_private_file"}
#: Methods that write the path they are called on.
_METHODS = frozenset(
    {"write_text", "write_bytes", "touch", "mkdir", "unlink", "rmdir", "symlink_to", "chmod"}
) | {"hardlink_to", "rename"}
#: Methods that unpack an archive, and where they unpack it.
_UNPACKS = {"extractall": ((0, "path"),), "extract": ((1, "path"),)}
#: Calls that keep their argument's path, or a folder of it.
_SAME_PATH = frozenset({"str", "os.fspath", "Path", "pathlib.Path"})


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


def _own(fn: ast.AST) -> list[ast.AST]:
    """*fn*'s nodes, without those of a function nested in it, which are that function's."""
    out: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(fn))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        out.append(node)
        pending.extend(ast.iter_child_nodes(node))
    return out


def functions(tree: ast.AST, prefix: str = "") -> Iterator[tuple[str, ast.AST]]:
    """Every function of *tree*, by its name in the module (``Class.method``, ``outer.inner``)."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}{node.name}"
            yield name, node
            yield from functions(node, f"{name}.")
        elif isinstance(node, ast.ClassDef):
            yield from functions(node, f"{prefix}{node.name}.")


def _module(rel: str) -> str:
    return "personalclaw." + rel[: -len(".py")].replace("/", ".")


def _imports(nodes: list[ast.AST]) -> dict[str, str]:
    """``name → module.name`` for what *nodes* import, a module by its own dotted name."""
    out: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                out[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name.split(".")[0]] = alias.name
    return out


@dataclass
class _Scope:
    """What one function binds, for asking where a path it writes at came from."""

    door: Door
    bindings: dict[str, list[tuple[str, ast.AST | None]]]

    @classmethod
    def of(cls, fn: ast.AST, door: Door) -> _Scope:
        bindings: dict[str, list[tuple[str, ast.AST | None]]] = {}

        def bind(target: ast.AST, how: str, value: ast.AST | None) -> None:
            if isinstance(target, ast.Name):
                bindings.setdefault(target.id, []).append((how, value))
            elif isinstance(target, (ast.Tuple, ast.List)):
                values = value.elts if isinstance(value, ast.Tuple) else None
                for i, elt in enumerate(target.elts):
                    pair = values[i] if values and len(values) == len(target.elts) else None
                    bind(elt, how if pair is not None else "unknown", pair)

        for node in _own(fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    bind(target, "value", node.value)
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                bind(node.target, "value", node.value)
            elif isinstance(node, ast.AugAssign):
                bind(node.target, "unknown", None)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                bind(node.target, "iter", node.iter)
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                bind(node.optional_vars, "with", node.context_expr)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bindings.setdefault(node.name, []).append(("unknown", None))
        args = fn.args  # type: ignore[attr-defined]
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            bindings.setdefault(arg.arg, []).append(("param", None))
        return cls(door, bindings)

    def checked(self, node: ast.AST, seen: frozenset[str] = frozenset()) -> bool:
        """Whether *node* is a path home_path gave."""
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name.rsplit(".", 1)[-1] in GUARDS:
                return True
            if name in _SAME_PATH and len(node.args) == 1:
                return self.checked(node.args[0], seen)
            return False
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            return self.checked(node.value, seen)
        if isinstance(node, ast.IfExp):
            sides = [s for s in (node.body, node.orelse) if not _is_none(s)]
            return bool(sides) and all(self.checked(s, seen) for s in sides)
        if isinstance(node, ast.Name) and node.id not in seen:
            found = self.bindings.get(node.id, [])
            if ("param", None) in found:
                return node.id in self.door.checked
            return bool(found) and all(
                how == "value" and value is not None and self.checked(value, seen | {node.id})
                for how, value in found
            )
        return False

    def aside(self, node: ast.AST, seen: frozenset[str] = frozenset()) -> bool:
        """Whether *node* is a path of the archive's own side, or of a scratch folder."""
        if isinstance(node, ast.Name):
            if node.id in self.door.archive:
                return True
            if node.id in seen:
                return False
            found = self.bindings.get(node.id, [])
            return bool(found) and all(self._aside_binding(b, seen | {node.id}) for b in found)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self.aside(node.left, seen)
        if isinstance(node, ast.Attribute) and node.attr == "parent":
            return self.aside(node.value, seen)
        if isinstance(node, ast.JoinedStr):
            return any(
                isinstance(v, ast.FormattedValue) and self.aside(v.value, seen) for v in node.values
            )
        if isinstance(node, ast.Subscript):
            return self.aside(node.value, seen)
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in {"tempfile.TemporaryDirectory", "tempfile.mkdtemp"}:
                return True
            if (name in _SAME_PATH or name in {"sorted", "list"}) and len(node.args) == 1:
                return self.aside(node.args[0], seen)
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "joinpath",
                "rglob",
                "glob",
                "iterdir",
            }:
                return self.aside(node.func.value, seen)
        return False

    def _aside_binding(self, binding: tuple[str, ast.AST | None], seen: frozenset[str]) -> bool:
        how, value = binding
        return how in {"value", "iter", "with"} and value is not None and self.aside(value, seen)

    def plain(self, node: ast.AST) -> bool:
        """Whether *node* is a home as it was handed on: a name, never a path built on the spot."""
        return isinstance(node, (ast.Name, ast.Attribute)) or (
            isinstance(node, ast.Call) and _dotted(node.func) in _SAME_PATH and len(node.args) == 1
        )


def _is_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _writes(call: ast.Call) -> bool:
    """Whether an ``open`` call writes: a mode that does, or one that is computed."""
    mode: ast.AST | None = call.args[1] if len(call.args) > 1 else None
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return bool(set(mode.value) & set("wax+"))
    return True


def _read_only(node: ast.AST) -> bool:
    """A database opened by a URI that asks for it read-only."""
    return isinstance(node, ast.JoinedStr) and any(
        isinstance(v, ast.Constant) and ("mode=ro" in v.value or "immutable=1" in v.value)
        for v in node.values
    )


def _argument(call: ast.Call, position: int, keyword: str) -> ast.AST | None:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return call.args[position] if len(call.args) > position else None


@dataclass(frozen=True)
class Writer:
    """A function a door hands a path to: what each of its parameters is."""

    checked: tuple[tuple[int, str], ...]
    homes: tuple[tuple[int, str], ...]


def writers(trees: dict[str, ast.AST]) -> dict[str, Writer]:
    """``module.function → Writer`` for each door and each writer read by hand, from *trees*."""
    out: dict[str, Writer] = {}
    for rel, tree in trees.items():
        defs = dict(functions(tree))
        for (where, name), checked, homes in [
            *((key, door.checked, door.homes) for key, door in DOORS.items()),
            *((key, params, ()) for key, params in HAND_READ.items()),
        ]:
            if where != rel or name not in defs:
                continue
            args = defs[name].args  # type: ignore[attr-defined]
            names = [a.arg for a in [*args.posonlyargs, *args.args]]
            if names and names[0] in {"self", "cls"}:
                names = names[1:]
            keywords = [a.arg for a in args.kwonlyargs]

            def at(params: tuple[str, ...]) -> tuple[tuple[int, str], ...]:
                return tuple((names.index(p) if p in names else 1_000, p) for p in params)

            assert all(p in names or p in keywords for p in (*checked, *homes)), (rel, name)
            out[f"{_module(rel)}.{name.rsplit('.', 1)[-1]}"] = Writer(at(checked), at(homes))
    return out


def stray_writes(
    tree: ast.AST, rel: str, known: dict[str, Writer], doors: dict[tuple[str, str], Door]
) -> dict[str, list[str]]:
    """``function → each write of it whose path is neither home_path's nor the archive's``, for
    every door of *rel* in *doors*."""
    module_imports = _imports(list(ast.walk(tree)))
    here = _module(rel)
    out: dict[str, list[str]] = {}
    for name, fn in functions(tree):
        door = doors.get((rel, name))
        if door is None:
            continue
        scope = _Scope.of(fn, door)
        imports = {**module_imports, **_imports(_own(fn))}
        problems: list[str] = []
        for node in _own(fn):
            if not isinstance(node, ast.Call):
                continue
            for what, where, must in _sinks(node, imports, here, known):
                if where is None:
                    continue
                if must == "home" and not scope.plain(where):
                    problems.append(f"{what} at line {node.lineno} is handed a home built there")
                elif must == "path" and not (scope.checked(where) or scope.aside(where)):
                    problems.append(f"{what} at line {node.lineno} writes at a path not checked")
            if _dotted(node.func).rsplit(".", 1)[-1] in GUARDS and node.args:
                if not scope.plain(node.args[0]):
                    problems.append(f"the check at line {node.lineno} starts below a built path")
        if problems:
            out[name] = problems
    return out


def _sinks(
    call: ast.Call, imports: dict[str, str], here: str, known: dict[str, Writer]
) -> Iterator[tuple[str, ast.AST | None, str]]:
    """Each place *call* writes at: ``(what, the argument that says where, "path"|"home")``."""
    name = _dotted(call.func)
    if name == "open":
        if _writes(call):
            yield "open", _argument(call, 0, "file"), "path"
        return
    if name in _CALLS:
        if name == "sqlite3.connect" and call.args and _read_only(call.args[0]):
            return
        for position, keyword in _CALLS[name]:
            yield name, _argument(call, position, keyword), "path"
        return
    last = name.rsplit(".", 1)[-1]
    if last in _WRITERS:
        yield last, _argument(call, 0, "path"), "path"
        return
    if isinstance(call.func, ast.Attribute) and call.func.attr in _METHODS:
        yield f".{call.func.attr}", call.func.value, "path"
        return
    if isinstance(call.func, ast.Attribute) and call.func.attr in _UNPACKS:
        for position, keyword in _UNPACKS[call.func.attr]:
            yield f".{call.func.attr}", _argument(call, position, keyword), "path"
        return
    target = _resolve(call.func, imports, here)
    writer = known.get(target) if target else None
    if writer is None:
        return
    for position, keyword in writer.checked:
        yield target, _argument(call, position, keyword), "path"
    for position, keyword in writer.homes:
        yield target, _argument(call, position, keyword), "home"


def _resolve(func: ast.AST, imports: dict[str, str], here: str) -> str:
    """``module.function`` a call names, as far as the module's imports say: a function, one of
    an imported module, or a method of an object made on the spot (``Store(home).merge_in``)."""
    if isinstance(func, ast.Name):
        return imports.get(func.id, f"{here}.{func.id}")
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        base = imports.get(func.value.id)
        return f"{base}.{func.attr}" if base else ""
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Call)
        and isinstance(func.value.func, ast.Name)
    ):
        made = imports.get(func.value.func.id, f"{here}.{func.value.func.id}")
        return f"{made.rsplit('.', 1)[0]}.{func.attr}"
    return ""


def unaccounted(tree: ast.AST, rel: str) -> list[str]:
    """The functions of *rel* that write and are in no table."""
    accounted = {name for where, name in (*DOORS, *HAND_READ, *ELSEWHERE) if where == rel}
    module_imports = _imports(list(ast.walk(tree)))
    out = []
    for name, fn in functions(tree):
        if name in accounted:
            continue
        imports = {**module_imports, **_imports(_own(fn))}
        if any(
            any(True for _ in _sinks(node, imports, _module(rel), {}))
            for node in _own(fn)
            if isinstance(node, ast.Call)
        ):
            out.append(name)
    return out


def _trees() -> dict[str, ast.AST]:
    rels = {*MODULES, *(rel for rel, _ in DOORS), *(rel for rel, _ in HAND_READ)}
    return {rel: ast.parse((SRC / rel).read_text(encoding="utf-8")) for rel in sorted(rels)}


# ── the rail ─────────────────────────────────────────────────────────────────────────────────────


def test_every_write_a_door_makes_takes_its_path_from_home_path():
    trees = _trees()
    known = writers(trees)
    offenders = {
        f"{rel}::{fn}": problems
        for rel, tree in trees.items()
        for fn, problems in stray_writes(tree, rel, known, DOORS).items()
    }
    assert not offenders, (
        "these put what an archive or another machine brings into the home at a path that did "
        "not come from durability.home_paths.home_path, so a link the home holds there would "
        f"carry the write out of it: {offenders}"
    )


def test_every_function_of_the_door_modules_that_writes_is_accounted_for():
    trees = _trees()
    loose = {rel: names for rel in MODULES if (names := unaccounted(trees[rel], rel))}
    assert not loose, (
        "these write and are in no table of this census: a door goes in DOORS (and takes its "
        f"paths from home_path), anything else in ELSEWHERE, with where it writes: {loose}"
    )


def test_every_table_names_a_function_that_is_there():
    trees = _trees()
    for rel, name in (*DOORS, *HAND_READ, *ELSEWHERE):
        assert name in dict(functions(trees[rel])), f"{rel}::{name} is gone; update the census"


def test_each_form_of_the_check_is_the_check():
    """``landing`` answers through ``home_path``: a form of the check that did not would be a way
    around it."""
    trees = {rel: ast.parse((SRC / rel).read_text(encoding="utf-8")) for rel in _GUARD_HOMES}
    for rel, name in _GUARD_HOMES.items():
        fn = dict(functions(trees[rel]))[name]
        calls = {_dotted(n.func) for n in _own(fn) if isinstance(n, ast.Call)}
        assert "home_path" in calls, f"{rel}::{name} does not ask home_path"
    assert set(_GUARD_HOMES.values()) | {"home_path"} == GUARDS


_GUARD_HOMES = {"durability/home_paths.py": "landing"}


def test_the_scan_sees_the_doors_write():
    """Positive control on the real tree: every door the table names writes, through home_path, a
    writer it hands a path to, or a door of its own. A scan broken into seeing no write would pass
    the rail over a tree full of them."""
    trees = _trees()
    known = writers(trees)
    for (rel, name), door in DOORS.items():
        fn = dict(functions(trees[rel]))[name]
        imports = {**_imports(list(ast.walk(trees[rel]))), **_imports(_own(fn))}
        calls = [n for n in _own(fn) if isinstance(n, ast.Call)]
        writes = [s for node in calls for s in _sinks(node, imports, _module(rel), known)]
        guarded = any(_dotted(n.func).rsplit(".", 1)[-1] in GUARDS for n in calls)
        hands_on = any(_resolve(n.func, imports, _module(rel)) in known for n in calls)
        assert (
            writes or guarded or hands_on
        ), f"the scan sees {rel}::{name} ({door.what}) write nothing"


# ── planted: the shapes the rail exists to catch, and the ones it lets through ──────────────────

_PLANTED = """
import shutil
import sqlite3

from personalclaw.atomic_write import atomic_write
from personalclaw.durability import home_paths, sqlite_files


def copied_to_a_path_built_on_the_spot(snap, pc):
    shutil.copy2(snap / "x.json", pc / "x.json")


def bound_to_a_built_path(snap, pc, rel):
    dst = pc / rel
    sqlite_files.bring_in(snap / rel, dst)


def checked_then_built_below(snap, pc, rel):
    folder = home_paths.home_path(pc, "workspace")
    atomic_write(folder / rel, "{}")


def a_writer_handed_a_built_path(snap, pc):
    _merge_memory(snap / "memory.db", pc / "memory.db")


def a_database_opened_at_a_built_path(pc):
    sqlite3.connect(str(pc / "learning.db"))


def a_home_built_below_the_home(snap, pc):
    _copy_tree_no_overwrite(snap / "workspace", pc / "workspace", "notes", [])


def a_check_that_starts_below_a_built_path(snap, pc):
    home_paths.put_file(snap / "x", home_paths.home_path(pc / "workspace", "x"))


def a_name_bound_twice(snap, pc, rel):
    dst = home_paths.home_path(pc, rel)
    if rel:
        dst = pc / rel
    dst.mkdir()


def a_new_write_of_its_own(pc):
    (pc / "new.json").write_text("{}")


def _merge_memory(src_db, dst_db):
    sqlite3.connect(str(dst_db))


def _copy_tree_no_overwrite(src, home, rel, left):
    return 0
"""

_THROUGH = """
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from personalclaw.atomic_write import atomic_write
from personalclaw.durability import home_paths, sqlite_files


def checked_inline(snap, pc):
    home_paths.put_file(snap / "x.json", home_paths.home_path(pc, "x.json"))


def checked_and_bound(snap, pc, rel, left):
    dst = home_paths.landing(pc, rel, left)
    if dst is not None:
        sqlite_files.bring_in(snap / rel, dst)
        dst.parent.mkdir(parents=True, exist_ok=True)


def checked_on_one_branch(snap, pc, left):
    here = home_paths.landing(pc, "skills", left) if (snap / "skills").is_dir() else None
    if here is not None:
        here.mkdir()


def a_writer_handed_a_checked_path(snap, pc):
    _merge_memory(snap / "memory.db", home_paths.home_path(pc, "memory.db"))


def the_archives_side(archive):
    with tempfile.TemporaryDirectory() as work_str:
        work = Path(work_str)
        with tarfile.open(archive) as tar:
            tar.extractall(work)
        for item in sorted(work.rglob("*")):
            item.unlink()


def a_home_handed_on_as_it_is(snap, pc):
    _copy_tree_no_overwrite(snap / "workspace", pc, "workspace", [])


def _merge_memory(src_db, dst_db):
    sqlite3.connect(str(src_db))
    sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    sqlite3.connect(str(dst_db))
    atomic_write(dst_db, "")


def _copy_tree_no_overwrite(src, home, rel, left):
    return 0
"""

_REL = "planted.py"
_PLANTED_DOORS = {
    (_REL, "copied_to_a_path_built_on_the_spot"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "bound_to_a_built_path"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "checked_then_built_below"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "a_writer_handed_a_built_path"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "a_database_opened_at_a_built_path"): Door("planted", homes=("pc",)),
    (_REL, "a_home_built_below_the_home"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "a_check_that_starts_below_a_built_path"): Door(
        "planted", homes=("pc",), archive=("snap",)
    ),
    (_REL, "a_name_bound_twice"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "a_new_write_of_its_own"): Door("planted", homes=("pc",)),
    (_REL, "_merge_memory"): Door("planted", ("dst_db",), (), ("src_db",)),
    (_REL, "checked_inline"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "checked_and_bound"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "checked_on_one_branch"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "a_writer_handed_a_checked_path"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "the_archives_side"): Door("planted", archive=("archive",)),
    (_REL, "a_home_handed_on_as_it_is"): Door("planted", homes=("pc",), archive=("snap",)),
    (_REL, "_copy_tree_no_overwrite"): Door("planted", homes=("home",)),
}
#: The planted writers, as the scan knows the real ones.
_PLANTED_WRITERS = {
    "personalclaw.planted._merge_memory": Writer(checked=((1, "dst_db"),), homes=()),
    "personalclaw.planted._copy_tree_no_overwrite": Writer(checked=(), homes=((1, "home"),)),
    "personalclaw.durability.home_paths.put_file": Writer(checked=((1, "dst"),), homes=()),
    "personalclaw.durability.sqlite_files.bring_in": Writer(checked=((1, "target"),), homes=()),
}


def test_the_rail_catches_each_way_around_the_check():
    found = stray_writes(ast.parse(_PLANTED), _REL, _PLANTED_WRITERS, _PLANTED_DOORS)
    assert set(found) == {
        "copied_to_a_path_built_on_the_spot",
        "bound_to_a_built_path",
        "checked_then_built_below",
        "a_writer_handed_a_built_path",
        "a_database_opened_at_a_built_path",
        "a_home_built_below_the_home",
        "a_check_that_starts_below_a_built_path",
        "a_name_bound_twice",
        "a_new_write_of_its_own",
    }, found


def test_the_rail_lets_each_checked_shape_through():
    found = stray_writes(ast.parse(_THROUGH), _REL, _PLANTED_WRITERS, _PLANTED_DOORS)
    assert found == {}, found


def test_a_new_function_that_writes_is_caught_until_it_is_accounted_for():
    tree = ast.parse(_PLANTED)
    assert "a_new_write_of_its_own" in unaccounted(tree, "snapshot.py")
