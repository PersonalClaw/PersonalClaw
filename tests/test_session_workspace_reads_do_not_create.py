"""A read of a session workspace must never CREATE one (#2993).

``session_workspace.workspace_dir()`` mkdirs the path it resolves, and three read-only
session GETs resolved through it — so a lookup of an id the caller invented wrote a
workspace and answered `404` while doing it. The ``tool-result`` door was the worst of
the three: its nested ``tool_results/`` made the ghost dir NON-empty, and the reaper wired
into the gateway (``session_pid.cleanup_orphaned_sessions``) collected empty dirs only,
so it survived every restart. That reaper grew an age pass in #2994, but a 7-day
backstop is not a reason to create the ghost.

🔴 THIS FILE IS A PARITY RAIL, NOT A PER-ROUTE ONE. A per-route test is exactly what
passed on #2983 with 8 of 9 doors fixed: it can only ever see the doors someone thought
to list. So the doors below are the *demonstration*, and the two structural rails are the
actual contract:

* :func:`test_no_read_helper_reaches_a_creating_resolver` — a static call-graph walk. Any
  function in the declared READ set that reaches ``mkdir``/``workspace_dir``/``_store_dir``
  (transitively, within its module) is a red, whether or not a route calls it yet.
* :func:`test_every_public_function_is_classified` — the coverage guard on the rail above.
  A new public function in either module is a red until it is classified READ or WRITE, so
  the enumerated rail cannot go quietly vacuous by omission.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from personalclaw import session_workspace as ws
from personalclaw.tool_providers import result_store as rs

# ── the classification the rails are built on ──────────────────────────────────
#
# READ  = a lookup, a probe, a path resolve, or a delete-check. Must not create.
# WRITE = the caller is storing something; creating the workspace is its job.
#
# Every public callable in each module must appear in exactly one of these
# (test_every_public_function_is_classified), so this is not a list that can be
# silently outgrown.

WS_READ = {
    "workspace_path",
    "history_path",
    "load_history",
    "result_path",
    "read_result",
    "list_results",
    "cleanup",
    "config_dir",
}
WS_WRITE = {"workspace_dir", "append_history", "write_result", "append_result"}

RS_READ = {"get_result", "fetch_slice", "purge_session"}
RS_WRITE = {"store_result"}


def _module_src(mod) -> ast.Module:
    return ast.parse(Path(inspect.getsourcefile(mod)).read_text(encoding="utf-8"))


def _call_graph(tree: ast.Module) -> dict[str, set[str]]:
    """name -> every callable name its body invokes (attribute calls kept as the attr)."""
    graph: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        called: set[str] = set()
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            fn = sub.func
            if isinstance(fn, ast.Name):
                called.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                called.add(fn.attr)
        graph[node.name] = called
    return graph


def _reaches(graph: dict[str, set[str]], start: str, targets: set[str]) -> str | None:
    """The first target reachable from *start*, as a "a -> b -> c" trail, else None."""
    stack: list[tuple[str, list[str]]] = [(start, [start])]
    seen: set[str] = set()
    while stack:
        name, trail = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for callee in sorted(graph.get(name, ())):
            if callee in targets:
                return " -> ".join([*trail, callee])
            if callee in graph:
                stack.append((callee, [*trail, callee]))
    return None


# ── rail 1: the static call graph ──────────────────────────────────────────────

#: Resolving a path is not the same act as creating it. These are the creating verbs;
#: a READ function may not reach any of them. ``workspace_dir`` is listed by NAME because
#: ``result_store`` imports it, so the rail catches a read path that borrows the creating
#: resolver from the other module rather than mkdir-ing itself.
_CREATORS = {"mkdir", "workspace_dir", "_store_dir"}


@pytest.mark.parametrize(
    ("mod", "reads"),
    [(ws, WS_READ), (rs, RS_READ)],
    ids=["session_workspace", "result_store"],
)
def test_no_read_helper_reaches_a_creating_resolver(mod, reads):
    graph = _call_graph(_module_src(mod))
    offenders = {}
    for name in sorted(reads):
        if name not in graph:  # re-exported/imported name, nothing to walk
            continue
        trail = _reaches(graph, name, _CREATORS)
        if trail:
            offenders[name] = trail
    assert not offenders, (
        f"read-only helpers in {mod.__name__} reach a creating resolver: {offenders}. "
        "A read must not be a write (#2993) — resolve with workspace_path()/_store_path() "
        "and let the write path call workspace_dir()/_store_dir()."
    )


# ── rail 2: the coverage guard on rail 1 ───────────────────────────────────────


@pytest.mark.parametrize(
    ("mod", "reads", "writes"),
    [(ws, WS_READ, WS_WRITE), (rs, RS_READ, RS_WRITE)],
    ids=["session_workspace", "result_store"],
)
def test_every_public_function_is_classified(mod, reads, writes):
    public = {
        name
        for name, obj in vars(mod).items()
        if not name.startswith("_") and inspect.isfunction(obj) and obj.__module__ == mod.__name__
    }
    unclassified = public - reads - writes
    assert not unclassified, (
        f"{mod.__name__} gained public function(s) {sorted(unclassified)} that this rail "
        "does not classify. Add each to the READ set (and it must then survive "
        "test_no_read_helper_reaches_a_creating_resolver) or to the WRITE set."
    )
    stale = (reads | writes) - public - {"config_dir"}
    assert not stale, f"{mod.__name__} no longer defines {sorted(stale)} — prune the rail."


# ── rail 3: behaviour, for every read helper, over an id that never existed ────


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated home whose ``sessions/`` starts absent."""
    monkeypatch.setattr(ws, "config_dir", lambda: tmp_path)
    return tmp_path


