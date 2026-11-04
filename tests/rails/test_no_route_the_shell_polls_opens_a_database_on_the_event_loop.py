"""No route the shell polls from every page opens a database on the event loop.

The shell reads routes on a timer from whatever page is open: today the loop list, for the Loop
tile's badge (``web/src/app/App.tsx``), and the system read, the heartbeat that says whether the
gateway is there (``web/src/app/gatewayConnection.tsx``). The loop list opened the workflow run
store on the event loop, and the browser harness's gateway stopped answering with its loop's thread
inside that open while a worker closed the same database (``pysqlite3`` closes holding the
interpreter lock: ``test_a_pysqlite3_close_never_runs_beside_an_open``). Whatever the driver, a
database opened on the loop holds every other request while it opens, and these are asked every
30 s by every open page.

The routes come from the shell's own pollers, so a new one is read as soon as it is written: every
``useVisiblePoll`` and ``setInterval`` under ``web/src/app``, the ``api.<method>`` calls of the
function it polls, the path each method reads (``web/src/lib/api.ts``), and the handler that path
is registered to under ``src/personalclaw``. A poller whose route cannot be followed that far
fails here.

Then derived from the source, with the whole-file census's walk
(``test_event_loop_whole_file_census``): what it follows is a database opened, a ``connect`` of the
driver ``personalclaw.sqlite_compat`` exports, or a call of a function that makes one, followed
through the functions that call it. Each polled route's handler, and every coroutine it awaits, is
read, and an open must sit inside the arguments of ``asyncio.to_thread`` / ``run_in_executor`` or
in a function handed to one of them.

Not a census of the whole package: the same walk finds an open on the loop in about 730 coroutines,
a list nobody would shrink by hand. What the walk cannot see is the census's: a call it cannot
resolve, a function passed around as a value.
"""

from __future__ import annotations

import ast
import functools
import re
import textwrap

import pytest
import source_index
from rails_repo import REPO
from test_event_loop_whole_file_census import _PACKAGE, _Census

#: The shell: what every page runs inside.
_SHELL = REPO / "web" / "src" / "app"
_API = REPO / "web" / "src" / "lib" / "api.ts"
_POLLER = re.compile(r"\b(?:useVisiblePoll|setInterval)\(")
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_PAIRS = {"(": ")", "[": "]", "{": "}"}

#: The pollers the shell has today, by the route each reads: the walk must find at least these.
KNOWN_POLLED = frozenset({"/api/loops", "/api/system"})

#: The driver module, whose ``sqlite3`` is the one every store opens with.
_DRIVER = "personalclaw.sqlite_compat"
_OPENED = "a database opened"


def _closing(text: str, at: int) -> int:
    """The index just past the bracket that closes the one at *at*."""
    depth = 0
    for i in range(at, len(text)):
        if text[i] in _PAIRS:
            depth += 1
        elif text[i] in _PAIRS.values():
            depth -= 1
            if depth == 0:
                return i + 1
    raise AssertionError(f"no bracket closes the one at {at}")


def _first_argument(text: str, paren: int) -> str:
    """The first argument of the call whose ``(`` is at *paren*."""
    depth = 0
    for i in range(paren + 1, len(text)):
        if text[i] in _PAIRS:
            depth += 1
        elif text[i] in _PAIRS.values():
            if depth == 0:
                return text[paren + 1 : i]
            depth -= 1
        elif text[i] == "," and depth == 0:
            return text[paren + 1 : i]
    raise AssertionError(f"the call at {paren} never closes")


def _polled(text: str, argument: str) -> str:
    """What a poller polls: its first argument, or, when that names a function of the file (a
    ``const`` or a ``function``), that function's text."""
    name = argument.strip()
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
        return argument
    named = re.escape(name)
    found = re.search(rf"\b(?:const|let)\s+{named}\s*=\s*|\bfunction\s+{named}\b", text)
    assert found, f"the poller's function {name} is not defined in its file"
    opening = re.compile(r"[({]").search(text, found.end())
    assert opening, f"no body follows {name}"
    end = _closing(text, opening.start())
    if text[opening.start()] == "(":  # a function's parameters, or a call's arguments
        after = re.compile(r"\s*(?::[^{=]*)?(=>)?\s*").match(text, end)
        assert after
        if text[after.end() : after.end() + 1] == "{":  # a function's body, or an arrow's
            end = _closing(text, after.end())
        elif after.group(1):  # an arrow's expression, to the end of its line
            end = text.find("\n", after.end()) % (len(text) + 1)
    return text[found.start() : end]


