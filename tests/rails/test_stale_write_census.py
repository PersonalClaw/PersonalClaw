"""Every route that refuses a stale whole-document write is one the dashboard sends a base to, and
every base the dashboard sends is checked.

``stale_write_refusal`` (``personalclaw/stale_write.py``) guards a write only when the route's
handler asks it, and asks it about a base only the client names: no ``If-Match`` is ``428
revision_required``, a stale one ``409 stale_write``. Each half used to be proven route by route,
a pair of tests per route; the families in ``tests/test_stale_write*.py`` now keep one 428 and one
409 each. What stands in for the rest is this equality, read from both ends:

* the router: the routes the gateway serves (``register_dashboard_routes`` and the registrars
  ``dashboard/server.py`` calls beside it), each handler followed into the helpers of its own
  module and of the dashboard's, as the owner-presence census follows them;
* the client: each ``api`` method in ``web/src/lib/api.ts`` whose body sends ``basedOn(…)``, and
  the route its call writes to. A ``${…}`` in the path is one segment, or each member of the
  string union its parameter is typed with (``MemoryDoc``).

A handler that reaches the refusal with no client write naming a base would refuse every save the
dashboard sends it; a client write naming a base to a route whose handler never checks it is the
silent overwrite the refusal exists to stop. Both are named. Handlers are compared rather than
routes, because one handler often serves a document's ``PUT`` and its ``DELETE``.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
import textwrap
import types
from collections.abc import Callable, Iterable
from typing import Any

from aiohttp import web
from rails_repo import REPO

from personalclaw.stale_write import stale_write_refusal

API_TS = REPO / "web" / "src" / "lib" / "api.ts"


# ── the router ────────────────────────────────────────────────────────────────


def _namespace(fn: Any, tree: ast.AST) -> dict[str, Any]:
    """The names *fn*'s body can call: its module's, its closure's, and its own imports."""
    names: dict[str, Any] = dict(getattr(fn, "__globals__", {}))
    try:
        names.update(inspect.getclosurevars(fn).nonlocals)
    except (TypeError, ValueError):
        pass
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            try:
                module = importlib.import_module(node.module)
            except Exception:  # noqa: BLE001 — a module that will not import names nothing
                continue
            for alias in node.names:
                names[alias.asname or alias.name] = getattr(module, alias.name, None)
    return names


def _resolve(expr: ast.expr, names: dict[str, Any]) -> Any:
    """What a called expression is: a name, or an attribute of a module or class it names."""
    if isinstance(expr, ast.Name):
        return names.get(expr.id)
    if isinstance(expr, ast.Attribute):
        base = _resolve(expr.value, names)
        if isinstance(base, (types.ModuleType, type)):
            return getattr(base, expr.attr, None)
    return None


