"""Every write a door makes into the home, every read an export makes of it, and every lock in it
takes its path from the one check (``durability.home_paths``).

A replace restore, a merge restore, the import of an export archive or of a project archive, a
pack's install and a sync's pull put what an archive or another machine brings into the home, and
the one rule they share is that nothing of it is written through a link the home holds. The check
is one function, ``home_paths.home_path``, and its forms (``landing``, and the pack layout's
``component_path`` and the helpers under it) answer through it. A write that takes its path from
anywhere else skips the check.

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

Two more rails hold the same rule's other halves, below: every read an export makes of the home to
send it out takes its path from ``export_path`` (the read form of the check), and every lock in the
home is opened by ``open_lock``, which never empties a file and never opens one through a link.
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
    ("workflows/project_archive.py", "import_project"): Door(
        "a project archive's import", homes=("home",)
    ),
    ("workflows/project_archive.py", "commit_import"): Door(
        "a project archive's files", homes=("home",)
    ),
    ("packs/import_.py", "import_pack"): Door("a pack's install", archive=("quarantine",)),
    ("packs/import_.py", "_Journal.__init__"): Door("a pack install's journal", homes=("home",)),
    ("packs/import_.py", "_commit_file_component"): Door("a pack's component", homes=("home",)),
    ("packs/import_.py", "_commit_skill"): Door("a pack's skill", homes=("home",)),
    ("packs/import_.py", "_stage_roster"): Door("a pack's staged roster", homes=("home",)),
    ("packs/import_.py", "_stage_config_subset"): Door("a pack's staged settings", homes=("home",)),
    ("packs/update.py", "apply_update"): Door("a pack's update", archive=("quarantine",)),
}

#: The writers a door hands a path to, and the parameters it is in. Each writes only there and at
#: the files kept beside it (a database's log), which home_path checked with it; read by hand.
HAND_READ: dict[tuple[str, str], tuple[str, ...]] = {
    ("durability/home_paths.py", "put_file"): ("dst",),
    ("durability/sqlite_files.py", "bring_in"): ("target",),
    ("durability/sqlite_files.py", "move_aside"): ("live", "backup"),
    # The folders on the way to the path it is handed that are not there yet, each recorded.
    ("packs/import_.py", "_mkdir_journaled"): ("path",),
    ("packs/import_.py", "_write_component_file"): ("path",),
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
    ("workflows/project_archive.py", "_open_archive"): "the archive's decrypted copy, in scratch",
    ("workflows/project_archive.py", "extract_archive"): "the archive, unpacked into scratch",
    ("workflows/project_archive.py", "_extract_one"): "one member, in the archive's scratch folder",
    ("packs/import_.py", "_extract_quarantine"): "the pack, unpacked into its quarantine (scratch)",
    ("packs/import_.py", "inspect_pack"): "the pack's quarantine, a scratch folder",
    (
        "packs/import_.py",
        "_Journal._flush",
    ): "the journal, at the path __init__ took from the check",
    ("packs/import_.py", "_Journal.rollback"): (
        "what the install wrote, by the paths it recorded before each write, each the check's"
    ),
    ("packs/import_.py", "_Journal.discard"): (
        "the journal and the folders it made for it, at the paths __init__ took from the check"
    ),
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
    "workflows/project_archive.py",
    "packs/import_.py",
    "packs/update.py",
)

#: The forms of the one check, by module and name: each answers through ``home_path`` or another
#: form (``test_each_form_of_the_check_is_the_check``). ``export_path`` is its form for a read.
FORMS: dict[tuple[str, str], tuple[str, ...]] = {
    ("durability/home_paths.py", "home_path"): ("_checked",),
    ("durability/home_paths.py", "export_path"): ("_checked",),
    ("durability/home_paths.py", "landing"): ("home_path",),
    ("packs/import_.py", "_landing"): ("home_path",),
    ("packs/import_.py", "_inside"): ("_landing",),
    ("packs/import_.py", "component_path"): ("_inside",),
    ("packs/import_.py", "_staged_dir"): ("_inside",),
}

#: The one check, and its forms, by name.
GUARDS = frozenset(name for _, name in FORMS)

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
    """Each form answers through the one it names, and ``home_path`` and ``export_path`` through
    the check itself (``_checked``): a form of the check that did not would be a way around it."""
    trees = {rel: ast.parse((SRC / rel).read_text(encoding="utf-8")) for rel, _ in FORMS}
    for (rel, name), through in FORMS.items():
        fn = dict(functions(trees[rel]))[name]
        calls = {_dotted(n.func).rsplit(".", 1)[-1] for n in _own(fn) if isinstance(n, ast.Call)}
        assert set(through) <= calls, f"{rel}::{name} does not answer through {through}"
        assert all(t in GUARDS or t == "_checked" for t in through), (rel, name)


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


# ── reads for an export: nothing a sync or a backup sends is read through a link ───────────────


@dataclass(frozen=True)
class ReadDoor:
    """A function that reads the home for an export: a sync's copy for the other machines, or the
    backup export. ``checked``: the parameters that hold a path the check gave, which every call of
    it in these modules passes one for. ``homes``: the parameters that are the home, passed as they
    are. ``aside``: the export's own side (its folder, its scratch copies), read freely."""

    what: str
    checked: tuple[str, ...] = ()
    homes: tuple[str, ...] = ()
    aside: tuple[str, ...] = ()


