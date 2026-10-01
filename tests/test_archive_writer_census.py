"""Every archive and export PersonalClaw writes goes through the private writer.

A snapshot holds the audit log's signing key and every memory; a shard export, a project archive and
a memory export hold the user's records. Each is readable by its owner alone from the moment it
exists, whatever the umask and wherever it is written, and the one writer that makes that so is
``atomic_write.private_file`` (with ``write_private_file`` for a whole payload and
``make_private_dirs`` for the folders): the bytes go to a temp file beside the destination that is
created 0600, and the finished file is renamed into place. A writer that opens the destination
itself and tightens it afterwards leaves it readable while it is written — a snapshot's temp file
was ``-rw-r--r--`` for the minute its 400 MB took.

The scan reads every module of the package and finds three shapes that go around it:

* **an archive opened for writing on a path** — ``tarfile``, ``zipfile``, ``gzip``, ``bz2`` or
  ``lzma`` writing to a file it opens itself, rather than to a buffer in memory or to the private
  writer's stream;
* **an archive built in memory, then written another way** — a function that fills a buffer with
  an archive and writes a file with anything but the private writer;
* **a declared writer that writes around it** — each function in :data:`WRITERS` writes an archive,
  an export or the manifest beside one, and may write a file, make a folder or set a mode only
  through the private writer.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "personalclaw"
SHARED_WRITER = SRC / "atomic_write.py"

#: The private writer's three entry points.
HELPERS = frozenset({"private_file", "write_private_file", "make_private_dirs"})

#: Every function that writes an archive, an export or the manifest beside one, and what it holds.
WRITERS: dict[tuple[str, str], str] = {
    ("snapshot.py", "_write_archive"): "a snapshot: the audit key and every memory",
    ("durability/archive.py", "write_sidecar"): "a snapshot's manifest, beside it",
    ("durability/shards.py", "export_shards"): "the folder of a shard export",
    ("durability/shards.py", "clear_shards"): "the folder of a shard export",
    ("durability/shards.py", "_write_shard"): "a shard of the user's records",
    ("durability/shards.py", "_stage_db_copy"): "a whole database, staged for a sync",
    ("durability/shards.py", "_write_manifest"): "a shard export's manifest",
    ("cli_project.py", "_export"): "a project archive",
    ("cli_commands.py", "_memory_cmd"): "every memory, exported to a file",
    ("workflows/project_archive.py", "_open_archive"): "a decrypted project archive, staged",
}

#: ``module.function`` of every archive opener, and the keywords that can name what it writes into.
_ARCHIVE_OPENERS = {
    "tarfile.open",
    "tarfile.TarFile",
    "zipfile.ZipFile",
    "gzip.open",
    "gzip.GzipFile",
    "bz2.open",
    "bz2.BZ2File",
    "lzma.open",
    "lzma.LZMAFile",
}
_FILE_KEYWORDS = ("fileobj", "file", "name", "filename")

#: Calls that create a file, make a folder or set a mode: methods by their name, the home's own
#: writers however they were imported, and the rest by their module.
_WRITER_ATTRS = frozenset({"write_bytes", "write_text", "touch", "mkdir", "chmod", "fchmod"})
_HOME_WRITERS = frozenset(
    {"atomic_write", "atomic_write_bytes", "atomic_json_write", "open_streamed", "_atomic_write"}
)
_WRITER_CALLS = frozenset(
    {
        "os.open",
        "os.makedirs",
        "os.mkdir",
        "os.chmod",
        "os.fchmod",
        "shutil.copy",
        "shutil.copy2",
        "shutil.copyfile",
        "shutil.copytree",
        "shutil.move",
        "tempfile.mkstemp",
        "tempfile.NamedTemporaryFile",
    }
)


def _dotted(node: ast.AST) -> str:
    """``name`` or ``a.b.c`` for a name or attribute chain; ``""`` for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else ""
    return ""


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


def _is_helper(call: ast.Call) -> bool:
    return _dotted(call.func).rsplit(".", 1)[-1] in HELPERS


def _is_buffer(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _dotted(node.func) in {"io.BytesIO", "BytesIO"}


def _mode_of(call: ast.Call, *, default: str) -> str | None:
    """The mode an opener was given: its literal, ``default`` when none, ``None`` when computed."""
    node: ast.AST | None = call.args[1] if len(call.args) >= 2 else None
    for kw in call.keywords:
        if kw.arg == "mode":
            node = kw.value
    if node is None:
        return default
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _writes(mode: str | None) -> bool:
    return mode is None or bool(set(mode) & set("wax+"))


def _archive_writes(nodes: list[ast.AST]) -> list[tuple[ast.Call, ast.AST | None]]:
    """Every archive opened for writing, with the node naming what it writes into."""
    out: list[tuple[ast.Call, ast.AST | None]] = []
    for node in nodes:
        if not (isinstance(node, ast.Call) and _dotted(node.func) in _ARCHIVE_OPENERS):
            continue
        default = "rb" if _dotted(node.func).startswith(("gzip", "bz2", "lzma")) else "r"
        if not _writes(_mode_of(node, default=default)):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords}
        target = keywords.get("fileobj")
        if target is None and node.args:
            target = node.args[0]
        if target is None:
            target = next((keywords[k] for k in _FILE_KEYWORDS if k in keywords), None)
        out.append((node, target))
    return out


