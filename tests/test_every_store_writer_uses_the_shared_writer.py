"""Every file PersonalClaw writes whole goes through the one writer, ``atomic_write``.

🔴 ``TriggerStore._write`` wrote ``triggers.json`` through a fixed ``triggers.json.tmp`` and renamed
it into place itself: the store kept the umask's mode (0644 on the usual umask), where every file
the shared writer writes in the home is 0600, and no post-write subscriber heard of the write.
Some thirty other writers did one of the two things it did. Most wrote a JSON document in place
with ``write_text``, so a crash midway left it truncated — a task, a project, a theme, the
rollback ledger of a pack import; the rest renamed a temp file of their own into place.

The shared writer (``atomic_write``, ``atomic_write_bytes``, ``atomic_json_write``) writes a unique
temp file and renames it, makes the file 0600 in a 0700 directory under the home, and announces the
write. This rail finds the two shapes a writer that goes around it takes:

* **a JSON document written in place** — ``write_text``/``write_bytes`` of what ``json.dumps``
  made, or ``json.dump`` into a handle;
* **a file renamed into place by its writer** — ``os.replace``, ``os.rename``, ``Path.replace`` or
  ``Path.rename`` of a file the same function wrote, or took from ``tempfile``.

A writer the shared writer cannot serve is listed below with why. The list must be exactly what the
scan finds, both ways: a new writer that goes around the shared one fails, and so does a listed one
that has since moved onto it.
"""

from __future__ import annotations

import ast
import stat
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
SHARED_WRITER = SRC / "atomic_write.py"

IN_PLACE = "writes a JSON document in place"
OWN_RENAME = "renames its own write into place"

#: The writers the shared writer cannot serve, and why.
EXEMPT: dict[tuple[str, str], str] = {
    ("dashboard/handlers/files.py", "api_file_write"): (
        "the owner's own file, where they keep it: a save keeps the mode it has, so a script "
        "stays executable, where the shared writer would make any file in the home 0600"
    ),
    ("dashboard/handlers/files.py", "api_file_upload"): (
        "a file the owner uploads into a folder they chose, streamed to disk chunk by chunk and "
        "capped as it arrives; the shared writer takes the whole content in memory"
    ),
    ("uploads/store.py", "write_part"): (
        "one part of a resumable upload, streamed to disk chunk by chunk; the shared writer takes "
        "the whole content in memory"
    ),
    ("service/macos.py", "_write_plist_atomic"): (
        "launchd's own file, in ~/Library/LaunchAgents, outside the home: not a store"
    ),
    ("knowledge_providers/pack_parse.py", "run_parse_script"): (
        "the input of one sandboxed script run, in a temp file tempfile creates 0600 for it"
    ),
    ("schedule_script.py", "run_script_sandboxed"): (
        "the input of one sandboxed script run, in a temp file tempfile creates 0600 for it"
    ),
}

_WRITES = ("write_text", "write_bytes")
_TEMP_FACTORIES = ("mkstemp", "NamedTemporaryFile")
_RENAMES = ("replace", "rename")