READ_DOORS: dict[tuple[str, str], ReadDoor] = {
    ("durability/shards.py", "export_shards"): ReadDoor(
        "an export of the home's stores", homes=("home",), aside=("out_dir", "workdir", "copy")
    ),
    ("durability/shards.py", "dirty_entries"): ReadDoor(
        "the backup export's measure of what changed", homes=("home",), aside=("state_path",)
    ),
    ("durability/shards.py", "machine_id"): ReadDoor("the id every copy names", homes=("home",)),
    ("durability/reconcile.py", "held_shas"): ReadDoor(
        "what this home holds, as its next copy says", homes=("home",)
    ),
    ("durability/reconcile.py", "read_local"): ReadDoor("a store's rows", checked=("src",)),
}

#: The readers a door hands a path to, and the parameters it is in, read by hand: each reads only
#: there. ``True`` for one that asks ``not_read`` of what it reads (it walks a folder, or names a
#: link at its own file), which the rail holds it to.
READ_HAND_READ: dict[tuple[str, str], tuple[tuple[str, ...], bool]] = {
    ("durability/shards.py", "read_entity_dir"): (("root",), True),
    ("durability/shards.py", "jsonl_files"): (("src",), True),
    ("durability/shards.py", "read_json_file"): (("path",), True),
    ("durability/shards.py", "_consistent_db_copy"): (("src",), False),
    ("durability/shards.py", "_jsonl_rows_by_year"): (("path",), False),
    ("durability/shards.py", "_file_bytes"): (("path",), False),
    ("durability/shards.py", "_fold_wal"): (("db_path",), False),
    # Measures a folder by its files' times and sizes; reads none of their content.
    ("durability/shards.py", "_fingerprint"): (("path",), False),
}

#: Every other function of the export modules that reads, and what: never the home, for an export.
READ_ELSEWHERE: dict[tuple[str, str], str] = {
    ("durability/shards.py", "recorded_machine_id"): "the id a restore's dry run compares",
    ("durability/shards.py", "_sqlite_tables"): "the export's scratch copy of a database",
    ("durability/shards.py", "_sqlite_rows"): "the export's scratch copy of a database",
    ("durability/shards.py", "_stage_db_copy"): "the export's scratch copy of a database",
    ("durability/shards.py", "is_an_export"): "an export folder's manifest",
    ("durability/shards.py", "_merged_shard_records"): "the earlier export's manifest",
    ("durability/shards.py", "validate"): "an export folder",
    ("durability/shards.py", "_rows_of_shard"): "a shard of an export folder",
    ("durability/shards.py", "import_shards"): "an export folder",
    ("durability/shards.py", "clear_shards"): "an export folder",
}

#: The modules an export reads the home in: every function of them that reads is in a table.
READ_MODULES = ("durability/shards.py", "durability/reconcile.py")

#: Readers that hand back only paths they checked, each with ``not_read``.
CHECKED_LISTS = frozenset({"jsonl_files"})

#: Calls that read, by name, and the argument positions and keywords that say where.
_READ_CALLS: dict[str, tuple[tuple[int, str], ...]] = {
    "os.walk": ((0, "top"),),
    "os.scandir": ((0, "path"),),
    "os.listdir": ((0, "path"),),
    "sqlite3.connect": ((0, "database"),),
    "shutil.copy": ((0, "src"),),
    "shutil.copy2": ((0, "src"),),
    "shutil.copyfile": ((0, "src"),),
    "shutil.copytree": ((0, "src"),),
}
#: Methods that read the path they are called on, or walk it.
_READ_METHODS = frozenset({"read_bytes", "read_text", "rglob", "glob", "iterdir", "open"})


