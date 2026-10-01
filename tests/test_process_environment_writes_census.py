"""Every place PersonalClaw changes its own process environment, and why it may.

The process environment is every child's: a child started with ``env`` left out inherits it, and
every child built on purpose (``env.gateway_env``, ``sandbox.build_child_env``) starts from it. A
change made to it while the process runs is therefore a change to every program started after it,
whatever it was made for. The transcription path once put a folder in front of ``PATH`` to find
ffmpeg, and every tool server, hook and script started afterwards resolved its programs from that
folder.

So every write in ``src/personalclaw`` (``os.environ[k] = v``, ``del os.environ[k]``, its
``update``/``setdefault``/``pop``/``popitem``/``clear``, ``os.putenv``/``os.unsetenv``) is
classified here once, keyed ``file::qualname``:

* ``_AS_A_COMMAND_STARTS``: the command sets up its own environment before it starts anything.
* ``_MIRRORS_THE_CREDENTIAL_STORE``: a named credential that was saved, removed, rotated or loaded
  is mirrored for the trusted children that read it. Never one whose name decides which programs
  run (``env.PROGRAM_RESOLUTION_NAMES``): the last test holds the store to that.
* ``_A_COMMAND_OF_ITS_OWN``: an evaluation command's own process, never the gateway.

No write anywhere names ``PATH`` or ``TMPDIR``, which a child is given as the process started
with them (``env.STARTUP_NAMES``). A new write reds by name until it is classified; a stale entry
reds too.
"""

from __future__ import annotations

import ast
from pathlib import Path

_AS_A_COMMAND_STARTS: dict[str, str] = {
    "_ssl_compat.py::_ensure_ssl_certs": (
        "the system CA bundle for OpenSSL, as the CLI module is first imported"
    ),
    "cli.py::main._load_named_credentials": (
        "a `.env`'s named credentials, before the command runs anything"
    ),
    "cli.py::main": "the folder this install's own resources live in, before anything runs",
    "library_env.py::keep_the_libraries_in_the_home": (
        "the libraries' own settings that keep them in the home, before an app imports one"
    ),
    "gateway_base.py::publish": "the port the gateway bound, once, right after it binds",
}

_MIRRORS_THE_CREDENTIAL_STORE: dict[str, str] = {
    "config/credentials.py::save_credentials": "a named credential just saved",
    "config/credentials.py::delete_credential": "a credential just deleted",
    "config/credential_migration.py::rollback_credentials_to_keychain": (
        "the values a keychain migration's rollback put back in `.env`"
    ),
    "config/loader.py::AppConfig.load_credentials": "the named credentials the store holds",
    "inbound/auth.py::load_surface_token": "an inbound token another process rotated",
}

_A_COMMAND_OF_ITS_OWN: dict[str, str] = {
    "evals/overlay.py::apply_in_child": "an evals matrix cell's own process",
    "evals/skills_bench.py::_suppressing": "the skills bench command's own process",
    "eval/runner.py::EvalRunner._run_scenario_in": "the eval command's own process",
}