def _name(node: ast.AST) -> str:
    """A name or a dotted attribute chain (``path``, ``self._path``); ``""`` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _name(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


def _is_dumps(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dumps"
        and _name(node.func.value) == "json"
    )


def _mode(call: ast.Call) -> str:
    mode: object = ""
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        mode = call.args[1].value
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    return mode if isinstance(mode, str) else ""


def _own(fn: ast.AST) -> list[ast.AST]:
    """*fn*'s nodes, without those of a function nested in it, which are that function's."""
    out: list[ast.AST] = []
    todo = list(ast.iter_child_nodes(fn))
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        out.append(node)
        todo.extend(ast.iter_child_nodes(node))
    return out


def _shapes(fn: ast.AST) -> set[str]:
    """Which of the two shapes *fn* takes."""
    nodes = _own(fn)
    dumped: set[str] = set()
    written: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Assign):
            targets = {_name(t) for t in node.targets} - {""}
            if any(_is_dumps(sub) for sub in ast.walk(node.value)):
                dumped |= targets
            value = node.value
            if (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr in _TEMP_FACTORIES
            ):
                for target in node.targets:
                    written |= {_name(sub) for sub in ast.walk(target)} - {""}
        if isinstance(node, ast.withitem):
            call = node.context_expr
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                if call.func.attr in _TEMP_FACTORIES and node.optional_vars is not None:
                    written.add(_name(node.optional_vars))
    shapes: set[str] = set()
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _WRITES:
            target = _name(func.value)
            if target:
                written.add(target)
            if node.args and any(
                _is_dumps(sub) or (isinstance(sub, ast.Name) and sub.id in dumped)
                for sub in ast.walk(node.args[0])
            ):
                shapes.add(IN_PLACE)
        if isinstance(func, ast.Attribute) and func.attr == "dump" and _name(func.value) == "json":
            shapes.add(IN_PLACE)
        if isinstance(func, ast.Name) and func.id == "open" and set(_mode(node)) & set("wx"):
            if node.args and _name(node.args[0]):
                written.add(_name(node.args[0]))
    for node in nodes:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in _RENAMES:
            continue
        if _name(node.func.value) == "os" and node.args:
            source = _name(node.args[0])
        elif len(node.args) == 1 and not node.keywords:
            source = _name(node.func.value)  # `tmp.replace(path)`; `str.replace` takes two
        else:
            continue
        if source and source in written:
            shapes.add(OWN_RENAME)
    return shapes


def findings(tree: ast.AST) -> dict[str, set[str]]:
    """``function name → the shapes it takes`` for every function in *tree* that takes one."""
    out: dict[str, set[str]] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            shapes = _shapes(fn)
            if shapes:
                out.setdefault(fn.name, set()).update(shapes)
    return out


def _scan() -> tuple[dict[tuple[str, str], set[str]], int]:
    found: dict[tuple[str, str], set[str]] = {}
    scanned = 0
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts or path == SHARED_WRITER:
            continue
        scanned += 1
        rel = path.relative_to(SRC).as_posix()
        for fn, shapes in findings(ast.parse(path.read_text(encoding="utf-8"))).items():
            found[(rel, fn)] = shapes
    return found, scanned


def test_every_writer_goes_through_the_shared_writer_or_says_why_not():
    found, scanned = _scan()
    assert scanned > 500, f"the scan read {scanned} modules — it did not see the package"
    around = {site: shapes for site, shapes in found.items() if site not in EXEMPT}
    assert not around, (
        "these write a file whole without the shared writer (atomic_write.atomic_write, "
        "atomic_write_bytes or atomic_json_write), which makes the write atomic, 0600 under the "
        "home and announced — use it, or list the writer in EXEMPT with why it cannot: "
        + "; ".join(f"{rel}::{fn} {sorted(shapes)}" for (rel, fn), shapes in sorted(around.items()))
    )
    moved_on = sorted(site for site in EXEMPT if site not in found)
    assert not moved_on, f"no longer goes around the shared writer; drop it from EXEMPT: {moved_on}"


_GOES_AROUND = """
import json
import os
import tempfile


def in_place(path, data):
    path.write_text(json.dumps(data))


def by_a_name(path, data):
    text = json.dumps(data, indent=2) + "\\n"
    path.write_text(text, encoding="utf-8")


def dumped(path, data):
    with open(path, "w") as fh:
        json.dump(data, fh)


def a_fixed_temp(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def its_own_temp(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    os.write(fd, data)
    os.close(fd)
    os.replace(tmp, path)


def outer(path, data):
    def inner():
        path.write_text(json.dumps(data))

    return inner
"""

_GOES_THROUGH = """
import json

from personalclaw.atomic_write import atomic_json_write, atomic_write


def through(path, data):
    atomic_json_write(path, data)


def compact(path, data):
    atomic_write(path, json.dumps(data, sort_keys=True))


def appended(path, row):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\\n")


def moved(src, dst):
    src.replace(dst)


def text(value):
    return value.replace("a", "b")
"""


def test_the_scan_sees_both_shapes_and_not_the_shared_writer():
    """The scan's positive control: every way around the shared writer it names, by the function
    that takes it; a writer through it, an append and a move of a file nobody wrote here, none."""
    assert findings(ast.parse(_GOES_AROUND)) == {
        "in_place": {IN_PLACE},
        "by_a_name": {IN_PLACE},
        "dumped": {IN_PLACE},
        "a_fixed_temp": {OWN_RENAME},
        "its_own_temp": {OWN_RENAME},
        "inner": {IN_PLACE},
    }
    assert findings(ast.parse(_GOES_THROUGH)) == {}


def test_the_trigger_store_is_written_0600_and_announced():
    from personalclaw import atomic_write
    from personalclaw.config.loader import config_dir
    from personalclaw.triggers.models import Trigger
    from personalclaw.triggers.store import TriggerStore

    heard: list[Path] = []
    hook = heard.append
    atomic_write.register_post_write_hook(hook)
    try:
        TriggerStore().upsert(
            Trigger(
                id="clock:nightly",
                name="nightly",
                kind="clock",
                spec={"kind": "cron", "expr": "0 2 * * *"},
                workflow={"inline": {"provider": "notify", "config": {"title": "nightly"}}},
            )
        )
    finally:
        atomic_write.unregister_post_write_hook(hook)
    path = config_dir() / "triggers.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert path in heard, "no post-write subscriber heard of the write"
    assert not path.with_suffix(".json.tmp").exists()