class _ReadScope(_Scope):
    """A read door's bindings. As the write scope, and: a list a checked reader returned
    (:data:`CHECKED_LISTS`), a comprehension that keeps items of a checked list, and a name a loop
    takes from one, are checked too; a name rebound from itself keeps what its other bindings
    are."""

    def checked(self, node: ast.AST, seen: frozenset[str] = frozenset()) -> bool:
        if isinstance(node, ast.Call) and _dotted(node.func).rsplit(".", 1)[-1] in CHECKED_LISTS:
            return True
        if isinstance(node, ast.ListComp) and len(node.generators) == 1:
            gen = node.generators[0]
            same = isinstance(node.elt, ast.Name) and isinstance(gen.target, ast.Name)
            return same and node.elt.id == gen.target.id and self.checked(gen.iter, seen)
        if isinstance(node, ast.Name):
            if node.id in seen:
                return True  # its other bindings decide
            found = self.bindings.get(node.id, [])
            if ("param", None) in found:
                return node.id in self.door.checked
            return bool(found) and all(
                how in {"value", "iter", "with"}
                and value is not None
                and self.checked(value, seen | {node.id})
                for how, value in found
            )
        return super().checked(node, seen)


def _read_sinks(
    call: ast.Call, imports: dict[str, str], here: str, known: dict[str, Writer]
) -> Iterator[tuple[str, ast.AST | None, str]]:
    """Each place *call* reads at: ``(what, the argument that says where, "path"|"home")``."""
    name = _dotted(call.func)
    if name == "open":
        if not _writes(call):
            yield "open", _argument(call, 0, "file"), "path"
        return
    if name in _READ_CALLS:
        for position, keyword in _READ_CALLS[name]:
            yield name, _argument(call, position, keyword), "path"
        return
    if isinstance(call.func, ast.Attribute) and call.func.attr in _READ_METHODS:
        yield f".{call.func.attr}", call.func.value, "path"
        return
    target = _resolve(call.func, imports, here)
    reader = known.get(target) if target else None
    if reader is None:
        return
    for position, keyword in reader.checked:
        yield target, _argument(call, position, keyword), "path"
    for position, keyword in reader.homes:
        yield target, _argument(call, position, keyword), "home"


def readers(trees: dict[str, ast.AST]) -> dict[str, Writer]:
    """``module.function → Writer`` for each read door and each reader read by hand."""
    out: dict[str, Writer] = {}
    rows = [
        *((key, door.checked, door.homes) for key, door in READ_DOORS.items()),
        *((key, params, ()) for key, (params, _asks) in READ_HAND_READ.items()),
    ]
    for (where, name), checked, homes in rows:
        defs = dict(functions(trees[where]))
        args = defs[name].args  # type: ignore[attr-defined]
        names = [a.arg for a in [*args.posonlyargs, *args.args]]
        at = {p: names.index(p) for p in names}
        assert all(p in at for p in (*checked, *homes)), (where, name)
        out[f"{_module(where)}.{name}"] = Writer(
            tuple((at[p], p) for p in checked), tuple((at[p], p) for p in homes)
        )
    return out


def stray_reads(
    tree: ast.AST, rel: str, known: dict[str, Writer], doors: dict[tuple[str, str], ReadDoor]
) -> dict[str, list[str]]:
    """``function → each read of it whose path is neither the check's nor the export's own``, for
    every read door of *rel* in *doors*."""
    module_imports = _imports(list(ast.walk(tree)))
    here = _module(rel)
    out: dict[str, list[str]] = {}
    for name, fn in functions(tree):
        door = doors.get((rel, name))
        if door is None:
            continue
        scope = _ReadScope.of(fn, Door(door.what, door.checked, door.homes, door.aside))
        imports = {**module_imports, **_imports(_own(fn))}
        problems: list[str] = []
        for node in _own(fn):
            if not isinstance(node, ast.Call):
                continue
            for what, where, must in _read_sinks(node, imports, here, known):
                if where is None:
                    continue
                if must == "home" and not scope.plain(where):
                    problems.append(f"{what} at line {node.lineno} is handed a home built there")
                elif must == "path" and not (scope.checked(where) or scope.aside(where)):
                    problems.append(f"{what} at line {node.lineno} reads at a path not checked")
            if _dotted(node.func).rsplit(".", 1)[-1] in GUARDS and node.args:
                if not scope.plain(node.args[0]):
                    problems.append(f"the check at line {node.lineno} starts below a built path")
        if problems:
            out[name] = problems
    return out