def asks_for_a_base(handler: Callable[..., Any]) -> bool:
    """Whether *handler*, or a helper of its own module or the dashboard's it calls, calls the
    refusal."""
    origin = getattr(inspect.unwrap(handler), "__module__", None)
    seen: set[Any] = set()
    todo = [inspect.unwrap(getattr(handler, "func", handler))]
    while todo:
        fn = todo.pop()
        if fn in seen:
            continue
        seen.add(fn)
        try:
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        except (OSError, TypeError, SyntaxError):
            continue
        names = _namespace(fn, tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = _resolve(node.func, names)
            if target is stale_write_refusal:
                return True
            module = getattr(target, "__module__", "") or ""
            if inspect.isfunction(target) and (
                module == origin or module.startswith("personalclaw.dashboard")
            ):
                todo.append(target)
    return False


def gateway_routes() -> web.Application:
    """An application holding every route the gateway serves, registered as ``server.py`` does."""
    from personalclaw.dashboard import server
    from personalclaw.dashboard.handlers.knowledge import setup_knowledge_routes
    from personalclaw.dashboard.routes import register_dashboard_routes
    from personalclaw.providers.entity_routes import register_entity_routes
    from personalclaw.providers.instance_routes import register_instance_routes
    from personalclaw.providers.routes import register_routes as register_extension_routes

    app = web.Application()
    server._register_mcp_routes(app)
    register_dashboard_routes(app, pages=False)
    register_extension_routes(app)
    register_instance_routes(app)
    register_entity_routes(app)
    setup_knowledge_routes(app)
    return app


def handlers_of(app: web.Application) -> dict[tuple[str, str], Callable[..., Any]]:
    return {
        (route.method, route.resource.canonical): inspect.unwrap(route.handler)
        for route in app.router.routes()
        if route.resource is not None and route.method != "HEAD"
    }


# ── the client ────────────────────────────────────────────────────────────────


def _holes(template: str) -> str:
    """A path template with each ``${…}`` (nested braces and all) as ``{}``."""
    out, i = [], 0
    while i < len(template):
        if template.startswith("${", i):
            depth, j = 1, i + 2
            while depth and j < len(template):
                depth += {"{": 1, "}": -1}.get(template[j], 0)
                j += 1
            out.append("{}")
            i = j
        else:
            out.append(template[i])
            i += 1
    return "".join(out)


def client_writes(api_text: str) -> dict[str, list[tuple[str, str]]]:
    """Each ``api`` method that sends ``basedOn(…)``: the method and path patterns it writes to."""
    unions = {
        m.group(1): [v.strip().strip("'`") for v in m.group(2).split("|")]
        for m in re.finditer(
            r"(?m)^export type (\w+) = ((?:\s*(?:'[^']*'|`[^`]*`)\s*\|?)+)$", api_text
        )
    }
    body = api_text[api_text.index("export const api = {") :]
    keys = list(re.finditer(r"(?m)^  (\w+)\s*[:(]", body))
    writes: dict[str, list[tuple[str, str]]] = {}
    for i, key in enumerate(keys):
        text = body[key.start() : keys[i + 1].start() if i + 1 < len(keys) else len(body)]
        if "basedOn(" not in text:
            continue
        call = re.search(r"\b(put|patch|post)\s*(?:<[^(]*>)?\s*\(", text)
        path = re.search(r"[`'](/api/[^`']*)[`']", text)
        if not call or not path:
            writes[key.group(1)] = []
            continue
        signature = re.match(r"\s*\w+\s*:\s*(?:async\s*)?\(([^)]*)\)", text)
        params = dict(re.findall(r"(\w+)\??\s*:\s*(\w+)", signature.group(1))) if signature else {}
        paths = [path.group(1)]
        for name in re.findall(r"\$\{(\w+)\}", path.group(1)):
            if params.get(name) in unions:
                paths = [
                    p.replace("${" + name + "}", v) for p in paths for v in unions[params[name]]
                ]
        writes[key.group(1)] = [(call.group(1).upper(), _holes(p)) for p in paths]
    return writes


def _matches(pattern: str, canonical: str) -> bool:
    """A client path pattern against a route: a ``{}`` in a segment stands for the route's
    parameter there, and a literal segment for itself."""
    a, b = pattern.split("/"), canonical.split("/")
    return len(a) == len(b) and all(
        x == y or ("{}" in x and y.startswith("{")) for x, y in zip(a, b)
    )


# ── the census ────────────────────────────────────────────────────────────────


def census(app: web.Application, api_text: str) -> tuple[dict[Any, list[str]], list[str]]:
    """``(each handler a client write names a base to → those writes, mismatches)``."""
    routes = handlers_of(app)
    writes = client_writes(api_text)
    guarded: dict[Any, list[str]] = {}
    mismatches: list[str] = []
    for name, targets in writes.items():
        hit = [
            route
            for route in routes
            for verb, pattern in targets
            if route[0] == verb and _matches(pattern, route[1])
        ]
        if not hit:
            mismatches.append(
                f"api.{name} sends a base to {targets or 'a path this census cannot read'}, "
                "which no route serves"
            )
        for route in hit:
            guarded.setdefault(routes[route], []).append(name)
    asking = {h for (method, _), h in routes.items() if method != "GET" and asks_for_a_base(h)}

    def named(handler: Any) -> str:
        served = sorted(f"{m} {p}" for (m, p), h in routes.items() if h is handler)
        return f"{handler.__module__}.{handler.__qualname__} ({', '.join(served)})"

    for handler in sorted(asking - set(guarded), key=named):
        mismatches.append(
            f"{named(handler)} refuses a write without a base, and the dashboard sends it none"
        )
    for handler in sorted(set(guarded) - asking, key=named):
        mismatches.append(
            f"{named(handler)} is sent a base by "
            f"api.{', api.'.join(sorted(set(guarded[handler])))} and never checks it"
        )
    return guarded, mismatches


def test_every_refusing_route_is_one_the_dashboard_sends_a_base_to_and_back() -> None:
    guarded, mismatches = census(gateway_routes(), API_TS.read_text(encoding="utf-8"))
    assert mismatches == []
    # The floors: the census read a real router and a real client, a wrapper and a union among them.
    assert len(guarded) == 32, sorted(h.__qualname__ for h in guarded)
    names = {n for writes in guarded.values() for n in writes}
    assert {"savePrompt", "saveMemoryDoc", "updateSchedule", "saveProviderConfig"} <= names


# ── planted: a route on each side of the equality ─────────────────────────────


async def _planted_checked(request: web.Request) -> web.Response:
    refused = stale_write_refusal(request, {"note": "stored"}, what="the note")
    return refused or web.json_response({"ok": True})


async def _planted_checked_tags(request: web.Request) -> web.Response:
    refused = stale_write_refusal(request, {"tags": []}, what="the tags")
    return refused or web.json_response({"ok": True})


async def _planted_unchecked(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def _helper_that_checks(request: web.Request) -> web.Response | None:
    return stale_write_refusal(request, {"note": "stored"}, what="the note")


async def _planted_checked_through_a_helper(request: web.Request) -> web.Response:
    return _helper_that_checks(request) or web.json_response({"ok": True})


_PLANTED_API = """export const api = {
  saveNote: (id: string, body: unknown, base: string) =>
    put<{ ok: boolean }>(`/api/notes/${encodeURIComponent(id)}`, body, basedOn(base)),
  readNote: (id: string) => get(`/api/notes/${id}`),
}
"""


def _app(routes: Iterable[tuple[str, str, Callable[..., Any]]]) -> web.Application:
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    return app


def test_the_planted_pair_agrees() -> None:
    guarded, mismatches = census(_app([("PUT", "/api/notes/{id}", _planted_checked)]), _PLANTED_API)
    assert mismatches == []
    assert list(guarded.values()) == [["saveNote"]]
    # Through a helper of the handler's own module, too.
    _, mismatches = census(
        _app([("PUT", "/api/notes/{id}", _planted_checked_through_a_helper)]), _PLANTED_API
    )
    assert mismatches == []


def test_a_route_that_refuses_a_write_the_dashboard_sends_no_base_to_is_named() -> None:
    app = _app(
        [
            ("PUT", "/api/notes/{id}", _planted_checked),
            ("PUT", "/api/tags/{id}", _planted_checked_tags),
        ]
    )
    _, mismatches = census(app, _PLANTED_API)
    assert mismatches == [
        f"{__name__}._planted_checked_tags (PUT /api/tags/{{id}}) refuses a write without a base, "
        "and the dashboard sends it none"
    ]


def test_a_base_the_dashboard_sends_to_a_route_that_never_checks_it_is_named() -> None:
    _, mismatches = census(_app([("PUT", "/api/notes/{id}", _planted_unchecked)]), _PLANTED_API)
    assert mismatches == [
        f"{__name__}._planted_unchecked (PUT /api/notes/{{id}}) is sent a base by api.saveNote "
        "and never checks it"
    ]