def _bound(nodes: list[ast.AST]) -> tuple[set[str], set[str]]:
    """``(buffers, streams)``: names bound to an in-memory buffer, and to the private writer."""
    buffers: set[str] = set()
    streams: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Assign) and _is_buffer(node.value):
            buffers |= {_dotted(t) for t in node.targets} - {""}
        if isinstance(node, ast.withitem) and isinstance(node.context_expr, ast.Call):
            if _is_helper(node.context_expr) and node.optional_vars is not None:
                streams.add(_dotted(node.optional_vars))
    return buffers, streams


def _file_writes(nodes: list[ast.AST]) -> list[str]:
    """Each call in *nodes* that writes a file, makes a folder or sets a mode."""
    out: list[str] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        if name == "open" and _writes(_mode_of(node, default="r")):
            out.append(f"open:{node.lineno}")
        elif name in _WRITER_CALLS or name.rsplit(".", 1)[-1] in _HOME_WRITERS:
            out.append(f"{name}:{node.lineno}")
        elif isinstance(node.func, ast.Attribute) and node.func.attr in _WRITER_ATTRS:
            out.append(f".{node.func.attr}:{node.lineno}")
    return out


def around(tree: ast.AST) -> dict[str, list[str]]:
    """``function → what it does around the private writer`` for the first two shapes."""
    out: dict[str, list[str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = _own(fn)
        buffers, streams = _bound(nodes)
        problems: list[str] = []
        in_memory = False
        for call, target in _archive_writes(nodes):
            if target is not None and (_is_buffer(target) or _dotted(target) in buffers):
                in_memory = True
            elif target is None or _dotted(target) not in streams:
                problems.append(f"an archive opened for writing on a path at line {call.lineno}")
        if in_memory:
            problems += [f"an archive built in memory, then {w}" for w in _file_writes(nodes)]
        if problems:
            out.setdefault(fn.name, []).extend(problems)
    return out


def declared(tree: ast.AST, names: set[str]) -> dict[str, list[str]]:
    """For each function named in *names*: what it writes around the private writer, or that it
    never calls it."""
    out: dict[str, list[str]] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name not in names:
            continue
        nodes = _own(fn)
        problems = _file_writes(nodes)
        if not any(isinstance(n, ast.Call) and _is_helper(n) for n in nodes):
            problems.append("never calls the private writer")
        out[fn.name] = problems
    return out


def _modules() -> list[tuple[str, ast.AST]]:
    out = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts or path == SHARED_WRITER:
            continue
        out.append((path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8"))))
    return out


def _archive_writer_functions(modules: list[tuple[str, ast.AST]]) -> set[tuple[str, str]]:
    found = set()
    for rel, tree in modules:
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and _archive_writes(
                _own(fn)
            ):
                found.add((rel, fn.name))
    return found


def test_no_archive_is_written_around_the_private_writer():
    modules = _modules()
    assert len(modules) > 500, f"the scan read {len(modules)} modules — it did not see the package"
    offenders = {
        f"{rel}::{fn}": problems for rel, tree in modules for fn, problems in around(tree).items()
    }
    assert not offenders, (
        "these write an archive without the private writer (atomic_write.private_file / "
        f"write_private_file), which makes it 0600 from its first byte and atomic: {offenders}"
    )
    # Positive control on the real tree: the scan sees the archive writers there are. A scanner
    # broken into seeing none would pass the assertion above over a tree full of them.
    seen = _archive_writer_functions(modules)
    for site in (
        ("snapshot.py", "_write_archive"),
        ("portability.py", "create_export_zip"),
        ("workflows/project_archive.py", "write_archive"),
        ("packs/build.py", "build_pack"),
        ("packs/bundled/__init__.py", "build_bundled"),
        ("packs/onelink.py", "materialize"),
    ):
        assert site in seen, f"the scan no longer sees {site} write an archive"


def test_every_declared_writer_writes_only_through_the_private_writer():
    by_module = dict(_modules())
    offenders: dict[str, list[str]] = {}
    for (rel, fn), holds in sorted(WRITERS.items()):
        assert rel in by_module, f"{rel} is gone; update WRITERS"
        found = declared(by_module[rel], {fn})
        assert fn in found, f"{rel}::{fn} ({holds}) is gone; update WRITERS"
        if found[fn]:
            offenders[f"{rel}::{fn} ({holds})"] = found[fn]
    assert not offenders, (
        "these write an archive or an export around the private writer "
        f"(atomic_write.private_file, write_private_file, make_private_dirs): {offenders}"
    )


# ── positive controls: the shapes the rail exists to catch, and the ones it lets through ─────

_AROUND = """
import bz2
import gzip
import io
import tarfile
import zipfile

from personalclaw.atomic_write import atomic_write_bytes


def tar_on_a_path(stage, outfile):
    tmp = outfile.with_suffix(".tar.gz.tmp")
    with tarfile.open(str(tmp), "w:gz") as tar:
        tar.add(str(stage))
    tmp.rename(outfile)


def zip_on_a_path(out_path, members):
    with zipfile.ZipFile(str(out_path), "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a", b"")


def gzip_on_a_path(path, data):
    with gzip.open(path, "wb") as fh:
        fh.write(data)


def bz2_by_keyword(path, data):
    with bz2.open(filename=path, mode="w") as fh:
        fh.write(data)


def tar_with_a_computed_mode(path, mode):
    with tarfile.open(str(path), mode) as tar:
        return tar


def built_in_memory_then_written_in_place(out):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a", b"")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())


def built_in_memory_then_written_by_the_home_writer(out):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add("x")
    atomic_write_bytes(out, buf.getvalue())


def built_in_memory_then_opened(out):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a", b"")
    with open(out, "wb") as fh:
        fh.write(buf.getvalue())
"""

_THROUGH = """
import io
import tarfile
import zipfile

from personalclaw.atomic_write import private_file, write_private_file


def streamed(stage, outfile):
    with private_file(outfile) as fh:
        with tarfile.open(name=str(outfile), mode="w:gz", fileobj=fh) as tar:
            tar.add(str(stage))


def zipped_onto_the_stream(out_path):
    with private_file(out_path, fsync=False) as fh, zipfile.ZipFile(fh, "w") as zf:
        zf.writestr("a", b"")


def returned():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a", b"")
    return buf.getvalue()


def in_memory_then_private(out):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a", b"")
    write_private_file(out, buf.getvalue())


def read(path):
    with tarfile.open(str(path), "r:gz") as tar:
        return tar.getnames()


def read_by_default(path):
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()
"""

_DECLARED = """
import os
import shutil

from personalclaw.atomic_write import atomic_write, make_private_dirs, write_private_file


def write_then_tighten(out, raw):
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    out.chmod(0o600)


def through_the_home_writer(out, text):
    atomic_write(out, text)


def by_hand(out, raw):
    with open(out, "wb") as fh:
        fh.write(raw)


def copied(src, out):
    shutil.copy2(src, out)


def tightened(out):
    os.chmod(out, 0o600)


def delegates_elsewhere(out, raw):
    return raw


def private(out, raw):
    make_private_dirs(out.parent)
    write_private_file(out, raw)


def reads_too(out, raw):
    with open(out.with_suffix(".old"), "rb") as fh:
        before = fh.read()
    write_private_file(out, before + raw)
"""


def test_the_rail_catches_each_archive_written_around_the_private_writer():
    found = around(ast.parse(_AROUND))
    assert set(found) == {
        "tar_on_a_path",
        "zip_on_a_path",
        "gzip_on_a_path",
        "bz2_by_keyword",
        "tar_with_a_computed_mode",
        "built_in_memory_then_written_in_place",
        "built_in_memory_then_written_by_the_home_writer",
        "built_in_memory_then_opened",
    }, found
    assert len(found["built_in_memory_then_written_in_place"]) == 2, found


def test_the_rail_lets_through_what_goes_through_the_private_writer_or_stays_in_memory():
    assert around(ast.parse(_THROUGH)) == {}


def test_the_rail_catches_a_declared_writer_that_writes_around_the_private_writer():
    names = {
        "write_then_tighten",
        "through_the_home_writer",
        "by_hand",
        "copied",
        "tightened",
        "delegates_elsewhere",
        "private",
        "reads_too",
    }
    found = declared(ast.parse(_DECLARED), names)
    assert set(found) == names, found
    caught = {name for name, problems in found.items() if problems}
    assert caught == names - {"private", "reads_too"}, found
    assert len(found["write_then_tighten"]) == 4, found  # mkdir, write_bytes, chmod, no helper
    assert found["delegates_elsewhere"] == ["never calls the private writer"], found