def read_unaccounted(tree: ast.AST, rel: str) -> list[str]:
    """The functions of *rel* that read and are in no read table."""
    accounted = {
        name for where, name in (*READ_DOORS, *READ_HAND_READ, *READ_ELSEWHERE) if where == rel
    }
    module_imports = _imports(list(ast.walk(tree)))
    out = []
    for name, fn in functions(tree):
        if name in accounted:
            continue
        imports = {**module_imports, **_imports(_own(fn))}
        if any(
            any(True for _ in _read_sinks(node, imports, _module(rel), {}))
            for node in _own(fn)
            if isinstance(node, ast.Call)
        ):
            out.append(name)
    return out


def _read_trees() -> dict[str, ast.AST]:
    rels = {*READ_MODULES, *(r for r, _ in READ_DOORS), *(r for r, _ in READ_HAND_READ)}
    return {rel: ast.parse((SRC / rel).read_text(encoding="utf-8")) for rel in sorted(rels)}


def test_every_read_an_export_makes_takes_its_path_from_the_check():
    trees = _read_trees()
    known = readers(trees)
    offenders = {
        f"{rel}::{fn}": problems
        for rel, tree in trees.items()
        for fn, problems in stray_reads(tree, rel, known, READ_DOORS).items()
    }
    assert not offenders, (
        "these read the home for an export at a path that did not come from "
        "durability.home_paths.export_path, so a link the home holds there would send what it "
        f"leads to out of this machine: {offenders}"
    )


def test_every_function_of_the_export_modules_that_reads_is_accounted_for():
    trees = _read_trees()
    loose = {rel: names for rel in READ_MODULES if (names := read_unaccounted(trees[rel], rel))}
    assert not loose, (
        "these read and are in no read table of this census: a read of the home for an export "
        f"goes in READ_DOORS (and takes its path from export_path), anything else in "
        f"READ_ELSEWHERE, with what it reads: {loose}"
    )


def test_every_read_table_names_a_function_that_is_there():
    trees = _read_trees()
    for rel, name in (*READ_DOORS, *READ_HAND_READ, *READ_ELSEWHERE):
        assert name in dict(functions(trees[rel])), f"{rel}::{name} is gone; update the census"


def test_each_reader_that_walks_asks_what_it_meets():
    """A reader read by hand that walks a folder, or names a link at its own file, asks
    ``not_read`` of each thing it reads there: one that did not would read through a link it met."""
    trees = _read_trees()
    for (rel, name), (_params, asks) in READ_HAND_READ.items():
        if not asks:
            continue
        fn = dict(functions(trees[rel]))[name]
        calls = {_dotted(n.func) for n in _own(fn) if isinstance(n, ast.Call)}
        assert "not_read" in calls, f"{rel}::{name} reads what it meets without asking not_read"


def test_the_read_scan_sees_the_doors_read():
    """Positive control on the real tree: every read door reads, through a reader it hands a
    path to, a read of its own, or the check."""
    trees = _read_trees()
    known = readers(trees)
    for (rel, name), door in READ_DOORS.items():
        fn = dict(functions(trees[rel]))[name]
        imports = {**_imports(list(ast.walk(trees[rel]))), **_imports(_own(fn))}
        calls = [n for n in _own(fn) if isinstance(n, ast.Call)]
        reads = [s for node in calls for s in _read_sinks(node, imports, _module(rel), known)]
        guarded = any(_dotted(n.func).rsplit(".", 1)[-1] in GUARDS for n in calls)
        assert reads or guarded, f"the scan sees {rel}::{name} ({door.what}) read nothing"


_READ_PLANTED = """
import os

from personalclaw.durability import home_paths, shards


def walks_a_store_built_on_the_spot(home):
    for _ in os.walk(home / "tasks"):
        pass


def reads_a_file_built_below_a_checked_folder(home):
    folder = home_paths.export_path(home, "tasks")
    (folder / "t1.json").read_bytes()


def hands_a_reader_a_built_path(home, entry):
    shards.read_entity_dir(entry, home / entry.path)


def a_check_that_starts_below_a_built_path(home, entry):
    home_paths.export_path(home / "workspace", "knowledge.db").read_bytes()


def a_new_read_of_its_own(home):
    (home / "machine_id").read_text()
"""