def _sessions_entries(home: Path) -> list[str]:
    root = home / "sessions"
    return sorted(p.name for p in root.rglob("*")) if root.exists() else []


_GHOST = "never-existed-zz"

READ_CALLS = [
    ("workspace_path", lambda: ws.workspace_path(_GHOST)),
    ("history_path", lambda: ws.history_path(_GHOST)),
    ("load_history", lambda: ws.load_history(_GHOST)),
    ("result_path", lambda: ws.result_path(_GHOST, "ag1")),
    ("read_result", lambda: ws.read_result(_GHOST, "ag1")),
    ("list_results", lambda: ws.list_results(_GHOST)),
    ("cleanup", lambda: ws.cleanup(_GHOST)),
    ("get_result", lambda: rs.get_result(_GHOST, "r_deadbeef")),
    ("fetch_slice", lambda: rs.fetch_slice(_GHOST, "r_deadbeef")),
    ("purge_session", lambda: rs.purge_session(_GHOST)),
]


@pytest.mark.parametrize(("label", "call"), READ_CALLS, ids=[c[0] for c in READ_CALLS])
def test_read_helper_leaves_no_workspace_behind(home, label, call):
    call()
    assert _sessions_entries(home) == [], (
        f"{label}() created on-disk state for a session that never existed: "
        f"{_sessions_entries(home)}"
    )


def test_read_calls_cover_every_declared_read_helper():
    """Rail 3 must exercise every READ helper rail 1 walks — no quiet gaps."""
    exercised = {label for label, _ in READ_CALLS}
    declared = (WS_READ | RS_READ) - {"config_dir", "workspace_path"} | {"workspace_path"}
    assert (
        declared - exercised == set()
    ), f"declared read helpers with no behavioural case: {sorted(declared - exercised)}"


def test_purge_session_does_not_claim_it_deleted_a_session_that_never_existed(home):
    """The resolve used to CREATE the dir, so ``d.is_dir()`` was always true."""
    assert rs.purge_session(_GHOST) is False
    rs.store_result("real-session", "payload")
    assert rs.purge_session("real-session") is True
    assert not (home / "sessions" / "real-session").exists()