def _pollers_in(source: str) -> list[tuple[int, set[str]]]:
    """Each poller in the TypeScript *source*: its line, and the ``api.<method>`` names the
    function it polls calls."""
    text = _COMMENT.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), source)
    found = []
    for call in _POLLER.finditer(text):
        body = _polled(text, _first_argument(text, call.end() - 1))
        line = text.count("\n", 0, call.start()) + 1
        found.append((line, set(re.findall(r"\bapi\.(\w+)\(", body))))
    return found


def _shell_pollers() -> dict[str, list[str]]:
    """``api.<method>`` → where the shell polls it (file:line), from every poller under
    :data:`_SHELL`."""
    polled: dict[str, list[str]] = {}
    for path in source_index.under(source_index.web_files(), _SHELL, (".ts", ".tsx")):
        if ".test." in path.name:
            continue
        for line, methods in _pollers_in(source_index.text(path)):
            where = f"{source_index.rel(path)}:{line}"
            assert methods, f"{where}: a poller that calls no api method the walk can follow"
            for method in methods:
                polled.setdefault(method, []).append(where)
    return polled


@functools.lru_cache(maxsize=1)
def _polled_routes() -> dict[str, tuple[str, str, str]]:
    """Each route the shell polls → ``(file under src/personalclaw, handler, who polls it)``."""
    census, api = _package_opens(), source_index.text(_API)
    routes: dict[str, tuple[str, str, str]] = {}
    for method, where in sorted(_shell_pollers().items()):
        route = _route_of(method, api)
        routes[route] = (*_handler_of(route, census), f"api.{method} at {', '.join(where)}")
    return routes


def _route_of(method: str, api: str) -> str:
    """The path ``api.<method>`` reads, from ``web/src/lib/api.ts``."""
    found = re.search(rf"^\s*{re.escape(method)}\s*:", api, re.M)
    assert found, f"api.{method} is not defined in {source_index.rel(_API)}"
    path = re.compile(r"['`](/api/[\w/-]*)").search(api, found.end())
    assert path, f"api.{method} reads no /api/ path"
    return path.group(1).rstrip("/")


def _handler_of(route: str, census: "_Opens") -> tuple[str, str]:
    """``(file under src/personalclaw, handler)`` the GET route *route* is registered to."""
    registered = rf"""add_get\(\s*["']{re.escape(route)}["']\s*,\s*([\w.]+)\s*\)"""
    names = {
        m.group(1).rsplit(".", 1)[-1]
        for path, _tree in source_index.package_trees()
        for m in re.finditer(registered, source_index.text(path))
    }
    assert len(names) == 1, f"GET {route} is registered to {sorted(names) or 'nothing'}"
    (name,) = names
    defs = [q for q in census.by_name[name] if isinstance(census.defs[q], ast.AsyncFunctionDef)]
    assert len(defs) == 1, f"GET {route}'s handler {name} is defined {len(defs)} times"
    rel, qual, _cls = census.owner[id(census.defs[defs[0]])]
    return rel, qual


class _Opens(_Census):
    """The census, following databases opened instead of whole-file work."""

    def whole_file_call(self, call: ast.Call, rel: str) -> str:
        func = call.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "connect"
            and isinstance(func.value, ast.Name)
        ):
            return ""
        bound = self.imports[rel].get(func.value.id, "")
        if bound == f"{_DRIVER}.sqlite3" or (
            self.module[rel] == _DRIVER and func.value.id == "sqlite3"
        ):
            return _OPENED
        return ""

    def loops(self, fn: ast.AST, rel: str) -> dict[int, str]:
        return {}

    def awaited_from(self, rel: str, qual: str) -> set[tuple[str, str]]:
        """``(file, function)`` of the coroutine *qual* in *rel* and of every coroutine it awaits,
        followed through the coroutines they await."""
        found: set[tuple[str, str]] = set()
        todo = [f"{self._module_name(rel)}.{qual}"]
        while todo:
            fn = self.defs.get(todo.pop())
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            here = self.owner[id(fn)][:2]
            if here in found:
                continue
            found.add(here)
            for node in ast.walk(fn):
                if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
                    target = self.resolve(node.value.func, fn)
                    if target:
                        todo.append(target)
        return found


@functools.lru_cache(maxsize=1)
def _package_opens() -> _Opens:
    return _Opens(
        {path.relative_to(_PACKAGE).as_posix(): tree for path, tree in source_index.package_trees()}
    )