_READ_THROUGH = """
from personalclaw.durability import home_paths, shards


def reads_a_checked_store(home, entry):
    src = home_paths.export_path(home, entry.path)
    shards.read_entity_dir(entry, src)
    files = shards.jsonl_files(entry, src, {})
    files = [f for f in files if f.name]
    for path in files:
        path.read_text()
    src.read_bytes()


def reads_its_own_side(home, out_dir):
    (out_dir / "manifest.json").read_text()
"""

_READ_PLANTED_DOORS = {
    (_REL, name): ReadDoor("planted", homes=("home",), aside=("out_dir",))
    for name in (
        "walks_a_store_built_on_the_spot",
        "reads_a_file_built_below_a_checked_folder",
        "hands_a_reader_a_built_path",
        "a_check_that_starts_below_a_built_path",
        "a_new_read_of_its_own",
        "reads_a_checked_store",
        "reads_its_own_side",
    )
}
_PLANTED_READERS = {
    "personalclaw.durability.shards.read_entity_dir": Writer(checked=((1, "root"),), homes=()),
}


def test_the_read_rail_catches_each_way_around_the_check():
    found = stray_reads(ast.parse(_READ_PLANTED), _REL, _PLANTED_READERS, _READ_PLANTED_DOORS)
    assert set(found) == {
        "walks_a_store_built_on_the_spot",
        "reads_a_file_built_below_a_checked_folder",
        "hands_a_reader_a_built_path",
        "a_check_that_starts_below_a_built_path",
        "a_new_read_of_its_own",
    }, found


def test_the_read_rail_lets_each_checked_shape_through():
    found = stray_reads(ast.parse(_READ_THROUGH), _REL, _PLANTED_READERS, _READ_PLANTED_DOORS)
    assert found == {}, found


def test_a_new_function_that_reads_is_caught_until_it_is_accounted_for():
    assert "a_new_read_of_its_own" in read_unaccounted(
        ast.parse(_READ_PLANTED), "durability/shards.py"
    )


# ── locks: every lock in the home is opened by open_lock ─────────────────────────────────────────

#: The one opener of a lock.
_OPENER = "personalclaw.durability.home_paths.open_lock"
#: Calls that take an advisory lock on the file they are handed, and the flags that take one.
_LOCKERS = frozenset({"fcntl.flock", "fcntl.lockf"})
_TAKES = frozenset({"LOCK_EX", "LOCK_SH"})
#: How many places took a lock when this was written: the scan must see at least that many.
LOCK_SITE_FLOOR = 22


def _takes_a_lock(call: ast.Call) -> bool:
    if _dotted(call.func) not in _LOCKERS or len(call.args) < 2:
        return False
    return any(
        (isinstance(n, ast.Attribute) and n.attr in _TAKES)
        or (isinstance(n, ast.Name) and n.id in _TAKES)
        for n in ast.walk(call.args[1])
    )


def _locked_file(node: ast.AST) -> ast.AST:
    """The file a lock is taken on: *node*, or what its ``fileno()`` is called on."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fileno"
        and not node.args
    ):
        return node.func.value
    return node


def _with_lambdas(fn: ast.AST) -> list[ast.AST]:
    """*fn*'s nodes and those of a lambda in it (a lock taken on a worker thread), without those of
    a function nested in it."""
    out: list[ast.AST] = []
    pending = list(ast.iter_child_nodes(fn))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        out.append(node)
        pending.extend(ast.iter_child_nodes(node))
    return out


def _the_opener(node: ast.AST | None, imports: dict[str, str], returners: set[str]) -> bool:
    """Whether *node* calls ``open_lock``, or a function or method of the module that returns what
    it opened."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return imports.get(func.id) == _OPENER or func.id in returners
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if f"{imports.get(func.value.id, '')}.{func.attr}" == _OPENER:
            return True
        return func.value.id == "self" and func.attr in returners
    return False


