"""No code of ours parses the source tree while pytest collects (#3927).

#3927's last Done-when line: "Collecting the whole suite with `ast.parse` patched to count calls
records no parse of a repository file before the warm-up hook runs." A repository file is read
as a file under one of the source index's roots (``source_index.ROOTS``: ``src/personalclaw``,
``web/src``, ``docs``, ``.github/workflows``). Two callers parse such files during collection
and are outside the line, because neither is our code parsing the tree:

- pytest's assertion rewrite (``_pytest.assertion.rewrite._rewrite_test``), which parses a test
  module so that it can load it;
- Hypothesis's local-constants scan
  (``hypothesis.internal.conjecture.providers._get_local_constants``), which its own pytest
  plugin runs once at the end of collection, outside every test's timeout.

A parse is excused only when one of its calling frames is that exact function of that exact
module, read from the frame's globals and code object, never from a file path: a function of
the same name anywhere else is ours, and counts.

Load it on a whole-suite collection, in one process:

    PYTHONPATH=tests pytest --collect-only -qq -n0 -p collection_parse_guard tests

It patches ``ast.parse`` when pytest loads it, before any conftest is imported, and stops
counting when collection ends, once every conftest's and plugin's collection-finish hook has
run, a ``trylast`` one too. A parse counts when its ``filename`` lies under a root or its
source is exactly one of the roots' ``.py`` files. It then exits 1 naming each parse and the
frame that made it, or prints ``collection-parse-guard: clean`` with what it excused. What it
cannot see: a ``compile(..., ast.PyCF_ONLY_AST)``; a module that bound ``ast.parse`` to a name
of its own before the plugin loaded; and a parse of part of a root file under no root's name,
such as one function's source (``ast.parse(textwrap.dedent(inspect.getsource(fn)))``), since
only a whole file's text is known by its digest.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

import pytest
import source_index

#: The two callers outside the line, as (module ``__name__``, function name).
EXCUSED: frozenset[tuple[str, str]] = frozenset(
    {
        ("_pytest.assertion.rewrite", "_rewrite_test"),
        ("hypothesis.internal.conjecture.providers", "_get_local_constants"),
    }
)


@dataclass(frozen=True)
class Parse:
    filename: str
    digest: str
    #: The nearest calling frame outside this module, as ``module.function (file:line)``.
    caller: str


_real_parse = ast.parse
PARSES: list[Parse] = []
EXCUSED_COUNT: dict[tuple[str, str], int] = {key: 0 for key in EXCUSED}
COUNTING = [True]


def _digest(source: object) -> str:
    if isinstance(source, str):
        source = source.encode("utf-8", "surrogatepass")
    if isinstance(source, (bytes, bytearray)):
        return hashlib.sha256(bytes(source)).hexdigest()
    return ""  # an AST passed back in names no file


def _excused_by(frame: FrameType | None) -> tuple[str, str] | None:
    while frame is not None:
        key = (str(frame.f_globals.get("__name__", "")), frame.f_code.co_name)
        if key in EXCUSED:
            return key
        frame = frame.f_back
    return None


def _caller(frame: FrameType | None) -> str:
    while frame is not None and frame.f_globals.get("__name__") == __name__:
        frame = frame.f_back
    if frame is None:
        return "?"
    module = frame.f_globals.get("__name__", "?")
    return f"{module}.{frame.f_code.co_name} ({frame.f_code.co_filename}:{frame.f_lineno})"


def _counting_parse(source, filename="<unknown>", *args, **kwargs):
    if COUNTING[0]:
        frame = sys._getframe(1)
        excused = _excused_by(frame)
        if excused is not None:
            EXCUSED_COUNT[excused] += 1
        else:
            PARSES.append(Parse(str(filename), _digest(source), _caller(frame)))
    return _real_parse(source, filename, *args, **kwargs)


ast.parse = _counting_parse


def _root_sources(root: Path) -> dict[str, str]:
    """Each ``.py`` file under the index's roots of *root*, by the digest of its bytes. The real
    tree's walks keep their floors; a planted tree's have none."""
    floors = None if root == source_index.REPO else dict.fromkeys(source_index.FLOORS, 0)
    index = source_index.SourceIndex(root, floors)
    found: dict[str, str] = {}
    for name in ("package", "web", "docs", "workflows"):
        if not (root / source_index.ROOTS[name]).is_dir():
            continue
        for path in index.files(name):
            if path.suffix == ".py":
                found[hashlib.sha256(path.read_bytes()).hexdigest()] = index.rel(path)
    return found


def violations(root: Path, parses: list[Parse]) -> list[str]:
    """Each counted parse of a file under one of *root*'s index roots."""
    roots = [(root / r).resolve() for r in sorted(set(source_index.ROOTS.values()))]
    sources = _root_sources(root)
    bad: list[str] = []
    for parse in parses:
        named = Path(parse.filename)
        if not named.is_absolute():
            named = root / named
        try:
            under = any(named.resolve().is_relative_to(r) for r in roots)
        except (OSError, ValueError):
            under = False
        which = parse.filename if under else sources.get(parse.digest)
        if under or which:
            bad.append(f"{which} was parsed during collection by {parse.caller}")
    return bad


def pytest_configure(config: pytest.Config) -> None:
    if getattr(config.option, "numprocesses", None):
        raise pytest.UsageError(
            "collection_parse_guard counts one process's parses: run it with -n0"
        )


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_finish(session: pytest.Session) -> Generator[None, object, object]:
    # A tryfirst wrapper, registered before every conftest and entry-point plugin: what follows
    # the yield runs after every collection-finish hook, trylast ones too, and after every other
    # wrapper of one but a tryfirst wrapper registered after this plugin.
    result = yield
    COUNTING[0] = False
    bad = violations(Path(session.config.rootpath), PARSES)
    excused = ", ".join(f"{m}.{f} {n}" for (m, f), n in sorted(EXCUSED_COUNT.items()))
    if bad:
        pytest.exit(
            f"collection-parse-guard: {len(bad)} parse(s) of the source tree before the "
            "warm-up:\n" + "\n".join(f"  {b}" for b in bad),
            returncode=1,
        )
    print(
        f"\ncollection-parse-guard: clean; {len(session.items)} items, "
        f"{len(PARSES)} other parses, excused {excused}"
    )
    return result
