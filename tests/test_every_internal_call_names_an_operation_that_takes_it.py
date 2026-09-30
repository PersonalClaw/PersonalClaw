"""Every call PersonalClaw makes with its internal credential names an operation that takes it.

PersonalClaw's own processes call their gateway over loopback with its internal credential
(``X-Internal-Secret``, the ``.local_secret`` the gateway writes when it starts), and the credential
opens exactly the operations ``server.INTERNAL_ROUTES`` and ``server.MIXED_INTERNAL_ROUTES`` list.
The two halves were kept by hand in different files with nothing holding them together, and
eleven of the agent's own tool calls named an operation neither list had: ``memory_recall``,
``get_context``, the triage rules, ``prompt_render``, a batch of subagents, the self-nudge stop and
the artifact tools' project. On a gateway that asks for a sign-in, every call of theirs failed with
the browser's sign-in sentence. The development server skips the sign-in for loopback callers, and
every test that booted a gateway turned the sign-in off, so neither saw it.

So both directions are read off the source here:

* every call that carries the credential names an operation one of the lists opens, and
* every operation on the lists is a route the gateway registers and one a call names: an entry
  nothing calls holds the credential's door open for nobody.

The calls are found where the credential is put on a request (:data:`SENDERS`). A new place that
sends it fails :func:`test_the_census_knows_every_place_the_credential_is_sent` until the census
reads its calls too.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import personalclaw
from personalclaw import schedule_script
from personalclaw.dashboard import server
from personalclaw.dashboard.token_auth import InternalRoute
from personalclaw.manifest_meta import canonical_route
from personalclaw.manifest_reference import _routes_from_ast

SRC = Path(personalclaw.__file__).resolve().parent
HEADER = "X-Internal-Secret"
#: ``mcp_core``'s helpers, by the method each sends.
HELPERS = {"_get": "GET", "_post": "POST", "_delete": "DELETE"}
#: The scheduled-script launcher runs as its own program, from source held in a string.
LAUNCHER = "schedule_script.py:_LAUNCHER_SRC"
#: What a path parameter is filled with. One segment, as the router reads it.
SAMPLE = "x"

#: Where the credential is put on a request, by file and function, and so where its calls are.
SENDERS = {
    ("mcp_core.py", "_internal_headers"): "mcp_core's _get/_post/_delete, and every call of them",
    ("mcp_shared.py", "_resolve_excluded_tools"): "an MCP server's read of its tool policy",
    ("auth/cli.py", "_rotate_key_cmd"): "`personalclaw auth rotate-key`",
    (LAUNCHER, "_post"): "a scheduled script's ctx.notify and ctx.call_tool",
}

#: Listed, and called by no PersonalClaw process: a webhook relayed on this computer presents the
#: credential (docs/architecture/security.md, "Webhook auth"), and the route checks its own token.
RELAYED = frozenset({"POST /api/hooks/agent"})

#: How many calls the census read when it was written. Fewer means a reader stopped reading.
CALL_FLOOR = 28


@dataclass(frozen=True)
class Call:
    method: str
    path: str | None  # the path the router sees, or None when the source does not spell it
    where: str


@functools.lru_cache(maxsize=1)
def _trees() -> dict[str, ast.Module]:
    trees = {
        path.relative_to(SRC).as_posix(): ast.parse(path.read_text(encoding="utf-8"))
        for path in sorted(SRC.rglob("*.py"))
    }
    trees[LAUNCHER] = ast.parse(schedule_script._LAUNCHER_SRC)
    return trees


def _walk(tree: ast.AST) -> Iterator[tuple[ast.AST, str]]:
    """Every node, with the name of the function it is in ("" at module level)."""
    stack: list[tuple[ast.AST, str]] = [(tree, "")]
    while stack:
        node, function = stack.pop()
        yield node, function
        inner = node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
        stack.extend((child, inner) for child in ast.iter_child_nodes(node))


def _is_header(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == HEADER


def _function(rel: str, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in ast.walk(_trees()[rel])
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _constants(tree: ast.Module) -> dict[str, str]:
    """A module's own string constants (``DISPATCH_PATH = "/api/…"``)."""
    return {
        target.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _path(node: ast.AST, constants: dict[str, str]) -> str | None:
    """The path a call's argument spells, its parameters as SAMPLE, without the query."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        text = "".join(v.value if isinstance(v, ast.Constant) else SAMPLE for v in node.values)
    elif isinstance(node, ast.Name) and node.id in constants:
        text = constants[node.id]
    else:
        return None
    text = text.split("?", 1)[0]
    return text if text.startswith("/") else None


def _requests(function: ast.FunctionDef) -> list[ast.Call]:
    """The ``urllib.request.Request(…)`` calls in *function*."""
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Request"
    ]


def _method(request: ast.Call) -> str:
    """The method a ``Request(…)`` sends: its ``method=``, else GET."""
    given = [k.value for k in request.keywords if k.arg == "method"]
    return str(given[0].value) if given and isinstance(given[0], ast.Constant) else "GET"


def _method_of_requests(function: ast.FunctionDef) -> set[str]:
    return {_method(request) for request in _requests(function)}


def _helper_calls() -> list[Call]:
    """Every call of mcp_core's helpers, in mcp_core and in each module that imports them."""
    calls = []
    for rel, tree in _trees().items():
        if rel == LAUNCHER:
            continue
        bound = dict(HELPERS) if rel == "mcp_core.py" else {}
        via_module = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "personalclaw.mcp_core":
                bound.update(
                    {a.asname or a.name: HELPERS[a.name] for a in node.names if a.name in HELPERS}
                )
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "personalclaw"
                and any(a.name == "mcp_core" for a in node.names)
            ):
                via_module = True
        constants = _constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in bound:
                method = bound[func.id]
            elif (
                via_module
                and isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "mcp_core"
                and func.attr in HELPERS
            ):
                method = HELPERS[func.attr]
            else:
                continue
            path = _path(node.args[0], constants) if node.args else None
            calls.append(Call(method, path, f"{rel}:{node.lineno}"))
    return calls