def lock_strays(tree: ast.AST, rel: str) -> list[str]:
    """Each place *tree* takes a lock on a file ``open_lock`` did not open, as ``function:line``."""
    module_imports = _imports(list(ast.walk(tree)))
    returners: set[str] = set()
    for name, fn in functions(tree):
        imports = {**module_imports, **_imports(_own(fn))}
        returned = [n.value for n in _own(fn) if isinstance(n, ast.Return) and n.value is not None]
        if returned and all(_the_opener(r, imports, set()) for r in returned):
            returners.add(name.rsplit(".", 1)[-1])
    opened: set[str] = set()  # the attributes a lock's file is kept on, each set from the opener
    for name, fn in functions(tree):
        imports = {**module_imports, **_imports(_own(fn))}
        for node in _own(fn):
            if isinstance(node, ast.Assign) and _the_opener(node.value, imports, returners):
                opened |= {t.attr for t in node.targets if isinstance(t, ast.Attribute)}
    out: list[str] = []
    for name, fn in functions(tree):
        scope = _Scope.of(fn, Door("a lock"))
        imports = {**module_imports, **_imports(_own(fn))}
        for node in _with_lambdas(fn):
            if not (isinstance(node, ast.Call) and _takes_a_lock(node)):
                continue
            held = _locked_file(node.args[0])
            if isinstance(held, ast.Name):
                found = scope.bindings.get(held.id, [])
                ok = bool(found) and all(
                    how in {"value", "with"} and _the_opener(value, imports, returners)
                    for how, value in found
                )
            elif isinstance(held, ast.Attribute) and _dotted(held.value) == "self":
                ok = held.attr in opened
            else:
                ok = False
            if not ok:
                out.append(f"{rel}::{name}:{node.lineno}")
    return out


def _lock_trees() -> dict[str, ast.AST]:
    """Every module of the package that names ``fcntl``: where a lock can be taken."""
    out: dict[str, ast.AST] = {}
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "fcntl" in text:
            out[path.relative_to(SRC).as_posix()] = ast.parse(text)
    return out


def _lock_sites(trees: dict[str, ast.AST]) -> int:
    return sum(
        1
        for tree in trees.values()
        for _, fn in functions(tree)
        for node in _with_lambdas(fn)
        if isinstance(node, ast.Call) and _takes_a_lock(node)
    )


def test_every_lock_in_the_home_is_opened_by_open_lock():
    trees = _lock_trees()
    strays = [site for rel, tree in trees.items() for site in lock_strays(tree, rel)]
    assert not strays, (
        "these take a lock on a file not opened by durability.home_paths.open_lock, so a link at "
        "the lock's name would be followed (and a lock opened to write empties what it leads to): "
        f"{strays}"
    )


def test_the_lock_scan_sees_every_lock():
    """Positive control: the scan finds the locks the package takes. A scan broken into finding
    none would pass the rail over any lock."""
    sites = _lock_sites(_lock_trees())
    assert sites >= LOCK_SITE_FLOOR, f"the scan sees {sites} locks taken (floor {LOCK_SITE_FLOOR})"


_LOCK_PLANTED = """
import fcntl

from personalclaw.durability.home_paths import open_lock


def opened_to_write(path):
    with open(path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)


def opened_to_append_by_its_number(path):
    handle = path.open("a")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def opened_by_an_opener_of_its_own(path):
    with open_my_lock(path) as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)


def open_my_lock(path):
    return open(path, "a+")


class Held:
    def take(self, path):
        self._handle = open(path, "r")
        fcntl.flock(self._handle, fcntl.LOCK_EX)
"""

_LOCK_THROUGH = """
import asyncio
import fcntl

from personalclaw.durability import home_paths
from personalclaw.durability.home_paths import open_lock


def with_the_opener(path):
    with open_lock(path) as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        fcntl.flock(handle, fcntl.LOCK_UN)


def by_its_number(path):
    handle = home_paths.open_lock(path)
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


class Held:
    def _open(self, path):
        try:
            return open_lock(path)
        except OSError:
            raise

    def take(self, path):
        self._fd = self._open(path)
        fcntl.flock(self._fd, fcntl.LOCK_EX)

    async def take_on_a_thread(self):
        await asyncio.to_thread(lambda: fcntl.flock(self._fd, fcntl.LOCK_EX))
"""


def test_the_lock_rail_catches_each_lock_not_opened_by_the_opener():
    found = lock_strays(ast.parse(_LOCK_PLANTED), _REL)
    assert {site.split("::")[1].split(":")[0] for site in found} == {
        "opened_to_write",
        "opened_to_append_by_its_number",
        "opened_by_an_opener_of_its_own",
        "Held.take",
    }, found


def test_the_lock_rail_lets_each_lock_the_opener_opened_through():
    assert lock_strays(ast.parse(_LOCK_THROUGH), _REL) == []