def test_write_path_still_creates(home):
    """The other half of the parity: a WRITE must still make its workspace."""
    ws.write_result("s1", "ag1", "hello")
    assert ws.read_result("s1", "ag1") == "hello"
    ws.append_history("s2", {"role": "user", "content": "hi"})
    assert ws.load_history("s2")[0]["content"] == "hi"
    rid = rs.store_result("s3", "raw output")
    assert rid and rs.get_result("s3", rid)["raw"] == "raw output"


# ── rail 4: the read-only handlers may only import READ helpers ────────────────
#
# ``api_session_agent_stream`` is one of the three doors, and it is an SSE endpoint with a
# 20-minute poll loop — driving it through a test client would hang, so the rail that
# covers it is static. Each of these handlers does its ``session_workspace`` import inside
# its own body, which makes the import list an exact statement of what the door resolves
# through.

_READ_ONLY_HANDLERS = [
    ("personalclaw.dashboard.handlers.core", "api_session_agents_list"),
    ("personalclaw.dashboard.handlers.core", "api_session_agent_result"),
    ("personalclaw.dashboard.handlers.core", "api_session_agent_stream"),
]


@pytest.mark.parametrize(
    ("module_name", "handler"), _READ_ONLY_HANDLERS, ids=[h for _, h in _READ_ONLY_HANDLERS]
)
def test_read_only_handler_imports_only_read_helpers(module_name, handler):
    import importlib

    mod = importlib.import_module(module_name)
    tree = _module_src(mod)
    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == handler
        ),
        None,
    )
    assert fn is not None, f"{module_name}.{handler} no longer exists — prune or rename the rail"
    imported = {
        alias.name
        for node in ast.walk(fn)
        if isinstance(node, ast.ImportFrom) and node.module == "personalclaw.session_workspace"
        for alias in node.names
    }
    assert imported, (
        f"{handler} no longer imports from session_workspace inside its body — this rail "
        "can no longer see what it resolves through. Re-point it at the new call site."
    )
    assert imported <= WS_READ, (
        f"{handler} is a read-only GET but imports creating helper(s) "
        f"{sorted(imported - WS_READ)} from session_workspace (#2993)."
    )


# ── the three doors from the issue, end to end ─────────────────────────────────


@pytest.fixture
def api_home(tmp_path, monkeypatch):
    """Point the REAL resolver at an isolated home, so the routes exercise it."""
    from personalclaw.config import loader as config_loader

    monkeypatch.setattr(config_loader, "config_dir", lambda: tmp_path)
    return tmp_path


def _door_app() -> web.Application:
    from personalclaw.dashboard.chat_handlers import api_chat_tool_result
    from personalclaw.dashboard.handlers.core import (
        api_session_agent_result,
        api_session_agents_list,
    )

    app = web.Application()
    # `api_session_agents_list` resolves the parent session before listing results (#2940), and
    # reads it off `app["state"]` the way its three neighbours in `handlers/core.py` already do —
    # the gateway always sets that key. A synthetic door app must supply it or the handler 500s on
    # a KeyError, which would have this rail reporting a fault as though it were the read under
    # test. An empty state means "no such session", which is the 404 the parametrisation allows.
    app["state"] = type("_State", (), {"conversation_log": None, "_sessions": {}})()
    app.router.add_get("/api/chat/sessions/{session}/tool-result/{rid}", api_chat_tool_result)
    app.router.add_get("/api/sessions/{id}/agents", api_session_agents_list)
    app.router.add_get("/api/sessions/{id}/agents/{agent_id}", api_session_agent_result)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/api/chat/sessions/inject-fish-1/tool-result/r_deadbeef",
        "/api/sessions/gh-A1/agents",
        "/api/sessions/gh-A1/agents/ag1",
    ],
)
async def test_read_only_session_get_writes_nothing(api_home, url):
    async with TestClient(TestServer(_door_app())) as client:
        resp = await client.get(url)
    assert resp.status in (200, 404)
    assert _sessions_entries(api_home) == [], (
        f"GET {url} created on-disk state for a session that never existed: "
        f"{_sessions_entries(api_home)}"
    )