def _launcher_calls() -> list[Call]:
    """The launcher's calls of its own ``_post``, which POSTs."""
    assert _method_of_requests(_function(LAUNCHER, "_post")) == {"POST"}
    return [
        Call("POST", _path(node.args[0], {}), f"{LAUNCHER}:{node.lineno}")
        for node in ast.walk(_trees()[LAUNCHER])
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_post"
    ]


def _request_calls(rel: str, function: str) -> list[Call]:
    """The requests *function* builds itself, ``Request(f"{base}/api/…")``: the path is the URL
    after the gateway's base."""
    calls = []
    for request in _requests(_function(rel, function)):
        url = request.args[0] if request.args else None
        path = None
        if isinstance(url, ast.JoinedStr):
            values = list(url.values)
            while values and not isinstance(values[0], ast.Constant):
                values.pop(0)  # the gateway's base
            path = _path(ast.JoinedStr(values=values), {})
        calls.append(Call(_method(request), path, f"{rel}:{request.lineno}"))
    return calls


def _named_header_calls(rel: str, function: str) -> list[Call]:
    """Calls in *function* that pass the header by name: ``_ask_gateway(port, "/api/…",
    secret_header="X-Internal-Secret")``, whose callee POSTs."""
    tree = _trees()[rel]
    calls = []
    for node in ast.walk(_function(rel, function)):
        if isinstance(node, ast.Call) and any(_is_header(k.value) for k in node.keywords):
            callee = _function(rel, node.func.id)
            (method,) = _method_of_requests(callee)
            path = _path(node.args[1], _constants(tree)) if len(node.args) > 1 else None
            calls.append(Call(method, path, f"{rel}:{node.lineno}"))
    return calls