_MUTATORS = {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"}


def _src_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "personalclaw"


def _is_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _literal(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def writes(tree: ast.Module) -> list[tuple[str, int, list[str | None]]]:
    """``(qualname, line, names)`` for every write to the process environment in *tree*; a name
    is ``None`` where the code computes it."""
    found: list[tuple[str, int, list[str | None]]] = []

    class V(ast.NodeVisitor):
        def __init__(self) -> None:
            self.q: list[str] = []

        def _scope(self, n: ast.AST) -> None:
            self.q.append(n.name)  # type: ignore[attr-defined]
            self.generic_visit(n)
            self.q.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

        def _add(self, n: ast.stmt | ast.expr, names: list[str | None]) -> None:
            found.append((".".join(self.q) or "<module>", n.lineno, names))

        def _targets(self, n: ast.AST, targets: list[ast.AST]) -> None:
            for t in targets:
                if isinstance(t, ast.Subscript) and _is_environ(t.value):
                    self._add(n, [_literal(t.slice)])

        def visit_Assign(self, n: ast.Assign) -> None:
            self._targets(n, n.targets)
            self.generic_visit(n)

        def visit_AugAssign(self, n: ast.AugAssign) -> None:
            self._targets(n, [n.target])
            if _is_environ(n.target):
                self._add(n, [None])
            self.generic_visit(n)

        def visit_Delete(self, n: ast.Delete) -> None:
            self._targets(n, n.targets)
            self.generic_visit(n)

        def visit_Call(self, n: ast.Call) -> None:
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in _MUTATORS and _is_environ(f.value):
                if f.attr == "update":
                    arg = n.args[0] if n.args else None
                    names: list[str | None] = (
                        [_literal(k) for k in arg.keys] if isinstance(arg, ast.Dict) else [None]
                    )
                    names += [k.arg for k in n.keywords]
                else:
                    names = [_literal(n.args[0]) if n.args else None]
                self._add(n, names)
            elif (
                isinstance(f, ast.Attribute)
                and f.attr in {"putenv", "unsetenv"}
                and isinstance(f.value, ast.Name)
                and f.value.id == "os"
            ):
                self._add(n, [_literal(n.args[0]) if n.args else None])
            self.generic_visit(n)

    V().visit(tree)
    return found


def _census() -> dict[str, list[tuple[int, list[str | None]]]]:
    root = _src_root()
    out: dict[str, list[tuple[int, list[str | None]]]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for qual, line, names in writes(tree):
            out.setdefault(f"{rel}::{qual}", []).append((line, names))
    return out


def test_every_write_to_the_process_environment_says_why_it_may():
    census = set(_census())
    classes = (_AS_A_COMMAND_STARTS, _MIRRORS_THE_CREDENTIAL_STORE, _A_COMMAND_OF_ITS_OWN)
    classified = set().union(*classes)
    unmapped = sorted(census - classified)
    assert not unmapped, (
        "A write to the process environment changes every program started after it. Pass the "
        "value to the child that needs it instead (`env=`), or classify the write in "
        "tests/test_process_environment_writes_census.py:\n" + "\n".join(f"  {k}" for k in unmapped)
    )
    stale = sorted(classified - census)
    assert not stale, "No such write any more; remove:\n" + "\n".join(f"  {k}" for k in stale)
    assert sum(len(c) for c in classes) == len(classified), "a site is in two classes"


def test_nothing_writes_path_or_tmpdir():
    """``PATH`` and ``TMPDIR`` are every child's: a program is found by its absolute path and
    handed over, and a child that needs another ``PATH`` or scratch folder is given it in its own
    environment."""
    from personalclaw.env import STARTUP_NAMES

    assert set(STARTUP_NAMES) == {"PATH", "TMPDIR"}
    hits = [
        f"  {key} (line {line}): {name}"
        for key, sites in sorted(_census().items())
        for line, names in sites
        for name in names
        if name in STARTUP_NAMES
    ]
    assert not hits, "The process environment's PATH or TMPDIR is changed at:\n" + "\n".join(hits)


def test_the_census_sees_every_form_of_write():
    """A positive control: each form, in a function and at module level."""
    src = """
import os
os.environ["A"] = "1"
def f(key, env):
    os.environ[key] = "1"
    del os.environ["B"]
    os.environ.update({"PATH": "/x"}, C="1")
    os.environ.update(env)
    os.environ.setdefault("D", "1")
    os.environ.pop("E", None)
    os.environ.clear()
    os.putenv("F", "1")
    os.unsetenv("G")
    os.environ |= env
    seen = os.environ["H"]
    other = {}
    other["I"] = "1"
"""
    got = [(qual, names) for qual, _line, names in writes(ast.parse(src))]
    assert got == [
        ("<module>", ["A"]),
        ("f", [None]),
        ("f", ["B"]),
        ("f", ["PATH", "C"]),
        ("f", [None]),
        ("f", ["D"]),
        ("f", ["E"]),
        ("f", [None]),
        ("f", ["F"]),
        ("f", ["G"]),
        ("f", [None]),
    ]


def test_the_credential_mirror_never_carries_a_name_that_decides_which_programs_run():
    from personalclaw.config.credentials import mirrored_into_the_environment
    from personalclaw.env import PROGRAM_RESOLUTION_NAMES

    assert "PATH" in PROGRAM_RESOLUTION_NAMES
    assert not [n for n in sorted(PROGRAM_RESOLUTION_NAMES) if mirrored_into_the_environment(n)]
    assert mirrored_into_the_environment("EXAMPLE_SERVICE_TOKEN")