@pytest.mark.timeout(50)
def test_no_route_the_shell_polls_opens_a_database_on_the_event_loop():
    """🔴 Red before: the loop list opened the workflow run store and the loops table on the
    loop."""
    census = _package_opens()
    on_loop, _handing = census.sites()

    found = []
    for route, (rel, handler, who) in sorted(_polled_routes().items()):
        read = census.awaited_from(rel, handler)
        assert (rel, handler) in read, f"{rel}: no coroutine {handler} (GET {route}, {who})"
        for (file, fn, what), lines in sorted(on_loop.items()):
            if (file, fn) in read:
                found.append(f"  {file}:{lines} {fn}: {what} (GET {route}, {who})")
    assert not found, (
        "a route the shell polls from every page opens a database on the event loop: hand it to "
        "asyncio.to_thread:\n" + "\n".join(found)
    )


@pytest.mark.timeout(50)
def test_the_walk_sees_the_running_list_hand_its_stores_off():
    """The positive control: the running list (which every page's activity reads) opens its stores
    in a worker thread, and the walk sees it do so; and it sees databases opened on the loop
    elsewhere, so a walk that matched nothing would not pass."""
    on_loop, handing = _package_opens().sites()

    assert ("dashboard/handlers/running.py", "_read") in handing, sorted(handing)[:20]
    assert len(on_loop) > 100, f"the walk found only {len(on_loop)} opens on the loop"


@pytest.mark.timeout(50)
def test_the_shells_pollers_are_followed_to_their_handlers():
    """The positive control of the pollers: the two the shell has today are found, each followed
    to the handler its route is registered to."""
    routes = _polled_routes()

    assert KNOWN_POLLED <= set(routes), sorted(routes)
    assert routes["/api/loops"][:2] == ("dashboard/handlers/loop_routes.py", "api_loop_list")
    assert routes["/api/system"][:2] == ("dashboard/handlers_system.py", "api_system")


_POLLERS = textwrap.dedent("""
    const check = useCallback(() => {
      Promise.resolve().then(() => api.system()).then(() => {})
    }, [])
    // useVisiblePoll(() => api.notPolled(), 1_000)
    useVisiblePoll(check, 30_000, { immediate: false })
    useVisiblePoll(() => {
      api.uLoops().then((ls) => setCount(ls.length)).catch(() => {})
    }, 30_000)
    function refresh(): void {
      void api.approvals()
    }
    const t = window.setInterval(refresh, 5_000)
    useVisiblePoll(() => setNow(Date.now()), 1_000)
    const count = () => api.running().then((r) => r.length)
    const read = async () => {
      await api.chatSessions()
    }
    useVisiblePoll(count, 60_000)
    setInterval(read, 60_000)
""")


def test_the_walk_finds_each_pollers_api_calls():
    """The falsification of the pollers: a ``useCallback`` named, an inline arrow, a ``function``
    handed to ``setInterval``, one in a comment (not a poller), a poller that calls no api method,
    and named arrows, one an expression and one a body."""
    assert _pollers_in(_POLLERS) == [
        (6, {"system"}),
        (7, {"uLoops"}),
        (13, {"approvals"}),
        (14, set()),
        (19, {"running"}),
        (20, {"chatSessions"}),
    ]


_SHAPES = textwrap.dedent("""
    import asyncio

    from personalclaw.sqlite_compat import sqlite3


    def _connect():
        return sqlite3.connect("runs.db")


    def list_runs():
        conn = _connect()
        try:
            return conn.execute("SELECT 1").fetchall()
        finally:
            conn.close()


    async def _rows():
        return list_runs()


    async def opens_through_what_it_awaits(request):
        return await _rows()


    async def opens_on_the_loop(request):
        return list_runs()


    async def hands_it_off(request):
        return await asyncio.to_thread(list_runs)


    async def hands_a_closure_off(request):
        def off_the_loop():
            return list_runs()

        return await asyncio.to_thread(off_the_loop)
""")


def test_the_walk_finds_an_open_on_the_loop_and_none_handed_off():
    """The falsification: source written to hold each shape, on the loop and handed off."""
    census = _Opens({"shapes.py": ast.parse(_SHAPES)})
    on_loop, handing = census.sites()

    assert sorted(on_loop) == [
        ("shapes.py", "_rows", "list_runs"),
        ("shapes.py", "opens_on_the_loop", "list_runs"),
    ], on_loop
    assert handing == {("shapes.py", "hands_it_off"), ("shapes.py", "hands_a_closure_off")}
    assert census.awaited_from("shapes.py", "opens_through_what_it_awaits") == {
        ("shapes.py", "opens_through_what_it_awaits"),
        ("shapes.py", "_rows"),
    }