@functools.lru_cache(maxsize=1)
def _census() -> dict[tuple[str, str], list[Call]]:
    return {
        ("mcp_core.py", "_internal_headers"): _helper_calls(),
        ("mcp_shared.py", "_resolve_excluded_tools"): _request_calls(
            "mcp_shared.py", "_resolve_excluded_tools"
        ),
        ("auth/cli.py", "_rotate_key_cmd"): _named_header_calls("auth/cli.py", "_rotate_key_cmd"),
        (LAUNCHER, "_post"): _launcher_calls(),
    }


def _routes() -> dict[str, InternalRoute]:
    return {
        entry: InternalRoute.parse(entry)
        for entry in server.INTERNAL_ROUTES | server.MIXED_INTERNAL_ROUTES
    }


def _registered() -> set[tuple[str, str]]:
    """Every ``(method, canonical path)`` the gateway registers, from the package's own census of
    its routes: the one the route reference and ``/api/manifest`` are built from, so however the
    routes are registered, this reads them the way those do. A ``*`` route serves every method."""
    return {(route["method"], route["path"]) for route in _routes_from_ast()}


def test_the_census_knows_every_place_the_credential_is_sent():
    """A header dict or a ``secret_header=`` argument naming the credential is a sender."""
    found = set()
    for rel, tree in _trees().items():
        for node, function in _walk(tree):
            if isinstance(node, ast.Dict) and any(_is_header(key) for key in node.keys):
                found.add((rel, function))
            elif isinstance(node, ast.keyword) and _is_header(node.value):
                found.add((rel, function))
    assert found == set(SENDERS), (
        "the internal credential is sent from a place this census does not read; add it to "
        f"SENDERS and teach _census() its calls: {sorted(found ^ set(SENDERS))}"
    )


def test_mcp_cores_helpers_all_carry_the_credential():
    """The positive control for the biggest reader: each helper puts the credential on."""
    for helper in HELPERS:
        body = _function("mcp_core.py", helper)
        assert any(
            isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_internal_headers"
            for node in ast.walk(body)
        ), helper


def test_every_call_that_carries_the_credential_names_an_operation_that_takes_it():
    """🔴 Against integration's lists, eleven calls, ``GET /api/memory/recall`` among them."""
    census = _census()
    calls = [call for sender in SENDERS for call in census[sender]]
    assert all(census[sender] for sender in SENDERS), "a sender's calls were not read"
    assert len(calls) >= CALL_FLOOR, f"the census read only {len(calls)} calls"
    unreadable = [call.where for call in calls if call.path is None]
    assert unreadable == [], (
        "these calls do not spell their path in the source, so nothing can check what they "
        f"reach: write it in the call, as an f-string with its parameters: {unreadable}"
    )
    routes = _routes().values()
    refused = [
        f"{call.method} {call.path} ({call.where})"
        for call in calls
        if not any(route.admits(call.method, call.path or "") for route in routes)
    ]
    assert refused == [], f"the gateway refuses the internal credential on: {refused}"


def test_every_listed_operation_is_a_route_the_gateway_registers():
    registered = _registered()
    assert ("GET", "/api/memory/recall") in registered, "vacuity: no registration was read"
    stale = [
        entry
        for entry, route in _routes().items()
        if not {
            (route.method, canonical_route(route.template)),
            ("*", canonical_route(route.template)),
        }
        & registered
    ]
    assert stale == [], f"listed, and registered nowhere: {stale}"


def test_every_listed_operation_is_one_a_call_names():
    calls = [call for sender in SENDERS for call in _census()[sender] if call.path]
    unused = [
        entry
        for entry, route in _routes().items()
        if entry not in RELAYED and not any(route.admits(call.method, call.path) for call in calls)
    ]
    assert unused == [], f"the credential opens these, and nothing calls them with it: {unused}"


def test_an_operation_is_on_one_list():
    assert server.INTERNAL_ROUTES.isdisjoint(server.MIXED_INTERNAL_ROUTES)
